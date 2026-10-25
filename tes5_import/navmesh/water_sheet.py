"""Water navmesh for swimmers the path grid left without one.

Oblivion's swimmers need no path grid; Skyrim's need navmesh, and the engine
moves an actor that has none near onto the nearest one.  A cell holding such a
swimmer gets one extra all-water navmesh: its terrain under the water outdoors,
a flat sheet under the swimmers in open water that has no floor.

See: docs/commentary/tes5_import_navmesh.md#water-sheets
"""

import math

import numpy as np

from ..base.text_reader import get_float, get_formid, get_int
from ..base.writer import pack_subrecord
from .from_pgrd import compute_adjacency, pack_navm_record, pack_nvnm

#: Second element of a sheet's `navm_cache` key; no path grid has FormID 0.
SHEET_KEY = 0
#: Side of one sheet square, two LAND quads (units).
SQUARE = 256.0
#: A swimmer with navmesh it can use this near in plan is left to it (units).
REACH = 1024.0
#: How far under its lowest swimmer an open-water sheet lies (units).
DROP = 100.0
#: How far from a swimmer an open-water sheet spreads, one exterior cell (units).
SPREAD = 4096.0
#: NVNM triangle flag: under water.
WATER = 0x0200
#: TES4 CREA `ACBS.Flags` bits.
CREA_SWIMS, CREA_WALKS = 0x10, 0x40
#: TES4 sea level, the water height of a worldspace that states no default.
SEA_LEVEL = 0.0
#: A stated height at or past this is Oblivion's "use the default" value.
_NO_HEIGHT = 1e9
#: Sheet squares along one side of an exterior cell.
_SIDE = 16
#: Leveled lists nested deeper than this are not followed.
_MAX_LIST_DEPTH = 8
#: Most corners or triangles one NVNM can index: its indices are signed 16-bit.
MAX_INDEX = 0x7FFF


def _stated_height(cell_rec):
    """The water height the cell states, when it is a finite number, else None."""
    try:
        height = float(cell_rec.get('XCLW.WaterHeight'))
    except (TypeError, ValueError):
        return None
    return height if math.isfinite(height) else None


def water_height(cell_rec, wrld_rec=None):
    """The height of the cell's water as the built plugin has it, or None.

    A cell not flagged Has Water has none.  A stated height wins.  Without
    one, or with Oblivion's "use the default" value, an exterior cell takes
    its worldspace's default (sea level for a TES4 worldspace) and an
    interior is read as `water_placement.water_height` reads it.
    See: docs/commentary/tes5_import_navmesh.md#water-sheets
    """
    if cell_rec is None or not get_int(cell_rec, 'DATA.Flags', 0) & 0x02:
        return None
    stated = _stated_height(cell_rec)
    if stated is not None and abs(stated) < _NO_HEIGHT:
        return stated
    if wrld_rec is None:
        from ..actors.water_placement import water_height as interior_height
        return interior_height(cell_rec)[0]
    return get_float(wrld_rec, 'DNAM.DefaultWaterHeight', SEA_LEVEL)


def submerged(corner_z, water_z):
    """True per triangle whose three corners, as stored (32-bit), are under `water_z`."""
    return np.asarray(corner_z, dtype=np.float32).max(axis=1) < water_z


def mark_water(view, water_z):
    """Flag water on a decoded navmesh's triangles whose three corners are under `water_z`.

    Every other triangle loses the flag; nothing else changes.  `view` is an
    `edge_links.NavMeshView`.  Returns how many triangles changed.
    See: docs/commentary/tes5_import_navmesh.md#water-flags
    """
    if not len(view.tris):
        return 0
    flags = view.tris[:, 6]
    under = submerged(view.verts[view.tris[:, 0:3], 2], water_z)
    marked = np.where(under, flags | WATER, flags & ~WATER)
    changed = int((marked != flags).sum())
    view.tris[:, 6] = marked
    return changed


def swim_kind(base, creatures, lists, depth=0):
    """(swims, walks) of a placed creature's base, or None when it cannot be told.

    A leveled list swims when every creature it can place swims, and walks
    when any of them does.  `creatures` and `lists` map FormID to record.
    """
    rec = creatures.get(base)
    if rec is not None:
        flags = get_int(rec, 'ACBS.Flags', 0)
        return bool(flags & CREA_SWIMS), bool(flags & CREA_WALKS)
    rec = lists.get(base)
    if rec is None or depth >= _MAX_LIST_DEPTH:
        return None
    kinds = [swim_kind(get_formid(rec, 'Entry[%d].FormID' % i), creatures,
                       lists, depth + 1)
             for i in range(get_int(rec, 'EntryCount', 0))]
    if not kinds or None in kinds:
        return None
    return all(k[0] for k in kinds), any(k[1] for k in kinds)


