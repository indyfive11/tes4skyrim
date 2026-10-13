"""A level-scaled creature's health, magicka and stamina are the stored value times its level.

See: docs/commentary/tes5_import_actors.md#level-pools
See: docs/commentary/tes5_import_actors.md#health-offset
"""

import struct

import pytest

from tes5_import.actors import creature_races
from tes5_import.actors import level_pools as lp
from tes5_import.actors.creature_races import CREATURE_RACE_BASE_HEALTH
from tes5_import.base.equivalents import (CREA_RACE_PATTERNS, VANILLA_RACE_START,
                                          resolve_creature_race)
from tes5_import.base.writer import PluginWriter, pack_record, pack_string_subrecord, pack_subrecord
from tes5_import.overrides.adoption import MasterAdoption, generated_formid
from tes5_import.overrides.builder import apply_changes
from tes5_import.overrides.diff import diff_records
from tes5_import.record_types.actor_common import T5_AUTOCALC, T5_PC_LEVEL_MULT
from tes5_import.record_types import world_falloutnv, world_morrowind
from tes5_import.record_types.creature import (convert_CREA, crea_acbs, crea_dnam,
                                               crea_spell_subs)
from tools.audit import actor_health_audit as audit

#: TES4 CREA ACBS flag PC Level Offset.
PC_LEVEL_OFFSET = 0x80

#: Skyrim.esm AbFortifyHealth and PlayerRef.
FORTIFY_HEALTH, PLAYER_REF = 0x000A1A3F, 0x00000014
#: Skyrim.esm AbFortifyMagickaHighElf and AbFortifyStamina.
FORTIFY_MAGICKA, FORTIFY_STAMINA = 0x000CDA4B, 0x00091F77
#: Skyrim.esm EitherHand, the equip type vanilla abilities carry.
EITHER_HAND = 0x00013F44

#: The second effect's CTDA of vanilla CWMissionBoostAllyHealth: GetLevel >= 20 on PlayerRef.
VANILLA_CTDA = bytes.fromhex('60000000' '0000a041' '5000' '0000' '00000000' '00000000'
                             '02000000' '14000000' 'ffffffff')

#: ACBS and DNAM the tree wrote for FIXED and CORPSE before level pools existed.
FIXED_ACBS = '100000000000000007000000000064000000000028000000'
FIXED_DNAM = ('1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e0a' + '00' * 18
              + '780000006400' + '00' * 10)
CORPSE_ACBS = '900000000000' '0000e8030000000064000000000000800000'
CORPSE_DNAM = ('1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e0a' + '00' * 18
               + '000000006400' + '00' * 10)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

#: FormID and Starting Stamina of the generated race the test creatures share.
RACE_FID, RACE_STAMINA = 0x01000900, 70.0


def _give_race(recs: list, fid: int = RACE_FID, stamina: float = RACE_STAMINA) -> None:
    """Register `recs` as sharing one generated race, as the race builder does."""
    for rec in recs:
        low = int(rec['FormID'], 16) & 0x00FFFFFF
        creature_races._CREA_RACE_MAP[low] = (fid, 'testmarsh')
        lp.set_race_start(low, (CREATURE_RACE_BASE_HEALTH, 0.0, stamina))


def _crea(fid: str, edid: str, health: int, level: int, flags: int = PC_LEVEL_OFFSET,
          cmin: int = 0, cmax: int = 0, magicka: int = 0, fatigue: int = 0,
          stand_in: bool = False) -> dict:
    """An exported CREA with invented names and only the fields the pool path reads.

    It is registered on the generated test race unless `stand_in`: then it has
    no generated race and the converter gives it the Skyrim race its name resolves to.
    """
    rec = {'Signature': 'CREA', 'FormID': fid, 'RecordFlags': '0', 'EditorID': edid,
           'SpellCount': '0', 'FactionCount': '0', 'ItemCount': '0', 'AIPackageCount': '0',
           'ACBS.Flags': str(flags), 'ACBS.Level': str(level), 'ACBS.CalcMin': str(cmin),
           'ACBS.CalcMax': str(cmax), 'DATA.Health': str(health),
           'ACBS.SpellPoints': str(magicka), 'ACBS.Fatigue': str(fatigue)}
    if not stand_in:
        _give_race([rec])
    return rec


#: A marsh strider that is two levels under the player, never below level 1.
STRIDER = _crea('00010001', 'TestMarshStrider', 15, -2, cmin=1)
#: A fixed level-7 creature and a level-scaled corpse prop: neither may change.
FIXED = _crea('00010002', 'TestFixedBoar', 120, 7, flags=0, fatigue=100)
CORPSE = _crea('00010003', 'TestDeadStrider', 0, -2, fatigue=100)
#: A level-scaled caster, one level over the player: all three pools grow.
SHAMAN = _crea('00010020', 'TestMarshShaman', 30, 1, magicka=20, fatigue=45)

#: Skyrim.esm WispRace and its RACE DATA starting health, magicka and stamina.
WISP_RACE, WISP_START = 0x00013208, (50.0, 50.0, 50.0)


def _stand_in_wisp() -> dict:
    """A level-scaled caster with no generated race, named so that WispRace stands in."""
    return _crea('00010030', 'TestMarshWisp', 22, -2, cmax=9, magicka=10, fatigue=20,
                 stand_in=True)


class _Writer:
    """Test double for PluginWriter: derived ids are stable per (site, key) and distinct."""

    def __init__(self):
        """Start empty, allocating from 0x01000001."""
        self.fid = 0x01000000
        self.records = []
        self._derived = {}

    def derive_formid(self, site, key):
        """A fresh id the first time (site, key) is seen, the same one after."""
        k = (site, repr(key))
        if k not in self._derived:
            self.fid += 1
            self._derived[k] = self.fid
        return self._derived[k]

    def add_record(self, rec_type, data):
        """Keep each written record."""
        self.records.append((rec_type, data))


def _build(writer, own: list, masters: list = ()) -> int:
    """Write the level pools of `own` over the races' base; how many."""
    return sum(lp.build_level_pools(writer, own, masters, CREATURE_RACE_BASE_HEALTH).values())


@pytest.fixture(autouse=True)
def _fresh_pools():
    """Each test starts with no level pool and only the module's creatures on the test race."""
    lp.reset()
    creature_races._CREA_RACE_MAP.clear()
    _give_race([STRIDER, FIXED, CORPSE, SHAMAN])
    yield
    lp.reset()
    creature_races._CREA_RACE_MAP.clear()


def _subs(record: bytes) -> list:
    """[(signature, payload)] of a packed record, after its 24-byte header."""
    out, pos = [], 24
    while pos + 6 <= len(record):
        size = struct.unpack_from('<H', record, pos + 4)[0]
        out.append((record[pos:pos + 4].decode('ascii'), record[pos + 6:pos + 6 + size]))
        pos += 6 + size
    return out


def _effects(spel: bytes) -> list:
    """[(effect FormID, magnitude, area, duration, [CTDA payloads])] of a packed SPEL."""
    effects = []
    for sig, payload in _subs(spel):
        if sig == 'EFID':
            effects.append([struct.unpack('<I', payload)[0], None, None, None, []])
        elif sig == 'EFIT':
            effects[-1][1:4] = struct.unpack('<fII', payload)
        elif sig == 'CTDA':
            effects[-1][4].append(payload)
    return effects


def _ctda(payload: bytes) -> tuple:
    """A CTDA's (operator, value, function, param1, param2, run on, reference, param3)."""
    return struct.unpack('<B3xfHxxIIIIi', payload)


def _acbs(rec: dict) -> tuple:
    """(flags, level, health offset) out of the creature's packed ACBS."""
    acbs = crea_acbs(rec)
    return (struct.unpack_from('<I', acbs, 0)[0], struct.unpack_from('<H', acbs, 8)[0],
            struct.unpack_from('<h', acbs, 20)[0])


