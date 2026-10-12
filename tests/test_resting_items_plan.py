"""A single-box collision is replaced only where the plugin places items inside it.

See: docs/commentary/asset_convert_collision.md#morrowind-stand-in-boxes
"""

import math
import struct

from asset_convert.collision import resting_items_morrowind, resting_items_plan
from asset_convert.nif import fixture_plan
from tes4_export.morrowind_patch import PATCH_NAME
from tes4_export.tes3_reader import Tes3Record
from tes4_export.tes4_reader import Subrecord

#: The shelf's box in its own frame: 100 x 40 x 200, centered on the origin.
_HALF = (50.0, 20.0, 100.0)


def _box_tris():
    """The 12 triangles of the shelf's box."""
    hx, hy, hz = _HALF
    c = [(x, y, z) for x in (-hx, hx) for y in (-hy, hy) for z in (-hz, hz)]
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6),
             (0, 2, 6, 4), (1, 5, 7, 3)]
    return [tri for a, b, d, e in quads
            for tri in ((c[a], c[b], c[d]), (c[a], c[d], c[e]))]


def _write(path, records):
    """One export text file holding `records`."""
    body = ''.join('---RECORD_BEGIN---\n' + ''.join(
        f'{k}={v}\n' for k, v in rec.items()) + '---RECORD_END---\n\n'
        for rec in records)
    path.write_text(body, encoding='utf-8')


def _plugin(root, name, masters=(), **files):
    """Export dump `root/name` with a header naming `masters` and `files`."""
    d = root / name
    d.mkdir()
    (d / '_HEADER.txt').write_text(''.join(
        f'Master[{i}]={m}\n' for i, m in enumerate(masters)), encoding='utf-8')
    for fname, records in files.items():
        _write(d / f'{fname}.txt', records)


def _refs(shelf_id, book_id, item_pos):
    """A shelf, rotated 90 degrees and scaled 2x, and a book at `item_pos`."""
    shelf = {'NAME': shelf_id, 'ParentCELL': '0000C001', 'PosX': '1000',
             'PosY': '500', 'PosZ': '0', 'RotX': '0', 'RotY': '0',
             'RotZ': str(math.pi / 2), 'XSCL.Scale': '2.0'}
    book = {'NAME': book_id, 'ParentCELL': '0000C001',
            'PosX': str(item_pos[0]), 'PosY': str(item_pos[1]),
            'PosZ': str(item_pos[2])}
    return [shelf, book]


def _export(tmp_path, item_pos):
    """One plugin defining and placing the shelf and the book."""
    _plugin(tmp_path, 'P.esm',
            STAT=[{'FormID': '00000A01', 'Model.MODL': 'f\\\\shelf.nif'}],
            BOOK=[{'FormID': '00000B01', 'Model.MODL': 'm\\\\book.nif'}],
            REFR=_refs('00000A01', '00000B01', item_pos))


def _stocked(tmp_path, item_pos, export=_export, plugin='P.esm') -> bool:
    """Whether `plugin`'s shelf box holds the book placed at `item_pos`."""
    export(tmp_path, item_pos)
    path, _ = resting_items_plan.write_index(tmp_path, plugin)
    resting_items_plan._LOADED.pop(path, None)
    plan = {fixture_plan.FIXTURE_KEY: {'f/shelf.nif'},
            resting_items_plan.RESTING_KEY: path}
    fixture_plan.latch_fixture_model(plan, tmp_path / 'm' / 'f' / 'shelf.nif',
                                     tmp_path / 'm')
    try:
        return resting_items_plan.items_rest_inside(_box_tris())
    finally:
        fixture_plan.latch_fixture_model(None, '', '')


def test_item_inside_the_box_is_found(tmp_path):
    """Scaled 2x and turned 90 degrees, the box spans x 960..1040, y 400..600."""
    assert _stocked(tmp_path, (1030.0, 420.0, 150.0))


def test_item_on_top_of_the_box_is_not(tmp_path):
    """Above the box's top face (z 200) is outside it."""
    assert not _stocked(tmp_path, (1000.0, 500.0, 205.0))


