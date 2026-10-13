"""The direct attribute magic add-on: two fields of an attribute effect's record, and its audit of a built plugin.

See: extensions/direct_attribute_magic/README.md
"""

import os
import struct
import zlib

import pytest

from core import extensions as loader
from extensions.direct_attribute_magic import audit, rule
from tes4_export.record_types.morrowind_magic import effect_editor_id
from tes5_import.base.writer import pack_record, pack_string_subrecord, pack_subrecord
from tes5_import.dialogue.morrowind_sidecar import sidecar_dir
from tes5_import.dialogue.morrowind_teleport import TELEPORTS_TABLE, copy_rows
from tes5_import.record_types import magic, magic_variants
from tes5_import.record_types.magic_morrowind import MW_ATTRIBUTE_EFFECTS, TES4_ATTRIBUTE_EFFECTS
from tests.extension_records import (EFFECTS, OTHER_ENTRIES, VariantWriter, effect_entry, mgef,
                                     subrecords)

ADD_ON = 'direct_attribute_magic'

#: MGEF DATA offsets of the archetype and the actor value, as xEdit lays the record out.
ARCHETYPE, ACTOR_VALUE = 64, 68

#: What the converter writes without the add-on: a Script effect on no actor value.
SCRIPT_ON_NONE = (1, -1)

#: The pair each family and attribute must get: archetype by family, actor value by attribute.
PEAK_VALUE_MODIFIER, VALUE_MODIFIER, ABSORB = 34, 0, 4
ARCHETYPES = {'FOAT': PEAK_VALUE_MODIFIER, 'DRAT': PEAK_VALUE_MODIFIER, 'DGAT': VALUE_MODIFIER,
              'REAT': VALUE_MODIFIER, 'ABAT': ABSORB}
ACTOR_VALUES = {0: 32, 1: 25, 2: 28, 3: 26, 4: 30, 5: 24, 6: 17, 7: 33}

#: Every (family, attribute) entry, and the two entries that are no attribute effect.
ATTRIBUTE_ENTRIES = [(code, attribute) for code in ARCHETYPES for attribute in range(8)]
ENTRIES = ATTRIBUTE_ENTRIES + list(OTHER_ENTRIES)


# ---------------------------------------------------------------------------
# Building and reading the variants
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    """Each test starts with only this add-on named and no variant registered."""
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, ADD_ON)
    magic_variants.reset()
    yield
    magic_variants.reset()


def _build(mgefs=None, entries=ENTRIES) -> tuple:
    """({EditorID: packed MGEF}, writer) of the variants the converter builds for `entries`."""
    magic_variants.reset()
    writer = VariantWriter()
    records = [mgef(code) for code in EFFECTS] if mgefs is None else mgefs
    written = magic_variants.build_av_variants(records, [effect_entry(*entry) for entry in entries], writer)
    built = {dict(subrecords(record))['EDID'].rstrip(b'\0').decode(): record for _sig, record in writer.records}
    assert written == len(writer.records) == len(built)
    return built, writer


def _pair(record: bytes) -> tuple:
    """(archetype, actor value) of a packed MGEF."""
    return struct.unpack_from('<Ii', dict(subrecords(record))['DATA'], ARCHETYPE)


def _differing_fields(one: bytes, other: bytes) -> list:
    """The offsets of the 4-byte DATA fields in which two packed MGEFs differ; every other byte must agree."""
    first, second = subrecords(one), subrecords(other)
    assert one[:24] == other[:24] and [sig for sig, _d in first] == [sig for sig, _d in second]
    assert [pair for pair in first if pair[0] != 'DATA'] == [pair for pair in second if pair[0] != 'DATA']
    a, b = dict(first)['DATA'], dict(second)['DATA']
    assert len(a) == len(b) == magic.MGEF_DATA_SIZE
    return [off for off in range(0, len(a), 4) if a[off:off + 4] != b[off:off + 4]]


def _name(code: str, attribute: int) -> str:
    """The EditorID of the variant of `code` for TES4 actor value `attribute`."""
    return magic_variants.av_variant_editor_id(code, attribute)


# ---------------------------------------------------------------------------
# The rule
# ---------------------------------------------------------------------------

