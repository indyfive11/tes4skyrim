"""Pools of a level-scaled creature: a base pool plus one conditioned effect per level.

A TES4 creature with PC Level Offset has DATA.Health, ACBS.SpellPoints and
ACBS.Fatigue each multiplied by its level, and its level is the player's
plus ACBS.Level, clamped to CalcMin/CalcMax.  Skyrim has no per-level
multiplier on an actor record, but vanilla builds a pool that grows with the
player's level out of plain records: CWMissionBoostAllyHealth is an Ability
of AbFortifyHealth effects, each conditioned on GetLevel >= N run on
PlayerRef.  This module does the same for health, magicka and stamina.  The
creature's ACBS carries the pools of a level-1 player, and one shared
ability per pool adds the stored value once more for every level above that.
Each ACBS offset is solved against what the creature's own race starts with.

See: docs/commentary/tes5_import_actors.md#level-pools
"""

import struct
from collections import Counter
from typing import NamedTuple

from ..base.equivalents import VANILLA_RACE_START, resolve_creature_race
from ..base.tes5_reader import decompress, subrecords
from ..base.text_reader import get_formid, get_int, get_str
from ..base.writer import (RECORD_HEADER_SIZE, pack_formid_subrecord, pack_obnd, pack_record,
                           pack_string_subrecord, pack_subrecord)
from ..overrides.adoption import adopted_formid, generated_formid
from ..record_types.world_falloutnv import is_fallout_source
from ..record_types.world_morrowind import is_tes3_source

#: Player level Skyrim reaches with every skill at 100 once; no pool grows past the level it has beside it.
PLAYER_LEVEL_CEILING = 81

#: TES4 CREA ACBS flag PC Level Offset.
_PC_LEVEL_OFFSET = 0x80


# ---------------------------------------------------------------------------
# The pools
# ---------------------------------------------------------------------------

class Pool(NamedTuple):
    """One of the three pools: where TES4 stores it and how its ability is named and keyed."""

    #: Export key of the stored value, and the value an export without the key gets.
    key: str
    default: int
    #: The letter after 'TES4LevelPool' in the ability's EditorID.
    letter: str
    #: The derive_formid site of the ability.
    site: str
    #: The Skyrim.esm PeakValueModifier effect on this pool's actor value.
    effect: int


#: AbFortifyHealth, the effect CWMissionBoostAllyHealth uses.
HEALTH = Pool('DATA.Health', 50, 'H', 'CREA_LEVEL_POOL', 0x000A1A3F)
#: AbFortifyMagickaHighElf, vanilla's constant magicka ability effect.
MAGICKA = Pool('ACBS.SpellPoints', 0, 'M', 'CREA_LEVEL_POOL_MAGICKA', 0x000CDA4B)
#: AbFortifyStamina, vanilla's constant stamina ability effect.
STAMINA = Pool('ACBS.Fatigue', 100, 'S', 'CREA_LEVEL_POOL_STAMINA', 0x00091F77)
#: Every pool, in the order their abilities follow the spell list.
POOLS = (HEALTH, MAGICKA, STAMINA)

#: FO3/FNV actor ACBS field with no TES4 counterpart; the TES4 exporter never writes it.
_FALLOUT_ACBS_KEY = 'ACBS.TemplateFlags'
#: Skyrim.esm EitherHand EQUP, the ETYP vanilla abilities carry.
_ETYP_EITHER_HAND = 0x00013F44
#: SPIT spell type Ability; cast type 0 Constant and delivery 0 Self go with it.
_SPELL_TYPE_ABILITY = 4

#: CTDA function GetLevel, comparison >=, run on Reference, and that reference: PlayerRef.
_FUNC_GET_LEVEL, _OP_GE, _RUN_ON_REFERENCE, _PLAYER_REF = 80, 0x60, 2, 0x00000014

#: An ACBS pool offset is an int16.
_OFFSET_MIN, _OFFSET_MAX = -32768, 32767

#: {(Pool, (stored, offset, cmin, cmax)): output ability SPEL FormID}, reset per plugin.
_POOL_ABILITY = {}
#: {CREA fid_low24: starting (health, magicka, stamina) of its generated race}, reset per plugin.
_RACE_START = {}
#: {generated RACE FormID: what `adopted_race_start` read for it}, reset per plugin.
_ADOPTED_START = {}


# ---------------------------------------------------------------------------
# The numbers
# ---------------------------------------------------------------------------

