"""Reading a Morrowind animation timeline: its text-key groups and its key tracks.

A Morrowind NIF animates on ONE timeline. `NiTextKeyExtraData` keys cut it
into named groups (`Idle: Start`, `Idle: Loop Start`, `Idle2: Stop`), and each
`NiKeyframeData` holds a node's keys over the whole of it. The creature split
and the object-animation pass both cut clips out of that timeline, so the
reading lives here once.

See: docs/commentary/tes4_export_morrowind.md#creatures
See: docs/commentary/asset_convert_animation.md#morrowind-object-animation
"""

import numpy as np

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

#: NiKeyframeData.rotation_type XYZ_ROTATION_KEY: three float curves, not quaternions.
XYZ_ROTATION_KEY = 4


def chain(first, link: str):
    """Every block on a singly linked NIF chain starting at `first`."""
    block = first
    while block is not None:
        yield block
        block = getattr(block, link, None)


def text_keys(data) -> list:
    """(time, text) for every text key in the file."""
    out = []
    for block in data.blocks:
        if isinstance(block, NifFormat.NiTextKeyExtraData):
            out.extend((float(k.time), k.value.decode('cp1252', 'replace'))
                       for k in block.text_keys)
    return out


def groups(keys: list) -> tuple:
    """({group: {event: time}}, [(time, cue kind, cue value)]) from the text keys.

    A key may carry several lines; each is `Group: Event` or a cue
    (`SoundGen: Left`, `Sound: SwishL`).
    """
    found, cues = {}, []
    for time, text in keys:
        for line in text.replace(chr(13), chr(10)).split(chr(10)):
            head, sep, tail = line.partition(':')
            if not sep:
                continue
            head, tail = head.strip().lower(), tail.strip()
            if head in ('soundgen', 'sound'):
                cues.append((time, head, tail))
            else:
                found.setdefault(head, {}).setdefault(tail.lower(), time)
    return found, cues


def clip_range(events: dict):
    """(start, stop, loops) for one group, or None when it has no span.

    A group with a real loop segment ships only that segment, because Skyrim
    loops whole clips; a one-shot ships Start..Stop.
    """
    start, stop = events.get('start'), events.get('stop')
    if start is None or stop is None or stop <= start:
        return None
    loop_start, loop_stop = events.get('loop start'), events.get('loop stop')
    if loop_start is not None and loop_stop is not None and loop_stop > loop_start:
        return loop_start, loop_stop, True
    return start, stop, False


def interp(times: np.ndarray, channel_keys: tuple) -> np.ndarray:
    """Linear interpolation of a (key times, one value row per key) channel at `times`."""
    key_times, values = channel_keys
    return np.stack([np.interp(times, key_times, values[:, i])
                     for i in range(values.shape[1])], axis=1)


def channel(keys, row):
    """(key times, `row(value)` per key) of one keyframe channel, or None when it is empty."""
    if not len(keys):
        return None
    return (np.array([float(k.time) for k in keys], dtype=np.float64),
            np.array([row(k.value) for k in keys], dtype=np.float64))


def key_arrays(kd) -> tuple:
    """(rotation, translation, scale) channels of one node, read once; euler rotations are skipped.

    Quaternions are sign-aligned to their predecessor so interpolation
    takes the short arc.
    """
    rotation = None
    if kd.rotation_type != XYZ_ROTATION_KEY and kd.num_rotation_keys:
        rotation = channel(kd.quaternion_keys, lambda v: (v.w, v.x, v.y, v.z))
        quats = rotation[1]
        for i in range(1, len(quats)):
            if np.dot(quats[i], quats[i - 1]) < 0:
                quats[i] = -quats[i]
    return (rotation,
            channel(kd.translations.keys, lambda v: (v.x, v.y, v.z)),
            channel(kd.scales.keys, lambda v: (v,)))


def euler_arrays(kd) -> list:
    """The three per-axis float channels of an XYZ-rotation node, else []."""
    if kd.rotation_type != XYZ_ROTATION_KEY:
        return []
    return [channel(axis.keys, lambda v: (v,)) for axis in kd.xyz_rotations]
