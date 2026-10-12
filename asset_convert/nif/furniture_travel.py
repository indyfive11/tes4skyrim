"""Sit entries whose seat an authored enter animation sets.

A plugin chooses a furniture's sit-down animation through its IDLE tree: an
IDLE conditioned on GetFurnitureMarkerID == N has a descendant conditioned on
GetSitting == 2 (getting ready to sit). Where that descendant names a .kf, the
sitter's seat is where its Bip01 root ends, relative to where it starts.
FalloutNV.esm authors three; Oblivion.esm and Nehrim.esm author none.

See: docs/commentary/asset_convert_falloutnv.md#stool-entries
"""

import os
import struct

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()

from pyffi.formats.nif import NifFormat

from core.plugin_masters import export_root, master_chain, master_export_dir
from output_layout import assets_for
from tes5_import.base.text_reader import parse_export_file

#: Condition functions that pick a sit-down idle, the same index in TES4 and FO3/FNV.
GET_SITTING, GET_FURNITURE_MARKER_ID = 159, 160

#: GetSitting's "getting ready to sit" state: the enter animation plays.
GETTING_READY_TO_SIT = 2.0

#: The skeleton root whose translation carries the sitter.
_ROOT_BONE = b'Bip01'

#: Where an exported IDLE names its parent: FO3/FNV keep it in ANAM, TES4 in DATA.
_PARENT_KEYS = ('ANAM.Parent', 'DATA.IdleParent')

#: Sit-travel sub-map key inside the asset stage's wearable plan.
SIT_TRAVEL_KEY = '*sit_travels*'

#: Record folder -> {marker id: (right, forward) travel}, built once per process.
_TRAVELS = {}

#: The travels of the plan the NIF now converting belongs to.
_LATCH = [{}]


def latch_sit_travels(plan) -> None:
    """Record the sit travels of `plan` for the NIF about to convert."""
    _LATCH[0] = (plan or {}).get(SIT_TRAVEL_KEY, {})


def latched_sit_travels() -> dict:
    """{marker id: travel} for the NIF now converting."""
    return _LATCH[0]


def sit_travels(record_dir) -> dict:
    """{marker id: (right, forward)}: each authored enter animation's root travel, entry to seat.

    Read from the plugin's own IDLE tree, then its masters', nearest first.
    """
    if not record_dir:
        return {}
    record_dir = str(record_dir)
    if record_dir not in _TRAVELS:
        root = export_root(record_dir)
        dirs = [record_dir] + [master_export_dir(root, name)
                               for name in reversed(master_chain(record_dir))]
        _TRAVELS[record_dir] = _travels(dirs)
    return _TRAVELS[record_dir]


def _travels(dirs: list) -> dict:
    """{marker id: travel} over `dirs`; an earlier folder's tree wins per marker id."""
    out = {}
    for folder in dirs:
        for marker, modl in _sit_down_models(folder).items():
            if marker in out:
                continue
            travel = _root_travel(_find_model(dirs, modl))
            if travel is not None:
                out[marker] = travel
    return out


def _conditions(rec: dict) -> list:
    """[(operator bits, function, comparison value)] of one exported IDLE."""
    out = []
    for i in range(int(rec.get('ConditionCount', '0') or 0)):
        raw = bytes.fromhex(rec.get(f'Condition[{i}].Raw', ''))
        if len(raw) >= 10:
            out.append((raw[0] & 0xE0, struct.unpack_from('<H', raw, 8)[0],
                        struct.unpack_from('<f', raw, 4)[0]))
    return out


def _sit_down_models(folder: str) -> dict:
    """{marker id: .kf model} from one plugin's IDLE tree."""
    path = os.path.join(folder, 'IDLE.txt')
    if not os.path.isfile(path):
        return {}
    recs = parse_export_file(path)
    children = {}
    for rec in recs:
        parent = next((rec[k] for k in _PARENT_KEYS if rec.get(k)), None)
        children.setdefault(parent, []).append(rec)
    out = {}
    for rec in recs:
        for op, func, value in _conditions(rec):
            if op == 0 and func == GET_FURNITURE_MARKER_ID:
                modl = _sit_down_model(rec, children)
                if modl:
                    out.setdefault(int(value), modl)
    return out


def _sit_down_model(rec: dict, children: dict) -> str:
    """The .kf of the first descendant of `rec` that plays while getting ready to sit, or ''."""
    queue = list(children.get(rec.get('FormID'), []))
    while queue:
        child = queue.pop(0)
        if any(func == GET_SITTING and value == GETTING_READY_TO_SIT
               for _op, func, value in _conditions(child)):
            modl = child.get('Model.MODL', '')
            if modl.lower().endswith('.kf'):
                return modl
        queue.extend(children.get(child.get('FormID'), []))
    return ''


def _find_model(dirs: list, modl: str) -> 'str | None':
    """The first `meshes/<modl>` file in the asset trees of `dirs`, or None."""
    rel = modl.replace(chr(92), '/')
    for folder in dirs:
        path = os.path.join(str(assets_for(folder)), 'meshes', rel)
        if os.path.isfile(path):
            return path
    return None


def _root_travel(kf_path: 'str | None') -> 'tuple | None':
    """(right, forward) the Bip01 root moves from first key to last, or None."""
    if kf_path is None:
        return None
    data = NifFormat.Data()
    with open(kf_path, 'rb') as fh:
        data.read(fh)
    for seq in data.roots:
        for block in getattr(seq, 'controlled_blocks', []):
            if block.get_node_name() == _ROOT_BONE and block.interpolator is not None:
                keys = _translations(block.interpolator)
                if len(keys) >= 2:
                    return keys[-1][0] - keys[0][0], keys[-1][1] - keys[0][1]
    return None


def _translations(interp) -> list:
    """Every translation key of a transform interpolator, in time order."""
    if isinstance(interp, NifFormat.NiBSplineInterpolator):
        return [tuple(p) for p in interp.get_translations()]
    data = getattr(interp, 'data', None)
    if data is None:
        return []
    return [(k.value.x, k.value.y, k.value.z) for k in data.translations.keys]
