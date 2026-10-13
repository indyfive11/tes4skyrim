"""Converted interiors with water get persistent water planes, by one generic rule.

Synthetic records only.  The activator's expected bytes are PINNED fixture
values read off Skyrim.esm's `Water1024` activator (EDID OBND MODL PNAM WNAM
FNAM, record flags 0); no game file is opened here.

See: docs/commentary/tes5_import_landscape.md#interior-water-planes
"""

import math
import random
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tes5_import import pipeline_records as PR
from tes5_import.actors import water_placement as WP
from tes5_import.base.writer import PluginWriter
from tes5_import.overrides.adoption import MasterAdoption
from tes5_import.record_types import items, world_falloutnv
from tes5_import.record_types.interior_range import INTERIOR_LIMIT

PITCH = 10240.0
WET, DRY_INTERIOR = '3', '1'
CELL_A, CELL_B, CELL_C = '00000D01', '00000D02', '00000D13'
WATER_1, WATER_2, LAVA = '00000E01', '00000E02', '00000E03'
SENTINEL = '-2147483648.0'
#: Skyrim.esm `Water1024`, after its EDID: OBND, MODL, PNAM, WNAM (DefaultWater), FNAM.
VANILLA_BODY = (b'OBND' + struct.pack('<H', 12) + bytes.fromhex('00fe00fe0000000200020000')
                + b'MODL' + struct.pack('<H', 20) + b'Water\\Water1024.nif\x00'
                + b'PNAM' + struct.pack('<H', 4) + bytes.fromhex('cc4c3300')
                + b'WNAM' + struct.pack('<H', 4) + bytes.fromhex('18000000')
                + b'FNAM' + struct.pack('<H', 2) + bytes.fromhex('0000'))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _cell(fid=CELL_A, flags=WET, height='-400.0', water=None, wrld=None, edid='WetCell'):
    """A TES4 CELL export record; None drops a field."""
    rec = {'Signature': 'CELL', 'FormID': fid, 'EditorID': edid, 'RecordFlags': '0',
           'DATA.Flags': flags, 'XCLW.WaterHeight': height, 'XCWT.Water': water,
           'ParentWRLD': wrld}
    return {k: v for k, v in rec.items() if v is not None}


def _ref(fid, x, y, cell=CELL_A, sig='REFR', flags=0, base='00000A01', z=0.0):
    """A placed reference or actor of `cell` at (x, y, z)."""
    return {'Signature': sig, 'FormID': fid, 'RecordFlags': str(flags), 'NAME': base,
            'ParentCELL': cell, 'PosX': repr(x), 'PosY': repr(y), 'PosZ': repr(z),
            'RotX': '0.0', 'RotY': '0.0', 'RotZ': '0.0'}


def _base(fid='00000A01', radius=None, sig='STAT'):
    """A base record with a model and bound `radius`; None states none."""
    rec = {'Signature': sig, 'FormID': fid, 'EditorID': 'Base' + fid, 'RecordFlags': '0',
           'Model.MODL': 'room\\piece.nif', 'Model.MODB': radius}
    return {k: v for k, v in rec.items() if v is not None}


def _watr(fid, edid, material=''):
    """A TES4 WATR export record."""
    return {'Signature': 'WATR', 'FormID': fid, 'EditorID': edid, 'RecordFlags': '0',
            'MNAM.MaterialID': material}


def _writer() -> PluginWriter:
    """A root plugin's writer."""
    return PluginWriter(masters=['Skyrim.esm'])


def _planner(by_type=None, writer=None, master_export=None) -> WP.WaterPlanner:
    """A planner over `by_type`, on a fresh writer unless one is given."""
    return WP.WaterPlanner(by_type or {}, writer or _writer(), master_export)


def _subs(record: bytes) -> list:
    """`[(tag, payload)]` of a packed record."""
    out, pos = [], 24
    while pos < len(record):
        size = struct.unpack_from('<H', record, pos + 4)[0]
        out.append((record[pos:pos + 4], record[pos + 6:pos + 6 + size]))
        pos += 6 + size
    return out


def _header(record: bytes) -> tuple:
    """`(signature, flags, FormID)` of a packed record."""
    return (record[:4],) + struct.unpack_from('<II', record, 8)


def _plane(record: bytes) -> tuple:
    """`(x, y, z)` of a packed plane."""
    return struct.unpack('<3f', dict(_subs(record))[b'DATA'][:12])


def _walk(blob: bytes) -> list:
    """`(record bytes, (group type, label))` of every record in a group blob."""
    found, stack, pos = [], [], 0
    while pos < len(blob):
        while stack and pos >= stack[-1][0]:
            stack.pop()
        if blob[pos:pos + 4] == b'GRUP':
            size, label, gtype = struct.unpack_from('<I4sI', blob, pos + 4)
            stack.append((pos + size, gtype, struct.unpack('<I', label)[0]))
            pos += 24
            continue
        size = struct.unpack_from('<I', blob, pos + 4)[0]
        found.append((blob[pos:pos + 24 + size], stack[-1][1:]))
        pos += 24 + size
    return found


