"""GetStartingPos / GetStartingAngle: the pose a reference was placed at.

See docs/commentary/script_convert.md#starting-pose.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from script_convert import start_pose
from script_convert.context import ScriptContext
from script_convert.converter import ScriptConverter
from script_convert.cross_ref import CrossRefGraph
from tes5_import.base.text_reader import parse_export_file

#: A door that swings 60 degrees from where it was placed and back: start angle against live angle.
SWING = ('scn Swing\nfloat orig\nfloat cur\nbegin GameMode\n'
         'set orig to GetStartingAngle z\nset cur to GetAngle z\n'
         'if cur < orig + 60\n  rotate z 15\nendif\nend\n')

#: A lever that snaps home when used: no poll, one read in OnActivate.
SNAP = 'scn Snap\nfloat h\nbegin OnActivate\nset h to GetStartingPos z\nSetPos z h\nend\n'

#: An item that checks on load and in its poll whether it still lies where it was placed.
ITEM = ('scn Item\nshort moved\nbegin OnLoad\nif GetPos x != GetStartingPos x\n  set moved to 1\nendif\nend\n'
        'begin GameMode\nif GetPos z != GetStartingPos z\n  set moved to 1\nendif\nend\n')

_OBLIVION_SCPT = Path(__file__).resolve().parent.parent / 'export' / 'Oblivion.esm' / 'SCPT.txt'


def _convert(source: str, extends: str = 'ObjectReference', carriable: bool = False) -> str:
    """One standalone script converted with an empty cross-reference graph."""
    conv = ScriptConverter(CrossRefGraph())
    conv.sc.carriable_only = carriable
    return conv.convert_standalone('T', source, extends, 'T')


def _event(text: str, opener: str) -> list:
    """The body lines of the one event `opener` names, stripped."""
    lines = text.split('\n')
    assert lines.count(opener) == 1, f'{opener}: {lines.count(opener)} definitions'
    start = lines.index(opener) + 1
    return [ln.strip() for ln in lines[start:lines.index('EndEvent', start)]]


def _block(text: str, opener: str) -> list:
    """The lines from `opener` to the next blank line."""
    lines = text.split('\n')
    start = lines.index(opener)
    return lines[start:lines.index('', start)]


class TestReads:
    """What one read becomes."""

    def test_start_angle_is_not_the_live_angle(self):
        """The start angle reads the stored pose while GetAngle still reads the live one."""
        out = _convert(SWING)
        assert '  orig = TES4_StartAngleZ()' in out
        assert '  cur = Self.GetAngleZ()' in out

    def test_start_position_is_not_zero(self):
        """A position read is the stored pose, with no not-converted note left behind."""
        out = _convert(SNAP)
        assert '  h = TES4_StartPosZ()' in out
        assert ';NE:' not in out

    def test_a_short_target_keeps_its_cast(self):
        """The read is still typed Float, so assigning it to a short casts."""
        out = _convert('scn S\nshort s\nbegin GameMode\nset s to GetStartingPos y\nend\n')
        assert '  s = TES4_StartPosY() as Int' in out

    def test_an_actor_reads_its_own_pose(self):
        """An actor script is a reference too: its read is the getter."""
        out = _convert('scn A\nfloat f\nbegin GameMode\nset f to GetStartingAngle z\nend\n', 'Actor')
        assert '  f = TES4_StartAngleZ()' in out and 'Function TES4_CaptureStart()' in out

    def test_a_self_name_is_the_script_itself(self):
        """`myself.` names the script's own reference, like no receiver at all."""
        out = _convert('scn N\nref myself\nfloat f\nbegin GameMode\nset f to myself.GetStartingPos z\nend\n')
        assert '  f = TES4_StartPosZ()' in out

    def test_a_read_inside_a_comparison(self):
        """Both sides of `GetPos x != GetStartingPos x` convert, live against stored."""
        assert '  If Self.GetPositionX() != TES4_StartPosX()' in _convert(ITEM)


