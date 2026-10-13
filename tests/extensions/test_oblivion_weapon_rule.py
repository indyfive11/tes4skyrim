"""The Oblivion weapon-rule add-on: band tables, the perk's bytes, who lists it, and its audit.

Every record is made through the loader's hooks, the way the pipeline makes it.

See: extensions/oblivion_weapon_rule/README.md
"""

import hashlib
import json
import os
import struct
import subprocess
import sys
import types

import pytest

from core import extensions as loader
from extensions.oblivion_weapon_rule import audit
from extensions.oblivion_weapon_rule import rule as wr
from tes5_import.actors import attack_score, stat_factions
from tes5_import.base import owned_records
from tes5_import.base.conditions import build_ctda
from tes5_import.base.owned_records import (FALL_DAMAGE_ENTRY, WELL_KNOWN_PROPERTIES,
                                            create_missing_globals)
from tes5_import.base.writer import PluginWriter, pack_subrecord
from tes5_import.record_types import magic_morrowind
from tes5_import.record_types.creature import convert_CREA
from tes5_import.record_types.npc import convert_NPC_

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: The add-on's name in the settings, and its switch global's EditorID.
ADD_ON = 'oblivion_weapon_rule'
WEAPON_RULE_GLOBAL = wr.WEAPON_RULE_GLOBAL

# ---------------------------------------------------------------------------
# Vanilla reference bytes, decoded from the official masters
# ---------------------------------------------------------------------------

def _vanilla_ctda(kind: str, value: str, func: str, param: str) -> str:
    """Hex of a CTDA subrecord with the fields the cited perks all share."""
    return ('43544441' '2000' + kind + '000000' + value + func + '0000' + param
            + '00000000' '00000000' '00000000' 'ffffffff')


#: PRKE (rank 0, priority 0), EPFT (float) and PRKF as every cited entry writes them.
_PRKE, _EPFT, _PRKF = '50524b45' '0300' '020000', '45504654' '0100' '01', '50524b46' '0000'

#: Skyrim.esm 0010C708 MS02IncreaseDamage: point 35, target tab in either of two factions, x3.
MS02_INCREASE_DAMAGE = (
    _PRKE + '44415441' '0300' '230303' + '50524b43' '0100' '02'
    + _vanilla_ctda('01', '0000803f', '4700', '6c810200')
    + _vanilla_ctda('01', '0000803f', '4700', '98350400')
    + _EPFT + '45504644' '0400' '00004040' + _PRKF)

#: Skyrim.esm 001069BB MS02ReduceDamage: point 36, attacker tab in either of two factions, x0.25.
MS02_REDUCE_DAMAGE = (
    _PRKE + '44415441' '0300' '240303' + '50524b43' '0100' '01'
    + _vanilla_ctda('01', '0000803f', '4700', '6c810200')
    + _vanilla_ctda('01', '0000803f', '4700', '98350400')
    + _EPFT + '45504644' '0400' '0000803e' + _PRKF)

#: Dragonborn.esm 0203CA73 DLC2ArrowDefense: point 36, attacker tab then attacker's-weapon tab, x0.5.
DLC2_ARROW_DEFENSE = (
    _PRKE + '44415441' '0300' '240303' + '50524b43' '0100' '01'
    + _vanilla_ctda('00', '0000803f', '1e01', '00000000')
    + '50524b43' '0100' '02' + _vanilla_ctda('00', '0000803f', '3002', '15e70100')
    + _EPFT + '45504644' '0400' '0000003f' + _PRKF)

#: Skyrim.esm 001046BD crDragonResistNPCs: point 36, attacker tab HasKeyword == 0, PRKE priority 100.
CR_DRAGON_RESIST_NPCS = (
    '50524b45' '0300' '020064' + '44415441' '0300' '240303' + '50524b43' '0100' '01'
    + _vanilla_ctda('00', '00000000', '3002', '06930900')
    + _EPFT + '45504644' '0400' '0000003f' + _PRKF)

#: Skyrim.esm 00058210 Backstab: point 18, weapon tab IsWeaponSkillType OneHanded, x2.
BACKSTAB = (
    _PRKE + '44415441' '0300' '120303' + '50524b43' '0100' '01'
    + _vanilla_ctda('01', '0000803f', '6d00', '06000000')
    + _EPFT + '45504644' '0400' '00000040' + _PRKF)

#: Dragonborn.esm 02017745 DLC2MageArmor30NPC: point 29, owner tab GetBaseActorValue 30 to 50, x2.
DLC2_MAGE_ARMOR_30_NPC = (
    _PRKE + '44415441' '0300' '1d0303' + '50524b43' '0100' '00'
    + _vanilla_ctda('60', '0000f041', '1501', '12000000')
    + _vanilla_ctda('80', '00004842', '1501', '12000000')
    + '50524b43' '0100' '01' + _vanilla_ctda('00', '0000803f', '3002', '72ea0100')
    + _EPFT + '45504644' '0400' '00000040' + _PRKF)

#: The player at level 15 to 19, as CWSoldierExtraDamageToPlayer tests it on two tabs.
_CW_BAND = (_vanilla_ctda('00', '0000803f', '4800', '07000000')
            + _vanilla_ctda('60', '00007041', '5000', '00000000')
            + _vanilla_ctda('80', '0000a041', '5000', '00000000'))

#: Skyrim.esm 0010B1D9 CWSoldierExtraDamageToPlayer, first entry: point 35, PRKE priority 50, x2.
CW_SOLDIER_EXTRA_DAMAGE = (
    '50524b45' '0300' '020032' + '44415441' '0300' '230303'
    + '50524b43' '0100' '00' + _CW_BAND + '50524b43' '0100' '02' + _CW_BAND
    + _EPFT + '45504644' '0400' '00000040' + _PRKF)

#: The first entry's subrecords (PRKE ... PRKF) of each cited perk: {name: (FormID, hex)}.
VANILLA_ENTRIES = {
    'MS02IncreaseDamage': (0x0010C708, MS02_INCREASE_DAMAGE),
    'MS02ReduceDamage': (0x001069BB, MS02_REDUCE_DAMAGE),
    'DLC2ArrowDefense': (0x0203CA73, DLC2_ARROW_DEFENSE),
    'crDragonResistNPCs': (0x001046BD, CR_DRAGON_RESIST_NPCS),
    'Backstab': (0x00058210, BACKSTAB),
    'DLC2MageArmor30NPC': (0x02017745, DLC2_MAGE_ARMOR_30_NPC),
    'CWSoldierExtraDamageToPlayer': (0x0010B1D9, CW_SOLDIER_EXTRA_DAMAGE),
}

#: Skyrim.esm MS02IncreaseDamage (0010C708) PERK DATA: a hidden, unplayable one-rank perk.
VANILLA_HIDDEN_PERK_DATA = '0000010001'

#: Skyrim.esm NPC_ 000173C2: SPCT, PRKZ and COCT payloads and the order they sit in.
VANILLA_ACTOR_ORDER = ('RNAM', 'SPCT', 'SPLO', 'PRKZ', 'PRKR', 'COCT')
VANILLA_PRKZ, VANILLA_PRKR_HEAD = '01000000', '7b990e0001'

#: Bytes the converter's own perk helpers give their callers; the add-on leaves them alone.
OLD_FALL_ENTRY = ('50524b4503000200004441544103003a030145504654010001'
                  '4550464404000000000050524b460000')
OLD_FALL_PERK = ('5045524b5600000000000000bc0a0001000000002c000000454449441500544553344e6f'
                 '46616c6c44616d6167655065726b004445534301000044415441050000000100'
                 '0150524b4503000200004441544103003a030145504654010001455046440400'
                 '0000000050524b460000')
OLD_DODGE_37 = ('50524b45030002000044415441030024030350524b43010000'
                '43544441200000000000000014424900000023010001000000000000000000000000ffffffff'
                '43544441200080000000000014424d00000000000000000000000000000000000000ffffffff'
                '455046540100014550464404000000000050524b460000')
OLD_SANCTUARY = (12362, '360f2dfa8c4850e61ad38274c8b01dc04f1743b13e1602f71f232abe94c4c2a8')

