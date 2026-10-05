"""Door-axis scan jobs (asset_convert/collision/collision_extract.py).

See: docs/commentary/tes5_import_pipeline.md#phase-0-stale-bounds-cache
"""

import os

from asset_convert.collision.collision_extract import _door_axis_jobs
from asset_convert.game_paths import current_namespace


def test_door_model_in_a_mixed_case_tree_is_scanned(tmp_path):
    """A lowercase DOOR MODL finds `Meshes/Dungeons/FRDoor01.NIF`; the cache key stays lowercase."""
    nif = tmp_path / 'meshes' / 'Dungeons' / 'FRDoor01.NIF'
    nif.parent.mkdir(parents=True)
    nif.write_bytes(b'nif')
    (tmp_path / 'DOOR.txt').write_text('Model.MODL=Dungeons\\\\FRDoor01.nif\n')
    [(found, key)] = _door_axis_jobs(str(tmp_path))
    assert os.path.samefile(found, nif)
    assert key == current_namespace() + '/dungeons/frdoor01.nif'