def _build(cells, refs=(), actors=(), waters=(), writer=None, bases=()):
    """Run `_build_cell_groups`; returns (writer, [(record, group)] of the CELL group)."""
    writer = writer or _writer()
    by_type = {'CELL': list(cells), 'REFR': list(refs), 'ACHR': list(actors),
               'WATR': list(waters), 'STAT': list(bases)}
    PR._build_cell_groups(by_type, writer)
    return writer, _walk(b''.join(writer._top_groups.get('CELL', [])))


def _activators(writer) -> list:
    """The ACTI records the writer holds."""
    return [r for r in writer.top_records() if r[:4] == b'ACTI']


def _planes(found) -> list:
    """`(record, group)` of the generated planes: persistent REFRs with a scale of 10."""
    return [(rec, grp) for rec, grp in found if rec[:4] == b'REFR'
            and dict(_subs(rec)).get(b'XSCL') == struct.pack('<f', 10.0)]


# ---------------------------------------------------------------------------
# 1. Which cells
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('cell, planes, height', [
    (_cell(flags=DRY_INTERIOR, water=WATER_1), 0, None),
    (_cell(flags='2', water=WATER_1), 0, None),
    (_cell(flags='0', water=WATER_1), 0, None),
    (_cell(), 1, -400.0),
    (_cell(height='0.0'), 1, 0.0),
    (_cell(height='137.5'), 1, 137.5),
    (_cell(height=None), 1, 0.0),
    (_cell(height=''), 1, 0.0),
    (_cell(height=SENTINEL), 0, None),
    (_cell(height='nan'), 0, None),
    (_cell(height='inf'), 0, None),
    (_cell(height='-1e9'), 0, None),
    (_cell(height='water'), 0, None),
    (_cell(wrld='0000003C'), 0, None),
    (_cell(flags='131', height='12.0'), 1, 12.0),
])
def test_eligibility_table(cell, planes, height):
    """Interior AND Has Water AND a usable height; an absent height is 0.0."""
    planner = _planner()
    got = planner.planes_for(cell)
    assert len(got) == planes
    if planes:
        assert _plane(got[0])[2] == height


def test_skips_are_counted_by_reason():
    """Sentinel, not-a-number and unreadable heights are reported apart; a dry cell is no skip."""
    planner = _planner()
    for height in (SENTINEL, 'nan', 'water', SENTINEL):
        assert planner.planes_for(_cell(height=height)) == []
    assert planner.planes_for(_cell(flags=DRY_INTERIOR)) == []
    assert sorted(planner.skips.values()) == [1, 1, 2]
    assert planner.cells == 0 and planner.planes == 0


def test_fallout_source_gets_nothing(monkeypatch):
    """An FO3/FNV source is left alone, whatever its cells state."""
    monkeypatch.setattr(world_falloutnv, '_IS_FALLOUT_SOURCE', [True])
    writer = _writer()
    planner = _planner(writer=writer)
    assert planner.planes_for(_cell()) == []
    assert not _activators(writer)
    monkeypatch.setattr(world_falloutnv, '_IS_FALLOUT_SOURCE', [])
    assert len(_planner().planes_for(_cell())) == 1


def test_unflagged_cell_builds_no_plane_and_no_activator():
    """Through the group builder: height and type present, flag off, nothing written."""
    writer, found = _build([_cell(flags=DRY_INTERIOR, water=WATER_1)],
                           [_ref('00000C01', 5.0, 5.0)], waters=[_watr(WATER_1, 'Pond')])
    assert not _planes(found)
    assert not _activators(writer)


# ---------------------------------------------------------------------------
# 2. Layout
# ---------------------------------------------------------------------------

def test_small_cell_gets_one_centred_plane():
    """Contents inside one plane: one origin, at the middle of their extent."""
    assert WP.axis_origins([-100.0, 300.0]) == [100.0]
    assert WP.axis_origins([7.0]) == [7.0]


def test_empty_cell_gets_one_plane_at_the_origin():
    """No contents: (0, 0)."""
    assert WP.axis_origins([]) == [0.0]
    assert WP.plane_origins([]) == [(0, 0, 0.0, 0.0)]


@pytest.mark.parametrize('span, count', [
    (PITCH - 1.0, 1), (PITCH, 2), (PITCH + 1.0, 2),
    (2 * PITCH - 1.0, 2), (2 * PITCH, 3), (5 * PITCH + 0.5, 6),
])
def test_count_steps_at_each_multiple_of_the_pitch(span, count):
    """floor(span / pitch) + 1 planes: one more exactly AT a multiple."""
    origins = WP.axis_origins([-span / 2, span / 2])
    assert len(origins) == count
    assert origins == [(k - (count - 1) / 2) * PITCH for k in range(count)]


def test_contents_beyond_the_limit_are_clipped_on_that_side():
    """The cover stops at the interior limit; the other side follows the contents."""
    origins = WP.axis_origins([-2000.0, 50000.0])
    assert len(origins) == 4
    assert max(origins) <= INTERIOR_LIMIT
    assert origins[0] - PITCH / 2 <= -2000.0
    assert origins[-1] + PITCH / 2 >= INTERIOR_LIMIT


