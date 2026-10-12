"""A script effect's entry is exported under the index of the effect it follows.

See: docs/commentary/tes5_import_magic.md#script-effect-index
"""

import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tes4_export.morroblivion_magic import _script_effects
from tes4_export.record_types import equipment
from tes4_export.record_types.common import emit_effects
from tes4_export.tes4_reader import Record, Subrecord


def _effect(code: str, script: int = None, school: int = 0, visual: bytes = b'FTHR'):
    """The subrecords of one effect: EFID, EFIT and, for a script effect, SCIT + FULL."""
    subs = [Subrecord('EFID', code.encode()),
            Subrecord('EFIT', code.encode() + struct.pack('<IIIIi', 0, 0, 5, 0, 8))]
    if script is not None:
        subs += [Subrecord('SCIT', struct.pack('<II', script, school) + visual),
                 Subrecord('FULL', b'Script Effect\x00')]
    return subs


def _fields(*effects, sig: str = 'SPEL') -> dict:
    """What `emit_effects` writes for a record holding `effects`, as a dict."""
    subs = [sub for effect in effects for sub in effect]
    lines = []
    emit_effects(lines, Record(type=sig, data_size=0, flags=0, form_id=1,
                               subrecords=subs))
    return dict(line.split('=', 1) for line in lines)


def _entries(fields: dict) -> dict:
    """{effect index: script FormID} of the script entries in `fields`."""
    return {int(key[13:key.index(']')]): value for key, value in fields.items()
            if key.startswith('ScriptEffect[') and key.endswith('.FormID')}


class TestScriptEntryFollowsItsEffect:
    def test_a_script_effect_in_first_place_keeps_index_zero(self):
        """The common shape is unchanged."""
        fields = _fields(_effect('SEFF', 0x1234), _effect('FTHR'))
        assert _entries(fields) == {0: '00001234'}

    def test_a_script_effect_in_second_place_is_entry_one(self):
        """A plain effect first: the script sits at index 1, nothing at 0."""
        fields = _fields(_effect('FTHR'), _effect('SEFF', 0x1234))
        assert _entries(fields) == {1: '00001234'}

    def test_two_script_effects_keep_their_own_scripts(self):
        """With a plain effect first, each script still sits at its own effect."""
        fields = _fields(_effect('FTHR'), _effect('SEFF', 0x1111),
                         _effect('SEFF', 0x2222))
        assert _entries(fields) == {1: '00001111', 2: '00002222'}

    def test_a_plain_effect_between_two_script_effects_leaves_a_gap(self):
        """Indices 0 and 2 hold the scripts; 1 holds none."""
        fields = _fields(_effect('SEFF', 0x1111), _effect('FTHR'),
                         _effect('SEFF', 0x2222))
        assert _entries(fields) == {0: '00001111', 2: '00002222'}

    def test_school_and_visual_travel_with_the_script(self):
        """The other two fields of the entry carry the same index."""
        fields = _fields(_effect('FTHR'),
                         _effect('SEFF', 0x1234, school=3, visual=b'SHLD'))
        assert fields['ScriptEffect[1].School'] == '3'
        assert fields['ScriptEffect[1].Visual'] == 'SHLD'
        assert 'ScriptEffect[0].School' not in fields

    def test_the_effect_lines_themselves_do_not_change(self):
        """Count and effect codes are written as before."""
        fields = _fields(_effect('FTHR'), _effect('SEFF', 0x1234))
        assert fields['EffectCount'] == '2'
        assert [fields[f'Effect[{i}].EFID'] for i in (0, 1)] == ['FTHR', 'SEFF']

    def test_every_owner_types_exporter_numbers_the_same_way(self):
        """The five exporters that carry effects, each run on its own record type."""
        subs = _effect('FTHR') + _effect('SEFF', 0x1234)
        for sig in ('SPEL', 'ENCH', 'ALCH', 'INGR', 'SGST'):
            rec = Record(type=sig, data_size=0, flags=0, form_id=1, subrecords=subs)
            lines = getattr(equipment, 'export_' + sig)(rec)
            assert 'ScriptEffect[1].FormID=00001234' in lines, sig
            assert not any(line.startswith('ScriptEffect[0]') for line in lines), sig

    def test_an_entry_written_before_its_efit_still_follows_its_effect(self):
        """Effects are counted by EFID, so the order inside one effect does not matter."""
        swapped = [Subrecord('EFID', b'SEFF'),
                   Subrecord('SCIT', struct.pack('<II', 0x1234, 0) + b'FTHR'),
                   Subrecord('EFIT', b'SEFF' + bytes(20))]
        assert _entries(_fields(_effect('FTHR'), swapped)) == {1: '00001234'}

    def test_an_entry_after_a_plain_effect_is_written_under_that_effect(self):
        """It follows effect 0, so it is entry 0; a reader of script effects never asks for it."""
        odd = _effect('FTHR') + [Subrecord('SCIT', struct.pack('<II', 0x1234, 0) + b'FTHR')]
        fields = _fields(odd, _effect('SEFF', 0x2222))
        assert _entries(fields) == {0: '00001234', 1: '00002222'}
        assert _script_effects(fields) == {'00002222'}


