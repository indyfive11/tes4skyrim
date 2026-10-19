"""
Measurements of a Morrowind head or hair body part: shape features and texture color.

A head's features are proportions of its own height, so heads of any scale
compare; the importer turns each into a Skyrim face slider by how far it sits
from the other heads of its race and sex.

See: docs/commentary/tes5_import_actors.md#morrowind-faces
"""

import os
from pathlib import Path

import numpy as np
from PIL import Image

from asset_convert.character.skin_retarget import m44_to_np
from asset_convert.nif.shaders import base_texture_path
from asset_convert.nif.sse_nif import read_nif
from asset_convert.sources.morrowind_assets import find_archived_file, resolve_mesh

#: Feature names, in the order `head_features` returns them.
FEATURES = ('nose_forward', 'nose_drop', 'jaw_width', 'chin_forward',
            'chin_length', 'brow_forward', 'cheek_width')

#: Height bands (fractions of head height from the bottom) the features read.
_CHIN, _JAW, _NOSE = (0.0, 0.15), (0.05, 0.3), (0.3, 0.62)
_CHEEK, _BROW, _CROWN = (0.45, 0.62), (0.62, 0.85), (0.85, 1.0)

#: Image extensions a Morrowind texture name may resolve to, preferred first.
_TEXTURE_EXTS = ('.dds', '.tga', '.bmp')

#: Alpha below which a texel is a cut-out, not color.
_OPAQUE = 128


def part_geometry(nif_path: str):
    """(vertices (n, 3) in the part's root frame, base texture name) of a body-part NIF."""
    data = read_nif(nif_path)
    root = data.roots[0]
    verts, texture = [], ''
    for block in data.blocks:
        shape_data = getattr(block, 'data', None)
        if not hasattr(shape_data, 'vertices') or not shape_data.num_vertices or block is root:
            continue
        v = np.array([[p.x, p.y, p.z, 1.0] for p in shape_data.vertices])
        verts.append((v @ m44_to_np(block.get_transform(root)))[:, :3])
        texture = texture or _shape_texture(block)
    return (np.vstack(verts) if verts else np.zeros((0, 3))), texture


def _shape_texture(shape) -> str:
    """The base texture a shape's NiTexturingProperty names, or ''."""
    for prop in getattr(shape, 'properties', None) or []:
        if prop is not None and hasattr(prop, 'base_texture'):
            return bytes(base_texture_path(prop)).decode('latin-1')
    return ''


def _band(verts: np.ndarray, z0: float, height: float, band) -> np.ndarray:
    """The vertices whose height lies in `band` (fractions of `height` above `z0`)."""
    z = (verts[:, 2] - z0) / height
    return verts[(z >= band[0]) & (z <= band[1])]


def _forward(verts: np.ndarray) -> float:
    """How far forward (toward -Y, where a Morrowind face looks) a band reaches."""
    return float(-verts[:, 1].min()) if len(verts) else 0.0


def head_features(verts: np.ndarray):
    """The FEATURES of a head's vertices as proportions of its height, or None."""
    if len(verts) < 20:
        return None
    z0, height = verts[:, 2].min(), np.ptp(verts[:, 2])
    if height <= 0:
        return None
    bands = {b: _band(verts, z0, height, b) for b in (_CHIN, _JAW, _NOSE, _CHEEK, _BROW, _CROWN)}
    if any(len(b) == 0 for b in bands.values()):
        return None
    brow = _forward(bands[_BROW])
    tip = bands[_NOSE][np.argmin(bands[_NOSE][:, 1])]
    brow_tip = bands[_BROW][np.argmin(bands[_BROW][:, 1])]
    return np.array([_forward(bands[_NOSE]) - brow,
                     brow_tip[2] - tip[2],
                     np.ptp(bands[_JAW][:, 0]),
                     _forward(bands[_CHIN]) - brow,
                     tip[2] - z0,
                     brow - _forward(bands[_CROWN]),
                     np.ptp(bands[_CHEEK][:, 0])]) / height


def resolve_texture(roots, name: str, export_root):
    """A Morrowind texture `name` (any extension) under `roots`, then the vanilla install; else None.

    `roots` are texture folders; `name` is relative to one, as a NIF names it.
    """
    stem = os.path.splitext(name.replace('/', chr(92)).strip(chr(92)).lower())[0]
    if stem.startswith('textures' + chr(92)):
        stem = stem[len('textures') + 1:]
    for ext in _TEXTURE_EXTS:
        for root in roots:
            path = Path(root) / (stem + ext)
            if path.is_file():
                return path
        path = find_archived_file(export_root, 'textures' + chr(92) + stem + ext)
        if path:
            return path
    return None


def texture_color(path):
    """The per-channel median (r, g, b) of a texture's opaque texels, or None."""
    try:
        with Image.open(path) as img:
            px = np.asarray(img.convert('RGBA').resize((64, 64)), np.float64).reshape(-1, 4)
    except (OSError, ValueError):
        return None
    px = px[px[:, 3] >= _OPAQUE]
    if not len(px):
        return None
    return tuple(int(c) for c in np.median(px[:, :3], axis=0))


def measure_part(mesh: str, mesh_roots, tex_roots, export_root, is_head: bool):
    """(head features or None, texture color or None) of the body part whose mesh is `mesh`.

    A mesh that is missing or fails to read measures as (None, None).
    """
    path = resolve_mesh(mesh_roots, mesh, export_root)
    if path is None:
        return None, None
    try:
        verts, texture = part_geometry(str(path))
    except Exception:
        return None, None
    tex = resolve_texture(tex_roots, texture, export_root) if texture else None
    return head_features(verts) if is_head else None, texture_color(tex) if tex else None


def measure_job(job):
    """Pool worker: `measure_part(*job)`."""
    return measure_part(*job)
