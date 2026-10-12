"""The birthsign menu's spell list, written as OpenMW's BirthDialog writes it.

A sign's spells go under Abilities, Powers and Spells, each spell's name then
one line per effect beside the effect's icon: "Fortify Personality 25 pts",
"Restore Health 2 pts for 30 secs on Self". A Morrowind sign's spells are read
from the TES3 binaries with the game's own settings, since the export keeps
one magnitude where TES3 has a range; a TES4 sign's from its export, in
Morrowind's words.

See: docs/commentary/morrowind_runtime.md#chargen-menus
"""

import re
import struct

from tes4_export.morrowind_mgef_names import MW_EFFECT_NAMES, MW_HARDCODED_FLAGS
from tes4_export.tes3_reader import get_all_subrecords, get_string, get_subrecord, read_file

#: A line's kind: a category's header, a spell's name, one of its effects.
HEADER, SPELL, EFFECT = 'h', 's', 'e'

#: The words OpenMW writes, as Morrowind's settings hold them; a TES3 game's own replace them.
WORDS = {'sbirthsignmenu1': 'Abilities:', 'spowers': 'Powers', 'sbirthsignmenu2': 'Spells:',
         'spoint': 'pt', 'spoints': 'pts', 'spercent': '%', 'sfeet': 'ft', 'slevel': 'Level',
         'slevels': 'Levels', 'sto': 'to', 'ssecond': 'sec', 'sseconds': 'secs', 'sfor': 'for',
         'sin': 'in', 'sfootarea': 'ft', 'sxtimesint': 'x INT', 'sonword': 'on',
         'srangeself': 'Self', 'srangetouch': 'Touch', 'srangetarget': 'Target'}

#: The three categories in OpenMW's order, by the setting that heads each.
CATEGORIES = ('sbirthsignmenu1', 'spowers', 'sbirthsignmenu2')
ABILITIES, POWERS, SPELLS = range(3)

#: A range's word, by TES3 ENAM range (TES4's `Effect[i].Type` names the same three).
RANGES = ('srangeself', 'srangetouch', 'srangetarget')
TES4_RANGES = {'Self': 0, 'Touch': 1, 'Target': 2}

#: How a magnitude is shown (ESM::MagicEffect::MagnitudeDisplayType).
NONE, POINTS, PERCENT, FEET, LEVEL, TIMES_INT = range(6)

#: OpenMW's MagicEffect flags: TargetSkill, TargetAttribute, NoDuration, NoMagnitude, AppliedOnce.
MW_TARGET_SKILL, MW_TARGET_ATTRIBUTE, MW_NO_DURATION, MW_NO_MAGNITUDE = 0x1, 0x2, 0x4, 0x8
MW_APPLIED_ONCE = 0x1000

#: TES3 spell type -> category: spell, ability, power (blight, disease and curse are not listed).
TES3_CATEGORIES = {0: SPELLS, 1: ABILITIES, 5: POWERS}

#: TES4 SPIT.Type -> category: spell, power, lesser power, ability.
TES4_CATEGORIES = {0: SPELLS, 2: POWERS, 3: POWERS, 4: ABILITIES}

#: TES4 MGEF flags: magnitude in percent, no duration, no magnitude, uses a skill, an attribute.
OB_PERCENT, OB_NO_DURATION, OB_NO_MAGNITUDE = 0x8, 0x80, 0x100
OB_USE_SKILL, OB_USE_ATTRIBUTE = 0x80000, 0x100000

#: The eight attributes, the setting names Morrowind reads them by and TES4's own names.
ATTRIBUTES = ('Strength', 'Intelligence', 'Willpower', 'Agility', 'Speed', 'Endurance',
              'Personality', 'Luck')