#: CTDA functions the vanilla fixtures use that the rule does not.
_HAS_KEYWORD, _GET_LEVEL, _FUNC_286 = 560, 80, 286


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

#: Oblivion.exe's compiled-in settings, and the set Oblivion.esm authors (seven of the eight).
EXE = {'fDamageWeaponMult': 1.0, 'fDamageWeaponConditionBase': 0.0,
       'fDamageWeaponConditionMult': 1.0, 'fDamageSkillBase': 0.2, 'fDamageSkillMult': 1.8,
       'fDamageStrengthBase': 0.5, 'fDamageStrengthMult': 1.0, 'fFatigueBase': 1.25}
AUTHORED = {'fDamageWeaponMult': 0.5, 'fDamageWeaponConditionBase': 0.5,
            'fDamageWeaponConditionMult': 0.5, 'fDamageSkillBase': 0.2, 'fDamageSkillMult': 1.5,
            'fDamageStrengthBase': 0.75, 'fDamageStrengthMult': 0.5, 'fFatigueBase': 1.0}
#: A skill base of 0 (a zero factor at skill 0) and a flat attribute term.
EDGE = dict(EXE, fDamageSkillBase=0.0, fDamageStrengthMult=0.0, fDamageStrengthBase=1.3)
SETTINGS = pytest.mark.parametrize('settings', [EXE, AUTHORED, EDGE], ids=['exe', 'authored', 'edge'])

#: Invented FormIDs for the forms the conditions name.
FORMS = {'gate': 0x01000A01, 'converted': 0x01000A02,
         'factions': {0: 0x01000A02, 3: 0x01000A03},
         'globals': {0: 0x01000A04, 3: 0x01000A05}}
AUDIT_FORMS = {'perk': 0x01000A10, 'gate': FORMS['gate'], 'converted': FORMS['converted'],
               'factions': {'Strength': 0x01000A02, 'Agility': 0x01000A03},
               'globals': {'Strength': 0x01000A04, 'Agility': 0x01000A05}}


class _Sub:
    """One subrecord the way the audit's reader hands it over."""

    def __init__(self, sig: str, data: bytes):
        """Keep the signature and payload."""
        self.type, self.data = sig, data


class _Rec:
    """A packed record parsed into `.subrecords`, as the audit reads one."""

    def __init__(self, packed: bytes):
        """Split the body after the 24-byte header."""
        self.form_id = struct.unpack_from('<I', packed, 12)[0]
        self.subrecords = [_Sub(s, d) for s, d in _subs(packed[24:])]


def _subs(body: bytes) -> list:
    """[(signature, payload)] of packed subrecords."""
    out, pos = [], 0
    while pos + 6 <= len(body):
        size = struct.unpack_from('<H', body, pos + 4)[0]
        out.append((body[pos:pos + 4].decode(), body[pos + 6:pos + 6 + size]))
        pos += 6 + size
    return out


def _perk(settings: dict, skyrim: dict = None) -> bytes:
    """The packed weapon-rule PERK for `settings`."""
    return wr.hidden_perk(AUDIT_FORMS['perk'], wr.WEAPON_RULE_PERK,
                       b''.join(wr.rule_entries(FORMS, settings, skyrim)))


def _entries(settings: dict, skyrim: dict = None) -> list:
    """The PERK's entries as the audit parses them back out of its bytes."""
    return audit.parse_entries(_Rec(_perk(settings, skyrim)))


def _ctda(func: int, param: int = 0, value: float = 1.0, op: int = 0, is_or: bool = False) -> bytes:
    """One packed CTDA subrecord."""
    return pack_subrecord('CTDA', build_ctda(func, param1=param, comp_value=value,
                                             operator=op, is_or=is_or))


class _Writer:
    """The writer surface the creators touch: stable derived ids, every record kept."""

    def __init__(self):
        """Start empty, allocating from 0x02000001."""
        self.fid, self.records, self._derived = 0x02000000, [], {}

    def derive_formid(self, site, key):
        """A fresh id the first time (site, key) is seen, the same one after."""
        if (site, key) not in self._derived:
            self.fid += 1
            self._derived[(site, key)] = self.fid
        return self._derived[(site, key)]

    def add_record(self, sig, data):
        """Keep each written record."""
        self.records.append((sig, data))


class _Index:
    """A master index answering `find_by_edid` from a table."""

    def __init__(self, table: dict):
        """`table` is {(sig, edid): FormID}."""
        self.table = table

    def find_by_edid(self, sig: bytes, edid: str) -> int:
        """The master's FormID, or 0."""
        return self.table.get((sig, edid), 0)


def _gmst(values: dict) -> list:
    """Exported GMST records for `values`."""
    return [{'Signature': 'GMST', 'EditorID': k, 'DATA.Value': str(v)} for k, v in values.items()]


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Each test starts with the add-on enabled and ends with no perk, stat faction or bound global."""
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, ADD_ON)
    saved = dict(WELL_KNOWN_PROPERTIES)
    stat_factions.create_stat_factions(None, None, wanted=False)
    wr.reset()
    yield
    stat_factions.create_stat_factions(None, None, wanted=False)
    wr.reset()
    WELL_KNOWN_PROPERTIES.clear()
    WELL_KNOWN_PROPERTIES.update(saved)


def _context(by_type: dict = None, master_index=None, master_export=None, tes4: bool = True):
    """The context the pipeline hands `create_records`, over the shared property table."""
    importer = types.SimpleNamespace(master_export=master_export, master_index=master_index)
    return loader.RecordContext(tes4, by_type or {}, importer, WELL_KNOWN_PROPERTIES)


def _root_run(writer, by_type: dict = None, master_index=None, master_export=None,
              tes4: bool = True) -> dict:
    """What the pipeline does for a source: globals, stat factions, then the loader's hook."""
    WELL_KNOWN_PROPERTIES.clear()
    if master_index is not None:
        for (sig, edid), fid in master_index.table.items():
            if sig == b'GLOB' and edid != WEAPON_RULE_GLOBAL:
                WELL_KNOWN_PROPERTIES[edid] = fid
    create_missing_globals(writer)
    WELL_KNOWN_PROPERTIES.update(stat_factions.create_stat_factions(writer, master_index, wanted=tes4))
    made = loader.create_records(writer, _context(by_type, master_index, master_export, tes4))
    WELL_KNOWN_PROPERTIES.update(made)
    return made


# ---------------------------------------------------------------------------
# The helpers reproduce vanilla bytes
# ---------------------------------------------------------------------------

def test_multiply_entry_reproduces_the_vanilla_entries_byte_for_byte():
    """Tabs, CTDA layout, EPFT/EPFD and PRKF: the same bytes the official masters hold."""
    multiply_entry = wr.multiply_entry
    built = {
        'MS02IncreaseDamage': multiply_entry(bytes((35, 3, 3)), {
            2: _ctda(71, 0x0002816C, is_or=True) + _ctda(71, 0x00043598, is_or=True)}, 3.0),
        'MS02ReduceDamage': multiply_entry(bytes((36, 3, 3)), {
            1: _ctda(71, 0x0002816C, is_or=True) + _ctda(71, 0x00043598, is_or=True)}, 0.25),
        'DLC2ArrowDefense': multiply_entry(bytes((36, 3, 3)), {
            1: _ctda(_FUNC_286), 2: _ctda(_HAS_KEYWORD, 0x0001E715)}, 0.5),
        'Backstab': multiply_entry(bytes((18, 3, 3)), {
            1: _ctda(wr.FUNC_IS_WEAPON_SKILL_TYPE, wr.ONE_HANDED, is_or=True)}, 2.0),
        'DLC2MageArmor30NPC': multiply_entry(bytes((29, 3, 3)), {
            0: _ctda(wr.FUNC_GET_BASE_ACTOR_VALUE, 18, 30.0, 0x60)
            + _ctda(wr.FUNC_GET_BASE_ACTOR_VALUE, 18, 50.0, 0x80),
            1: _ctda(_HAS_KEYWORD, 0x0001EA72)}, 2.0),
    }
    for name, entry in built.items():
        assert entry.hex() == VANILLA_ENTRIES[name][1], name