def soup_of(parts):
    """One (N, 3, 3) triangle array from (verts, tris) pairs and ready arrays."""
    out = [np.zeros((0, 3, 3))]
    for part in parts:
        if isinstance(part, tuple):
            verts, tris = part
            if len(verts) and len(tris):
                part = np.asarray(verts, dtype=np.float64)[np.asarray(tris, dtype=np.int64)]
            else:
                continue
        if len(part):
            out.append(np.asarray(part, dtype=np.float64).reshape(-1, 3, 3))
    return np.concatenate(out, axis=0)


def _plan_distance(x, y, plan):
    """Distance in plan from (x, y) to each triangle of an (N, 3, 2) array; 0 inside."""
    point = np.array([x, y], dtype=np.float64)
    start, end = plan, np.roll(plan, -1, axis=1)
    edge, to_point = end - start, point - start
    along = np.clip((to_point * edge).sum(-1)
                    / np.maximum((edge * edge).sum(-1), 1e-12), 0.0, 1.0)
    dist = np.linalg.norm(to_point - along[..., None] * edge, axis=-1).min(axis=1)
    cross = edge[..., 0] * to_point[..., 1] - edge[..., 1] * to_point[..., 0]
    inside = (cross >= 0).all(axis=1) | (cross <= 0).all(axis=1)
    return np.where(inside, 0.0, dist)


def stranded(swimmers, soup, water_z):
    """The swimmers of one cell that no existing navmesh serves.

    swimmers: [(x, y, z, walks)], each placed under `water_z`.  soup: the
    cell's navmesh as an (N, 3, 3) array.  One that also walks is served by
    any triangle within REACH in plan, one that only swims by a triangle
    `mark_water` flags: all three corners under the water.
    See: docs/commentary/tes5_import_navmesh.md#water-sheets
    """
    if not len(soup):
        return list(swimmers)
    plan = soup[:, :, :2]
    wet = submerged(soup[:, :, 2], water_z)
    out = []
    for swimmer in swimmers:
        near = _plan_distance(swimmer[0], swimmer[1], plan) <= REACH
        if not (near if swimmer[3] else near & wet).any():
            out.append(swimmer)
    return out


def _touches(tri, x0, y0, size):
    """True when a plan triangle and the square at (x0, y0) overlap or touch."""
    xs, ys = [p[0] for p in tri], [p[1] for p in tri]
    if max(xs) < x0 or min(xs) > x0 + size or max(ys) < y0 or min(ys) > y0 + size:
        return False
    corners = ((x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size))
    for i in range(3):
        a, b = tri[i], tri[(i + 1) % 3]
        nx, ny = a[1] - b[1], b[0] - a[0]
        mine = [nx * p[0] + ny * p[1] for p in tri]
        theirs = [nx * p[0] + ny * p[1] for p in corners]
        if max(mine) < min(theirs) or max(theirs) < min(mine):
            return False
    return True


def _square_of(x, y, ox=0.0, oy=0.0):
    """The (column, row) of the sheet square holding (x, y)."""
    return int(math.floor((x - ox) / SQUARE)), int(math.floor((y - oy) / SQUARE))


def _taken(plan, ox, oy):
    """The squares of an exterior cell that a triangle of `plan` touches."""
    out = set()
    for tri in plan.tolist():
        lo = _square_of(min(p[0] for p in tri), min(p[1] for p in tri), ox, oy)
        hi = _square_of(max(p[0] for p in tri), max(p[1] for p in tri), ox, oy)
        for qy in range(max(lo[1], 0), min(hi[1], _SIDE - 1) + 1):
            for qx in range(max(lo[0], 0), min(hi[0], _SIDE - 1) + 1):
                if (qx, qy) not in out and _touches(
                        tri, ox + qx * SQUARE, oy + qy * SQUARE, SQUARE):
                    out.add((qx, qy))
    return out


def _flood(seeds, is_open):
    """The squares reached from `seeds` through edge-sharing squares `is_open` takes."""
    todo = [seed for seed in seeds if is_open(seed)]
    seen = set(todo)
    while todo:
        qx, qy = todo.pop()
        for nxt in ((qx + 1, qy), (qx - 1, qy), (qx, qy + 1), (qx, qy - 1)):
            if nxt not in seen and is_open(nxt):
                seen.add(nxt)
                todo.append(nxt)
    return seen


def _mesh(squares, corner):
    """(verts, tris): two upward triangles per square, corners shared.

    `corner(column, row)` gives a lattice corner's (x, y, z).
    """
    index, verts, tris = {}, [], []

    def vid(ix, iy):
        """The index of a corner, added on first use."""
        if (ix, iy) not in index:
            index[(ix, iy)] = len(verts)
            verts.append(corner(ix, iy))
        return index[(ix, iy)]

    for qx, qy in sorted(squares, key=lambda q: (q[1], q[0])):
        a, b, c, d = vid(qx, qy), vid(qx + 1, qy), vid(qx + 1, qy + 1), vid(qx, qy + 1)
        tris += [(a, b, c), (a, c, d)]
    return verts, tris


