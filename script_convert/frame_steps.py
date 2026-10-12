"""Fixed steps a TES4 poll block takes on one of its own float variables.

TES4 ran GameMode every frame, so `set alpha to alpha - 0.01` ahead of
`SetActorAlpha alpha` fades by 0.01 per FRAME: a rate.  A converted poll passes
a few times a second, so such a step is multiplied by the frames the measured
pass stood for.  Only a variable that ends in a pose, a size or a fade is
scaled; counters and timers keep their steps.

See: docs/commentary/script_convert.md#variable-steps-are-rates
"""

import math

from script_convert.constants import POLL_BLOCKS
from script_convert.poll_motion import TES4_FRAME_RATE
from script_convert.tes4 import nodes as N

#: Commands whose arguments are seen in game: a pose, a size, a fade.
SINKS = ('setpos', 'setangle', 'setscale', 'rotate', 'moveto',
         'positionworld', 'positioncell', 'setactoralpha', 'saa')

#: Sinks whose first argument names an axis, not a value.
_AXIS_FIRST = ('setpos', 'setangle', 'rotate')

#: What a scaled step is multiplied by: frames per second times seconds passed.
RATE = f' * {TES4_FRAME_RATE} * TES4_SecondsPassed'

_EXACT = ('==', '!=')
_ORDER = ('<', '>', '<=', '>=')

#: Half a turn and a whole one, in degrees and in radians.
_TURNS = (180.0, 360.0, math.pi, 2 * math.pi)


def _number(expr):
    """(literal node, value) of a numeric literal, signed or not; else None."""
    if isinstance(expr, N.Unary) and expr.op == '-':
        inner = _number(expr.operand)
        return (inner[0], -inner[1]) if inner else None
    if isinstance(expr, N.Literal) and not expr.is_string:
        try:
            return expr, float(expr.text)
        except ValueError:
            return None
    return None


def _var(expr) -> str:
    """The lowercase name of a bare variable, else ''."""
    return expr.name.lower() if isinstance(expr, N.Ident) else ''


def step_of(st):
    """(variable, literal node, signed amount) for `set v to v +/- c` or `let v += c`."""
    if not isinstance(st, N.Assign) or not _var(st.target):
        return None
    name, value = _var(st.target), st.value
    if st.op in ('+', '-'):
        num = _number(value)
        return (name, num[0], num[1] if st.op == '+' else -num[1]) if num else None
    if st.op or not isinstance(value, N.BinOp) or value.op not in ('+', '-'):
        return None
    num = _number(value.right)
    if _var(value.left) == name and num:
        return name, num[0], num[1] if value.op == '+' else -num[1]
    num = _number(value.left)
    if value.op == '+' and _var(value.right) == name and num:
        return name, num[0], num[1]
    return None


def _names(expr) -> set:
    """Lowercase names of the bare variables an expression mentions."""
    return {_var(e) for e in N.walk_expr(expr) if _var(e)}


def _bodies(tree) -> list:
    """Every statement list of a script: its blocks and what sits outside them."""
    return [tree.body] + [b.body for b in tree.blocks]


def seen_variables(tree) -> set:
    """Variables whose value ends in an argument of a `SINKS` command.

    Directly, or through any chain of assignments to other variables.
    """
    seen, feeds = set(), {}
    for body in _bodies(tree):
        for st in N.walk_stmts(body):
            if isinstance(st, N.Assign) and _var(st.target):
                for name in _names(st.value):
                    feeds.setdefault(name, set()).add(_var(st.target))
        for expr in N.walk_exprs_in(body):
            if isinstance(expr, N.Call) and expr.called in SINKS:
                skip = 1 if expr.called in _AXIS_FIRST else 0
                for arg in expr.args[skip:]:
                    seen |= _names(arg)
    grown = True
    while grown:
        more = {src for src, dst in feeds.items() if dst & seen} - seen
        grown = bool(more)
        seen |= more
    return seen


def exact_variables(tree) -> set:
    """Variables the script compares with `==` or `!=`: a scaled step would skip the value."""
    out = set()
    for body in _bodies(tree):
        for expr in N.walk_exprs_in(body):
            if isinstance(expr, N.BinOp) and expr.op in _EXACT:
                out |= _names(expr)
    return out


def _branches(st) -> list:
    """[(conditions that govern the branch, body)] for each branch of an `If`.

    An `elseif` or `else` branch is governed by every condition before it too.
    A loop's body is not walked: a step inside one runs several times a pass.
    """
    if not isinstance(st, N.If):
        return []
    conds, out = (st.cond,), [((st.cond,), st.body)]
    for cond, body, _ in st.elifs:
        conds += (cond,)
        out.append((conds, body))
    return out + [(conds, st.orelse)]


def _is_return_guard(st) -> bool:
    """Is `st` an `if` whose whole body is `return`: a guard on what follows it?"""
    if not isinstance(st, N.If) or st.elifs or st.orelse:
        return False
    body = [s for s in st.body if not isinstance(s, (N.Comment, N.Blank))]
    return len(body) == 1 and isinstance(body[0], N.Return)


def _walk(body, guards: tuple):
    """(statement, enclosing (conditions, guarded statements) pairs), depth first.

    The statements after `if done == 1 / return / endif` are guarded by that
    test as much as an `if` body is by its own.
    """
    body = list(body or ())
    for at, st in enumerate(body):
        yield st, guards
        for conds, branch in _branches(st):
            yield from _walk(branch, guards + ((conds, branch),))
        if _is_return_guard(st):
            guards = guards + (((st.cond,), body[at + 1:]),)


