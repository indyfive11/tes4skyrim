"""The mean colour of every land texture, read once per import for the LAND layer fold.

A quadrant can draw six textures; a layer past that is folded into the kept
layer whose texture looks most like it, so it needs each texture's colour.
See: docs/commentary/asset_convert_terrain.md#land-layer-fold
"""

from asset_convert import case_paths

from ..base.master_export import records_of
from ..base.text_reader import remap_formid
from .common import get_str

#: Side of the thumbnail a texture's mean colour is taken over.
_THUMB_SIDE = 64

#: Case-paths site name for the lookups.
_SITE = 'land.colour'

#: Output LTEX FormID -> (r, g, b) mean of its diffuse; built by `load_land_colours`.
_COLOURS = {}


def _mean_rgb(path):
    """(r, g, b) mean of the image at `path`, or None when it cannot be read."""
    from PIL import Image
    try:
        with Image.open(path) as img:
            rgb = img.convert('RGB')
            rgb.thumbnail((_THUMB_SIDE, _THUMB_SIDE))
            pixels = list(rgb.getdata())
    except (OSError, ValueError):
        return None
    if not pixels:
        return None
    return tuple(sum(p[c] for p in pixels) / len(pixels) for c in range(3))


def _ltex_records(by_type: dict, master_export) -> list:
    """(output FormID, LTEX record) of the masters', then the plugin's own."""
    out = []
    for key, rec in records_of(master_export, 'LTEX'):
        try:
            out.append((remap_formid(int(key, 16)), rec))
        except ValueError:
            continue
    for rec in by_type.get('LTEX', []):
        try:
            out.append((remap_formid(int(rec.get('FormID', ''), 16)), rec))
        except ValueError:
            continue
    return out


def load_land_colours(by_type: dict, master_export, roots) -> int:
    """Index the mean colour of each LTEX's diffuse under `roots` (texture folders); the count.

    A texture that cannot be found or read gets no colour, and its layer is
    never folded.
    See: docs/commentary/asset_convert_terrain.md#land-layer-fold
    """
    _COLOURS.clear()
    missing = 0
    for fid, rec in _ltex_records(by_type, master_export):
        icon = get_str(rec, 'ICON')
        path = case_paths.resolve(roots, icon, _SITE) if icon else None
        rgb = _mean_rgb(path) if path is not None else None
        if rgb is None:
            missing += 1
            continue
        _COLOURS[fid] = rgb
    print(f'  Land texture colours: {len(_COLOURS)} read, {missing} without a readable diffuse')
    return len(_COLOURS)


def land_colour(fid: int):
    """The mean colour of land texture `fid`, or None."""
    return _COLOURS.get(fid)


def colour_distance(a: int, b: int):
    """Distance between two land textures' mean colours, or None when either is unknown."""
    ca, cb = _COLOURS.get(a), _COLOURS.get(b)
    if ca is None or cb is None:
        return None
    return sum((x - y) ** 2 for x, y in zip(ca, cb)) ** 0.5