def test_multiply_entry_matches_vanilla_entries_whose_priority_is_not_zero():
    """PRKE's third byte is the priority; from DATA on the bytes are the vanilla ones."""
    band = _ctda(72, 7) + _ctda(_GET_LEVEL, 0, 15.0, 0x60) + _ctda(_GET_LEVEL, 0, 20.0, 0x80)
    multiply_entry = wr.multiply_entry
    built = {
        'CWSoldierExtraDamageToPlayer': multiply_entry(bytes((35, 3, 3)), {0: band, 2: band}, 2.0),
        'crDragonResistNPCs': multiply_entry(bytes((36, 3, 3)), {
            1: _ctda(_HAS_KEYWORD, 0x00099306, 0.0)}, 0.5),
    }
    for name, entry in built.items():
        vanilla = bytes.fromhex(VANILLA_ENTRIES[name][1])
        assert vanilla[:8] == entry[:8] and vanilla[6:9] in (b'\x02\x00\x32', b'\x02\x00\x64')
        assert entry[9:] == vanilla[9:], name


def test_the_rule_tests_are_the_vanilla_condition_bytes():
    """The rule's own condition builders give Backstab's weapon test and a vanilla band's bounds."""
    backstab = bytes.fromhex(VANILLA_ENTRIES['Backstab'][1])
    weapon = wr._weapon_is((wr.ONE_HANDED, wr.TWO_HANDED))
    assert weapon[:38] == backstab[backstab.index(b'CTDA'):][:38]
    mage = bytes.fromhex(VANILLA_ENTRIES['DLC2MageArmor30NPC'][1])
    start = mage.index(b'CTDA')
    assert wr._in_band(wr.FUNC_GET_BASE_ACTOR_VALUE, 18, (30, 50, 2.0)) == mage[start:start + 76]


def test_hidden_perk_header_is_a_vanilla_hidden_perks():
    """DATA of the rule's PERK equals MS02IncreaseDamage's."""
    subs = dict(_subs(_perk(EXE)[24:])[:3])
    assert subs['DATA'].hex() == VANILLA_HIDDEN_PERK_DATA


def test_the_add_ons_condition_packer_gives_the_converters_bytes():
    """The add-on packs its own CTDA; field for field it is the converter's `build_ctda`."""
    for func, param, value, op, is_or in ((74, 0x01000A01, 1.0, 0, False), (277, 6, 37.0, 0x60, False),
                                          (73, 0x01000A02, 101.0, 0x80, False), (109, 7, 1.0, 0, True),
                                          (71, 0x01000A02, 0.0, 0, False)):
        assert wr._test(func, param, value, op, is_or) == _ctda(func, param, value, op, is_or)


def test_the_converters_own_perk_helpers_emit_the_bytes_they_did():
    """The fall-damage entry and perk and every Sanctuary rank are unchanged, and the add-on's copies agree."""
    assert FALL_DAMAGE_ENTRY.hex() == OLD_FALL_ENTRY
    assert owned_records.multiply_entry(bytes((58, 3, 1))).hex() == OLD_FALL_ENTRY
    assert wr.multiply_entry(bytes((58, 3, 1)), {}, 0.0).hex() == OLD_FALL_ENTRY
    for build in (owned_records.hidden_perk, wr.hidden_perk):
        assert build(0x01000ABC, 'TES4NoFallDamagePerk', FALL_DAMAGE_ENTRY).hex() == OLD_FALL_PERK
    assert magic_morrowind._dodge_entry(0x01000123, 37).hex() == OLD_DODGE_37
    sanctuary = magic_morrowind.sanctuary_entries(0x01000123)
    assert (len(sanctuary), hashlib.sha256(sanctuary).hexdigest()) == OLD_SANCTUARY


# ---------------------------------------------------------------------------
# Bands
# ---------------------------------------------------------------------------

def _factors(settings: dict) -> dict:
    """{table name: its exact factor} as the rule defines them."""
    sky = wr.SKYRIM_SKILL_SETTINGS
    return {'skill': lambda v: wr.skill_factor(settings, v, sky['fDamageSkillMin'],
                                               sky['fDamageSkillMax']),
            'skill_pc': lambda v: wr.skill_factor(settings, v, sky['fDamagePCSkillMin'],
                                                  sky['fDamagePCSkillMax']),
            'attribute': lambda v: wr.attribute_factor(settings, v)}


@SETTINGS
def test_bands_tile_zero_to_the_cap_with_one_open_band_above(settings):
    """No gap, no overlap, no floor under the first band, no ceiling over the last."""
    for name, bands in wr.rule_tables(settings).items():
        assert bands[0][0] is None and bands[-1][:2] == (101, None), name
        assert [b[1] for b in bands[:-1]] == [b[0] for b in bands[1:]], name
        covered = [v for lo, hi, _value in bands[:-1] for v in range(lo or 0, hi)]
        assert covered == list(range(101)), name


@SETTINGS
def test_every_integer_is_within_the_budget_of_its_bands_value(settings):
    """The exact factor of each stat 0-100 is within 5 % of the value of the band holding it."""
    for name, factor in _factors(settings).items():
        for lo, hi, value in wr.rule_tables(settings)[name][:-1]:
            for stat in range(lo or 0, hi):
                exact = factor(stat)
                error = abs(value - exact) / exact if exact > 0 else abs(value)
                assert error <= wr.BAND_ERROR_BUDGET + 1e-12, (name, stat, value, exact)


@SETTINGS
def test_a_band_holds_the_harmonic_mean_of_its_ends_and_cannot_grow(settings):
    """Each value is 2ab/(a+b) of its end factors, and one more integer would break the budget."""
    for name, factor in _factors(settings).items():
        bands = wr.rule_tables(settings)[name]
        for lo, hi, value in bands[:-1]:
            a, b = factor(lo or 0), factor(hi - 1)
            assert value == pytest.approx(2 * a * b / (a + b) if a + b else 0.0, rel=1e-12)
            if hi <= 100:
                c = factor(hi)
                assert abs(c - a) / (c + a) > wr.BAND_ERROR_BUDGET, (name, lo, hi)
        assert bands[-1][2] == factor(100)


def test_the_open_band_is_exact_for_an_attribute_above_the_cap():
    """Oblivion caps the attribute at 100, so every rank above it has the cap's factor."""
    assert wr.attribute_factor(EXE, 127) == wr.attribute_factor(EXE, 100) == 1.5
    assert wr.rule_tables(EXE)['attribute'][-1] == (101, None, 1.5)


def test_different_settings_give_a_different_table():
    """The tables are a function of the source game's settings, each of them."""
    base = wr.rule_tables(EXE)
    assert wr.rule_tables(AUTHORED) != base
    for name in wr.OBLIVION_HIT_SETTINGS:
        moved = wr.rule_tables(dict(EXE, **{name: EXE[name] + 0.3}))
        assert moved != base, name
    assert wr.rule_tables(EXE, dict(wr.SKYRIM_SKILL_SETTINGS, fDamagePCSkillMax=2.0)) != base


def test_the_factor_is_oblivions_hit_over_skyrims_skill_factor():
    """At the exe defaults: 1 x 1 x 1.25 x (0.2 + 1.8 s) / (1 + s/2); Strength 0.5 + a."""
    assert wr.skill_factor(EXE, 100, 1.0, 1.5) == pytest.approx(1.25 * 2.0 / 1.5)
    assert wr.skill_factor(EXE, 0, 1.0, 1.5) == pytest.approx(0.25)
    assert wr.skill_factor(EXE, 150, 1.0, 1.5) == pytest.approx(1.25 * 2.0 / 1.75)
    assert wr.attribute_factor(EXE, 50) == pytest.approx(1.0)
    assert wr.attribute_factor(AUTHORED, 50) == pytest.approx(1.0)


def test_the_add_on_carries_the_exe_defaults_the_converter_holds():
    """Its eight defaults are the fixture's and the converter's own."""
    assert wr.OBLIVION_HIT_SETTINGS == EXE
    assert {name: attack_score._EXE_SETTINGS[name] for name in EXE} == EXE