def test_the_tables_are_the_converters_own():
    """A drift between the add-on's copy and the converter's tables shows here."""
    assert rule.FAMILY_ARCHETYPE == ARCHETYPES and rule.ATTRIBUTE_VALUE == ACTOR_VALUES
    assert set(rule.FAMILY_ARCHETYPE) == set(TES4_ATTRIBUTE_EFFECTS)
    assert rule.FAMILY_ARCHETYPE == {code: magic.EFFECT_ARCHETYPES[code][0] for code in TES4_ATTRIBUTE_EFFECTS}
    assert all(magic.EFFECT_ARCHETYPES[code][1] == magic.DERIVE_AV for code in rule.FAMILY_ARCHETYPE)
    assert rule.ATTRIBUTE_VALUE == magic.ATTRIBUTE_TO_AV
    assert (PEAK_VALUE_MODIFIER, VALUE_MODIFIER, ABSORB) == (
        magic.A_PEAK_VALUE_MODIFIER, magic.A_VALUE_MODIFIER, magic.A_ABSORB)
    assert SCRIPT_ON_NONE == (magic.A_SCRIPT, magic.AV_NONE)
    assert (ARCHETYPE, ACTOR_VALUE) == (magic.O_ARCHETYPE, magic.O_ACTOR_VALUE)


@pytest.mark.parametrize('code, attribute', ATTRIBUTE_ENTRIES)
def test_the_rule_gives_each_family_and_attribute_its_pair(code, attribute):
    """Archetype by family, actor value by attribute: the converter's own."""
    assert rule.attribute_effect(code, attribute, {}) == (ARCHETYPES[code], ACTOR_VALUES[attribute])
    assert rule.attribute_effect(code, attribute, mgef(code)) == (
        magic.get_archetype(code), magic.resolve_actor_value(code, attribute))


@pytest.mark.parametrize('code, attribute', [('FOSK', 14), ('FOHE', -1), ('FOAT', -1), ('FOAT', 8), ('FOAT', 14),
                                             ('foat', 4), ('', 4), ('DRSK', 4), ('SEFF', 0)])
def test_the_rule_leaves_every_other_effect_alone(code, attribute):
    """No pair outside the five families and the eight attributes."""
    assert rule.attribute_effect(code, attribute, {}) is None


@pytest.mark.parametrize('index', ['79', '0', 79, 0, '-1'])
def test_the_rule_leaves_a_morrowind_effect_to_the_runtime(index):
    """Any Morrowind effect index, 0 and -1 too, means no pair; an empty one is none."""
    assert rule.attribute_effect('FOAT', 4, {'MorrowindEffectIndex': index}) is None
    assert rule.attribute_effect('FOAT', 4, {'MorrowindEffectIndex': ''}) == (PEAK_VALUE_MODIFIER, 30)
    assert rule.attribute_effect('FOAT', 4, {'MorrowindEffectIndex': None}) == (PEAK_VALUE_MODIFIER, 30)


def test_the_add_on_defines_the_one_hook_and_the_build_names_it():
    """It writes no record and touches no actor or weapon; a build with it records its name."""
    module = loader._load(ADD_ON)
    assert [hook for hook in loader.HOOKS + ('reset',) if hasattr(module, hook)] == ['attribute_effect']
    assert loader.enabled() == (module,)
    assert loader.build_stamp() == {loader.MANIFEST_KEY: [ADD_ON]}
    assert loader.attribute_effect('FOAT', 4, {}) == (PEAK_VALUE_MODIFIER, 30)
    assert loader.create_records(None, None) == {} and loader.actor_subrecords('NPC_', {}) == b''


# ---------------------------------------------------------------------------
# The records
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('listed', ['', None, 'weapon_type_keywords'], ids=['empty', 'unset', 'another add-on'])
def test_off_every_variant_is_the_record_the_converter_writes(monkeypatch, listed):
    """Without the add-on an attribute effect is a Script effect on no actor value, byte for byte the same build."""
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, '')
    plain, _writer = _build()
    if listed is None:
        monkeypatch.delenv(loader.EXTENSIONS_ENV_VAR)
    else:
        monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, listed)
    off, _writer = _build()
    assert off == plain and len(off) == len(ENTRIES) - 1
    for code, attribute in ATTRIBUTE_ENTRIES:
        assert _pair(off[_name(code, attribute)]) == SCRIPT_ON_NONE


