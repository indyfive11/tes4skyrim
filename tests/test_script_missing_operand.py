"""A TES4 expression missing an operand halts the script, as Oblivion's evaluator does.

See: docs/commentary/script_convert.md#missing-operand-halts
"""

from script_convert.converter import ScriptConverter
from script_convert.cross_ref import CrossRefGraph
from script_convert.tes4 import nodes as N
from script_convert.tes4.parser import parse


def _convert(body: str) -> str:
    """Convert a GameMode block holding `body` as a standalone quest script."""
    src = f"scn T\nshort x\nshort y\nbegin GameMode\n{body}\nend\n"
    return ScriptConverter(CrossRefGraph()).convert_standalone('T', src, 'Quest', 'T')


def test_parser_marks_the_missing_operand():
    """`a >= 1 && < 2` keeps the stray comparison with an explicit Missing left side."""
    tree = parse("scn T\nbegin GameMode\nif x >= 23.7 && < 23.8\nendif\nend\n")
    cond = tree.blocks[0].body[0].cond
    assert cond.op == '&&' and cond.right.op == '<'
    assert isinstance(cond.right.left, N.Missing)
    assert cond.right.right.text == '23.8'


def test_if_with_missing_operand_halts_and_every_event_checks():
    """The If becomes the halt; the poll and OnInit return while the flag is set."""
    out = _convert('if y == 1\n  if x >= 23.7 && < 23.8\n    set x to 0\n  endif\nendif')
    assert '&& <' not in out and 'x = 0' not in out
    assert 'Bool TES4_Halted' in out
    assert 'TES4_Halted = True' in out
    lines = out.splitlines()
    events = [i for i, line in enumerate(lines) if line.startswith('Event ')]
    assert events
    for i in events:
        assert lines[i + 1:i + 4] == ['  If TES4_Halted', '    Return', '  EndIf']


def test_elseif_with_missing_operand_halts_only_when_reached():
    """Earlier branches still run; the broken elseif and everything after it become the halt."""
    out = _convert('if y == 1\n  set x to 1\nelseif x > < 2\n  set x to 2\n'
                   'else\n  set x to 3\nendif')
    assert 'x = 1' in out and 'x = 2' not in out and 'x = 3' not in out
    assert 'TES4_Halted = True' in out


def test_script_without_missing_operand_is_unchanged():
    """No halted flag or guards in an ordinary script."""
    assert 'TES4_Halted' not in _convert('if x >= 23.7 && x < 23.8\n  set x to 0\nendif')


def test_fragment_only_ends_its_pass():
    """A result script has no halted flag to set; it just returns."""
    conv = ScriptConverter(CrossRefGraph())
    lines = conv.convert_fragment('set x to 1 && < 2\n')
    text = '\n'.join(lines)
    assert 'Return' in text and 'TES4_Halted' not in text
