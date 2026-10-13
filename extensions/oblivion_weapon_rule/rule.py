"""Oblivion's weapon-hit rule on hits a converted actor deals or takes.

A converted weapon keeps Oblivion's damage number and a converted actor
Oblivion's health, but Skyrim turns that number into a far bigger hit. One
hidden perk on every converted NPC_ and creature multiplies a weapon hit by
Oblivion's factor over Skyrim's, in bands derived from the source game's own
settings: its Dealt entries scale what the owner deals, its Taken entries what
an unconverted attacker deals to the owner. Skyrim's records and settings are
not touched and the player holds nothing.

See: extensions/oblivion_weapon_rule/README.md
"""

import struct

from tes5_import.base.writer import pack_record, pack_string_subrecord, pack_subrecord

#: EditorID of the perk; a dependent adopts its master's by this name.
WEAPON_RULE_PERK = 'TES4OblivionWeaponRulePerk'

#: EditorID of the in-game switch, a short global at 1: every entry applies only while it is 1.
WEAPON_RULE_GLOBAL = 'TES4OblivionWeaponRule'

#: TES4 attribute index -> the conversion's hidden faction whose rank is a converted actor's attribute.
STAT_FACTIONS = {0: 'TES4AttributeStrength', 3: 'TES4AttributeAgility'}

#: TES4 attribute index -> the conversion's global holding the player's attribute.
PLAYER_GLOBALS = {0: 'TES4PlayerStrength', 3: 'TES4PlayerAgility'}

#: The faction every converted TES4 actor is in: the first stat faction.
CONVERTED_FACTION = STAT_FACTIONS[0]

#: Oblivion.exe's compiled-in value of each setting the weapon-hit formula reads (UESP, "The Complete Damage Formula").
OBLIVION_HIT_SETTINGS = {
    'fDamageWeaponMult': 1.0, 'fDamageWeaponConditionBase': 0.0,
    'fDamageWeaponConditionMult': 1.0, 'fDamageSkillBase': 0.2, 'fDamageSkillMult': 1.8,
    'fDamageStrengthBase': 0.5, 'fDamageStrengthMult': 1.0, 'fFatigueBase': 1.25,
}

#: Skyrim's skill-factor settings: Min + (Max - Min) x skill/100. UNVERIFIED engine defaults.
SKYRIM_SKILL_SETTINGS = {'fDamageSkillMin': 1.0, 'fDamageSkillMax': 1.5,
                         'fDamagePCSkillMin': 1.0, 'fDamagePCSkillMax': 1.5}

#: The most a band's value may differ from the exact factor anywhere inside it.
BAND_ERROR_BUDGET = 0.05

#: Oblivion caps skill and attribute at this in the damage formula.
STAT_CAP = 100

#: The attribute an unconverted, non-player attacker is taken to have.
SKYRIM_ACTOR_ATTRIBUTE = 50

#: Skyrim actor values OneHanded, TwoHanded and Marksman: what IsWeaponSkillType tests.
ONE_HANDED, TWO_HANDED, MARKSMAN = 6, 7, 8
WEAPON_SKILLS = (ONE_HANDED, TWO_HANDED, MARKSMAN)

#: TES4 attribute index -> the weapon skills whose hits it scales: Strength melee, Agility bows.
ATTRIBUTE_WEAPONS = {0: (ONE_HANDED, TWO_HANDED), 3: (MARKSMAN,)}

#: PERK entry points Mod Attack Damage and Mod Incoming Damage: function 3 Multiply Value, 3 tabs.
DEALT_ENTRY, TAKEN_ENTRY = bytes((35, 3, 3)), bytes((36, 3, 3))

#: Condition tabs: the perk owner on both; then Dealt's weapon, Taken's attacker and its weapon.
TAB_OWNER, TAB_DEALT_WEAPON, TAB_ATTACKER, TAB_ATTACKER_WEAPON = 0, 1, 1, 2

#: CTDA functions GetInFaction, GetIsID, GetFactionRank, GetGlobalValue, IsWeaponSkillType, GetBaseActorValue.
FUNC_GET_IN_FACTION, FUNC_GET_IS_ID, FUNC_GET_FACTION_RANK, FUNC_GET_GLOBAL_VALUE = 71, 72, 73, 74
FUNC_IS_WEAPON_SKILL_TYPE, FUNC_GET_BASE_ACTOR_VALUE = 109, 277