def test_all_contents_beyond_the_limit_give_no_plane(capsys):
    """Nothing in range on one axis: no plane, a skip and one log line."""
    assert WP.axis_origins([30000.5, 41000.0]) == []
    assert WP.axis_origins([-41000.0, -30000.5]) == []
    assert WP.plane_origins([(100.0, 30001.0)]) == []
    writer = _writer()
    planner = _planner(writer=writer)
    assert planner.planes_for(_cell(), [_ref('00000C01', 31000.0, 0.0)]) == []
    assert sum(planner.skips.values()) == 1
    assert not _activators(writer)
    assert capsys.readouterr().out.count('\n') == 1


def test_contents_exactly_at_the_limit_keep_a_plane():
    """The limit itself is in range."""
    assert WP.axis_origins([30000.0, 40000.0]) == [30000.0]
    assert WP.axis_origins([-40000.0, -30000.0]) == [-30000.0]


def _f32(value: float) -> float:
    """`value` rounded to float32."""
    return struct.unpack('<f', struct.pack('<f', value))[0]


def test_layout_properties_over_random_spans():
    """Cover contains the clipped extent; no origin beyond the limit; neighbours a pitch apart.

    Origins are snapped to 1/512, so the cover is checked to that grid.
    """
    rng = random.Random(20261012)
    snap, checked = 1 / 512, 0
    for _ in range(4000):
        centre = rng.uniform(-45000, 45000)
        width = rng.choice([0.0, rng.uniform(0, 500), rng.uniform(0, 90000),
                            PITCH * rng.randint(1, 5), PITCH * rng.randint(1, 5) - 0.01])
        values = [_f32(centre - width / 2), _f32(centre + width / 2)]
        values += [_f32(rng.uniform(values[0], values[1])) for _ in range(3)]
        origins = WP.axis_origins(values)
        low, high = max(min(values), -INTERIOR_LIMIT), min(max(values), INTERIOR_LIMIT)
        if low > high:
            assert origins == []
            continue
        checked += 1
        assert origins[0] - PITCH / 2 <= low + snap
        assert origins[-1] + PITCH / 2 >= high - snap
        assert all(abs(o) <= INTERIOR_LIMIT for o in origins)
        assert all(b - a == PITCH for a, b in zip(origins, origins[1:]))
        assert all(_f32(o) == o for o in origins)
        assert len(origins) == math.floor((high - low) / PITCH) + 1
    assert checked > 3000


def test_planes_tile_both_axes_row_by_row():
    """n_x * n_y planes, indexed (ix, iy), y outermost."""
    got = WP.plane_origins([(0.0, 0.0), (PITCH, 2 * PITCH)])
    assert [(ix, iy) for ix, iy, _x, _y in got] == [
        (0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2)]
    assert got[0][2:] == (0.0, 0.0) and got[-1][2:] == (PITCH, 2 * PITCH)


def test_positions_are_the_built_ones(monkeypatch):
    """Deleted records are left out; a re-origined base moves with its shift; actors count.

    A position that is no number is read as `convert_REFR` writes it.
    """
    monkeypatch.setitem(items._BASE_ORIGIN_SHIFT, '00000A09', 64.0)
    tilted = dict(_ref('00000C03', 100.0, 200.0, base='00000A09'), RotY=repr(math.pi / 2))
    got = WP.content_corners(
        [_ref('00000C01', 1.0, 2.0), _ref('00000C02', 9e4, 9e4, flags=0x20), tilted,
         dict(_ref('00000C04', 0.0, 0.0), PosX='nan')],
        [_ref('00000C05', -7.0, 8.0, sig='ACHR'),
         _ref('00000C06', 5e4, 5e4, sig='ACRE', flags=0x20)])
    assert got == [(1.0, 2.0), (36.0, 200.0), (0.0, 0.0), (-7.0, 8.0)]


def test_positions_are_float32():
    """A position is read as the plugin stores it."""
    (x, _y), = WP.content_corners([_ref('00000C01', 0.1, 0.0)], [])
    assert x == _f32(0.1) and x != 0.1
    low, high = WP.content_corners([_ref('00000C01', 0.1, 0.0)], [], {0xA01: 8.0})
    assert (low[0], high[0]) == (_f32(0.1) - 8.0, _f32(0.1) + 8.0)


# ---------------------------------------------------------------------------
# 2b. The cover reaches as far as the placed models do
# ---------------------------------------------------------------------------

#: Two references whose origins fit one plane and whose models, 700 wide each way, do not.
NEAR, FAR, RADIUS = 0.0, 9000.0, '700.0'


def _pair(scale=None, base='00000A01'):
    """The two references of the reach tests, at `scale` when one is given."""
    refs = [_ref('00000C01', NEAR, 0.0, base=base), _ref('00000C02', FAR, 0.0, base=base)]
    return [dict(r, **{'XSCL.Scale': scale}) for r in refs] if scale else refs


def _xy(planes) -> list:
    """`(x, y)` of packed planes."""
    return [_plane(p)[:2] for p in planes]


def test_cover_reaches_as_far_as_the_base_radius():
    """Origins 9,000 apart fit one plane; with a radius of 700 the span is 10,400: two."""
    planner = _planner({'STAT': [_base(radius=RADIUS)]})
    assert _xy(planner.planes_for(_cell(), _pair())) == [(-620.0, 0.0), (9620.0, 0.0)]
    assert WP.content_corners(_pair()[:1], [], {0xA01: 700.0}) == [(-700.0, -700.0),
                                                                  (700.0, 700.0)]


