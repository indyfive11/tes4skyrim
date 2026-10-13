"""Audit the Oblivion weapon-rule perk of a built plugin against its TES4 export.

Reads the BUILT plugin (not the converter's functions) and checks three things:

1. Every entry of `TES4OblivionWeaponRulePerk` holds the value this file
   recomputes for its band from the export's game settings, and the bands of
   each table tile 0-100 with one open band above.
2. A sweep of weapon skill x skill x attribute for each attacker (a converted
   actor, the player, a Skyrim actor) evaluates the perk's conditions the way
   the engine combines them and compares the product of the matching entries
   with Oblivion's exact factor over Skyrim's; the worst error is printed.
3. Every actor in the stat faction lists the perk, and no other actor does.

The settings a plugin does not author are read from its masters' exports
(--master-export, farthest first), then from Oblivion.exe's compiled defaults.
A perk or faction the plugin adopts from a master is found in --master.

Usage, from the repo root:
    python -m extensions.oblivion_weapon_rule.audit <built plugin> <export dir>
    python -m extensions.oblivion_weapon_rule.audit <built plugin> <export dir>
        [--master <built master>]... [--master-export <master export dir>]...

Nothing here imports the add-on's own code: the formula, the bands and the
names are a second copy, so a mistake in one shows against the other. The
built plugin is read with the tree's plugin reader.

See: extensions/oblivion_weapon_rule/README.md
"""
import argparse
import itertools
import os
import struct
import sys

from tools.esm.tes5_esm_reader import read_tes5_file

PERK_EDID = 'TES4OblivionWeaponRulePerk'
GATE_EDID = 'TES4OblivionWeaponRule'
CONVERTED_EDID = 'TES4AttributeStrength'
ATTRIBUTE_FACTIONS = {'Strength': 'TES4AttributeStrength', 'Agility': 'TES4AttributeAgility'}
PLAYER_GLOBALS = {'Strength': 'TES4PlayerStrength', 'Agility': 'TES4PlayerAgility'}

#: Oblivion.exe compiled-in values of the settings the hit formula reads.
EXE_DEFAULTS = {'fDamageWeaponMult': 1.0, 'fDamageWeaponConditionBase': 0.0,
                'fDamageWeaponConditionMult': 1.0, 'fDamageSkillBase': 0.2,
                'fDamageSkillMult': 1.8, 'fDamageStrengthBase': 0.5,
                'fDamageStrengthMult': 1.0, 'fFatigueBase': 1.25}

#: Skyrim's skill factor is Min + (Max - Min) x skill / 100: (NPC, player). Unverified defaults.
SKYRIM_SKILL = {'npc': (1.0, 1.5), 'player': (1.0, 1.5)}

BUDGET = 0.05
CAP = 100
SKYRIM_ACTOR_ATTRIBUTE = 50
ONE_HANDED, TWO_HANDED, MARKSMAN = 6, 7, 8
#: Weapon skill -> the attribute Oblivion scales its hits by.
WEAPON_ATTRIBUTE = {ONE_HANDED: 'Strength', TWO_HANDED: 'Strength', MARKSMAN: 'Agility'}
DEALT, TAKEN = 35, 36
MULTIPLY_VALUE, FLOAT_VALUE = 3, 1
GET_IN_FACTION, GET_IS_ID, GET_FACTION_RANK, GET_GLOBAL = 71, 72, 73, 74
IS_WEAPON_SKILL_TYPE, GET_BASE_AV = 109, 277
#: The condition functions whose first parameter is a FormID.
FORM_FUNCTIONS = (GET_IN_FACTION, GET_IS_ID, GET_FACTION_RANK, GET_GLOBAL)
PLAYER = 0x7
OP_EQ, OP_GE, OP_LT, OR_FLAG = 0x00, 0x60, 0x80, 0x01
#: Which thing each condition tab of an entry point tests.
TAB_SUBJECTS = {DEALT: ('attacker', 'weapon', 'target'), TAKEN: ('target', 'attacker', 'weapon')}
#: A rank no band table is built for, held by the hit's target: an entry reading it shows up.
TARGET_RANK = 77
#: The attacker's other two weapon skills sit this far from the swung one, so a wrong read shows.
OTHER_SKILL_SHIFT = 37


# ---------------------------------------------------------------------------
# The export's settings and the exact factors
# ---------------------------------------------------------------------------