def test_the_settings_reader_takes_the_plugins_values_over_the_exe_defaults():
    """Unauthored settings keep the exe value; `own` says whether the plugin authors one."""
    def read(by_type, master_export=None):
        """The rule's settings as the context reads them."""
        return _context(by_type, master_export=master_export).game_settings(wr.OBLIVION_HIT_SETTINGS)
    assert read({}) == (EXE, False)
    got, own = read({'GMST': _gmst({'fDamageSkillMult': 1.5, 'fOther': 9})})
    assert own and got == dict(EXE, fDamageSkillMult=1.5)
    assert read({'GMST': _gmst({'fAIMeleeWeaponMult': 5})}) == (EXE, False)
    master = {'00000001': {'Signature': 'GMST', 'EditorID': 'fFatigueBase', 'DATA.Value': '1.0'}}
    assert read({}, master) == (dict(EXE, fFatigueBase=1.0), False)
    assert wr.OBLIVION_HIT_SETTINGS == EXE


# ---------------------------------------------------------------------------
# The packed perk
# ---------------------------------------------------------------------------

@SETTINGS
def test_the_packed_perk_parses_back(settings):
    """Entry points 35 and 36, Multiply Value, three tabs, float values, known functions only."""
    entries = _entries(settings)
    assert {e['point'] for e in entries} == {35, 36}
    assert {(e['function'], e['tab_count'], e['type']) for e in entries} == {(3, 3, 1)}
    functions = {c[3] for e in entries for cs in e['tabs'].values() for c in cs}
    assert functions <= {71, 72, 73, 74, 109, 277}
    tables = wr.rule_tables(settings)
    dealt = [e for e in entries if e['point'] == 35]
    assert len(dealt) == 3 * len(tables['skill']) + 2 * len(tables['attribute'])
    values = sorted(e['value'] for e in dealt)
    want = sorted([b[2] for b in tables['skill']] * 3 + [b[2] for b in tables['attribute']] * 2)
    assert values == pytest.approx(want, rel=1e-6)


@SETTINGS
def test_every_entry_carries_the_global_gate_on_its_owner_tab(settings):
    """`GetGlobalValue(switch) == 1` is the first condition of tab 0 of every entry."""
    gate = (audit.OP_EQ, False, 1.0, audit.GET_GLOBAL, FORMS['gate'])
    entries = _entries(settings)
    assert entries and all(e['tabs'][0][0] == gate for e in entries)


@SETTINGS
def test_every_taken_entry_requires_an_unconverted_attacker(settings):
    """`GetInFaction(stat faction) == 0` heads the attacker tab of every entry-point-36 entry."""
    outsider = (audit.OP_EQ, False, 0.0, audit.GET_IN_FACTION, FORMS['converted'])
    taken = [e for e in _entries(settings) if e['point'] == 36]
    assert taken and all(e['tabs'][1][0] == outsider for e in taken)
    dealt = [e for e in _entries(settings) if e['point'] == 35]
    assert not any(c[3] == audit.GET_IN_FACTION for e in dealt for cs in e['tabs'].values()
                   for c in cs)


def test_skill_is_read_off_the_attacker_and_the_attribute_off_its_source():
    """Dealt: the owner's skill and faction rank. Taken: the attacker's skill, the player's global."""
    kinds = {}
    for entry in _entries(EXE):
        family, kind, audience, weapons, key, _band = audit.classify(entry, AUDIT_FORMS)
        kinds.setdefault((family, kind, audience), set()).add((weapons, key))
    assert kinds == {
        (35, 'skill', 'converted'): {((6,), 6), ((7,), 7), ((8,), 8)},
        (35, 'attribute', 'converted'): {((6, 7), 'Strength'), ((8,), 'Agility')},
        (36, 'skill', 'outsider'): {((6,), 6), ((7,), 7), ((8,), 8)},
        (36, 'attribute', 'player'): {((6, 7), 'Strength'), ((8,), 'Agility')},
    }
    ranks = [e for e in _entries(EXE) if any(c[3] == audit.GET_FACTION_RANK
                                             for cs in e['tabs'].values() for c in cs)]
    assert ranks and all(e['point'] == 35 for e in ranks)


def test_a_skyrim_attackers_attribute_is_a_constant_only_when_it_is_not_one():
    """Attribute 50 is factor 1.0 under the exe and authored settings: no entry; else one per attribute."""
    def constants(settings):
        """The entries classified as a constant."""
        return [e for e in _entries(settings) if audit.classify(e, AUDIT_FORMS)[1] == 'constant']
    assert constants(EXE) == [] and constants(AUTHORED) == []
    got = constants(EDGE)
    assert [e['value'] for e in got] == [pytest.approx(1.3)] * 2
    assert all(audit.classify(e, AUDIT_FORMS)[2] == 'skyrim' for e in got)


def test_the_player_gets_its_own_skill_entries_only_when_its_skyrim_factor_differs():
    """One table serves every outsider while the player's Skyrim settings equal the NPCs'."""
    same = [audit.classify(e, AUDIT_FORMS)[2] for e in _entries(EXE) if e['point'] == 36]
    assert 'outsider' in same and 'skyrim' not in same
    split = dict(wr.SKYRIM_SKILL_SETTINGS, fDamagePCSkillMax=2.0)
    got = {audit.classify(e, AUDIT_FORMS)[:3] for e in _entries(EXE, split) if e['point'] == 36}
    assert got == {(36, 'skill', 'player'), (36, 'skill', 'skyrim'), (36, 'attribute', 'player')}


# ---------------------------------------------------------------------------
# The sweep: which entries a hit matches, and how far the product is off
# ---------------------------------------------------------------------------

@SETTINGS
def test_one_skill_entry_and_at_most_one_attribute_entry_match_any_hit(settings):
    """For each family, weapon skill, skill and attribute; and the product stays under the limit."""
    entries = _entries(settings)
    problems, worst, where = audit.sweep(entries, AUDIT_FORMS, settings)
    assert problems == []
    assert worst <= (1 + wr.BAND_ERROR_BUDGET) ** 2 - 1, where
    assert audit.check_values(entries, AUDIT_FORMS, settings)[0] == []
    assert audit.check_silence(entries, AUDIT_FORMS) == []


def test_matching_counts_by_hand_for_a_few_hits():
    """A converted swordsman, the player with a bow, and a Skyrim two-hander against a converted target."""
    entries = _entries(EXE)

    def kinds(point, attacker, weapon, skill, attribute):
        """Sorted kinds of the entries the hit matches."""
        hit = audit._hit(AUDIT_FORMS, weapon, skill, attribute, attacker)
        return sorted(audit.classify(e, AUDIT_FORMS)[1] for e in audit.matching(entries, point, hit))
    for skill in (0, 1, 37, 99, 100):
        for attribute in (0, 50, 100, 127):
            assert kinds(35, 'converted', 6, skill, attribute) == ['attribute', 'skill']
            assert kinds(36, 'player', 8, skill, attribute) == ['attribute', 'skill']
            assert kinds(36, 'skyrim', 7, skill, attribute) == ['skill']
            assert kinds(36, 'converted', 6, skill, attribute) == []
    hit = audit._hit(AUDIT_FORMS, 6, 40, 80, 'converted')
    product = 1.0
    for entry in audit.matching(entries, 35, hit):
        product *= entry['value']
    exact = 1.25 * (0.2 + 1.8 * 0.4) / 1.2 * (0.5 + 0.8)
    assert abs(product - exact) / exact < 0.1025


def test_no_weapon_no_entry():
    """Hand-to-hand and natural attacks have no weapon skill 6, 7 or 8, so nothing matches."""
    entries = _entries(EXE)
    for attacker, point in (('converted', 35), ('player', 36)):
        hit = audit._hit(AUDIT_FORMS, 0, 50, 50, attacker)
        hit['attacker']['av'] = {6: 50, 7: 50, 8: 50}
        assert audit.matching(entries, point, hit) == []


# ---------------------------------------------------------------------------
# The records the pipeline writes
# ---------------------------------------------------------------------------