def test_the_fixture_builds_a_variant_for_every_attribute_entry_and_the_skill_one():
    """Forty attribute variants and Fortify Blade; a fixed effect has none."""
    on, _writer = _build()
    assert sorted(on) == sorted([_name(*entry) for entry in ATTRIBUTE_ENTRIES] + ['TES4FOSKBlade'])


@pytest.mark.parametrize('code, attribute', ATTRIBUTE_ENTRIES)
def test_on_a_variant_differs_in_its_archetype_and_actor_value_alone(monkeypatch, code, attribute):
    """DATA offsets 64 and 68 hold the family's archetype and the attribute's actor value; no other byte moved."""
    on, _writer = _build()
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, '')
    off, _writer = _build()
    assert list(on) == list(off)
    name = _name(code, attribute)
    assert _differing_fields(on[name], off[name]) == [ARCHETYPE, ACTOR_VALUE]
    assert _pair(off[name]) == SCRIPT_ON_NONE
    assert _pair(on[name]) == (ARCHETYPES[code], ACTOR_VALUES[attribute])


@pytest.mark.parametrize('code', list(ARCHETYPES))
def test_on_a_variant_of_a_no_magnitude_source_effect_differs_in_its_flags_too(monkeypatch, code):
    """A value modifier may not keep No Magnitude, so four families' flags field changes as well; Absorb's does not."""
    source = [mgef(each, **{'DATA.Flags': str(EFFECTS[each][1] | magic.T4_NO_MAGNITUDE)}) for each in EFFECTS]
    on, _writer = _build(source)
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, '')
    off, _writer = _build(source)
    name, absorb = _name(code, 4), ARCHETYPES[code] == ABSORB
    assert _differing_fields(on[name], off[name]) == [magic.O_FLAGS][absorb:] + [ARCHETYPE, ACTOR_VALUE]
    flags_on, flags_off = (struct.unpack_from('<I', dict(subrecords(built[name]))['DATA'], magic.O_FLAGS)[0]
                           for built in (on, off))
    moved = magic.F_NO_MAGNITUDE | magic.F_POWER_AFFECTS_MAGNITUDE | magic.F_POWER_AFFECTS_DURATION
    assert flags_off & moved == magic.F_NO_MAGNITUDE | magic.F_POWER_AFFECTS_DURATION
    assert flags_on ^ flags_off == (0 if absorb else moved)


def test_a_skill_effect_and_a_fixed_effect_are_byte_identical_on_and_off(monkeypatch):
    """Fortify Blade's variant keeps its bytes, and no record is added or dropped."""
    on, on_writer = _build()
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, '')
    off, off_writer = _build()
    assert on['TES4FOSKBlade'] == off['TES4FOSKBlade']
    assert _pair(on['TES4FOSKBlade']) == (magic.get_archetype('FOSK'), magic.resolve_actor_value('FOSK', 14))
    assert [record[:24] for _sig, record in on_writer.records] == [record[:24] for _sig, record in off_writer.records]
    assert not any(name.startswith('TES4FOHE') for name in on)


def test_the_form_is_the_one_the_converter_gives_an_effect_the_runtime_does_not_take(monkeypatch):
    """With the runtime's claim switched off the converter itself writes each variant as the add-on does."""
    on, _writer = _build()
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, '')
    monkeypatch.setattr(magic_variants, 'runtime_attribute_index', lambda _index, _code, _av: -1)
    unclaimed, _writer = _build()
    assert on == unclaimed


@pytest.mark.parametrize('code, attribute', [('FOAT', 4), ('DRAT', 0), ('DGAT', 2), ('REAT', 5), ('ABAT', 7)])
def test_a_clone_of_a_variant_carries_its_pair_and_the_effect_table_lists_the_same_rows(
        monkeypatch, code, attribute):
    """A delivery clone and an Ability clone copy the variant's DATA; the runtime's list names all three, on or off."""
    rows, pairs = {}, {}
    for listed in (ADD_ON, ''):
        monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, listed)
        _built, writer = _build()
        variant = magic_variants.get_mgef_formid(code, attribute)
        delivered = magic_variants.delivery_variant(variant, 0, 0, writer)
        ability = magic_variants.ability_variant(delivered, code, 5, writer)
        assert len({variant, delivered, ability}) == 3
        by_fid = {struct.unpack_from('<I', record, 12)[0]: record for _sig, record in writer.records}
        pairs[listed] = [_pair(by_fid[fid]) for fid in (variant, delivered, ability)]
        rows[listed] = copy_rows('Invented.esp', 0)
        index = TES4_ATTRIBUTE_EFFECTS[code]
        assert {f'Invented.esp|{fid:08X}={index}:{attribute}' for fid in (variant, delivered, ability)} <= set(rows[listed])
        assert len(rows[listed]) == len(ATTRIBUTE_ENTRIES) + 2
        assert struct.unpack_from('<II', dict(subrecords(by_fid[delivered]))['DATA'], magic.O_CASTING_TYPE) == (0, 0)
    assert rows[ADD_ON] == rows['']
    assert pairs[ADD_ON] == [(ARCHETYPES[code], ACTOR_VALUES[attribute])] * 3
    assert pairs[''] == [SCRIPT_ON_NONE] * 3


