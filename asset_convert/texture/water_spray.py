"""A water spray texture whose alpha follows the spray's shape.

Oblivion's spray textures were made for an additive blend: their shape is in
the brightness over a flat background, and their alpha is near zero or opaque
everywhere. Under Skyrim's normal blend that draws nothing, or a box.
See: docs/commentary/asset_convert_shader.md#water-spray-alpha
"""

import os
import struct

import numpy as np

from asset_convert.nif.flipbook import decode_dds

#: Border band, in texels, whose median brightness is the spray's background level.
_EDGE_BAND = 8

#: An alpha above this mean, and above `_OPAQUE_MIN` everywhere, holds no shape.
_OPAQUE_MEAN = 200.0
_OPAQUE_MIN = 128.0

#: Factor on an authored alpha that still holds the shape.
_ALPHA_GAIN = 2.0

#: Edge fade width at 256 texels and more; narrower sides scale it down to `_FADE_MIN`.
_FADE_TEXELS = 24.0
_FADE_FULL_SIDE = 256
_FADE_MIN = 4.0

#: Texels at the very edge that stay fully clear.
_FADE_OFFSET = 2.0

#: Share of the distance to white the colour keeps: white spray, like Skyrim's.
_TOWARD_WHITE = 0.4

#: DDS header: magic + 124-byte header, flags CAPS|HEIGHT|WIDTH|PITCH|PIXELFORMAT|MIPMAPCOUNT.
_DDS_FLAGS = 0x1 | 0x2 | 0x4 | 0x8 | 0x1000 | 0x20000

#: DDPF_RGB | DDPF_ALPHAPIXELS, 32 bits, BGRA masks.
_PIXEL_FORMAT = (32, 0x41, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)

#: DDSCAPS_TEXTURE | DDSCAPS_MIPMAP | DDSCAPS_COMPLEX.
_DDS_CAPS = 0x1000 | 0x400000 | 0x8


def _shape(lum: np.ndarray) -> np.ndarray:
    """0-255: how far each texel's brightness rises above the edge band's level."""
    band = _EDGE_BAND
    edge = float(np.median(np.r_[lum[:band].ravel(), lum[-band:].ravel(),
                                 lum[:, :band].ravel(), lum[:, -band:].ravel()]))
    return np.clip((lum - edge) / max(float(lum.max()) - edge, 1.0), 0, 1) * 255


def _edge_fade(h: int, w: int) -> np.ndarray:
    """A smoothstep from clear at the quad's edge to 1 inside it."""
    side = min(h, w)
    fade = _FADE_TEXELS if side >= _FADE_FULL_SIDE else max(_FADE_MIN, side * _FADE_TEXELS / _FADE_FULL_SIDE)
    rows = np.minimum(np.arange(h), np.arange(h)[::-1])
    cols = np.minimum(np.arange(w), np.arange(w)[::-1])
    t = np.clip((np.minimum.outer(rows, cols) - _FADE_OFFSET) / fade, 0, 1)
    return t * t * (3 - 2 * t)


def spray_rgba(rgba: np.ndarray) -> np.ndarray:
    """The spray look of an RGBA float image (H, W, 4), 0-255.

    See: docs/commentary/asset_convert_shader.md#water-spray-alpha
    """
    out = rgba.astype(float).copy()
    shape = _shape(out[..., :3].mean(-1))
    alpha = out[..., 3]
    opaque = float(alpha.mean()) > _OPAQUE_MEAN and float(alpha.min()) > _OPAQUE_MIN
    out[..., 3] = shape if opaque else np.clip(np.maximum(alpha * _ALPHA_GAIN, shape), 0, 255)
    out[..., 3] *= _edge_fade(*out.shape[:2])
    out[..., :3] = 255 - (255 - out[..., :3]) * _TOWARD_WHITE
    return out


def _halve(img: np.ndarray) -> np.ndarray:
    """The next mip level: each side longer than 1 texel averaged in pairs."""
    h, w = img.shape[:2]
    if h > 1:
        img = img[:h // 2 * 2].reshape(h // 2, 2, w, 4).mean(1)
    if w > 1:
        img = img[:, :w // 2 * 2].reshape(img.shape[0], w // 2, 2, 4).mean(2)
    return img


def _mip_chain(rgba: np.ndarray) -> list:
    """BGRA bytes of every level down to 1x1."""
    levels, cur = [], rgba
    while True:
        levels.append(cur[..., [2, 1, 0, 3]].round().clip(0, 255).astype(np.uint8).tobytes())
        if cur.shape[0] == 1 and cur.shape[1] == 1:
            return levels
        cur = _halve(cur)


def bgra_dds(rgba: np.ndarray) -> bytes:
    """An uncompressed 32-bit DDS of `rgba` with its full mip chain."""
    h, w = rgba.shape[:2]
    levels = _mip_chain(rgba)
    hdr = bytearray(128)
    hdr[0:4] = b'DDS '
    struct.pack_into('<7I', hdr, 4, 124, _DDS_FLAGS, h, w, w * 4, 0, len(levels))
    struct.pack_into('<II4s5I', hdr, 76, *_PIXEL_FORMAT[:2], b'\0\0\0\0', *_PIXEL_FORMAT[3:])
    struct.pack_into('<I', hdr, 108, _DDS_CAPS)
    return bytes(hdr) + b''.join(levels)


def build_spray_texture(src_dds: str, out_path: str) -> bool:
    """Write the spray look of `src_dds` to `out_path`; False when it cannot be read.

    Mesh conversion is a process pool, so the bytes go to a per-process name
    first and are moved into place.
    See: docs/commentary/asset_convert_shader.md#water-spray-alpha
    """
    try:
        w, h, bgra = decode_dds(src_dds)
    except (OSError, ValueError):
        return False
    rgba = np.frombuffer(bytes(bgra), np.uint8).reshape(h, w, 4)[..., [2, 1, 0, 3]].astype(float)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = f'{out_path}.{os.getpid()}.tmp'
    with open(tmp, 'wb') as f:
        f.write(bgra_dds(spray_rgba(rgba)))
    os.replace(tmp, out_path)
    return True
