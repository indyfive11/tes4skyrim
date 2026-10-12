"""Oblivion's `StartConversation` walk: each played line's own next-topic list.

Oblivion's engine plays the topic, then tries the played line's Choice (TCLT)
topics in order with the speaker its NextSpeaker names, until a line has none.
Skyrim's `Say` plays one line, so a generated per-plugin script walks the same
graph at runtime. Import keeps every topic the walk can reach; the scripts
stage hooks their lines and writes the routing script, both from this module.

See: docs/commentary/tes5_import_dialogue.md#script-started-conversation-chains
"""
from script_convert.constants import papyrus_script_name
from .converter import CONV_KEEP_EDIDS
from .say_topics import PLAYER_TOKENS, STARTCONV_RE, collect_script_texts, script_lines

#: Most lines one walk plays, so a line that loops to its own topic cannot run forever.
MAX_HOPS = 64


def _hex(value) -> int:
    """A raw source FormID from its export hex text, 0 when absent or malformed."""
    try:
        return int(value or '0', 16)
    except ValueError:
        return 0


def _raw(rec: dict, key: str) -> int:
    """The record's `key` as a raw source FormID, 0 when absent or malformed."""
    return _hex(rec.get(key))


def _choices(info: dict) -> list:
    """The line's Choice targets as raw source FormIDs, in authored order."""
    out, i = [], 0
    while (v := info.get(f'Choice[{i}]')) is not None:
        out.append(_hex(v))
        i += 1
    return [c for c in out if c]


def started_topics(by_type: dict) -> set:
    """Lowercase topic EditorIDs that a `StartConversation <npc> <topic>` names."""
    out = set()
    for line in script_lines(collect_script_texts(by_type)):
        for m in STARTCONV_RE.finditer(line):
            if m.group(2) and m.group(1).lower() not in PLAYER_TOKENS:
                out.add(m.group(2).lower())
    return out


def reachable_topics(by_type: dict) -> dict:
    """Raw source FormID -> DIAL for every topic a StartConversation walk can play.

    From each started topic, follow the Choice lists of lines owned by the
    started topic's quests, as the scheduler driver does, and never through an
    engine channel (CONV_KEEP_EDIDS) the bark pass splits per quest.
    """
    dials = {_raw(d, 'FormID'): d for d in by_type.get('DIAL', [])
             if d.get('EditorID') not in CONV_KEEP_EDIDS}
    infos_by_topic = {}
    for inf in by_type.get('INFO', []):
        infos_by_topic.setdefault(_raw(inf, 'ParentDIAL'), []).append(inf)
    starts = started_topics(by_type)
    seen = set()
    for start, d in dials.items():
        if (d.get('EditorID') or '').lower() in starts:
            quests = {i.get('QSTI.Quest') for i in infos_by_topic.get(start, [])}
            seen |= _walk(start, quests, dials, infos_by_topic)
    return {f: dials[f] for f in seen}


def _walk(start: int, quests: set, dials: dict, infos_by_topic: dict) -> set:
    """Raw topic FormIDs one walk reaches through lines of `quests`."""
    seen, frontier = set(), [start]
    while frontier:
        fid = frontier.pop()
        lines = [i for i in infos_by_topic.get(fid, []) if i.get('QSTI.Quest') in quests]
        if fid in seen or fid not in dials or not lines:
            continue
        seen.add(fid)
        for inf in lines:
            frontier.extend(_choices(inf))
    return seen


def conversation_routes(by_type: dict, own_file: str, masters: list) -> dict:
    """info raw24 -> (NextSpeaker, [(plugin file, topic raw24), ...]) per routed line.

    Only this plugin's own lines under reachable topics, and only next topics
    the walk reaches; a line left without one ends the conversation.
    """
    reach = reachable_topics(by_type)
    own = len(masters)
    out = {}
    for inf in _routed_lines(by_type, reach, own):
        targets = [(masters[c >> 24] if c >> 24 < own else own_file, c & 0xFFFFFF)
                   for c in _choices(inf) if c in reach]
        if targets:
            ns = int(inf.get('DATA.NextSpeaker') or '0')
            out[_raw(inf, 'FormID') & 0xFFFFFF] = (ns, targets)
    return out


def _routed_lines(by_type: dict, reach: dict, own: int) -> list:
    """This plugin's own INFOs under a reachable topic."""
    return [i for i in by_type.get('INFO', [])
            if _raw(i, 'ParentDIAL') in reach and _raw(i, 'FormID') >> 24 == own]


def routed_starts(by_type: dict, routes: dict) -> set:
    """Lowercase started topic EditorIDs whose own lines name a next topic."""
    topics = {_raw(i, 'ParentDIAL') for i in by_type.get('INFO', [])
              if _raw(i, 'FormID') & 0xFFFFFF in routes}
    starts = started_topics(by_type)
    return {e for d in by_type.get('DIAL', [])
            if (e := (d.get('EditorID') or '').lower()) in starts
            and _raw(d, 'FormID') in topics}


def route_script_name(own_file: str) -> str:
    """The routing script's name for the plugin `own_file`."""
    stem = own_file.rsplit('.', 1)[0]
    return papyrus_script_name(f'ConvRoute_{stem}')


def generate_route_psc(name: str, routes: dict) -> str:
    """The routing script source, or '' when the plugin has no routed line."""
    if not routes:
        return ''
    lines = [f'ScriptName {name} Hidden',
             '{Oblivion StartConversation: each played line picks the next topic.',
             ' Generated by tes5_import.dialogue.conversation_routes - do not edit.}',
             '', *_RUN, '',
             'Int Function Route(Int aiInfo) Global',
             '  {NextSpeaker * 16 + choice count of a routed line, else 0.}']
    for info, (ns, targets) in sorted(routes.items()):
        lines += [f'  If aiInfo == 0x{info:06X}',
                  f'    Return {ns * 16 + len(targets)}', '  EndIf']
    lines += ['  Return 0', 'EndFunction', '',
              'Topic Function Choice(Int aiInfo, Int aiK) Global',
              '  {The routed line\'s aiK-th next topic.}']
    for info, (_ns, targets) in sorted(routes.items()):
        lines.append(f'  If aiInfo == 0x{info:06X}')
        for k, (plugin, topic) in enumerate(targets):
            lines += [f'    If aiK == {k}',
                      f'      Return Game.GetFormFromFile(0x{topic:06X}, "{plugin}") as Topic',
                      '    EndIf']
        lines.append('  EndIf')
    lines += ['  Return None', 'EndFunction', '']
    return '\n'.join(lines)


_RUN = """\
Function Run(Actor akA, Actor akB, Topic akTopic) Global
  {akA says akTopic to akB; then each played line's next topic follows.}
  Actor speaker = akA
  Actor other = akB
  Actor swap = None
  Int info = TES4Polyfill.ConverseLine(speaker, akTopic)
  Int hops = 1
  Int played = 0
  Int route = 0
  Int k = 0
  Topic choice = None
  While info > 0 && hops < {max_hops}
    played = info
    route = Route(played)
    info = 0
    If route / 16 != 1
      swap = speaker
      speaker = other
      other = swap
    EndIf
    k = 0
    While info == 0 && k < route % 16
      choice = Choice(played, k)
      info = TES4Polyfill.ConverseLine(speaker, choice)
      If info == 0 && route / 16 == 2
        info = TES4Polyfill.ConverseLine(other, choice)
        If info > 0
          swap = speaker
          speaker = other
          other = swap
        EndIf
      EndIf
      k += 1
    EndWhile
    hops += 1
  EndWhile
EndFunction""".replace('{max_hops}', str(MAX_HOPS)).splitlines()