def _gmst_values(path):
    """(EditorID, value text) of each record in an exported GMST.txt."""
    name = None
    with open(path, encoding='utf-8', errors='replace') as fh:
        for line in fh:
            key, _, value = line.rstrip('\n').partition('=')
            if key == 'EditorID':
                name = value.strip()
            elif key == 'DATA.Value':
                yield name, value


def export_settings(export_dirs):
    """EXE_DEFAULTS overlaid by each export dir's GMST.txt in order; ({name: value}, authored names)."""
    settings, authored = dict(EXE_DEFAULTS), set()
    for folder in export_dirs:
        path = os.path.join(folder, 'GMST.txt')
        found = _gmst_values(path) if os.path.isfile(path) else ()
        for name, value in found:
            if name in settings:
                settings[name] = float(value)
                authored.add(name)
    return settings, authored


def exact_skill(settings, skill, who):
    """Oblivion's hit without its attribute factor over Skyrim's skill factor."""
    low, high = SKYRIM_SKILL[who]
    capped = min(CAP, max(0, skill))
    oblivion = (settings['fDamageWeaponMult']
                * (settings['fDamageWeaponConditionBase'] + settings['fDamageWeaponConditionMult'])
                * settings['fFatigueBase']
                * (settings['fDamageSkillBase'] + settings['fDamageSkillMult'] * capped / 100.0))
    return max(0.0, oblivion / (low + (high - low) * skill / 100.0))


def exact_attribute(settings, attribute):
    """Oblivion's Strength or Agility factor."""
    capped = min(CAP, max(0, attribute))
    return max(0.0, settings['fDamageStrengthBase'] + settings['fDamageStrengthMult'] * capped / 100.0)


def expected_bands(factor):
    """[(lo, hi, value)] recomputed here: integer runs whose ends stay within BUDGET of their harmonic mean."""
    bands, start = [], 0
    for stat in range(1, CAP + 2):
        a, b = factor(start), factor(min(stat, CAP))
        if stat <= CAP and (a + b <= 0 or abs(b - a) / (a + b) <= BUDGET):
            continue
        a, b = factor(start), factor(stat - 1)
        bands.append((start, stat, 2 * a * b / (a + b) if a + b > 0 else 0.0))
        start = stat
    return bands + [(CAP + 1, None, factor(CAP))]


# ---------------------------------------------------------------------------
# Reading the built plugin
# ---------------------------------------------------------------------------

def _edid(rec):
    """A record's EditorID."""
    for s in rec.subrecords:
        if s.type == 'EDID':
            return s.data.rstrip(b'\0').decode('latin-1')
    return ''


def _master_names(header, path):
    """The file's master list plus itself, lowercased: who each index byte names."""
    names = [s.data.rstrip(b'\0').decode('latin-1').lower()
             for s in header.subrecords if s.type == 'MAST']
    return names + [os.path.basename(path).lower()]


def _remapper(names, plugin_names):
    """FormID of a file with master list `names` -> the same form as the plugin names it."""
    def remap(fid):
        """The plugin-space FormID; ValueError for a file the plugin does not list."""
        index = fid >> 24
        name = names[index] if index < len(names) else names[-1]
        if name not in plugin_names:
            raise ValueError(f'{name} is not a master of the plugin')
        return (plugin_names.index(name) << 24) | (fid & 0x00FFFFFF)
    return remap


def load_built(path, masters):
    """(the plugin's records by signature, {(sig, EditorID): (record, remap)} masters then plugin).

    `remap` turns a FormID as that record's file writes it into the plugin's.
    """
    wanted = {'PERK', 'NPC_', 'FACT', 'GLOB'}
    header, own_recs, _loc = read_tes5_file(path, parse_types=wanted)
    plugin_names = _master_names(header, path)
    by_edid = {}
    for source in masters:
        hdr, recs, _loc = read_tes5_file(source, parse_types=wanted)
        remap = _remapper(_master_names(hdr, source), plugin_names)
        by_edid.update({(r.type, _edid(r)): (r, remap) for r in recs if r.type in wanted})
    own = {}
    for rec in own_recs:
        if rec.type in wanted:
            own.setdefault(rec.type, []).append(rec)
            by_edid[(rec.type, _edid(rec))] = (rec, int)
    return own, by_edid


