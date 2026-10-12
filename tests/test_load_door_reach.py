"""A Morrowind-placed door that never swings gets collision deep enough to pick.

NifFormat comes from `sse_nif`, which applies pyffi's clock patch on import.
See: docs/commentary/asset_convert_nif.md#a-load-door-needs-reach
"""

from asset_convert.nif.sse_nif import NifFormat
from asset_convert.collision.cms import decode_cms_materials
from asset_convert.collision.cms_builder import (GAME_UNITS_PER_HAVOK,
                                                 build_cms_collision)
from asset_convert.nif.door_anim_morrowind import (LOAD_DOOR_REACH,
                                                   give_door_reach,
                                                   widen_load_door)
from asset_convert.nif.door_plan import places_morrowind_doors


def _box(half_x, half_y, half_z):
    """A bhkBoxShape with the given half-extents, in game units."""
    box = NifFormat.bhkBoxShape()
    box.dimensions.x = half_x / GAME_UNITS_PER_HAVOK
    box.dimensions.y = half_y / GAME_UNITS_PER_HAVOK
    box.dimensions.z = half_z / GAME_UNITS_PER_HAVOK
    return box


def _door(shape):
    """A door root carrying `shape` as its static collision body."""
    root = NifFormat.NiNode()
    body = NifFormat.bhkRigidBody()
    body.shape = shape
    collision = NifFormat.bhkCollisionObject()
    collision.body = body
    root.collision_object = collision
    return root


def _thin(box):
    """The box's smallest half-extent, back in game units, to 2 places."""
    dims = box.dimensions
    return round(min(dims.x, dims.y, dims.z) * GAME_UNITS_PER_HAVOK, 2)


def test_thin_panel_gains_reach():
    """A panel too thin for Skyrim's pick grows to LOAD_DOOR_REACH."""
    panel = _box(64.5, 2.09, 92.8)
    assert widen_load_door(_door(panel))
    assert _thin(panel) == LOAD_DOOR_REACH


def test_width_and_height_are_kept():
    """Only the thin axis moves, so the door still fits its frame."""
    panel = _box(64.5, 2.09, 92.8)
    widen_load_door(_door(panel))
    assert round(panel.dimensions.x * GAME_UNITS_PER_HAVOK, 2) == 64.5
    assert round(panel.dimensions.z * GAME_UNITS_PER_HAVOK, 2) == 92.8


def test_thick_panel_is_left_alone():
    """A panel already deep enough is not touched."""
    panel = _box(64.5, LOAD_DOOR_REACH + 1.0, 92.8)
    assert not widen_load_door(_door(panel))


def test_only_the_broadest_box_grows():
    """The panel gains reach; the latch box beside it does not."""
    panel, latch = _box(64.5, 2.09, 92.8), _box(1.3, 7.0, 11.2)
    shape = NifFormat.bhkListShape()
    shape.num_sub_shapes = 2
    shape.sub_shapes.update_size()
    shape.sub_shapes[0], shape.sub_shapes[1] = panel, latch
    assert widen_load_door(_door(shape))
    assert _thin(panel) == LOAD_DOOR_REACH
    assert _thin(latch) == 1.3


def test_a_swinging_door_is_skipped():
    """A door that swings keeps its panel, so it cannot clip its frame."""
    panel = _box(64.5, 2.09, 92.8)
    root = _door(panel)
    root.controller = NifFormat.NiControllerManager()
    assert not widen_load_door(root)
    assert _thin(panel) == 2.09


def test_a_swing_on_a_child_node_is_skipped():
    """The swing manager need not sit on the root to make it a swinging door."""
    panel = _box(64.5, 2.09, 92.8)
    root = _door(panel)
    hinge = NifFormat.NiNode()
    hinge.controller = NifFormat.NiControllerManager()
    root.num_children = 1
    root.children.update_size()
    root.children[0] = hinge
    assert not widen_load_door(root)


def _sheet(y):
    """Two triangles forming a flat quad at the given `y`."""
    corners = [(-49.5, y, -75.6), (44.5, y, -75.6),
               (44.5, y, 86.1), (-49.5, y, 86.1)]
    return [(corners[0], corners[1], corners[2]),
            (corners[0], corners[2], corners[3])]


def _span(tris, axis):
    """How far the triangle list reaches along `axis`."""
    pts = [p[axis] for tri in tris for p in tri]
    return max(pts) - min(pts)


def test_a_flat_sheet_becomes_a_slab():
    """Collision authored as one plane gains 2 * reach of depth."""
    thick = give_door_reach(_sheet(-2.2), 5.0)
    assert round(_span(thick, 1), 2) == 10.0


def test_a_thin_solid_is_deepened_to_the_same_depth():
    """Collision with some depth already ends at 2 * reach, not more."""
    thick = give_door_reach(_sheet(-2.2) + _sheet(1.8), 18.0)
    assert round(_span(thick, 1), 2) == 36.0


def test_deepening_keeps_width_and_height():
    """Only the thin axis grows, so the door still fits its frame."""
    flat = _sheet(-2.2)
    thick = give_door_reach(flat, 5.0)
    assert round(_span(thick, 0), 2) == round(_span(flat, 0), 2)
    assert round(_span(thick, 2), 2) == round(_span(flat, 2), 2)


def test_a_deep_shape_is_left_alone():
    """Collision already 2 * reach deep is untouched."""
    solid = _sheet(-2.2) + _sheet(8.0)
    assert give_door_reach(solid, 5.0) == solid


def test_no_collision_stays_empty():
    """An empty triangle list survives the pass unchanged."""
    assert give_door_reach([], 5.0) == []


def _havok(tris):
    """Game-unit triangles scaled to havok units."""
    return [tuple(tuple(c / GAME_UNITS_PER_HAVOK for c in p) for p in t)
            for t in tris]


def test_a_flat_mesh_door_is_rebuilt_deep_with_its_materials():
    """A compressed-mesh door panel is rebuilt 2 * reach deep, materials kept."""
    wood, metal = (NifFormat.SkyrimHavokMaterial.MATWOOD,
                   NifFormat.SkyrimHavokMaterial.MATSOLIDMETAL)
    mopp = build_cms_collision(_havok(_sheet(-2.2)), [wood, metal], NifFormat)
    root = _door(mopp)
    assert widen_load_door(root)
    rows = decode_cms_materials(root.collision_object.body.shape.shape.data)
    depth = _span([tri for tri, _m in rows], 1) * GAME_UNITS_PER_HAVOK
    assert round(depth) == 2 * LOAD_DOOR_REACH
    assert {mat for _tri, mat in rows} == {wood, metal}


def _export(tmp_path, name, source=None):
    """A minimal export record dir with a `_HEADER.txt`."""
    folder = tmp_path / name
    folder.mkdir()
    lines = ['HEDR.Version=1.0'] + ([f'Source={source}'] if source else [])
    (folder / '_HEADER.txt').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return folder


def test_tes3_and_morroblivion_doors_are_morrowind_placed(tmp_path):
    """A TES3 export and the Morroblivion plugin both place Morrowind doors."""
    assert places_morrowind_doors(_export(tmp_path, 'TR_Mainland.esm', 'TES3'))
    assert places_morrowind_doors(_export(tmp_path, 'Morrowind_ob.esm'))


def test_oblivion_doors_are_not_morrowind_placed(tmp_path):
    """Oblivion's own doors keep their authored collision."""
    assert not places_morrowind_doors(_export(tmp_path, 'Oblivion.esm'))
