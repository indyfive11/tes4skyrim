"""A LAND quadrant with alpha layers and no base layer keeps its alpha layers.

At most 5 alpha layers are kept per quadrant, picked by coverage and written
in the source blend order: the landscape shader has six colour slots (base +
5), and a sixth alpha layer was dropped by the engine instead of chosen here.

See: docs/commentary/tes5_import_world.md#land-quadrants-without-a-base-layer
See: docs/commentary/asset_convert_terrain.md#land-layer-run
"""

import struct
from pathlib import Path

import pytest

from asset_convert.lod.terrain_lod_textures import decode_land_layers
from tes5_import.base.text_reader import (get_formid, get_int, get_str,
                                          parse_file_range, remap_formid)
from tes5_import.overrides.builder import apply_changes
from tes5_import.overrides.diff import diff_records
from tes5_import.record_types.world import build_land_layers, convert_LAND

REAL_LAND = (Path(__file__).resolve().parent.parent
             / 'export' / 'Oblivion.esm' / 'LAND.txt')
RANGE_BYTES = 16 * 1024 * 1024
RANGES_TRIED = 8

GRASS, DIRT, ROCK, MOSS = 0x0002506E, 0x0001C8BE, 0x0012FBE2, 0x0012FBE3


def _subs(blob):
    """[(tag, payload)] of a packed subrecord run."""
    out, off = [], 0
    while off + 6 <= len(blob):
        size = struct.unpack_from('<H', blob, off + 4)[0]
        out.append((blob[off:off + 4], blob[off + 6:off + 6 + size]))
        off += 6 + size
    return out


def _fid(tex):
    """A texture id as the importer writes it."""
    return get_formid({'Texture': '%08X' % tex}, 'Texture')


def _rec(layers):
    """An exported LAND from [(type, quadrant, texture, {pos: opacity})].

    Shaped like a real export block: ATXT.Layer counts from 0 per quadrant.
    """
    rec = {'Signature': 'LAND', 'FormID': '00008F26', 'RecordFlags': '0',
           'DATA.Flags': '31', 'LayerCount': str(len(layers))}
    seen = {}
    for i, (kind, quad, tex, points) in enumerate(layers):
        pfx = f'Layer[{i}]'
        rec[f'{pfx}.Type'] = kind
        key = 'BTXT' if kind == 'BASE' else 'ATXT'
        rec[f'{pfx}.{key}.Texture'] = '%08X' % tex
        rec[f'{pfx}.{key}.Quadrant'] = str(quad)
        if kind == 'ALPHA':
            rec[f'{pfx}.ATXT.Layer'] = str(seen.setdefault(quad, 0))
            seen[quad] += 1
            rec[f'{pfx}.VTXTCount'] = str(len(points))
            for j, (pos, opacity) in enumerate(sorted(points.items())):
                rec[f'{pfx}.VT[{j}].Pos'] = str(pos)
                rec[f'{pfx}.VT[{j}].Opacity'] = repr(opacity)
    return rec


def _packed(quads):
    """The expected run from [(quadrant, base or None, [(texture, {pos: opacity})])]."""
    out = b''
    for quad, base, alphas in quads:
        if base is not None:
            out += b'BTXT' + struct.pack('<HIBBxx', 8, _fid(base), quad, 0)
        for idx, (tex, points) in enumerate(alphas):
            out += b'ATXT' + struct.pack('<HIBBH', 8, _fid(tex), quad, 0, idx)
            out += b'VTXT' + struct.pack('<H', 8 * len(points)) + b''.join(
                struct.pack('<HHf', pos, 0, op)
                for pos, op in sorted(points.items()))
    return out


def _baseless(rec):
    """Quadrants of an exported LAND with a textured ALPHA layer and no BASE."""
    kinds = {}
    for i in range(get_int(rec, 'LayerCount')):
        pfx = f'Layer[{i}]'
        if get_str(rec, f'{pfx}.Type') == 'BASE':
            quad = get_int(rec, f'{pfx}.BTXT.Quadrant')
            kinds.setdefault(quad, set()).add('BASE')
        elif get_formid(rec, f'{pfx}.ATXT.Texture'):
            quad = get_int(rec, f'{pfx}.ATXT.Quadrant')
            kinds.setdefault(quad, set()).add('ALPHA')
    return {quad for quad, kind in kinds.items() if kind == {'ALPHA'}}