def _built_pool(rec: dict, spel: bytes, player_level: int) -> float:
    """The pool the written records give: manual stats plus every active effect."""
    pool = CREATURE_RACE_BASE_HEALTH + _acbs(rec)[2]
    for _fid, magnitude, _area, _dur, ctdas in _effects(spel) if spel else []:
        if all(player_level >= _ctda(c)[1] for c in ctdas):
            pool += magnitude
    return pool


# ---------------------------------------------------------------------------
# The numbers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('offset, cmin, cmax, span', [
    pytest.param(3, 0, 0, (4, 84), id='positive offset'),
    pytest.param(0, 0, 0, (1, 81), id='zero offset'),
    pytest.param(-5, 0, 0, (1, 76), id='negative offset is never below level 1'),
    pytest.param(-2, 8, 0, (8, 79), id='CalcMin lifts the level-1 level'),
    pytest.param(13, 1, 10, (10, 10), id='CalcMax caps the level-1 level'),
    pytest.param(0, 1, 3, (1, 3), id='CalcMax is the top'),
    pytest.param(5, 1, 60000, (6, 86), id='a CalcMax above the ceiling does not lengthen the pool'),
    pytest.param(15, 10, 100, (16, 96), id='CalcMax just above the ceiling'),
    pytest.param(-10, 0, 4, (1, 4), id='negative offset under a CalcMax'),
    pytest.param(0, 90, 0, (90, 90), id='CalcMin past the ceiling'),
    pytest.param(2, 9, 5, (5, 5), id='CalcMax under CalcMin wins'),
])
def test_level_span(offset, cmin, cmax, span):
    """L0 is the creature's level beside a level-1 player, top the highest it reaches."""
    assert lp.level_span(offset, cmin, cmax) == span


def test_ceiling_is_the_all_skills_player_level():
    """With no CalcMax the pool stops at the all-skills-100 player level."""
    assert lp.PLAYER_LEVEL_CEILING == 81
    assert lp.creature_level(81, 2, 0, 0) == lp.creature_level(500, 2, 0, 0) == 83


def test_fixed_corpse_and_other_games_have_no_pool(monkeypatch):
    """Only a level-scaled TES4 creature with a positive pool takes the rule."""
    assert lp.pool_numbers(STRIDER) == (15, -2, 1, 0)
    assert lp.pool_numbers(FIXED) is None
    assert lp.pool_numbers(CORPSE) is None
    monkeypatch.setattr(world_falloutnv, '_IS_FALLOUT_SOURCE', [True])
    assert lp.pool_numbers(STRIDER) is None
    monkeypatch.setattr(world_falloutnv, '_IS_FALLOUT_SOURCE', [])
    monkeypatch.setattr(world_morrowind, '_IS_TES3_SOURCE', [True])
    assert lp.pool_numbers(STRIDER) is None


def test_base_pool_overflowing_int16_leaves_a_remainder():
    """What the offset cannot hold goes to the ability, never lost."""
    assert lp.base_split(15, 1, 50.0) == (-35, 0.0)
    assert lp.base_split(300, 100, 50.0) == (29950, 0.0)
    assert lp.base_split(300, 200, 50.0) == (32767, 60000 - 50 - 32767)


# ---------------------------------------------------------------------------
# The ability
# ---------------------------------------------------------------------------

def test_rungs_are_one_conditioned_effect_per_level():
    """Each rung is AbFortifyHealth of the stored health under GetLevel >= N on PlayerRef.

    L0 is 1 and the top 81 - 2, so levels 2..79 are reached at player levels 4..81.
    """
    writer = _Writer()
    assert _build(writer, [STRIDER]) == 1
    (sig, spel), = writer.records
    effects = _effects(spel)
    assert sig == 'SPEL' and dict(_subs(spel))['EDID'] == b'TES4LevelPoolH15Om2N1X0\x00'
    assert len(effects) == 78
    for i, (fid, magnitude, area, duration, ctdas) in enumerate(effects):
        assert (fid, magnitude, area, duration) == (FORTIFY_HEALTH, 15.0, 0, 0)
        (ctda,) = ctdas
        assert len(ctda) == 32
        assert _ctda(ctda) == (0x60, float(4 + i), 80, 0, 0, 2, PLAYER_REF, -1)


def test_condition_bytes_match_the_vanilla_ability():
    """Only the float differs from CWMissionBoostAllyHealth's own condition."""
    writer = _Writer()
    _build(writer, [_crea('00010004', 'TestStag', 100, 0)])
    ctdas = [c for *_e, cs in _effects(writer.records[0][1]) for c in cs]
    assert ctdas[18] == VANILLA_CTDA
    assert all(c[:4] + c[8:] == VANILLA_CTDA[:4] + VANILLA_CTDA[8:] for c in ctdas)


@pytest.mark.parametrize('health, level, cmin, cmax, ceiling', [
    (15, -2, 1, 0, 79), (40, 0, 0, 0, 81), (25, 3, 0, 0, 84), (60, -5, 4, 12, None),
    (10, 13, 1, 10, None), (180, 0, 20, 0, 81), (33, 2, 1, 3, None), (300, 150, 0, 0, 231),
])
def test_pool_is_stored_health_times_level_at_every_player_level(health, level, cmin, cmax,
                                                                 ceiling):
    """Manual stats plus the active effects reproduce Oblivion's product.

    `ceiling` is the creature's level beside a level-81 player, where a pool
    with no CalcMax stops growing.
    """
    rec = _crea('00010005', 'TestScaled', health, level, cmin=cmin, cmax=cmax)
    writer = _Writer()
    _build(writer, [rec])
    spel = writer.records[0][1] if writer.records else b''
    for player in (1, 2, 5, 10, 20, 30, 50, 81, 120):
        oblivion = max(1, level + player)
        oblivion = max(oblivion, cmin) if cmin else oblivion
        oblivion = min(oblivion, cmax) if cmax else oblivion
        if ceiling:
            oblivion = min(oblivion, ceiling)
        assert _built_pool(rec, spel, player) == health * oblivion


def test_overflowing_base_pool_rides_an_unconditional_first_effect():
    """An int16 offset clamps; the rest is one effect with no condition."""
    rec = _crea('00010006', 'TestColossus', 300, 150)
    writer = _Writer()
    _build(writer, [rec])
    first, second = _effects(writer.records[0][1])[:2]
    assert _acbs(rec)[2] == 32767
    assert first[1] == 300 * 151 - 50 - 32767 and first[4] == []
    assert second[1] == 300.0 and _ctda(second[4][0])[1] == 2.0
    assert struct.unpack_from('<H', crea_dnam(rec), 36)[0] == 300 * 151


def test_a_pool_that_never_grows_writes_no_ability():
    """CalcMin = CalcMax leaves one level: the offset alone is the pool."""
    rec = _crea('00010007', 'TestPinned', 50, -2, cmin=1, cmax=1)
    writer = _Writer()
    assert _build(writer, [rec]) == 0
    assert writer.records == [] and lp.level_pool_ability(rec) == 0
    assert crea_spell_subs(rec) == b''
    assert _acbs(rec)[2] == 0


# ---------------------------------------------------------------------------
# The creature record
# ---------------------------------------------------------------------------

def test_level_scaled_creature_has_manual_stats_and_its_level_one_pool():
    """PC Level Mult stays, Auto-calc goes, and the offset is stored * L0 - the race base."""
    rec = _crea('00010008', 'TestBriarWolf', 40, 3)
    flags, level, offset = _acbs(rec)
    assert flags & T5_PC_LEVEL_MULT and not flags & T5_AUTOCALC
    assert level == 1000
    assert offset == 40 * 4 - CREATURE_RACE_BASE_HEALTH
    assert struct.unpack_from('<H', crea_dnam(rec), 36)[0] == 160