def _open_tab(entry, sub, _remap):
    """PRKC: start a condition tab; ValueError for one repeated or past the tab count."""
    tab = sub.data[0]
    if tab in entry['tabs'] or tab >= entry.get('tab_count', 0):
        raise ValueError(f'PRKC tab {tab} repeated or past the tab count')
    entry['tab'] = tab
    entry['tabs'][tab] = []


def _add_condition(entry, sub, remap):
    """CTDA: one more condition of the open tab; ValueError outside a tab."""
    if 'tab' not in entry:
        raise ValueError('a CTDA before any PRKC')
    entry['tabs'][entry['tab']].append(parse_ctda(sub.data, remap))


def _set_data(entry, sub, _remap):
    """DATA: entry point, function, tab count; ValueError for a second one."""
    if 'point' in entry:
        raise ValueError('a second DATA in one perk entry')
    entry['point'], entry['function'], entry['tab_count'] = sub.data[0], sub.data[1], sub.data[2]


def _set_type(entry, sub, _remap):
    """EPFT: the function parameter type."""
    entry['type'] = sub.data[0]


def _set_value(entry, sub, _remap):
    """EPFD: the float the entry multiplies by."""
    entry['value'] = struct.unpack('<f', sub.data)[0]


#: Subrecord of an open entry -> what stores it.
ENTRY_FIELDS = {'DATA': _set_data, 'PRKC': _open_tab, 'CTDA': _add_condition,
                'EPFT': _set_type, 'EPFD': _set_value}


def _entry_step(entry, sub, remap):
    """The open entry after `sub`, None once PRKF closes it; ValueError on an unknown layout."""
    if entry is None:
        if sub.type == 'PRKE' and sub.data == bytes((2, 0, 0)):
            return {'tabs': {}}
        if sub.type in ('EDID', 'DESC', 'DATA'):
            return None
        raise ValueError(f'unexpected {sub.type} {sub.data.hex()} outside a perk entry')
    if sub.type not in ENTRY_FIELDS:
        raise ValueError(f'unexpected {sub.type} {sub.data.hex()} in a perk entry')
    ENTRY_FIELDS[sub.type](entry, sub, remap)
    return entry


def parse_entries(perk, remap=int):
    """[{point, function, tab_count, tabs: {tab: [ctda]}, type, value}] of a PERK.

    ValueError on a subrecord or an order the vanilla entry layout does not have.
    """
    entries, entry = [], None
    for sub in perk.subrecords:
        if entry is not None and sub.type == 'PRKF':
            entries.append(entry)
            entry = None
            continue
        entry = _entry_step(entry, sub, remap)
    if entry is not None:
        raise ValueError('the last perk entry has no PRKF')
    return entries


def parse_ctda(data, remap=int):
    """(operator, is_or, value, function, param1) of a 32-byte CTDA run on its subject."""
    kind, value, func, p1, p2, run_on, ref, p3 = struct.unpack('<B3xfH2xIIIIi', data)
    if kind & ~(0xE0 | OR_FLAG) or (p2, run_on, ref, p3) != (0, 0, 0, -1):
        raise ValueError(f'CTDA {data.hex()} is not a plain subject condition')
    if func in FORM_FUNCTIONS:
        p1 = remap(p1)
    return kind & 0xE0, bool(kind & OR_FLAG), value, func, p1


# ---------------------------------------------------------------------------
# Evaluating an entry the way the engine combines conditions
# ---------------------------------------------------------------------------

def _reading(func, param, subject, hit):
    """What a condition function reads off `subject` ('weapon' or an actor dict)."""
    if func == GET_GLOBAL:
        return hit['globals'].get(param, 0.0)
    if func == IS_WEAPON_SKILL_TYPE and subject == 'weapon':
        return 1.0 if hit['weapon'] == param else 0.0
    if subject == 'weapon':
        raise ValueError(f'condition function {func} on a weapon tab')
    if func == GET_BASE_AV:
        return float(subject['av'][param])
    if func == GET_FACTION_RANK:
        return float(subject['ranks'].get(param, -1))
    if func == GET_IN_FACTION:
        return 1.0 if param in subject['ranks'] else 0.0
    if func == GET_IS_ID:
        return 1.0 if subject['player'] and param == PLAYER else 0.0
    raise ValueError(f'condition function {func} is not one this audit can evaluate')


