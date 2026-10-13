"""The weapon-type keyword add-on: one vanilla type keyword per converted weapon, nothing else.

See: extensions/weapon_type_keywords/README.md
"""

import struct

import pytest

from core import extensions as loader
from extensions.weapon_type_keywords import keywords as wk
from tes5_import.base.equivalents import WEAPON_ANIM_EQUP
from tes5_import.overrides.builder import apply_changes
from tes5_import.overrides.diff import diff_records
from tes5_import.record_types.common import VENDOR_KYWD
from tes5_import.record_types.equipment import convert_WEAP
from tests.extension_records import WEAPONS, subrecords, weap

ADD_ON = 'weapon_type_keywords'

#: Skyrim.esm KYWD FormIDs by EditorID, as read out of the master.
VANILLA = {'WeapTypeSword': 0x0001E711, 'WeapTypeWarAxe': 0x0001E712, 'WeapTypeDagger': 0x0001E713,
           'WeapTypeMace': 0x0001E714, 'WeapTypeBow': 0x0001E715, 'WeapTypeStaff': 0x0001E716,
           'WeapTypeWarhammer': 0x0006D930, 'WeapTypeGreatsword': 0x0006D931,
           'WeapTypeBattleaxe': 0x0006D932}

#: The keyword each fixture weapon must get.
EXPECTED = {'sword': 'WeapTypeSword', 'dagger': 'WeapTypeDagger', 'war axe': 'WeapTypeWarAxe',
            'mace': 'WeapTypeMace', 'greatsword': 'WeapTypeGreatsword',
            'battleaxe': 'WeapTypeBattleaxe', 'warhammer': 'WeapTypeWarhammer',
            'bow': 'WeapTypeBow', 'staff': 'WeapTypeStaff'}

#: Skyrim.esm 000BABE4 Armsman00: the four keywords its HasKeyword conditions name.
ARMSMAN_KEYWORDS = {0x0001E714, 0x0001E711, 0x0001E712, 0x0001E713}


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    """Each test starts with only this add-on named."""
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, ADD_ON)


def _keywords(record: bytes) -> tuple:
    """(KSIZ count, [KWDA FormIDs]) of a packed WEAP."""
    subs = dict(subrecords(record))
    data = subs['KWDA']
    return struct.unpack('<I', subs['KSIZ'])[0], list(struct.unpack(f'<{len(data) // 4}I', data))


def test_the_table_names_the_vanilla_keywords():
    """Nine constants, the FormIDs Skyrim.esm gives those EditorIDs."""
    assert {wk.WEAP_TYPE_SWORD, wk.WEAP_TYPE_WAR_AXE, wk.WEAP_TYPE_DAGGER, wk.WEAP_TYPE_MACE,
            wk.WEAP_TYPE_BOW, wk.WEAP_TYPE_STAFF, wk.WEAP_TYPE_WARHAMMER, wk.WEAP_TYPE_GREATSWORD,
            wk.WEAP_TYPE_BATTLEAXE} == set(VANILLA.values())
    assert wk.TYPE_KEYWORDS == {
        1: VANILLA['WeapTypeSword'], 2: VANILLA['WeapTypeDagger'], 3: VANILLA['WeapTypeWarAxe'],
        4: VANILLA['WeapTypeMace'], 5: VANILLA['WeapTypeGreatsword'],
        6: VANILLA['WeapTypeWarhammer'], 7: VANILLA['WeapTypeBow'], 8: VANILLA['WeapTypeStaff'],
        9: VANILLA['WeapTypeBow']}


def test_every_animation_type_the_converter_writes_has_a_keyword_but_hand_to_hand():
    """The table covers each type the converter gives a weapon; type 0 has no vanilla keyword."""
    assert set(wk.TYPE_KEYWORDS) == set(WEAPON_ANIM_EQUP)
    assert wk.weapon_keywords({}, 0) == [] and wk.weapon_keywords({}, 77) == []
    for anim, keyword in wk.TYPE_KEYWORDS.items():
        assert wk.weapon_keywords({}, anim) == [keyword]


@pytest.mark.parametrize('name', sorted(WEAPONS))
def test_a_converted_weapon_carries_its_type_keyword_once_after_the_vendor_keyword(name):
    """KSIZ 2: the vendor keyword the converter always wrote, then the type keyword."""
    tes4_type, model, anim = WEAPONS[name]
    record = convert_WEAP(weap(tes4_type, model))
    assert dict(subrecords(record))['DNAM'][0] == anim
    vendor = VENDOR_KYWD['Staff' if tes4_type == 4 else 'Weapon']
    count, keywords = _keywords(record)
    assert count == len(keywords) == 2
    assert keywords == [vendor, VANILLA[EXPECTED[name]]]
    assert keywords.count(VANILLA[EXPECTED[name]]) == 1