def test_ability_is_the_last_entry_of_the_spell_list():
    """It follows the authored spells in SPLO."""
    rec = dict(STRIDER, SpellCount='1')
    rec['Spell[0]'] = '00020001'
    writer = _Writer()
    _build(writer, [rec])
    splo = [struct.unpack('<I', p)[0] for s, p in _subs(convert_CREA(rec)) if s == 'SPLO']
    assert splo[0] == 0x00020001 and splo[-1] == lp.level_pool_ability(rec) != 0


def test_fixed_level_and_zero_health_creatures_are_unchanged():
    """Byte for byte what the tree wrote before: auto-calc, own offset, no ability."""
    _build(_Writer(), [FIXED, CORPSE, STRIDER])
    assert crea_acbs(FIXED).hex() == FIXED_ACBS
    assert crea_dnam(FIXED).hex() == FIXED_DNAM
    assert crea_acbs(CORPSE).hex() == CORPSE_ACBS
    assert crea_dnam(CORPSE).hex() == CORPSE_DNAM
    assert crea_spell_subs(FIXED) == crea_spell_subs(CORPSE) == b''
    assert _acbs(CORPSE)[0] & T5_AUTOCALC and _acbs(CORPSE)[2] == -32768


def test_creatures_with_the_same_four_numbers_share_one_ability():
    """Stored health, offset and both bounds decide the ability, nothing else."""
    twin = dict(STRIDER, FormID='00010009', EditorID='TestFenStrider')
    other = dict(STRIDER, FormID='0001000A', EditorID='TestBogStrider')
    other['DATA.Health'] = '16'
    writer = _Writer()
    assert _build(writer, [STRIDER, twin, other]) == 2
    assert len(writer.records) == 2
    assert lp.level_pool_ability(STRIDER) == lp.level_pool_ability(twin)
    assert lp.level_pool_ability(other) not in (0, lp.level_pool_ability(STRIDER))


@pytest.mark.parametrize('numbers, edid', [
    ((15, -2, 1, 0), 'TES4LevelPoolH15Om2N1X0'),
    ((15, 2, 1, 0), 'TES4LevelPoolH15O2N1X0'),
    ((180, 0, 20, 99), 'TES4LevelPoolH180O0N20X99'),
])
def test_editor_id_is_the_four_numbers(numbers, edid):
    """A negative offset is spelled with a letter, so the name stays alphanumeric."""
    assert lp.pool_edid(numbers) == edid


# ---------------------------------------------------------------------------
# FormIDs
# ---------------------------------------------------------------------------

def _pool_ids(creatures: list, noise: int = 0) -> dict:
    """{four numbers: FormID} from a fresh PluginWriter, after `noise` unrelated ids."""
    lp.reset()
    writer = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
    for i in range(noise):
        writer.derive_formid('CREA_UNARMED', (('folder', ('body.nif',)), i))
    _build(writer, creatures)
    return {lp.pool_numbers(r): lp.level_pool_ability(r) for r in creatures}


def test_ability_formids_are_a_function_of_the_four_numbers():
    """Walk order, creature FormIDs and other generated records do not move one."""
    herd = [STRIDER, _crea('0001000B', 'TestElk', 40, 0), _crea('0001000C', 'TestLynx', 25, 3)]
    moved = [dict(r, FormID=f'0009{i:04X}', EditorID=f'TestRenamed{i}')
             for i, r in enumerate(reversed(herd))]
    first = _pool_ids(herd)
    assert len(set(first.values())) == 3 and all(first.values())
    assert _pool_ids(moved, noise=50) == first
    writer = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
    assert first[(15, -2, 1, 0)] == writer.derive_formid('CREA_LEVEL_POOL', (15, -2, 1, 0))


def test_level_pools_move_no_other_generated_formid():
    """An id derived at another site is the same with and without the pools."""
    def unarmed(with_pools: bool) -> list:
        """Three ids derived at another site, with or without the pools built first."""
        lp.reset()
        writer = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
        if with_pools:
            _build(writer, [STRIDER, _crea('0001000D', 'TestHare', 5, 1)])
        return [writer.derive_formid('CREA_UNARMED', (('folder', ('body.nif',)), d))
                for d in (10, 20, 58)]

    assert unarmed(True) == unarmed(False)


# ---------------------------------------------------------------------------
# A dependent plugin's override
# ---------------------------------------------------------------------------

def _override(master: dict, plugin: dict, master_record: bytes = None) -> list:
    """Subrecords of the master's converted record with the plugin's changes applied.

    The changes are what production hands over: `diff_records`, where a
    changed list is one `Name[]` key. `master_record` stands in for the
    master's built record when it is not this run's own conversion.
    """
    changes = diff_records(master, plugin)
    record, _applied, unmapped = apply_changes(master_record or convert_CREA(master), changes,
                                               plugin, master)
    assert not unmapped
    return _subs(record)


def _splo(subs: list) -> list:
    """The spell FormIDs of a record's SPLO run, checked against its SPCT."""
    fids = [struct.unpack('<I', p)[0] for s, p in subs if s == 'SPLO']
    counts = [struct.unpack('<I', p)[0] for s, p in subs if s == 'SPCT']
    assert counts == ([len(fids)] if fids else [])
    return fids


@pytest.mark.parametrize('key, value', [
    ('ACBS.Level', '4'), ('ACBS.CalcMin', '6'), ('ACBS.CalcMax', '30'), ('DATA.Health', '22'),
])
def test_override_of_a_pool_number_gets_its_own_ability_and_acbs(key, value):
    """Each of the four numbers, changed alone, swaps the ability and re-solves the offset."""
    plugin = dict(STRIDER)
    plugin[key] = value
    _build(_Writer(), [STRIDER, plugin])
    stale, fresh = lp.level_pool_ability(STRIDER), lp.level_pool_ability(plugin)
    assert stale and fresh and stale != fresh
    subs = _override(STRIDER, plugin)
    assert _splo(_subs(convert_CREA(STRIDER))) == [stale]
    assert _splo(subs) == [fresh]
    assert dict(subs)['ACBS'] == crea_acbs(plugin)


def test_override_to_a_fixed_level_drops_the_ability():
    """Clearing PC Level Offset returns the creature to auto-calc with no pool."""
    plugin = dict(STRIDER)
    plugin['ACBS.Flags'] = '0'
    _build(_Writer(), [STRIDER, plugin])
    subs = _override(STRIDER, plugin)
    assert _splo(subs) == []
    assert struct.unpack_from('<I', dict(subs)['ACBS'], 0)[0] & T5_AUTOCALC


def test_override_keeps_authored_spells_beside_the_swapped_ability():
    """A changed spell list and a changed level in one override both land."""
    master = dict(STRIDER, SpellCount='1')
    master['Spell[0]'] = '00020001'
    plugin = dict(master)
    plugin['Spell[0]'] = '00020002'
    plugin['ACBS.Level'] = '1'
    _build(_Writer(), [master, plugin])
    assert _splo(_override(master, plugin)) == [0x00020002, lp.level_pool_ability(plugin)]


def test_override_of_an_unrelated_field_leaves_the_spell_list_alone():
    """Same four numbers: the master's bytes stay."""
    plugin = dict(STRIDER)
    plugin['BNAM.BaseScale'] = '1.5'
    _build(_Writer(), [STRIDER, plugin])
    master_subs = _subs(convert_CREA(STRIDER))
    subs = _override(STRIDER, plugin)
    assert _splo(subs) == _splo(master_subs)
    assert dict(subs)['ACBS'] == dict(master_subs)['ACBS']


