"""A swimmer the path grid left without navmesh gets an all-water sheet.

Oblivion's swimmers need no path grid; in Skyrim the engine moves an actor
with no navmesh near onto the nearest one, which put creatures placed deep
in a lake on its walkways.
See: docs/commentary/tes5_import_navmesh.md#water-sheets
"""

import math
import struct
import subprocess
import sys
import types
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tes5_import import pipeline_records
from tes5_import.navmesh import from_pgrd, pool
from tes5_import.navmesh import water_sheet as ws
from tes5_import.navmesh.edge_links import NavMeshView, build_edge_links, extract_nvnm
from tes5_import.navmesh.navi import build_navi_record
from tools.navmesh import check as navcheck

WRLD = 0x00001000
DEEP, DRY = -504.0, 104.0
SWIMS, WALKS = 0x10, 0x40
FISH, CRAB, WOLF = 0x00005001, 0x00005002, 0x00005003


class FakeWriter:
    """The plugin writer's two facts the rule reads: its index and a stable id per key."""

    own_index = 0

    def __init__(self):
        self.keys = {}

    def derive_formid(self, site, key):
        return self.keys.setdefault((site, repr(key)), 0x00800000 + len(self.keys))


def _vhgt(height):
    """LAND VHGT hex whose corner (col, row) stands at `height(col, row)`, a multiple of 8."""
    rows = [[height(c, r) / 8.0 for c in range(33)] for r in range(33)]
    deltas, prev_start = [], rows[0][0]
    for r, row in enumerate(rows):
        deltas.append(row[0] - prev_start if r else 0.0)
        deltas += [row[c] - row[c - 1] for c in range(1, 33)]
        prev_start = row[0]
    return (struct.pack('<f', rows[0][0]) + struct.pack('<1089b', *(int(d) for d in deltas))).hex()


def _grid(height):
    """The decoded 33x33 heights of `_vhgt(height)`."""
    from tes5_import.navmesh.world import decode_vhgt
    return decode_vhgt(_vhgt(height))


def _cell(fid, wrld=0, x=0, y=0, water=0.0, flags=2):
    """A CELL export record; with `wrld` an exterior one at grid (x, y)."""
    rec = {'Signature': 'CELL', 'FormID': '%08X' % fid, 'DATA.Flags': str(flags)}
    if water is not None:
        rec['XCLW.WaterHeight'] = repr(water)
    if wrld:
        rec.update({'ParentWRLD': '%08X' % wrld, 'XCLC.X': str(x), 'XCLC.Y': str(y)})
    return rec


def _land(fid, cell, height):
    return {'Signature': 'LAND', 'FormID': '%08X' % fid,
            'ParentCELL': '%08X' % cell, 'VHGT': _vhgt(height)}


def _crea(fid, flags):
    return {'Signature': 'CREA', 'FormID': '%08X' % fid, 'ACBS.Flags': str(flags)}


def _lvlc(fid, entries):
    """A leveled creature list naming `entries`."""
    rec = {'Signature': 'LVLC', 'FormID': '%08X' % fid, 'EntryCount': str(len(entries))}
    for i, entry in enumerate(entries):
        rec['Entry[%d].FormID' % i] = '%08X' % entry
    return rec


def _acre(fid, cell, base, x, y, z, flags=0):
    return {'Signature': 'ACRE', 'FormID': '%08X' % fid, 'ParentCELL': '%08X' % cell,
            'NAME': '%08X' % base, 'RecordFlags': str(flags),
            'PosX': repr(float(x)), 'PosY': repr(float(y)), 'PosZ': repr(float(z))}


def _creatures():
    return [_crea(FISH, SWIMS), _crea(CRAB, SWIMS | WALKS), _crea(WOLF, WALKS)]


def _sea(cell=0x2000, x=0, y=0, base=FISH, pos=(2048.0, 2048.0, -200.0), height=None):
    """One exterior cell with a sea floor, and one creature placed in it."""
    return {
        'CELL': [_cell(cell, WRLD, x, y)],
        'LAND': [_land(0x3000 + (cell & 0xFF), cell, height or (lambda c, r: DEEP))],
        'CREA': _creatures(),
        'ACRE': [_acre(0x6000 + (cell & 0xFF), cell, base, *pos)],
    }


def _soup(*tris):
    return np.asarray(tris, dtype=np.float64).reshape(-1, 3, 3)


def _tri(x, y, z, size=64.0):
    """A small level triangle whose first corner is (x, y, z)."""
    return [(x, y, z), (x + size, y, z), (x, y + size, z)]


def _entry(verts, tris, cell=0x2000, fid=0x00700001):
    """A path-grid navmesh as the precompute caches it."""
    return (b'NAVM-bytes', {'fid': fid, 'cell_fid': cell,
                            'geometry': (verts, tris, [])})


def _run(by_type, cache=None, master_export=None):
    """(sheets made, cache) of the rule run over `by_type`."""
    cache = {} if cache is None else cache
    made = pool.add_water_sheets(cache, by_type, FakeWriter(), {}, master_export)
    return made, cache


def _faces_up(verts, tris):
    v = np.asarray(verts)
    return all(float(np.cross(v[b] - v[a], v[c] - v[a])[2]) > 0 for a, b, c in tris)



NO_HEIGHT = -2147483648.0
SEA = {'Signature': 'WRLD', 'FormID': '%08X' % WRLD}
DEEP_WORLD = dict(SEA, **{'DNAM.DefaultWaterHeight': '-2000.0'})


@pytest.mark.parametrize('cell, want', [
    (_cell(1, water=-400.0), -400.0),
    (_cell(1, water=-400.0, flags=0), None),
    (_cell(1, water=None), 0.0),
    (_cell(1, water=NO_HEIGHT), None),
    (dict(_cell(1), **{'XCLW.WaterHeight': 'deep'}), None),
    (dict(_cell(1), **{'XCLW.WaterHeight': 'nan'}), None),
    (None, None),
])
def test_an_interiors_water_height(cell, want):
    assert ws.water_height(cell) == want


@pytest.mark.parametrize('cell, world, want', [
    (_cell(1, WRLD, water=-400.0), SEA, -400.0),
    (_cell(1, WRLD, water=-400.0), DEEP_WORLD, -400.0),
    (_cell(1, WRLD, water=None), SEA, ws.SEA_LEVEL),
    (_cell(1, WRLD, water=NO_HEIGHT), SEA, ws.SEA_LEVEL),
    (_cell(1, WRLD, water=None), DEEP_WORLD, -2000.0),
    (_cell(1, WRLD, water=NO_HEIGHT), DEEP_WORLD, -2000.0),
    (_cell(1, WRLD, water=None, flags=0), SEA, None),
    (_cell(1, WRLD, water=None), {}, ws.SEA_LEVEL),
])
def test_an_exterior_cell_without_a_stated_height_has_its_worlds_default(cell, world, want):
    assert ws.water_height(cell, world) == want