def test_rotation_is_applied(tmp_path):
    """Inside the UNROTATED footprint (x 900..1100) but outside the turned one."""
    assert not _stocked(tmp_path, (1080.0, 500.0, 0.0))


def _patch_export(tmp_path, item_pos):
    """P.esm defines the shelf; only its dependent places it, with a master's book.

    The shelf is 01000A01 in P.esm but 02000A01 in the dependent, whose
    master list puts another plugin between the two.
    """
    _plugin(tmp_path, 'Base.esm',
            BOOK=[{'FormID': '00000B01', 'Model.MODL': 'm\\\\book.nif'}])
    _plugin(tmp_path, 'Other.esm', ['Base.esm'])
    _plugin(tmp_path, 'P.esm', ['Base.esm'],
            STAT=[{'FormID': '01000A01', 'Model.MODL': 'f\\\\shelf.nif'}])
    _plugin(tmp_path, 'Dep.esm', ['Base.esm', 'Other.esm', 'P.esm'],
            REFR=_refs('02000A01', '00000B01', item_pos))


def test_a_dependents_placements_are_read(tmp_path):
    """The shelf's owner places nothing; its dependent stocks the shelf."""
    assert _stocked(tmp_path, (1030.0, 420.0, 150.0), _patch_export)


def test_a_dependents_item_outside_is_not(tmp_path):
    """The dependent's book on top of the shelf leaves the box closed."""
    assert not _stocked(tmp_path, (1000.0, 500.0, 205.0), _patch_export)


def _sub(sig, *values, fmt=None):
    """One TES3 subrecord: a NUL-terminated string, or `values` packed by `fmt`."""
    data = struct.pack(fmt, *values) if fmt else values[0].encode() + b'\0'
    return Subrecord(sig, data)


def _ref(ref_num, record_id, pos, rot=(0.0, 0.0, 0.0), scale=None):
    """The FRMR run of one TES3 cell reference."""
    subs = [_sub('FRMR', ref_num, fmt='<I'), _sub('NAME', record_id)]
    if scale is not None:
        subs.append(_sub('XSCL', scale, fmt='<f'))
    return subs + [_sub('DATA', *pos, *rot, fmt='<6f')]


def _source_esms(item_pos):
    """Vanilla source records: the book, and one interior holding the shelf and it."""
    cell = [_sub('NAME', 'Shop'), _sub('DATA', 1, 0, 0, fmt='<iii'),
            *_ref(1, 'Furn_Shelf', (1000.0, 500.0, 0.0), (0.0, 0.0, math.pi / 2), 2.0),
            *_ref(2, 'Book_A', item_pos)]
    return [Tes3Record('BOOK', 0, [], record_id='Book_A'),
            Tes3Record('CELL', 0, cell, record_id='Shop')]


def _patch_stocked(tmp_path, monkeypatch, item_pos) -> bool:
    """Whether the patch's shelf holds a book its source ESMs place at `item_pos`."""
    def export(root, _pos):
        _plugin(root, PATCH_NAME, ['Oblivion.esm', 'Morrowind_ob.esm'],
                STAT=[{'FormID': '029EE724', 'EditorID': 'Furn_Shelf',
                       'Model.MODL': 'f\\\\shelf.nif'}])
    monkeypatch.setattr(resting_items_morrowind, '_source_records',
                        lambda _root: _source_esms(item_pos))
    return _stocked(tmp_path, item_pos, export, PATCH_NAME)


def test_the_patch_reads_its_source_esms(tmp_path, monkeypatch):
    """No export of Morrowind exists; the ESM's own placement stocks the shelf."""
    assert _patch_stocked(tmp_path, monkeypatch, (1030.0, 420.0, 150.0))


def test_the_patch_source_item_outside_is_not(tmp_path, monkeypatch):
    """The ESM's book on top of the shelf leaves the box closed."""
    assert not _patch_stocked(tmp_path, monkeypatch, (1000.0, 500.0, 205.0))