#: CTDA comparisons not below and below, and the OR flag of the type byte.
_OP_GE, _OP_LT, _CTDA_OR = 0x60, 0x80, 0x01

#: The player's base actor, which GetIsID names.
_PLAYER = 0x00000007

#: Run state: the perk's FormID, 0 while this plugin has no rule.
_STATE = {'perk': 0}


# ---------------------------------------------------------------------------
# The exact factors
# ---------------------------------------------------------------------------

def _capped(value: float) -> float:
    """`value` within Oblivion's 0-100."""
    return min(float(STAT_CAP), max(0.0, value))


def weapon_hit_constant(s: dict) -> float:
    """A hit's weapon, condition and fatigue factors, the last two at full."""
    return (s['fDamageWeaponMult']
            * (s['fDamageWeaponConditionBase'] + s['fDamageWeaponConditionMult'])
            * s['fFatigueBase'])


def skill_term(s: dict, skill: float) -> float:
    """The weapon-skill factor of a hit, for a skill already capped."""
    return s['fDamageSkillBase'] + s['fDamageSkillMult'] * skill / 100.0


def attribute_term(s: dict, attribute: float) -> float:
    """The Strength (a bow: Agility) factor of a hit, for a capped attribute."""
    return s['fDamageStrengthBase'] + s['fDamageStrengthMult'] * attribute / 100.0


def skill_factor(settings: dict, skill: float, skyrim_min: float, skyrim_max: float) -> float:
    """Oblivion's hit at `skill` and attribute factor 1 over Skyrim's skill factor."""
    skyrim = skyrim_min + (skyrim_max - skyrim_min) * skill / 100.0
    oblivion = weapon_hit_constant(settings) * skill_term(settings, _capped(skill))
    return max(0.0, oblivion / skyrim) if skyrim > 0 else 0.0


def attribute_factor(settings: dict, attribute: float) -> float:
    """Oblivion's Strength or Agility factor of a hit."""
    return max(0.0, attribute_term(settings, _capped(attribute)))


# ---------------------------------------------------------------------------
# Bands
# ---------------------------------------------------------------------------

def _band_value(low: float, high: float) -> float:
    """The harmonic mean of a band's two end factors: equally far, in ratio, from both."""
    return 2.0 * low * high / (low + high) if low + high > 0 else 0.0


def _band_error(low: float, high: float) -> float:
    """How far, in ratio, the band's value is from either end factor."""
    return abs(high - low) / (high + low) if high + low > 0 else 0.0


def derive_bands(factor, budget: float = BAND_ERROR_BUDGET) -> list:
    """[(lo, hi, value)] over 0-100: `lo <= stat < hi`, None for no bound on that side.

    A band grows from its low end while its two end factors stay within
    `budget` of their harmonic mean, which is its value. The first band has
    no floor; one more band above the cap holds the factor at the cap.
    See: extensions/oblivion_weapon_rule/README.md
    """
    bands, low = [], 0
    while low <= STAT_CAP:
        high = low
        while high < STAT_CAP and _band_error(factor(low), factor(high + 1)) <= budget:
            high += 1
        bands.append((low or None, high + 1, _band_value(factor(low), factor(high))))
        low = high + 1
    return bands + [(STAT_CAP + 1, None, factor(STAT_CAP))]


def rule_tables(settings: dict, skyrim: dict = None) -> dict:
    """The band tables: 'skill' against an NPC's Skyrim factor, 'skill_pc' the player's, 'attribute'."""
    skyrim = skyrim or SKYRIM_SKILL_SETTINGS
    return {
        'skill': derive_bands(lambda v: skill_factor(
            settings, v, skyrim['fDamageSkillMin'], skyrim['fDamageSkillMax'])),
        'skill_pc': derive_bands(lambda v: skill_factor(
            settings, v, skyrim['fDamagePCSkillMin'], skyrim['fDamagePCSkillMax'])),
        'attribute': derive_bands(lambda v: attribute_factor(settings, v)),
    }


# ---------------------------------------------------------------------------
# Conditions
# ---------------------------------------------------------------------------

