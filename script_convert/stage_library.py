"""A quest whose stages are called as functions: what its callers and its fragment script emit.

Before script extenders a TES4 author wrote a function as a quest stage: set
the quest's variables, `SetStage`, read the results.  TES4 runs one script at a
time; converted callers run at once and the engine's `SetStage` is latent, so
callers read each other's values.  A library's stage is called directly and
its callers take turns.  `CrossRefGraph.library_stages` says which quests
qualify.

See: docs/commentary/script_convert.md#stage-function-libraries
"""

from __future__ import annotations

from script_convert.constants import (papyrus_script_name, safe_property_name,
                                      script_prefix)
from script_convert.tes4 import nodes as N

#: Frames a caller waits for its turn before it takes it anyway.
TURN_TRIES = 100

#: Statements a turn may hold: one line each, no branch and no early exit.
_PLAIN = (N.Assign, N.ExprStmt)

#: Statements that neither join nor break a run.
_FILLER = (N.Comment, N.Blank)

#: Statements that would leave a branch, and with it a turn, by another way.
_EXITS = (N.Return, N.Goto, N.Label)

#: What a caller emits around or for a library stage call.
_CALL_MARKS = ('.TES4Enter(', '.TES4CallStage(', '.TES4LockedStage(')


def quest_key(node) -> str:
    """The lowercase quest name an operand spells, quoted or not; '' for anything else."""
    if isinstance(node, N.Ident):
        return node.name.lower()
    if isinstance(node, N.Literal) and node.is_string:
        return node.text.strip('"').lower()
    return ''


def fragment_script_name(xref, key: str) -> str:
    """The fragment script of the quest `key`, spelled as its record spells the quest."""
    edid = xref.formid_to_edid.get(xref.edid_to_formid.get(key, ''), key)
    return papyrus_script_name(edid, script_prefix('_QF_'))


def call_text(conv, quest_src: str, prop: str, stage_no) -> str:
    """The Papyrus call for `SetStage <library quest> <n>`; '' when the quest is no library.

    Inside the library's own stage code the stage is a sibling function; a
    caller holding a turn calls it directly; any other caller takes a turn for
    the one call.
    See: docs/commentary/script_convert.md#stage-function-libraries
    """
    key = quest_src.strip().strip('"').lower()
    stages = conv.xref.library_stages(key) if conv.xref else ()
    if not stages:
        return ''
    if conv.own_stage_library == key:
        if str(stage_no).isdigit() and int(stage_no) in stages:
            return 'Fragment_Stage_%04d_Item_0()' % int(stage_no)
        return f'TES4RunStage({stage_no})'
    verb = 'TES4CallStage' if key in conv.turn_span else 'TES4LockedStage'
    return f'{fragment_script_name(conv.xref, key)}.{verb}({prop}, {stage_no})'


def library_call(conv, st) -> str:
    """The library quest a statement calls a stage of, from outside its own stage code; else ''."""
    expr = st.expr if type(st) is N.ExprStmt else None
    if (not isinstance(expr, N.Call) or expr.name.lower() != 'setstage'
            or not expr.args or conv.xref is None):
        return ''
    key = quest_key(expr.args[0])
    if key and key != conv.own_stage_library and conv.xref.library_stages(key):
        return key
    return ''


def _touches(st, key: str) -> bool:
    """Does a plain statement read or write a variable of the quest `key`?"""
    return any(isinstance(e, N.Member) and quest_key(e.owner) == key
               for e in N.walk_exprs_in([st]))


def _joins(conv, st, key: str) -> bool:
    """May `st` stand inside a turn on `key` next to the call?

    A plain statement that touches the library or calls it, and a branch that
    touches it and has no early exit, so that a result tested right after the
    call is still the caller's own.
    """
    if isinstance(st, _FILLER):
        return True
    if type(st) in _PLAIN:
        return _touches(st, key) or library_call(conv, st) == key
    return (type(st) is N.If and _touches(st, key)
            and not any(isinstance(inner, _EXITS) for inner in N.walk_stmts([st])))


def turn_spans(conv, body) -> list:
    """[(first index, last index, quest key)] for each run of `body` that must hold a turn.

    A run is a stage call on a library with the statements next to it that
    read or write that library's variables.  A call with no such neighbour
    gets no run: it takes its turn inside the call.  Runs on one library never
    overlap; inside a turn already held on a library no second one is opened.
    See: docs/commentary/script_convert.md#stage-function-libraries
    """
    spans, taken = [], {}
    for i, st in enumerate(body):
        key = library_call(conv, st)
        floor = taken.get(key, -1)
        if not key or key in conv.turn_span or i <= floor:
            continue
        lo = hi = i
        while lo - 1 > floor and _joins(conv, body[lo - 1], key):
            lo -= 1
        while hi + 1 < len(body) and _joins(conv, body[hi + 1], key):
            hi += 1
        while isinstance(body[lo], _FILLER):
            lo += 1
        while isinstance(body[hi], _FILLER):
            hi -= 1
        if any(_touches(body[k], key) for k in range(lo, hi + 1)):
            spans.append((lo, hi, key))
            taken[key] = hi
    return spans