def _holds(ctda, subject, hit):
    """One comparison."""
    op, _or, value, func, param = ctda
    got = _reading(func, param, subject, hit)
    if op == OP_EQ:
        return got == value
    if op == OP_GE:
        return got >= value
    if op == OP_LT:
        return got < value
    raise ValueError(f'comparison {op:#x} is not one this audit can evaluate')


def tab_passes(ctdas, subject, hit):
    """ORs bind tighter than ANDs: each run of OR-flagged conditions plus the one after is a group."""
    group_true, in_group = False, False
    for ctda in ctdas:
        group_true = (group_true if in_group else False) or _holds(ctda, subject, hit)
        in_group = ctda[1]
        if not in_group and not group_true:
            return False
    return group_true if in_group else True


def matching(entries, point, hit):
    """The entries of `point` whose every tab passes for `hit`."""
    names = TAB_SUBJECTS[point]
    out = []
    for entry in entries:
        if entry['point'] == point and all(
                tab_passes(ctdas, hit[names[tab]] if names[tab] != 'weapon' else 'weapon', hit)
                for tab, ctdas in entry['tabs'].items()):
            out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Check 1: structure and entry values
# ---------------------------------------------------------------------------

def _funcs(entry):
    """{function: [(tab, ctda)]} of an entry."""
    out = {}
    for tab, ctdas in entry['tabs'].items():
        for ctda in ctdas:
            out.setdefault(ctda[3], []).append((tab, ctda))
    return out


def _band_of(conds):
    """(lo, hi) of the >= and < conditions of one function, None for a missing bound."""
    low = [c[2] for _t, c in conds if c[0] == OP_GE]
    high = [c[2] for _t, c in conds if c[0] == OP_LT]
    if len(low) > 1 or len(high) > 1 or len(low) + len(high) != len(conds):
        raise ValueError('a band needs at most one >= and one < condition')
    return (int(low[0]) if low else 0, int(high[0]) if high else None)


def _check_shape(entry, funcs, forms):
    """ValueError unless a three-tab Multiply Value entry gated by the switch on its owner tab."""
    if entry.get('function') != MULTIPLY_VALUE or entry.get('type') != FLOAT_VALUE \
            or entry.get('tab_count') != 3 or entry.get('point') not in TAB_SUBJECTS:
        raise ValueError('not a three-tab Multiply Value entry on entry point 35 or 36')
    gates = [(t, c[0], c[2]) for t, c in funcs.get(GET_GLOBAL, []) if c[4] == forms['gate']]
    if gates != [(0, OP_EQ, 1.0)]:
        raise ValueError('no `gate global == 1` on the owner tab')


def _weapons(entry, funcs):
    """The weapon skills the entry's weapon tab tests; ValueError for none or one elsewhere."""
    tab = TAB_SUBJECTS[entry['point']].index('weapon')
    tests = funcs.get(IS_WEAPON_SKILL_TYPE, [])
    weapons = tuple(c[4] for t, c in tests if t == tab and (c[0], c[2]) == (OP_EQ, 1.0))
    if not weapons or len(weapons) != len(tests):
        raise ValueError('no weapon-skill test on the weapon tab')
    return weapons


def _audience(entry, funcs, forms):
    """Whose hits the entry scales: 'converted' (Dealt), or a Taken entry's 'outsider', 'player', 'skyrim'."""
    if entry['point'] == DEALT:
        if GET_IN_FACTION in funcs or GET_IS_ID in funcs:
            raise ValueError('a Dealt entry tests who the actors are')
        return 'converted'
    outsider = [(t, c[0], c[2], c[4]) for t, c in funcs.get(GET_IN_FACTION, [])]
    if outsider != [(1, OP_EQ, 0.0, forms['converted'])]:
        raise ValueError('a Taken entry without `attacker not converted`')
    ids = tuple((t, c[0], c[2], c[4]) for t, c in funcs.get(GET_IS_ID, []))
    known = {(): 'outsider', ((1, OP_EQ, 1.0, PLAYER),): 'player', ((1, OP_EQ, 0.0, PLAYER),): 'skyrim'}
    if ids not in known:
        raise ValueError('an attacker test this audit does not know')
    return known[ids]


