"""Place water planes in converted interiors that have water.

Skyrim draws no water from an interior cell's own settings: the Has Water
flag, `XCLW` and `XCWT` give an interior neither a surface nor a volume.  The
game's own wet interiors hold PLACED, PERSISTENT references of an activator
that carries a water type, and those references are the water.  Oblivion
needs none of that, so a converted interior arrives with its flag, height and
type and stays dry.  This module adds the references:

  * one generated activator per water type per plugin, in the shape of the
    game's own `Water1024` activators, pointing at the game's own mesh, and
  * per wet interior, a tiling of persistent references of it at the cell's
    water height, covering the extent of the cell's contents.

The extent is not that of the contents' origins: a room piece reaches beyond
its own origin, so a cover fitted to origins can end before the far wall.
Each reference spans `origin +- reach` on X and on Y, where reach is the
bound radius its base record states (`Model.MODB`) times the reference's
scale.  That radius is authored data, read from the plugin's own bases and
its masters'; a base that states none (an actor, a marker, a light) reaches 0.

The cell record, its flag, height and type, and the lava overlay are left as
they are; a lava interior gets the planes and the overlay.

DEFERRED, each to be revisited when the named case turns up:

  * TRIPWIRE (override): a plugin that overrides a master's wet interior and
    changes its water height or type is not followed; the master's planes
    stay where the master put them.
  * TRIPWIRE (range): the cover is clipped to `INTERIOR_LIMIT`.  An install
    that lifts that engine rule needs tiles beyond it: until then the parts
    of a level beyond the limit get no water there.
  * TRIPWIRE (Morrowind): Morrowind sources are not excluded.  The rule
    applies wherever the export carries the flag and the height; it has not
    been run on such an export.
  * TRIPWIRE (Fallout): FO3/FNV sources are skipped whole; their water is
    placed by the source itself and is converted elsewhere.
  * TRIPWIRE (lava): which cells get the lava overlay is decided by
    `lava_placement` alone and does not ask for the Has Water flag.

See: docs/commentary/tes5_import_landscape.md#interior-water-planes
"""

import math
import struct
from collections import Counter

from ..base.tes5_reader import decompress, subrecords
from ..base.text_reader import get_float, get_formid, get_int, get_str
from ..base.writer import (RECORD_HEADER_SIZE, pack_float_subrecord, pack_formid_subrecord,
                           pack_obnd, pack_record, pack_string_subrecord, pack_subrecord)
from ..navmesh.pool import build_base_radius_index
from ..overrides.nested import DELETED_FLAG
from ..record_types.interior_range import INTERIOR_LIMIT
from ..record_types.world import shifted_position
from ..record_types.world_falloutnv import is_fallout_source

#: Edge of the game's own sheet `Water\Water1024.nif`; Skyrim.esm's `Water1024` activators state OBND -512..512.
PLANE_SIZE = 1024
#: The largest scale Skyrim's own masters place that sheet at: 27 tiled planes of one Dragonborn interior.
PLANE_SCALE = 10.0
#: Distance between neighbouring planes: one scaled sheet, edge to edge as the game tiles them.
PLANE_PITCH = PLANE_SIZE * PLANE_SCALE
#: Skyrim.esm's DefaultWater, the fallback `record_types.world` writes for a worldspace that names no water.
SKYRIM_DEFAULT_WATER = 0x00000018
#: The game's own model path, in its own archive: no namespace prefix, nothing is copied.
PLANE_MODEL = 'Water\\Water1024.nif'
#: PNAM as every `Water1024` activator of Skyrim.esm that has one writes it.
_MARKER_COLOR = bytes.fromhex('cc4c3300')
#: TES4 CELL DATA flags.
_INTERIOR, _HAS_WATER = 0x01, 0x02
#: Record flag Persistent.
_PERSISTENT = 0x00000400
#: Origins are snapped to this fraction of a unit, which float32 holds exactly up to 32,768.
_SNAP = 512.0
#: A stated height at or beyond this is Oblivion's "no height" sentinel, -2147483648.0.
_HEIGHT_LIMIT = 1e9
_EDID_PREFIX = 'TES4WaterPlane'
_DEFAULT_NAME = 'SkyrimDefault'


# ---------------------------------------------------------------------------
# Which cells, and at what height
# ---------------------------------------------------------------------------

def is_wet_interior(cell_rec: dict) -> bool:
    """True for an interior cell that carries the Has Water flag."""
    if get_formid(cell_rec, 'ParentWRLD'):
        return False
    flags = get_int(cell_rec, 'DATA.Flags')
    return bool(flags & _INTERIOR) and bool(flags & _HAS_WATER)


def water_height(cell_rec: dict):
    """`(height, '')` of a wet interior's water, or `(None, reason)` when it states none.

    An absent `XCLW` is 0.0, as Oblivion reads it; the sentinel and a value
    that is no number state no height.

    See: docs/commentary/tes5_import_landscape.md#interior-water-planes
    """
    raw = get_str(cell_rec, 'XCLW.WaterHeight').strip()
    if not raw:
        return 0.0, ''
    try:
        value = float(raw)
    except ValueError:
        return None, 'height unreadable'
    if math.isnan(value):
        return None, 'height not a number'
    if abs(value) >= _HEIGHT_LIMIT:
        return None, 'height is the no-height sentinel'
    return value, ''


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def _float32(value: float) -> float:
    """`value` as the plugin stores it."""
    return struct.unpack('<f', struct.pack('<f', value))[0]


