"""A transform controller on a mesh's root moves to an inner node, leaving the root to the placement.

See: docs/commentary/asset_convert_nif.md#root-transform-controller
"""
import pytest

from asset_convert.nif import grass_profile, nif_converter
from asset_convert.nif.nif_passes import ROOT_MOVER_SUFFIX, move_root_controller
from pyffi.formats.nif import NifFormat

#: NiTimeController.flags of the cases below: Active + clamp, clamp alone, and the manager bit.
ACTIVE, INACTIVE, MANAGED = 0x0C, 0x04, 0x20

#: Bit 3 of NiTimeController.flags, Active.
ACTIVE_BIT = 0x08

#: A quarter turn about Z, as the rows of a root's rotation.
QUARTER_TURN = ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0))


def _node(name, rows=None):
    """A NiNode at identity, or turned by `rows`."""
    node = NifFormat.NiNode()
    node.name = name
    node.scale = 1.0
    node.rotation.set_identity()
    for r, row in enumerate(rows or (), 1):
        for c, value in enumerate(row, 1):
            setattr(node.rotation, 'm_%d%d' % (r, c), value)
    return node


def _keys(group, values):
    """Fill one key group with linear keys a second apart."""
    group.num_keys = len(values)
    group.interpolation = 1
    group.keys.update_size()
    for i, value in enumerate(values):
        group.keys[i].time = float(i)
        if isinstance(value, tuple):
            group.keys[i].value.x, group.keys[i].value.y, group.keys[i].value.z = value
        else:
            group.keys[i].value = value


def _transform_controller(flags, turn=(), slide=(), data=True):
    """A NiTransformController keyed with Z angles `turn` and positions `slide`."""
    ctrl = NifFormat.NiTransformController()
    ctrl.flags = flags
    ctrl.frequency = 1.0
    ctrl.stop_time = 2.0
    ctrl.interpolator = NifFormat.NiTransformInterpolator()
    ctrl.interpolator.scale = 1.0
    ctrl.interpolator.rotation.w = 1.0
    if data:
        keys = ctrl.interpolator.data = NifFormat.NiTransformData()
        keys.rotation_type = 4
        keys.num_rotation_keys = 1 if turn else 0
        _keys(keys.xyz_rotations[2], turn)
        _keys(keys.translations, slide)
    return ctrl


def _box_collision(node, motion):
    """Give `node` a box body of Oblivion motion system `motion` (7 fixed, 6 keyframed)."""
    node.collision_object = NifFormat.bhkCollisionObject()
    node.collision_object.target = node
    body = node.collision_object.body = NifFormat.bhkRigidBody()
    body.motion_system = motion
    body.shape = NifFormat.bhkBoxShape()
    body.shape.dimensions.x = body.shape.dimensions.y = body.shape.dimensions.z = 1.0


def _mesh(flags=ACTIVE, rows=None, collision=0, part=b'Part', **keys):
    """(data, controller) of a source mesh whose root `Top` carries a transform controller.

    `collision` is the motion system of a body on the child `part`, 0 for none.
    """
    root = _node(b'Top', rows)
    root.add_child(_node(part))
    if collision:
        _box_collision(root.children[0], collision)
    ctrl = root.controller = _transform_controller(flags, **keys)
    ctrl.target = root
    data = NifFormat.Data(version=0x14000005, user_version=11, user_version_2=11)
    data.roots = [root]
    return data, ctrl


def _convert(**mesh):
    """(root, controller, stats) after the converter ran over `_mesh(**mesh)`."""
    data, ctrl = _mesh(**mesh)
    stats = nif_converter._convert_nif(data, src_path='mesh.nif')
    return data.roots[0], ctrl, stats


def _names(node):
    """The names of a node's children."""
    return [bytes(c.name) for c in node.children if c is not None]