def test_override_from_a_fixed_level_gains_a_spell_list():
    """A master with no SPLO run gets one, placed after RNAM as convert_CREA writes it.

    Its 100 fatigue now grows too, so the run is the health and the stamina ability.
    """
    plugin = dict(FIXED)
    plugin['ACBS.Flags'] = str(PC_LEVEL_OFFSET)
    _build(_Writer(), [FIXED, plugin])
    subs = _override(FIXED, plugin)
    sigs = [s for s, _p in subs]
    assert _splo(subs) == [lp.level_pool_ability(plugin), lp.level_pool_ability(plugin, lp.STAMINA)]
    assert 0 not in _splo(subs)
    assert sigs[sigs.index('RNAM') + 1:sigs.index('RNAM') + 4] == ['SPCT', 'SPLO', 'SPLO']
    assert dict(subs)['DNAM'] == crea_dnam(plugin)
    assert not struct.unpack_from('<I', dict(subs)['ACBS'], 0)[0] & T5_AUTOCALC


class _MasterIndex:
    """A master index over a {FormID: packed record} dict."""

    def __init__(self, records: dict):
        """Index `records` by FormID and by (signature, EditorID)."""
        self.records = records
        self.edids = {(r[:4], _subs(r)[0][1].rstrip(b'\0').decode()): fid
                      for fid, r in records.items()}

    def find_by_edid(self, sig: bytes, edid: str) -> int:
        """The FormID of the record with this signature and EditorID, else 0."""
        return self.edids.get((sig, edid), 0)

    def record(self, fid: int) -> bytes:
        """The packed record at `fid`, else b''."""
        return self.records.get(fid, b'')


def test_dependent_plugin_adopts_its_masters_abilities():
    """Same four numbers resolve to the master's SPEL; new ones are the child's own."""
    elk = _crea('0001000E', 'TestElk', 40, 0)
    master = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
    _build(master, [STRIDER, elk])
    in_master = {struct.unpack_from('<I', r, 12)[0]: r for r in master.top_records()}
    strider_fid, elk_fid = lp.level_pool_ability(STRIDER), lp.level_pool_ability(elk)

    lp.reset()
    child = PluginWriter(masters=['Skyrim.esm', 'TestMaster.esm'], is_esm=False)
    child.adoption = MasterAdoption(_MasterIndex(in_master))
    tougher = dict(STRIDER)
    tougher['DATA.Health'] = '30'
    newcomer = _crea('02000001', 'TestChildStrider', 15, -2, cmin=1)
    assert _build(child, [tougher, newcomer], [STRIDER, elk]) == 2
    assert lp.level_pool_ability(newcomer) == lp.level_pool_ability(STRIDER) == strider_fid
    assert lp.level_pool_ability(elk) == elk_fid
    assert lp.level_pool_ability(tougher) >> 24 == 2
    child.adoption.finalize(child)
    assert [struct.unpack_from('<I', r, 12)[0] for r in child.top_records()] == [
        lp.level_pool_ability(tougher)]


# ---------------------------------------------------------------------------
# The audit
# ---------------------------------------------------------------------------

def _export_text(recs: list) -> str:
    """`recs` as an export file's key=value blocks."""
    return ''.join('---RECORD_BEGIN---\n' + ''.join(f'{k}={v}\n' for k, v in r.items())
                   for r in recs)


def _race_record(fid: int = RACE_FID,
                 start: tuple = (CREATURE_RACE_BASE_HEALTH, 0.0, RACE_STAMINA),
                 edid: str = 'TestMarshRace') -> bytes:
    """A RACE with starting (health, magicka, stamina) `start`: by default a generated race's."""
    data = bytearray(164)
    struct.pack_into('<fff', data, 36, *start)
    return pack_record('RACE', fid, 0, pack_string_subrecord('EDID', edid)
                       + pack_subrecord('DATA', bytes(data)))


def _patched(record: bytes, sig: str, offset: int, fmt: str, value) -> bytes:
    """`record` with one field of its first `sig` subrecord overwritten."""
    out, pos = bytearray(record), 24
    while pos + 6 <= len(out):
        size = struct.unpack_from('<H', out, pos + 4)[0]
        if out[pos:pos + 4] == sig.encode():
            struct.pack_into(fmt, out, pos + 6 + offset, value)
            return bytes(out)
        pos += 6 + size
    raise AssertionError(f'no {sig}')


def _audit(tmp_path, recs: list, keep_spells: bool = True, edit=None) -> tuple:
    """Build `recs` into a plugin on disk and run the health audit over it.

    `edit(record) -> record` rewrites each written SPEL and NPC_ first.
    """
    edit = edit or (lambda record: record)
    built = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
    _build(built, recs)
    _give_race(recs)
    writer = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
    writer.add_record('RACE', _race_record())
    for spel in built.top_records() if keep_spells else []:
        writer.add_record('SPEL', edit(spel))
    for rec in recs:
        writer.add_record('NPC_', edit(convert_CREA(rec)))
    plugin = tmp_path / 'TestPools.esm'
    writer.write(str(plugin))
    export = tmp_path / 'CREA.txt'
    export.write_text(_export_text(recs), encoding='utf-8')
    return audit.audit(audit.parse_export(str(export)), *audit.load_built(str(plugin)))


def test_audit_recomputes_every_level_scaled_pool_from_the_built_plugin(tmp_path):
    """Exact at every checked player level, read back out of the written file."""
    recs = [STRIDER, FIXED, CORPSE, _crea('0001000F', 'TestColossus', 300, 150),
            _crea('00010010', 'TestPinned', 50, -2, cmin=1, cmax=1),
            _crea('00010011', 'TestLynx', 25, 3, cmin=2, cmax=30), SHAMAN,
            _crea('00010012', 'TestTitan', 90, 150, magicka=400, fatigue=350)]
    counts, rows, pool_rows = _audit(tmp_path, recs)
    assert (counts['pool_exact'], counts['pool_mismatch'], counts['pool_undetermined']) == (6, 0, 0)
    assert (counts['exact'], counts['mismatch'], counts['corpse']) == (1, 0, 1)
    assert rows == [] and pool_rows == []


def test_audit_reports_a_pool_that_lost_its_ability(tmp_path):
    """An ability the plugin lists and does not hold is reported as missing."""
    counts, _rows, pool_rows = _audit(tmp_path, [STRIDER], keep_spells=False)
    assert counts['pool_exact'] == 0 and counts['pool_mismatch'] == 1
    assert pool_rows == [('TestMarshStrider', 'missing', 5, 15 * 3, 15.0, False)]


def test_audit_counts_an_auto_calc_level_scaled_creature_as_a_mismatch(tmp_path):
    """Right numbers under the Auto-calc flag are wrong: the engine adds its own level term."""
    def auto(record: bytes) -> bytes:
        """The NPC_ with Auto calc stats set; a SPEL unchanged."""
        if record[:4] != b'NPC_':
            return record
        flags = struct.unpack_from('<I', dict(_subs(record))['ACBS'], 0)[0]
        return _patched(record, 'ACBS', 0, '<I', flags | T5_AUTOCALC)

    counts, _rows, pool_rows = _audit(tmp_path, [STRIDER], edit=auto)
    assert (counts['pool_exact'], counts['pool_mismatch']) == (0, 1)
    assert pool_rows == [('TestMarshStrider', 'auto-calc', 1, 0, 0.0, True)]


@pytest.mark.parametrize('offset, value', [(8, 0), (16, 1), (20, 2)],
                         ids=['spell type', 'cast type', 'delivery'])
def test_audit_counts_a_pool_that_is_not_a_constant_self_ability(tmp_path, offset, value):
    """A level-pool SPEL the engine would not apply passively is a mismatch."""
    def not_ability(record: bytes) -> bytes:
        """The SPEL with one SPIT field off; an NPC_ unchanged."""
        return _patched(record, 'SPIT', offset, '<I', value) if record[:4] == b'SPEL' else record

    counts, _rows, pool_rows = _audit(tmp_path, [SHAMAN], edit=not_ability)
    assert (counts['pool_exact'], counts['pool_mismatch']) == (0, 1)
    assert pool_rows[0][:2] == ('TestMarshShaman', 'not an ability')