@pytest.mark.parametrize('code', [effect_editor_id(79), 'FOAT'], ids=['its own name', 'a name of the five'])
def test_a_morrowind_effects_variant_is_unchanged(monkeypatch, code):
    """A record with a Morrowind effect index is written as without the add-on, even under one of the five codes."""
    record = dict(mgef('FOAT'), EditorID=code, MorrowindEffectIndex='79')
    on, _writer = _build([record], [(code, 4)])
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, '')
    off, _writer = _build([record], [(code, 4)])
    assert on == off and list(on) == [_name(code, 4)]
    assert _pair(on[_name(code, 4)]) == SCRIPT_ON_NONE


# ---------------------------------------------------------------------------
# The audit of a built plugin
# ---------------------------------------------------------------------------

#: A built master's two attribute effect records and the spell using them; a dependent's clone and its spell.
MASTER_EFFECTS = {0x01000A01: ('TES4FOATSpeed', 34, 30), 0x01000A02: ('TES4DGATLuckFFTargetActor', 0, 33)}
MASTER_ITEMS = {0x01000B01: [0x01000A01, 0x01000A02]}
CHILD_EFFECTS = {0x02000A01: ('TES4FOATSpeedConstantSelfAbility', 34, 30)}
CHILD_ITEMS = {0x02000B01: [0x02000A01]}
CHILD_ROW = 'Child.esp|02000A01=79:4'

#: A Morrowind plugin's Fortify Speed variant, as the converter names it.
MORROWIND_SPEED = magic_variants.av_variant_editor_id(effect_editor_id(79), 4)


def _group(sig: str, records: list, kind: int = 0) -> bytes:
    """A group of packed records labelled `sig`; a top group unless `kind` is set."""
    body = b''.join(records)
    return b'GRUP' + struct.pack('<I4sIII', 24 + len(body), sig.encode(), kind, 0, 0) + body


def _compressed(record: bytes) -> bytes:
    """A packed record as a plugin stores it compressed."""
    body = struct.pack('<I', len(record) - 24) + zlib.compress(record[24:])
    flags = struct.unpack_from('<I', record, 8)[0] | 0x00040000
    return record[:4] + struct.pack('<II', len(body), flags) + record[12:24] + body


def _effect(fid: int, edid: str, archetype: int, value: int, shape: str = '') -> bytes:
    """A packed MGEF; `shape` compresses it, puts an oversize subrecord before DATA, or cuts DATA short."""
    data = struct.pack('<64xIi80x', archetype, value)[:64 if shape == 'short' else None]
    big = (pack_subrecord('XXXX', struct.pack('<I', 70001)) + b'VMAD' + struct.pack('<H', 0) + b'\x07' * 70001
           if shape == 'oversize' else b'')
    record = pack_record('MGEF', fid, 0, pack_string_subrecord('EDID', edid) + big + pack_subrecord('DATA', data))
    return _compressed(record) if shape == 'compressed' else record


def _rows(name: str, masters: list, effects: dict) -> list:
    """The rows the converter lists for a plugin's own attribute effect records, by the converter's own names."""
    cells = {edid: f'{index}:{attribute}' for code, index in TES4_ATTRIBUTE_EFFECTS.items() for attribute in range(8)
             for edid in [_name(code, attribute)] + magic_variants.copy_editor_ids(_name(code, attribute))}
    return [f'{name}|{fid:08X}={cells[effect[0]]}' for fid, effect in effects.items()
            if fid >> 24 == len(masters) and effect[0] in cells]


