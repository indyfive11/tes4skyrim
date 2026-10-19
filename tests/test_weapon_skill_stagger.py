"""WEAP DNAM carries the governing skill at 76 and the stagger at 96.

See: docs/commentary/tes5_import_magic.md#weapon-skill-and-stagger
"""

import struct

import pytest

from tes5_import.base import equivalents
from tes5_import.base.constants import WEAPON_TYPE_MAP
from tes5_import.record_types import equipment
from tools.esm.tes5_esm_reader import Sub, decode_subrecord

#: Skyrim actor-value indices of the three weapon skills.
ONE_HANDED, TWO_HANDED, MARKSMAN = 6, 7, 8

#: Skyrim animation types HandToHand, Dagger, WarAxe, Staff and Crossbow.
HAND_TO_HAND, DAGGER, WAR_AXE, STAFF, CROSSBOW = 0, 2, 3, 8, 9

#: One TES4 type and mesh for each animation type an Oblivion weapon can take.
EVERY_TYPE = ((0, 'Weapons\\Iron\\LongSword.NIF'), (0, 'Weapons\\Iron\\Dagger.NIF'),
              (2, 'Weapons\\Iron\\WarAxe.NIF'), (2, 'Weapons\\Iron\\Mace.NIF'),
              (1, 'Weapons\\Iron\\Claymore.NIF'), (3, 'Weapons\\Iron\\BattleAxe.NIF'),
              (5, 'Weapons\\Iron\\Bow.NIF'), (4, 'Weapons\\Staff\\Staff.NIF'))


def _weap(tes4_type: int, model: str) -> dict:
    """An exported WEAP with invented names: TES4 type, mesh, speed 0.8 and reach 1.3."""
    return {'Signature': 'WEAP', 'FormID': '00012345', 'EditorID': 'TestWeapon',
            'RecordFlags': '0', 'FULL': 'Test Weapon', 'Model.MODL': model,
            'DATA.Type': str(tes4_type), 'DATA.Speed': '0.8', 'DATA.Reach': '1.3',
            'DATA.Value': '10', 'DATA.Weight': '5.0', 'DATA.Damage': '7'}


class _Writer:
    """What `convert_WEAP` asks of the plugin writer: a FormID per key, a place for records."""

    def __init__(self):
        self.records = []

    def derive_formid(self, _site, key) -> int:
        """A FormID that is a function of the key alone."""
        return 0x01000000 | (hash(str(key)) & 0x00FFFFFF)

    def add_record(self, signature: str, record: bytes) -> None:
        """Keep a companion record."""
        self.records.append((signature, record))


def _dnam(rec: dict, writer=None) -> bytes:
    """The DNAM payload of the converted weapon."""
    record, pos = equipment.convert_WEAP(rec, writer), 24
    while pos + 6 <= len(record):
        size = struct.unpack_from('<H', record, pos + 4)[0]
        if record[pos:pos + 4] == b'DNAM':
            return record[pos + 6:pos + 6 + size]
        pos += 6 + size
    raise AssertionError('no DNAM')


def _blank_dnam(skill: int, stagger: float) -> bytes:
    """A 100-byte DNAM holding only a skill at 76 and a stagger at 96."""
    dnam = bytearray(100)
    struct.pack_into('<i', dnam, 76, skill)
    struct.pack_into('<f', dnam, 96, stagger)
    return bytes(dnam)


@pytest.mark.parametrize('tes4_type, model, anim, skill, stagger', [
    pytest.param(0, 'Weapons\\Iron\\LongSword.NIF', 1, ONE_HANDED, 0.75, id='sword'),
    pytest.param(0, 'Weapons\\Iron\\Dagger.NIF', DAGGER, ONE_HANDED, 0.0, id='dagger'),
    pytest.param(2, 'Weapons\\Iron\\WarAxe.NIF', WAR_AXE, ONE_HANDED, 0.85, id='war axe'),
    pytest.param(2, 'Weapons\\Iron\\Mace.NIF', 4, ONE_HANDED, 1.0, id='mace'),
    pytest.param(1, 'Weapons\\Iron\\Claymore.NIF', 5, TWO_HANDED, 1.1, id='greatsword'),
    pytest.param(3, 'Weapons\\Iron\\BattleAxe.NIF', 6, TWO_HANDED, 1.15, id='battleaxe'),
    pytest.param(5, 'Weapons\\Iron\\Bow.NIF', 7, MARKSMAN, 0.0, id='bow'),
])
def test_skill_is_an_int32_at_76_and_stagger_a_float_at_96(tes4_type, model, anim, skill,
                                                            stagger):
    """Each weapon type parses back to its vanilla skill and its vanilla stagger."""
    dnam = _dnam(_weap(tes4_type, model))
    assert len(dnam) == 100 and dnam[0] == anim
    assert struct.unpack_from('<i', dnam, 76)[0] == skill
    assert dnam[76:80] == bytes([skill, 0, 0, 0])
    assert dnam[96:100] == struct.pack('<f', stagger)