def reference_reach(rec: dict, radius_by_base: dict) -> float:
    """Its base's bound radius times its scale; 0.0 when none or not finite."""
    radius = radius_by_base.get(get_formid(rec, 'NAME'), 0.0)
    reach = abs(radius * (get_float(rec, 'XSCL.Scale', 1.0) or 1.0))
    return reach if math.isfinite(reach) else 0.0


def content_corners(refs, actors, radius_by_base=None) -> list:
    """`(x, y)` of the corners of the span each live reference and actor covers.

    A reference covers `origin +- reach` on X and on Y (`reference_reach`)
    and gives its low and its high corner; with a reach of 0 it gives its
    origin alone, as every actor does.  A reference's origin is the shifted
    one `convert_REFR` writes, an actor's the authored one, both as float32.
    Deleted records are left out.

    See: docs/commentary/tes5_import_landscape.md#interior-water-planes
    """
    radius_by_base = radius_by_base or {}
    out = []
    for rec in refs:
        if not get_int(rec, 'RecordFlags') & DELETED_FLAG:
            x, y, _z = shifted_position(rec, get_float(rec, 'XSCL.Scale'))
            out.append((x, y, reference_reach(rec, radius_by_base)))
    for rec in actors:
        if not get_int(rec, 'RecordFlags') & DELETED_FLAG:
            out.append((get_float(rec, 'PosX'), get_float(rec, 'PosY'), 0.0))
    corners = []
    for x, y, reach in out:
        x, y = _float32(x), _float32(y)
        corners.append((x - reach, y - reach))
        if reach:
            corners.append((x + reach, y + reach))
    return corners


def axis_origins(values) -> list:
    """Plane origins along one axis for contents whose span ends are `values`.

    `values` holds the low and the high end of every span.  The cover is
    centred on their extent, lowest low end to highest high end, clipped to
    `INTERIOR_LIMIT`; neighbours are `PLANE_PITCH` apart and no origin lies
    beyond the limit.  A span that straddles the limit keeps its part inside;
    [] when every span lies wholly beyond the limit on one side.  No
    contents: one plane at 0.

    See: docs/commentary/tes5_import_landscape.md#interior-water-planes
    """
    if not values:
        return [0.0]
    low = max(min(values), -INTERIOR_LIMIT)
    high = min(max(values), INTERIOR_LIMIT)
    if low > high:
        return []
    count = int(math.floor((high - low) / PLANE_PITCH)) + 1
    first = (low + high) / 2 - (count - 1) * PLANE_PITCH / 2
    first = round(first * _SNAP) / _SNAP
    return [first + k * PLANE_PITCH for k in range(count)]


def plane_origins(positions) -> list:
    """`(ix, iy, x, y)` of every plane covering `positions`, row by row."""
    xs = axis_origins([x for x, _y in positions])
    ys = axis_origins([y for _x, y in positions])
    return [(ix, iy, x, y) for iy, y in enumerate(ys) for ix, x in enumerate(xs)]


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

def build_plane_activator(fid: int, edid: str, water_fid: int) -> bytes:
    """The water-plane ACTI: EDID OBND MODL PNAM WNAM FNAM, as Skyrim.esm's `Water1024`."""
    half = PLANE_SIZE // 2
    subs = pack_string_subrecord('EDID', edid)
    subs += pack_obnd(-half, -half, 0, half, half, 0)
    subs += pack_string_subrecord('MODL', PLANE_MODEL)
    subs += pack_subrecord('PNAM', _MARKER_COLOR)
    subs += pack_formid_subrecord('WNAM', water_fid)
    subs += pack_subrecord('FNAM', struct.pack('<H', 0))
    return pack_record('ACTI', fid, 0, subs)


def build_plane_refr(fid: int, activator_fid: int, x: float, y: float,
                     height: float) -> bytes:
    """One persistent plane: NAME XSCL DATA, as the game's own tiled planes."""
    subs = pack_formid_subrecord('NAME', activator_fid)
    subs += pack_float_subrecord('XSCL', PLANE_SCALE)
    subs += pack_subrecord('DATA', struct.pack('<6f', x, y, height, 0.0, 0.0, 0.0))
    return pack_record('REFR', fid, _PERSISTENT, subs)


def _record_water(record: bytes) -> int:
    """The WNAM of a packed activator; -1 when it has none."""
    if len(record) < RECORD_HEADER_SIZE:
        return -1
    flags = struct.unpack_from('<I', record, 8)[0]
    for tag, data in subrecords(decompress(record[RECORD_HEADER_SIZE:], flags)):
        if tag == b'WNAM' and len(data) == 4:
            return struct.unpack('<I', data)[0]
    return -1