class TestHelpers:
    """The variables, the capture function and the getters a reading script gains."""

    def test_only_the_axes_read(self):
        """Each axis read is declared once, however often it is read."""
        sc = ScriptContext()
        for command, axis in (('getstartingpos', 'Z'), ('getstartingangle', 'X'), ('getstartingpos', 'Z')):
            start_pose.read(sc, command, axis)
        assert sorted(start_pose.helpers(sc, '')[:3]) == [
            'Bool TES4_HaveStart = False', 'Float TES4_StartAngX = 0.0', 'Float TES4_StartZ = 0.0']

    def test_the_text_does_not_depend_on_read_order(self):
        """Reads met in any order declare angles first, then positions, each X to Z."""
        sc = ScriptContext()
        sc.start_pose = [('getstartingpos', 'Z'), ('getstartingpos', 'X'), ('getstartingangle', 'Y')]
        assert start_pose.helpers(sc, '')[:3] == [
            'Float TES4_StartAngY = 0.0', 'Float TES4_StartX = 0.0', 'Float TES4_StartZ = 0.0']

    def test_each_variable_is_taken_from_its_own_axis(self):
        """The capture pairs every variable with the read of the same kind and axis."""
        out = _convert('scn M\nfloat f\nbegin GameMode\nset f to GetStartingPos x\n'
                       'set f to GetStartingAngle y\nend\n')
        assert _block(out, 'Function TES4_CaptureStart()')[4:6] == [
            '  TES4_StartAngY = GetAngleY()', '  TES4_StartX = GetPositionX()']

    def test_the_pose_is_taken_once(self):
        """The capture returns when the flag is set and sets it after reading the pose."""
        assert _block(_convert(SNAP), 'Function TES4_CaptureStart()') == [
            'Function TES4_CaptureStart()',
            '  If TES4_HaveStart || GetParentCell() == None',
            '    Return',
            '  EndIf',
            '  TES4_StartZ = GetPositionZ()',
            '  TES4_HaveStart = True',
            'EndFunction']

    def test_every_read_takes_the_pose_first(self):
        """A getter calls the capture before it returns, so no read sees an untaken pose."""
        assert _block(_convert(SWING), 'Float Function TES4_StartAngleZ()') == [
            'Float Function TES4_StartAngleZ()',
            '  TES4_CaptureStart()',
            '  Return TES4_StartAngZ',
            'EndFunction']

    def test_a_held_object_takes_no_pose(self):
        """A script that tracks its holder tests it before touching its own reference."""
        out = _convert(ITEM, carriable=True)
        assert '  If TES4_HaveStart || TES4_Holder != None || GetParentCell() == None' in out

    def test_no_holder_test_without_a_holder_variable(self):
        """A script with no poll declares no holder, so the capture must not name one."""
        assert 'TES4_Holder' not in _convert(SNAP)

    def test_a_script_that_never_asks_gains_nothing(self):
        """No start read, no capture: the conversion is what it was."""
        out = _convert('scn P\nfloat z\nbegin GameMode\nset z to GetPos z\nend\n')
        assert 'TES4_CaptureStart' not in out and 'TES4_HaveStart' not in out