def test_reach_is_the_radius_times_the_references_scale():
    """Scale 0.5 keeps the pair in one plane; scale 2 on a smaller radius needs two."""
    half = _planner({'STAT': [_base(radius=RADIUS)]})
    assert _xy(half.planes_for(_cell(), _pair('0.5'))) == [(4500.0, 0.0)]
    double = _planner({'STAT': [_base(radius='350.0')]})
    assert _xy(double.planes_for(_cell(), _pair('2.0'))) == [(-620.0, 0.0), (9620.0, 0.0)]
    assert _xy(_planner({'STAT': [_base(radius='350.0')]}).planes_for(
        _cell(), _pair())) == [(4500.0, 0.0)]


@pytest.mark.parametrize('scale, reach', [
    (None, 700.0), ('1.0', 700.0), ('3.0', 2100.0), ('0.25', 175.0), ('0.0', 700.0),
    ('-2.0', 1400.0),
])
def test_reach_table(scale, reach):
    """An absent or zero scale is 1; a reach is never negative."""
    rec = _pair(scale)[0]
    assert WP.reference_reach(rec, {0xA01: 700.0}) == reach


def test_base_without_a_radius_reaches_nothing():
    """No stated radius, a base type that is no room piece, an unknown base, an actor: the origin."""
    by_type = {'STAT': [_base(radius=None), _base('00000A03', '0.0')],
               'LIGH': [_base('00000A02', RADIUS, sig='LIGH')]}
    for base in ('00000A01', '00000A02', '00000A03', '00000A7F'):
        planner = _planner(by_type)
        assert _xy(planner.planes_for(_cell(), _pair(base=base))) == [(4500.0, 0.0)]
    actors = [dict(r, Signature='ACHR') for r in _pair()]
    planner = _planner({'STAT': [_base(radius=RADIUS)]})
    assert _xy(planner.planes_for(_cell(), (), actors)) == [(4500.0, 0.0)]
    assert WP.content_corners([], actors, {0xA01: 700.0}) == [(NEAR, 0.0), (FAR, 0.0)]


def test_reach_that_is_no_finite_number_is_none():
    """A radius that is no finite number leaves the reference at its origin."""
    rec = _pair()[0]
    assert WP.reference_reach(rec, {0xA01: float('inf')}) == 0.0
    assert WP.reference_reach(rec, {0xA01: float('nan')}) == 0.0
    assert WP.content_corners([rec], [], {0xA01: float('inf')}) == [(NEAR, 0.0)]


def test_base_in_a_master_gives_its_radius():
    """The masters' export is asked too; without it the same cell gets one plane."""
    master = {'00000A01': _base(radius=RADIUS)}
    assert _xy(_planner(master_export=master).planes_for(_cell(), _pair())) == [
        (-620.0, 0.0), (9620.0, 0.0)]
    assert _xy(_planner().planes_for(_cell(), _pair())) == [(4500.0, 0.0)]


def test_group_builder_sizes_the_cover_from_the_whole_plugin(monkeypatch):
    """`_build_groups` gives the planner every base of the plugin and its masters' export."""
    monkeypatch.setattr(PR, '_build_world_groups', lambda *a, **k: None)
    writer = _writer()
    own = {'CELL': [_cell(), _cell(fid=CELL_B)],
           'REFR': _pair() + [dict(r, ParentCELL=CELL_B, FormID='00000C1' + r['FormID'][-1])
                              for r in _pair(base='00000A05')]}
    st = SimpleNamespace(
        by_type=dict(own, STAT=[_base(radius=RADIUS)]), writer=writer, navm_metas=[],
        base_model_by_fid={}, door_fids=set(), navm_cache={},
        ctx=SimpleNamespace(master_export={'00000A05': _base('00000A05', RADIUS)}))
    PR._build_groups(st, own, {}, lambda _label: None, 'own ')
    planes = _planes(_walk(b''.join(writer._top_groups['CELL'])))
    assert sorted(_plane(rec)[:2] for rec, _grp in planes) == [
        (-620.0, 0.0), (-620.0, 0.0), (9620.0, 0.0), (9620.0, 0.0)]


def test_radius_index_is_built_once_and_only_for_a_wet_cell(monkeypatch):
    """A plugin without a wet interior never asks for the bases; one with several asks once."""
    calls = []
    real = WP.build_base_radius_index
    monkeypatch.setattr(WP, 'build_base_radius_index',
                        lambda *args: calls.append(args) or real(*args))
    by_type, master = {'STAT': [_base(radius=RADIUS)]}, {'00000A09': _base('00000A09', RADIUS)}
    planner = _planner(by_type, master_export=master)
    assert planner.planes_for(_cell(flags=DRY_INTERIOR), _pair()) == []
    assert planner.planes_for(_cell(height=SENTINEL), _pair()) == []
    assert planner.planes_for(_cell(wrld='0000003C'), _pair()) == []
    assert not calls
    assert len(planner.planes_for(_cell(), _pair())) == 2
    assert len(planner.planes_for(_cell(fid=CELL_B), _pair())) == 2
    assert len(calls) == 1 and calls[0][0] is by_type and calls[0][1] is master


