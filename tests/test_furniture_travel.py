"""Sit entries whose seat their authored enter animation sets (FNV's stool, pod, child bar).

See: docs/commentary/asset_convert_falloutnv.md#stool-entries
"""
import math
import os

import pytest

from asset_convert.nif.furniture_markers import marker_seats
from asset_convert.nif.furniture_travel import sit_travels
from pyffi.formats.nif import NifFormat
from tes5_import.record_types.items import IS_BAR_STOOL_KYWD, furn_keywords

#: StoolEnter.kf's Bip01 travel, entry to seat: 18.95 left, 47.20 ahead.
STOOL_TRAVEL = (-18.952112840799214, 47.199537034663)

FNV = os.path.join('export', 'FalloutNV.esm')
OBLIVION = os.path.join('export', 'Oblivion.esm')


def _marker(x, y, milliradians, ref):
    """One BSFurnitureMarker holding a single entry point."""
    marker = NifFormat.BSFurnitureMarker()
    marker.num_positions = 1
    marker.positions.update_size()
    pos = marker.positions[0]
    pos.offset.x, pos.offset.y, pos.offset.z = x, y, 0.0
    pos.orientation, pos.position_ref_1 = milliradians, ref
    return marker


def test_stool01_entry_lands_where_its_enter_animation_ends():
    """stool01's entry ends where StoolEnter.kf's root does, facing ahead."""
    (seat,), _shift = marker_seats([_marker(20.5, -41.0, 0, 15)], lambda: (0.0, 0.0), {15: STOOL_TRAVEL})
    assert (round(seat['x'], 1), round(seat['y'], 1), round(seat['heading'], 3)) == (1.5, 6.2, 0.0)


def test_travel_turns_with_the_entry():
    """Approaching along +X, ahead is +X and left is +Y."""
    (seat,), _shift = marker_seats([_marker(0.0, 0.0, int(math.pi / 2 * 1000), 15)],
                                   lambda: (0.0, 0.0), {15: STOOL_TRAVEL})
    assert (round(seat['x'], 1), round(seat['y'], 1)) == (47.2, 19.0)


def test_entry_without_an_animation_keeps_the_fixed_walk():
    """No authored travel: the Oblivion front walk, turning to sit."""
    (seat,), _shift = marker_seats([_marker(0.0, 0.0, 0, 14)], lambda: (0.0, 0.0), {15: STOOL_TRAVEL})
    assert (round(seat['x'], 1), round(seat['y'], 1), round(seat['heading'], 3)) == (0.0, 55.0, round(math.pi, 3))


def test_an_authored_seat_takes_the_bar_stool_keyword():
    """A walk-in-and-sit seat gets Skyrim's isBarStool; a plain chair gets none."""
    stool = marker_seats([_marker(20.5, -41.0, 0, 15)], lambda: (0.0, 0.0), {15: STOOL_TRAVEL})[0]
    chair = marker_seats([_marker(0.0, 0.0, 0, 14)], lambda: (0.0, 0.0), {15: STOOL_TRAVEL})[0]
    assert furn_keywords(stool) == [IS_BAR_STOOL_KYWD] == [0x00074EC7]
    assert furn_keywords(chair) == [] and furn_keywords(None) == []


def test_fnv_stool_travel_is_read_from_the_idle_tree_and_kf():
    """FalloutNV.esm's StoolSitting -> SitDownStool -> StoolEnter.kf gives marker 15 its travel."""
    if not os.path.isfile(os.path.join(FNV, 'IDLE.txt')):
        pytest.skip('FalloutNV.esm not exported')
    travels = sit_travels(FNV)
    if 15 not in travels:
        pytest.skip('FalloutNV.esm IDLE export predates ANAM.Parent')
    assert tuple(round(v, 2) for v in travels[15]) == (-18.95, 47.2)


def test_oblivion_authors_no_enter_animation():
    """Oblivion's marker idles name no .kf, so every seat keeps the walk rule."""
    if not os.path.isfile(os.path.join(OBLIVION, 'IDLE.txt')):
        pytest.skip('Oblivion.esm not exported')
    assert sit_travels(OBLIVION) == {}