class TestStartEvents:
    """The pose is taken when the object first attaches, before the script can move it."""

    def test_attach_and_load_take_the_pose_first(self):
        """OnCellAttach and OnLoad open with the capture, ahead of arming the poll."""
        out = _convert(SWING)
        assert _event(out, 'Event OnCellAttach()')[0] == 'TES4_CaptureStart()'
        assert _event(out, 'Event OnLoad()')[0] == 'TES4_CaptureStart()'

    def test_init_takes_the_pose_ahead_of_the_poll_gate(self):
        """In OnInit the capture stands ahead of the gate that arms the poll."""
        body = _event(_convert(SWING), 'Event OnInit()')
        assert body[0] == 'TES4_CaptureStart()'
        assert body[1].startswith('If (') and body[-1] == 'EndIf'

    def test_a_script_without_a_poll_still_gets_them(self):
        """With nothing to arm, each start event holds the capture and no empty gate."""
        out = _convert(SNAP)
        assert _event(out, 'Event OnCellAttach()') == ['TES4_CaptureStart()']
        assert _event(out, 'Event OnInit()') == ['TES4_CaptureStart()']
        assert 'TES4_CaptureStart()' in _event(out, 'Event OnLoad()')

    def test_an_authored_onload_is_defined_once(self):
        """The script's own OnLoad stays the only one and opens with the capture too."""
        body = _event(_convert(ITEM), 'Event OnLoad()')
        assert body[:2] == ['TES4_CaptureStart()', 'If Self.GetPositionX() != TES4_StartPosX()']

    def test_an_authored_onload_cannot_move_first(self):
        """A script that moves in its own OnLoad still reads where it was placed."""
        out = _convert('scn L\nfloat f\nbegin OnLoad\nSetPos z 100\nset f to GetStartingPos z\nend\n')
        body = _event(out, 'Event OnLoad()')
        assert body[0] == 'TES4_CaptureStart()' and body[1].startswith('Self.SetPosition(')

    def test_a_carried_item_gets_no_init_capture(self):
        """A carriable script has no OnInit; OnCellAttach takes its pose."""
        out = _convert(ITEM, carriable=True)
        assert 'Event OnInit()' not in out
        assert _event(out, 'Event OnCellAttach()')[0] == 'TES4_CaptureStart()'


class TestOtherSubjects:
    """Reads the script cannot answer from its own pose convert as they did before."""

    def test_another_reference_is_left_to_the_rows(self):
        """A receiver declines: the position read stays inert and noted, the angle read live."""
        out = _convert('scn O\nref other\nfloat f\nbegin GameMode\nset f to other.GetStartingPos x\n'
                       'set f to other.GetStartingAngle y\nend\n')
        assert '  f = 0  ;NE: other.GetStartingPos' in out
        assert '  f = other.GetAngleY()' in out
        assert 'TES4_CaptureStart' not in out

    def test_an_axis_that_is_not_one_is_left_to_the_rows(self):
        """A read with no usable axis letter declines: no getter could answer it."""
        out = _convert('scn B\nfloat f\nbegin GameMode\nset f to GetStartingPos q\nend\n')
        assert '  f = 0  ;NE: GetStartingPos' in out
        assert 'TES4_StartPos' not in out

    def test_a_user_function_is_left_to_the_rows(self):
        """In a user function the subject is the caller, whose pose this script never took."""
        out = _convert('scn F\nfloat f\nbegin Function {}\nset f to GetStartingPos z\nend\n')
        assert 'Function TES4Call(' in out and ';NE: GetStartingPos' in out
        assert 'TES4_StartPos' not in out

    def test_a_quest_script_is_left_to_the_rows(self):
        """A quest has no pose of its own, so nothing is declared for it."""
        out = _convert('scn Q\nfloat f\nbegin GameMode\nset f to GetStartingPos z\nend\n', 'Quest')
        assert '  f = 0  ;NE: GetStartingPos' in out
        assert 'TES4_CaptureStart' not in out


def test_a_shipped_script_reads_all_six_axes():
    """The clutter script that snaps back on load: twelve reads become twelve getter calls."""
    if not _OBLIVION_SCPT.exists():
        pytest.skip('needs export/Oblivion.esm')
    record = next((r for r in parse_export_file(str(_OBLIVION_SCPT))
                   if r.get('EditorID') == 'SEBruscusDannusItemSCRIPT'), None)
    if record is None:
        pytest.skip('this export has no SEBruscusDannusItemSCRIPT')
    out = _convert(record['SCTX'], carriable=True)
    assert out.count('TES4_StartPos') - out.count('Function TES4_StartPos') == 9
    assert out.count('TES4_StartAngle') - out.count('Function TES4_StartAngle') == 3
    assert ';NE: getstartingpos' not in out and out.count('Float TES4_Start') == 6