# ---------------------------------------------------------------------------
# The planner
# ---------------------------------------------------------------------------

class WaterPlanner:
    """Decides which interiors get water planes, and mints their records."""

    def __init__(self, by_type: dict, writer, master_export=None):
        """Plan for `writer`; water types are named from `by_type`, then `master_export`."""
        self.writer = writer
        self.enabled = not is_fallout_source()
        self._own_water = {get_str(rec, 'FormID').upper(): rec
                           for rec in by_type.get('WATR', ())}
        self._master_export = master_export or {}
        self._by_type = by_type
        self._radius_by_base = None
        if getattr(writer, 'water_plane_activators', None) is None:
            writer.water_plane_activators = {}
        self._activators = writer.water_plane_activators
        self.cells = 0
        self.planes = 0
        self.activators = 0
        self.adopted = 0
        self.skips = Counter()

    def _water_name(self, raw: str) -> str:
        """The EditorID of water type `raw`, or `raw` itself when no record names it."""
        rec = self._own_water.get(raw) or self._master_export.get(raw) or {}
        return get_str(rec, 'EditorID') or raw

    def _radii(self) -> dict:
        """Bound radius by base FormID, masters' too; built at the first wet cell."""
        if self._radius_by_base is None:
            self._radius_by_base = build_base_radius_index(self._by_type, self._master_export)
        return self._radius_by_base

    def _master_activator(self, edid: str) -> tuple:
        """`(FormID, water)` of a master's ACTI `edid`; `(0, -1)` when no master has one."""
        adoption = getattr(self.writer, 'adoption', None)
        fid = adoption.master_index.find_by_edid(b'ACTI', edid) if adoption else 0
        if not fid:
            return 0, -1
        return fid, _record_water(adoption.master_index.record(fid))

    def _mint(self, raw: str, water_fid: int) -> tuple:
        """`(FormID, EditorID)` for the activator of water `raw`, adopting a master's.

        A master's record of the same EditorID is taken only when it carries
        the same water.  An EditorID in use for another water gets the
        water's id appended.
        """
        edid = _EDID_PREFIX + (self._water_name(raw) if raw else _DEFAULT_NAME)
        master_fid, master_water = self._master_activator(edid)
        if master_fid and master_water == water_fid:
            self.adopted += 1
            return self.writer.adoption.find('ACTI', edid), edid
        if master_fid or edid in {e for _fid, e in self._activators.values()}:
            edid += f'_{water_fid:08X}'
        return self.writer.derive_formid('WATER_PLANE_ACTI', raw or _DEFAULT_NAME), edid

    def activator_for(self, cell_rec: dict) -> int:
        """The FormID of the plane activator of this cell's water type, written on first use.

        One per water type per plugin.  The id is keyed on the water's source
        FormID, so it does not move between builds.
        """
        water_fid = get_formid(cell_rec, 'XCWT.Water')
        raw = get_str(cell_rec, 'XCWT.Water').upper() if water_fid else ''
        water_fid = water_fid or SKYRIM_DEFAULT_WATER
        if water_fid not in self._activators:
            fid, edid = self._mint(raw, water_fid)
            self.writer.add_record('ACTI', build_plane_activator(fid, edid, water_fid))
            self._activators[water_fid] = (fid, edid)
            self.activators += 1
        return self._activators[water_fid][0]

    def planes_for(self, cell_rec: dict, refs=(), actors=()) -> list:
        """The packed persistent plane references of one cell; [] when it gets none.

        `refs` and `actors` are the cell's own source REFR and ACHR/ACRE
        records; the cover reaches as far as their bases' bound radii.  Ids
        are keyed on the cell's source FormID and the plane's place in the
        tiling.

        See: docs/commentary/tes5_import_landscape.md#interior-water-planes
        """
        if not self.enabled or not is_wet_interior(cell_rec):
            return []
        height, reason = water_height(cell_rec)
        if height is None:
            self.skips[reason] += 1
            return []
        origins = plane_origins(content_corners(refs, actors, self._radii()))
        if not origins:
            self.skips['contents beyond the interior range'] += 1
            print(f"    Water planes: none for {get_str(cell_rec, 'EditorID', '?')}"
                  f" (everything placed lies beyond {INTERIOR_LIMIT:.0f} on X or Y)")
            return []
        activator = self.activator_for(cell_rec)
        cell = get_str(cell_rec, 'FormID').upper()
        planes = [build_plane_refr(
            self.writer.derive_formid('WATER_PLANE_REFR', (cell, ix, iy)),
            activator, x, y, height) for ix, iy, x, y in origins]
        self.cells += 1
        self.planes += len(planes)
        return planes

    def report(self) -> None:
        """Print the build-log summary; nothing when no cell was handled or skipped."""
        if not self.cells and not self.skips:
            return
        print(f"    Interior water planes: {self.planes} in {self.cells} cell(s), "
              f"{self.activators} activator(s), {self.adopted} of them a master's")
        for reason, count in sorted(self.skips.items()):
            print(f"      skipped, {reason}: {count}")