@pytest.mark.parametrize('cell', [
    _cell(1, water=-400.0), _cell(1, water=-400.0, flags=0), _cell(1, water=None),
    _cell(1, water=12.5, flags=3), _cell(1, water=NO_HEIGHT),
])
def test_flag_height_is_the_height_the_path_grid_meshes_are_flagged_with(cell):
    frame = from_pgrd._cell_frame({'ParentCELL': '00000001'}, cell)
    assert ws.flag_height(cell) == frame[4]


@pytest.mark.parametrize('stated', ['nan', 'inf', '-inf', 'deep'])
def test_flag_height_takes_no_height_that_is_not_a_finite_number(stated):
    assert ws.flag_height(dict(_cell(1), **{'XCLW.WaterHeight': stated})) is None


@pytest.mark.parametrize('flags, want', [
    (SWIMS, (True, False)), (SWIMS | WALKS, (True, True)), (WALKS, (False, True)),
    (0, (False, False)),
])
def test_a_creature_swims_and_walks_by_its_own_flags(flags, want):
    assert ws.swim_kind(FISH, {FISH: _crea(FISH, flags)}, {}) == want


def _kind(entries, extra_lists=()):
    """swim_kind of a list holding `entries`, among the three test creatures."""
    creatures = {int(c['FormID'], 16): c for c in _creatures()}
    lists = {0x7000: _lvlc(0x7000, entries)}
    lists.update({int(rec['FormID'], 16): rec for rec in extra_lists})
    return ws.swim_kind(0x7000, creatures, lists)


def test_a_list_swims_when_every_creature_in_it_swims():
    assert _kind([FISH, FISH]) == (True, False)


def test_a_list_walks_when_any_creature_in_it_walks():
    assert _kind([FISH, CRAB]) == (True, True)


def test_a_list_with_one_creature_that_cannot_swim_does_not_swim():
    assert _kind([FISH, WOLF]) == (False, True)


def test_a_list_naming_something_unknown_cannot_be_told():
    assert _kind([FISH, 0x9999]) is None


def test_an_empty_list_cannot_be_told():
    assert _kind([]) is None


def test_a_list_inside_a_list_is_followed():
    assert _kind([0x7001], [_lvlc(0x7001, [FISH, CRAB])]) == (True, True)


def test_a_list_that_names_itself_cannot_be_told():
    assert _kind([0x7000]) is None


def test_an_unknown_base_cannot_be_told():
    assert ws.swim_kind(0x9999, {}, {}) is None



FISH_AT = (0.0, 0.0, -200.0, False)
CRAB_AT = (0.0, 0.0, -200.0, True)


def test_every_swimmer_is_stranded_in_a_cell_without_navmesh():
    assert ws.stranded([FISH_AT, CRAB_AT], _soup(), 0.0) == [FISH_AT, CRAB_AT]


def test_a_walker_is_served_by_a_dry_triangle_within_reach():
    assert ws.stranded([CRAB_AT], _soup(_tri(ws.REACH - 1, 0, 50)), 0.0) == []


def test_a_walker_is_stranded_when_the_nearest_triangle_is_beyond_reach():
    assert ws.stranded([CRAB_AT], _soup(_tri(ws.REACH + 1, 0, 50)), 0.0) == [CRAB_AT]


def test_reach_is_measured_to_the_nearest_point_of_the_triangle():
    assert ws.stranded([CRAB_AT], _soup(_tri(ws.REACH, 0, 50)), 0.0) == []


def test_reach_is_measured_in_plan():
    assert ws.stranded([CRAB_AT], _soup(_tri(10, 10, 9000.0)), 0.0) == []


def test_a_swimmer_inside_a_triangle_in_plan_is_at_distance_zero():
    big = [(-5000.0, -5000.0, 50.0), (5000.0, -5000.0, 50.0), (0.0, 9000.0, 50.0)]
    assert ws.stranded([CRAB_AT], _soup(big), 0.0) == []


def test_a_triangle_across_the_water_line_is_wet_as_the_path_grid_flags_it():
    """`from_pgrd` flags a triangle by its centre height; the stranded test judges it the same."""
    mostly_under = [(100.0, 0.0, -90.0), (164.0, 0.0, -90.0), (100.0, 64.0, 60.0)]
    mostly_over = [(100.0, 0.0, 90.0), (164.0, 0.0, 90.0), (100.0, 64.0, -60.0)]
    for tri in (mostly_under, mostly_over):
        flagged = from_pgrd._compute_water_flags(tri, [(0, 1, 2)], 0.0)[0] != 0
        assert (ws.stranded([FISH_AT], _soup(tri), 0.0) == []) is flagged
    assert ws.stranded([FISH_AT], _soup(mostly_under), 0.0) == []
    assert ws.stranded([FISH_AT], _soup(mostly_over), 0.0) == [FISH_AT]


def test_a_swimmer_inside_a_triangle_wound_the_other_way_is_at_distance_zero():
    big = [(-5000.0, -5000.0, 50.0), (0.0, 9000.0, 50.0), (5000.0, -5000.0, 50.0)]
    assert ws.stranded([CRAB_AT], _soup(big), 0.0) == []


def test_a_creature_that_only_swims_is_not_served_by_a_dry_triangle():
    assert ws.stranded([FISH_AT], _soup(_tri(100, 0, 50)), 0.0) == [FISH_AT]


def test_a_creature_that_only_swims_is_served_by_a_triangle_under_the_water():
    assert ws.stranded([FISH_AT], _soup(_tri(100, 0, -300)), 0.0) == []


def test_a_triangle_under_the_water_of_a_cell_that_flags_none_does_not_serve_a_fish():
    assert ws.stranded([FISH_AT], _soup(_tri(100, 0, -300)), None) == [FISH_AT]


def test_a_cell_that_flags_no_triangle_still_serves_a_walker():
    assert ws.stranded([CRAB_AT], _soup(_tri(100, 0, -300)), None) == []


def test_a_water_triangle_beyond_reach_does_not_serve_it():
    assert ws.stranded([FISH_AT], _soup(_tri(ws.REACH + 1, 0, -300)), 0.0) == [FISH_AT]



MID = (2048.0, 2048.0, -200.0, False)


def _terrain(height, swimmers=(MID,), soup=None, ox=0.0, oy=0.0, water=0.0):
    return ws.terrain_sheet(_grid(height), ox, oy, water,
                            _soup() if soup is None else soup, list(swimmers))


def test_a_flat_sea_floor_is_laid_whole():
    """All 256 squares of a cell wholly under water, at the floor's own height."""
    verts, tris = _terrain(lambda c, r: DEEP)
    assert (len(verts), len(tris)) == (17 * 17, 2 * 16 * 16)
    assert {v[2] for v in verts} == {DEEP}