def open_turn(conv, key: str, quest_src: str, pad: str) -> tuple:
    """(the line that takes a turn on `key`, the line that gives it back).

    See: docs/commentary/script_convert.md#stage-function-libraries
    """
    prop = safe_property_name(quest_src)
    conv.turn_seq += 1
    name = 'TES4_Turn%d' % conv.turn_seq
    script = fragment_script_name(conv.xref, key)
    conv.turn_span.add(key)
    return (f'{pad}Int {name} = {script}.TES4Enter({prop})',
            f'{pad}{script}.TES4Leave({prop}, {name})')


def holds_library_call(lines) -> bool:
    """Does any emitted line take a turn on a library or call one of its stages?"""
    return any(mark in line for line in lines for mark in _CALL_MARKS)


def quest_source(st) -> str:
    """The quest operand of a stage-call statement, as the author wrote it."""
    arg = st.expr.args[0]
    return arg.name if isinstance(arg, N.Ident) else arg.text


def library_functions(script_name: str, quest_script: str, stages) -> list:
    """The turn-taking and direct-call functions of a library's fragment script.

    A turn is a number: giving back a turn that was taken over is a no-op, so
    a caller that waited out `TURN_TRIES` tries cannot be unlocked by the one
    it replaced.  A take-over and a start of the quest are logged.
    See: docs/commentary/script_convert.md#stage-function-libraries
    """
    start = (f'{quest_script}.TES4Start(akQuest as {quest_script})'
             if quest_script != 'Quest' else 'akQuest.Start()')
    out = ['Int TES4_TurnOwner = 0',
           'Int TES4_TurnNext = 0',
           '',
           'Int Function TES4TakeTurn()',
           '  Int tries = 0',
           f'  While TES4_TurnOwner != 0 && tries < {TURN_TRIES}',
           '    Utility.Wait(0.01)',
           '    tries += 1',
           '  EndWhile',
           '  If TES4_TurnOwner != 0',
           f'    Debug.Trace("{script_name}: turn " + TES4_TurnOwner + " was not given back; taken over")',
           '  EndIf',
           '  TES4_TurnNext += 1',
           '  TES4_TurnOwner = TES4_TurnNext',
           '  Return TES4_TurnOwner',
           'EndFunction',
           '',
           'Function TES4EndTurn(Int aiTurn)',
           '  If TES4_TurnOwner == aiTurn',
           '    TES4_TurnOwner = 0',
           '  EndIf',
           'EndFunction',
           '',
           'Bool Function TES4RunStage(Int aiStage)']
    for n, stage in enumerate(stages):
        out += [f"  {'If' if n == 0 else 'ElseIf'} aiStage == {stage}",
                '    Fragment_Stage_%04d_Item_0()' % stage]
    out += ['  Else',
            '    Return SetStage(aiStage)',
            '  EndIf',
            '  Return True',
            'EndFunction',
            '',
            'Int Function TES4Enter(Quest akQuest) Global',
            f'  {script_name} library = akQuest as {script_name}',
            '  If library == None',
            '    Return 0',
            '  EndIf',
            '  Return library.TES4TakeTurn()',
            'EndFunction',
            '',
            'Function TES4Leave(Quest akQuest, Int aiTurn) Global',
            f'  {script_name} library = akQuest as {script_name}',
            '  If library != None',
            '    library.TES4EndTurn(aiTurn)',
            '  EndIf',
            'EndFunction',
            '',
            'Bool Function TES4CallStage(Quest akQuest, Int aiStage) Global',
            '  If !akQuest.IsRunning()',
            f'    Debug.Trace("{script_name}: the quest was not running; starting it")',
            f'    {start}',
            '  EndIf',
            f'  {script_name} library = akQuest as {script_name}',
            '  If library == None',
            '    Return akQuest.SetStage(aiStage)',
            '  EndIf',
            '  Return library.TES4RunStage(aiStage)',
            'EndFunction',
            '',
            'Bool Function TES4LockedStage(Quest akQuest, Int aiStage) Global',
            '  Int turn = TES4Enter(akQuest)',
            '  Bool done = TES4CallStage(akQuest, aiStage)',
            '  TES4Leave(akQuest, turn)',
            '  Return done',
            'EndFunction',
            '']
    return out
