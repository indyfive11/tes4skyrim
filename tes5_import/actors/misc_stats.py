"""The TES4 general statistics Skyrim does not keep, for the character sheet.

Oblivion's content writes only five of its 34 misc stats; its engine keeps the
rest, so a script writing another has made it its own (Nehrim keeps its
experience points in "Days as a Vampire"). Each such index a plugin's scripts
read or write is a conversion-owned global, `TES4MiscStat<NN>`, which the
converted `Get/ModPCMiscStat` read and write, and MorrowindRuntime's
Statistics tab lists under the plugin's own label for it, once something
writes it: a script, or a command whose engine code counted it
(script_convert.misc_stats.COMMAND_STATS). A stat only the engine's own
minigames counted never moves here and is not listed. The tab also lists the
values a game's statistics page read off a quest script's variable or off
quest stages.

See: docs/commentary/morrowind_runtime.md#statistics-tab
"""

import re
import struct

from script_convert.constants import PAGE_VARIABLES
from script_convert.misc_stats import (COMMAND_STATS, is_script_kept, misc_stat_global,
                                       page_variable_global)
from tes4_export.record_types.common import escape_value
from ..base.owned_records import WELL_KNOWN_PROPERTIES, adopt_or_write, owner_row
from ..base.text_reader import get_str, remap_formid
from ..base.writer import pack_record, pack_string_subrecord, pack_subrecord

#: Each misc stat's label setting in Oblivion.exe, by index (xEdit wbMiscStatEnum); '' has none.
MISC_STAT_GMSTS = (
    'sMiscDaysJailed', 'sMiscGameDaysPlayed', 'sMiscSkillAdvances', 'sMiscTrainingSessions',
    'sMiscLargestBounty', 'sMiscNumKills', 'sMiscNumPersonKills', 'sMiscNumPlacesDiscovered',
    'sMiscNumLocksPicked', 'sMiscNumPicksBroken', 'sMiscSoulsTrapped', 'sMiscIngredientsEaten',
    'sMiscPotionsMade', 'sMiscOblivionGatesShut', 'sMiscHorsesOwned', 'sMiscHousesOwned',
    'sMiscStoresInvestedIn', 'sMiscNumBooksRead', 'sMiscNumSkillBooksRead', 'sMiscArtifactsFound',
    'sMiscHoursSlept', 'sMiscHoursWaited', 'sMiscDaysAsAVampire', '', 'sMiscPeopleFedOn',
    'sMiscJokesTold', 'sMiscDiseasesContracted', 'sMiscNirnrootsFound', 'sMiscNumThefts',
    'sMiscNumPocketsPicked', 'sMiscNumTrespasses', 'sMiscNumAssaults', 'sMiscNumMurders',
    'sMiscNumHorsesStolen')

#: The label each stat falls back on, xEdit's name for it.
MISC_STAT_DEFAULTS = (
    'Days In Prison', 'Days Passed', 'Skill Increases', 'Training Sessions', 'Largest Bounty',
    'Creatures Killed', 'People Killed', 'Places Discovered', 'Locks Picked', 'Picks Broken',
    'Souls Trapped', 'Ingredients Eaten', 'Potions Made', 'Oblivion Gates Shut', 'Horses Owned',
    'Houses Owned', 'Stores Invested In', 'Books Read', 'Skill Books Read', 'Artifacts Found',
    'Hours Slept', 'Hours Waited', 'Days As A Vampire', 'Last Day As A Vampire', 'People Fed On',
    'Jokes Told', 'Diseases Contracted', 'Nirnroots Found', 'Items Stolen', 'Items Pickpocketed',
    'Trespasses', 'Assaults', 'Murders', 'Horses Stolen')

#: A label too long for its row -> the short form the tab shows (Nehrim's English and German).
SHORT_LABELS = {'Overall amount of experience points': 'Total XP',
                'Current amount of learning points': 'Learning Points',
                'Insgesamt gesammelte Erfahrungspunkte (EP)': 'Gesamt-EP',
                'Insgesamt gesammelte Lernpunkte (LP)': 'Lernpunkte'}

#: A page value read off quest stages: (label, ((quest, below this stage, value), ...), otherwise).
PAGE_STAGE_RULES = (('Bank interest (percent)', (('MQ14', 20, 2), ('MQ19', 70, 1)), 3),)

#: The standing globals the tab lists, by the row the runtime reads them under.
STANDING_GLOBALS = (('fame', 'TES4Fame'), ('infamy', 'TES4Infamy'))

#: A call reading or writing one stat, anywhere in a script's text.
_CALL = re.compile(r'(?i)\b(get|mod)pcmiscstat\b[\s,]*(\d+)')

#: A COMMAND_STATS command, anywhere in a script's text.
_COUNTING = re.compile(r'(?i)\b(' + '|'.join(COMMAND_STATS) + r')\b')

#: The record types whose text holds script source.
_SCRIPTED = ('SCPT', 'INFO', 'QUST')


def _script_texts(by_type: dict):
    """Each scripted record's text."""
    for sig in _SCRIPTED:
        for rec in by_type.get(sig, ()):
            yield '\n'.join(v for v in rec.values() if isinstance(v, str))


