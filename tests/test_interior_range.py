"""The importer names interior cells whose references Skyrim would move.

See: docs/commentary/tes5_import_world.md#interior-range-census
"""

import copy
import inspect
import math
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tes5_import import pipeline
from tes5_import.record_types import interior_range as IR
from tes5_import.record_types import items

CELL = '0100AAAA'
STATIC = '01000010'


def _refr(fid, x=0.0, y=0.0, z=0.0, cell=CELL, base=STATIC, **extra):
    """An exported REFR at (x, y, z)."""
    rec = {'Signature': 'REFR', 'FormID': fid, 'RecordFlags': '0',
           'ParentCELL': cell, 'NAME': base, 'PosX': str(x), 'PosY': str(y),
           'PosZ': str(z), 'RotX': '0.0', 'RotY': '0.0', 'RotZ': '0.0'}
    rec.update(extra)
    return rec


def _census(*refrs, **by_type):
    """The census over `refrs`, plus any other record lists by type."""
    return IR.census({'REFR': list(refrs), **by_type})


def test_beyond_the_limit_on_x_or_on_y_counts():
    """Either axis, either sign: four of four."""
    rows = _census(_refr('01000001', x=30000.5),
                   _refr('01000002', y=-45000.0),
                   _refr('01000003', x=-30001.0),
                   _refr('01000004', x=-30001.0, y=30001.0))
    assert rows == [(4, CELL)]


def test_the_limit_itself_is_in_range():
    assert _census(_refr('01000001', x=30000.0, y=-30000.0)) == []


def test_height_is_not_tested():
    assert _census(_refr('01000001', z=90000.0)) == []


def test_an_exterior_reference_is_never_counted():
    assert _census(_refr('01000001', x=99999.0, ParentWRLD='0000003C')) == []


def test_a_reference_in_an_exterior_cell_is_never_counted():
    """Some exporters name the worldspace on the cell, not on the reference."""
    far = [_refr('01000001', x=99999.0, cell='0100000a'),
           _refr('01000002', x=99999.0, cell='0000000B'),
           _refr('01000003', x=99999.0, cell='0100000C')]
    cells = [{'FormID': '0100000a', 'ParentWRLD': '0000003C'},
             {'FormID': '0100000C'}]
    masters = {'0000000B': {'Signature': 'CELL', 'ParentWRLD': '0000003C'}}
    assert IR.census({'REFR': far, 'CELL': cells}, masters) == [(1, '0100000C')]
    assert IR.census({'REFR': far, 'CELL': cells}) == [(1, '0000000B'),
                                                       (1, '0100000C')]


def test_a_deleted_reference_is_skipped():
    assert _census(_refr('01000001', x=99999.0, RecordFlags='32')) == []


def test_a_reference_without_a_position_is_in_range():
    bare = {'Signature': 'REFR', 'FormID': '01000001', 'ParentCELL': CELL}
    assert _census(bare) == []


def test_a_reference_without_a_cell_is_skipped():
    """Far out of range but in no cell: not counted, and no error."""
    orphan = _refr('01000001', x=99999.0)
    del orphan['ParentCELL']
    assert _census(orphan) == []


def test_lights_actors_and_levelled_lists_are_exempt():
    """Six far references, ids in either case; only the static's counts."""
    far = dict(x=50000.0)
    refrs = [_refr('01000001', base='01000020', **far),
             _refr('01000002', base='01000021', **far),
             _refr('01000003', base='0100002b', **far),
             _refr('01000004', base='0100002A', **far),
             _refr('01000005', base='01000023', **far),
             _refr('01000006', base=STATIC, **far)]
    rows = _census(*refrs,
                   LIGH=[{'FormID': '01000020'}],
                   LVLC=[{'FormID': '01000021'}],
                   NPC_=[{'FormID': '0100002B'}],
                   CREA=[{'FormID': '0100002a'}],
                   LVLN=[{'FormID': '01000023'}],
                   STAT=[{'FormID': STATIC}])
    assert rows == [(1, CELL)]


def test_a_masters_light_is_exempt_and_an_injected_base_is_not():
    """An id in a master's space is exempt only if a light really owns it."""
    masters = {'00000020': {'Signature': 'LIGH'},
               '00000021': {'Signature': 'STAT'}}
    by_type = {'REFR': [_refr('01000001', x=50000.0, base='00000020'),
                        _refr('01000002', x=50000.0, base='00000021'),
                        _refr('01000003', x=50000.0, base='00EE8440')],
               'ACTI': [{'FormID': '00EE8440'}]}
    assert IR.census(by_type, masters) == [(2, CELL)]