def _test(func: int, param: int, value: float = 1.0, operator: int = 0, is_or: bool = False) -> bytes:
    """One packed 32-byte CTDA run on its subject: `func(param) <operator> value`."""
    return pack_subrecord('CTDA', struct.pack(
        '<B3xfH2xIIIIi', operator | (_CTDA_OR if is_or else 0), value, func, param, 0, 0, 0, -1))


def _in_band(func: int, param: int, band: tuple) -> bytes:
    """`func(param)` inside the band's bounds."""
    low, high, _value = band
    out = _test(func, param, float(low), _OP_GE) if low is not None else b''
    return out + (_test(func, param, float(high), _OP_LT) if high is not None else b'')


def _weapon_is(skills: tuple) -> bytes:
    """The weapon swings with one of `skills`."""
    return b''.join(_test(FUNC_IS_WEAPON_SKILL_TYPE, skill, is_or=skill != skills[-1])
                    for skill in skills)


def _is_player(expected: bool) -> bytes:
    """The actor is, or is not, the player."""
    return _test(FUNC_GET_IS_ID, _PLAYER, 1.0 if expected else 0.0)


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

def multiply_entry(entry_point: bytes, tabs: dict, value: float) -> bytes:
    """One rank-0 PERK entry multiplying its entry point's value by `value`.

    `tabs` is {tab index: packed CTDAs}; every tab has to pass.
    """
    subs = pack_subrecord('PRKE', bytes((2, 0, 0)))
    subs += pack_subrecord('DATA', entry_point)
    for tab, ctdas in sorted(tabs.items()):
        if ctdas:
            subs += pack_subrecord('PRKC', bytes((tab,))) + ctdas
    subs += pack_subrecord('EPFT', bytes((1,)))
    subs += pack_subrecord('EPFD', struct.pack('<f', value))
    return subs + pack_subrecord('PRKF', b'')


def _dealt_entries(forms: dict, tables: dict) -> list:
    """What the owner deals: its skill band per weapon skill, then its attribute band."""
    gate = _test(FUNC_GET_GLOBAL_VALUE, forms['gate'])
    out = [multiply_entry(DEALT_ENTRY, {
        TAB_OWNER: gate + _in_band(FUNC_GET_BASE_ACTOR_VALUE, skill, band),
        TAB_DEALT_WEAPON: _weapon_is((skill,))}, band[2])
        for skill in WEAPON_SKILLS for band in tables['skill']]
    out += [multiply_entry(DEALT_ENTRY, {
        TAB_OWNER: gate + _in_band(FUNC_GET_FACTION_RANK, forms['factions'][attribute], band),
        TAB_DEALT_WEAPON: _weapon_is(skills)}, band[2])
        for attribute, skills in ATTRIBUTE_WEAPONS.items() for band in tables['attribute']]
    return out


def _attacker_audiences(tables: dict) -> list:
    """[(attacker test, skill table)]: the player apart only when its table differs."""
    if tables['skill_pc'] == tables['skill']:
        return [(b'', tables['skill'])]
    return [(_is_player(True), tables['skill_pc']), (_is_player(False), tables['skill'])]


def _taken_skill_entries(forms: dict, tables: dict) -> list:
    """What an unconverted attacker deals to the owner, by the attacker's skill band."""
    gate = _test(FUNC_GET_GLOBAL_VALUE, forms['gate'])
    outsider = _test(FUNC_GET_IN_FACTION, forms['converted'], 0.0)
    return [multiply_entry(TAKEN_ENTRY, {
        TAB_OWNER: gate,
        TAB_ATTACKER: outsider + who + _in_band(FUNC_GET_BASE_ACTOR_VALUE, skill, band),
        TAB_ATTACKER_WEAPON: _weapon_is((skill,))}, band[2])
        for who, table in _attacker_audiences(tables)
        for skill in WEAPON_SKILLS for band in table]