def _plugin(tmp_path, name: str, masters: list, effects: dict, items: dict, rows: list = None,
            kinds: dict = None, nested: bytes = b'') -> str:
    """Write a plugin and the runtime's list beside it, where the converter stages it.

    `effects` is {FormID: (EditorID, archetype, actor value[, shape])}, `items`
    {FormID: [EFID]}, each a SPEL unless `kinds` names its type. `rows` are
    the list's rows, the converter's when None; without rows there is no list.
    """
    header = pack_record('TES4', 0, 0, b''.join(pack_string_subrecord('MAST', master) for master in masters))
    made = {}
    for fid, efids in items.items():
        made.setdefault((kinds or {}).get(fid, 'SPEL'), []).append(pack_record(
            (kinds or {}).get(fid, 'SPEL'), fid, 0, pack_string_subrecord('EDID', f'Item{fid:X}') + b''.join(
                pack_subrecord('EFID', struct.pack('<I', efid)) + pack_subrecord('EFIT', bytes(12)) for efid in efids)))
    path = tmp_path / name
    path.write_bytes(header + _group('GLOB', [pack_record('GLOB', 0x900, 0, b'')]) + nested
                     + _group('MGEF', [_effect(fid, *effect) for fid, effect in effects.items()])
                     + b''.join(_group(sig, records) for sig, records in made.items()))
    listed = _rows(name, masters, effects) if rows is None else rows
    if listed:
        os.makedirs(sidecar_dir(str(path), name), exist_ok=True)
        with open(os.path.join(sidecar_dir(str(path), name), TELEPORTS_TABLE), 'w', encoding='utf-8') as handle:
            handle.write('\n'.join(listed) + '\n')
    return str(path)


def _master_and_child(tmp_path, **child) -> list:
    """The audit's arguments for the built master and its dependent; `child` replaces parts of the dependent."""
    master = _plugin(tmp_path, 'Master.esm', ['Skyrim.esm'], MASTER_EFFECTS, MASTER_ITEMS)
    parts = dict({'effects': CHILD_EFFECTS, 'items': CHILD_ITEMS}, **child)
    return [master, _plugin(tmp_path, 'Child.esp', ['Skyrim.esm', 'Master.esm'], **parts),
            '--other-master', 'Skyrim.esm']


@pytest.mark.parametrize('pair, code', [((34, 30), audit.OK), ((1, -1), audit.MISMATCH), ((34, 26), audit.MISMATCH),
                                        ((0, 30), audit.MISMATCH)])
def test_the_audit_passes_a_built_plugin_only_when_each_attribute_entry_is_on_its_table_value(
        tmp_path, capsys, pair, code):
    """A master's variant, a dependent's clone of it and the items of both: one record off its pair fails the run."""
    master = _plugin(tmp_path, 'Master.esm', ['Skyrim.esm'], {
        0x01000A01: ('TES4FOATSpeed', 34, 30), 0x01000A02: ('TES4DGATLuckFFTargetActor', 0, 33),
        0x01000A03: ('TES4FOSKBlade', 34, 6)}, {0x01000B01: [0x01000A01, 0x01000A03, 0x00012345]})
    child = _plugin(tmp_path, 'Child.esp', ['Skyrim.esm', 'Master.esm'], {
        0x02000A01: ('TES4FOATSpeedConstantSelfAbility',) + pair},
        {0x02000B01: [0x02000A01, 0x01000A02], 0x02000B02: [0x01000A01]})
    assert audit.main([master, child, '--other-master', 'Skyrim.esm']) == code
    out = capsys.readouterr().out
    assert 'attribute effect records: 3, NOT on their table value: %d' % (code != audit.OK) in out
    assert "attribute effect entries: 4 ({'SPEL': 4}), NOT on their table value: %d" % (code != audit.OK) in out
    assert "other effect entries read (no attribute effect): 1; " in out and "{'skyrim.esm': 1}" in out
    assert "lists name alike: 3; Morrowind ones the lists name (the runtime's, not checked): 0; " in out