def classify(entry, forms):
    """(family, kind, audience, weapon skills, key, (lo, hi)) of an entry, with structure checks.

    `kind` is 'skill', 'attribute' or 'constant'; `key` the actor value, the
    attribute name, or None. ValueError names the first broken guarantee.
    """
    funcs = _funcs(entry)
    _check_shape(entry, funcs, forms)
    weapons, audience = _weapons(entry, funcs), _audience(entry, funcs, forms)
    actor_tab = TAB_SUBJECTS[entry['point']].index('attacker')
    skills = funcs.get(GET_BASE_AV, [])
    stats = funcs.get(GET_FACTION_RANK, []) + [
        (t, c) for t, c in funcs.get(GET_GLOBAL, []) if c[4] != forms['gate']]
    if skills and (stats or {t for t, _c in skills} != {actor_tab}
                   or {c[4] for _t, c in skills} != set(weapons)):
        raise ValueError("the skill read is not the attacker's skill with the weapon")
    if skills:
        return entry['point'], 'skill', audience, weapons, weapons[0], _band_of(skills)
    if not stats:
        return entry['point'], 'constant', audience, weapons, None, (0, None)
    source = forms['factions'] if audience == 'converted' else forms['globals']
    names = {name for name, fid in source.items() if fid in {c[4] for _t, c in stats}}
    funcs_used = {c[3] for _t, c in stats}
    want = {GET_FACTION_RANK} if audience == 'converted' else {GET_GLOBAL}
    if len(names) != 1 or funcs_used != want or {t for t, _c in stats} != {0} \
            or {c[4] for _t, c in stats} != {source[next(iter(names))]}:
        raise ValueError('an attribute entry reads an unknown form or the wrong tab')
    return entry['point'], 'attribute', audience, weapons, names.pop(), _band_of(stats)


def _close(a, b):
    """Equal as the float32 the record stores."""
    return abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))


def check_values(entries, forms, settings):
    """(problems, tables found) comparing every entry with the bands recomputed here."""
    problems, found = [], {}
    for index, entry in enumerate(entries):
        try:
            family, kind, audience, weapons, key, band = classify(entry, forms)
        except (ValueError, KeyError, IndexError) as exc:
            problems.append(f'entry {index}: {exc}')
            continue
        found.setdefault((family, kind, audience, weapons, key), []).append(
            (band[0], band[1], entry['value']))
    for (family, kind, audience, weapons, key), bands in sorted(found.items(), key=repr):
        label = f'{"Taken" if family == TAKEN else "Dealt"} {kind} {audience} weapons {weapons}'
        if kind == 'constant':
            want = [(0, None, exact_attribute(settings, SKYRIM_ACTOR_ATTRIBUTE))]
        elif kind == 'attribute':
            want = expected_bands(lambda v: exact_attribute(settings, v))
            if WEAPON_ATTRIBUTE[weapons[0]] != key or {WEAPON_ATTRIBUTE[w] for w in weapons} != {key}:
                problems.append(f'{label}: reads {key}')
        else:
            who = 'player' if audience == 'player' else 'npc'
            want = expected_bands(lambda v, who=who: exact_skill(settings, v, who))
        got = sorted(bands, key=lambda b: b[0])
        if [(b[0], b[1]) for b in got] != [(b[0], b[1]) for b in want]:
            problems.append(f'{label}: bands {[(b[0], b[1]) for b in got]} != '
                            f'{[(b[0], b[1]) for b in want]}')
        else:
            problems += [f'{label} band {g[0]}-{g[1]}: value {g[2]:.6f} != {w[2]:.6f}'
                         for g, w in zip(got, want) if not _close(g[2], w[2])]
    return problems, found


# ---------------------------------------------------------------------------
# Check 2: the sweep
# ---------------------------------------------------------------------------

def _hit(forms, weapon, skill, attribute, attacker, gate=1.0, target_converted=True):
    """One weapon hit: who swings (`attacker` = 'converted', 'player' or 'skyrim'), with what, at whom."""
    ranks = {forms['factions'][n]: attribute for n in forms['factions']}
    target_ranks = {forms['factions'][n]: TARGET_RANK for n in forms['factions']}
    globals_ = {forms['gate']: gate}
    globals_.update({forms['globals'][n]: float(attribute) for n in forms['globals']})
    return {'weapon': weapon, 'globals': globals_,
            'attacker': {'player': attacker == 'player',
                         'av': {av: skill if av == weapon else (skill + OTHER_SKILL_SHIFT) % (CAP + 1)
                                for av in WEAPON_ATTRIBUTE},
                         'ranks': ranks if attacker == 'converted' else {}},
            'target': {'player': False, 'av': {}, 'ranks': target_ranks if target_converted else {}}}