@pytest.mark.parametrize('model, keyword', [
    ('Weapons\\Iron\\BattleAxe.NIF', 'WeapTypeBattleaxe'),
    ('weapons/steel/battleaxe.nif', 'WeapTypeBattleaxe'),
    ('Weapons\\Iron\\Warhammer.NIF', 'WeapTypeWarhammer'),
    ('Weapons\\Axes\\Maul.NIF', 'WeapTypeWarhammer'),
    ('', 'WeapTypeWarhammer'),
])
def test_a_two_handed_blunt_weapon_is_an_axe_only_by_its_mesh_file_name(model, keyword):
    """Type 6 is a battleaxe by "axe" in the mesh file name, else a warhammer."""
    assert wk.weapon_keywords({'Model.MODL': model}, 6) == [VANILLA[keyword]]
    assert wk.weapon_keywords({}, 6) == [VANILLA['WeapTypeWarhammer']]


def test_the_mesh_name_decides_for_type_6_alone():
    """A one-handed sword on a mesh called an axe keeps the sword's keyword."""
    for anim, keyword in wk.TYPE_KEYWORDS.items():
        if anim != 6:
            assert wk.weapon_keywords({'Model.MODL': 'Weapons\\Iron\\BattleAxe.NIF'}, anim) == [keyword]


def test_a_crossbow_gets_the_bows_keyword():
    """Animation type 9 is filed with the bows."""
    assert wk.weapon_keywords({}, 9) == [VANILLA['WeapTypeBow']]


def test_the_one_handed_weapons_are_the_ones_armsman_tests():
    """Sword, dagger, war axe and mace carry a keyword Armsman00 names; no other does."""
    for name, (tes4_type, model, _anim) in WEAPONS.items():
        _count, keywords = _keywords(convert_WEAP(weap(tes4_type, model)))
        assert bool(ARMSMAN_KEYWORDS & set(keywords)) == (name in ('sword', 'dagger', 'war axe', 'mace')), name


@pytest.mark.parametrize('name', sorted(WEAPONS))
def test_nothing_else_in_the_record_changes_and_off_is_byte_identical(monkeypatch, name):
    """On and off differ in KSIZ and KWDA alone; off, the add-on leaves no byte."""
    tes4_type, model, _anim = WEAPONS[name]
    rec = weap(tes4_type, model)
    on_record = convert_WEAP(rec)
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, '')
    off_record = convert_WEAP(rec)
    monkeypatch.delenv(loader.EXTENSIONS_ENV_VAR)
    assert convert_WEAP(rec) == off_record
    on, off = subrecords(on_record), subrecords(off_record)
    assert _keywords(off_record) == (1, [VENDOR_KYWD['Staff' if tes4_type == 4 else 'Weapon']])
    assert [sig for sig, _d in on] == [sig for sig, _d in off]
    assert [pair for pair in on if pair[0] not in ('KSIZ', 'KWDA')] == \
        [pair for pair in off if pair[0] not in ('KSIZ', 'KWDA')]
    assert on_record[:4] == off_record[:4] and on_record[8:24] == off_record[8:24]
    assert len(on_record) == len(off_record) + 4


@pytest.mark.parametrize('edit, keyword', [({'DATA.Type': '2'}, 'WeapTypeMace'),
                                           ({'DATA.Type': '4'}, 'WeapTypeStaff'),
                                           ({'DATA.Damage': '99'}, 'WeapTypeSword')],
                         ids=['blade to blunt', 'blade to staff', 'damage alone'])
def test_under_one_list_an_override_carries_the_keyword_of_what_it_makes_the_weapon(edit, keyword):
    """A master's sword with the author's change put in: KSIZ counts KWDA, and the type keyword is the result's."""
    master = weap(0, 'Weapons\\Iron\\LongSword.NIF')
    plugin = dict(master, **edit)
    record, _applied, _unmapped = apply_changes(convert_WEAP(master), diff_records(master, plugin), plugin, master)
    count, keywords = _keywords(record)
    assert count == len(keywords) == 2 and keywords[1] == VANILLA[keyword]
    assert (count, keywords) == _keywords(convert_WEAP(plugin))


def test_no_material_keyword_and_no_record_of_its_own():
    """The add-on defines the weapon hook alone: it writes no record and touches no actor."""
    module = loader._load(ADD_ON)
    assert not hasattr(module, 'create_records') and not hasattr(module, 'actor_subrecords')
    assert loader.create_records(None, None) == {}
    assert loader.actor_subrecords('NPC_', {}) == b''
    for tes4_type, model, _anim in WEAPONS.values():
        _count, keywords = _keywords(convert_WEAP(weap(tes4_type, model)))
        assert set(keywords[1:]) <= set(VANILLA.values())