def test_a_root_plugin_writes_the_switch_global_and_the_perk():
    """The global is an owned short at 1; the perk is written once and registered by EditorID."""
    writer = _Writer()
    got = _root_run(writer)
    glob = next(d for s, d in writer.records if s == 'GLOB' and WEAPON_RULE_GLOBAL.encode() in d)
    subs = dict(_subs(glob[24:]))
    assert subs['FNAM'] == b's' and struct.unpack('<f', subs['FLTV'])[0] == 1.0
    perks = [d for s, d in writer.records if s == 'PERK']
    assert len(perks) == 1 and got == {
        wr.WEAPON_RULE_PERK: struct.unpack_from('<I', perks[0], 12)[0],
        WEAPON_RULE_GLOBAL: struct.unpack_from('<I', glob, 12)[0]}
    forms = dict(AUDIT_FORMS, gate=WELL_KNOWN_PROPERTIES[WEAPON_RULE_GLOBAL],
                 converted=stat_factions.stat_faction(0),
                 factions={'Strength': stat_factions.stat_faction(0),
                           'Agility': stat_factions.stat_faction(3)},
                 globals={'Strength': WELL_KNOWN_PROPERTIES['TES4PlayerStrength'],
                          'Agility': WELL_KNOWN_PROPERTIES['TES4PlayerAgility']})
    entries = audit.parse_entries(_Rec(perks[0]))
    assert audit.check_values(entries, forms, EXE)[0] == []


def test_the_perk_is_built_from_the_plugins_own_settings():
    """A root plugin authoring the settings gets their table, not the exe's."""
    writer = _Writer()
    _root_run(writer, {'GMST': _gmst(AUTHORED)})
    perk = next(d for s, d in writer.records if s == 'PERK')
    values = sorted(e['value'] for e in audit.parse_entries(_Rec(perk)) if e['point'] == 35)
    tables = wr.rule_tables(AUTHORED)
    want = sorted([b[2] for b in tables['skill']] * 3 + [b[2] for b in tables['attribute']] * 2)
    assert values == pytest.approx(want, rel=1e-6)


def _master_table() -> dict:
    """A master that supplies every form the rule names, and the perk."""
    table = {(b'FACT', edid): 0x01000B00 + av for av, edid in stat_factions.STAT_FACTIONS.items()}
    table.update({(b'GLOB', edid): 0x01000C00 + i
                  for i, (edid, _c, _v) in enumerate(owned_records._OWNED_GLOBALS)})
    table[(b'GLOB', WEAPON_RULE_GLOBAL)] = 0x01000CF0
    table[(b'PERK', wr.WEAPON_RULE_PERK)] = 0x01000D00
    return table


def test_a_dependent_adopts_its_masters_perk():
    """Nothing is written, and its actors list the master's FormID."""
    writer = _Writer()
    got = _root_run(writer, master_index=_Index(_master_table()))
    assert got == {wr.WEAPON_RULE_PERK: 0x01000D00, WEAPON_RULE_GLOBAL: 0x01000CF0}
    assert writer.records == []
    assert loader.actor_subrecords('NPC_', NPC)[-8:] == struct.pack('<IB3x', 0x01000D00, 1)


def test_a_dependent_authoring_a_setting_overrides_the_perk_under_the_same_formid():
    """Its own table, written as an override of the master's record, over the master's forms."""
    writer = _Writer()
    got = _root_run(writer, {'GMST': _gmst({'fDamageSkillMult': 0.9})},
                    master_index=_Index(_master_table()))
    assert got[wr.WEAPON_RULE_PERK] == 0x01000D00
    assert [s for s, _d in writer.records] == ['PERK']
    perk = writer.records[0][1]
    assert struct.unpack_from('<I', perk, 12)[0] == 0x01000D00
    values = sorted(e['value'] for e in audit.parse_entries(_Rec(perk)) if e['point'] == 35)
    tables = wr.rule_tables(dict(EXE, fDamageSkillMult=0.9))
    want = sorted([b[2] for b in tables['skill']] * 3 + [b[2] for b in tables['attribute']] * 2)
    assert values == pytest.approx(want, rel=1e-6)
    assert struct.pack('<I', 0x01000B00) in perk and struct.pack('<I', 0x01000B03) in perk
    assert struct.pack('<I', 0x01000CF0) in perk


def test_a_dependent_authoring_an_unrelated_setting_writes_nothing():
    """Only the settings the table is built from make an override."""
    writer = _Writer()
    _root_run(writer, {'GMST': _gmst({'fAIMeleeWeaponMult': 9.0})},
              master_index=_Index(_master_table()))
    assert writer.records == []


def test_a_dependent_of_a_master_without_the_perk_writes_its_own():
    """A master built before the rule existed: the perk and the switch global are created."""
    table = _master_table()
    del table[(b'PERK', wr.WEAPON_RULE_PERK)], table[(b'GLOB', WEAPON_RULE_GLOBAL)]
    writer = _Writer()
    got = _root_run(writer, master_index=_Index(table))
    assert sorted(s for s, _d in writer.records) == ['GLOB', 'PERK']
    assert got[wr.WEAPON_RULE_PERK] >> 24 == 2


def test_no_perk_for_a_source_that_is_not_tes4_or_without_its_stat_factions():
    """Only the switch global is written, and no actor lists a perk."""
    for tes4 in (False, True):
        writer = _Writer()
        if tes4:
            WELL_KNOWN_PROPERTIES.clear()
            got = loader.create_records(writer, _context())
        else:
            got = _root_run(writer, tes4=False)
        assert list(got) == [WEAPON_RULE_GLOBAL]
        assert [s for s, _d in writer.records if s == 'PERK'] == []
        assert loader.actor_subrecords('NPC_', NPC) == b''


def test_the_source_kind_alone_keeps_the_perk_from_a_source_that_is_not_tes4():
    """With every stat faction in reach: a TES4 source gets the perk, any other the switch global alone."""
    for tes4 in (True, False):
        writer = _Writer()
        WELL_KNOWN_PROPERTIES.clear()
        create_missing_globals(writer)
        WELL_KNOWN_PROPERTIES.update(stat_factions.create_stat_factions(writer, None, wanted=True))
        got = loader.create_records(writer, _context(tes4=tes4))
        assert sorted(got) == sorted([WEAPON_RULE_GLOBAL] + [wr.WEAPON_RULE_PERK] * tes4)
        assert [s for s, _d in writer.records].count('PERK') == int(tes4)
        assert (loader.actor_subrecords('NPC_', NPC) != b'') is tes4


def test_a_later_plugin_without_the_rule_does_not_inherit_the_last_ones_perk():
    """The loader resets the add-on before each plugin's records."""
    _root_run(_Writer())
    assert loader.actor_subrecords('CREA', CREA) != b''
    _root_run(_Writer(), tes4=False)
    assert loader.actor_subrecords('CREA', CREA) == b''


def test_nothing_is_written_while_the_settings_do_not_name_the_add_on(monkeypatch):
    """No global, no perk, no perk list: the hook returns its neutral value untouched."""
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, '')
    writer = _Writer()
    assert _root_run(writer) == {}
    assert [s for s, _d in writer.records if s == 'PERK'] == []
    assert not any(WEAPON_RULE_GLOBAL.encode() in d for _s, d in writer.records)
    assert WEAPON_RULE_GLOBAL not in WELL_KNOWN_PROPERTIES
    assert loader.actor_subrecords('NPC_', NPC) == b''


# ---------------------------------------------------------------------------
# Who lists the perk
# ---------------------------------------------------------------------------

NPC = {'Signature': 'NPC_', 'FormID': '00000500', 'RecordFlags': '0', 'EditorID': 'TestFencer',
       'ACBS.Flags': '0', 'ACBS.Level': '5', 'ACBS.CalcMin': '5', 'ACBS.CalcMax': '10',
       'FactionCount': '0', 'SpellCount': '1', 'Spell[0]': '00000A11', 'ItemCount': '1',
       'Item[0].FormID': '00000A12', 'Item[0].Count': '1', 'AIPackageCount': '0',
       'DATA.Health': '50', 'DATA.Strength': '60', 'DATA.Agility': '45',
       'HCLR.R': '100', 'HCLR.G': '80', 'HCLR.B': '60'}