class TestEntryLengths:
    def test_an_entry_that_holds_only_the_script_is_written_without_the_rest(self):
        """Some game records carry a 4-byte SCIT: the script, no school, no visual."""
        short = [Subrecord('EFID', b'SEFF'), Subrecord('EFIT', b'SEFF' + bytes(20)),
                 Subrecord('SCIT', struct.pack('<I', 0x1234))]
        fields = _fields(_effect('FTHR'), short)
        assert _entries(fields) == {1: '00001234'}
        assert 'ScriptEffect[1].School' not in fields
        assert 'ScriptEffect[1].Visual' not in fields

    def test_twelve_and_sixteen_bytes_carry_school_and_visual(self):
        """The longer forms add the school and the visual; a 16-byte one has four more bytes."""
        for tail in (b'', bytes(4)):
            entry = [Subrecord('EFID', b'SEFF'), Subrecord('EFIT', b'SEFF' + bytes(20)),
                     Subrecord('SCIT', struct.pack('<II', 0x1234, 2) + b'SHLD' + tail)]
            fields = _fields(entry)
            assert (fields['ScriptEffect[0].FormID'], fields['ScriptEffect[0].School'],
                    fields['ScriptEffect[0].Visual']) == ('00001234', '2', 'SHLD')

    def test_an_entry_too_short_to_hold_a_script_is_not_written(self):
        """Fewer than four bytes cannot name a script."""
        stub = [Subrecord('EFID', b'SEFF'), Subrecord('EFIT', b'SEFF' + bytes(20)),
                Subrecord('SCIT', b'\x34\x12')]
        assert _entries(_fields(stub)) == {}


class TestEntriesThatBelongToNoEffect:
    def test_an_entry_before_the_first_effect_is_not_written(self):
        """An entry that follows no effect belongs to none."""
        stray = [Subrecord('SCIT', struct.pack('<II', 0x1234, 0) + b'FTHR')]
        assert _entries(_fields(stray, _effect('SEFF', 0x2222))) == {0: '00002222'}

    def test_an_entry_after_an_effect_with_no_efit_is_not_written(self):
        """`EffectCount` counts whole EFID + EFIT pairs; the entry is past it."""
        half = [Subrecord('EFID', b'SEFF'),
                Subrecord('SCIT', struct.pack('<II', 0x3333, 0) + b'FTHR')]
        fields = _fields(_effect('SEFF', 0x1234), half)
        assert fields['EffectCount'] == '1'
        assert _entries(fields) == {0: '00001234'}


class TestReadersAskByEffectIndex:
    def test_a_reader_finds_the_script_of_a_later_effect(self):
        """`_script_effects` looks each script effect up at its own index."""
        fields = _fields(_effect('FTHR'), _effect('SEFF', 0x1234))
        assert _script_effects(fields) == {'00001234'}

    def test_a_reader_finds_both_scripts_around_a_plain_effect(self):
        """Both script effects resolve, each at its own index."""
        fields = _fields(_effect('SEFF', 0x1111), _effect('FTHR'),
                         _effect('SEFF', 0x2222))
        assert _script_effects(fields) == {'00001111', '00002222'}