def sweep(entries, forms, settings):
    """(problems, worst relative error, where) over weapon x skill x attribute x attacker."""
    problems, worst, where = [], 0.0, None
    cases = (('converted', DEALT), ('player', TAKEN), ('skyrim', TAKEN))
    for (attacker, point), weapon in itertools.product(cases, WEAPON_ATTRIBUTE):
        weapon_tab = TAB_SUBJECTS[point].index('weapon')
        own = [e for e in entries if e['point'] == point
               and tab_passes(e['tabs'].get(weapon_tab, []), 'weapon', {'weapon': weapon})]
        attributes = (SKYRIM_ACTOR_ATTRIBUTE,) if attacker == 'skyrim' else range(128)
        for skill, attribute in itertools.product(range(CAP + 1), attributes):
            hit = _hit(forms, weapon, skill, attribute, attacker)
            hits = matching(own, point, hit)
            kinds = sorted('skill' if any(c[3] == GET_BASE_AV for cs in e['tabs'].values() for c in cs)
                           else 'attribute' for e in hits)
            if kinds not in (['skill'], ['attribute', 'skill']):
                problems.append(f'{attacker} weapon {weapon} skill {skill} attribute {attribute}: '
                                f'{kinds or "no"} entries match')
                if len(problems) > 20:
                    return problems, worst, where
                continue
            got = 1.0
            for entry in hits:
                got *= entry['value']
            seen = SKYRIM_ACTOR_ATTRIBUTE if attacker == 'skyrim' else attribute
            want = (exact_skill(settings, skill, 'player' if attacker == 'player' else 'npc')
                    * exact_attribute(settings, seen))
            error = abs(got - want) / want if want > 0 else abs(got)
            if error > worst:
                worst, where = error, (attacker, weapon, skill, attribute, got, want)
    return problems, worst, where


def check_silence(entries, forms):
    """Problems where an entry applies though it must not: switch off, both converted, wrong point."""
    problems = []
    for weapon, skill in itertools.product(WEAPON_ATTRIBUTE, (0, 50, 100)):
        off = _hit(forms, weapon, skill, 50, 'converted', gate=0.0)
        both = _hit(forms, weapon, skill, 50, 'converted')
        outsiders = _hit(forms, weapon, skill, 50, 'skyrim', target_converted=False)
        if matching(entries, DEALT, off) or matching(entries, TAKEN, off):
            problems.append(f'weapon {weapon} skill {skill}: an entry applies with the switch off')
        if matching(entries, TAKEN, both):
            problems.append(f'weapon {weapon} skill {skill}: converted on converted is scaled twice')
        if not matching(entries, TAKEN, outsiders):
            problems.append(f'weapon {weapon} skill {skill}: no Taken entry for a Skyrim attacker')
    return problems


# ---------------------------------------------------------------------------
# Check 3: who lists the perk
# ---------------------------------------------------------------------------

def check_actors(npcs, perk_fid, converted_fid):
    """(problems, members, listed): stat-faction membership must equal listing the perk at rank 1."""
    problems, members, listed = [], 0, 0
    for npc in npcs:
        factions = {struct.unpack_from('<I', s.data)[0] for s in npc.subrecords if s.type == 'SNAM'}
        sigs = [s.type for s in npc.subrecords]
        perks = [struct.unpack_from('<IB', s.data) for s in npc.subrecords if s.type == 'PRKR']
        counts = [struct.unpack('<I', s.data)[0] for s in npc.subrecords if s.type == 'PRKZ']
        member, has = converted_fid in factions, (perk_fid, 1) in perks
        members += member
        listed += has
        if member != has:
            problems.append(f'{_edid(npc)} {npc.form_id:08X}: in the stat faction {member}, '
                            f'lists the perk {has}')
        if perks and (counts != [len(perks)] or not _perks_in_place(sigs)):
            problems.append(f'{_edid(npc)} {npc.form_id:08X}: PRKZ/PRKR count or position')
    return problems, members, listed