CREA = {'Signature': 'CREA', 'FormID': '00000501', 'RecordFlags': '0', 'EditorID': 'TestBoar',
        'SpellCount': '1', 'Spell[0]': '00000A11', 'FactionCount': '0', 'ItemCount': '1',
        'Item[0].FormID': '00000A12', 'Item[0].Count': '1', 'AIPackageCount': '0',
        'ACBS.Flags': '0', 'ACBS.Level': '3', 'DATA.Health': '40', 'DATA.Strength': '70'}


@pytest.mark.parametrize('convert, rec', [(convert_NPC_, NPC), (convert_CREA, CREA)],
                         ids=['npc', 'creature'])
def test_a_converted_actor_lists_the_perk_where_vanilla_does(convert, rec):
    """RNAM SPCT SPLO PRKZ PRKR COCT, with the vanilla PRKZ payload and rank byte."""
    writer = _Writer()
    perk = _root_run(writer)[wr.WEAPON_RULE_PERK]
    subs = _subs(convert(rec)[24:])
    order = [s for s, _d in subs if s in VANILLA_ACTOR_ORDER]
    assert tuple(order) == VANILLA_ACTOR_ORDER
    payload = dict(subs)
    assert payload['PRKZ'].hex() == VANILLA_PRKZ and len(payload['PRKR']) == 8
    assert payload['PRKR'] == struct.pack('<IB3x', perk, 1)
    assert payload['PRKR'][4:5].hex() == VANILLA_PRKR_HEAD[8:]
    factions = {struct.unpack_from('<I', d)[0] for s, d in subs if s == 'SNAM'}
    assert stat_factions.stat_faction(0) in factions


@pytest.mark.parametrize('convert, rec', [(convert_NPC_, NPC), (convert_CREA, CREA)],
                         ids=['npc', 'creature'])
def test_an_actor_of_a_source_without_the_rule_lists_no_perk(convert, rec):
    """No stat factions, no perk: the record is what it is without the add-on."""
    sigs = [s for s, _d in _subs(convert(rec)[24:])]
    assert 'PRKZ' not in sigs and 'PRKR' not in sigs


@pytest.mark.parametrize('convert, rec', [(convert_NPC_, NPC), (convert_CREA, CREA)],
                         ids=['npc', 'creature'])
def test_the_perk_list_is_the_only_difference_the_rule_makes_to_an_actor(convert, rec, monkeypatch):
    """Without the add-on in the settings the bytes equal the record minus PRKZ and PRKR."""
    writer = _Writer()
    _root_run(writer)
    with_rule = convert(rec)
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, '')
    without = convert(rec)
    assert b'PRKZ' in with_rule and b'PRKZ' not in without
    assert with_rule[24:].replace(
        pack_subrecord('PRKZ', struct.pack('<I', 1))
        + pack_subrecord('PRKR', struct.pack('<IB3x', writer._derived[('PERK', wr.WEAPON_RULE_PERK)], 1)),
        b'') == without[24:]


# ---------------------------------------------------------------------------
# FormID determinism
# ---------------------------------------------------------------------------

def test_the_perk_and_global_ids_do_not_depend_on_what_else_was_allocated():
    """Derived from the EditorID alone: other sites and another order leave them where they were."""
    first = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
    ids = (first.derive_formid('PERK', wr.WEAPON_RULE_PERK),
           first.derive_formid('GLOB', WEAPON_RULE_GLOBAL))
    second = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
    for key in range(50):
        second.derive_formid('OTFT', key)
    assert (second.derive_formid('GLOB', WEAPON_RULE_GLOBAL),
            second.derive_formid('PERK', wr.WEAPON_RULE_PERK)) == ids[::-1]


def test_the_switch_global_is_not_one_of_the_converters_and_moves_none_of_them():
    """The add-on owns it; each global's id is a function of its own name."""
    writer = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
    WELL_KNOWN_PROPERTIES.clear()
    create_missing_globals(writer)
    core = dict(WELL_KNOWN_PROPERTIES)
    assert WEAPON_RULE_GLOBAL not in core and 'TES4Fame' in core
    made = _root_run(PluginWriter(masters=['Skyrim.esm'], is_esm=True))
    assert {edid: WELL_KNOWN_PROPERTIES[edid] for edid in core} == core
    alone = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
    assert alone.derive_formid('GLOB', WEAPON_RULE_GLOBAL) == made[WEAPON_RULE_GLOBAL]
    assert alone.derive_formid('PERK', wr.WEAPON_RULE_PERK) == made[wr.WEAPON_RULE_PERK]


def test_two_runs_write_the_same_perk_bytes():
    """Same settings, same forms: the record is byte-identical."""
    runs = []
    for _ in range(2):
        writer = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
        _root_run(writer, {'GMST': _gmst(AUTHORED)})
        runs.append(writer._top_groups['PERK'])
    assert runs[0] == runs[1] and len(runs[0]) == 1


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------

def _built(tmp_path, settings: dict, tamper=None, recs=(NPC, CREA)):
    """Build a root plugin with two actors and its export on disk; (plugin path, export dir)."""
    writer = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
    _root_run(writer, {'GMST': _gmst(settings)})
    for rec in recs:
        writer.add_record('NPC_', (convert_NPC_ if rec['Signature'] == 'NPC_' else convert_CREA)(rec))
    if tamper:
        tamper(writer)
    plugin = tmp_path / 'TestRule.esm'
    writer.write(str(plugin))
    export = tmp_path / 'export'
    export.mkdir()
    (export / 'GMST.txt').write_text(''.join(
        f'---RECORD_BEGIN---\nSignature=GMST\nEditorID={k}\nRecordFlags=0\nDATA.Value={v}\n'
        f'---RECORD_END---\n\n' for k, v in settings.items() if EXE[k] != v), encoding='utf-8')
    return str(plugin), str(export)


def _replace(writer, sig: str, change) -> None:
    """Rewrite every record of `sig` in the writer through `change(bytes) -> bytes`."""
    writer._top_groups[sig] = [change(r) for r in writer._top_groups[sig]]


@SETTINGS
def test_the_audit_passes_a_built_plugin(tmp_path, capsys, settings):
    """Entry values, the sweep and the actor lists, read back out of the written file."""
    assert audit.run(*_built(tmp_path, settings)) == 0
    out = capsys.readouterr().out
    assert 'in the stat faction 2, listing the perk 2' in out and 'worst error' in out


def test_the_audit_reports_a_changed_entry_value(tmp_path, capsys):
    """One EPFD moved by 3 %: the entry is named and the result is not zero."""
    def tamper(writer):
        """Scale the first entry's value."""
        def change(rec):
            """The record with its first EPFD scaled."""
            at = rec.index(b'EPFD') + 6
            value = struct.unpack_from('<f', rec, at)[0] * 1.03
            return rec[:at] + struct.pack('<f', value) + rec[at + 4:]
        _replace(writer, 'PERK', change)
    assert audit.run(*_built(tmp_path, AUTHORED, tamper)) > 0
    assert 'MISMATCH: Dealt skill converted weapons (6,) band 0-2' in capsys.readouterr().out


def test_the_audit_reports_settings_the_perk_was_not_built_from(tmp_path):
    """A perk from the exe defaults against an export that authors other values."""
    plugin, _export = _built(tmp_path, EXE)
    other = tmp_path / 'other'
    other.mkdir()
    (other / 'GMST.txt').write_text(
        '---RECORD_BEGIN---\nEditorID=fDamageSkillMult\nDATA.Value=1.5\n---RECORD_END---\n',
        encoding='utf-8')
    assert audit.run(plugin, str(other)) > 0