def test_rows_are_largest_first_then_by_cell_id():
    """Fed out of order, with a three-way tie behind the largest."""
    refrs = [_refr('01000001', x=40000.0, cell='0100000C'),
             _refr('01000002', x=40000.0, cell='0100000a'),
             _refr('01000003', x=40000.0, cell='0100000D'),
             _refr('01000004', x=40000.0, cell='0100000B'),
             _refr('01000005', x=40000.0, cell='0100000D')]
    assert _census(*refrs) == [(2, '0100000D'), (1, '0100000A'),
                               (1, '0100000B'), (1, '0100000C')]


def test_the_written_position_is_what_is_tested(monkeypatch):
    """A re-origined base, tilted and scaled, moves X past the limit."""
    tilted = _refr('01000001', x=29990.0, RotY=str(math.pi / 2),
                   **{'XSCL.Scale': '2.0'})
    assert _census(tilted) == []
    monkeypatch.setitem(items._BASE_ORIGIN_SHIFT, STATIC, -10.0)
    assert _census(tilted) == [(1, CELL)]


def test_the_census_changes_no_record():
    """Every record list is equal to its copy after a report."""
    by_type = {'REFR': [_refr('01000001', x=50000.0), _refr('01000002')],
               'CELL': [{'FormID': CELL, 'EditorID': 'BigCave'}],
               'LIGH': [{'FormID': '01000020'}]}
    before = copy.deepcopy(by_type)
    IR.report(by_type, {'00000020': {'Signature': 'LIGH'}})
    assert by_type == before


def test_report_names_own_and_master_cells(capsys):
    """A cell is named by the plugin's own record, else by its master's."""
    by_type = {'REFR': [_refr('01000001', x=50000.0),
                        _refr('01000002', y=50000.0),
                        _refr('00000003', x=50000.0, cell='00018093')],
               'CELL': [{'FormID': CELL.lower(), 'EditorID': 'BigCave'}]}
    masters = {'00018093': {'Signature': 'CELL', 'EditorID': 'MasterHall'},
               CELL: {'Signature': 'CELL', 'EditorID': 'OlderName'}}
    assert IR.report(by_type, masters) == 3
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith('  WARNING: 3 interior reference(s) in 2 cell(s)')
    assert '30000 units' in out[0]
    assert out[1:] == ['    BigCave (0100AAAA): 2',
                       '    MasterHall (00018093): 1']


def test_report_shows_five_cells_and_counts_the_rest(capsys):
    """Seven cells: the five largest are listed, two are summed up."""
    refrs = [_refr('0100%04X' % i, x=50000.0, cell='0100%04X' % (i // 2))
             for i in range(13)]
    assert IR.report({'REFR': refrs}) == 13
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 7
    assert out[1] == '    ? (01000000): 2'
    assert out[5] == '    ? (01000004): 2'
    assert out[6] == '    ... and 2 more cell(s)'


def test_report_with_exactly_five_cells_has_no_more_line(capsys):
    """The warning and five cell lines; nothing is left to sum up."""
    refrs = [_refr('0100%04X' % i, x=50000.0, cell='0100%04X' % i)
             for i in range(5)]
    assert IR.report({'REFR': refrs}) == 5
    assert len(capsys.readouterr().out.splitlines()) == 6


def test_report_is_silent_when_everything_is_in_range(capsys):
    assert IR.report({'REFR': [_refr('01000001', x=100.0)]}) == 0
    assert capsys.readouterr().out == ''


def test_the_census_runs_after_the_scans_it_depends_on():
    """It reads the origin-shift table and the REFR list both scans edit."""
    source = inspect.getsource(pipeline._run_prescans)
    census = source.index('interior_range.report(')
    assert source.index('_prescan_furniture_and_actors(') < census
    assert source.index('_prescan_leveled_actors(') < census


def test_the_prescans_end_with_the_census(monkeypatch, capsys):
    """The real `_run_prescans`, every scan stubbed out, prints the warning."""
    for name in dir(pipeline):
        if name.startswith('_prescan_') or name in ('_register_run_tables',
                                                    'plan_crime'):
            monkeypatch.setattr(pipeline, name, lambda *a, **k: (0, 0, 0))
    masters = {'00018093': {'Signature': 'CELL', 'EditorID': 'MasterHall'}}
    state = SimpleNamespace(
        by_type={'REFR': [_refr('00000003', x=50000.0, cell='00018093')]},
        ctx=SimpleNamespace(master_export=masters), writer=None,
        plugin_out_dir='', output_path='', output_root='', num_tes4_masters=0)
    pipeline._run_prescans(state, [], 0, '', lambda *_: None)
    out = capsys.readouterr().out
    assert 'WARNING: 1 interior reference(s) in 1 cell(s)' in out
    assert '    MasterHall (00018093): 1' in out
    state.ctx = None
    pipeline._run_prescans(state, [], 0, '', lambda *_: None)
    assert '    ? (00018093): 1' in capsys.readouterr().out