def test_audit_checks_magicka_and_stamina_like_health(tmp_path):
    """An ACBS magicka or stamina offset that is off is reported under its pool's name."""
    def low_magicka(record: bytes) -> bytes:
        """The NPC_ with five magicka less."""
        return _patched(record, 'ACBS', 4, '<h', 35) if record[:4] == b'NPC_' else record

    def low_stamina(record: bytes) -> bytes:
        """The NPC_ with a stamina offset of 0, as a fixed-level creature has."""
        return _patched(record, 'ACBS', 6, '<h', 0) if record[:4] == b'NPC_' else record

    for i, (edit, row) in enumerate([
            (low_magicka, ('TestMarshShaman', 'magicka', 1, 40, 35.0, False)),
            (low_stamina, ('TestMarshShaman', 'stamina', 1, 90, 70.0, False))]):
        lp.reset()
        folder = tmp_path / str(i)
        folder.mkdir()
        counts, _rows, pool_rows = _audit(folder, [SHAMAN], edit=edit)
        assert (counts['pool_exact'], counts['pool_mismatch']) == (0, 1)
        assert pool_rows == [row]


def _stand_in_plugin(tmp_path, edit=None, race: bool = True, abilities: bool = True) -> tuple:
    """(plugin, stand-in for the Skyrim master, export source) for one creature on WispRace.

    The master file is named Skyrim.esm and holds the race, unless not `race`;
    `edit(record) -> record` rewrites the creature's NPC_ first. Without
    `abilities` the plugin lists its level pools and does not hold them.
    """
    rec = _stand_in_wisp()
    writer = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
    _build(writer if abilities else PluginWriter(masters=['Skyrim.esm'], is_esm=True), [rec])
    writer.add_record('NPC_', (edit or (lambda record: record))(convert_CREA(rec)))
    plugin = tmp_path / 'TestPools.esm'
    writer.write(str(plugin))
    skyrim = PluginWriter(masters=[], is_esm=True)
    if race:
        skyrim.add_record('RACE', _race_record(WISP_RACE, WISP_START, 'TestWispRace'))
    master = tmp_path / 'Skyrim.esm'
    skyrim.write(str(master))
    export = tmp_path / 'CREA.txt'
    export.write_text(_export_text([rec]), encoding='utf-8')
    return plugin, master, audit.parse_export(str(export))


def _old_magicka(record: bytes) -> bytes:
    """The NPC_ with the magicka offset a race starting at 0 would need: stored x L0."""
    return _patched(record, 'ACBS', 4, '<h', 10)


def test_audit_reads_a_stand_in_race_from_the_skyrim_master(tmp_path):
    """Undetermined without the master that holds the race, exact with it."""
    plugin, master, src = _stand_in_plugin(tmp_path)
    assert _tally(audit.audit(src, *audit.load_built(str(plugin)))[0]) == (0, 0, 1)
    assert _tally(audit.audit(src, *audit.load_built(str(plugin), [str(master)]))[0]) == (1, 0, 0)


def test_audit_assumes_nothing_of_a_race_it_did_not_read(tmp_path):
    """Offsets solved for a race starting at 50 / 0: undetermined unread, a mismatch once read."""
    plugin, master, src = _stand_in_plugin(tmp_path, edit=_old_magicka)
    counts, _rows, pool_rows = audit.audit(src, *audit.load_built(str(plugin)))
    assert _tally(counts) == (0, 0, 1) and pool_rows == []
    counts, _rows, pool_rows = audit.audit(src, *audit.load_built(str(plugin), [str(master)]))
    assert _tally(counts) == (0, 1, 0)
    assert pool_rows == [('TestMarshWisp', 'magicka', 1, 10, 60.0, False)]


def test_audit_row_of_an_unread_race_carries_no_number_from_an_assumed_start(tmp_path):
    """A missing ability beside an unread race: the row names the finding, with no pool figures."""
    plugin, _master, src = _stand_in_plugin(tmp_path, abilities=False)
    counts, _rows, pool_rows = audit.audit(src, *audit.load_built(str(plugin)))
    assert _tally(counts) == (0, 1, 0)
    assert pool_rows == [('TestMarshWisp', 'missing', 1, 0, 0.0, False)]


def test_audit_counts_a_stand_in_race_the_given_master_lacks_as_a_mismatch(tmp_path):
    """A read master that does not hold the race is a mismatch, never exact."""
    plugin, master, src = _stand_in_plugin(tmp_path, race=False)
    counts, _rows, pool_rows = audit.audit(src, *audit.load_built(str(plugin), [str(master)]))
    assert _tally(counts) == (0, 1, 0)
    assert pool_rows[0][:2] == ('TestMarshWisp', 'missing')


def _master_and_child(tmp_path, drop: bytes = b'') -> tuple:
    """(child plugin, master plugin, export) on disk: the child's creature uses the master's pools.

    The master holds the race and the three abilities, the child only an
    actor that lists them. `drop` names a record type left out of the master.
    """
    master = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
    _build(master, [SHAMAN])
    in_master = {struct.unpack_from('<I', r, 12)[0]: r for r in master.top_records()}
    if drop != b'RACE':
        master.add_record('RACE', _race_record())
    master.remove_records(lambda r: r[:4] == drop)
    master_path = tmp_path / 'TestMaster.esm'
    master.write(str(master_path))

    lp.reset()
    child = PluginWriter(masters=['Skyrim.esm', 'TestMaster.esm'], is_esm=False)
    child.adoption = MasterAdoption(_MasterIndex(in_master))
    newcomer = dict(SHAMAN, FormID='02000001', EditorID='TestChildShaman')
    _build(child, [newcomer], [SHAMAN])
    _give_race([newcomer])
    child.add_record('NPC_', convert_CREA(newcomer))
    child.adoption.finalize(child)
    child_path = tmp_path / 'TestChild.esp'
    child.write(str(child_path))
    export = tmp_path / 'CREA.txt'
    export.write_text(_export_text([newcomer]), encoding='utf-8')
    return child_path, master_path, export


def _tally(counts) -> tuple:
    """(exact, mismatch, undetermined) level-scaled creatures."""
    return counts['pool_exact'], counts['pool_mismatch'], counts['pool_undetermined']


def test_audit_reads_an_ability_and_a_race_that_live_in_a_master(tmp_path):
    """Undetermined without the master, exact once --masters hands it over."""
    child, master, export = _master_and_child(tmp_path)
    src = audit.parse_export(str(export))
    assert _tally(audit.audit(src, *audit.load_built(str(child)))[0]) == (0, 0, 1)
    assert _tally(audit.audit(src, *audit.load_built(str(child), [str(master)]))[0]) == (1, 0, 0)


@pytest.mark.parametrize('drop, what', [(b'SPEL', 'missing'), (b'RACE', 'missing')],
                         ids=['abilities', 'race'])
def test_audit_with_masters_counts_what_the_master_lacks_as_a_mismatch(tmp_path, drop, what):
    """A given master that does not hold the ability or the race is a mismatch, rc 1."""
    child, master, export = _master_and_child(tmp_path, drop)
    src = audit.parse_export(str(export))
    counts, _rows, pool_rows = audit.audit(src, *audit.load_built(str(child), [str(master)]))
    assert _tally(counts) == (0, 1, 0)
    assert pool_rows[0][:2] == ('TestChildShaman', what)


def test_audit_command_line_takes_masters_and_fails_on_a_mismatch(tmp_path, monkeypatch, capsys):
    """rc 0 with the master given and whole, rc 1 when it lacks the abilities."""
    for i, (drop, rc) in enumerate([(b'', 0), (b'SPEL', 1)]):
        lp.reset()
        folder = tmp_path / str(i)
        folder.mkdir()
        child, master, _export = _master_and_child(folder, drop)
        monkeypatch.setattr('sys.argv', ['actor_health_audit.py', str(child), str(folder),
                                         '--masters', str(master)])
        assert audit.main() == rc
    assert 'undetermined : 0' in capsys.readouterr().out