def test_the_audit_refuses_when_it_cannot_tell(tmp_path, capsys):
    """An entry into a plugin neither given nor named, and a run that met no attribute entry, are not a pass."""
    master = _plugin(tmp_path, 'Master.esm', ['Skyrim.esm'], {0x01000A01: ('TES4FOATSpeed', 34, 30)},
                     {0x01000B01: [0x01000A01, 0x00012345]})
    child = _plugin(tmp_path, 'Child.esp', ['Skyrim.esm', 'Master.esm'], {}, {0x02000B01: [0x01000A01]})
    plain = _plugin(tmp_path, 'Plain.esp', [], {0x00000A03: ('TES4FOSKBlade', 34, 6)}, {0x00000B01: [0x00000A03]})
    assert audit.main([master]) == audit.UNDETERMINED
    assert "neither given nor named with --other-master: {'skyrim.esm': 1}" in capsys.readouterr().out
    assert audit.main([child, '--other-master', 'skyrim.esm']) == audit.UNDETERMINED
    assert "{'master.esm': 1}" in capsys.readouterr().out
    assert audit.main([plain]) == audit.UNDETERMINED
    assert 'no attribute effect entry was found' in capsys.readouterr().out
    assert audit.main([master, child, '--other-master', 'SKYRIM.ESM']) == audit.OK


#: (what was done to the dependent, the parts replaced, the exit code, words of the report).
IDENTIFICATIONS = [
    ('as built', {}, audit.OK, 'lists name alike: 3; '),
    ('renamed and made a Script effect', {'effects': {0x02000A01: ('XES4FOATSpeedConstantSelfAbility', 1, -1)},
                                          'rows': [CHILD_ROW]},
     audit.MISMATCH, 'XES4FOATSpeedConstantSelfAbility (child.esp|000A01) is listed as FOAT Speed'),
    ('renamed alone', {'effects': {0x02000A01: ('XES4FOATSpeedConstantSelfAbility', 34, 30)}, 'rows': [CHILD_ROW]},
     audit.MISMATCH, 'named differently or by one alone: 1'),
    ('missing from its list', {'rows': ['Child.esp|02000A07=60']},
     audit.MISMATCH, 'TES4FOATSpeedConstantSelfAbility (child.esp|000A01) is in no list'),
    ('listed under another attribute', {'rows': ['Child.esp|02000A01=79:3']},
     audit.MISMATCH, 'TES4FOATSpeedConstantSelfAbility (child.esp|000A01) is listed as FOAT Agility'),
    ('listed under another family', {'rows': ['Child.esp|02000A01=17:4']}, audit.MISMATCH, 'is listed as DRAT Speed'),
    ('a listed record that is not there', {'rows': [CHILD_ROW, 'Child.esp|02000A09=85:7']},
     audit.MISMATCH, 'child.esp|000A09 is listed as ABAT Luck and is no effect record read'),
    ('no list beside it', {'rows': []}, audit.UNDETERMINED,
     "with no teleports_formid.txt beside them, so their records are identified once only: {'child.esp': 1}"),
    ('no item entry into its listed records', {'items': {0x02000B01: [0x01000A01]}}, audit.UNDETERMINED,
     "no item entry points at a record of 1 listed (plugin, family, attribute): ['child.esp FOAT Speed']"),
    ('a Morrowind effect the list names', {'effects': {**CHILD_EFFECTS, 0x02000A05: (MORROWIND_SPEED, 1, -1)},
                                           'rows': [CHILD_ROW, 'Child.esp|02000A05=79:4']},
     audit.OK, "lists name alike: 3; Morrowind ones the lists name (the runtime's, not checked): 1; "),
    ('a Morrowind effect listed under another attribute', {
        'effects': {**CHILD_EFFECTS, 0x02000A05: (MORROWIND_SPEED, 1, -1)},
        'rows': [CHILD_ROW, 'Child.esp|02000A05=79:3']},
     audit.MISMATCH, f'{MORROWIND_SPEED} (child.esp|000A05) is listed as FOAT Agility'),
]


@pytest.mark.parametrize('_done, child, code, words', IDENTIFICATIONS, ids=[row[0] for row in IDENTIFICATIONS])
def test_the_audit_holds_the_editor_ids_against_the_runtimes_list(tmp_path, capsys, _done, child, code, words):
    """A record the EditorIDs and the build's own list do not name alike fails the run; so does a list that is
    missing, and a listed family and attribute no item points into."""
    assert audit.main(_master_and_child(tmp_path, **child)) == code
    out = capsys.readouterr().out
    assert words in out
    assert 'attribute effect records: %d, NOT on their table value: 0' % (2 + ('TES4FOAT' in str(child.get(
        'effects', CHILD_EFFECTS)))) in out