def test_every_sheet_triangle_faces_up():
    verts, tris = _terrain(lambda c, r: DEEP)
    assert _faces_up(verts, tris)


def test_neighbouring_squares_share_their_corners():
    """One connected sheet: its only open edges are the cell's rim."""
    verts, tris = _terrain(lambda c, r: DEEP)
    adj = from_pgrd.compute_adjacency(tris)
    open_edges = sum(1 for a in adj for n in a if n == -1)
    assert open_edges == 4 * 16
    assert len(set(verts)) == len(verts)


def test_the_sheet_stays_inside_its_cell():
    """A cell at grid (-3, 2) keeps its sheet on its own 4096 square."""
    ox, oy = -3 * 4096.0, 2 * 4096.0
    verts, _tris = _terrain(lambda c, r: DEEP, [(ox + 2048, oy + 2048, -200.0, False)],
                            ox=ox, oy=oy)
    assert min(v[0] for v in verts) == ox and max(v[0] for v in verts) == ox + 4096
    assert min(v[1] for v in verts) == oy and max(v[1] for v in verts) == oy + 4096


def test_only_squares_wholly_under_water_are_laid():
    """A square with a corner on the dry half is not laid."""
    verts, tris = _terrain(lambda c, r: DRY if c >= 16 else DEEP, [(512.0, 2048.0, -200.0, False)])
    assert len(tris) == 2 * 7 * 16
    assert max(v[0] for v in verts) == 7 * ws.SQUARE


def test_a_middle_corner_above_the_water_drops_its_square():
    """The nine LAND corners count, not the four the sheet uses."""
    whole = len(_terrain(lambda c, r: DEEP)[1])
    poked = len(_terrain(lambda c, r: DRY if (c, r) == (5, 5) else DEEP)[1])
    assert poked == whole - 2


def test_an_edge_corner_above_the_water_drops_both_squares_it_bounds():
    """A LAND corner on a shared edge belongs to both squares."""
    whole = len(_terrain(lambda c, r: DEEP)[1])
    poked = len(_terrain(lambda c, r: DRY if (c, r) == (6, 5) else DEEP)[1])
    assert poked == whole - 4


def test_the_sheet_follows_the_floor():
    verts, _tris = _terrain(lambda c, r: DEEP - 8.0 * c)
    assert all(v[2] == DEEP - 8.0 * (v[0] / 128.0) for v in verts)


def test_a_floor_at_the_water_height_is_not_under_it():
    assert _terrain(lambda c, r: 0.0) == ([], [])


def test_a_square_an_existing_triangle_touches_is_left_out():
    """The path-grid navmesh keeps its ground: one square, two triangles."""
    whole = len(_terrain(lambda c, r: DEEP)[1])
    cut = len(_terrain(lambda c, r: DEEP, soup=_soup(_tri(300.0, 300.0, -400.0, 100.0)))[1])
    assert cut == whole - 2


def test_a_cut_is_placed_from_the_cells_own_origin():
    """In a cell away from the world origin the triangle still takes the square it lies in."""
    ox, oy = -3 * 4096.0, 2 * 4096.0
    here = [(ox + 2048.0, oy + 2048.0, -200.0, False)]
    whole = len(_terrain(lambda c, r: DEEP, here, ox=ox, oy=oy)[1])
    soup = _soup(_tri(ox + 300.0, oy + 300.0, -400.0, 100.0))
    verts, tris = _terrain(lambda c, r: DEEP, here, soup=soup, ox=ox, oy=oy)
    assert len(tris) == whole - 2
    assert sum(1 for v in verts if (v[0], v[1]) == (ox + 256.0, oy + 256.0)) == 1


def test_a_triangle_touching_a_square_only_at_its_edge_still_takes_it():
    """Touching counts: both squares on the shared edge go."""
    edge = [(256.0, 300.0, 50.0), (200.0, 300.0, 50.0), (200.0, 400.0, 50.0)]
    whole = len(_terrain(lambda c, r: DEEP)[1])
    assert len(_terrain(lambda c, r: DEEP, soup=_soup(edge))[1]) == whole - 4


def test_a_triangle_over_several_squares_takes_each_one_it_crosses():
    """A thin triangle 1,000 long crosses five squares."""
    long = [(100.0, 100.0, 0.0), (1100.0, 100.0, 0.0), (100.0, 110.0, 0.0)]
    whole = len(_terrain(lambda c, r: DEEP)[1])
    assert len(_terrain(lambda c, r: DEEP, soup=_soup(long))[1]) == whole - 2 * 5


def test_a_triangle_whose_box_covers_a_square_it_misses_leaves_that_square():
    """The overlap test is the triangle's, not its bounding box's."""
    slanted = [(0.0, 0.0, 0.0), (1000.0, 1000.0, 0.0), (0.0, 20.0, 0.0)]
    verts, tris = _terrain(lambda c, r: DEEP, soup=_soup(slanted))
    corners = {(v[0], v[1]) for v in verts}
    assert (768.0, 0.0) in corners and (1024.0, 256.0) in corners


def test_water_the_swimmer_cannot_reach_is_not_laid():
    """Water beyond a dry ridge is another water: only the swimmer's side is laid."""
    ridge = lambda c, r: DRY if 14 <= c <= 18 else DEEP
    verts, tris = _terrain(ridge, [(512.0, 2048.0, -200.0, False)])
    assert len(tris) == 2 * 6 * 16
    assert max(v[0] for v in verts) < 14 * 128.0


def test_waters_that_meet_only_at_a_corner_are_not_one_water():
    """Two basins touching at the cell's middle point share no edge: only the swimmer's is laid."""
    dry = lambda c, r: DRY if (c <= 15 and r >= 17) or (c >= 17 and r <= 15) else DEEP
    verts, tris = _terrain(dry, [(500.0, 500.0, -200.0, False)])
    assert len(tris) == 2 * 8 * 8
    assert max(v[0] for v in verts) == 2048.0 and max(v[1] for v in verts) == 2048.0


def test_a_swimmer_on_each_side_of_a_ridge_gets_both_waters():
    """Each stranded swimmer brings in the water it is in."""
    ridge = lambda c, r: DRY if 14 <= c <= 18 else DEEP
    both = [(512.0, 2048.0, -200.0, False), (3500.0, 2048.0, -200.0, False)]
    assert len(_terrain(ridge, both)[1]) == 2 * (6 + 6) * 16


def test_a_swimmer_in_a_square_that_is_not_wet_starts_from_its_neighbours():
    """A swimmer by the shore still gets the water beside it."""
    shore = lambda c, r: DRY if c >= 16 else DEEP
    verts, tris = _terrain(shore, [(8 * 256.0 - 100.0, 2048.0, -200.0, False)])
    assert len(tris) == 2 * 7 * 16