#: TES3's 27 skills as OpenMW names their settings (`sSkill<name>`).
MW_SKILLS = ('Block', 'Armorer', 'Mediumarmor', 'Heavyarmor', 'Bluntweapon', 'Longblade', 'Axe',
             'Spear', 'Athletics', 'Enchant', 'Destruction', 'Alteration', 'Illusion',
             'Conjuration', 'Mysticism', 'Restoration', 'Alchemy', 'Unarmored', 'Security',
             'Sneak', 'Acrobatics', 'Lightarmor', 'Shortblade', 'Marksman', 'Mercantile',
             'Speechcraft', 'Handtohand')

#: TES4's skills by actor value from 12, as Oblivion writes them.
TES4_SKILLS = ('Armorer', 'Athletics', 'Blade', 'Block', 'Blunt', 'Hand to Hand',
               'Heavy Armor', 'Alchemy', 'Alteration', 'Conjuration', 'Destruction',
               'Illusion', 'Mysticism', 'Restoration', 'Acrobatics', 'Light Armor',
               'Marksman', 'Mercantile', 'Security', 'Sneak', 'Speechcraft')
TES4_FIRST_SKILL = 12

#: The verbs OpenMW writes before an attribute or skill instead of the effect's own name.
_VERBS = ('Absorb', 'Damage', 'Drain', 'Fortify', 'Restore')

#: One TES3 ENAM: index, skill, attribute, range, area, duration, min, max.
_ENAM = '<hbbiiiii'

#: Where each game keeps its effect icons in an install.
MW_ICONS, OB_ICONS = 'icons', 'textures\\menus\\icons'


# ---------------------------------------------------------------------------
# The lines
# ---------------------------------------------------------------------------

def icon_key(path: str) -> str:
    """An icon path as the movie names its sprites: `Icon<row>_<key>`."""
    return re.sub(r'[^a-z0-9]+', '_', path.lower().rsplit('.', 1)[0]).strip('_')


def _magnitude(effect: dict, words: dict) -> str:
    """MWSpellEffect's magnitude part: ` 25 pts`, ` 50%`, ` 1.5x INT`, or ''."""
    low, high, display = effect['low'], effect['high'], effect['display']
    if not (low or high) or display == NONE:
        return ''
    to = f" {words['sto']} "
    if display == TIMES_INT:
        span = f' {low / 10:.1f}' + (f'{to}{high / 10:.1f}' if low != high else '')
        return span + words['sxtimesint']
    span = f' {low}' + (f'{to}{high}' if low != high else '')
    if display == PERCENT:
        return span + words['spercent']
    one = low == high and abs(low) == 1
    units = {FEET: words['sfeet'], LEVEL: words['slevel'] if one else words['slevels']}
    return f"{span} {units.get(display, words['spoint'] if one else words['spoints'])}"


def _duration(effect: dict, words: dict) -> str:
    """The duration, area and range part, which a constant effect has none of."""
    seconds = effect['duration'] if effect['applied_once'] else max(1, effect['duration'])
    text = ''
    if seconds > 0 and not effect['no_duration']:
        unit = words['ssecond'] if seconds == 1 else words['sseconds']
        text += f" {words['sfor']} {seconds} {unit}"
    if effect['area'] > 0:
        text += f" {words['sin']} {effect['area']} {words['sfootarea']}"
    return text + f" {words['sonword']} {words[RANGES[effect['range']]]}"


def effect_text(effect: dict, words: dict, constant: bool) -> str:
    """One effect's line, as MWSpellEffect::updateWidgets writes it."""
    text = effect['name'] + _magnitude(effect, words)
    return text if constant else text + _duration(effect, words)


def sign_lines(spells: list, words: dict) -> list:
    """`[(kind, icon path, text)]` for a sign's spells (`{name, category, effects}`):
    each category's header, its spells' names, each effect under its spell."""
    lines = []
    for category, label in enumerate(CATEGORIES):
        group = [spell for spell in spells if spell['category'] == category]
        if group:
            lines.append((HEADER, '', words[label]))
        for spell in group:
            lines.append((SPELL, '', spell['name']))
            lines += [(EFFECT, effect['icon'], effect_text(effect, words, category == ABILITIES))
                      for effect in spell['effects']]
    return lines


