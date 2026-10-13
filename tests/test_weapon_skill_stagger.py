"""WEAP DNAM carries the governing skill at 76 and the stagger at 96.

See: docs/commentary/tes5_import_magic.md#weapon-skill-and-stagger
"""

import struct

import pytest

from tes5_import.base.constants import WEAPON_TYPE_MAP
from tes5_import.base.equivalents import (WEAPON_ANIM_CROSSBOW, WEAPON_ANIM_SKILL,
                                          WEAPON_ANIM_STAGGER, weapon_skill_stagger)
from tes5_import.record_types.equipment import convert_WEAP
from tools.esm.tes5_esm_reader import Sub, decode_subrecord

#: Skyrim actor-value indices of the three weapon skills.
ONE_HANDED, TWO_HANDED, MARKSMAN = 6, 7, 8

#: Skyrim animation types Dagger and WarAxe, which convert_WEAP picks by mesh name.
DAGGER, WAR_AXE = 2, 3

#: DNAM bytes 0-75 and 80-95 the tree wrote for these two before the fix.
SWORD_REST = ('010000000000803f6666a63f' + '00' * 14 + '01' + '00' * 17 + 'cdcc4c3f' + '00' * 28,
              '00' * 16)
BOW_REST = ('070000000000803f6666a63f' + '00' * 14 + '01' + '00' * 17 + 'cdcc4c3f' + '00' * 28,
            '00' * 16)


def _weap(tes4_type: int, model: str) -> dict:
    """An exported WEAP with invented names: TES4 type, mesh, speed 0.8 and reach 1.3."""
    return {'Signature': 'WEAP', 'FormID': '00012345', 'EditorID': 'TestWeapon',
            'RecordFlags': '0', 'FULL': 'Test Weapon', 'Model.MODL': model,
            'DATA.Type': str(tes4_type), 'DATA.Speed': '0.8', 'DATA.Reach': '1.3',
            'DATA.Value': '10', 'DATA.Weight': '5.0', 'DATA.Damage': '7'}


def _dnam(rec: dict) -> bytes:
    """The DNAM payload of the converted weapon."""
    record, pos = convert_WEAP(rec), 24
    while pos + 6 <= len(record):
        size = struct.unpack_from('<H', record, pos + 4)[0]
        if record[pos:pos + 4] == b'DNAM':
            return record[pos + 6:pos + 6 + size]
        pos += 6 + size
    raise AssertionError('no DNAM')


@pytest.mark.parametrize('tes4_type, model, anim, skill, stagger', [
    pytest.param(0, 'Weapons\\Iron\\LongSword.NIF', 1, ONE_HANDED, 0.75, id='sword'),
    pytest.param(0, 'Weapons\\Iron\\Dagger.NIF', 2, ONE_HANDED, 0.0, id='dagger'),
    pytest.param(2, 'Weapons\\Iron\\WarAxe.NIF', 3, ONE_HANDED, 0.85, id='war axe'),
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
    """-1, sign-extended over all four bytes, and no stagger."""
    dnam = _dnam(_weap(4, 'Weapons\\Staff\\Staff.NIF'))
    assert dnam[0] == 8
    assert dnam[76:80] == b'\xff\xff\xff\xff'
    assert struct.unpack_from('<i', dnam, 76)[0] == -1
    assert dnam[96:100] == bytes(4)


@pytest.mark.parametrize('tes4_type, model, rest', [
    pytest.param(0, 'Weapons\\Iron\\LongSword.NIF', SWORD_REST, id='sword'),
    pytest.param(5, 'Weapons\\Iron\\Bow.NIF', BOW_REST, id='bow'),
])
def test_the_other_92_bytes_are_what_they_were(tes4_type, model, rest):
    """Only the skill and the stagger moved: bytes 0-75 and 80-95 are pinned."""
    dnam = _dnam(_weap(tes4_type, model))
    assert (dnam[:76].hex(), dnam[80:96].hex()) == rest


def test_a_firearm_becomes_a_crossbow_with_the_bows_skill_and_no_stagger():
    """An FO3/FNV rifle is animation type 9: Marksman, stagger 0.0."""
    rifle = _weap(0, 'Weapons\\2HandRifle\\Rifle.NIF')
    rifle['DNAM.FalloutAnimType'] = '6'
    dnam = _dnam(rifle)
    assert dnam[0] == WEAPON_ANIM_CROSSBOW == 9
    assert struct.unpack_from('<i', dnam, 76)[0] == MARKSMAN
    assert dnam[96:100] == bytes(4)


@pytest.mark.parametrize('anim', [0, 10, 255], ids=['hand to hand', 'past crossbow', 'a byte'])
def test_an_animation_type_with_no_row_has_no_skill_and_no_stagger(anim):
    """HandToHand has no row, as nothing emits it: -1 like vanilla's Unarmed, and 0.0."""
    assert anim not in WEAPON_ANIM_SKILL and anim not in WEAPON_ANIM_STAGGER
    assert weapon_skill_stagger(anim) == (-1, 0.0)


def test_the_tables_cover_exactly_the_types_convert_weap_emits():
    """One row per emitted type in both tables; skills are weapon skills or none."""
    emitted = set(WEAPON_TYPE_MAP.values()) | {DAGGER, WAR_AXE, WEAPON_ANIM_CROSSBOW}
    assert set(WEAPON_ANIM_SKILL) == set(WEAPON_ANIM_STAGGER) == emitted
    assert set(WEAPON_ANIM_SKILL.values()) == {ONE_HANDED, TWO_HANDED, MARKSMAN, -1}
    assert all(0.0 <= value <= 1.25 for value in WEAPON_ANIM_STAGGER.values())
    assert all(weapon_skill_stagger(anim) == (WEAPON_ANIM_SKILL[anim], WEAPON_ANIM_STAGGER[anim])
               for anim in emitted)


def test_the_plugin_reader_decodes_the_skill_and_the_stagger():
    """tes5_esm_reader prints offset 76 as an int32 skill and offset 96 as a float stagger."""
    lines = decode_subrecord('WEAP', Sub('DNAM', _dnam(_weap(1, 'Weapons\\Iron\\Claymore.NIF'))),
                             False, {})
    assert 'DNAM.Skill=7' in lines and 'DNAM.Stagger=1.1000' in lines
    staff = decode_subrecord('WEAP', Sub('DNAM', _dnam(_weap(4, 'Weapons\\Staff\\Staff.NIF'))),
                             False, {})
    assert 'DNAM.Skill=-1' in staff and 'DNAM.Stagger=0.0000' in staff
    assert not any('ColorRemap' in line for line in lines + staff)