def test_a_swimmer_whose_only_wet_neighbour_is_diagonal_starts_from_it():
    """All eight neighbours of the swimmer's square seed the sheet."""
    corner = lambda c, r: DEEP if (c <= 16 and r <= 16) else DRY
    assert len(_terrain(corner, [(2100.0, 2100.0, -200.0, False)])[1]) == 2 * 8 * 8


def test_the_triangles_come_row_by_row_from_the_south_west():
    """A fixed order: two builds of one export must give the same bytes."""
    verts, tris = _terrain(lambda c, r: DEEP)
    assert [verts[i][:2] for i in tris[0]] == [(0.0, 0.0), (256.0, 0.0), (256.0, 256.0)]
    assert [verts[i][:2] for i in tris[1]] == [(0.0, 0.0), (256.0, 256.0), (0.0, 256.0)]
    assert [verts[i][:2] for i in tris[2]][0] == (256.0, 0.0)
    assert [verts[i][:2] for i in tris[32]][0] == (0.0, 256.0)


def test_a_swimmer_with_no_wet_square_around_it_gets_nothing():
    shore = lambda c, r: DRY if c >= 8 else DEEP
    assert _terrain(shore, [(3500.0, 2048.0, -200.0, False)]) == ([], [])



def _open(swimmers, collision=None, navmesh=None, water=0.0):
    return ws.open_water_sheet(list(swimmers), water,
                               _soup() if collision is None else collision,
                               _soup() if navmesh is None else navmesh)


def _squares_within_spread(x, y):
    """Squares whose centre is within SPREAD of (x, y), counted the slow way."""
    n, span = 0, int(ws.SPREAD // ws.SQUARE) + 2
    for qx in range(-span, span):
        for qy in range(-span, span):
            cx, cy = (qx + 0.5) * ws.SQUARE, (qy + 0.5) * ws.SQUARE
            n += math.hypot(cx - x, cy - y) <= ws.SPREAD
    return n


def test_open_water_gives_a_flat_sheet_under_the_swimmer():
    """With no collision the sheet is the SPREAD disc, DROP under the swimmer."""
    verts, tris = _open([(100.0, 100.0, -900.0, True)])
    assert {v[2] for v in verts} == {-900.0 - ws.DROP}
    assert len(tris) == 2 * _squares_within_spread(100.0, 100.0)
    assert _faces_up(verts, tris)


def test_the_sheet_lies_under_the_lowest_swimmer():
    verts, _tris = _open([(0.0, 0.0, -900.0, True), (600.0, 0.0, -1700.0, True),
                          (0.0, 600.0, -1200.0, True)])
    assert {v[2] for v in verts} == {-1700.0 - ws.DROP}


def test_the_sheet_spreads_no_farther_than_spread_from_a_swimmer():
    """The farthest corner is about SPREAD away, give or take a square."""
    verts, _tris = _open([(100.0, 100.0, -900.0, True)])
    far = max(math.hypot(v[0] - 100.0, v[1] - 100.0) for v in verts)
    assert ws.SPREAD - ws.SQUARE < far <= ws.SPREAD + ws.SQUARE


def _box(x0, y0, x1, y1, z0, z1):
    """Four walls around a rectangle, each two triangles from z0 up to z1."""
    out = []
    for (ax, ay), (bx, by) in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)),
                               ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        out += [[(ax, ay, z0), (bx, by, z0), (bx, by, z1)],
                [(ax, ay, z0), (bx, by, z1), (ax, ay, z1)]]
    return out


def test_walls_that_cross_the_water_at_the_swimmers_depth_bound_the_sheet():
    """A walled basin 1,800 wide keeps the sheet to its 6 x 6 inner squares."""
    walls = _soup(*_box(-900.0, -900.0, 900.0, 900.0, -2000.0, 100.0))
    verts, tris = _open([(0.0, 0.0, -900.0, True)], walls)
    assert len(tris) == 2 * 6 * 6
    assert max(abs(v[0]) for v in verts) == 3 * ws.SQUARE


def test_a_swimmer_at_negative_coordinates_starts_in_its_own_square():
    """Squares are floored, not truncated: (-300, -300) is in square (-2, -2), not (-1, -1)."""
    walls = _soup(*_box(-700.0, -700.0, -50.0, -50.0, -2000.0, 100.0))
    verts, tris = _open([(-300.0, -300.0, -900.0, True)], walls)
    assert len(tris) == 2 and {(v[0], v[1]) for v in verts} == {
        (-512.0, -512.0), (-256.0, -512.0), (-256.0, -256.0), (-512.0, -256.0)}


def test_each_swimmer_brings_in_the_basin_it_is_in():
    """Two walled basins, a swimmer in each: both get their 6 x 6 squares."""
    walls = _soup(*_box(-900.0, -900.0, 900.0, 900.0, -2000.0, 100.0),
                  *_box(2100.0, -900.0, 3900.0, 900.0, -2000.0, 100.0))
    _verts, tris = _open([(0.0, 0.0, -900.0, True), (3000.0, 0.0, -900.0, True)], walls)
    assert len(tris) == 2 * (36 + 36)


def test_a_floor_under_the_swimmers_does_not_shut_a_square():
    """What lies under the swimmers leaves the water above it open."""
    floor = _soup(*[_tri(x, y, -1500.0, 5000.0) for x in (-5000.0,) for y in (-5000.0,)])
    free = _open([(100.0, 100.0, -900.0, True)])
    assert _open([(100.0, 100.0, -900.0, True)], floor) == free


def test_collision_above_the_surface_does_not_shut_a_square():
    """A bridge over the water is not in the water."""
    bridge = _soup(_tri(-5000.0, -5000.0, 300.0, 12000.0))
    free = _open([(100.0, 100.0, -900.0, True)])
    assert _open([(100.0, 100.0, -900.0, True)], bridge) == free


def test_collision_between_the_highest_swimmer_and_the_surface_shuts_its_squares():
    """A shelf above the swimmer's depth: the water is not open there."""
    shelf = _soup(_tri(300.0, 300.0, -500.0, 100.0))
    free = len(_open([(100.0, 100.0, -900.0, True)])[1])
    assert len(_open([(100.0, 100.0, -900.0, True)], shelf)[1]) == free - 2


def test_the_band_starts_at_the_highest_swimmer():
    """A shelf between two swimmers' depths lies under the higher one: open."""
    shelf = _soup(_tri(300.0, 300.0, -1000.0, 100.0))
    pair = [(100.0, 100.0, -900.0, True), (700.0, 100.0, -1500.0, True)]
    assert _open(pair, shelf) == _open(pair)


