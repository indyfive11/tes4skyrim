"""Render a NIF's geometry to PNG for LOD decimation validation.

Renders every shape from orbit views (tools.nif.nif_render).  With --far it
renders the source NIF and its freshly-decimated LOD side by side so holes /
silhouette damage from the simplifier are immediately visible.

Usage:
    python -m tools.lod.nif_lod_preview <src.nif> [--far] [--ratio 0.07]
        [--out temp/preview.png] [--size 700]
"""

import argparse
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from asset_convert.lod import lod_far_gen
from asset_convert.nif.sse_nif import read_nif
from tools.nif.nif_render import collect_shapes, render


def _label(img, text):
    """`img` with `text` drawn in its top-left corner."""
    ImageDraw.Draw(img).text((8, 6), text, fill=(240, 240, 200))
    return img


def _tri_count(shapes) -> int:
    """Total triangles over `collect_shapes` output."""
    return sum(len(s[1]) for s in shapes)


def _far_panels(src: Path, ratio, size: int, yaws) -> list:
    """Labeled renders of `src` decimated in memory; empty when decimation is skipped."""
    kw = {} if ratio is None else {'decimate_ratio': ratio}
    with tempfile.TemporaryDirectory() as td:
        dst = Path(td) / (src.stem + '_far.nif')
        if not lod_far_gen.generate_far_nif(src, dst, **kw):
            print("decimation FAILED/skipped")
            return []
        shapes = collect_shapes(read_nif(str(dst)))
    print(f"far: {_tri_count(shapes)} tris")
    return [_label(render(shapes, size, yaw), f"far {_tri_count(shapes)} tris") for yaw in yaws]


def _parse_args():
    """Command-line arguments."""
    ap = argparse.ArgumentParser()
    ap.add_argument('nif')
    ap.add_argument('--far', action='store_true',
                    help='also decimate in-memory and render side by side')
    ap.add_argument('--ratio', type=float, default=None,
                    help='decimation ratio override')
    ap.add_argument('--out', default='temp/nif_lod_preview.png')
    ap.add_argument('--size', type=int, default=700)
    ap.add_argument('--views', type=str, default='30,120',
                    help='comma-separated yaw angles')
    return ap.parse_args()


def main():
    """Render the source NIF (and with --far its decimated LOD) into one PNG."""
    args = _parse_args()
    src = Path(args.nif)
    shapes = collect_shapes(read_nif(str(src)))
    print(f"{src.name}: {_tri_count(shapes)} tris")
    yaws = [float(x) for x in args.views.split(',')]
    panels = [_label(render(shapes, args.size, yaw, 18.0), f"src {_tri_count(shapes)} tris")
              for yaw in yaws]
    if args.far:
        panels += _far_panels(src, args.ratio, args.size, yaws)
    w = args.size
    canvas = Image.new('RGB', (w * len(panels) + 4 * (len(panels) - 1), w), (0, 0, 0))
    for i, p in enumerate(panels):
        canvas.paste(p, (i * (w + 4), 0))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    print(f"wrote {out}")


if __name__ == '__main__':
    main()