def _perks_in_place(sigs):
    """PRKZ then its PRKRs in one run, after every SPLO and before COCT."""
    first = sigs.index('PRKZ') if 'PRKZ' in sigs else -1
    run = [i for i, s in enumerate(sigs) if s == 'PRKR']
    if first < 0 or run != list(range(first + 1, first + 1 + len(run))):
        return False
    before = [i for i, s in enumerate(sigs) if s in ('SPCT', 'SPLO', 'RNAM')]
    after = [i for i, s in enumerate(sigs) if s in ('COCT', 'CNTO', 'AIDT')]
    return all(i < first for i in before) and all(i > run[-1] for i in after)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _form(by_edid, sig, edid):
    """The plugin-space FormID of a record by EditorID; ValueError when no file read has it."""
    if (sig, edid) not in by_edid:
        raise ValueError(f'{sig} {edid} is in neither the plugin nor a --master')
    rec, remap = by_edid[(sig, edid)]
    return remap(rec.form_id)


def find_forms(by_edid):
    """The FormIDs the conditions and actors name, by EditorID; ValueError naming one missing."""
    return {'perk': _form(by_edid, 'PERK', PERK_EDID),
            'gate': _form(by_edid, 'GLOB', GATE_EDID),
            'converted': _form(by_edid, 'FACT', CONVERTED_EDID),
            'factions': {n: _form(by_edid, 'FACT', e) for n, e in ATTRIBUTE_FACTIONS.items()},
            'globals': {n: _form(by_edid, 'GLOB', e) for n, e in PLAYER_GLOBALS.items()}}


def run(plugin, export_dir, masters=(), master_exports=()):
    """Run the three checks, print the report, and return the number of problems (-1: undetermined)."""
    own, by_edid = load_built(plugin, masters)
    try:
        forms = find_forms(by_edid)
        perk, remap = by_edid[('PERK', PERK_EDID)]
        entries = parse_entries(perk, remap)
    except ValueError as exc:
        print(f'UNDETERMINED: {exc}')
        return -1
    settings, authored = export_settings(list(master_exports) + [export_dir])
    print(f'{os.path.basename(plugin)}: perk {forms["perk"]:08X}, {len(entries)} entries, '
          f'{sum(len(s.data) + 6 for s in perk.subrecords)} bytes of subrecords')
    print('  settings: ' + ', '.join(f'{k}={v:g}{"" if k in authored else " (exe)"}'
                                     for k, v in settings.items()))
    problems, found = check_values(entries, forms, settings)
    print(f'  tables: {len(found)}, entry values compared: {sum(len(b) for b in found.values())}')
    sweep_problems, worst, where = sweep(entries, forms, settings)
    problems += sweep_problems + check_silence(entries, forms)
    limit = (1 + BUDGET) ** 2 - 1
    print(f'  sweep: worst error {100 * worst:.3f}% (limit {100 * limit:.2f}%) at '
          f'(attacker, weapon skill, skill, attribute, built, exact) = {where}')
    if worst > limit:
        problems.append(f'worst sweep error {worst:.4f} over {limit:.4f}')
    actor_problems, members, listed = check_actors(own.get('NPC_', []), forms['perk'],
                                                   forms['converted'])
    print(f'  actors: {len(own.get("NPC_", []))}, in the stat faction {members}, listing the perk {listed}')
    problems += actor_problems
    for line in problems[:40]:
        print(f'  MISMATCH: {line}')
    if len(problems) > 40:
        print(f'  ... {len(problems) - 40} more')
    print(f'  {"OK" if not problems else str(len(problems)) + " problem(s)"}')
    return len(problems)


def main():
    """Parse the command line and run; 0 clean, 1 on a mismatch, 2 when undetermined."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('plugin', help='built TES5 plugin')
    ap.add_argument('export_dir', help="the plugin's TES4 export dir (GMST.txt)")
    ap.add_argument('--master', action='append', default=[],
                    help='a built master the plugin adopts the perk or its forms from')
    ap.add_argument('--master-export', action='append', default=[],
                    help="a master's TES4 export dir, farthest master first")
    args = ap.parse_args()
    found = run(args.plugin, args.export_dir, args.master, args.master_export)
    return 2 if found < 0 else (1 if found else 0)


if __name__ == '__main__':
    sys.exit(main())