def test_a_navmesh_triangle_under_the_water_shuts_its_squares():
    """Navmesh already under the water keeps its ground."""
    ribbon = _soup(_tri(300.0, 300.0, -1500.0, 100.0))
    free = len(_open([(100.0, 100.0, -900.0, True)])[1])
    assert len(_open([(100.0, 100.0, -900.0, True)], navmesh=ribbon)[1]) == free - 2


def test_a_navmesh_triangle_above_the_water_shuts_nothing():
    """A walkway over the lake does not cut the sheet under it."""
    walkway = _soup(_tri(-5000.0, -5000.0, 80.0, 12000.0))
    free = _open([(100.0, 100.0, -900.0, True)])
    assert _open([(100.0, 100.0, -900.0, True)], navmesh=walkway) == free


def test_collision_whose_box_covers_a_square_it_misses_leaves_that_square_open():
    """The column test is the triangle's own outline, not its bounding box."""
    slanted = _soup([(0.0, 0.0, -500.0), (1000.0, 1000.0, -500.0), (0.0, 20.0, -500.0)])
    verts, tris = _open([(2000.0, 100.0, -900.0, True)], slanted)
    squares = {(min(verts[i][0] for i in t) // ws.SQUARE, min(verts[i][1] for i in t) // ws.SQUARE)
               for t in tris}
    assert (3.0, 0.0) in squares and (0.0, 0.0) not in squares


def test_navmesh_between_the_swimmer_and_the_surface_shuts_its_squares_too():
    """Any navmesh under the water keeps its ground, above the swimmers or below them."""
    shelf = _soup(_tri(300.0, 300.0, -500.0, 100.0))
    free = len(_open([(100.0, 100.0, -900.0, True)])[1])
    assert len(_open([(100.0, 100.0, -900.0, True)], navmesh=shelf)[1]) == free - 2


def test_a_swimmer_whose_own_square_is_shut_gets_nothing():
    pillar = _soup(_tri(90.0, 90.0, -500.0, 30.0))
    assert _open([(100.0, 100.0, -900.0, True)], pillar) == ([], [])



def _decode(navm_bytes, fid=0x00800000):
    blob, _before, _after = extract_nvnm(navm_bytes)
    return NavMeshView(fid, blob)


def test_every_packed_triangle_is_flagged_water_and_found():
    """The packed triangles carry the water flag and the generated flag."""
    verts, tris = _terrain(lambda c, r: DEEP)
    navm, _meta = ws.pack_sheet(verts, tris, (0x2000, WRLD, -3, 2), 0x00800000)
    view = _decode(navm)
    assert len(view.tris) == len(tris)
    assert all(int(f) & 0x0A00 == 0x0A00 for f in view.tris[:, 6])


def test_an_exterior_sheet_carries_its_worldspace_and_grid():
    """The record header and the meta both name the cell's grid square."""
    verts, tris = _terrain(lambda c, r: DEEP)
    navm, meta = ws.pack_sheet(verts, tris, (0x2000, WRLD, -3, 2), 0x00800000)
    view = _decode(navm)
    assert view.exterior and view.grid == (-3, 2)
    assert (meta['is_exterior'], meta['wrld_fid'], meta['grid_x'], meta['grid_y']) == (True, WRLD, -3, 2)


def test_an_interior_sheet_names_its_cell():
    """An interior sheet is keyed by its cell, with no worldspace."""
    verts, tris = _open([(100.0, 100.0, -900.0, True)])
    navm, meta = ws.pack_sheet(verts, tris, (0x2000, 0, 0, 0), 0x00800000)
    assert not _decode(navm).exterior
    assert (meta['is_exterior'], meta['cell_fid'], meta['fid']) == (False, 0x2000, 0x00800000)


def test_the_meta_centre_is_the_mean_of_the_corners():
    """The info map's centre for a whole-cell sheet is the cell's middle."""
    verts, tris = _terrain(lambda c, r: DEEP)
    _navm, meta = ws.pack_sheet(verts, tris, (0x2000, WRLD, 0, 0), 0x00800000)
    assert meta['center'] == pytest.approx((2048.0, 2048.0, DEEP))


@pytest.mark.parametrize('frame, build', [
    ((0x2000, WRLD, 0, 0), lambda: _terrain(lambda c, r: DEEP - 8.0 * (c % 5))),
    ((0x2000, 0, 0, 0), lambda: _open([(100.0, 100.0, -900.0, True)])),
])
def test_a_sheet_passes_the_navmesh_rule_check(frame, build):
    """Both sheet kinds pass the repo's port of the editor's navmesh rules."""
    verts, tris = build()
    navm, _meta = ws.pack_sheet(verts, tris, frame, 0x00800000)
    blob, _before, _after = extract_nvnm(navm)
    mesh = navcheck.parse_nvnm(0x00800000, blob)
    assert navcheck.check_navmesh(mesh) == []
    assert navcheck.check_counts(mesh) == []


def test_the_info_map_takes_a_sheet():
    """The NAVI record registers a sheet's id like any navmesh's."""
    verts, tris = _terrain(lambda c, r: DEEP)
    _navm, meta = ws.pack_sheet(verts, tris, (0x2000, WRLD, 0, 0), 0x00800000)
    navi = build_navi_record(0x00012FB4, [meta])
    assert navi[:4] == b'NAVI' and struct.pack('<I', 0x00800000) in navi



KEY = (0x2000, ws.SHEET_KEY)


def test_the_numbers_the_commentary_states():
    """The documented numbers; changing one is a design change."""
    assert (ws.REACH, ws.DROP, ws.SPREAD, ws.SQUARE, ws.SHEET_KEY) == (1024.0, 100.0, 4096.0, 256.0, 0)
    assert (ws.WATER, ws.CREA_SWIMS, ws.CREA_WALKS) == (0x0200, 0x10, 0x40)


def test_a_swimmer_in_a_sea_cell_without_navmesh_gets_a_sheet():
    """The whole rule on one cell: one cache entry under the sheet key."""
    made, cache = _run(_sea())
    assert made == 1 and list(cache) == [KEY]
    navm, meta = cache[KEY]
    assert len(_decode(navm, meta['fid']).tris) == 2 * 16 * 16
    assert meta['is_exterior'] and (meta['grid_x'], meta['grid_y']) == (0, 0)


def test_the_sheet_id_is_the_same_on_every_run():
    first = _run(_sea())[1][KEY][1]['fid']
    assert _run(_sea())[1][KEY][1]['fid'] == first


def test_the_sheet_id_is_derived_from_the_cell():
    """The id's key is authored data: the cell and the word 'water'."""
    writer = FakeWriter()
    pool.add_water_sheets({}, _sea(), writer, {}, None)
    assert list(writer.keys) == [('NAVM', repr((0x2000, 'water')))]


def test_a_cell_at_another_grid_square_gets_its_own_square():
    """The sheet's corners start at the cell's own origin."""
    by_type = _sea(x=-3, y=2, pos=(-3 * 4096 + 500.0, 2 * 4096 + 500.0, -200.0))
    meta = _run(by_type)[1][KEY][1]
    verts = meta['geometry'][0]
    assert min(v[0] for v in verts) == -3 * 4096.0 and min(v[1] for v in verts) == 2 * 4096.0
    assert (meta['grid_x'], meta['grid_y'], meta['wrld_fid']) == (-3, 2, WRLD)


@pytest.mark.parametrize('change', [
    lambda b: b['ACRE'][0].update(PosZ='50.0'),
    lambda b: b['ACRE'][0].update(NAME='%08X' % WOLF),
    lambda b: b['ACRE'][0].update(NAME='00009999'),
    lambda b: b['ACRE'][0].update(RecordFlags='32'),
    lambda b: b['CELL'][0].update({'DATA.Flags': '0'}),
    lambda b: (b['CELL'][0].pop('XCLW.WaterHeight'), b['ACRE'][0].update(PosZ='50.0')),
    lambda b: b['CELL'][0].update(FormID='01002000'),
    lambda b: b['CELL'][0].update(RecordFlags='32'),
    lambda b: b.pop('LAND'),
    lambda b: b.update(NAVM=[{'Signature': 'NAVM', 'FormID': '00004444'}]),
    lambda b: b['LAND'][0].update(VHGT=_vhgt(lambda c, r: DRY)),
])
def test_what_the_rule_leaves_alone(change):
    """Each change alone must leave the cache empty.

    In order: above the water, cannot swim, unknown base, deleted placement,
    no Has Water flag, above sea level in a cell stating no height, a master's
    cell, a deleted cell,
    no LAND, a source with authored navmesh, a floor above the water.
    """
    by_type = _sea()
    change(by_type)
    if by_type['CELL'][0]['FormID'] != '00002000':
        by_type['ACRE'][0]['ParentCELL'] = by_type['CELL'][0]['FormID']
        by_type['LAND'][0]['ParentCELL'] = by_type['CELL'][0]['FormID']
    assert _run(by_type) == (0, {})


def test_a_sea_cell_that_states_no_height_is_at_sea_level():
    """Nearly every exterior cell of the base game leaves its water height to the default."""
    by_type = _sea()
    by_type['CELL'][0].pop('XCLW.WaterHeight')
    made, cache = _run(by_type)
    assert made == 1 and len(cache[KEY][1]['geometry'][1]) == 2 * 16 * 16


def test_the_worlds_own_default_height_is_read():
    """A worldspace stating a default 2,000 down leaves a swimmer at -200 above its water."""
    by_type = _sea()
    by_type['CELL'][0].pop('XCLW.WaterHeight')
    by_type['WRLD'] = [dict(DEEP_WORLD)]
    assert _run(by_type) == (0, {})


def test_a_fish_by_an_unflagged_ribbon_under_a_default_sea_gets_a_sheet():
    """Path-grid triangles carry no water flag where the cell states no height: no home for a fish."""
    by_type = _sea()
    by_type['CELL'][0].pop('XCLW.WaterHeight')
    cache = {(0x2000, 0x4000): _entry(_tri(2100.0, 2100.0, -480.0), [(0, 1, 2)])}
    made, cache = _run(by_type, cache)
    assert made == 1 and len(cache[KEY][1]['geometry'][1]) == 2 * 16 * 16 - 2


def test_a_reference_that_places_a_swimming_list_counts():
    """Oblivion places a leveled creature list with a REFR, not an ACRE."""
    by_type = _sea()
    placed = by_type.pop('ACRE')[0]
    by_type['LVLC'] = [_lvlc(0x7000, [FISH, CRAB])]
    by_type['REFR'] = [dict(placed, Signature='REFR', NAME='00007000')]
    assert _run(by_type)[0] == 1


def test_a_list_placement_the_importer_already_rewrote_counts():
    """By the navmesh phase a list's REFR is an ACHR aimed at a shell; it keeps the list's id."""
    by_type = _sea()
    placed = by_type.pop('ACRE')[0]
    by_type['LVLC'] = [_lvlc(0x7000, [FISH])]
    by_type['ACHR'] = [dict(placed, Signature='ACHR', NAME='00800123', PlacedList='00007000')]
    assert _run(by_type)[0] == 1


def test_a_placed_person_is_not_read():
    """An ACHR that was never a list placement is a person: left out."""
    by_type = _sea()
    placed = by_type.pop('ACRE')[0]
    by_type['ACHR'] = [dict(placed, Signature='ACHR')]
    assert _run(by_type) == (0, {})


class _ShellWriter(FakeWriter):
    """A writer the shell builder can add its records to."""

    def add_record(self, _sig, _data):
        """The shell record itself is not looked at here."""


def test_the_shell_builder_leaves_the_lists_id_on_the_placement():
    """The real rewrite, then the rule: a slaughterfish list placed in the sea gets its sheet."""
    from tes5_import.actors.leveled_actors import PLACED_LIST, build_leveled_actor_shells
    by_type = _sea()
    placed = by_type.pop('ACRE')[0]
    by_type['LVLC'] = [_lvlc(0x7000, [FISH])]
    by_type['REFR'] = [dict(placed, Signature='REFR', NAME='00007000')]
    assert build_leveled_actor_shells(by_type, _ShellWriter()) == 1
    moved = by_type['ACHR'][0]
    assert by_type['REFR'] == [] and moved[PLACED_LIST] == '00007000' and moved['NAME'] != '00007000'
    assert _run(by_type)[0] == 1


def test_a_reference_that_places_anything_else_is_not_read():
    """A REFR naming a creature record directly, or an object, is no placement of a list."""
    by_type = _sea()
    placed = by_type.pop('ACRE')[0]
    by_type['REFR'] = [dict(placed, Signature='REFR'),
                       dict(placed, Signature='REFR', FormID='00006101', NAME='00000ABC')]
    assert _run(by_type) == (0, {})


def test_a_swimmer_placed_through_a_leveled_list_counts():
    """Most swimmers are placed through a leveled list, not directly."""
    by_type = _sea(base=0x7000)
    by_type['LVLC'] = [_lvlc(0x7000, [FISH, CRAB])]
    assert _run(by_type)[0] == 1


def test_a_base_defined_in_a_master_counts():
    """A plugin's placement of a master's creature is read through the master."""
    by_type = _sea()
    master = {rec['FormID']: rec for rec in by_type.pop('CREA')}
    assert _run(by_type, master_export=master)[0] == 1


def test_a_fish_near_a_water_triangle_is_left_to_it():
    cache = {(0x2000, 0x4000): _entry(_tri(2100.0, 2100.0, -480.0), [(0, 1, 2)])}
    assert _run(_sea(), cache)[0] == 0 and list(cache) == [(0x2000, 0x4000)]


def test_a_fish_near_only_a_dry_triangle_gets_a_sheet_that_leaves_its_square_out():
    """A road by the water does not serve a fish; the sheet avoids it."""
    cache = {(0x2000, 0x4000): _entry(_tri(2100.0, 2100.0, 60.0), [(0, 1, 2)])}
    made, cache = _run(_sea(), cache)
    assert made == 1
    assert len(cache[KEY][1]['geometry'][1]) == 2 * 16 * 16 - 2


def test_a_walker_near_a_dry_triangle_is_left_to_it():
    cache = {(0x2000, 0x4000): _entry(_tri(2100.0, 2100.0, 60.0), [(0, 1, 2)])}
    assert _run(_sea(base=CRAB), cache)[0] == 0


def test_a_walker_far_from_every_triangle_gets_a_sheet():
    cache = {(0x2000, 0x4000): _entry(_tri(3900.0, 3900.0, 60.0), [(0, 1, 2)])}
    assert _run(_sea(base=CRAB, pos=(500.0, 500.0, -200.0)), cache)[0] == 1


def test_the_path_grid_entries_are_not_touched():
    """The rule only adds: the existing entry is the same object."""
    entry = _entry(_tri(2100.0, 2100.0, 60.0), [(0, 1, 2)])
    cache = {(0x2000, 0x4000): entry}
    _run(_sea(), cache)
    assert cache[(0x2000, 0x4000)] is entry


def test_another_cells_navmesh_does_not_serve_this_cells_swimmer():
    cache = {(0x2abc, 0x4000): _entry(_tri(2100.0, 2100.0, -480.0), [(0, 1, 2)], cell=0x2abc)}
    assert _run(_sea(), cache)[0] == 1


def test_a_persistent_placement_is_filed_under_the_cell_it_stands_in():
    """A persistent ref's parent is the world's dummy cell; position decides."""
    by_type = _sea()
    by_type['CELL'].append(dict(_cell(0x2fff, WRLD), RecordFlags='1024'))
    by_type['ACRE'][0]['ParentCELL'] = '00002FFF'
    made, cache = _run(by_type)
    assert made == 1 and list(cache) == [KEY]


def test_a_persistent_placement_over_a_deleted_cell_is_left():
    """A placement by position cannot land in a deleted cell, whatever land lies under it."""
    child = 0x00001001
    by_type = _sea(cell=0x2100)
    by_type['WRLD'] = [{'Signature': 'WRLD', 'FormID': '%08X' % WRLD},
                       {'Signature': 'WRLD', 'FormID': '%08X' % child,
                        'WNAM.Parent': '%08X' % WRLD}]
    by_type['CELL'] += [dict(_cell(0x2000, child), RecordFlags='32'),
                        dict(_cell(0x2fff, child), RecordFlags='1024')]
    by_type['ACRE'] = [_acre(0x6000, 0x2fff, FISH, 2048.0, 2048.0, -200.0)]
    assert _run(by_type) == (0, {})


def test_only_the_stranded_swimmers_bring_in_their_water():
    """A fish served by water navmesh east of a ridge brings in nothing; the one west of it does."""
    by_type = _sea(pos=(512.0, 2048.0, -200.0), height=lambda c, r: DRY if 14 <= c <= 18 else DEEP)
    by_type['ACRE'].append(_acre(0x6099, 0x2000, FISH, 3500.0, 2048.0, -200.0))
    cache = {(0x2000, 0x4000): _entry(_tri(3500.0, 2100.0, -480.0), [(0, 1, 2)])}
    made, cache = _run(by_type, cache)
    verts, tris, _ledges = cache[KEY][1]['geometry']
    assert made == 1 and len(tris) == 2 * 6 * 16
    assert max(v[0] for v in verts) < 14 * 128.0


def test_an_entry_without_record_bytes_is_no_navmesh():
    """Geometry without a record was never emitted: it serves nobody."""
    entry = (None, {'geometry': (_tri(2100.0, 2100.0, -480.0), [(0, 1, 2)], [])})
    assert _run(_sea(), {(0x2000, 0x4000): entry})[0] == 1


def test_a_placement_outside_every_own_cell_is_left():
    by_type = _sea(pos=(9000.0, 2048.0, -200.0))
    assert _run(by_type) == (0, {})


def test_a_failed_path_grid_entry_is_read_as_no_navmesh():
    cache = {(0x2000, 0x4000): (None, None)}
    assert _run(_sea(), cache)[0] == 1


def _lake(swimmers=((100.0, 100.0, -900.0),), base=CRAB):
    """One interior cell flagged as water at -400, with creatures placed in it."""
    return {
        'CELL': [_cell(0x2000, water=-400.0)],
        'CREA': _creatures(),
        'ACRE': [_acre(0x6000 + i, 0x2000, base, *pos) for i, pos in enumerate(swimmers)],
    }


def test_an_interior_swimmer_in_open_water_gets_a_flat_sheet():
    """An interior with no collision: the SPREAD disc, DROP under the swimmer."""
    made, cache = _run(_lake())
    verts, tris, _ledges = cache[KEY][1]['geometry']
    assert made == 1 and not cache[KEY][1]['is_exterior']
    assert {v[2] for v in verts} == {-900.0 - ws.DROP}
    assert len(tris) == 2 * _squares_within_spread(100.0, 100.0)


def test_an_interior_that_states_the_no_height_value_has_no_water():
    """Only an exterior falls back to a worldspace default."""
    by_type = _lake()
    by_type['CELL'][0]['XCLW.WaterHeight'] = repr(NO_HEIGHT)
    assert _run(by_type) == (0, {})


def test_an_interior_sheet_is_shaped_by_its_stranded_swimmers_only():
    """A served fish deeper in the cell neither lowers the sheet nor seeds it."""
    ribbon = _entry(_tri(9000.0, 9000.0, -3000.0, 100.0), [(0, 1, 2)])
    by_type = _lake(swimmers=((100.0, 100.0, -900.0), (9000.0, 9000.0, -2500.0)), base=FISH)
    made, cache = _run(by_type, {(0x2000, 0x4000): ribbon})
    verts, tris, _ledges = cache[KEY][1]['geometry']
    assert made == 1 and {v[2] for v in verts} == {-900.0 - ws.DROP}
    assert len(tris) == 2 * _squares_within_spread(100.0, 100.0)


def test_a_sheet_too_large_for_one_navmesh_is_not_laid(capsys):
    """Twenty-five swimmers far apart need more triangles than a navmesh can index."""
    spread = [(i * 8300.0 + 100.0, j * 8300.0 + 100.0, -900.0) for i in range(5) for j in range(5)]
    assert _run(_lake(swimmers=spread)) == (0, {})
    assert "do not fit one navmesh" in capsys.readouterr().out


def test_a_swimmer_placed_exactly_at_the_water_height_is_not_under_it():
    assert _run(_lake(swimmers=((100.0, 100.0, -400.0),))) == (0, {})


def test_an_interior_swimmer_above_the_water_gets_nothing():
    assert _run(_lake(swimmers=((100.0, 100.0, -300.0),))) == (0, {})


def test_walkways_above_an_interior_lake_do_not_cut_its_sheet():
    """The lake's own navmesh, above the water, leaves the whole disc."""
    walkway = _entry([(-9000.0, -9000.0, -300.0), (9000.0, -9000.0, -300.0),
                      (0.0, 9000.0, -300.0)], [(0, 1, 2)])
    by_type = _lake(base=FISH)
    made, cache = _run(by_type, {(0x2000, 0x4000): walkway})
    assert made == 1
    assert len(cache[KEY][1]['geometry'][1]) == 2 * _squares_within_spread(100.0, 100.0)


def test_navmesh_under_an_interior_lakes_water_keeps_its_ground():
    """A ribbon on a far shelf under the water does not serve the fish, and the sheet leaves its square."""
    ribbon = _entry(_tri(1830.0, 1830.0, -1500.0, 100.0), [(0, 1, 2)])
    made, cache = _run(_lake(base=FISH), {(0x2000, 0x4000): ribbon})
    assert made == 1
    assert len(cache[KEY][1]['geometry'][1]) == 2 * _squares_within_spread(100.0, 100.0) - 2


def test_the_cells_placed_collision_bounds_an_interior_sheet(monkeypatch):
    """The cell's placed objects are read: a walled basin bounds the sheet."""
    walls = [c for tri in _box(-900.0, -900.0, 900.0, 900.0, -2000.0, 100.0)
             for p in tri for c in p]
    from asset_convert.collision import collision_extract
    monkeypatch.setattr(collision_extract, 'get_collision',
                        lambda key: {'w': [], 'b': walls} if key == 'box' else None)
    by_type = _lake()
    by_type['REFR'] = [{'Signature': 'REFR', 'FormID': '00006100', 'ParentCELL': '00002000',
                        'NAME': '00000ABC', 'PosX': '0', 'PosY': '0', 'PosZ': '0',
                        'RotX': '0', 'RotY': '0', 'RotZ': '0'}]
    cache = {}
    assert pool.add_water_sheets(cache, by_type, FakeWriter(), {0xABC: 'box'}, None) == 1
    assert len(cache[KEY][1]['geometry'][1]) == 2 * 6 * 6



def _two_seas():
    west, east = _sea(cell=0x2000), _sea(cell=0x2001, x=1, pos=(4096 + 2048.0, 2048.0, -200.0))
    return {sig: west.get(sig, []) + (east.get(sig, []) if sig != 'CREA' else [])
            for sig in ('CELL', 'LAND', 'CREA', 'ACRE')}


def test_sheets_of_neighbouring_cells_are_linked_across_their_seam():
    """Sixteen squares meet on the seam: a portal each way for each."""
    _made, cache = _run(_two_seas())
    assert build_edge_links(cache, verbose=False) == 2 * 16
    west, east = cache[(0x2000, ws.SHEET_KEY)][1], cache[(0x2001, ws.SHEET_KEY)][1]
    assert west['edge_link_fids'] == [east['fid']] and east['edge_link_fids'] == [west['fid']]


def test_a_lone_sheet_has_no_link():
    _made, cache = _run(_sea())
    assert build_edge_links(cache, verbose=False) == 0


def test_the_import_phase_lays_the_sheets_before_it_links_the_seams(monkeypatch, tmp_path):
    """The hook itself: two sea cells go through the navmesh phase and come out linked."""
    for name in ('set_cell_locations', 'set_teleport_grid', 'set_activators',
                 'set_door_navmesh_links'):
        monkeypatch.setattr(pipeline_records, name, lambda *a, **k: None)
    monkeypatch.setattr(pipeline_records, 'build_marker_locations', lambda *a: ())
    monkeypatch.setattr(pool, 'precompute_navmeshes', lambda *a, **k: {})
    st = types.SimpleNamespace(by_type=_two_seas(), writer=FakeWriter(), ctx=None,
                               num_tes4_masters=0)
    pipeline_records._phase4a_navmesh(st, str(tmp_path), lambda _name: None, set())
    west, east = st.navm_cache[(0x2000, ws.SHEET_KEY)][1], st.navm_cache[(0x2001, ws.SHEET_KEY)][1]
    assert west['edge_link_fids'] == [east['fid']] and east['edge_link_fids'] == [west['fid']]


class _NoLava:
    @staticmethod
    def refr_for(_cell_rec):
        return None


def _temporaries(cache, cell_fid=0x2000, pgrds=()):
    """(trailing records, metas) the cell builder emits for one cell."""
    metas = []
    leading, trailing = pipeline_records._cell_temporaries(
        {'FormID': '%08X' % cell_fid}, cell_fid, _NoLava, {}, {},
        {cell_fid: list(pgrds)}, cache, metas)
    return trailing, metas


def test_the_cell_emits_its_sheet_and_registers_it():
    """The sheet's bytes go in the cell and its meta to the info map."""
    _made, cache = _run(_sea())
    trailing, metas = _temporaries(cache)
    assert trailing == [cache[KEY][0]] and metas == [cache[KEY][1]]


def test_the_sheet_follows_the_path_grid_navmesh_in_the_cell():
    """Path-grid meshes come first, so a plugin adopting ids pairs them in file order."""
    entry = _entry(_tri(3900.0, 3900.0, 60.0), [(0, 1, 2)])
    _made, cache = _run(_sea(pos=(500.0, 500.0, -200.0)), {(0x2000, 0x4000): entry})
    trailing, metas = _temporaries(cache, pgrds=[{'FormID': '00004000'}])
    assert trailing == [entry[0], cache[KEY][0]]
    assert [m['fid'] for m in metas] == [entry[1]['fid'], cache[KEY][1]['fid']]


def test_a_cell_without_a_sheet_emits_none():
    assert _temporaries({}) == ([], [])


@pytest.mark.parametrize('first', ['tes5_import.navmesh.pool', 'tes5_import.pipeline',
                                   'tes5_import.navmesh.water_sheet',
                                   'tes5_import.actors.water_placement',
                                   'tes5_import.actors.leveled_actors'])
def test_the_importer_loads_whichever_module_comes_first(first):
    """A fresh interpreter: the pool, the sheet module and the water planner import each other."""
    root = str(Path(__file__).resolve().parent.parent)
    done = subprocess.run([sys.executable, '-B', '-c', 'import ' + first], cwd=root,
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr[-600:]


def test_the_new_module_does_not_move_the_geometry_cache_tag():
    names = {Path(src).name for src in pool.tag_sources()}
    assert 'water_sheet.py' not in names and 'from_pgrd.py' in names
