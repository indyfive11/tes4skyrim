"""Morrowind NPC animation files become vanilla furniture and idles."""

import math
import struct

from tes5_import.packages.converter import MARKER_KEY, convert_PACK
from tes5_import.record_types.npc_markers_morrowind import (flush_npc_markers,
                                                            place_npc_markers,
                                                            write_idle_table)


class _Writer:
    """Just the writer calls the pass makes."""

    def derive_formid(self, site, key):
        """A stable id per (site, key)."""
        return 0x00800000 | (hash((site, key)) & 0xFFFF)


def _by_type(anim: str, shared: bool = False) -> dict:
    """One NPC with `anim` on a wander package, placed once; optionally a sharer."""
    npcs = [{'FormID': '00000B01', 'MorrowindAnim': anim, 'AIPackageCount': '1',
             'AIPackage[0]': '00000A01'}]
    if shared:
        npcs.append({'FormID': '00000B02', 'AIPackageCount': '1', 'AIPackage[0]': '00000A01'})
    return {'NPC_': npcs,
            'PACK': [{'FormID': '00000A01', 'PKDT.Type': '5', 'PLDT.Type': '3',
                      'PLDT.Radius': '0'}],
            'ACHR': [{'FormID': '00000C01', 'NAME': '00000B01', 'ParentCELL': '00000D01',
                      'PosX': '100', 'PosY': '200', 'PosZ': '50', 'RotZ': str(math.pi / 2),
                      'RecordFlags': '0'}]}


def test_seated_npc_gets_a_linked_chair():
    """A chair file: the package is flagged, the placement links to a chair marker beside it."""
    by_type = _by_type('Arktwend\\\\Ani\\\\SitzGespräch.nif')
    assert place_npc_markers(by_type, _Writer(), 0) == 1
    assert by_type['PACK'][0][MARKER_KEY] == 'sit'
    assert flush_npc_markers(by_type) == 1
    marker = by_type['REFR'][0]
    assert by_type['ACHR'][0]['XLKR.Reference'] == marker['FormID']
    assert marker['NAME.Final'] == '0006411B' and marker['ParentCELL'] == '00000D01'
    assert abs(float(marker['PosZ']) - 58.8) < 1e-6
    assert abs(float(marker['PosX']) - (100 - 2.4)) < 1e-6
    assert abs(float(marker['RotZ']) - (math.pi / 2 + math.radians(3))) < 1e-6


def test_sweeper_is_held_by_the_runtime_table(tmp_path):
    """A sweep file places no marker: its placement and vanilla idle go to the runtime table."""
    by_type = _by_type('AM\\\\AM_Sweeping.nif')
    by_type['NPC_'][0]['EditorID'] = 'Sweeper'
    assert place_npc_markers(by_type, _Writer(), 0) == 0
    assert by_type['PACK'][0][MARKER_KEY] == 'idle'
    assert 'XLKR.Reference' not in by_type['ACHR'][0]
    assert write_idle_table(str(tmp_path / 'Test.esm')) == 1
    table = tmp_path / 'SKSE' / 'Plugins' / 'MorrowindRuntime' / 'Test.anims.txt'
    assert table.read_text(encoding='utf-8').splitlines() == [
        'clip.idleloosesweepingstart.idle=idleLooseSweepingStart|0.000',
        'actor.Sweeper=idleloosesweepingstart||0',
        'place.00000C01=Test.esm|Sweeper|100.000,200.000,50.000,0.000,0.000,90.000']


def test_shared_package_with_an_unmapped_npc_keeps_its_sandbox():
    """A package another NPC also runs is left alone, and nothing is placed."""
    by_type = _by_type('AM\\\\AM_Fishman.nif', shared=True)
    assert place_npc_markers(by_type, _Writer(), 0) == 0
    assert MARKER_KEY not in by_type['PACK'][0]
    assert 'XLKR.Reference' not in by_type['ACHR'][0]


def test_npc_the_plugin_never_places_keeps_its_sandbox():
    """An NPC only a script spawns has no marker to link, so its package is left alone."""
    by_type = _by_type('AM\\\\AM_Fishman.nif')
    by_type['ACHR'] = []
    assert place_npc_markers(by_type, _Writer(), 0) == 0
    assert MARKER_KEY not in by_type['PACK'][0]


def test_flagged_wander_is_sit_target_on_the_linked_ref():
    """The flagged package instances SitTarget with a linked-reference target."""
    rec = {'FormID': '00000A01', 'PKDT.Type': '5', 'PLDT.Type': '3', 'PLDT.Radius': '0',
           MARKER_KEY: 'sit'}
    data = convert_PACK(rec)
    pkcu = data.index(b'PKCU')
    assert struct.unpack_from('<I', data, pkcu + 10)[0] == 0x000A9277
    ptda = data.index(b'PTDA')
    assert struct.unpack_from('<iIi', data, ptda + 6) == (3, 0, 0)