def test_the_audit_reports_an_actor_without_the_perk_and_one_with_it_wrongly(tmp_path, capsys):
    """A stat-faction member that lost its PRKR, and a non-member that lists the perk."""
    def tamper(writer):
        """Strip the perk list from the first actor and the stat factions from the second."""
        first, second = writer._top_groups['NPC_']
        at = first.index(b'PRKZ')
        body = first[24:at] + first[at + 10 + 14:]
        first = first[:4] + struct.pack('<I', len(body)) + first[8:24] + body
        strength = struct.pack('<I', stat_factions.stat_faction(0))
        second = second.replace(b'SNAM\x08\x00' + strength, b'SNAM\x08\x00' + b'\x99\x00\x00\x01')
        writer._top_groups['NPC_'] = [first, second]
    assert audit.run(*_built(tmp_path, EXE, tamper)) == 2
    out = capsys.readouterr().out
    assert 'TestFencer 00000500: in the stat faction True, lists the perk False' in out
    assert 'TestBoar 00000501: in the stat faction False, lists the perk True' in out


def test_the_audit_reports_an_entry_without_its_gate_or_its_outsider_test(tmp_path, capsys):
    """Each structural guarantee is checked on the bytes, entry by entry."""
    def tamper(writer):
        """Point the first gate and the last outsider test at other forms."""
        gate = struct.pack('<I', WELL_KNOWN_PROPERTIES[WEAPON_RULE_GLOBAL])
        faction = struct.pack('<H2xI', audit.GET_IN_FACTION, stat_factions.stat_faction(0))

        def change(rec):
            """The record with those two FormIDs replaced."""
            at, last = rec.index(gate), rec.rindex(faction)
            rec = rec[:at] + b'\x77\x00\x00\x01' + rec[at + 4:]
            return rec[:last + 4] + b'\x78\x00\x00\x01' + rec[last + 8:]
        _replace(writer, 'PERK', change)
    assert audit.run(*_built(tmp_path, EXE, tamper)) > 0
    out = capsys.readouterr().out
    assert 'no `gate global == 1` on the owner tab' in out
    assert 'a Taken entry without `attacker not converted`' in out


def test_the_audit_refuses_a_plugin_whose_perk_it_cannot_find(tmp_path, capsys):
    """No perk in the plugin or a --master: undetermined, never a pass."""
    plugin, export = _built(tmp_path, EXE, lambda w: w._top_groups.pop('PERK'))
    assert audit.run(plugin, export) == -1
    assert 'UNDETERMINED' in capsys.readouterr().out


def test_the_audit_reads_an_adopted_perk_out_of_the_master(tmp_path):
    """A dependent lists its master's perk: found through --master, in the dependent's id space."""
    master, export = _built(tmp_path, AUTHORED)
    master_ids = dict(WELL_KNOWN_PROPERTIES)
    table = {(b'GLOB', edid): fid for edid, fid in master_ids.items()}
    del table[(b'GLOB', wr.WEAPON_RULE_PERK)]
    table.update({(b'FACT', edid): stat_factions.stat_faction(av)
                  for av, edid in stat_factions.STAT_FACTIONS.items()})
    table[(b'PERK', wr.WEAPON_RULE_PERK)] = master_ids[wr.WEAPON_RULE_PERK]
    writer = PluginWriter(masters=['Skyrim.esm', 'TestRule.esm'], is_esm=False)
    assert _root_run(writer, master_index=_Index(table))[wr.WEAPON_RULE_PERK] == \
        master_ids[wr.WEAPON_RULE_PERK]
    writer.add_record('NPC_', convert_NPC_(dict(NPC, FormID='02000600', EditorID='TestSquire')))
    child = tmp_path / 'TestChild.esp'
    writer.write(str(child))
    empty = tmp_path / 'child_export'
    empty.mkdir()
    assert audit.run(str(child), str(empty)) == -1
    assert audit.run(str(child), str(empty), [master], [export]) == 0


# ---------------------------------------------------------------------------
# Golden bytes: what the tree wrote before the rule became an add-on
# ---------------------------------------------------------------------------

#: (record length, sha256) of the PERK a root plugin got, per settings fixture, before the move.
GOLDEN_PERK = {
    'authored': (30214, 'bc6d44a4c15296a2cfc447b8e72b79c4b3f29f7bd5dd6206296caeb2b741ca4d'),
    'exe': (36941, 'e9db4e05f25db8b2db2f44b30705e405b6ba589a691d032f7c2735c9fd16a697'),
}

#: The FormIDs `derive_formid` gives the perk and the switch in a plugin whose one master is Skyrim.esm.
GOLDEN_PERK_FORMID, GOLDEN_GLOBAL_FORMID = 0x01C84F88, 0x01C7936C

#: The switch global's whole record, and the perk list on every converted actor, before the move.
GOLDEN_GLOBAL = ('474c4f422e000000000000006c93c701000000002c000000454449441700544553344f626c6976696f6e'
                 '576561706f6e52756c6500464e414d010073464c545604000000803f')
GOLDEN_PERK_LIST = '50524b5a04000100000050524b520800884fc80101000000'


@pytest.mark.parametrize('name, settings', [('authored', AUTHORED), ('exe', EXE)])
def test_the_add_on_writes_the_records_the_tree_wrote_before_the_move(name, settings):
    """Same EditorIDs, FormIDs and bytes: the perk, the switch global and the actors' perk list."""
    writer = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
    made = _root_run(writer, {'GMST': _gmst(settings)})
    assert made == {'TES4OblivionWeaponRulePerk': GOLDEN_PERK_FORMID,
                    'TES4OblivionWeaponRule': GOLDEN_GLOBAL_FORMID}
    (perk,) = writer._top_groups['PERK']
    assert (len(perk), hashlib.sha256(perk).hexdigest()) == GOLDEN_PERK[name]
    globs = [g for g in writer._top_groups['GLOB'] if b'TES4OblivionWeaponRule' in g]
    assert [g.hex() for g in globs] == [GOLDEN_GLOBAL]
    for kind, rec in (('NPC_', NPC), ('CREA', CREA)):
        assert loader.actor_subrecords(kind, rec).hex() == GOLDEN_PERK_LIST


# ---------------------------------------------------------------------------
# The pipeline's hook call
# ---------------------------------------------------------------------------

#: The pipeline's support-record step for an empty root TES4 plugin, then one NPC_ and one CREA.
PRESCAN = '''
import hashlib, json, sys
from tes5_import import pipeline
from tes5_import.base.owned_records import WELL_KNOWN_PROPERTIES
from tes5_import.base.writer import PluginWriter
from tes5_import.record_types.creature import convert_CREA
from tes5_import.record_types.npc import convert_NPC_
from tests.extension_records import CREA, NPC

writer = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
pipeline._prescan_special_records({}, None, writer, sys.argv[1], lambda _name: None)
mine = lambda record: b'TES4OblivionWeaponRule' in record
print(json.dumps({
    'forms': {k: v for k, v in WELL_KNOWN_PROPERTIES.items() if k.startswith('TES4OblivionWeaponRule')},
    'mine': sorted([sig, len(r), hashlib.sha256(r).hexdigest()]
                   for sig, recs in writer._top_groups.items() for r in recs if mine(r)),
    'others': {sig: [hashlib.sha256(r).hexdigest() for r in recs if not mine(r)]
               for sig, recs in writer._top_groups.items()},
    'actors': [convert_NPC_(NPC).hex(), convert_CREA(CREA).hex()],
}))
'''