class TestRootControllerMovesInward:
    """The root ends without a controller; one inner node holds it and the root's children."""

    def test_an_active_turning_controller_moves(self):
        """The controller, still active, sits on the new node with the root's children."""
        root, ctrl, stats = _convert(turn=(0.0, 0.7))
        mover = root.children[0]
        assert root.controller is None and _names(root) == [b'Top' + ROOT_MOVER_SUFFIX]
        assert mover.controller is ctrl and ctrl.target is mover
        assert ctrl.flags & ACTIVE_BIT and _names(mover) == [b'Part']
        assert stats['root_controllers_moved'] == 1

    @pytest.mark.parametrize('first, second', [(ACTIVE, INACTIVE), (INACTIVE, ACTIVE)])
    def test_a_chain_with_one_active_controller_moves_whole(self, first, second):
        """The inactive one rides along and stays inactive."""
        data, ctrl = _mesh(flags=first, turn=(0.0, 0.7))
        ctrl.next_controller = _transform_controller(second, turn=(0.0, 0.7))
        ctrl.next_controller.target = data.roots[0]
        nif_converter._convert_nif(data, src_path='mesh.nif')
        mover = data.roots[0].children[0]
        assert data.roots[0].controller is None and mover.controller is ctrl
        assert ctrl.next_controller.target is mover
        assert [bool(c.flags & ACTIVE_BIT) for c in (ctrl, ctrl.next_controller)] == [
            first == ACTIVE, second == ACTIVE]

    def test_a_dataless_stub_moves(self):
        """A controller whose interpolator has no key data moves all the same."""
        root, ctrl, _ = _convert(data=False)
        assert root.controller is None and ctrl.target is root.children[0]
        assert ctrl.interpolator.data is None

    def test_a_sliding_controller_moves_with_its_keys(self):
        """Translation keys reach the new node unchanged."""
        root, ctrl, _ = _convert(slide=((0.0, 0.0, 0.0), (0.0, -8.0, 0.0)))
        keys = ctrl.interpolator.data.translations.keys
        assert root.controller is None and ctrl.target is root.children[0]
        assert [k.value.y for k in keys] == [0.0, -8.0]

    def test_the_mover_sits_at_identity_under_an_unturned_root(self):
        """The new node adds no transform of its own and carries the root's flags."""
        root = _convert(turn=(0.0, 0.7))[0]
        mover = root.children[0]
        assert mover.controller is not None and mover.flags == root.flags
        assert mover.rotation.is_identity() and mover.scale == 1.0
        assert (mover.translation.x, mover.translation.y, mover.translation.z) == (0.0, 0.0, 0.0)

    def test_a_turned_root_s_wrapper_takes_the_controller(self):
        """The wrapper's node holds the root's turn and the controller; no second node is added."""
        root, ctrl, stats = _convert(rows=QUARTER_TURN, turn=(0.0, 0.7))
        mover = root.children[0]
        assert stats['root_rotation_baked'] == 1 and root.controller is None
        assert _names(root) == [b'Top'] and _names(mover) == [b'Part']
        assert mover.controller is ctrl and ctrl.target is mover
        assert (mover.rotation.m_12, mover.rotation.m_21) == (1.0, -1.0)

    def test_an_unturned_root_s_child_of_the_same_name_is_not_taken_for_the_wrapper(self):
        """Only a root the wrapper split reuses a child."""
        root = _convert(part=b'Top', turn=(0.0, 0.7))[0]
        assert _names(root) == [b'Top' + ROOT_MOVER_SUFFIX] and _names(root.children[0]) == [b'Top']

    def test_a_skinned_mesh_keeps_its_root_controller(self, monkeypatch):
        """The converter's own skin verdict reaches the pass."""
        monkeypatch.setattr(nif_converter, '_prepare_rig', lambda *args: True)
        root, ctrl, stats = _convert(turn=(0.0, 0.7))
        assert root.controller is ctrl and ctrl.target is root and _names(root) == [b'Part']
        assert 'root_controllers_moved' not in stats


def _rooted(ctrl, name=b'Top', kids=(b'Part',)):
    """A root holding `ctrl` and one child node per name in `kids`."""
    root = _node(name)
    for kid in kids:
        root.add_child(_node(kid))
    root.controller = ctrl
    ctrl.target = root
    return root