def _with_bases(rec, quads):
    """A copy of an exported LAND with a BASE layer appended for each of `quads`."""
    out = dict(rec)
    count = get_int(rec, 'LayerCount')
    for quad in sorted(quads):
        out[f'Layer[{count}].Type'] = 'BASE'
        out[f'Layer[{count}].BTXT.Texture'] = '%08X' % GRASS
        out[f'Layer[{count}].BTXT.Quadrant'] = str(quad)
        count += 1
    out['LayerCount'] = str(count)
    return out


def _alpha_as_under_a_base(rec, quads):
    """The run `rec` gets once `quads` have a BASE, with those BTXT taken out."""
    return [(tag, payload)
            for tag, payload in _subs(build_land_layers(_with_bases(rec, quads)))
            if not (tag == b'BTXT' and payload[4] in quads)]


def _real_baseless_lands():
    """[(record, baseless quadrants)] from the first export range holding any."""
    size = REAL_LAND.stat().st_size
    for start in range(0, min(size, RANGES_TRIED * RANGE_BYTES), RANGE_BYTES):
        records = parse_file_range((str(REAL_LAND), start, start + RANGE_BYTES))
        found = [(rec, _baseless(rec)) for rec in records]
        found = [(rec, quads) for rec, quads in found if quads]
        if found:
            return found
    return []


def test_baseless_quadrant_keeps_its_alpha_layers():
    """Quadrant 2 has two alpha layers and no BASE: both are written, no BTXT."""
    rec = _rec([('BASE', 0, GRASS, {}),
                ('ALPHA', 0, DIRT, {5: 0.5}),
                ('BASE', 1, GRASS, {}),
                ('ALPHA', 2, ROCK, {0: 1.0, 17: 0.75, 288: 0.25}),
                ('ALPHA', 2, DIRT, {3: 0.5}),
                ('BASE', 3, GRASS, {})])

    assert build_land_layers(rec) == _packed([
        (0, GRASS, [(DIRT, {5: 0.5})]),
        (1, GRASS, []),
        (2, None, [(ROCK, {0: 1.0, 17: 0.75, 288: 0.25}), (DIRT, {3: 0.5})]),
        (3, GRASS, [])])


def test_land_with_no_base_anywhere_still_writes_a_run():
    """No BASE in any quadrant: the LOD decoder reads the alpha layers back."""
    rec = _rec([('ALPHA', 0, ROCK, {0: 1.0, 288: 0.25}),
                ('ALPHA', 3, DIRT, {16: 0.5})])

    got = decode_land_layers(build_land_layers(rec))

    assert got['base'] == {} and sorted(got['alpha']) == [0, 3]
    [(tex, grid)] = got['alpha'][0]
    assert tex == _fid(ROCK)
    assert grid[0, 0] == 1.0 and grid[16, 16] == 0.25
    assert [tex for tex, _grid in got['alpha'][3]] == [_fid(DIRT)]


def test_quadrant_with_a_base_is_written_as_before():
    """Beside a baseless quadrant, a BASE quadrant keeps its merge, order and cap.

    The five widest layers are kept and written in the source blend order.
    """
    cover = [0.3, 0.9, 0.1, 0.8, 0.2, 0.7, 0.6]
    busy = [('ALPHA', 1, 0x2000 + i, {pos: c for pos in range(4)})
            for i, c in enumerate(cover)]
    rec = _rec([('BASE', 1, GRASS, {})] + busy + [
        ('ALPHA', 1, 0, {9: 1.0}),
        ('ALPHA', 1, 0x2000, {0: 0.1, 200: 0.4}),
        ('ALPHA', 2, MOSS, {7: 1.0})])

    merged = {0: 0.3, 1: 0.3, 2: 0.3, 3: 0.3, 200: 0.4}
    kept = [(0x2001, 0.9), (0x2003, 0.8), (0x2005, 0.7), (0x2006, 0.6)]
    assert build_land_layers(rec) == _packed([
        (1, GRASS, [(0x2000, merged)]
         + [(tex, {pos: c for pos in range(4)}) for tex, c in kept]),
        (2, None, [(MOSS, {7: 1.0})])])


def test_baseless_quadrant_gets_the_layers_it_would_get_under_a_base():
    """Eight textures and a repeat, no BASE: same merge, order and cap as with one."""
    cover = [0.3, 0.9, 0.1, 0.8, 0.2, 0.7, 0.6, 0.5]
    rec = _rec([('ALPHA', 2, 0x2000 + i, {pos: c for pos in range(4)})
                for i, c in enumerate(cover)]
               + [('ALPHA', 2, 0x2002, {100: 1.0})])

    got = _subs(build_land_layers(rec))

    assert [tag for tag, _payload in got].count(b'ATXT') > 1
    assert got == _alpha_as_under_a_base(rec, {2})