def test_a_staff_has_no_skill():
    """-1 over exactly four bytes, and no stagger."""
    dnam = _dnam(_weap(4, 'Weapons\\Staff\\Staff.NIF'))
    assert dnam[0] == STAFF
    assert dnam[76:80] == b'\xff\xff\xff\xff'
    assert struct.unpack_from('<i', dnam, 76)[0] == -1
    assert dnam[96:100] == bytes(4)


def test_the_import_path_with_a_writer_writes_the_same_two_fields():
    """The record loop hands `convert_WEAP` a writer: skill and stagger are there too."""
    for tes4_type, model in EVERY_TYPE:
        rec, writer = _weap(tes4_type, model), _Writer()
        assert _dnam(rec, writer)[76:] == _dnam(rec)[76:], model
        assert writer.records, model


def test_the_unused_bytes_beside_the_two_fields_stay_zero():
    """Bytes 72-75, 80-87 and 92-95 are zero for every type, as in vanilla."""
    for tes4_type, model in EVERY_TYPE:
        dnam = _dnam(_weap(tes4_type, model))
        assert dnam[72:76] + dnam[80:88] + dnam[92:96] == bytes(16), model


def test_a_firearm_becomes_a_crossbow_with_the_bows_skill_and_no_stagger():
    """An FO3/FNV rifle is animation type 9: Marksman, stagger 0.0."""
    rifle = _weap(0, 'Weapons\\2HandRifle\\Rifle.NIF')
    rifle['DNAM.FalloutAnimType'] = '6'
    dnam = _dnam(rifle)
    assert dnam[0] == equivalents.WEAPON_ANIM_CROSSBOW == CROSSBOW
    assert struct.unpack_from('<i', dnam, 76)[0] == MARKSMAN
    assert dnam[76:80] == bytes([MARKSMAN, 0, 0, 0])
    assert dnam[96:100] == bytes(4)


def test_a_type_no_table_knows_is_written_with_no_skill_and_no_stagger(monkeypatch):
    """A weapon forced to HandToHand, which has no row, carries -1 and 0.0."""
    monkeypatch.setattr(equipment, 'refine_fallout_anim_type', lambda rec, anim: HAND_TO_HAND)
    dnam = _dnam(_weap(0, 'Weapons\\Iron\\LongSword.NIF'))
    assert dnam[0] == HAND_TO_HAND
    assert struct.unpack_from('<i', dnam, 76)[0] == -1
    assert dnam[96:100] == bytes(4)


@pytest.mark.parametrize('anim', [0, 10, 255], ids=['hand to hand', 'past crossbow', 'a byte'])
def test_an_animation_type_with_no_row_has_no_skill_and_no_stagger(anim):
    """HandToHand has no row, as nothing emits it: -1 like vanilla's Unarmed, and 0.0."""
    assert anim not in equivalents.WEAPON_ANIM_SKILL
    assert anim not in equivalents.WEAPON_ANIM_STAGGER
    assert equivalents.weapon_skill_stagger(anim) == (-1, 0.0)


def test_the_tables_cover_exactly_the_types_convert_weap_emits():
    """One row per emitted type in both tables; skills are weapon skills or none."""
    skills, staggers = equivalents.WEAPON_ANIM_SKILL, equivalents.WEAPON_ANIM_STAGGER
    emitted = set(WEAPON_TYPE_MAP.values()) | {DAGGER, WAR_AXE, CROSSBOW}
    assert set(skills) == set(staggers) == emitted
    assert set(skills.values()) == {ONE_HANDED, TWO_HANDED, MARKSMAN, -1}
    assert all(0.0 <= value <= 1.25 for value in staggers.values())
    assert all(equivalents.weapon_skill_stagger(anim) == (skills[anim], staggers[anim])
               for anim in emitted)


@pytest.mark.parametrize('skill, stagger, lines', [
    pytest.param(TWO_HANDED, 1.1, ('DNAM.Skill=7', 'DNAM.Stagger=1.1000'), id='greatsword'),
    pytest.param(-1, 0.0, ('DNAM.Skill=-1', 'DNAM.Stagger=0.0000'), id='staff'),
])
def test_the_plugin_reader_decodes_the_skill_and_the_stagger(skill, stagger, lines):
    """The reader prints offset 76 as an int32 skill, offset 96 as a float stagger."""
    decoded = decode_subrecord('WEAP', Sub('DNAM', _blank_dnam(skill, stagger)), False, {})
    assert [line for line in decoded if 'Skill' in line or 'Stagger' in line] == list(lines)
    assert not any('ColorRemap' in line for line in decoded)


def test_the_plugin_reader_reads_back_what_the_converter_wrote():
    """A converted greatsword, through the reader: TwoHanded and 1.1."""
    decoded = decode_subrecord(
        'WEAP', Sub('DNAM', _dnam(_weap(1, 'Weapons\\Iron\\Claymore.NIF'))), False, {})
    assert 'DNAM.Skill=7' in decoded and 'DNAM.Stagger=1.1000' in decoded