def _calls(by_type: dict):
    """Each (verb, index) a script's text calls, lowercase verb; a counting command is a `mod`."""
    for text in _script_texts(by_type):
        for verb, index in _CALL.findall(text):
            yield verb.lower(), int(index)
        for command in _COUNTING.findall(text):
            yield 'mod', COMMAND_STATS[command.lower()]


def misc_stat_calls(by_type: dict) -> tuple:
    """(indices read or written, indices written) by the plugin's scripts, ours only."""
    calls = [(verb, index) for verb, index in _calls(by_type) if is_script_kept(index)]
    return ({index for _verb, index in calls},
            {index for verb, index in calls if verb == 'mod'})


def page_writes(by_type: dict) -> list:
    """The PAGE_VARIABLES entries some script of the plugin sets."""
    texts = list(_script_texts(by_type))
    return [entry for entry in PAGE_VARIABLES
            if any(re.search(rf'(?i)\bset\s+{entry[0]}\.{entry[1]}\s+to\b', t) for t in texts)]


def _global(fid: int, edid: str) -> bytes:
    """A float GLOB at 0."""
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_subrecord('FNAM', struct.pack('<B', ord('f')))
    subs += pack_subrecord('FLTV', struct.pack('<f', 0.0))
    return pack_record('GLOB', fid, 0, subs)


def create_misc_stat_globals(writer, master_index, by_type: dict) -> dict:
    """{EditorID: FormID} of the global behind every stat the plugin uses,
    and every page variable its scripts set; a master's adopted."""
    edids = [misc_stat_global(i) for i in sorted(misc_stat_calls(by_type)[0])]
    edids += [page_variable_global(quest, variable) for quest, variable, _ in page_writes(by_type)]
    made = {}
    for edid in edids:
        made[edid], _new = adopt_or_write(writer, master_index, 'GLOB', edid,
                                          lambda fid, e=edid: _global(fid, e))
    return made


def label_lines(gmsts: list) -> list:
    """`label.<setting>=text` for each sMisc setting the plugin authors, its colon
    gone and a SHORT_LABELS one shortened. The runtime takes the latest of the
    game's own plugins, so a translation's wins."""
    rows = []
    for rec in gmsts:
        edid = get_str(rec, 'EditorID', '')
        text = get_str(rec, 'DATA.Value', '').strip().rstrip(':').strip()
        if edid.startswith('sMisc') and text:
            rows.append(f'label.{edid}={escape_value(SHORT_LABELS.get(text, text))}')
    return rows


def _own_ref(edid: str, plugin: str, masters: list) -> str:
    """`Plugin.esm|FormID` of global `edid` when the plugin holds it, else ''."""
    fid = WELL_KNOWN_PROPERTIES.get(edid, 0)
    return owner_row('', fid, plugin, masters)[1:] if fid and fid >> 24 == len(masters) else ''


def _stage_rows(by_type: dict, plugin: str, masters: list) -> list:
    """(label, `Quest@FormID,stage,value;...|otherwise`) for each stage rule
    whose quests are all the plugin's own."""
    quests = {get_str(r, 'EditorID').lower(): remap_formid(int(r['FormID'], 16))
              for r in by_type.get('QUST', ()) if r.get('FormID')}
    rows = []
    for label, rules, otherwise in PAGE_STAGE_RULES:
        fids = [quests.get(quest.lower(), 0) for quest, _stage, _value in rules]
        if not all(fids) or any(fid >> 24 != len(masters) for fid in fids):
            continue
        parts = [owner_row('', fid, plugin, masters)[1:].replace('|', '@') + f',{stage},{value}'
                 for fid, (_quest, stage, value) in zip(fids, rules)]
        rows.append((label, ';'.join(parts) + f'|{otherwise}'))
    return rows


def page_lines(by_type: dict, plugin: str, masters: list) -> list:
    """The page rows the plugin holds: each mirrored variable's global, then the stage rules."""
    rows = [(label, _own_ref(page_variable_global(quest, variable), plugin, masters))
            for quest, variable, label in PAGE_VARIABLES]
    rows = [row for row in rows if row[1]] + _stage_rows(by_type, plugin, masters)
    return [f'page.{i}={escape_value(label)}|{value}' for i, (label, value) in enumerate(rows)]


def stat_lines(by_type: dict, plugin: str, masters: list) -> list:
    """stats.txt rows: `fame=`/`infamy=` for the standing globals the plugin holds,
    `misc.<index>=setting|default label|Plugin.esm|FormID` for each stat it writes,
    its page rows and its own labels."""
    rows = []
    for row, edid in STANDING_GLOBALS:
        ref = _own_ref(edid, plugin, masters)
        if ref:
            rows.append(f'{row}={ref}')
    for index in sorted(misc_stat_calls(by_type)[1]):
        fid = WELL_KNOWN_PROPERTIES.get(misc_stat_global(index), 0)
        if fid:
            head = f'misc.{index}={MISC_STAT_GMSTS[index]}|{MISC_STAT_DEFAULTS[index]}|'
            rows.append(owner_row('', fid, plugin, masters).replace('=', head, 1))
    return rows + page_lines(by_type, plugin, masters) + label_lines(by_type.get('GMST', []))