def test_span_straddling_the_limit_keeps_its_part_inside(capsys):
    """An origin beyond the limit whose model reaches back inside still gets water there."""
    assert WP.axis_origins([29000.0, 33000.0]) == [29500.0]
    assert WP.axis_origins([-33000.0, -29000.0]) == [-29500.0]
    planner = _planner({'STAT': [_base(radius='2000.0')]})
    inside = planner.planes_for(_cell(), [_ref('00000C01', 31000.0, 0.0)])
    assert _xy(inside) == [(29500.0, 0.0)] and not planner.skips
    assert _xy(planner.planes_for(_cell(fid=CELL_B), [_ref('00000C02', 0.0, -29000.0)])) == [
        (0.0, -28500.0)]
    assert capsys.readouterr().out == ''


def test_span_wholly_beyond_the_limit_gives_no_plane():
    """Low end past the limit: nothing, and a skip, as for an origin alone."""
    planner = _planner({'STAT': [_base(radius='2000.0')]})
    assert planner.planes_for(_cell(), [_ref('00000C01', 32000.5, 0.0)]) == []
    assert planner.planes_for(_cell(fid=CELL_B), [_ref('00000C02', 0.0, -32000.5)]) == []
    assert sum(planner.skips.values()) == 2 and planner.cells == 0


def test_cover_holds_every_span_over_random_cells():
    """Each reference's span, clipped to the limit, lies inside the planes' cover."""
    rng = random.Random(20261013)
    snap, checked = 1 / 512, 0
    for _ in range(300):
        radii = {0xA00 + k: rng.choice([0.0, rng.uniform(1, 400), rng.uniform(400, 6000)])
                 for k in range(1, 6)}
        centre, width = rng.uniform(-28000, 28000), rng.uniform(0, 30000)
        refs = [dict(_ref(f'00000C{n:02X}', rng.uniform(centre - width, centre + width),
                          rng.uniform(-width, width), base=f'00000A{rng.randint(1, 5):02X}'),
                     **{'XSCL.Scale': repr(rng.choice([1.0, 1.0, rng.uniform(0.2, 4.0)]))})
                for n in range(rng.randint(1, 12))]
        origins = WP.plane_origins(WP.content_corners(refs, [], radii))
        if not origins:
            continue
        xs, ys = sorted({o[2] for o in origins}), sorted({o[3] for o in origins})
        for rec in refs:
            reach = WP.reference_reach(rec, radii)
            for at, cover in ((_f32(float(rec['PosX'])), xs), (_f32(float(rec['PosY'])), ys)):
                low, high = max(at - reach, -INTERIOR_LIMIT), min(at + reach, INTERIOR_LIMIT)
                if low > high:
                    continue
                checked += 1
                assert cover[0] - PITCH / 2 <= low + snap
                assert cover[-1] + PITCH / 2 >= high - snap
    assert checked > 3000


# ---------------------------------------------------------------------------
# 3. The activator
# ---------------------------------------------------------------------------

def test_activator_bytes_are_the_vanilla_shape():
    """Header flags 0, then exactly the vanilla body after the EDID."""
    record = WP.build_plane_activator(0x01000900, 'TES4WaterPlaneX', 0x18)
    assert _header(record) == (b'ACTI', 0, 0x01000900)
    edid = b'EDID' + struct.pack('<H', 16) + b'TES4WaterPlaneX\x00'
    assert record[24:] == edid + VANILLA_BODY
    assert struct.unpack_from('<I', record, 4)[0] == len(edid + VANILLA_BODY)


def test_activator_carries_the_cells_water_type():
    """WNAM is the cell's XCWT as the cell record writes it; absent: Skyrim's default."""
    writer = _writer()
    planner = _planner({'WATR': [_watr(WATER_1, 'Pond')]}, writer)
    planner.planes_for(_cell(water=WATER_1))
    planner.planes_for(_cell(fid=CELL_B))
    planner.planes_for(_cell(fid=CELL_C, water='00000000'))
    waters = [dict(_subs(r))[b'WNAM'] for r in _activators(writer)]
    assert waters == [struct.pack('<I', 0x00000E01), struct.pack('<I', 0x18)]


def test_one_activator_per_water_type_per_plugin():
    """A second cell of the same type reuses the activator, also from a second planner."""
    writer = _writer()
    by_type = {'WATR': [_watr(WATER_1, 'Pond'), _watr(WATER_2, 'Sewer')]}
    planner = _planner(by_type, writer)
    first = planner.planes_for(_cell(water=WATER_1))
    second = planner.planes_for(_cell(fid=CELL_B, water=WATER_1))
    third = planner.planes_for(_cell(fid=CELL_C, water=WATER_2))
    again = _planner(by_type, writer).planes_for(_cell(fid='00000D24', water=WATER_1))
    names = [dict(_subs(r))[b'NAME'] for r in (first[0], second[0], third[0], again[0])]
    assert names[0] == names[1] == names[3] != names[2]
    assert len(_activators(writer)) == 2
    assert planner.activators == 2