def test_override_carries_a_baseless_quadrant():
    """A plugin that paints a quadrant with alpha only gets it in its override."""
    master = _rec([('BASE', 0, GRASS, {})])
    plugin = _rec([('BASE', 0, GRASS, {}),
                   ('ALPHA', 1, ROCK, {40: 0.5})])

    out, _applied, unmapped = apply_changes(
        convert_LAND(master), diff_records(master, plugin), plugin, master)

    assert unmapped == set()
    assert _subs(out[24:])[1:] == _subs(_packed([
        (0, GRASS, []), (1, None, [(ROCK, {40: 0.5})])]))


@pytest.mark.skipif(not REAL_LAND.exists(),
                    reason='Oblivion.esm export not available')
def test_real_exported_land_keeps_its_baseless_quadrants():
    """Real LAND blocks, read as the importer reads them, keep such quadrants."""
    found = _real_baseless_lands()
    if not found:
        pytest.skip('no baseless quadrant in the first export ranges')
    for rec, quads in found:
        got = _subs(build_land_layers(rec))
        written = {payload[4] for tag, payload in got if tag == b'ATXT'}
        assert quads <= written, rec['FormID']
        assert got == _alpha_as_under_a_base(rec, quads), rec['FormID']


def _rec_in_file_order(layers):
    """An exported LAND dict from [(type, quad, tex, {pos: opacity})]; ATXT.Layer is the file index."""
    rec = {'LayerCount': str(len(layers))}
    for i, (kind, quad, tex, vt) in enumerate(layers):
        p = f'Layer[{i}]'
        rec[f'{p}.Type'] = kind
        key = 'BTXT' if kind == 'BASE' else 'ATXT'
        rec[f'{p}.{key}.Texture'] = '%08X' % tex
        rec[f'{p}.{key}.Quadrant'] = str(quad)
        if kind == 'ALPHA':
            rec[f'{p}.ATXT.Layer'] = str(i)
            rec[f'{p}.VTXTCount'] = str(len(vt))
            for j, (pos, op) in enumerate(sorted(vt.items())):
                rec[f'{p}.VT[{j}].Pos'] = str(pos)
                rec[f'{p}.VT[{j}].Opacity'] = repr(op)
    return rec


def test_atxt_only_quadrant_is_emitted_in_vanilla_shape():
    """No BTXT, ATXT idx 0 then its VTXT, quadrants ascending."""
    rec = _rec_in_file_order([('BASE', 0, 0x1111, {}),
                ('ALPHA', 2, 0x2222, {5: 0.75}),
                ('ALPHA', 0, 0x3333, {1: 0.5})])

    subs = _subs(build_land_layers(rec))

    tags = [(t, p[4]) for t, p in subs if t in (b'BTXT', b'ATXT')]
    assert tags == [(b'BTXT', 0), (b'ATXT', 0), (b'ATXT', 2)]
    i = next(k for k, (t, p) in enumerate(subs) if t == b'ATXT' and p[4] == 2)
    tex, quad, _u, layer = struct.unpack('<IBBH', subs[i][1])
    assert (tex, quad, layer) == (remap_formid(0x2222), 2, 0)
    assert subs[i + 1][0] == b'VTXT'
    assert struct.unpack('<HHf', subs[i + 1][1]) == (5, 0, 0.75)


def test_atxt_only_quadrant_round_trips_through_the_lod_decoder():
    """decode_land_layers sees alpha with no base for that quadrant."""
    rec = _rec_in_file_order([('ALPHA', 3, 0x4444, {0: 1.0, 288: 0.25})])

    got = decode_land_layers(build_land_layers(rec))

    assert got['base'] == {}
    [(tex, grid)] = got['alpha'][3]
    assert tex == remap_formid(0x4444)
    assert grid[0, 0] == 1.0 and grid[16, 16] == 0.25


def test_at_most_five_alpha_layers_the_top_five_by_coverage():
    """Seven textures in a quadrant: the two with least coverage go."""
    cover = [0.9, 0.1, 0.8, 0.7, 0.05, 0.6, 0.5]
    rec = _rec_in_file_order([('BASE', 1, 0x1000, {})] + [
        ('ALPHA', 1, 0x2000 + i, {p: c for p in range(10)})
        for i, c in enumerate(cover)])

    subs = _subs(build_land_layers(rec))

    atxt = [struct.unpack('<IBBH', p) for t, p in subs if t == b'ATXT']
    assert len(atxt) == 5
    kept = {tex for tex, _q, _u, _l in atxt}
    assert kept == {remap_formid(0x2000 + i) for i in (0, 2, 3, 5, 6)}
    assert [layer for *_x, layer in atxt] == [0, 1, 2, 3, 4]