def _taken_attribute_entries(forms: dict, tables: dict, settings: dict) -> list:
    """The attacker's attribute: the player's from its global, any other outsider's as a constant."""
    gate = _test(FUNC_GET_GLOBAL_VALUE, forms['gate'])
    outsider = _test(FUNC_GET_IN_FACTION, forms['converted'], 0.0)
    out = [multiply_entry(TAKEN_ENTRY, {
        TAB_OWNER: gate + _in_band(FUNC_GET_GLOBAL_VALUE, forms['globals'][attribute], band),
        TAB_ATTACKER: outsider + _is_player(True),
        TAB_ATTACKER_WEAPON: _weapon_is(skills)}, band[2])
        for attribute, skills in ATTRIBUTE_WEAPONS.items() if forms['globals'].get(attribute)
        for band in tables['attribute']]
    constant = attribute_factor(settings, SKYRIM_ACTOR_ATTRIBUTE)
    if struct.pack('<f', constant) != struct.pack('<f', 1.0):
        out += [multiply_entry(TAKEN_ENTRY, {
            TAB_OWNER: gate, TAB_ATTACKER: outsider + _is_player(False),
            TAB_ATTACKER_WEAPON: _weapon_is(skills)}, constant)
            for skills in ATTRIBUTE_WEAPONS.values()]
    return out


def rule_entries(forms: dict, settings: dict, skyrim: dict = None) -> list:
    """Every packed entry of the perk, Dealt then Taken, over the FormIDs in `forms`.

    `forms` holds 'gate' the switch global, 'converted' the faction every
    converted actor is in, and 'factions' and 'globals': the stat faction and
    the player's global per TES4 attribute index.
    See: extensions/oblivion_weapon_rule/README.md
    """
    tables = rule_tables(settings, skyrim)
    entries = _dealt_entries(forms, tables)
    entries += _taken_skill_entries(forms, tables)
    entries += _taken_attribute_entries(forms, tables, settings)
    return entries


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

def hidden_perk(fid: int, edid: str, entries: bytes) -> bytes:
    """Hidden, unplayable one-rank PERK carrying the packed `entries`."""
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_string_subrecord('DESC', '')
    subs += pack_subrecord('DATA', bytes((0, 0, 1, 0, 1)))
    return pack_record('PERK', fid, 0, subs + entries)


def switch_global(fid: int) -> bytes:
    """The in-game switch: a short GLOB at 1."""
    subs = pack_string_subrecord('EDID', WEAPON_RULE_GLOBAL)
    subs += pack_subrecord('FNAM', struct.pack('<B', ord('s')))
    subs += pack_subrecord('FLTV', struct.pack('<f', 1.0))
    return pack_record('GLOB', fid, 0, subs)


def _forms(context, gate: int) -> dict:
    """The FormIDs the entries name: the switch, and the conversion's factions and globals."""
    return {
        'gate': gate,
        'converted': context.form(CONVERTED_FACTION),
        'factions': {attribute: context.form(edid) for attribute, edid in STAT_FACTIONS.items()},
        'globals': {attribute: context.form(edid) for attribute, edid in PLAYER_GLOBALS.items()},
    }


def reset() -> None:
    """Forget the last plugin's perk."""
    _STATE['perk'] = 0


def create_records(writer, context) -> dict:
    """{EditorID: FormID} of the switch global and, for a TES4 source with stat factions, the perk.

    A master's global and perk are adopted, and the perk is rewritten as an
    override of the same FormID only when this plugin authors one of the
    settings it is built from.
    See: extensions/oblivion_weapon_rule/README.md
    """
    reset()
    gate, _written = context.adopt_or_write(writer, 'GLOB', WEAPON_RULE_GLOBAL, switch_global)
    forms = _forms(context, gate)
    if not (context.source_is_tes4 and all(forms['factions'].values())):
        return {WEAPON_RULE_GLOBAL: gate}
    settings, own_settings = context.game_settings(OBLIVION_HIT_SETTINGS)
    fid = context.master_formid('PERK', WEAPON_RULE_PERK)
    if not fid or own_settings:
        fid = fid or writer.derive_formid('PERK', WEAPON_RULE_PERK)
        entries = rule_entries(forms, settings)
        writer.add_record('PERK', hidden_perk(fid, WEAPON_RULE_PERK, b''.join(entries)))
        print(f"  Oblivion weapon rule: perk {fid:08X}, {len(entries)} entries")
    _STATE['perk'] = fid
    return {WEAPON_RULE_GLOBAL: gate, WEAPON_RULE_PERK: fid}


def actor_subrecords(_kind: str, _rec: dict) -> bytes:
    """PRKZ + PRKR listing the perk at rank 1 on a converted actor; b'' while the plugin has no rule."""
    if not _STATE['perk']:
        return b''
    return (pack_subrecord('PRKZ', struct.pack('<I', 1))
            + pack_subrecord('PRKR', struct.pack('<IB3x', _STATE['perk'], 1)))