def test_activator_editor_ids():
    """Own WATR name, then the masters' export, a fixed name for the default, else the id."""
    writer = _writer()
    planner = _planner({'WATR': [_watr(WATER_1, 'Pond')]}, writer,
                       {WATER_1: {'Signature': 'WATR', 'EditorID': 'MasterPond'},
                        WATER_2: {'Signature': 'WATR', 'EditorID': 'MasterSewer'}})
    for n, water in enumerate((WATER_1, WATER_2, None, '00000EFF')):
        planner.planes_for(_cell(fid=f'00000D{n:02X}', water=water))
    edids = [dict(_subs(r))[b'EDID'].rstrip(b'\0').decode() for r in _activators(writer)]
    assert edids == ['TES4WaterPlanePond', 'TES4WaterPlaneMasterSewer',
                     'TES4WaterPlaneSkyrimDefault', 'TES4WaterPlane00000EFF']


def test_two_waters_of_one_name_get_two_editor_ids():
    """A second water type with a name already used keeps its own activator and EditorID."""
    writer = _writer()
    planner = _planner({'WATR': [_watr(WATER_1, 'Pond'), _watr(WATER_2, 'Pond')]}, writer)
    planner.planes_for(_cell(water=WATER_1))
    planner.planes_for(_cell(fid=CELL_B, water=WATER_2))
    edids = [dict(_subs(r))[b'EDID'].rstrip(b'\0').decode() for r in _activators(writer)]
    assert edids == ['TES4WaterPlanePond', 'TES4WaterPlanePond_00000E02']


# ---------------------------------------------------------------------------
# 4. The planes, and where they land
# ---------------------------------------------------------------------------

def test_plane_bytes():
    """Persistent, NAME XSCL DATA in that order, scale 10, the cell's height, no rotation."""
    record = WP.build_plane_refr(0x01000901, 0x01000900, 12.5, -3.0, -400.0)
    assert _header(record) == (b'REFR', 0x00000400, 0x01000901)
    assert _subs(record) == [
        (b'NAME', struct.pack('<I', 0x01000900)),
        (b'XSCL', struct.pack('<f', 10.0)),
        (b'DATA', struct.pack('<6f', 12.5, -3.0, -400.0, 0.0, 0.0, 0.0))]


def test_planes_land_in_the_persistent_group(capsys):
    """Type-8 group of their own cell, after the cell's authored persistent references."""
    cell = int(CELL_A, 16)
    writer, found = _build(
        [_cell(water=WATER_1)],
        [_ref('00000C01', 0.0, 0.0, flags=0x400), _ref('00000C02', PITCH, 0.0)],
        [_ref('00000C03', 5.0, 5.0, sig='ACHR', flags=0x400)],
        [_watr(WATER_1, 'Pond')])
    planes = _planes(found)
    assert len(planes) == 2 and all(grp == (8, cell) for _rec, grp in planes)
    persistent = [_header(rec)[2] & 0xFFFFFF for rec, grp in found if grp == (8, cell)]
    assert persistent[:2] == [0x000C01, 0x000C03] and len(persistent) == 4
    acti, = _activators(writer)
    assert all(dict(_subs(rec))[b'NAME'] == acti[12:16] for rec, _grp in planes)
    assert [_plane(rec) for rec, _grp in planes] == [(0.0, 2.5, -400.0), (PITCH, 2.5, -400.0)]
    assert 'Interior cells: 1, children: 6' in capsys.readouterr().out


def test_cell_without_other_children_still_gets_its_group():
    """An empty wet interior: one plane at the origin in a type-8 group."""
    _writer_, found = _build([_cell(height=None)])
    (record, group), = _planes(found)
    assert group == (8, int(CELL_A, 16)) and _plane(record) == (0.0, 0.0, 0.0)


def test_lava_reference_stays_in_the_temporary_group():
    """A lava interior gets the overlay where it was, and the planes too."""
    cell = int(CELL_A, 16)
    lava = [_watr(LAVA, 'Lava', 'lava')]
    _w, found = _build([_cell(water=LAVA, height='-64.0')],
                       [_ref('00000C01', 0.0, 0.0)], waters=lava)
    stat, = [r for r in _w.top_records() if r[:4] == b'STAT']
    overlay = [(rec, grp) for rec, grp in found if rec[:4] == b'REFR'
               and dict(_subs(rec))[b'NAME'] == stat[12:16]]
    assert [grp for _rec, grp in overlay] == [(9, cell)]
    assert found[-1] == overlay[0]
    assert _plane(overlay[0][0]) == (0.0, 0.0, -64.0)
    (plane, group), = _planes(found)
    assert group == (8, cell) and _plane(plane) == (0.0, 0.0, -64.0 - WP.LAVA_PLANE_DROP)


def test_planes_lie_under_a_lava_overlay_and_nowhere_else():
    """Only a cell that gets the overlay has its planes lowered, by the drop and no more."""
    lava = [_watr(LAVA, 'Lava', 'lava'), _watr(WATER_1, 'Pond')]
    cells = [_cell(water=LAVA, height='-64.0'),
             _cell(CELL_B, water=WATER_1, height='-64.0', edid='PondCell')]
    refs = [_ref('00000C01', 0.0, 0.0), _ref('00000C02', 0.0, 0.0, cell=CELL_B)]
    _w, found = _build(cells, refs, waters=lava)
    heights = {grp[1]: _plane(rec)[2] for rec, grp in _planes(found)}
    assert heights == {int(CELL_A, 16): -66.0, int(CELL_B, 16): -64.0}
    assert WP.LAVA_PLANE_DROP == 2.0