class TestWhichNodeTakesIt:
    """The wrapper's node only when the root still has the wrapper's shape; else a new node."""

    @pytest.mark.parametrize('wrapped, kids', [
        (False, (b'Top',)),            # an unwrapped root whose only child shares its name
        (True, (b'Top', b'Part')),     # more than one child
        (True, (b'Part',)),            # one child under another name
    ])
    def test_a_new_node_is_added(self, wrapped, kids):
        """A root that lacks the wrapper's exact shape gets its own node."""
        root = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)), kids=kids)
        assert move_root_controller(root, False, wrapped, {})
        mover = root.children[0]
        assert _names(root) == [b'Top' + ROOT_MOVER_SUFFIX] and _names(mover) == list(kids)
        assert mover.controller is not None and root.controller is None

    def test_a_shape_named_like_the_root_is_not_the_wrapper(self):
        """Geometry cannot hold the children, so it is never reused."""
        root = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)), kids=())
        shape = NifFormat.NiTriShape()
        shape.name = b'Top'
        root.add_child(shape)
        assert move_root_controller(root, False, True, {})
        assert root.children[0].children[0] is shape and shape.controller is None

    def test_a_keyframe_controller_moves_like_a_transform_controller(self):
        """The older controller class writes the same transform."""
        ctrl = NifFormat.NiKeyframeController()
        ctrl.flags = ACTIVE
        root = _rooted(ctrl)
        assert move_root_controller(root, False, False, {})
        assert root.controller is None and ctrl.target is root.children[0]

    def test_one_moving_controller_among_still_ones_is_counted(self):
        """One moving controller in the chain is enough for the count."""
        still = _transform_controller(ACTIVE, data=False)
        still.next_controller = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        root, stats = _rooted(still), {}
        _box_collision(root, 7)
        move_root_controller(root, False, False, stats)
        assert stats == {'root_controllers_moved': 1, 'root_movers_over_static_collision': 1}

    def test_an_inactive_controller_s_moving_keys_are_not_counted(self):
        """Only a controller that runs can carry the picture away from the body."""
        still = _transform_controller(ACTIVE, data=False)
        still.next_controller = _transform_controller(INACTIVE, turn=(0.0, 0.7))
        root, stats = _rooted(still), {}
        _box_collision(root, 7)
        assert move_root_controller(root, False, False, stats)
        assert stats == {'root_controllers_moved': 1}

    def test_the_new_node_s_name_is_the_root_s_plus_anim(self):
        """The suffix is part of the output: the docs and other tools name it."""
        root = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)))
        move_root_controller(root, False, False, {})
        assert ROOT_MOVER_SUFFIX == b' Anim' and _names(root) == [b'Top Anim']

    def test_a_node_root_without_children_gets_an_empty_node(self):
        """A childless NiNode root is not exempt: the controller still leaves the root."""
        root, stats = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)), kids=()), {}
        assert move_root_controller(root, False, False, stats)
        mover = root.children[0]
        assert _names(root) == [b'Top' + ROOT_MOVER_SUFFIX] and mover.num_children == 0
        assert root.controller is None and mover.controller.target is mover
        assert stats == {'root_controllers_moved': 1}


def _chain(node):
    """A node's controllers in chain order."""
    chain, ctrl = [], node.controller
    while ctrl is not None:
        chain.append(ctrl)
        ctrl = ctrl.next_controller
    return chain


def _other(cls, flags=ACTIVE):
    """A controller of another class, by name."""
    ctrl = getattr(NifFormat, cls)()
    ctrl.flags = flags
    return ctrl


def _chained(ctrls, **rooted):
    """A root whose controller chain is `ctrls` in order, each targeting the root."""
    for ctrl, after in zip(ctrls, ctrls[1:]):
        ctrl.next_controller = after
    root = _rooted(ctrls[0], **rooted)
    for ctrl in ctrls:
        ctrl.target = root
    return root


