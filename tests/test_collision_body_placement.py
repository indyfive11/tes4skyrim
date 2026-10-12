"""_body_placements: a rigid body is placed at its OWNING NODE's transform.

`collision_from_data` used to emit every body in its own frame, assuming
conversion always moves collision to the root.  ImperialDungeon01 places
`prisonsecretwall01.nif`, which keeps its two boxes on the child nodes `bed`
(translation 12.8, 170.0, -125.8) and `wall` (-61.6, 214.7, -64.0): both were
emitted ~200u away at the mesh origin, and the navmesh built against that
phantom while the engine put the collision where the nodes say.

See: docs/commentary/asset_convert_collision.md#body-placement
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from asset_convert.collision import collision_extract as ce


class _Body(object):
    """Stand-in for a bhkRigidBody; identity only, never inspected here."""


class _CollisionObject(object):
    """The bhkCollisionObject linking a node to its body."""

    def __init__(self, body):
        """Hold the body this node collides with."""
        self.body = body


class _Vec(object):
    """A NIF translation triple."""

    def __init__(self, x, y, z):
        """Hold the three components."""
        self.x, self.y, self.z = x, y, z


class _Rot(object):
    """A NIF 3x3 rotation, row-major as m_<row><col>."""

    def __init__(self, rows):
        """Expand the row list into the m_ij attributes pyffi exposes."""
        for i, row in enumerate(rows, 1):
            for j, val in enumerate(row, 1):
                setattr(self, 'm_%d%d' % (i, j), val)


class _Node(object):
    """A NiNode with an optional collision object and children."""

    def __init__(self, translation=(0.0, 0.0, 0.0), rotation=None,
                 scale=1.0, body=None, children=()):
        """Build one node of the test tree."""
        self.translation = _Vec(*translation)
        self.rotation = _Rot(rotation) if rotation else None
        self.scale = scale
        self.collision_object = _CollisionObject(body) if body else None
        self.children = list(children)


class _Data(object):
    """A parsed NIF stand-in exposing just `roots`."""

    def __init__(self, root):
        """Hold the single root."""
        self.roots = [root]


def test_root_mounted_body_gets_no_placer():
    """The common case must stay free: an identity chain records nothing."""
    body = _Body()
    data = _Data(_Node(body=body))
    assert ce._body_placements(data) == {}


def test_child_translation_is_applied():
    """A body on a moved child lands at that child's translation."""
    body = _Body()
    data = _Data(_Node(children=[
        _Node(translation=(12.83, 170.01, -125.77), body=body)]))
    place = ce._body_placements(data)[id(body)]
    assert place((0.0, 0.0, 0.0)) == pytest.approx(
        (12.83, 170.01, -125.77))


def test_translation_accumulates_through_the_chain():
    """Nested nodes compose: the engine honours the whole chain, not one hop."""
    body = _Body()
    data = _Data(_Node(translation=(10.0, 0.0, 0.0), children=[
        _Node(translation=(0.0, 5.0, 0.0), children=[
            _Node(translation=(0.0, 0.0, 2.0), body=body)])]))
    place = ce._body_placements(data)[id(body)]
    assert place((1.0, 1.0, 1.0)) == pytest.approx((11.0, 6.0, 3.0))


def test_rotation_is_applied_to_the_point():
    """A 180-degree Z rotation flips X and Y (rfswitchpressureplate01)."""
    body = _Body()
    data = _Data(_Node(children=[
        _Node(rotation=[[-1.0, 0.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, 1.0]],
              body=body)]))
    place = ce._body_placements(data)[id(body)]
    assert place((3.0, 4.0, 5.0)) == pytest.approx((-3.0, -4.0, 5.0))


def test_scale_multiplies_before_translation():
    """Node scale scales the point, then the translation offsets it."""
    body = _Body()
    data = _Data(_Node(children=[
        _Node(translation=(100.0, 0.0, 0.0), scale=2.0, body=body)]))
    place = ce._body_placements(data)[id(body)]
    assert place((3.0, 0.0, 0.0)) == pytest.approx((106.0, 0.0, 0.0))


def _body_t(w, z, translation):
    """A bhkRigidBodyT turned about Z by quaternion (w, 0, 0, z), translated in Skyrim havok units."""
    from pyffi.formats.nif import NifFormat
    body = NifFormat.bhkRigidBodyT()
    body.rotation.w, body.rotation.x, body.rotation.y, body.rotation.z = w, 0.0, 0.0, z
    body.translation.x, body.translation.y, body.translation.z = translation
    return body


def test_body_transform_places_the_shape():
    """The hoisted cage wall's bodyT (z -15.641 havok) puts its shape below.

    See: docs/commentary/asset_convert_collision.md#nested-owner-transform
    """
    place = ce._body_transform(_body_t(1.0, 0.0, (0.0, 0.0, -15.641)))
    assert place((1.0, 2.0, 3.0)) == pytest.approx((1.0, 2.0, 3.0 - 15.641 * 70.0))


def test_body_transform_rotates_before_translating():
    """A quarter turn about Z sends +X to +Y, then the translation offsets it."""
    half = 0.5 ** 0.5
    place = ce._body_transform(_body_t(half, half, (1.0, 0.0, 0.0)))
    assert place((10.0, 0.0, 0.0)) == pytest.approx((70.0, 10.0, 0.0))


def test_identity_body_transform_places_nothing():
    """A plain-identity bodyT (every folded CMS body) adds no placer."""
    assert ce._body_transform(_body_t(1.0, 0.0, (0.0, 0.0, 0.0))) is None