def test_the_audit_reads_the_list_the_converter_stages(tmp_path):
    """The rows the converter writes for the variants it built are read back as each record's family and attribute."""
    built, _writer = _build()
    rows = copy_rows('Invented.esp', 0)
    path = tmp_path / TELEPORTS_TABLE
    path.write_text('\n'.join(['Invented.esp|00000A07=60', ''] + rows) + '\n', encoding='utf-8')
    by_fid = {struct.unpack_from('<I', record, 12)[0]: name for name, record in built.items()}
    listed = audit.read_list(str(path))
    assert len(listed) == len(rows) == len(ATTRIBUTE_ENTRIES)
    assert listed == {('invented.esp', fid): audit.expected(by_fid[fid])[:2] for fid in by_fid
                      if by_fid[fid] != 'TES4FOSKBlade'}
    assert audit.read_list(str(tmp_path / 'absent.txt')) is None
    for row in ('Invented.esp|00000A01=79:8', 'Invented.esp|00000A01=79:-1', 'Invented.esp|00000A01=60:4',
                'Invented.esp|00000A01=79:', 'Invented.esp|not hex=79:4'):
        path.write_text(row + '\n', encoding='utf-8')
        with pytest.raises(ValueError, match='not a row of an attribute effect: ' + row.replace('|', r'\|')):
            audit.read_list(str(path))


def test_the_audit_reads_a_compressed_record_and_an_oversize_subrecord_and_enters_top_groups_alone(tmp_path, capsys):
    """Both records are found on their pair, and a spell in a group that is no top group is not read."""
    stray = _group('SPEL', [pack_record('SPEL', 0x01000B09, 0, pack_subrecord('EFID', struct.pack('<I', 0x01000A01)))], 6)
    plugin = _plugin(tmp_path, 'Master.esm', ['Skyrim.esm'], {
        0x01000A01: ('TES4FOATSpeed', 34, 30, 'compressed'), 0x01000A02: ('TES4DRATLuck', 34, 33, 'oversize')},
        MASTER_ITEMS, nested=stray)
    assert audit.main([plugin]) == audit.OK
    out = capsys.readouterr().out
    assert 'Master.esm: 2 effect records, 1 items, 2 attribute effects listed' in out
    assert 'attribute effect records: 2, NOT on their table value: 0' in out
    assert "attribute effect entries: 2 ({'SPEL': 2}), NOT on their table value: 0" in out
    assert 'Fortify  Peak Value Modifier on SpeedMult' in out and 'Drain    Peak Value Modifier on CritChance' in out


@pytest.mark.parametrize('shape', ['compressed', 'oversize', 'short'])
def test_the_audit_fails_a_record_off_its_pair_whatever_its_shape(tmp_path, capsys, shape):
    """A compressed record or one behind an oversize subrecord is read, and a DATA too short to hold the pair is off."""
    pair = (34, 30) if shape == 'short' else (1, -1)
    plugin = _plugin(tmp_path, 'Master.esm', ['Skyrim.esm'], {
        **MASTER_EFFECTS, 0x01000A01: ('TES4FOATSpeed',) + pair + (shape,)}, MASTER_ITEMS)
    assert audit.main([plugin]) == audit.MISMATCH
    out = capsys.readouterr().out
    assert 'attribute effect records: 2, NOT on their table value: 1' in out
    assert ('archetype None on actor value None' if shape == 'short' else 'Script on none') + ', table Peak' in out


@pytest.mark.parametrize('first, override, code', [((34, 30), (1, -1), audit.MISMATCH), ((1, -1), (34, 30), audit.OK)])
def test_a_later_plugins_override_of_an_effect_record_is_the_one_audited(tmp_path, first, override, code):
    """The game loads the last plugin's version of a record, so that is the one held against the table."""
    master = _plugin(tmp_path, 'Master.esm', ['Skyrim.esm'], {0x01000A01: ('TES4FOATSpeed',) + first},
                     {0x01000B01: [0x01000A01]})
    child = _plugin(tmp_path, 'Child.esp', ['Skyrim.esm', 'Master.esm'], {0x01000A01: ('TES4FOATSpeed',) + override}, {})
    assert audit.main([master, child]) == code


