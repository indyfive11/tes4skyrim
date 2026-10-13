"""Audit converted actor health against the TES4 source pool.

The engine derives an NPC's max health as

    manual:     RACE.StartingHealth + ACBS.HealthOffset
    auto-calc:  ... + (Level-1) * fNPCHealthLevelBonus
                    + (Level-1) * iAVDhmsLevelUp * wHealth / (wHealth + wMagicka + wStamina)

with fNPCHealthLevelBonus = 5.0, iAVDhmsLevelUp = 10 and w* the CLAS attribute
weights. TES4 DATA.Health is a FINAL, fully-calculated pool, so a faithful
conversion makes that sum reproduce it exactly. This reads the BUILT esm (not
the converter's own functions, so it catches anything the pipeline does after
ACBS is packed) and reports every actor whose health does not match its TES4
source.

A level-scaled TES4 creature (PC Level Offset) has no single pool: Oblivion
multiplies DATA.Health, ACBS.SpellPoints and ACBS.Fatigue by the creature's
level, the player's plus ACBS.Level clamped to CalcMin/CalcMax. Its converted
pools are manual stats plus the effects of its level-pool abilities, so
health, magicka and stamina are recomputed at several player levels from the
built records and compared with the export at each. Level-scaled NPCs are
still skipped.

A creature may list an ability, or use a race, that lives in a master: a
converted master's, or a Skyrim.esm race standing in for a creature with no
generated one. Give the masters with --masters, Skyrim.esm included, and they
are read too; what a given master should hold and does not is then a
mismatch. A creature whose race is in a file not read is "undetermined":
nothing is assumed of a race that was not read.

Usage:
    python tools/audit/actor_health_audit.py output/Nehrim.esm/Nehrim.esm export/Nehrim.esm
    python tools/audit/actor_health_audit.py <built.esm> <export_dir> [--limit N] [--all]
        [--masters <master>[,<master>...]]

See: docs/commentary/tes5_import_actors.md#health-offset
See: docs/commentary/tes5_import_actors.md#level-pools
"""
import argparse
import collections
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from tools.esm.tes5_esm_reader import read_tes5_file, _get

HEALTH_LEVEL_BONUS = 5
HMS_POINTS_PER_LEVEL = 10
AUTO_CALC_STATS = 0x10
PC_LEVEL_MULT = 0x80
DEFAULT_RACE_BASE = 50.0

#: Player levels a level-scaled creature's pool is checked at.
PLAYER_LEVELS = (1, 2, 5, 10, 20, 30, 50, 81)
#: The player level past which a pool with no CalcMax stops growing.
PLAYER_LEVEL_CEILING = 81
#: CTDA of a level-pool rung: GetLevel >= N, run on Reference PlayerRef.
FUNC_GET_LEVEL, OP_GE, RUN_ON_REFERENCE, PLAYER_REF = 80, 0x60, 2, 0x14
#: SPIT (spell type, cast type, delivery) of an Ability: constant effect on self.
SPIT_ABILITY = (4, 0, 0)
#: Record types an actor's SPLO may name.
SPELL_LIST_TYPES = ('SPEL', 'SHOU', 'LVSP')


class Pool(collections.namedtuple('Pool', 'name key default acbs race effect')):
    """One pool of a level-scaled creature.

    `key`/`default`: the export field and its value when absent; `acbs` and
    `race`: byte offsets of its int16 offset in ACBS and of its float starting
    value in RACE DATA; `effect`: the Skyrim.esm effect its ability carries.
    """


#: AbFortifyHealth, AbFortifyMagickaHighElf and AbFortifyStamina.
POOLS = (Pool('health', 'DATA.Health', 50, 20, 36, 0x000A1A3F),
         Pool('magicka', 'ACBS.SpellPoints', 0, 4, 40, 0x000CDA4B),
         Pool('stamina', 'ACBS.Fatigue', 100, 6, 44, 0x00091F77))


def _sub(rec, sig):
    s = _get(rec, sig)
    return s.data if s is not None else None


def _fid(rec, sig):
    d = _sub(rec, sig)
    return struct.unpack('<I', d[:4])[0] if d and len(d) >= 4 else 0


def parse_export(path):
    out = {}
    cur = None
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            line = line.rstrip('\n')
            if line.startswith('---RECORD_BEGIN---'):
                if cur and cur.get('EditorID'):
                    out[cur['EditorID']] = cur
                cur = {}
            elif cur is not None and '=' in line:
                k, v = line.split('=', 1)
                cur[k.strip()] = v.split()[0].strip() if v.strip() else ''
    if cur and cur.get('EditorID'):
        out[cur['EditorID']] = cur
    return out