def test_repeated_texture_merges_by_max_opacity():
    """Two layers of one texture in a quadrant become one, max per vertex."""
    rec = _rec_in_file_order([('BASE', 0, 0x1, {}),
                ('ALPHA', 0, 0x5, {1: 0.2, 2: 0.9}),
                ('ALPHA', 0, 0x5, {1: 0.6})])

    [(tex, grid)] = decode_land_layers(build_land_layers(rec))['alpha'][0]

    assert grid[0, 1] == 0.6 and abs(grid[0, 2] - 0.9) < 1e-6


def test_kept_layers_are_written_in_source_blend_order():
    """Coverage picks the survivors; the author's ATXT layer order blends them.

    Re-sorting by coverage changed the blend order of 92% of Oblivion's
    multi-layer quadrants.
    """
    cover = [0.1, 0.9, 0.5, 0.05, 0.7, 0.3, 0.2]
    rec = _rec_in_file_order([('BASE', 0, 0x1000, {})] + [
        ('ALPHA', 0, 0x2000 + i, {p: c for p in range(10)})
        for i, c in enumerate(cover)])

    subs = _subs(build_land_layers(rec))

    order = [struct.unpack('<IBBH', p)[0] for t, p in subs if t == b'ATXT']
    assert order == [remap_formid(0x2000 + i) for i in (1, 2, 4, 5, 6)]


def test_a_merged_texture_blends_at_its_first_layer():
    """A texture painted twice keeps the lower of its two layer indices."""
    rec = _rec_in_file_order([('BASE', 0, 0x1, {}),
                ('ALPHA', 0, 0x3A00, {1: 0.9}),
                ('ALPHA', 0, 0x3B00, {1: 0.9, 2: 0.9, 5: 0.9}),
                ('ALPHA', 0, 0x3A00, {3: 0.9})])

    subs = _subs(build_land_layers(rec))

    order = [struct.unpack('<IBBH', p)[0] for t, p in subs if t == b'ATXT']
    assert order == [remap_formid(0x3A00), remap_formid(0x3B00)]


#: Vertices well inside a quadrant (rows and columns 2-14) and on its south edge (row 0, columns 2-14).
INNER = [r * 17 + c for r in range(2, 15) for c in range(2, 15)]
SOUTH = [c for c in range(2, 15)]


@pytest.fixture
def colours():
    """Set land texture colours by texture id; cleared after the test."""
    from tes5_import.record_types import land_colours
    land_colours._COLOURS.clear()

    def put(**by_tex):
        for tex, rgb in by_tex.items():
            land_colours._COLOURS[_fid(int(tex[1:], 16))] = rgb
    yield put
    land_colours._COLOURS.clear()


def _six(extra):
    """Base 0x1000, four big inner layers 0x2000-0x2003, then `extra`."""
    big = [('ALPHA', 0, 0x2000 + i, {p: 1.0 for p in INNER[i * 30:(i + 1) * 30]}) for i in range(4)]
    return _rec_in_file_order([('BASE', 0, 0x1000, {})] + big + extra)


def _atxt(rec):
    """{texture: {pos: opacity}} of the written alpha layers."""
    out, cur = {}, None
    for tag, payload in _subs(build_land_layers(rec)):
        if tag == b'ATXT':
            cur = out.setdefault(struct.unpack_from('<I', payload)[0], {})
        elif tag == b'VTXT':
            for k in range(0, len(payload), 8):
                pos, _u, op = struct.unpack_from('<HHf', payload, k)
                cur[pos] = op
    return out


def test_the_vertices_a_quadrant_shares():
    """A corner is drawn by four quadrants, an edge vertex by two, the rest by one."""
    from tes5_import.record_types.world import _quadrants_drawing
    assert [_quadrants_drawing(p) for p in (0, 16, 272, 288)] == [4, 4, 4, 4]
    assert [_quadrants_drawing(p) for p in (1, 17, 33, 287)] == [2, 2, 2, 2]
    assert _quadrants_drawing(18) == 1