def test_a_lava_water_without_an_overlay_keeps_the_cells_height():
    """The drop follows the overlay, not the water type: no overlay, no drop."""
    planner = _planner({'WATR': [_watr(LAVA, 'Lava', 'lava')]})
    cell = _cell(water=LAVA, height='-64.0')
    plane, = planner.planes_for(cell, [_ref('00000C01', 0.0, 0.0)])
    assert _plane(plane) == (0.0, 0.0, -64.0)
    low, = _planner().planes_for(cell, [_ref('00000C01', 0.0, 0.0)], under_overlay=True)
    assert _plane(low) == (0.0, 0.0, -66.0)


def test_lava_overlay_is_unchanged_by_the_planes():
    """The overlay's bytes and the temporary group are the same with the planner switched off."""
    lava = [_watr(LAVA, 'Lava', 'lava')]
    cells, refs = [_cell(water=LAVA)], [_ref('00000C01', 3.0, 4.0)]
    _w, with_planes = _build(cells, refs, waters=lava)
    writer = _writer()
    off = _planner(writer=writer)
    off.enabled = False
    PR._build_cell_groups({'CELL': cells, 'REFR': refs, 'WATR': lava}, writer, water=off)
    without = _walk(b''.join(writer._top_groups['CELL']))
    assert without == [pair for pair in with_planes if pair not in _planes(with_planes)]
    assert len(with_planes) == len(without) + 1


def _build_with(planner, cells, refs, actors=()):
    """`_build_cell_groups` with `planner`: [(record, group)] of the CELL group."""
    by_type = {'CELL': list(cells), 'REFR': list(refs), 'ACHR': list(actors)}
    PR._build_cell_groups(by_type, planner.writer, water=planner)
    return _walk(b''.join(planner.writer._top_groups['CELL']))


def test_planner_failure_costs_a_cell_only_its_planes(monkeypatch, capsys):
    """One ERROR line names the cell; its children are packed as with the planner off."""
    cells = [_cell(edid='Flooded'), _cell(fid=CELL_B, edid='Other')]
    refs = [_ref('00000C01', 0.0, 0.0, flags=0x400), _ref('00000C02', 9.0, 0.0),
            _ref('00000C04', 1.0, 1.0, cell=CELL_B)]
    actors = [_ref('00000C03', 5.0, 5.0, sig='ACHR')]
    off = _planner()
    off.enabled = False
    expected = _build_with(off, cells, refs, actors)
    capsys.readouterr()

    real = WP.build_plane_refr
    failing = _planner()
    monkeypatch.setattr(WP, 'build_plane_refr', lambda fid, acti, x, y, height: (
        real(fid, acti, x, y, height) if x != 4.5 else [].pop()))
    found = _build_with(failing, cells, refs, actors)
    out = capsys.readouterr().out
    assert [line for line in out.splitlines() if 'ERROR' in line] == [
        '  ERROR placing water planes for Flooded: pop from empty list']
    planes = _planes(found)
    assert [grp for _rec, grp in planes] == [(8, int(CELL_B, 16))]
    assert [pair for pair in found if pair not in planes] == expected
    assert len(expected) == 6 and 'Interior cells: 2, children: 7' in out
    assert failing.cells == 1 and failing.planes == 1


# ---------------------------------------------------------------------------
# 5. Stable ids
# ---------------------------------------------------------------------------

def _ids(cells, refs=(), waters=()):
    """({plane FormID: (x, y, z)} of CELL_A, {water: activator FormID}) after one build."""
    writer, found = _build(cells, refs, waters=waters)
    planes = {_header(rec)[2]: _plane(rec) for rec, grp in _planes(found)
              if grp[1] == int(CELL_A, 16)}
    return planes, {dict(_subs(r))[b'WNAM']: _header(r)[2] for r in _activators(writer)}


def test_ids_are_the_same_on_a_second_build():
    """Same cells, same ids."""
    cells = [_cell(water=WATER_1)]
    refs = [_ref('00000C01', 0.0, 0.0), _ref('00000C02', 2 * PITCH, PITCH)]
    waters = [_watr(WATER_1, 'Pond')]
    first, second = _ids(cells, refs, waters), _ids(cells, refs, waters)
    assert first == second
    assert len(first[0]) == 6 and len(first[1]) == 1


def test_ids_do_not_move_when_unrelated_content_is_added():
    """Another wet cell, another water type and more records leave this cell's ids alone."""
    cells = [_cell(water=WATER_1)]
    refs = [_ref('00000C01', 0.0, 0.0), _ref('00000C02', 2 * PITCH, PITCH)]
    waters = [_watr(WATER_1, 'Pond')]
    before = _ids(cells, refs, waters)
    more_cells = [_cell(fid=CELL_C, water=WATER_2, edid='Other'),
                  _cell(fid='00000D00', height=None, edid='Third')] + cells
    more_refs = refs + [_ref('00000C09', 77.0, 88.0, cell=CELL_C),
                        _ref('00000C0A', 100.0, 100.0)]
    after = _ids(more_cells, more_refs, [_watr(WATER_2, 'Sewer')] + waters)
    assert after[0] == before[0]
    assert len(after[1]) == 3 and before[1].items() <= after[1].items()