class TestOnlyTransformControllersMove:
    """The root's own transform controllers leave; every other controller stays where it was."""

    @pytest.mark.parametrize('place', [0, 1, 2])
    def test_the_others_stay_on_the_root_in_their_order(self, place):
        """First, middle or last in the chain: one moves, two stay, both chains end."""
        turn = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        others = [_other('NiVisController'), _other('NiFloatExtraDataController')]
        root = _chained(others[:place] + [turn] + others[place:])
        assert move_root_controller(root, False, False, {})
        mover = root.children[0]
        assert [c is o for c, o in zip(_chain(root), others)] == [True, True]
        assert [c.target is root for c in others] == [True, True]
        assert _chain(mover) == [turn] and turn.target is mover
        assert turn.next_controller is None and others[1].next_controller is None

    def test_two_transform_controllers_keep_their_order_around_others(self):
        """An inactive one rides along behind the active one; the rest close ranks."""
        first = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        second = _transform_controller(INACTIVE, turn=(0.0, 0.7))
        seen, value = _other('NiVisController'), _other('NiFloatExtraDataController')
        root = _chained([first, seen, second, value])
        assert move_root_controller(root, False, False, {})
        mover = root.children[0]
        assert [c is o for c, o in zip(_chain(mover), (first, second))] == [True, True]
        assert [c is o for c, o in zip(_chain(root), (seen, value))] == [True, True]
        assert len(_chain(mover)) == len(_chain(root)) == 2
        assert first.target is mover and second.target is mover
        assert seen.target is root and value.target is root
        assert not second.flags & ACTIVE_BIT

    def test_an_extra_data_controller_stays_with_its_extra_data(self):
        """The value it writes is the root's, so both stay on the root."""
        turn = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        value = _other('NiFloatExtraDataController')
        root = _chained([turn, value])
        amount = NifFormat.NiFloatExtraData()
        amount.name = b'Amount'
        root.add_extra_data(amount)
        assert move_root_controller(root, False, False, {})
        mover = root.children[0]
        assert root.controller is value and value.target is root
        assert list(root.extra_data_list) == [amount] and mover.num_extra_data_list == 0

    def test_a_visibility_controller_stays_through_the_converter(self):
        """End to end: the root keeps the visibility controller, the mover takes the turn."""
        data, ctrl = _mesh(turn=(0.0, 0.7))
        seen = ctrl.next_controller = _other('NiVisController')
        seen.target = data.roots[0]
        nif_converter._convert_nif(data, src_path='mesh.nif')
        root = data.roots[0]
        mover = root.children[0]
        assert root.controller is seen and seen.target is root
        assert seen.next_controller is None and ctrl.next_controller is None
        assert mover.controller is ctrl and ctrl.target is mover

    def test_a_transform_controller_a_manager_drives_stays(self):
        """It is not the root's own, so it stays beside the one that moves."""
        turn = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        driven = _transform_controller(ACTIVE | MANAGED, turn=(0.0, 0.7))
        root = _chained([driven, turn])
        assert move_root_controller(root, False, False, {})
        mover = root.children[0]
        assert _chain(root) == [driven] and driven.target is root
        assert _chain(mover) == [turn] and turn.target is mover