# ---------------------------------------------------------------------------
# The ability record
# ---------------------------------------------------------------------------

def test_ability_is_a_constant_self_ability_with_bounds_and_an_equip_type():
    """EDID OBND ETYP SPIT, then the effects: what vanilla's own abilities carry."""
    writer = _Writer()
    _build(writer, [STRIDER])
    subs = _subs(writer.records[0][1])
    assert [sig for sig, _p in subs[:5]] == ['EDID', 'OBND', 'ETYP', 'SPIT', 'EFID']
    fields = dict(subs[:4])
    assert fields['OBND'] == bytes(12)
    assert struct.unpack('<I', fields['ETYP'])[0] == EITHER_HAND
    assert fields['SPIT'] == struct.pack('<IIIfIIffI', 0, 0, 4, 0.0, 0, 0, 0.0, 0.0, 0)
    assert len(fields['SPIT']) == 36


# ---------------------------------------------------------------------------
# The race builder owns the pools' lifetime
# ---------------------------------------------------------------------------

def test_race_builder_builds_the_pools_and_forgets_the_previous_plugins(tmp_path):
    """Phase 0f writes the abilities of this plugin's creatures and drops the last plugin's."""
    writer = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
    creature_races.build_creature_races({'CREA': [SHAMAN]}, writer, str(tmp_path))
    assert sorted(dict(_subs(r))['EDID'] for r in writer.top_records()) == [
        b'TES4LevelPoolH30O1N0X0\x00', b'TES4LevelPoolM20O1N0X0\x00',
        b'TES4LevelPoolS45O1N0X0\x00']
    assert len(lp.level_pool_abilities(SHAMAN)) == 3

    _give_race([SHAMAN])
    creature_races.build_creature_races({}, PluginWriter(masters=['Skyrim.esm'], is_esm=True),
                                        str(tmp_path))
    assert lp.level_pool_abilities(SHAMAN) == []
    assert lp._RACE_START == {}


# ---------------------------------------------------------------------------
# Magicka and stamina
# ---------------------------------------------------------------------------

def _pool_value(rec: dict, records: list, effect: int, race_start: float, acbs_at: int,
                player_level: int) -> float:
    """One pool from the written records: race start + ACBS offset + its active effects."""
    pool = race_start + struct.unpack_from('<h', crea_acbs(rec), acbs_at)[0]
    for _sig, spel in records:
        for fid, magnitude, _area, _dur, ctdas in _effects(spel):
            if fid == effect and all(player_level >= _ctda(c)[1] for c in ctdas):
                pool += magnitude
    return pool


@pytest.mark.parametrize('level, cmin, cmax, levels', [
    pytest.param(1, 0, 0, {1: 2, 2: 3, 10: 11, 50: 51, 81: 82, 120: 82}, id='offset +1'),
    pytest.param(-3, 2, 0, {1: 2, 2: 2, 10: 7, 50: 47, 81: 78, 120: 78}, id='CalcMin 2'),
    pytest.param(0, 4, 9, {1: 4, 2: 4, 10: 9, 50: 9, 81: 9, 120: 9}, id='both bounds'),
    pytest.param(5, 3, 3, {1: 3, 2: 3, 10: 3, 50: 3, 81: 3, 120: 3}, id='pinned level'),
])
def test_magicka_and_stamina_are_the_stored_value_times_level(level, cmin, cmax, levels):
    """SpellPoints and Fatigue take the level rule, L0, top and thresholds health takes."""
    rec = _crea('00010021', 'TestHexer', 30, level, cmin=cmin, cmax=cmax, magicka=20, fatigue=45)
    _give_race([rec], stamina=70.0)
    writer = _Writer()
    _build(writer, [rec])
    for player, creature in levels.items():
        assert _pool_value(rec, writer.records, FORTIFY_HEALTH, 50.0, 20, player) == 30 * creature
        assert _pool_value(rec, writer.records, FORTIFY_MAGICKA, 0.0, 4, player) == 20 * creature
        assert _pool_value(rec, writer.records, FORTIFY_STAMINA, 70.0, 6, player) == 45 * creature


def test_each_pool_has_its_own_ability_effect_and_editor_id():
    """Three abilities after the spell list, health first; each carries only its own effect."""
    writer = _Writer()
    assert _build(writer, [SHAMAN]) == 3
    by_edid = {dict(_subs(spel))['EDID'].rstrip(b'\0').decode(): spel
               for _sig, spel in writer.records}
    assert sorted(by_edid) == ['TES4LevelPoolH30O1N0X0', 'TES4LevelPoolM20O1N0X0',
                               'TES4LevelPoolS45O1N0X0']
    for edid, effect, magnitude in (('TES4LevelPoolH30O1N0X0', FORTIFY_HEALTH, 30.0),
                                    ('TES4LevelPoolM20O1N0X0', FORTIFY_MAGICKA, 20.0),
                                    ('TES4LevelPoolS45O1N0X0', FORTIFY_STAMINA, 45.0)):
        effects = _effects(by_edid[edid])
        assert len(effects) == 80
        assert {(fid, mag) for fid, mag, *_rest in effects} == {(effect, magnitude)}
        assert [_ctda(cs[0])[1] for *_e, cs in effects] == [float(p) for p in range(2, 82)]
        assert all(cs[0][:4] + cs[0][8:] == VANILLA_CTDA[:4] + VANILLA_CTDA[8:]
                   for *_e, cs in effects)
    splo = [struct.unpack('<I', p)[0] for s, p in _subs(convert_CREA(SHAMAN)) if s == 'SPLO']
    assert splo == [lp.level_pool_ability(SHAMAN, pool) for pool in lp.POOLS]
    assert len(set(splo)) == 3 and all(splo)


def test_magicka_and_stamina_abilities_do_not_move_the_health_ability():
    """The health key is the four numbers at its old site; the other two have sites of their own."""
    def ids(rec: dict) -> dict:
        """{pool: ability FormID} from a fresh PluginWriter."""
        lp.reset()
        _build(PluginWriter(masters=['Skyrim.esm'], is_esm=True), [rec])
        return {pool: lp.level_pool_ability(rec, pool) for pool in lp.POOLS}

    plain = dict(SHAMAN)
    plain['ACBS.SpellPoints'] = plain['ACBS.Fatigue'] = '0'
    writer = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
    health = writer.derive_formid('CREA_LEVEL_POOL', (30, 1, 0, 0))
    assert ids(plain) == {lp.HEALTH: health, lp.MAGICKA: 0, lp.STAMINA: 0}
    full = ids(SHAMAN)
    assert full[lp.HEALTH] == health
    assert full[lp.MAGICKA] == writer.derive_formid('CREA_LEVEL_POOL_MAGICKA', (20, 1, 0, 0))
    assert full[lp.STAMINA] == writer.derive_formid('CREA_LEVEL_POOL_STAMINA', (45, 1, 0, 0))
    assert len(set(full.values())) == 3


def test_a_stored_zero_writes_no_ability_and_a_zero_pool():
    """No spell points and no fatigue: nothing to grow, and the offsets cancel the race's start."""
    rec = _crea('00010022', 'TestHusk', 30, 1)
    _give_race([rec], stamina=70.0)
    writer = _Writer()
    assert _build(writer, [rec]) == 1
    acbs = crea_acbs(rec)
    assert struct.unpack_from('<hh', acbs, 4) == (0, -70)
    assert struct.unpack_from('<HHH', crea_dnam(rec), 36) == (60, 0, 0)
    assert lp.level_pool_abilities(rec) == [lp.level_pool_ability(rec)]