def pool_numbers(rec: dict, pool: Pool = HEALTH):
    """(stored value, level offset, CalcMin, CalcMax) of a level-scaled TES4 creature, else None.

    None for a fixed-level creature, a zero-health corpse prop, and any
    FO3/FNV or Morrowind actor, whose bit 0x80 and Level mean something else:
    the run's source game, or a record carrying the FO3/FNV-only ACBS field.
    The stored value is `pool`'s; a stored magicka or stamina may be 0.
    See: docs/commentary/tes5_import_actors.md#level-pools
    """
    if not get_int(rec, 'ACBS.Flags') & _PC_LEVEL_OFFSET:
        return None
    stored = get_int(rec, HEALTH.key, HEALTH.default)
    if stored <= 0 or _FALLOUT_ACBS_KEY in rec or is_fallout_source() or is_tes3_source():
        return None
    if pool is not HEALTH:
        stored = max(0, get_int(rec, pool.key, pool.default))
    return (stored, get_int(rec, 'ACBS.Level'),
            max(0, get_int(rec, 'ACBS.CalcMin')), max(0, get_int(rec, 'ACBS.CalcMax')))


def level_span(offset: int, cmin: int, cmax: int) -> tuple:
    """(L0, top): the creature's level for a level-1 player, and the highest it reaches.

    A CalcMin or CalcMax of 0 is no bound; a level is never below 1.  The
    top is the level it has beside a PLAYER_LEVEL_CEILING player, or CalcMax
    when that is lower: a huge CalcMax must not buy an effect per level of it.
    """
    low = 1 + offset
    if cmin:
        low = max(low, cmin)
    if cmax:
        low = min(low, cmax)
    low = max(1, low)
    top = max(low, PLAYER_LEVEL_CEILING + offset)
    if cmax > 0:
        top = min(top, max(cmax, low))
    return low, top


def creature_level(player_level: int, offset: int, cmin: int, cmax: int) -> int:
    """The level the converted pool gives the creature beside a `player_level` player."""
    low, top = level_span(offset, cmin, cmax)
    return max(low, min(player_level + offset, top))


def base_split(stored: int, low: int, race_base: float) -> tuple:
    """(ACBS offset, what int16 cannot hold) of `stored * low` over `race_base`."""
    want = stored * low - race_base
    offset = int(min(want, _OFFSET_MAX))
    return offset, float(want - offset)


def level_pool_base(rec: dict, pool: Pool = HEALTH):
    """A level-scaled creature's `pool` beside a level-1 player, else None."""
    numbers = pool_numbers(rec, pool)
    if numbers is None:
        return None
    stored, offset, cmin, cmax = numbers
    return stored * level_span(offset, cmin, cmax)[0]


def level_pool_offset(rec: dict, ability_base: float, pool: Pool = HEALTH):
    """ACBS offset that lands a level-scaled creature's level-1 `pool` on its race's start, else None.

    `ability_base` is the start the pool's ability is split over (`base_split`);
    a race whose start is unknown is taken to start there.
    See: docs/commentary/tes5_import_actors.md#stand-in-race-start
    """
    numbers = pool_numbers(rec, pool)
    if numbers is None:
        return None
    stored, offset, cmin, cmax = numbers
    carried = base_split(stored, level_span(offset, cmin, cmax)[0], ability_base)[0]
    start = race_start(rec)
    own = ability_base if start is None else start[POOLS.index(pool)]
    return max(_OFFSET_MIN, min(int(carried + ability_base - own), _OFFSET_MAX))


def pool_rungs(numbers: tuple) -> list:
    """[(player level threshold, magnitude)], one per creature level above L0."""
    stored, offset, cmin, cmax = numbers
    low, top = level_span(offset, cmin, cmax)
    return [(level - offset, float(stored)) for level in range(low + 1, top + 1)]


# ---------------------------------------------------------------------------
# The race's start
# ---------------------------------------------------------------------------

def adopted_race_start(writer, race_fid: int):
    """Starting (health, magicka, stamina) of the master RACE a child adopted as `race_fid`, else None.

    An adopted race ships as the master's record, so its start is the
    master's whatever this plugin's records say.
    See: docs/commentary/tes5_import_actors.md#stand-in-race-start
    """
    adoption = getattr(writer, 'adoption', None)
    if adoption is None or race_fid not in adoption.adopted:
        return None
    if race_fid not in _ADOPTED_START:
        record = adoption.master_index.record(race_fid)
        flags = struct.unpack_from('<I', record, 8)[0] if len(record) >= RECORD_HEADER_SIZE else 0
        data = next((p for sig, p in subrecords(decompress(record[RECORD_HEADER_SIZE:], flags))
                     if sig == b'DATA'), b'')
        _ADOPTED_START[race_fid] = struct.unpack_from('<fff', data, 36) if len(data) >= 48 else None
    return _ADOPTED_START[race_fid]


def set_race_start(crea_fid: int, start: tuple) -> None:
    """Record (health, magicka, stamina) of creature `crea_fid`'s generated race."""
    _RACE_START[crea_fid & 0x00FFFFFF] = start


def _stand_in_race(rec: dict) -> int:
    """The Skyrim race FormID a creature with no generated race is given."""
    return resolve_creature_race(get_str(rec, 'EditorID'), get_str(rec, 'FULL'))[0]