class TestOtherTransformWritersAreCounted:
    """A path or look-at controller is left on the root; an active one is counted.

    See: docs/commentary/asset_convert_nif.md#root-transform-controller
    """

    @pytest.mark.parametrize('cls', ['NiPathController', 'NiLookAtController'])
    @pytest.mark.parametrize('flags, counted', [
        (ACTIVE, 1), (INACTIVE, 0), (ACTIVE | MANAGED, 0)])
    def test_alone_on_the_root(self, cls, flags, counted):
        """Nothing moves; only one that runs on its own is counted."""
        ctrl, stats = _other(cls, flags), {}
        root = _chained([ctrl])
        assert not move_root_controller(root, False, False, stats)
        assert root.controller is ctrl and ctrl.target is root and _names(root) == [b'Part']
        assert stats == ({'root_path_lookat_controllers_kept': 1} if counted else {})

    def test_beside_a_transform_controller_that_moves(self):
        """The transform controller moves; the path controller stays and is counted."""
        turn, path, stats = _transform_controller(ACTIVE, turn=(0.0, 0.7)), _other('NiPathController'), {}
        root = _chained([turn, path])
        assert move_root_controller(root, False, False, stats)
        assert root.controller is path and path.target is root
        assert stats == {'root_controllers_moved': 1, 'root_path_lookat_controllers_kept': 1}

    def test_through_the_converter(self):
        """The block survives the conversion on the root and the count reaches the mesh's stats."""
        root = _chained([_other('NiPathController', 0x0C)])
        data = NifFormat.Data(version=0x14000005, user_version=11, user_version_2=11)
        data.roots = [root]
        stats = nif_converter._convert_nif(data, src_path='mesh.nif')
        assert type(data.roots[0].controller).__name__ == 'NiPathController'
        assert stats['root_path_lookat_controllers_kept'] == 1 and 'root_controllers_moved' not in stats

    @pytest.mark.parametrize('skinned, name', [(True, b'Top'), (False, b'NPC Root [Root]')])
    def test_an_exempt_root_is_not_counted(self, skinned, name):
        """The count covers the roots the rule looks at."""
        root, stats = _chained([_other('NiPathController')], name=name), {}
        assert not move_root_controller(root, skinned, False, stats) and not stats


class TestRootControllerLeftAlone:
    """Roots the rule does not touch."""

    def _unmoved(self, root, has_skin=False):
        """True when the pass leaves `root`'s controller and children as they were."""
        ctrl, stats = root.controller, {}
        moved = move_root_controller(root, has_skin, False, stats)
        return (not moved and not stats and root.controller is ctrl
                and ctrl.target is root and _names(root) == [b'Part'])

    def test_a_root_with_a_controller_manager(self):
        """A manager first in the chain keeps everything on the root."""
        manager = NifFormat.NiControllerManager()
        manager.next_controller = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        assert self._unmoved(_rooted(manager))

    def test_a_manager_after_the_transform_controller(self):
        """A manager anywhere in the chain keeps everything on the root."""
        ctrl = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        ctrl.next_controller = NifFormat.NiControllerManager()
        assert self._unmoved(_rooted(ctrl))

    def test_a_controller_a_manager_drives(self):
        """The manager bit alone exempts the controller."""
        assert self._unmoved(_rooted(
            _transform_controller(ACTIVE | MANAGED, turn=(0.0, 0.7))))

    @pytest.mark.parametrize('mesh', [
        {'turn': (0.0, 0.7)},
        {'collision': 7, 'turn': (0.0, 0.7)},
        {'rows': QUARTER_TURN, 'turn': (0.0, 0.7)},
        {'data': False},
    ])
    def test_a_root_whose_transform_controllers_are_all_inactive(self, mesh):
        """Nothing is moved: no node, no count, the bit left clear."""
        root, ctrl, stats = _convert(flags=INACTIVE, **mesh)
        holder = root.children[0] if mesh.get('rows') else root
        assert root.controller is ctrl and ctrl.target is root and not ctrl.flags & ACTIVE_BIT
        assert _names(holder) == ([] if mesh.get('collision') else [b'Part'])
        assert not [k for k in stats if k.startswith('root_') and 'moved' in k or 'movers' in k]

    def test_two_inactive_controllers(self):
        """No controller in the chain is active, so nothing moves."""
        ctrl = _transform_controller(INACTIVE, turn=(0.0, 0.7))
        ctrl.next_controller = _transform_controller(INACTIVE, turn=(0.0, 0.7))
        assert self._unmoved(_rooted(ctrl))

    def test_an_active_controller_a_manager_drives_beside_an_inactive_own_one(self):
        """Only the root's own controllers are asked for the active bit."""
        ctrl = _transform_controller(INACTIVE, turn=(0.0, 0.7))
        ctrl.next_controller = _transform_controller(ACTIVE | MANAGED, turn=(0.0, 0.7))
        assert self._unmoved(_rooted(ctrl))

    def test_a_root_with_no_transform_controller(self):
        """Another controller class, active or not, is not a reason to move."""
        ctrl = NifFormat.NiVisController()
        ctrl.flags = ACTIVE
        assert self._unmoved(_rooted(ctrl))

    def test_a_skinned_mesh(self):
        """Skinned meshes are exempt."""
        root = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)))
        assert self._unmoved(root, has_skin=True)

    def test_a_rig_root(self):
        """The rig root keeps its controller: the engine binds the actor by that node."""
        root = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)),
                            name=b'NPC Root [Root]')
        assert self._unmoved(root)

    def test_a_root_that_holds_no_children(self):
        """A geometry root has no children to hand over and is left alone."""
        shape = NifFormat.NiTriShape()
        ctrl = shape.controller = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        assert not move_root_controller(shape, False, False, {}) and shape.controller is ctrl

    def test_the_same_roots_move_without_the_exemption(self):
        """The control: the same root without an exemption does move."""
        root = _rooted(_transform_controller(ACTIVE, turn=(0.0, 0.7)))
        assert move_root_controller(root, False, False, {}) and root.controller is None