def _prescan(tmp_path, add_ons: str) -> dict:
    """What that step writes in a fresh process (it leaves run state behind) with `add_ons` named."""
    env = dict(os.environ, **{loader.EXTENSIONS_ENV_VAR: add_ons})
    done = subprocess.run([sys.executable, '-B', '-c', PRESCAN, str(tmp_path)], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_the_pipeline_runs_an_enabled_add_ons_create_records_and_not_a_disabled_ones(tmp_path):
    """Through `_prescan_special_records`: the perk and the switch exist only while the settings name the add-on."""
    on, off = _prescan(tmp_path, ADD_ON), _prescan(tmp_path, '')
    assert on['forms'] == {wr.WEAPON_RULE_PERK: GOLDEN_PERK_FORMID,
                           WEAPON_RULE_GLOBAL: GOLDEN_GLOBAL_FORMID}
    assert on['mine'] == [['GLOB', len(GOLDEN_GLOBAL) // 2,
                           hashlib.sha256(bytes.fromhex(GOLDEN_GLOBAL)).hexdigest()],
                          ['PERK', *GOLDEN_PERK['exe']]]
    assert off['forms'] == {} and off['mine'] == []
    assert {sig: recs for sig, recs in on['others'].items() if recs} == off['others']
    for with_rule, without in zip(on['actors'], off['actors']):
        assert with_rule.count(GOLDEN_PERK_LIST) == 1 and GOLDEN_PERK_LIST not in without
        assert with_rule[48:].replace(GOLDEN_PERK_LIST, '') == without[48:]


# ---------------------------------------------------------------------------
# Which skill table each family is built from
# ---------------------------------------------------------------------------

#: Skyrim settings under which the player's skill factor is not an NPC's.
SPLIT = dict(wr.SKYRIM_SKILL_SETTINGS, fDamagePCSkillMax=2.0)


def _skill_rows(settings: dict, skyrim: dict) -> dict:
    """{(family, audience): sorted [(lo, hi, value)]} of the built skill entries."""
    rows = {}
    for entry in _entries(settings, skyrim):
        family, kind, audience, _weapons, _key, band = audit.classify(entry, AUDIT_FORMS)
        if kind == 'skill':
            rows.setdefault((family, audience), []).append((band[0], band[1], entry['value']))
    return {key: sorted(value, key=lambda row: (row[0], row[2])) for key, value in rows.items()}


@SETTINGS
def test_dealt_is_built_from_the_npc_table_and_each_attacker_from_its_own(settings):
    """A converted attacker and a Skyrim NPC read 'skill'; only the player reads 'skill_pc'."""
    tables = wr.rule_tables(settings, SPLIT)
    assert tables['skill'] != tables['skill_pc']

    def want(name):
        """The table's rows once per weapon skill, as `_skill_rows` orders them."""
        rows = [(lo or 0, hi, value) for lo, hi, value in tables[name]] * len(wr.WEAPON_SKILLS)
        return sorted(rows, key=lambda row: (row[0], row[2]))
    got = _skill_rows(settings, SPLIT)
    assert set(got) == {(35, 'converted'), (36, 'player'), (36, 'skyrim')}
    for key, name in (((35, 'converted'), 'skill'), ((36, 'skyrim'), 'skill'),
                      ((36, 'player'), 'skill_pc')):
        assert [row[:2] for row in got[key]] == [row[:2] for row in want(name)], key
        assert [row[2] for row in got[key]] == pytest.approx([row[2] for row in want(name)], rel=1e-6), key
    other = [row[2] for row in want('skill_pc')]
    assert [row[2] for row in got[(35, 'converted')]] != pytest.approx(other, rel=1e-6)


# ---------------------------------------------------------------------------
# The audit's own checks, each fed what it exists to catch
# ---------------------------------------------------------------------------

def _dropping(entries: list, unwanted) -> list:
    """`entries` without the conditions `unwanted(ctda)` holds for."""
    return [dict(entry, tabs={tab: [c for c in ctdas if not unwanted(c)]
                              for tab, ctdas in entry['tabs'].items()}) for entry in entries]


def test_the_silence_check_reports_an_entry_that_ignores_the_switch():
    """With the gate condition gone every entry applies while the global is 0."""
    entries = _dropping(_entries(EXE), lambda c: c[3] == audit.GET_GLOBAL and c[4] == FORMS['gate'])
    problems = audit.check_silence(entries, AUDIT_FORMS)
    assert len(problems) == 9 and all('an entry applies with the switch off' in p for p in problems)


def test_the_silence_check_reports_a_converted_pair_scaled_twice():
    """With the outsider test gone a Taken entry also matches a converted attacker."""
    entries = _dropping(_entries(EXE), lambda c: c[3] == audit.GET_IN_FACTION)
    problems = audit.check_silence(entries, AUDIT_FORMS)
    assert len(problems) == 9 and all('converted on converted is scaled twice' in p for p in problems)


def test_the_silence_check_reports_a_skyrim_attacker_no_taken_entry_covers():
    """Without the Taken family nothing scales a Skyrim actor's hit on a converted one."""
    entries = [e for e in _entries(EXE) if e['point'] == audit.DEALT]
    problems = audit.check_silence(entries, AUDIT_FORMS)
    assert len(problems) == 9 and all('no Taken entry for a Skyrim attacker' in p for p in problems)


def test_the_sweep_alone_decides_the_limit(monkeypatch, tmp_path, capsys):
    """A plugin that passes fails once the limit is under its worst error, by that line only."""
    plugin, export = _built(tmp_path, EXE)
    assert audit.run(plugin, export) == 0
    monkeypatch.setattr(audit, 'BUDGET', 0.01)
    monkeypatch.setattr(audit, 'check_values', lambda entries, forms, settings: ([], {}))
    assert audit.run(plugin, export) == 1
    assert 'MISMATCH: worst sweep error' in capsys.readouterr().out


def _move_perk_list(record: bytes, before: bytes) -> bytes:
    """An actor record with its PRKZ + PRKR moved to just before the first `before` subrecord."""
    at = record.index(b'PRKZ\x04\x00')
    block, body = record[at:at + 24], record[:at] + record[at + 24:]
    to = body.index(before, 24)
    return body[:to] + block + body[to:]


@pytest.mark.parametrize('before', [b'RNAM\x04\x00', b'AIDT'], ids=['ahead of the spells', 'after the items'])
def test_the_audit_reports_a_perk_list_out_of_its_place(tmp_path, capsys, before):
    """The list is complete and the rank right, but it does not sit between SPLO and COCT."""
    def tamper(writer):
        """Move the first actor's perk list."""
        first, second = writer._top_groups['NPC_']
        writer._top_groups['NPC_'] = [_move_perk_list(first, before), second]
    assert audit.run(*_built(tmp_path, EXE, tamper)) == 1
    assert 'TestFencer 00000500: PRKZ/PRKR count or position' in capsys.readouterr().out


def test_the_perk_list_place_rule():
    """PRKZ, then its PRKRs in one run, after RNAM / SPCT / SPLO and before COCT / CNTO / AIDT."""
    place = audit._perks_in_place
    assert place(['EDID', 'RNAM', 'SPCT', 'SPLO', 'PRKZ', 'PRKR', 'COCT', 'CNTO', 'AIDT'])
    assert place(['RNAM', 'PRKZ', 'PRKR', 'PRKR', 'AIDT'])
    assert not place(['RNAM', 'PRKZ', 'PRKR', 'SPCT', 'SPLO', 'COCT'])
    assert not place(['RNAM', 'SPCT', 'SPLO', 'COCT', 'CNTO', 'PRKZ', 'PRKR', 'AIDT'])
    assert not place(['RNAM', 'SPLO', 'PRKZ', 'COCT', 'PRKR'])
    assert not place(['RNAM', 'SPLO', 'PRKR', 'PRKZ', 'COCT'])
    assert not place(['RNAM', 'SPLO', 'PRKR', 'COCT'])


def test_the_audit_reports_a_perk_count_that_is_not_the_number_listed(tmp_path, capsys):
    """PRKZ says two, one PRKR follows."""
    def tamper(writer):
        """Raise the first actor's PRKZ."""
        first, second = writer._top_groups['NPC_']
        at = first.index(b'PRKZ\x04\x00') + 6
        writer._top_groups['NPC_'] = [first[:at] + struct.pack('<I', 2) + first[at + 4:], second]
    assert audit.run(*_built(tmp_path, EXE, tamper)) == 1
    assert 'TestFencer 00000500: PRKZ/PRKR count or position' in capsys.readouterr().out


@pytest.mark.parametrize('rank', [0, 2])
def test_the_audit_accepts_the_perk_at_rank_one_only(tmp_path, capsys, rank):
    """Vanilla lists a one-rank perk at rank 1; any other rank is not listing it."""
    def tamper(writer):
        """Rewrite the rank byte of the first actor's PRKR."""
        first, second = writer._top_groups['NPC_']
        at = first.index(b'PRKR\x08\x00') + 10
        writer._top_groups['NPC_'] = [first[:at] + bytes((rank,)) + first[at + 1:], second]
    assert audit.run(*_built(tmp_path, EXE, tamper)) == 1
    assert 'TestFencer 00000500: in the stat faction True, lists the perk False' in capsys.readouterr().out