def race_start(rec: dict):
    """(health, magicka, stamina) the creature's race starts with; None if unknown.

    See: docs/commentary/tes5_import_actors.md#stand-in-race-start
    """
    start = _RACE_START.get(get_formid(rec, 'FormID') & 0x00FFFFFF)
    return start if start is not None else VANILLA_RACE_START.get(_stand_in_race(rec))


def stand_in_census(creatures: list) -> tuple:
    """(level-scaled creatures on a stand-in race, Counter of such races with no row)."""
    scaled = [rec for rec in creatures if pool_numbers(rec) is not None
              and get_formid(rec, 'FormID') & 0x00FFFFFF not in _RACE_START]
    return len(scaled), Counter(_stand_in_race(rec) for rec in scaled if race_start(rec) is None)


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

def pool_edid(numbers: tuple, pool: Pool = HEALTH) -> str:
    """The ability's EditorID; 'm' marks a negative offset."""
    stored, offset, cmin, cmax = numbers
    sign = 'm' if offset < 0 else ''
    return f'TES4LevelPool{pool.letter}{stored}O{sign}{abs(offset)}N{cmin}X{cmax}'


def _effect(effect: int, magnitude: float, threshold=None) -> bytes:
    """One `effect` of `magnitude`, active from player level `threshold` (always when None)."""
    subs = pack_formid_subrecord('EFID', effect)
    subs += pack_subrecord('EFIT', struct.pack('<fII', magnitude, 0, 0))
    if threshold is None:
        return subs
    return subs + pack_subrecord('CTDA', struct.pack(
        '<B3xfHHIIIIi', _OP_GE, float(threshold), _FUNC_GET_LEVEL, 0, 0, 0,
        _RUN_ON_REFERENCE, _PLAYER_REF, -1))


def _ability_subs(edid: str, numbers: tuple, race_base: float, pool: Pool = HEALTH) -> bytes:
    """A constant self Ability: the int16 remainder, if any, then one effect per rung; b'' when empty.

    A stored value of 0 adds nothing at any level, so it has no ability.
    """
    stored, offset, cmin, cmax = numbers
    if stored <= 0:
        return b''
    remainder = base_split(stored, level_span(offset, cmin, cmax)[0], race_base)[1]
    effects = _effect(pool.effect, remainder) if remainder > 0 else b''
    for threshold, magnitude in pool_rungs(numbers):
        effects += _effect(pool.effect, magnitude, threshold)
    if not effects:
        return b''
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_obnd()
    subs += pack_formid_subrecord('ETYP', _ETYP_EITHER_HAND)
    subs += pack_subrecord('SPIT', struct.pack(
        '<IIIfII12x', 0, 0, _SPELL_TYPE_ABILITY, 0.0, 0, 0))
    return subs + effects


def reset() -> None:
    """Forget every ability and race start; runs once per plugin, before the build."""
    _POOL_ABILITY.clear()
    _RACE_START.clear()
    _ADOPTED_START.clear()


def _build_pool(writer, pool: Pool, own: list, masters: list, race_base: float) -> int:
    """Write one `pool` ability per distinct four numbers among `own` creatures; returns how many.

    The FormID is keyed on the pool's site and the four numbers, so creatures
    that scale alike share an ability, and a dependent plugin adopts its
    master's. A `masters` creature only resolves to the ability its master
    already has.
    See: docs/commentary/tes5_import_actors.md#level-pools
    """
    made = 0
    for rec in own:
        numbers = pool_numbers(rec, pool)
        if numbers is None or (pool, numbers) in _POOL_ABILITY:
            continue
        edid = pool_edid(numbers, pool)
        subs = _ability_subs(edid, numbers, race_base, pool)
        if not subs:
            continue
        fid = generated_formid(writer, 'SPEL', edid, pool.site, numbers)
        writer.add_record('SPEL', pack_record('SPEL', fid, 0, subs))
        _POOL_ABILITY[(pool, numbers)] = fid
        made += 1
    for rec in masters:
        numbers = pool_numbers(rec, pool)
        if numbers is None or (pool, numbers) in _POOL_ABILITY:
            continue
        fid = adopted_formid(writer, 'SPEL', pool_edid(numbers, pool))
        if fid:
            _POOL_ABILITY[(pool, numbers)] = fid
    return made


def build_level_pools(writer, own: list, masters: list, race_base: float) -> dict:
    """Every pool's abilities; {Pool: how many written}. `race_base` is health's alone."""
    return {pool: _build_pool(writer, pool, own, masters,
                              race_base if pool is HEALTH else 0.0)
            for pool in POOLS}


def level_pool_ability(rec: dict, pool: Pool = HEALTH) -> int:
    """The creature's level-pool ability for `pool`, or 0 when it has none."""
    numbers = pool_numbers(rec, pool)
    return _POOL_ABILITY.get((pool, numbers), 0) if numbers is not None else 0


def level_pool_abilities(rec: dict) -> list:
    """The creature's level-pool abilities, health then magicka then stamina."""
    return [fid for fid in (level_pool_ability(rec, pool) for pool in POOLS) if fid]
