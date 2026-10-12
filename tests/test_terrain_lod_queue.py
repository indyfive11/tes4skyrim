"""The terrain-LOD tile queue covers the whole inclusive cell range.

See: docs/commentary/asset_convert_terrain.md#lod-invents-terrain-over-cells-with-no-land
"""

import pytest

from asset_convert.lod import terrain_lod
from asset_convert.lod.terrain_lod import LOD_LEVELS


def _block(bounds):
    """Every cell of an inclusive (min_x, min_y, max_x, max_y) rectangle, as LAND."""
    x0, y0, x1, y1 = bounds
    return {(x, y): True for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}


@pytest.mark.parametrize('bounds', [(0, 0, 32, 32), (-20, -20, 22, 20),
                                    (-7, -9, -4, -8), (0, 0, 0, 0),
                                    (3, 3, 5, 5), (-33, -1, -32, 0)])
def test_the_queue_is_every_tile_holding_a_cell_of_the_range(bounds):
    """At each level the tiles are exactly those a cell of the inclusive range falls in."""
    lands = _block(bounds)
    want = {((x // n) * n, (y // n) * n, n) for x, y in lands for n in LOD_LEVELS}

    work = terrain_lod._queue_tiles(lands, bounds, 'Wrld7', None)

    assert sorted(t[:3] for t in work) == sorted(want)
    assert {t[3] for t in work} == {'Wrld7'}


def test_a_tile_with_no_land_is_not_queued():
    """Only tiles holding a LAND cell are queued, however wide the range."""
    lands = {(0, 0): True, (41, 41): True}

    work = terrain_lod._queue_tiles(lands, (0, 0, 41, 41), 'W', None)

    assert sorted(t[:3] for t in work if t[2] == 4) == [(0, 0, 4), (40, 40, 4)]


def test_only_cells_keeps_the_tiles_those_cells_touch():
    """An override run queues a tile only when one of its changed cells is inside it."""
    lands = _block((0, 0, 9, 9))

    work = terrain_lod._queue_tiles(lands, (0, 0, 9, 9), 'W', {(9, 9)})

    assert sorted(t[:3] for t in work) == [
        (0, 0, 16), (0, 0, 32), (8, 8, 4), (8, 8, 8)]