def terrain_sheet(grid, ox, oy, water_z, soup, swimmers):
    """(verts, tris) of an exterior cell's terrain under the water, around its swimmers.

    grid: decode_vhgt's 33x33 heights (row = y) of the cell at (ox, oy).  A
    square is laid when the nine LAND corners it spans are all under
    `water_z` and no triangle of `soup`, the cell's own navmesh, touches it
    in plan; only squares joined to a swimmer's square or its neighbours stay.
    See: docs/commentary/tes5_import_navmesh.md#water-sheets
    """
    taken = _taken(soup[:, :, :2], ox, oy) if len(soup) else set()

    def wet(square):
        qx, qy = square
        return (0 <= qx < _SIDE and 0 <= qy < _SIDE and square not in taken
                and float(grid[2 * qy:2 * qy + 3, 2 * qx:2 * qx + 3].max()) < water_z)

    seeds = set()
    for swimmer in swimmers:
        qx, qy = _square_of(swimmer[0], swimmer[1], ox, oy)
        seeds.update((qx + dx, qy + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1))
    return _mesh(_flood(sorted(seeds), wet), lambda ix, iy: (
        ox + ix * SQUARE, oy + iy * SQUARE, float(grid[2 * iy, 2 * ix])))


def _column_test(soup, low_z, high_z):
    """A test `crossed(square)`: does a triangle of `soup` cross that column between two heights?"""
    zs = soup[:, :, 2]
    plan = soup[(zs.max(axis=1) >= low_z) & (zs.min(axis=1) <= high_z)][:, :, :2]
    lo, hi = plan.min(axis=1), plan.max(axis=1)

    def crossed(square):
        """True when a kept triangle touches the square in plan."""
        x0, y0 = square[0] * SQUARE, square[1] * SQUARE
        hit = ((hi[:, 0] >= x0) & (lo[:, 0] <= x0 + SQUARE)
               & (hi[:, 1] >= y0) & (lo[:, 1] <= y0 + SQUARE))
        return any(_touches(tri, x0, y0, SQUARE) for tri in plan[hit].tolist())

    return crossed


def _within_spread(square, swimmers):
    """True when the square's centre is within SPREAD of a swimmer in plan."""
    cx, cy = (square[0] + 0.5) * SQUARE, (square[1] + 0.5) * SQUARE
    return any(math.hypot(cx - s[0], cy - s[1]) <= SPREAD for s in swimmers)


def open_water_sheet(swimmers, water_z, collision, navmesh):
    """(verts, tris) of one flat sheet under an interior cell's stranded swimmers.

    It lies DROP under the lowest of them and spreads square by square from
    each one's own square, at most SPREAD from a swimmer.  A square is shut
    when `collision` crosses its column between the highest swimmer and the
    surface (the water is not open at their depth there), or when a triangle
    of `navmesh` under the water touches it in plan.
    See: docs/commentary/tes5_import_navmesh.md#water-sheets
    """
    floor_z = min(s[2] for s in swimmers) - DROP
    blocked = _column_test(collision, max(s[2] for s in swimmers), water_z)
    covered = _column_test(navmesh, -math.inf, water_z)
    verdict = {}

    def is_open(square):
        """The square's verdict, worked out once."""
        if square not in verdict:
            verdict[square] = (_within_spread(square, swimmers)
                               and not blocked(square) and not covered(square))
        return verdict[square]

    homes = sorted({_square_of(s[0], s[1]) for s in swimmers})
    return _mesh(_flood(homes, is_open),
                 lambda ix, iy: (ix * SQUARE, iy * SQUARE, floor_z))


def pack_sheet(verts, tris, frame, navm_fid):
    """(navm_bytes, meta) of an all-water navmesh, shaped as convert_PGRD's answer.

    frame: (cell FormID, worldspace FormID or 0, grid x, grid y).
    """
    cell_fid, wrld_fid, grid_x, grid_y = frame
    exterior = bool(wrld_fid)
    nvnm = pack_nvnm(verts, tris, compute_adjacency(tris), [WATER] * len(tris),
                     wrld_fid, cell_fid, grid_x, grid_y, exterior,
                     navm_fid=navm_fid)
    meta = {
        'fid': navm_fid, 'wrld_fid': wrld_fid, 'cell_fid': cell_fid,
        'grid_x': grid_x, 'grid_y': grid_y, 'is_exterior': exterior,
        'center': tuple(sum(v[i] for v in verts) / len(verts) for i in range(3)),
        'base_objects': [], 'geom_cached': False, 'geom_hash': None,
        'geometry': (verts, tris, []), 'door_refs': [], 'door_xndp': {},
    }
    return pack_navm_record(navm_fid, pack_subrecord('NVNM', nvnm)), meta