class TestCollisionStaysOnTheRoot:
    """A child's collision is hoisted to the root first, so it does not ride the mover."""

    def test_child_collision_ends_on_the_root(self):
        """The body is the root's; the mover carries only the controller."""
        root, ctrl, _ = _convert(collision=7, turn=(0.0, 0.7))
        mover = root.children[0]
        owners = [b for b in root.tree() if getattr(b, 'collision_object', None) is not None]
        assert owners == [root] and root.collision_object.target is root
        assert mover.controller is ctrl and root.controller is None

    def test_the_emptied_collision_node_is_gone_before_the_move(self):
        """The hoist clears the node it emptied only among the root's own children."""
        root = _convert(collision=7, turn=(0.0, 0.7))[0]
        assert _names(root.children[0]) == []

    @pytest.mark.parametrize('motion', [7, 6])
    def test_the_root_body_is_static(self, motion):
        """A keyframed source body too: nothing named like the root moves."""
        body = _convert(collision=motion, turn=(0.0, 0.7))[0].collision_object.body
        assert body.mass == 0.0 and body.motion_system == 5

    @pytest.mark.parametrize('mesh, counted', [
        ({'collision': 7, 'turn': (0.0, 0.7)}, 1),
        ({'collision': 7, 'slide': ((0.0, 0.0, 0.0), (0.0, -8.0, 0.0))}, 1),
        ({'collision': 7, 'turn': (0.3, 0.3)}, 0),
        ({'collision': 7, 'data': False}, 0),
        ({'turn': (0.0, 0.7)}, 0),
    ])
    def test_a_mover_above_static_collision_is_counted(self, mesh, counted):
        """The count of meshes whose picture now moves away from a body that stays.

        See: docs/commentary/asset_convert_nif.md#root-transform-controller
        """
        stats = _convert(**mesh)[2]
        assert stats['root_controllers_moved'] == 1
        assert stats.get('root_movers_over_static_collision', 0) == counted

    def test_collision_that_rides_the_mover_is_counted_too(self):
        """The count asks for any collision in the mesh, also one under the mover.

        A wrapped root keeps its child's collision below the wrapper's node, which the controller moves.
        See: docs/commentary/asset_convert_nif.md#root-transform-controller
        """
        root, ctrl, stats = _convert(rows=QUARTER_TURN, collision=7, turn=(0.0, 0.7))
        mover = root.children[0]
        assert root.collision_object is None and mover.controller is ctrl
        assert mover.children[0].collision_object is not None
        assert stats['root_movers_over_static_collision'] == 1


