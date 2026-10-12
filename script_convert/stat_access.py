"""TES4 attributes and kept skills in converted scripts.

The player's attributes live in the TES4Player<Attribute> globals that
MorrowindRuntime keeps. Every other actor's attributes and kept skills are its
rank in a stat faction (`tes5_import.actors.stat_factions`). `TES4_Attributes`
decides at run time, since a reference variable can hold the player.

See: docs/commentary/morrowind_runtime.md#npc-attributes
"""

from script_convert.command_rows import ACTOR_VALUE_READ_FUNCTIONS
from tes5_import.actors.stat_factions import KEPT_SKILLS, STAT_FACTIONS
from tes5_import.base.owned_records import PLAYER_ATTRIBUTE_GLOBALS, TES4_ATTRIBUTE_NAMES
from tes5_import.dialogue.say_topics import PLAYER_TOKENS

#: TES4 attribute (lowercase) -> the global MorrowindRuntime keeps the player's in.
_PLAYER_ATTRIBUTE_GLOBALS = {name.lower(): edid for name, edid
                             in zip(TES4_ATTRIBUTE_NAMES, PLAYER_ATTRIBUTE_GLOBALS)}

#: TES4 attribute or kept skill (lowercase) -> its stat faction's EditorID.
_STAT_FACTIONS = {name.lower(): STAT_FACTIONS[av] for av, name
                  in (*enumerate(TES4_ATTRIBUTE_NAMES), *KEPT_SKILLS.items())}

#: The kept skills, lowercase.
KEPT_SKILL_NAMES = frozenset(name.lower() for name in KEPT_SKILLS.values())


def actor_subject(ref: str, extends: str) -> str:
    """The subject as an Actor expression: `ref`, `Self`, or `(Self as Actor)`."""
    if ref != 'Self' or extends == 'Actor':
        return ref
    return '(Self as Actor)'


def _stat_expr(call, modding: bool, kind: str, args: list) -> 'str | None':
    """`TES4_Attributes.<Read|Write|Modify><kind>(args[, value])`, or None for a write with no value."""
    if call.name in ACTOR_VALUE_READ_FUNCTIONS:
        return f'TES4_Attributes.Read{kind}({", ".join(args)})'
    if len(call) < 2:
        return None
    verb = 'Modify' if modding else 'Write'
    return f'TES4_Attributes.{verb}{kind}({", ".join(args)}, {call.arg(1)})'


def _subject_and_faction(ctx, call, raw: str) -> list:
    """[subject, stat faction property] for another actor's stat."""
    faction = _STAT_FACTIONS[raw.lower()]
    ctx.sc.property_refs[faction] = 'Faction'
    ref = ctx._resolve_self_ref(call.ref, call.extends, actor_func=True)
    return [actor_subject(ref, call.extends), faction]


def attribute_call(ctx, call, raw: str, modding: bool) -> 'str | None':
    """A TES4 attribute read or write: the player's global, any other actor's stat
    faction rank; None for a name that is no TES4 attribute.

    See: docs/commentary/script_convert.md#player-attributes
    """
    edid = _PLAYER_ATTRIBUTE_GLOBALS.get(raw.lower())
    if not edid:
        return None
    ctx.sc.property_refs[edid] = 'GlobalVariable'
    if (call.ref or '').lower() in PLAYER_TOKENS:
        return _stat_expr(call, modding, '', [edid])
    return _stat_expr(call, modding, 'Actor', _subject_and_faction(ctx, call, raw) + [edid])


def kept_skill_call(ctx, call, raw: str, modding: bool, player_av: str) -> 'str | None':
    """Another actor's kept skill through its stat faction; None for the player,
    whose kept skills still read `player_av`, or for any other skill."""
    if raw.lower() not in KEPT_SKILL_NAMES or (call.ref or '').lower() in PLAYER_TOKENS:
        return None
    return _stat_expr(call, modding, 'Skill',
                      _subject_and_faction(ctx, call, raw) + [f'"{player_av}"'])