def rung_threshold(ctda):
    """The player level of a GetLevel >= N condition run on PlayerRef, else None."""
    if len(ctda) < 32:
        return None
    op, value, func = struct.unpack_from('<B3xfH', ctda, 0)
    shape = (op, func) + struct.unpack_from('<II', ctda, 20)
    return value if shape == (OP_GE, FUNC_GET_LEVEL, RUN_ON_REFERENCE, PLAYER_REF) else None


def pool_effects(spel):
    """[(effect FormID, player level threshold or None, magnitude)] of a level-pool ability, else None.

    A level-pool ability is a SPEL whose every effect is one of the POOLS
    effects, unconditional or under one GetLevel >= N condition run on PlayerRef.
    """
    known = {pool.effect for pool in POOLS}
    effects = []
    for s in spel.subrecords:
        if s.type == 'EFID':
            effects.append([struct.unpack('<I', s.data[:4])[0], 0.0, []])
        elif s.type == 'EFIT' and effects:
            effects[-1][1] = struct.unpack_from('<f', s.data, 0)[0]
        elif s.type == 'CTDA' and effects:
            effects[-1][2].append(rung_threshold(s.data))
    if not effects or any(fid not in known or len(ctdas) > 1 or None in ctdas
                          for fid, _m, ctdas in effects):
        return None
    return [(fid, ctdas[0] if ctdas else None, m) for fid, m, ctdas in effects]


def is_ability(spel):
    """True when the SPEL's SPIT says Ability, constant effect, delivered to self."""
    spit = _sub(spel, 'SPIT')
    return bool(spit) and len(spit) >= 24 and (
        struct.unpack_from('<I', spit, 8) + struct.unpack_from('<II', spit, 16)) == SPIT_ABILITY


def _masters_of(hdr):
    """The file header's master names, lower case, in index order."""
    return [s.data.rstrip(b'\x00').decode('utf-8', 'replace').lower()
            for s in hdr.subrecords if s.type == 'MAST']


def _read(esm):
    """(master names, records) of one file; audited types parsed."""
    hdr, recs, _loc = read_tes5_file(esm, parse_types={'RACE', 'NPC_', 'CLAS', 'SPEL'})
    return _masters_of(hdr), recs


def _tables(recs, index_of):
    """(races, classes, spells) of one file's records, FormIDs through `index_of`.

    `index_of(index byte)` gives the byte the audited plugin uses for the
    same file, or None when the plugin does not load it; such records are
    left out. races: {fid: RACE DATA}, classes: {fid: HMS weights}, spells:
    {fid: (pool_effects, is_ability)} for a level-pool ability, else
    {fid: None} for any other record a spell list may name.
    """
    races, classes, spells = {}, {}, {}
    for r in recs:
        index = index_of(r.form_id >> 24)
        if index is None:
            continue
        fid = index << 24 | r.form_id & 0x00FFFFFF
        d = _sub(r, 'DATA') if r.type in ('RACE', 'CLAS') else None
        if r.type == 'RACE' and d and len(d) >= 48:
            races[fid] = d
        elif r.type == 'CLAS' and d and len(d) >= 35:
            classes[fid] = tuple(d[32:35])
        elif r.type in SPELL_LIST_TYPES:
            effects = pool_effects(r)
            spells[fid] = (effects, is_ability(r)) if effects else None
    return races, classes, spells


def load_built(esm, masters=()):
    """(RACE DATA by fid, class HMS weights by fid, NPC_ records, level pools).

    Level pools are the spells of `_tables`, plus 'own' -> the plugin's own
    FormID index byte and 'loaded' -> the index bytes whose file was read.
    `masters` are built plugins this one lists as masters, read under this
    plugin's FormID indices; a later master's record wins over an earlier
    one's, the plugin's over all.
    """
    names, recs = _read(esm)
    tables, loaded = ({}, {}, {}), {len(names)}
    order = sorted((names.index(os.path.basename(m).lower()), m) for m in masters
                   if os.path.basename(m).lower() in names)
    for skipped in sorted(set(masters) - {m for _i, m in order}):
        print(f'  --masters: {os.path.basename(skipped)} is not a master of this plugin, not read')
    for index, master in order:
        theirs, m_recs = _read(master)
        theirs.append(names[index])

        def index_of(byte, theirs=theirs):
            """The audited plugin's index byte for this master's index `byte`."""
            name = theirs[min(byte, len(theirs) - 1)]
            return names.index(name) if name in names else None

        for table, found in zip(tables, _tables(m_recs, index_of)):
            table.update(found)
        loaded.add(index)
    for table, found in zip(tables, _tables(recs, lambda byte: min(byte, len(names)))):
        table.update(found)
    races, classes, spells = tables
    return (races, classes, [r for r in recs if r.type == 'NPC_'],
            dict(spells, own=len(names), loaded=loaded))


def _race_start(races, r, pool):
    """The race's starting `pool`, or None when the race was not read."""
    data = races.get(_fid(r, 'RNAM'))
    return struct.unpack_from('<f', data, pool.race)[0] if data is not None else None