def test_the_audit_counts_the_entries_of_all_five_item_types_and_a_given_plugin_named_twice(tmp_path, capsys):
    """Spell, enchantment, potion, ingredient and scroll are read; a plugin both given and named is still read."""
    kinds = dict(zip(range(0x01000B01, 0x01000B06), ('SPEL', 'ENCH', 'ALCH', 'INGR', 'SCRL')))
    plugin = _plugin(tmp_path, 'Master.esm', [], {0x00000A01: ('TES4FOATSpeed', 34, 30)},
                     {fid: [0x00000A01] for fid in kinds}, kinds=kinds)
    assert audit.main([plugin, '--other-master', 'Master.esm']) == audit.OK
    assert ("attribute effect entries: 5 ({'ALCH': 1, 'ENCH': 1, 'INGR': 1, 'SCRL': 1, 'SPEL': 1}), "
            "NOT on their table value: 0") in capsys.readouterr().out


def test_the_audit_fails_an_unused_record_off_its_pair_and_passes_over_a_name_that_only_holds_one(tmp_path, capsys):
    """A record no item points at is still held against the table; an EditorID is read from its start."""
    effects = {**MASTER_EFFECTS, 0x01000A03: ('NotTES4FOATSpeed', 1, -1)}
    items = {0x01000B01: [0x01000A01, 0x01000A02, 0x01000A03]}
    assert audit.main([_plugin(tmp_path, 'Master.esm', ['Skyrim.esm'], effects, items), '--other-master',
                       'Skyrim.esm']) == audit.OK
    assert 'other effect entries read (no attribute effect): 1; ' in capsys.readouterr().out
    unused = {**effects, 0x01000A04: ('TES4FOATSpeedConstantSelf', 1, -1)}
    assert audit.main([_plugin(tmp_path, 'Master.esm', ['Skyrim.esm'], unused, items), '--other-master',
                       'Skyrim.esm']) == audit.MISMATCH
    out = capsys.readouterr().out
    assert 'attribute effect records: 3, NOT on their table value: 1' in out
    assert "attribute effect entries: 2 ({'SPEL': 2}), NOT on their table value: 0" in out


def test_the_audits_help_says_the_other_master_is_a_name(capsys):
    """--other-master takes a file name; a path would never match a master's name."""
    with pytest.raises(SystemExit):
        audit.main(['--help'])
    assert 'file NAME, not a path, of a master the conversion did not build' in ' '.join(capsys.readouterr().out.split())


def test_the_audits_tables_are_the_add_ons():
    """The audit's second copy names the same archetype per family and actor value per attribute."""
    assert {code: archetype for code, (_family, archetype) in audit.FAMILIES.items()} == rule.FAMILY_ARCHETYPE
    assert dict(enumerate(audit.ATTRIBUTES.values())) == rule.ATTRIBUTE_VALUE
    assert list(audit.ATTRIBUTES) == [magic_variants.av_variant_editor_id('FOAT', index)[8:] for index in range(8)]
    assert (audit.O_ARCHETYPE, audit.O_ACTOR_VALUE) == (ARCHETYPE, ACTOR_VALUE)


def test_the_audits_list_names_and_place_are_the_converters():
    """The list's file, its folder, its effect indices and a Morrowind variant's EditorID, as the converter has them."""
    built = os.path.join('out', 'Some Plugin.esp')
    assert audit.list_path(built) == os.path.join(sidecar_dir(built, 'Some Plugin.esp'), TELEPORTS_TABLE)
    assert audit.LIST_FAMILIES == {index: code for code, index in TES4_ATTRIBUTE_EFFECTS.items()}
    assert set(audit.LIST_FAMILIES) == set(MW_ATTRIBUTE_EFFECTS)
    for index, code in audit.LIST_FAMILIES.items():
        for attribute, name in enumerate(audit.ATTRIBUTES):
            morrowind, oblivion = _name(effect_editor_id(index), attribute), _name(code, attribute)
            for edid in [morrowind] + magic_variants.copy_editor_ids(morrowind):
                assert audit._morrowind(edid) == (code, name) and audit.expected(edid) is None
            for edid in [oblivion] + magic_variants.copy_editor_ids(oblivion):
                assert audit.expected(edid)[:2] == (code, name) and audit._morrowind(edid) is None