def test_ids_are_distinct_and_in_the_plugins_own_space():
    """Every plane and the activator get their own id under the writer's index."""
    planes, actis = _ids([_cell()], [_ref('00000C01', 0.0, 0.0),
                                    _ref('00000C02', 2 * PITCH, 2 * PITCH)])
    ids = list(planes) + list(actis.values())
    assert len(set(ids)) == 10 and all(fid >> 24 == 1 for fid in ids)


def test_two_cells_do_not_share_plane_ids():
    """The cell is part of a plane's key."""
    _w, found = _build([_cell(), _cell(fid=CELL_B)],
                       [_ref('00000C01', 0.0, 0.0), _ref('00000C02', PITCH, 0.0),
                        _ref('00000C03', 0.0, 0.0, cell=CELL_B),
                        _ref('00000C04', PITCH, 0.0, cell=CELL_B)])
    assert len({_header(rec)[2] for rec, _grp in _planes(found)}) == 4


# ---------------------------------------------------------------------------
# 6. A dependent plugin
# ---------------------------------------------------------------------------

MASTER_ACTI = 0x01000A77


class _Index:
    """A master index over a {FormID: packed record} dict."""

    def __init__(self, records: dict):
        """Index `records` by FormID and by (signature, EditorID)."""
        self.records = records
        self.edids = {(r[:4], _subs(r)[0][1].rstrip(b'\0').decode()): fid
                      for fid, r in records.items()}

    def find_by_edid(self, sig: bytes, edid: str) -> int:
        """The FormID of the record with this signature and EditorID, else 0."""
        return self.edids.get((sig, edid), 0)

    def record(self, fid: int) -> bytes:
        """The packed record at `fid`, else b''."""
        return self.records.get(fid, b'')


def _child(master_water: int) -> PluginWriter:
    """A child writer whose master holds `TES4WaterPlanePond` on `master_water`."""
    writer = PluginWriter(masters=['Skyrim.esm', 'Master.esm'], is_esm=False)
    writer.adoption = MasterAdoption(_Index({MASTER_ACTI: WP.build_plane_activator(
        MASTER_ACTI, 'TES4WaterPlanePond', master_water)}))
    return writer


def test_child_reuses_its_masters_activator():
    """Same EditorID and same water: the planes name the master's record, none is shipped."""
    writer = _child(0x00000E01)
    planner = _planner(None, writer, {WATER_1: {'Signature': 'WATR', 'EditorID': 'Pond'}})
    plane, = planner.planes_for(_cell(water=WATER_1))
    assert dict(_subs(plane))[b'NAME'] == struct.pack('<I', MASTER_ACTI)
    assert planner.adopted == 1 and MASTER_ACTI in writer.adoption.adopted
    writer.adoption.finalize(writer)
    assert not _activators(writer)


def test_child_keeps_its_own_activator_when_the_masters_water_differs():
    """Same EditorID on another water: an own record under an own EditorID."""
    writer = _child(0x00000E55)
    planner = _planner(None, writer, {WATER_1: {'Signature': 'WATR', 'EditorID': 'Pond'}})
    plane, = planner.planes_for(_cell(water=WATER_1))
    writer.adoption.finalize(writer)
    acti, = _activators(writer)
    assert dict(_subs(plane))[b'NAME'] == acti[12:16] != struct.pack('<I', MASTER_ACTI)
    assert _header(acti)[2] >> 24 == 2 and not writer.adoption.adopted
    assert dict(_subs(acti))[b'EDID'] == b'TES4WaterPlanePond_00000E01\x00'


def test_group_builder_names_waters_from_the_whole_plugin(monkeypatch):
    """`_build_groups` hands the planner the plugin's WATR and its masters' export."""
    monkeypatch.setattr(PR, '_build_world_groups', lambda *a, **k: None)
    writer = _writer()
    own = {'CELL': [_cell(water=WATER_1), _cell(fid=CELL_B, water=WATER_2)]}
    st = SimpleNamespace(
        by_type=dict(own, WATR=[_watr(WATER_1, 'Pond')]), writer=writer, navm_metas=[],
        base_model_by_fid={}, door_fids=set(), navm_cache={},
        ctx=SimpleNamespace(master_export={WATER_2: {'Signature': 'WATR',
                                                     'EditorID': 'Sewer'}}))
    PR._build_groups(st, own, {}, lambda _label: None, 'own ')
    edids = sorted(dict(_subs(r))[b'EDID'] for r in _activators(writer))
    assert edids == [b'TES4WaterPlanePond\x00', b'TES4WaterPlaneSewer\x00']


def test_summary_line(capsys):
    """Cells, planes, activators and skips by reason reach the build log."""
    _build([_cell(), _cell(fid=CELL_B, height=SENTINEL), _cell(fid=CELL_C, flags=DRY_INTERIOR)],
           [_ref('00000C01', 0.0, 0.0), _ref('00000C02', PITCH, 0.0)])
    out = capsys.readouterr().out
    assert 'Interior water planes: 2 in 1 cell(s), 1 activator(s)' in out
    assert 'skipped, height is the no-height sentinel: 1' in out