def engine_health(r, races, classes):
    """(max health the engine computes for a fixed-level NPC_, ACBS flags, level)."""
    acbs = _sub(r, 'ACBS')
    fl, _mo, _so, lvl, _cn, _cx, _sp, _dp, _tf, ho, _bo = struct.unpack(
        '<IhhHHHHhHhH', acbs[:24])
    start = _race_start(races, r, POOLS[0])
    final = (DEFAULT_RACE_BASE if start is None else start) + ho
    if fl & AUTO_CALC_STATS:
        w = classes.get(_fid(r, 'CNAM'), (0, 0, 0))
        share = (lvl - 1) * HMS_POINTS_PER_LEVEL * w[0] // sum(w) if sum(w) else 0
        final += (lvl - 1) * HEALTH_LEVEL_BONUS + share
    return final, fl, lvl


def source_level(s, player_level):
    """The TES4 creature's level beside a `player_level` player, per the export record."""
    offset = int(s.get('ACBS.Level', '0') or 0)
    cmin = int(s.get('ACBS.CalcMin', '0') or 0)
    cmax = int(s.get('ACBS.CalcMax', '0') or 0)

    def clamp(level):
        """`level` inside CalcMin/CalcMax (0 = no bound), never below 1."""
        level = max(level, cmin) if cmin else level
        level = min(level, cmax) if cmax else level
        return max(1, level)

    low = clamp(1 + offset)
    top = max(cmax, low) if cmax > 0 else max(low, PLAYER_LEVEL_CEILING + offset)
    return max(low, min(player_level + offset, top))


def _unread(fid, pools):
    """'missing' when the file that should hold `fid` was read, else 'unloaded'."""
    return 'missing' if min(fid >> 24, pools['own']) in pools['loaded'] else 'unloaded'


def built_pools(r, races, pools):
    """({pool name: {player level: built pool} or None}, findings) for a level-scaled creature.

    Manual stats: race start + ACBS offset + every active effect on that
    pool. Every pool is None when the race was not read.
    `findings`: 'unloaded' (a spell or the race is in a file not read),
    'missing' (not in the file read that should hold it), 'not an ability'
    (a level-pool SPEL of another spell type).
    """
    acbs, findings, effects = _sub(r, 'ACBS'), set(), []
    for s in (s for s in r.subrecords if s.type == 'SPLO'):
        fid = struct.unpack('<I', s.data[:4])[0]
        if fid not in pools:
            findings.add(_unread(fid, pools))
        elif pools[fid]:
            effects += pools[fid][0]
            if not pools[fid][1]:
                findings.add('not an ability')
    if _fid(r, 'RNAM') not in races:
        findings.add(_unread(_fid(r, 'RNAM'), pools))
    built = {}
    for pool in POOLS:
        start = _race_start(races, r, pool)
        if start is None:
            built[pool.name] = None
            continue
        base = start + struct.unpack_from('<h', acbs, pool.acbs)[0]
        built[pool.name] = {p: base + sum(m for fid, t, m in effects
                                          if fid == pool.effect and (t is None or t <= p))
                            for p in PLAYER_LEVELS}
    return built, findings


def _first_bad_pool(s, built):
    """(pool name, player level, expected, built) of the first pool off its TES4 value, else None."""
    for pool in POOLS:
        stored = int(s.get(pool.key, '') or pool.default)
        stored = stored if pool.name == 'health' else max(0, stored)
        for p in PLAYER_LEVELS if built[pool.name] else ():
            want = stored * source_level(s, p)
            if abs(built[pool.name][p] - want) >= 0.6:
                return pool.name, p, want, built[pool.name][p]
    return None


def audit_level_scaled(edid, s, r, fl, races, pools, counts, rows):
    """Tally one level-scaled actor: a creature's pools are checked, an NPC skipped.

    Exact needs every pool on its TES4 value at every checked level, manual
    stats, abilities that are abilities, and nothing missing from a file
    that was read. Short of that, a creature with something in a file that
    was not read is undetermined, any other a mismatch. Appends a
    (edid, what, player level, expected, built, autocalc) row per mismatch;
    `what` is the pool's name, else the finding.
    """
    if s.get('Signature') != 'CREA':
        counts['level_mult'] += 1
        return
    built, findings = built_pools(r, races, pools)
    bad = _first_bad_pool(s, built)
    auto = bool(fl & AUTO_CALC_STATS)
    hard = sorted(findings - {'unloaded'})
    if not bad and not auto and not hard and None not in built.values():
        counts['pool_exact'] += 1
    elif 'unloaded' in findings and not hard:
        counts['pool_undetermined'] += 1
    else:
        counts['pool_mismatch'] += 1
        what = hard[0] if hard else 'auto-calc' if not bad else bad[0]
        level, want, got = bad[1:] if bad else (PLAYER_LEVELS[0], 0, 0.0)
        rows.append((edid, what, level, want, got, auto))