def test_level_one_pools_ride_the_acbs_offsets_and_the_dnam_cache():
    """Offsets are relative to the race's start (50 / 0 / its Starting Stamina); DNAM is absolute."""
    _give_race([SHAMAN], stamina=70.0)
    acbs = crea_acbs(SHAMAN)
    assert struct.unpack_from('<hh', acbs, 4) == (20 * 2, 45 * 2 - 70)
    assert struct.unpack_from('<h', acbs, 20)[0] == 30 * 2 - 50
    assert struct.unpack_from('<HHH', crea_dnam(SHAMAN), 36) == (60, 40, 90)


def test_dnam_cache_stops_at_the_u16_ceiling():
    """A level-1 pool of exactly 65,535 is cached whole, and a larger one as 65,535."""
    rec = _crea('00010026', 'TestBehemoth', 40, 4, magicka=20000, fatigue=13107)
    assert struct.unpack_from('<HHH', crea_dnam(rec), 36) == (200, 65535, 65535)


# ---------------------------------------------------------------------------
# The race's start
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('edid, start', [
    pytest.param('TestMarshWisp', WISP_START, id='WispRace'),
    pytest.param('TestCaveTroll', (150.0, 0.0, 100.0), id='TrollRace'),
    pytest.param('TestNamelessThing', (20.0, 0.0, 80.0), id='the fallback race'),
])
def test_stand_in_race_pools_are_solved_against_its_real_start(edid, start):
    """With no generated race the offsets cancel the Skyrim race's own health, magicka and stamina.

    `start` is that race's RACE DATA in Skyrim.esm; offset -2 under CalcMax 9
    gives the creature levels below.
    """
    rec = _crea('00010030', edid, 22, -2, cmax=9, magicka=10, fatigue=20, stand_in=True)
    assert lp.race_start(rec) == start
    writer = _Writer()
    _build(writer, [rec])
    for player, creature in {1: 1, 2: 1, 5: 3, 10: 8, 30: 9, 81: 9}.items():
        for effect, at, stored, base in ((FORTIFY_HEALTH, 20, 22, start[0]),
                                         (FORTIFY_MAGICKA, 4, 10, start[1]),
                                         (FORTIFY_STAMINA, 6, 20, start[2])):
            assert _pool_value(rec, writer.records, effect, base, at, player) == stored * creature


def test_every_race_the_converter_can_assign_has_a_start_row():
    """The table covers each pattern's race and the fallback, and nothing else."""
    assignable = {fid for _keyword, fid, _source, _alt in CREA_RACE_PATTERNS}
    assignable.add(resolve_creature_race('', '')[0])
    assert set(VANILLA_RACE_START) == assignable
    assert VANILLA_RACE_START[WISP_RACE] == WISP_START
    assert all(len(row) == 3 and min(row) >= 0 for row in VANILLA_RACE_START.values())


def test_a_stand_in_race_with_no_start_row_is_counted_and_keeps_the_old_offsets(
        monkeypatch, tmp_path, capsys):
    """Unknown start: health over 50, magicka over 0, stamina offset 0, and a counted warning."""
    wisp = _stand_in_wisp()
    writer = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
    creature_races.build_creature_races({'CREA': [wisp, STRIDER, FIXED]}, writer, str(tmp_path))
    out = capsys.readouterr().out
    assert 'Creature level pools: 2 level-scaled CREA on a stand-in Skyrim race' in out
    assert 'WARNING' not in out
    assert struct.unpack_from('<hh', crea_acbs(wisp), 4) == (10 - 50, 20 - 50)

    monkeypatch.setattr(lp, 'VANILLA_RACE_START', {})
    creature_races.build_creature_races({'CREA': [wisp, STRIDER, FIXED]}, writer, str(tmp_path))
    assert lp.race_start(wisp) is None
    assert ('WARNING: Creature level pools: 2 level-scaled CREA on a stand-in race with no '
            'starting values (00013208 x1, 000B7998 x1)') in capsys.readouterr().out
    acbs = crea_acbs(wisp)
    assert struct.unpack_from('<hh', acbs, 4) == (10, 0)
    assert struct.unpack_from('<h', acbs, 20)[0] == 22 - 50
    assert lp.level_pool_ability(wisp, lp.STAMINA) != 0


def test_offsets_stay_inside_int16_whatever_the_race_starts_with():
    """A start under the ability's base cannot push past 32767, a huge one not under -32768."""
    colossus = _crea('00010031', 'TestNamelessColossus', 300, 150, stand_in=True)
    assert lp.race_start(colossus)[0] == 20.0
    assert struct.unpack_from('<h', crea_acbs(colossus), 20)[0] == 32767
    husk = _crea('00010032', 'TestHusk', 30, 1)
    _give_race([husk], stamina=40000.0)
    assert struct.unpack_from('<h', crea_acbs(husk), 6)[0] == -32768


def _walk_races(monkeypatch, writer, own: list, master_export: dict = None) -> None:
    """The race builder's real walk over `own`, every creature in one stubbed project.

    The race chain is a stub that only allocates the race's FormID, which
    adopts the master's race when `writer` can.
    """
    monkeypatch.setattr(creature_races, '_PROJECTS', {'testbeast': {}})
    monkeypatch.setattr(creature_races, 'folder_of', lambda rec: 'testbeast')
    monkeypatch.setattr(creature_races, 'bodies_of', lambda rec, proj: ['body'])
    monkeypatch.setattr(creature_races, '_attack_reach', lambda rec: 0)
    monkeypatch.setattr(creature_races, '_build_race_chain', lambda w, *_a: (
        generated_formid(w, 'RACE', 'TES4TestBeastRace', 'CREA_RACE', 'testbeast'), {}, 0))
    lp.reset()
    creature_races._CREA_RACE_MAP.clear()
    by_type = {'CREA': own}
    groups = creature_races._race_groups(creature_races._shared_creatures(by_type, master_export))
    creature_races._build_all_races(writer, by_type, groups, set(), {'testbeast'})


def _stamina_offset(rec: dict) -> int:
    """The creature's ACBS stamina offset."""
    return struct.unpack_from('<h', crea_acbs(rec), 6)[0]


#: A race's founding creature (Fatigue 70, fixed level) and a level-scaled one sharing its race.
ALPHA = _crea('00000A01', 'TestBeastAlpha', 90, 4, flags=0, fatigue=70, stand_in=True)
CUB = _crea('00000A02', 'TestBeastCub', 10, 0, fatigue=20, stand_in=True)


def test_stamina_is_solved_against_the_founders_fatigue_not_the_creatures_own(monkeypatch):
    """The race starts at its founder's Fatigue; the cub lands on its own 20 x 1."""
    _walk_races(monkeypatch, _Writer(), [ALPHA, CUB])
    assert lp.race_start(CUB) == (CREATURE_RACE_BASE_HEALTH, 0.0, 70.0)
    assert 70 + _stamina_offset(CUB) == 20 * 1


def test_dependent_solves_stamina_against_the_start_the_adopted_race_carries(monkeypatch):
    """A dependent raises the founder's Fatigue 70 -> 120; the adopted race still starts at 70.

    Its own creature of that race must land on Fatigue x L0. With no master
    race to adopt the dependent builds the race itself, from the founder it sees.
    """
    stronger = dict(ALPHA)
    stronger['ACBS.Fatigue'] = '120'
    elder = _crea('01000B01', 'TestBeastElder', 10, 0, fatigue=30, stand_in=True)
    in_master = {'00000A01': ALPHA, '00000A02': CUB}
    master_race = _race_record(RACE_FID, (CREATURE_RACE_BASE_HEALTH, 0.0, 70.0),
                               'TES4TestBeastRace')
    child = PluginWriter(masters=['Skyrim.esm', 'TestMaster.esm'], is_esm=False)
    child.adoption = MasterAdoption(_MasterIndex({RACE_FID: master_race}))
    _walk_races(monkeypatch, child, [stronger, elder], in_master)
    assert creature_races.get_creature_race(0x000B01) == RACE_FID
    assert lp.race_start(elder) == (CREATURE_RACE_BASE_HEALTH, 0.0, 70.0)
    assert 70 + _stamina_offset(elder) == 30 * 1

    alone = PluginWriter(masters=['Skyrim.esm', 'TestMaster.esm'], is_esm=False)
    _walk_races(monkeypatch, alone, [stronger, elder], in_master)
    assert lp.race_start(elder) == (CREATURE_RACE_BASE_HEALTH, 0.0, 120.0)
    assert 120 + _stamina_offset(elder) == 30 * 1