class TestWhereThePassRuns:
    """Every root of every mesh kind the converter walks, each move counted."""

    def test_a_root_that_stays_a_plain_node(self):
        """A sky mesh keeps its NiNode root and its controller moves all the same."""
        data, ctrl = _mesh(turn=(0.0, 0.7))
        stats = nif_converter._convert_nif(data, src_path='meshes/sky/stars.nif')
        root = data.roots[0]
        assert type(root).__name__ == 'NiNode' and stats['root_converted'] == 0
        assert root.controller is None and ctrl.target is root.children[0]
        assert stats['root_controllers_moved'] == 1

    def test_a_creature_mesh(self):
        """The creature path runs the pass too."""
        data, ctrl = _mesh(turn=(0.0, 0.7))
        stats = nif_converter._convert_nif(data, src_path='mesh.nif', creature=True)
        assert data.roots[0].controller is None and ctrl.target is data.roots[0].children[0]
        assert stats['root_controllers_moved'] == 1

    def test_two_roots_in_one_file_add_up(self):
        """Both counters count roots, not files."""
        data, _ = _mesh(collision=7, turn=(0.0, 0.7))
        second = _mesh(collision=7, turn=(0.0, 0.7))[0].roots[0]
        second.name = b'Second'
        data.roots = [data.roots[0], second]
        stats = nif_converter._convert_nif(data, src_path='mesh.nif')
        assert [_names(r) for r in data.roots] == [[b'Top Anim'], [b'Second Anim']]
        assert stats['root_controllers_moved'] == 2
        assert stats['root_movers_over_static_collision'] == 2


def _grass_file(path, animated):
    """Write a grass-shaped mesh: a fade root, one inner node holding a shape, `animated` or not."""
    root = NifFormat.BSFadeNode()
    root.name = b'Top'
    root.scale = 1.0
    root.rotation.set_identity()
    inner = _node(b'Top' + ROOT_MOVER_SUFFIX)
    shape = NifFormat.NiTriShape()
    shape.name = b'Blade'
    shape.scale = 1.0
    shape.rotation.set_identity()
    inner.add_child(shape)
    root.add_child(inner)
    if animated:
        inner.controller = _transform_controller(ACTIVE, turn=(0.0, 0.7))
        inner.controller.target = inner
    data = NifFormat.Data(version=0x14000005, user_version=11, user_version_2=11)
    data.roots = [root]
    nif_converter._upgrade_version(data)
    with open(path, 'wb') as out:
        data.write(out)


def _read(path):
    """The mesh at `path`."""
    data = NifFormat.Data()
    with open(path, 'rb') as src:
        data.read(src)
    return data


class TestGrassUnderAMover:
    """The grass pass cannot flatten a node that carries a controller; it says so.

    See: docs/commentary/asset_convert_nif.md#root-transform-controller
    """

    def test_an_animated_node_keeps_its_geometry_and_is_reported(self, tmp_path, capsys):
        """The mesh is left nested as before, and one warning names the file."""
        path = tmp_path / 'blade.nif'
        _grass_file(path, animated=True)
        assert grass_profile.animated_wrappers(_read(path)) == 1
        grass_profile.apply_grass_profile(path)
        printed = capsys.readouterr().out
        assert printed.count('WARNING') == 1 and 'blade.nif' in printed and '1 animated node' in printed
        root = _read(path).roots[0]
        assert _names(root) == [b'Top' + ROOT_MOVER_SUFFIX]
        assert _names(root.children[0]) == [b'Blade'] and root.children[0].controller is not None

    def test_strips_count_as_geometry_and_an_empty_node_does_not(self):
        """Only an animated node that holds a shape of either kind is counted."""
        data, ctrl = _mesh(turn=(0.0, 0.7))
        nif_converter._convert_nif(data, src_path='mesh.nif')
        assert grass_profile.animated_wrappers(data) == 0
        data.roots[0].children[0].add_child(NifFormat.NiTriStrips())
        assert grass_profile.animated_wrappers(data) == 1

    def test_a_mesh_without_a_fade_root_counts_nothing(self):
        """The grass pass only reads a fade-node root."""
        data = _mesh(turn=(0.0, 0.7))[0]
        assert grass_profile.animated_wrappers(data) == 0

    def test_a_plain_wrapper_is_flattened_without_a_word(self, tmp_path, capsys):
        """The control: with no controller the node is baked away and nothing is printed."""
        path = tmp_path / 'blade.nif'
        _grass_file(path, animated=False)
        assert grass_profile.animated_wrappers(_read(path)) == 0
        assert grass_profile.apply_grass_profile(path)
        assert 'WARNING' not in capsys.readouterr().out
        assert _names(_read(path).roots[0]) == [b'Blade']