def test_a_layer_painted_on_the_edge_is_kept_before_an_inner_one():
    """Six alpha layers: of the two small ones, the one the neighbour draws too survives.

    Less paint of its own (6 vertices against 8), but each edge vertex is drawn
    on both sides; dropping it cut the paint off on the border.
    """
    rec = _six([('ALPHA', 0, 0x3000, {p: 1.0 for p in INNER[120:128]}),
                ('ALPHA', 0, 0x3001, {p: 1.0 for p in SOUTH[:6]})])
    kept = set(_atxt(rec))
    assert remap_formid(0x3001) in kept and remap_formid(0x3000) not in kept


def test_a_dropped_layer_folds_into_the_kept_layer_most_like_it(colours):
    """Its paint goes, by max, to the nearest colour; nothing else changes."""
    colours(t1000=(0, 0, 255), t2000=(200, 40, 40), t2001=(40, 200, 40), t2002=(40, 40, 200),
            t2003=(120, 120, 120), t3000=(190, 50, 50), t3001=(10, 10, 10))
    rec = _six([('ALPHA', 0, 0x3001, {p: 1.0 for p in INNER[120:150]}),
                ('ALPHA', 0, 0x3000, {p: 0.6 for p in INNER[150:152]})])
    got = _atxt(rec)
    red = got[remap_formid(0x2000)]
    assert remap_formid(0x3000) not in got
    assert red[INNER[150]] == pytest.approx(0.6) and red[INNER[0]] == pytest.approx(1.0)
    assert INNER[150] not in got[remap_formid(0x2001)]


def test_a_dropped_layer_nearest_the_base_is_left_to_the_base(colours):
    """When the base looks most like it, nothing is folded: the base shows there."""
    colours(t1000=(190, 50, 50), t2000=(40, 200, 40), t2001=(40, 40, 200), t2002=(200, 200, 40),
            t2003=(120, 120, 120), t3000=(200, 40, 40), t3001=(10, 10, 10))
    rec = _six([('ALPHA', 0, 0x3001, {p: 1.0 for p in INNER[120:150]}),
                ('ALPHA', 0, 0x3000, {p: 0.6 for p in INNER[150:152]})])
    got = _atxt(rec)
    assert all(INNER[150] not in grid for grid in got.values())


def test_a_dropped_layer_without_a_colour_is_dropped_as_before(colours):
    """A rule that cannot tell leaves the old behaviour."""
    colours(t1000=(0, 0, 255), t2000=(200, 40, 40), t2001=(40, 200, 40), t2002=(40, 40, 200),
            t2003=(120, 120, 120), t3001=(10, 10, 10))
    rec = _six([('ALPHA', 0, 0x3001, {p: 1.0 for p in INNER[120:150]}),
                ('ALPHA', 0, 0x3000, {p: 0.6 for p in INNER[150:152]})])
    assert all(INNER[150] not in grid for grid in _atxt(rec).values())


def test_a_quadrant_within_the_cap_is_written_as_before(colours):
    """Five alpha layers or fewer: the same bytes with colours known or not."""
    rec = _six([('ALPHA', 0, 0x3000, {p: 0.5 for p in SOUTH})])
    plain = build_land_layers(rec)
    colours(t1000=(0, 0, 255), t2000=(200, 40, 40), t2001=(40, 200, 40), t2002=(40, 40, 200),
            t2003=(120, 120, 120), t3000=(190, 50, 50))
    assert build_land_layers(rec) == plain


def test_the_colour_index_reads_each_diffuse(tmp_path):
    """The mean colour of an LTEX's image under the texture roots; a missing one has none."""
    from PIL import Image
    from tes5_import.record_types import land_colours
    (tmp_path / 'Sub').mkdir()
    Image.new('RGB', (8, 8), (30, 60, 90)).save(tmp_path / 'Sub' / 'Mud01.png', format='PNG')
    (tmp_path / 'Sub' / 'Mud01.png').rename(tmp_path / 'Sub' / 'Mud01.dds')
    by_type = {'LTEX': [{'Signature': 'LTEX', 'FormID': '00001234', 'ICON': 'sub\\mud01.dds'},
                        {'Signature': 'LTEX', 'FormID': '00001235', 'ICON': 'gone.dds'}]}
    try:
        assert land_colours.load_land_colours(by_type, None, [tmp_path]) == 1
        assert land_colours.land_colour(remap_formid(0x1234)) == pytest.approx((30, 60, 90))
        assert land_colours.land_colour(remap_formid(0x1235)) is None
    finally:
        land_colours._COLOURS.clear()
