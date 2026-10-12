"""The root-collision hoist: it checks the node it will move, and carries a nested owner's whole transform.

See: docs/commentary/asset_convert_collision.md#every-collision-owner
"""
import os

import pytest

from asset_convert.nif.nif_converter import convert_nif
from pyffi.formats.nif import NifFormat

#: Oblivion's ruins gate: a still frame and a swinging leaf, each with collision.
RUINS_GATE = os.path.join('export', 'Oblivion.esm', 'meshes', 'dungeons', 'ruininteriors', 'doors',
                          'ruinsgateshort01.nif')

#: A hanging cage whose walls sit under a chain node 798 units down.
CAGE = os.path.join('export', 'Oblivion.esm', 'meshes', 'dungeons', 'caves', 'ctorturecage01.nif')


def _convert(src, tmp_path):
    """The converted root of an exported NIF; skips when the NIF is not exported."""
    if not os.path.isfile(src):
        pytest.skip(f'{src} not exported')
    out = str(tmp_path / os.path.basename(src))
    convert_nif(src, out, fix_textures=False)
    data = NifFormat.Data()
    with open(out, 'rb') as fh:
        data.read(fh)
    return data.roots[0]


def test_a_moving_leaf_keeps_its_collision(tmp_path):
    """The hoist would take the leaf, which animates, so nothing is hoisted and the leaf keeps its own."""
    root = _convert(RUINS_GATE, tmp_path)
    owners = {bytes(n.name) for n in root.tree()
              if isinstance(n, NifFormat.NiNode) and n.collision_object is not None}
    assert root.collision_object is None and b'RuinsDoorShortA01' in owners


def test_a_nested_owner_is_hoisted_with_its_ancestors_transform(tmp_path):
    """The cage wall lands at its chain's depth, -1094.8, not its own -296.7."""
    root = _convert(CAGE, tmp_path)
    body = root.collision_object.body
    assert round(body.translation.z * 70.0, 0) == -1095.0
