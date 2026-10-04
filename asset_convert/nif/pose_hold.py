"""One-frame pose-hold sequences for script-driven animated objects.

Skyrim saves a reference's behaviour-graph state and, on load, re-enters it
with its generator restarted at t=0.  A converted object used to stay in its
transition state after moving, so every load replayed the motion.  Vanilla
leaves a transition on the `End` event for a one-frame idle; this module
synthesises that idle, `<Seq>Hold`, frozen at the transition's last keys.

Planning never changes the NIF; applying only registers what was planned.
See: docs/commentary/asset_convert_animation.md#end-hold-states
"""

import collections

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

from asset_convert.havok.hkx_animobject import LOAD_SEQUENCES
from asset_convert.nif.sequences import (CYCLE_CLAMP, CYCLE_LOOP,
                                         SCRIPT_DRIVEN_SEQUENCES,
                                         clone_sequence_as)

#: A hold sequence is named after the transition whose end pose it freezes.
HOLD_SUFFIX = 'Hold'

#: Length of a hold: one frame, as every vanilla idle beside a transition.
HOLD_SECONDS = 1.0 / 30.0

#: nif.xml KeyType LINEAR_KEY: the only key type written here, as it carries no tangents.
KEY_LINEAR = 1

#: NiTransformData.rotation_type XYZ_ROTATION_KEY: three Euler key groups, not quaternions.
XYZ_ROTATION_KEY = 4

#: Slack when comparing a key time with a sequence's stop time.
TIME_EPSILON = 1e-4

#: One hold to register: the source's root and block, both names, and its interpolators.
PlannedHold = collections.namedtuple(
    'PlannedHold', 'root sequence name hold interpolators')

#: class name -> (constant field, key data class); None keeps the hold dataless.
_VALUE_INTERPOLATORS = {
    'NiFloatInterpolator': ('float_value', 'NiFloatData'),
    'NiPoint3Interpolator': ('point_3_value', 'NiPosData'),
    'NiBoolInterpolator': ('bool_value', None),
    'NiBoolTimelineInterpolator': ('bool_value', None),
}

#: Sequences an object plays from load; they are states to rest in, not transitions.
_LOAD_NAMES = frozenset(name.lower() for name in LOAD_SEQUENCES)


class Refused(Exception):
    """A sequence whose end pose cannot be frozen; the message says why."""


def _assign(owner, field, value):
    """Set `owner.field` to `value`, copying a vector or quaternion by axis."""
    axes = [axis for axis in 'wxyz' if hasattr(value, axis)]
    if not axes:
        setattr(owner, field, value)
        return
    for axis in axes:
        setattr(getattr(owner, field), axis, getattr(value, axis))


def _last_value(keys, stop):
    """Value of the last of `keys`; refuses unsorted keys or one past `stop`."""
    times = [key.time for key in keys]
    if times != sorted(times):
        raise Refused('unsorted keys')
    if times[-1] > stop + TIME_EPSILON:
        raise Refused('key past stop time')
    return keys[-1].value


def _set_hold_keys(keys, value):
    """Fill the two-key array `keys`: LINEAR, at 0 and HOLD_SECONDS, both `value`."""
    for key, when in zip(keys, (0.0, HOLD_SECONDS)):
        key.arg = KEY_LINEAR
        key.time = when
        _assign(key, 'value', value)


def _freeze_group(src, dst, stop):
    """Give key group `dst` two LINEAR keys at `src`'s last value.

    False, leaving `dst` empty, when `src` has no keys.
    """
    if not src.num_keys:
        return False
    value = _last_value(list(src.keys), stop)
    dst.num_keys = 2
    dst.interpolation = KEY_LINEAR
    dst.keys.update_size()
    _set_hold_keys(dst.keys, value)
    return True


def _freeze_rotation(src, dst, stop):
    """Freeze the rotation channel of transform data `src` into `dst`.

    Euler rotation stays Euler, one group per axis; quaternion keys of any
    type become two LINEAR quaternion keys.  False when nothing is keyed.
    """
    if not src.num_rotation_keys:
        return False
    if int(src.rotation_type) == XYZ_ROTATION_KEY:
        dst.rotation_type = XYZ_ROTATION_KEY
        dst.num_rotation_keys = 1
        return any([_freeze_group(a, b, stop)
                    for a, b in zip(src.xyz_rotations, dst.xyz_rotations)])
    value = _last_value(list(src.quaternion_keys), stop)
    dst.rotation_type = KEY_LINEAR
    dst.num_rotation_keys = 2
    dst.quaternion_keys.update_size()
    _set_hold_keys(dst.quaternion_keys, value)
    return True


def _hold_transform(src, stop):
    """A transform interpolator frozen at `src`'s last keys.

    `src` itself when it carries no key at all: it is already a constant.
    """
    if src.data is None:
        return src
    data = NifFormat.NiTransformData()
    keyed = [_freeze_rotation(src.data, data, stop),
             _freeze_group(src.data.translations, data.translations, stop),
             _freeze_group(src.data.scales, data.scales, stop)]
    if not any(keyed):
        return src
    hold = NifFormat.NiTransformInterpolator()
    for field in ('translation', 'rotation', 'scale'):
        _assign(hold, field, getattr(src, field))
    hold.data = data
    return hold