def test_race_builder_resolves_a_master_creatures_ability_from_the_master(tmp_path):
    """Phase 0f hands the masters' creatures to the pools, so each resolves to its master's ability."""
    elk = _crea('0001000E', 'TestElk', 40, 0)
    master = PluginWriter(masters=['Skyrim.esm'], is_esm=True)
    _build(master, [elk])
    in_master = {struct.unpack_from('<I', r, 12)[0]: r for r in master.top_records()}
    elk_fid = lp.level_pool_ability(elk)

    child = PluginWriter(masters=['Skyrim.esm', 'TestMaster.esm'], is_esm=False)
    child.adoption = MasterAdoption(_MasterIndex(in_master))
    creature_races.build_creature_races({'CREA': [STRIDER]}, child, str(tmp_path),
                                        {'0001000E': elk})
    assert lp.level_pool_ability(elk) == elk_fid != 0
    assert lp.level_pool_ability(STRIDER) >> 24 == 2


def test_magicka_and_stamina_overflowing_int16_ride_an_unconditional_first_effect():
    """What the offset cannot hold is the ability's first effect, with no condition."""
    rec = _crea('00010023', 'TestTitan', 90, 150, magicka=400, fatigue=350)
    _give_race([rec], stamina=70.0)
    writer = _Writer()
    _build(writer, [rec])
    by_effect = {_effects(spel)[0][0]: _effects(spel) for _sig, spel in writer.records}
    acbs = crea_acbs(rec)
    assert struct.unpack_from('<hh', acbs, 4) == (32767, 32767 - 70)
    magicka, stamina = by_effect[FORTIFY_MAGICKA], by_effect[FORTIFY_STAMINA]
    assert (magicka[0][1], magicka[0][4]) == (400 * 151 - 32767, [])
    assert (stamina[0][1], stamina[0][4]) == (350 * 151 - 32767, [])
    assert magicka[1][1] == 400.0 and _ctda(magicka[1][4][0])[1] == 2.0
    assert struct.unpack_from('<HHH', crea_dnam(rec), 36) == (13590, 60400, 52850)
    for player, creature in ((1, 151), (2, 152), (81, 231)):
        assert _pool_value(rec, writer.records, FORTIFY_MAGICKA, 0.0, 4, player) == 400 * creature
        assert _pool_value(rec, writer.records, FORTIFY_STAMINA, 70.0, 6, player) == 350 * creature


def test_fixed_level_caster_keeps_its_bytes():
    """A fixed-level creature with spell points and fatigue: ACBS and DNAM as before the pools."""
    caster = _crea('00010024', 'TestFixedImp', 120, 7, flags=0, magicka=250, fatigue=300)
    _give_race([caster], stamina=70.0)
    _build(_Writer(), [caster, SHAMAN])
    assert crea_acbs(caster).hex() == '10000000fa00000007000000000064000000000028000000'
    assert crea_dnam(caster).hex() == ('1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e1e0a' + '00' * 18
                                       + '7800fa002c01' + '00' * 10)
    assert crea_spell_subs(caster) == b''


def test_a_record_with_the_fallout_acbs_field_is_never_level_scaled():
    """The FO3/FNV exporter's own ACBS key marks the record, whatever the run detected."""
    rec = dict(SHAMAN)
    rec['ACBS.TemplateFlags'] = '0'
    assert [lp.pool_numbers(rec, pool) for pool in lp.POOLS] == [None, None, None]
    assert lp.pool_numbers(SHAMAN, lp.STAMINA) == (45, 1, 0, 0)


# ---------------------------------------------------------------------------
# An override that changes one stored value
# ---------------------------------------------------------------------------

#: An ability the master's run added from state a dependent plugin's run does not have.
MASTER_ONLY_ABILITY = 0x00AA0001


def _with_extra_ability(record: bytes, before: int) -> bytes:
    """The master's built record with MASTER_ONLY_ABILITY listed before spell `before`."""
    out = []
    for sig, payload in _subs(record):
        if sig == 'SPCT':
            payload = struct.pack('<I', struct.unpack('<I', payload)[0] + 1)
        if sig == 'SPLO' and struct.unpack('<I', payload)[0] == before:
            out.append(('SPLO', struct.pack('<I', MASTER_ONLY_ABILITY)))
        out.append((sig, payload))
    body = b''.join(pack_subrecord(sig, payload) for sig, payload in out)
    return record[:4] + struct.pack('<I', len(body)) + record[8:24] + body


@pytest.mark.parametrize('key, value, pool', [
    ('DATA.Health', '44', lp.HEALTH), ('ACBS.SpellPoints', '25', lp.MAGICKA),
    ('ACBS.Fatigue', '50', lp.STAMINA),
])
def test_override_of_one_stored_value_keeps_every_other_spell_entry(key, value, pool):
    """Only that pool's ability is swapped; a converter-added entry of the master's stays."""
    master = dict(SHAMAN, SpellCount='1')
    master['Spell[0]'] = '00020001'
    plugin = dict(master)
    plugin[key] = value
    _give_race([master], stamina=70.0)
    _build(_Writer(), [master, plugin])
    stale = lp.level_pool_abilities(master)
    built = _with_extra_ability(convert_CREA(master), stale[0])
    assert _splo(_subs(built)) == [0x00020001, MASTER_ONLY_ABILITY] + stale

    subs = _override(master, plugin, built)
    fresh = lp.level_pool_abilities(plugin)
    assert [a != b for a, b in zip(stale, fresh)] == [p is pool for p in lp.POOLS]
    assert _splo(subs) == [0x00020001, MASTER_ONLY_ABILITY] + fresh
    assert dict(subs)['ACBS'] == crea_acbs(plugin)
    assert dict(subs)['DNAM'] == crea_dnam(plugin)


def test_override_of_a_level_bound_moves_the_pool_cache_too():
    """CalcMin 11 -> 1: the level-1 pools in DNAM are the plugin's, as a fresh conversion writes."""
    master = _crea('00010025', 'TestFenLurker', 15, 0, cmin=11, magicka=4, fatigue=6)
    plugin = dict(master)
    plugin['ACBS.CalcMin'] = '1'
    _build(_Writer(), [master, plugin])
    assert struct.unpack_from('<HHH', crea_dnam(master), 36) == (165, 44, 66)
    dnam = dict(_override(master, plugin))['DNAM']
    assert struct.unpack_from('<HHH', dnam, 36) == (15, 4, 6)
    assert dnam[:36] + dnam[42:] == crea_dnam(master)[:36] + crea_dnam(master)[42:]


def test_override_of_a_fixed_level_creatures_level_leaves_its_cache_alone():
    """Neither side level-scaled: DNAM is the master's bytes, whatever they are."""
    plugin = dict(FIXED)
    plugin['ACBS.Level'] = '9'
    _build(_Writer(), [FIXED, plugin])
    built = _patched(convert_CREA(FIXED), 'DNAM', 36, '<H', 4321)
    subs = _override(FIXED, plugin, built)
    assert struct.unpack_from('<H', dict(subs)['DNAM'], 36)[0] == 4321
    assert dict(subs)['ACBS'] == crea_acbs(plugin)
