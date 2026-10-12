"""scan_door_axes: a plugin with no door meshes still leaves a current cache.

See: docs/commentary/tes5_import_navmesh.md#door-base-line-is-local-y
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert.collision import collision_extract as ce


def test_no_doors_writes_a_current_cache(tmp_path):
    """No DOOR.txt: the scan classifies nothing yet the cache reads as current."""
    dest = str(tmp_path / 'door_panel_axis_cache.json')
    assert ce.scan_door_axes(str(tmp_path), dest) == 0
    assert ce.door_axis_cache_is_current(dest)