def _hold_value(src, stop):
    """A float, point3 or bool interpolator frozen at `src`'s last key.

    Float and point3 keep key data, as vanilla's idles do; a bool becomes a
    dataless constant.  `src` itself when it carries no key.
    See: docs/commentary/asset_convert_animation.md#hold-interpolator-forms
    """
    field, data_class = _VALUE_INTERPOLATORS[type(src).__name__]
    group = src.data.data if src.data is not None else None
    if group is None or not group.num_keys:
        return src
    hold = type(src)()
    if data_class is None:
        hold.bool_value = bool(_last_value(list(group.keys), stop))
        return hold
    _assign(hold, field, getattr(src, field))
    hold.data = getattr(NifFormat, data_class)()
    _freeze_group(group, hold.data.data, stop)
    return hold


def hold_interpolator(src, stop):
    """The interpolator a hold uses in place of `src`, frozen at time `stop`.

    Null, dataless and keyless interpolators are shared as they are.  Raises
    Refused for a class no constant can stand in for.
    """
    if src is None:
        return None
    name = type(src).__name__
    if name == 'NiTransformInterpolator':
        return _hold_transform(src, stop)
    if name in _VALUE_INTERPOLATORS:
        return _hold_value(src, stop)
    raise Refused('unsupported interpolator ' + name)


def _sequence_name(seq):
    """A sequence's name as text."""
    raw = getattr(seq, 'name', b'') or b''
    return raw.decode('latin-1') if isinstance(raw, bytes) else str(raw)


def _managed_sequences(data):
    """[(root, sequence, name)] for every sequence a controller manager holds."""
    managers = [(root, block) for root in data.roots if root is not None
                for block in root.tree()
                if isinstance(block, NifFormat.NiControllerManager)]
    return [(root, seq, _sequence_name(seq)) for root, manager in managers
            for seq in manager.controller_sequences if seq is not None]


def _ends_on_end_key(seq):
    """Whether the sequence's LAST text key is `end`, sitting at its stop time."""
    keys = seq.text_keys
    if keys is None or not keys.num_text_keys:
        return False
    last = keys.text_keys[keys.num_text_keys - 1]
    return (bytes(last.value or b'').lower() == b'end'
            and abs(last.time - seq.stop_time) < TIME_EPSILON)


def eligible(seq, name):
    """Whether `seq` is a one-shot, script-driven transition that raises End.

    CLAMP only: a LOOP sequence passes its `end` key every cycle and would
    freeze.  A load sequence is a state to rest in, and a sequence at
    frequency 0 never reaches its end.
    """
    low = name.lower()
    return (low in SCRIPT_DRIVEN_SEQUENCES and low not in _LOAD_NAMES
            and int(seq.cycle_type) == CYCLE_CLAMP and seq.frequency > 0
            and getattr(seq, 'manager', None) is not None
            and _ends_on_end_key(seq))


def _hold_text_keys():
    """A fresh `start`/`end` pair, so no sound key of the source replays."""
    block = NifFormat.NiTextKeyExtraData()
    block.num_text_keys = 2
    block.text_keys.update_size()
    block.text_keys[0].time, block.text_keys[0].value = 0.0, b'start'
    block.text_keys[1].time, block.text_keys[1].value = HOLD_SECONDS, b'end'
    return block


def plan_pose_holds(data, seq_names):
    """(planned holds, refusals) for graph sequences `seq_names`; `data` is unchanged.

    Judges the first sequence of each name that has controlled blocks, the one
    the graph plays.  It earns a hold when eligible, its hold name is free and
    every interpolator can be frozen; a refusal is (name, reason).
    """
    found = _managed_sequences(data)
    taken = {name for _, _, name in found}
    judged, planned, refused = set(), [], []
    for root, seq, name in found:
        if name in judged or not seq.num_controlled_blocks:
            continue
        judged.add(name)
        hold = name + HOLD_SUFFIX
        if name not in seq_names or hold in taken or not eligible(seq, name):
            continue
        try:
            interps = [hold_interpolator(block.interpolator, float(seq.stop_time))
                       for block in seq.controlled_blocks]
        except Refused as why:
            refused.append((name, str(why)))
            continue
        planned.append(PlannedHold(root, seq, name, hold, interps))
    return planned, refused


def loop_sequences(data, seq_names):
    """The graph sequences `seq_names` that never finish: cycle type LOOP only.

    Judges the first sequence of each name that has controlled blocks, the one
    the graph plays.  REVERSE finishes like CLAMP, so it is not a loop.  A
    looping state reads as playing for as long as it is active.
    See: docs/commentary/asset_convert_animation.md#playing-variable
    """
    judged, loops = set(), set()
    for _root, seq, name in _managed_sequences(data):
        if name in judged or not seq.num_controlled_blocks:
            continue
        judged.add(name)
        if name in seq_names and int(seq.cycle_type) == CYCLE_LOOP:
            loops.add(name)
    return loops


def hold_names(planned):
    """{sequence name: hold name} of a plan, the map the graph is built from."""
    return {plan.name: plan.hold for plan in planned}


def missing_holds(data, planned):
    """Planned hold names the NIF does not hold; empty once they are applied."""
    present = {name for _, _, name in _managed_sequences(data)}
    return sorted(set(hold_names(planned).values()) - present)


def apply_pose_holds(planned):
    """Register every planned hold beside its sequence; returns `hold_names`.

    The hold covers all of its source's controlled blocks, in order, sharing
    the controllers; only the interpolators and the text keys are its own.
    """
    for plan in planned:
        hold = clone_sequence_as(plan.root, plan.sequence, plan.hold, CYCLE_CLAMP)
        hold.weight = plan.sequence.weight
        hold.start_time = 0.0
        hold.stop_time = HOLD_SECONDS
        hold.text_keys = _hold_text_keys()
        for block, interp in zip(hold.controlled_blocks, plan.interpolators):
            block.interpolator = interp
    return hold_names(planned)