def audit(src, races, classes, npcs, pools):
    """Tally every built NPC_ against its source pool.

    Returns (counts, fixed-level mismatch rows, level-scaled creature mismatch rows).
    """
    counts, rows, pool_rows = collections.Counter(), [], []
    for r in npcs:
        e = _sub(r, 'EDID')
        edid = e.rstrip(b'\x00').decode('ascii', 'replace') if e else None
        s = src.get(edid)
        acbs = _sub(r, 'ACBS')
        if not s or not acbs or len(acbs) < 24:
            counts['unmatched'] += 1
            continue
        t4h = int(s.get('DATA.Health', '50') or 50)
        final, fl, lvl = engine_health(r, races, classes)
        if t4h <= 0:
            counts['corpse'] += 1
        elif fl & PC_LEVEL_MULT:
            audit_level_scaled(edid, s, r, fl, races, pools, counts, pool_rows)
        else:
            counts['dead_bug'] += final <= 0
            exact = abs(final - t4h) < 0.6
            counts['exact' if exact else 'mismatch'] += 1
            if not exact:
                rows.append((edid, t4h, final, bool(fl & AUTO_CALC_STATS), lvl))
    return counts, rows, pool_rows


def report_level_scaled(counts, pool_rows, limit):
    """Print the level-scaled creature tally and up to `limit` mismatch rows."""
    total = counts['pool_exact'] + counts['pool_mismatch'] + counts['pool_undetermined']
    levels = ', '.join(str(p) for p in PLAYER_LEVELS)
    print(f'  level-scaled creatures {total} (health, magicka, stamina at player levels {levels})')
    print(f'    exact        : {counts["pool_exact"]}')
    print(f'    mismatch     : {counts["pool_mismatch"]}')
    print(f'    undetermined : {counts["pool_undetermined"]}   <- a spell or the race is in a '
          f'file not read (--masters)')
    if not pool_rows:
        return
    print('\n  editorid                             what            player  tes4*level    built'
          '  autocalc')
    show = pool_rows if limit is None else pool_rows[:limit]
    for edid, what, level, want, built, auto in show:
        print(f'  {edid[:36]:36s} {what:14s} {level:7d} {want:11d} {built:8.0f} {str(auto):>9s}')
    if len(pool_rows) > len(show):
        print(f'  ... {len(pool_rows) - len(show)} more (--all)')


def report(name, counts, rows, limit):
    """Print the tally and up to `limit` mismatch rows (None prints all)."""
    total = counts['exact'] + counts['mismatch']
    pct = 100.0 * counts['exact'] / total if total else 0.0
    print(f'{name}: fixed-level actors {total}')
    print(f'  exact      : {counts["exact"]} ({pct:.1f}%)')
    print(f'  mismatch   : {counts["mismatch"]}')
    print(f'  SPAWN-DEAD : {counts["dead_bug"]}   <- positive TES4 health but <=0 in game')
    print(f'  skipped    : {counts["level_mult"]} pc-level-mult NPCs, {counts["corpse"]} zero-health '
          f'corpse props, {counts["unmatched"]} not in export')
    if not rows:
        return
    print('\n  editorid                              tes4    built  autocalc  level')
    show = rows if limit is None else rows[:limit]
    for edid, t4h, final, auto, lvl in show:
        print(f'  {edid[:36]:36s} {t4h:7d} {final:8.0f} {str(auto):>9s} {lvl:6d}')
    if len(rows) > len(show):
        print(f'  ... {len(rows) - len(show)} more (--all)')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('esm', help='built TES5 esm')
    ap.add_argument('export_dir', help='TES4 export dir (NPC_.txt / CREA.txt)')
    ap.add_argument('--limit', type=int, default=20, help='mismatches to print')
    ap.add_argument('--all', action='store_true', help='print every mismatch')
    ap.add_argument('--masters', default='',
                    help='comma-separated plugins this one lists as masters (Skyrim.esm too)')
    args = ap.parse_args()

    src = {}
    for sig in ('NPC_', 'CREA'):
        p = os.path.join(args.export_dir, f'{sig}.txt')
        if os.path.isfile(p):
            src.update(parse_export(p))
    if not src:
        print(f'no NPC_/CREA exports under {args.export_dir}')
        return 1
    masters = [m for m in args.masters.split(',') if m]
    counts, rows, pool_rows = audit(src, *load_built(args.esm, masters))
    limit = None if args.all else args.limit
    report(os.path.basename(args.esm), counts, rows, limit)
    report_level_scaled(counts, pool_rows, limit)
    return 0 if counts['mismatch'] == 0 and counts['pool_mismatch'] == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
