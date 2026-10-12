"""Editor-only marker geometry is stripped exactly when its engine hides it.

See: docs/commentary/asset_convert_nif.md#nodes-stripped-by-name
"""

import pytest

import asset_convert.nif.nif_converter as nc
from asset_convert.nif.nif_converter_morrowind import (
    convert_legacy_nodes, is_marker_shape, root_flag_extras)
from pyffi.formats.nif import NifFormat


def _shape(name):
    """A bare NiTriShape carrying `name`."""
    shape = NifFormat.NiTriShape()
    shape.name = name
    return shape


@pytest.mark.parametrize('name', [b'Tri EditorMarker', b'Tri EditorMarker.001',
                                  b'tri editormarker_cube_01'])
def test_mrk_root_hides_tri_marker(name):
    """Under an MRK root every "Tri EditorMarker*" shape is editor-only."""
    assert is_marker_shape(_shape(name), ['mrk'])


def test_no_mrk_keeps_tri_marker():
    """Without MRK Morrowind renders and collides with the shape, so it stays."""
    assert not is_marker_shape(_shape(b'Tri EditorMarker_cube_01'), [])


def test_mrk_spares_real_geometry():
    """MRK hides only the marker-named shapes."""
    assert not is_marker_shape(_shape(b'Tri Door'), ['mrk'])


def test_mrk_spares_nodes():
    """A node is never a marker shape; only geometry is."""
    node = NifFormat.NiNode()
    node.name = b'Tri EditorMarker'
    assert not is_marker_shape(node, ['mrk'])


@pytest.mark.parametrize('flags, stripped', [(['mrk'], True), ([], False)])
def test_walk_follows_the_latched_root(monkeypatch, flags, stripped):
    """The render walk strips a Morrowind marker only under an MRK source root."""
    monkeypatch.setattr(nc, 'source_root_flags', lambda: flags)
    assert nc._is_stripped_node(_shape(b'Tri EditorMarker')) is stripped


@pytest.mark.parametrize('name', [b'EditorMarker', b'SecretBigger01'])
def test_oblivion_names_still_strip(monkeypatch, name):
    """Oblivion's own marker names strip with no Morrowind flag."""
    monkeypatch.setattr(nc, 'source_root_flags', lambda: [])
    assert nc._is_stripped_node(_shape(name))


@pytest.mark.parametrize('text', [b'MRK', b'NCO'])
def test_legacy_root_rewrite_keeps_flags(text):
    """A Morrowind animated root rewritten as NiNode keeps its v4 flag extra."""
    data = NifFormat.Data(version=0x04000002)
    root = NifFormat.NiBSAnimationNode()
    root.extra_data = NifFormat.NiStringExtraData()
    root.extra_data.string_data = text
    data.roots = [root]
    data.blocks = [root, root.extra_data]
    assert convert_legacy_nodes(data) == 1
    assert root_flag_extras(data.roots[0]) == [text.decode().lower()]