# ---------------------------------------------------------------------------
# A Morrowind sign's spells, from the TES3 binaries
# ---------------------------------------------------------------------------

def tes3_tables(chain: list) -> dict:
    """The chain's string settings, effect icons and spells, a later plugin winning:
    `{'gmsts': {lower name: text}, 'icons': {index: path}, 'spells': {lower id: record}}`."""
    tables = {'gmsts': {}, 'icons': {}, 'spells': {}}
    for _name, path in chain:
        for rec in read_file(path)[1]:
            if not rec.deleted:
                _take_record(tables, rec)
    return tables


def _take_record(tables: dict, rec) -> None:
    """A GMST's text, an MGEF's icon or a SPEL into `tables`; anything else is skipped."""
    if rec.type == 'GMST':
        value = get_subrecord(rec, 'STRV')
        if value is not None:
            tables['gmsts'][rec.record_id.lower()] = get_string(value)
    elif rec.type == 'MGEF':
        _take_icon(tables['icons'], rec)
    elif rec.type == 'SPEL':
        tables['spells'][rec.record_id.lower()] = rec


def _take_icon(icons: dict, rec) -> None:
    """An MGEF's icon under its effect index, as OpenMW finds it: `icons\\`, a .dds."""
    index, icon = get_subrecord(rec, 'INDX'), get_subrecord(rec, 'ITEX')
    if index is not None and len(index.data) >= 4 and icon is not None:
        path = get_string(icon).rsplit('.', 1)[0] + '.dds'
        icons[struct.unpack_from('<i', index.data)[0]] = f'{MW_ICONS}\\{path}'


def _mw_display(index: int, flags: int) -> int:
    """ESM::MagicEffect::getMagnitudeDisplayType."""
    if flags & MW_NO_MAGNITUDE:
        return NONE
    if index == 84:
        return TIMES_INT
    if index == 59 or 64 <= index <= 66:
        return FEET
    if index in (118, 119):
        return LEVEL
    if 28 <= index <= 36 or 90 <= index <= 99 or index in (40, 47, 57, 68):
        return PERCENT
    return POINTS


def _mw_target(flags: int, skill: int, attribute: int, gmsts: dict) -> str:
    """The skill or attribute an effect names, by the game's setting; '' for none."""
    if flags & MW_TARGET_SKILL and 0 <= skill < len(MW_SKILLS):
        return gmsts.get(f'sskill{MW_SKILLS[skill]}'.lower(), MW_SKILLS[skill])
    if flags & MW_TARGET_ATTRIBUTE and 0 <= attribute < len(ATTRIBUTES):
        return gmsts.get(f'sattribute{ATTRIBUTES[attribute]}'.lower(), ATTRIBUTES[attribute])
    return ''


def mw_effect_name(index: int, skill: int, attribute: int, gmsts: dict) -> str:
    """MWMechanics::getMagicEffectString: `Fortify Strength`, `Water Breathing`."""
    name = MW_EFFECT_NAMES[index]
    target = _mw_target(MW_HARDCODED_FLAGS[index], skill, attribute, gmsts)
    verb = next((v for v in _VERBS if target and name in (v + 'Attribute', v + 'Skill')), '')
    text = gmsts.get(f's{verb}'.lower(), verb) if verb else gmsts.get(f'seffect{name}'.lower(), name)
    return f'{text} {target}' if target else text


def _mw_effect(entry: tuple, tables: dict) -> dict:
    """One ENAM (a known effect index) as the effect `effect_text` writes."""
    index, skill, attribute, rng, area, duration, low, high = entry
    flags = MW_HARDCODED_FLAGS[index]
    return {'name': mw_effect_name(index, skill, attribute, tables['gmsts']),
            'icon': tables['icons'].get(index, ''), 'low': low, 'high': high,
            'duration': duration, 'area': area, 'range': rng if 0 <= rng < len(RANGES) else 0,
            'display': _mw_display(index, flags), 'applied_once': bool(flags & MW_APPLIED_ONCE),
            'no_duration': bool(flags & MW_NO_DURATION)}