def _ref(expr) -> str:
    """The lowercase spelling of a variable or of a `ref.variable`, else ''."""
    if isinstance(expr, N.Member):
        owner = _ref(expr.owner)
        return f'{owner}.{expr.name.lower()}' if owner else ''
    return _var(expr)


def _refs(expr) -> set:
    """What an expression reads: its variables and its `ref.variable` members."""
    return {_ref(e) for e in N.walk_expr(expr) if _ref(e)}


def _ends_state(st, named: set) -> bool:
    """Does one statement end what a branch tested?

    A write to something its conditions name, unless it drains or steps it
    (`set t to t - x`); the `SetStage` of a quest they read; or an `if` whose
    every branch does one of those.
    """
    if isinstance(st, N.Assign):
        target, value = _ref(st.target), st.value
        drains = bool(st.op) or (isinstance(value, N.BinOp) and value.op in ('+', '-')
                                 and _ref(value.left) == target)
        return target in named and not drains
    if isinstance(st, N.If):
        return all(any(_ends_state(inner, named) for inner in branch)
                   for _, branch in _branches(st))
    call = st.expr if isinstance(st, N.ExprStmt) else None
    return (isinstance(call, N.Call) and call.called == 'setstage'
            and bool(call.args) and _var(call.args[0]) in named)


def _runs_once(guards: tuple) -> bool:
    """Does an enclosing branch switch itself off as it runs?

    It does when one of its own statements ends what its conditions tested:
    `if stage == 1 ... set stage to 2`.  A switch behind a test of its own,
    one `if` deeper, leaves the branch running until that test holds.
    """
    for conds, branch in guards:
        named = set().union(*(_refs(cond) for cond in conds))
        if any(_ends_state(st, named) for st in branch):
            return True
    return False


def _limits(conds, name: str) -> list:
    """The numbers conditions order `name` against: `v > 360` gives [360.0]."""
    out = []
    for cond in conds:
        for expr in N.walk_expr(cond):
            if not isinstance(expr, N.BinOp) or expr.op not in _ORDER:
                continue
            for var, other in ((expr.left, expr.right), (expr.right, expr.left)):
                num = _number(other)
                if _var(var) == name and num:
                    out.append(num[1])
    return out


def _span(tree, name: str) -> float:
    """The width of the range the script tests `name` against, 0 with fewer than two limits."""
    conds = [st.cond for body in _bodies(tree) for st in N.walk_stmts(body)
             if isinstance(st, (N.If, N.While))]
    conds += [cond for body in _bodies(tree) for st in N.walk_stmts(body)
              if isinstance(st, N.If) for cond, _, _ in st.elifs]
    limits = _limits(conds, name)
    return max(limits) - min(limits) if limits else 0.0


def _is_turn(amount: float) -> bool:
    """Is the amount half a turn or a whole one, in degrees or radians?"""
    return any(abs(abs(amount) - turn) < 0.01 for turn in _TURNS)


def _is_jump(guards: tuple, name: str, amount: float, span: float) -> bool:
    """Is a step under a test of its own variable too large to be a rate?

    `if angle > 360 ... set angle to angle - 360` crosses its range at once:
    as large as the limit it is tested against, or as the whole tested range,
    or a turn.  A wrap-around or a jump, not a rate.
    """
    if not any(name in _names(cond) for conds, _ in guards for cond in conds):
        return False
    own = [limit for conds, _ in guards for limit in _limits(conds, name)]
    return (_is_turn(amount) or (span and abs(amount) >= span)
            or any(limit and abs(amount) >= abs(limit) for limit in own))


def _poll_blocks(tree) -> list:
    """The blocks TES4 ran every frame."""
    return [b for b in tree.blocks if b.btype.lower() in POLL_BLOCKS]


def pass_counters(tree, wanted: set) -> set:
    """Unscaled variables a poll steps by a fixed amount: pass counters."""
    return {step[0] for block in _poll_blocks(tree)
            for step in map(step_of, N.walk_stmts(block.body)) if step} - wanted


def _is_counted(guards: tuple, counters: set) -> bool:
    """Does an enclosing condition test a pass counter (a share, not a rate)?"""
    return any(_names(cond) & counters for conds, _ in guards for cond in conds)


def frame_steps(tree, extends: str = '') -> dict:
    """{(line, variable): (literal node, '+' or '-')} for each step to scale.

    A step qualifies when it sits in a poll block, on a float variable of the
    script that ends in a `SINKS` argument and is never compared exactly, and
    is neither a one-off, a jump, nor counted out by a pass counter.  A quest
    script has none: TES4 ran it every fQuestDelayTime, not every frame.
    See: docs/commentary/script_convert.md#variable-steps-are-rates
    """
    if tree is None or extends == 'Quest':
        return {}
    floats = {v.name.lower() for v in tree.variables
              if v.vtype.lower() == 'float'}
    wanted = (floats & seen_variables(tree)) - exact_variables(tree)
    counters = pass_counters(tree, wanted) if wanted else set()
    out = {}
    for block in _poll_blocks(tree) if wanted else ():
        for st, guards in _walk(block.body, ()):
            step = step_of(st)
            if not step or step[0] not in wanted or not step[2]:
                continue
            name, literal, amount = step
            if (_runs_once(guards) or _is_counted(guards, counters)
                    or _is_jump(guards, name, amount, _span(tree, name))):
                continue
            out[(st.line, name)] = (literal, '+' if amount > 0 else '-')
    return out


def scaled(sc, st):
    """(literal node, sign) when the assignment `st` is a step to scale, else None."""
    name = _var(getattr(st, 'target', None))
    return sc.frame_steps.get((st.line, name)) if name else None


def line(target: str, sign: str, amount: str) -> str:
    """The Papyrus line of a scaled step: amount times frames passed."""
    return f'{target} = {target} {sign} {amount}{RATE}'