def tes3_spell(spell_id: str, tables: dict):
    """A TES3 spell as `{name, category, effects}`, or None when the chain lacks it."""
    rec = tables['spells'].get(spell_id.lower())
    spdt = get_subrecord(rec, 'SPDT') if rec else None
    if spdt is None or len(spdt.data) < 4:
        return None
    name = get_subrecord(rec, 'FNAM')
    entries = [struct.unpack(_ENAM, sub.data) for sub in get_all_subrecords(rec, 'ENAM')
               if len(sub.data) == struct.calcsize(_ENAM)]
    return {'name': get_string(name) if name is not None else spell_id,
            'category': TES3_CATEGORIES.get(struct.unpack_from('<i', spdt.data)[0]),
            'effects': [_mw_effect(entry, tables) for entry in entries
                        if 0 <= entry[0] < len(MW_EFFECT_NAMES)]}


def tes3_words(tables: dict) -> dict:
    """WORDS as the TES3 game's own settings hold them."""
    return {key: tables['gmsts'].get(key, text) for key, text in WORDS.items()}


# ---------------------------------------------------------------------------
# A TES4 sign's spells, from the export
# ---------------------------------------------------------------------------

def _int(rec: dict, key: str, default: int = 0) -> int:
    """An export field as an int, `default` when absent or not a number."""
    try:
        return int(rec.get(key, default))
    except (TypeError, ValueError):
        return default


def _ob_name(mgef: dict, av: int) -> str:
    """An effect's name, its last word (Attribute, Skill) the attribute or skill it names."""
    name = mgef.get('FULL') or mgef.get('EditorID', '?')
    flags = _int(mgef, 'DATA.Flags')
    target = ''
    if flags & OB_USE_ATTRIBUTE and 0 <= av < len(ATTRIBUTES):
        target = ATTRIBUTES[av]
    elif flags & OB_USE_SKILL and 0 <= av - TES4_FIRST_SKILL < len(TES4_SKILLS):
        target = TES4_SKILLS[av - TES4_FIRST_SKILL]
    head, _sep, last = name.rpartition(' ')
    return f'{head} {target}' if target and head and last in ('Attribute', 'Skill') else name


def _ob_effect(spell: dict, i: int, mgefs: dict) -> dict:
    """The spell's `i`th effect as the effect `effect_text` writes."""
    mgef = mgefs.get(spell.get(f'Effect[{i}].EFID', ''), {})
    flags = _int(mgef, 'DATA.Flags')
    magnitude = _int(spell, f'Effect[{i}].Magnitude')
    icon = mgef.get('ICON', '').replace('\\\\', '\\')
    display = NONE if flags & OB_NO_MAGNITUDE else PERCENT if flags & OB_PERCENT else POINTS
    return {'name': _ob_name(mgef, _int(spell, f'Effect[{i}].ActorValue', -1)),
            'icon': f'{OB_ICONS}\\{icon}' if icon else '', 'low': magnitude, 'high': magnitude,
            'duration': _int(spell, f'Effect[{i}].Duration'),
            'area': _int(spell, f'Effect[{i}].Area'),
            'range': TES4_RANGES.get(spell.get(f'Effect[{i}].Type', 'Self'), 0),
            'display': display, 'applied_once': False,
            'no_duration': bool(flags & OB_NO_DURATION)}


def tes4_spell(spell: dict, mgefs: dict) -> dict:
    """An exported TES4 spell as `{name, category, effects}`; `mgefs` by EditorID."""
    count = _int(spell, 'EffectCount')
    return {'name': spell.get('FULL') or spell.get('EditorID', ''),
            'category': TES4_CATEGORIES.get(_int(spell, 'SPIT.Type')),
            'effects': [_ob_effect(spell, i, mgefs) for i in range(count)]}
