"""Oblivion hair -> Skyrim head-part (HDPT) meshes: the bake.

hair_plan decides WHICH variants a plugin needs (length step x gender x the
wearer's head family); this module bakes each one: the .tri HairMorph blended
in by the length's weight, the geometry fitted onto the Skyrim head of that
gender and family, then converted and given the Hair Tint shader.  Hair is a
separate stage because `meshes\\characters\\` is in nif_converter.SKIP_PATHS:
one source mesh becomes several outputs.

See: docs/commentary/asset_convert_armor.md#hair-variants-follow-the-wearer
"""

from asset_convert.game_paths import current_namespace
import io
import os
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache

import numpy as np

from asset_convert.character import head_fit
from asset_convert.character.facegen_tri import TriFile, TriError, build_skyrim_hair_tri
from asset_convert.character.hair_plan import (FIT_ARGS, build_plan, bucket_weight,
                                               mesh_family, mesh_name_family,
                                               norm_model, out_rel_dir, variant_stem)
from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from asset_convert.nif.tex_paths import path_bytes, path_text
from pyffi.formats.nif import NifFormat

from core.worker_budget import worker_count
from output_layout import assets_for

_WORKERS = worker_count()


# ---------------------------------------------------------------------------
# Geometry baking
# ---------------------------------------------------------------------------

def bake_hair_variant(nif_bytes: bytes, tri_bytes, weight: float,
                      female: bool = False, race=None, group=None,
                      source_head=None):
    """Bake one hair variant; returns (nif bytes, emitted .tri bytes or None).

    Blends `weight` of the .tri HairMorph into the shape whose VERTEX COUNT
    matches it (extra shapes such as jewellery are left unmorphed), fits every
    shape as one system onto the Skyrim head `race`/`group` select -- from
    `source_head` when the hair was authored on another game's head -- and
    builds the emitted .tri from the largest shape.

    See: docs/commentary/asset_convert_armor.md#hair-source-head
    """
    tri, deltas = _hair_morph(tri_bytes)
    data = NifFormat.Data()
    data.read(io.BytesIO(nif_bytes))
    blocks = _morphed_blocks(data, deltas, weight)
    _fit_blocks_to_head(blocks, female, race, group, source_head)
    baked = _largest_shape(blocks)
    out = io.BytesIO()
    data.write(out)
    if baked is None:
        return out.getvalue(), None
    return out.getvalue(), _baked_tri(tri, *baked)


def _hair_morph(tri_bytes):
    """(TriFile, HairMorph deltas) of a source .tri; (None, None) if absent or unreadable."""
    if not tri_bytes:
        return None, None
    try:
        tri = TriFile.from_bytes(tri_bytes)
        return tri, tri.hair_morph()
    except TriError:
        return None, None


def _morphed_blocks(data, deltas, weight: float) -> list:
    """Every geometry block, `weight` of the morph added where the vertex count matches."""
    blocks = []
    for root in (r for r in data.roots if r is not None):
        for block in root.tree():
            gd = getattr(block, 'data', None)
            if (not isinstance(block, (NifFormat.NiTriShape, NifFormat.NiTriStrips))
                    or gd is None or gd.num_vertices == 0):
                continue
            if deltas is not None and weight and gd.num_vertices == len(deltas):
                for v, (dx, dy, dz) in zip(gd.vertices, deltas):
                    v.x, v.y, v.z = v.x + dx * weight, v.y + dy * weight, v.z + dz * weight
            blocks.append(block)
    return blocks


def _largest_shape(blocks):
    """(verts, faces, uvs) of the largest block, refreshing every block's bounds."""
    best = None
    for block in blocks:
        gd = block.data
        try:
            gd.update_center_radius()
        except Exception:
            pass
        if best is None or gd.num_vertices > len(best[0]):
            best = ([(v.x, v.y, v.z) for v in gd.vertices],
                    _shape_triangles(block, gd), _shape_uvs(gd))
    return best


def _baked_tri(tri, verts, faces, uvs) -> bytes:
    """The emitted .tri: the source .tri's topology when its count matches the bake."""
    if tri is not None and len(tri.vertices) == len(verts):
        return build_skyrim_hair_tri(verts, tri.faces, tri.uvs, tri.uv_faces)
    return build_skyrim_hair_tri(verts, faces, uvs, None)


def _block_arrays(blocks) -> list:
    """(verts, tris) numpy arrays of each geometry block."""
    return [(np.array([[v.x, v.y, v.z] for v in b.data.vertices], dtype=np.float64),
             np.array(_shape_triangles(b, b.data), dtype=np.int64).reshape(-1, 3))
            for b in blocks]


@lru_cache(maxsize=None)
def _source_head(path: str):
    """(verts, tris) of a source head mesh in its own face space, read once per process."""
    data = NifFormat.Data()
    with open(path, 'rb') as fh:
        data.read(fh)
    parts = _block_arrays(_morphed_blocks(data, None, 0.0))
    offs = np.cumsum([0] + [len(v) for v, _t in parts])
    return (np.vstack([v for v, _t in parts]),
            np.vstack([t + offs[i] for i, (_v, t) in enumerate(parts)]))


def _fit_blocks_to_head(blocks, female: bool, race=None, group=None,
                        source_head=None) -> bool:
    """Fit hair blocks onto the Skyrim head as one system; whether the fit ran.

    Hair is authored in face space and glued rigidly to Skyrim's head bone,
    so unfitted the SOURCE skull's shape survives around the Skyrim one.
    `source_head` is another game's head mesh the hair was authored on
    (head_fit.register_source_pack); without fit data the geometry passes
    through untouched.
    """
    if not blocks or not head_fit.fit_available(female):
        return False
    if source_head:
        sv, st = _source_head(source_head)
        race = head_fit.register_source_pack(
            female, '%s|%s|%s' % (source_head, race, group), sv, st,
            race=race, group=group)
        group = None
    fitted = head_fit.fit_head_gear(_block_arrays(blocks), female, race=race,
                                    group=group, cover_ears=True, hug=True)
    if fitted is None:
        return False
    for block, new_v in zip(blocks, fitted):
        for v, p in zip(block.data.vertices, new_v):
            v.x, v.y, v.z = float(p[0]), float(p[1]), float(p[2])
    return True


# BSLightingShaderProperty.skyrim_shader_type 6 = "Hair Tint".  nif.xml is
# explicit that the Hair Tint Color field is read ONLY when Shader Type == 6
# ("Enables Hair Tint Color" / cond="Shader Type == 6"), and the engine carries
# a matching BSLightingShaderMaterialHairTint RTTI class plus a "HairTint"
# shader technique.  Converted hair shipped as type 0 (Default), which is why
# it rendered as the raw grey source texture no matter what the NPC's HCLF
# said: with the wrong material class the tint is never sampled.
#
# Vanilla census (references/Skyrim Meshes, 214 hair shaders): type 6 on 196,
# type 5 on the remaining 18 (the beast-race horn meshes, which are genuinely
# untinted).  Flags on the type-6 majority are 0x82440303 / 0x80a1.
SHADER_TYPE_HAIR_TINT = 6

# The mesh's own tint is a PLACEHOLDER: nif.xml notes it is "Overridden by game
# settings", and the engine substitutes the wearer's HCLF -> CLFM color at
# runtime.  Vanilla's most common value (92 of 214 shaders) is
# (0.5176, 0.4706, 0.3922) = RGB (132, 120, 100); matching it keeps a converted
# mesh looking right in NifSkope and in the CK preview.
_VANILLA_HAIR_TINT = (0.5176470875740051, 0.4705882966518402, 0.3921569287776947)

# Vanilla hair sets these three on top of what the generic converter already
# writes.  Soft lighting is what gives hair its through-strand falloff;
# own_emit and assume_shadowmask come with it in every vanilla type-6 hair.
# Alpha TEST threshold for hair.  Oblivion authored hair with alpha BLEND and
# threshold 0 (60 of its 61 hair meshes), which its renderer tolerated.  Skyrim
# does not: with threshold 0 the test rejects nothing, so every semi-transparent
# strand pixel goes through the blend path and gets depth-sorted per frame --
# which reads in game as the whole hairstyle smearing/blurring as the camera
# rotates, and as broken transparency generally.
#
# Vanilla Skyrim hair alpha-tests (dominant flag word 0x12EC -- test ON,
# blend OFF), with thresholds 128 (x92), 100 (x57), 120 (x12) and a low tail
# (hairlonghumanm ships 0x12EC at 35).  128 is right for VANILLA textures,
# whose alpha is a strand mask -- but OBLIVION hair diffuses were authored
# for blend-at-0, and several put large VISIBLE regions at mid alpha:
# measured, threshold 128 deletes 18% of grey.dds (the blindfold band tore
# into holes in game, 2026-08-24 -- no triangles were lost, the test cut the
# texels), 18% of mane.dds, 17% of dremora.dds, 12% of khajiit.dds.  At 35
# the loss drops to 2-5% (texels under ~14% opacity, near-invisible under
# Oblivion's own blending); the blindfold band still showed residual holes
# at 35 (its 16-35 band is 3% of visible texels), so the shipped threshold
# is 16 — loss 1.3%, texels under ~6% opacity.  Keeps the vanilla no-blur
# flags while cutting essentially nothing Oblivion showed.
HAIR_ALPHA_THRESHOLD = 16

# NiAlphaProperty flag word used by 126 of 211 vanilla hair meshes: alpha test
# enabled (bit 9), alpha blend disabled (bit 0), src/dst blend modes left at
# the vanilla SRC_ALPHA / INV_SRC_ALPHA pair.
HAIR_ALPHA_FLAGS = 0x12EC


def _texture_bias(dds_path):
    """Per-channel color bias of a hair diffuse, normalized to its own mean.

    Returns (br, bg, bb) where 1.0 on every channel means a neutral texture.
    Alpha-weighted, because a hair diffuse's transparent margin is not part of
    the visible strand color.
    """
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        return None
    try:
        im = Image.open(dds_path)
        im.load()
        arr = np.asarray(im.convert('RGBA')).astype(float)
    except Exception:
        return None
    rgb = arr[..., :3]
    alpha = arr[..., 3:4] / 255.0
    if alpha.sum() < 1.0:
        alpha = np.ones_like(alpha)
    mean = (rgb * alpha).sum(axis=(0, 1)) / alpha.sum()
    grey = mean.mean()
    if grey <= 1e-6:
        return None
    return tuple(float(c / grey) for c in mean)


def hair_tint_for_texture(dds_path):
    """The mesh tint to write so an authored hair color reads true.

    Skyrim MULTIPLIES the hair tint by the diffuse, so the texture's own color
    cast survives into the result.  Oblivion's hair diffuses are nowhere near
    uniform -- measured alpha-weighted channel ratios (r:g:b, normalized):

        grey     1.07 : 0.99 : 0.94      near neutral
        dremora  1.06 : 1.00 : 0.95      near neutral
        short    1.09 : 0.99 : 0.92      near neutral
        khajiit  1.11 : 1.00 : 0.89      mild warm
        mane     1.43 : 1.03 : 0.54      strongly orange-brown
        argonian 1.40 : 0.97 : 0.63      strongly orange-brown

    so the same authored HCLF renders very differently depending on which
    texture the hairstyle happens to use -- a Khajiit mane comes out orange no
    matter what color the NPC authored.  Dividing the bias out of the mesh
    tint cancels the texture's cast, leaving the wearer's own color to do the
    work.  Falls back to the plain vanilla tint when the texture cannot be read.
    """
    bias = _texture_bias(dds_path) if dds_path else None
    if not bias:
        return _VANILLA_HAIR_TINT
    out = []
    for base, b in zip(_VANILLA_HAIR_TINT, bias):
        out.append(min(1.0, max(0.0, base / b)) if b > 1e-6 else base)
    return tuple(out)


def apply_hair_shader(data, tint=None, spec_strength=None):
    """Retype a converted hair NIF's shaders to Hair Tint (type 6).

    Also switches the NiAlphaProperty from Oblivion's blend-with-no-threshold
    to Skyrim's alpha-test form (see HAIR_ALPHA_THRESHOLD).

    Returns the number of shader properties updated.
    """
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat

    for block in data.blocks:
        if isinstance(block, NifFormat.NiAlphaProperty):
            block.flags = HAIR_ALPHA_FLAGS
            block.threshold = HAIR_ALPHA_THRESHOLD

    n = 0
    for block in data.blocks:
        if not isinstance(block, NifFormat.BSLightingShaderProperty):
            continue
        block.skyrim_shader_type = SHADER_TYPE_HAIR_TINT
        block.shader_flags_1.slsf_1_hair_soft_lighting = 1
        block.shader_flags_1.slsf_1_own_emit = 1
        block.shader_flags_2.slsf_2_assume_shadowmask = 1
        # ANISOTROPIC LIGHTING is what makes the specular read as hair
        # strands instead of a round plastic highlight — vanilla human hair
        # (hairshorthumanm, malehumanoldhair01) sets it alongside soft
        # lighting params 0.3/2.0; without them converted hair looked
        # "really shiny and not hair-like" in game (2026-08-24).
        block.shader_flags_2.slsf_2_anisotropic_lighting = 1
        block.lighting_effect_1 = 0.30000001192092896
        block.lighting_effect_2 = 2.0
        tint_col = getattr(block, 'hair_tint_color', None)
        if tint_col is not None:
            tint_col.r, tint_col.g, tint_col.b = tint or _VANILLA_HAIR_TINT
        # Vanilla hair is specular with a tight highlight; the generic path
        # leaves these at zero, which reads as flat matte under the tint.
        block.glossiness = 10.0
        block.specular_strength = (_SPEC_STRENGTH_BASE
                                   if spec_strength is None
                                   else float(spec_strength))
        spec = getattr(block, 'specular_color', None)
        if spec is not None:
            spec.r = spec.g = spec.b = 1.0
        n += 1
    return n


def _shape_triangles(block, gd):
    """Triangle list for either a NiTriShape or a NiTriStrips."""
    try:
        return [tuple(t) for t in gd.get_triangles()]
    except Exception:
        pass
    tris = getattr(gd, 'triangles', None)
    if not tris:
        return []
    return [(t.v_1, t.v_2, t.v_3) for t in tris]


def _shape_uvs(gd):
    sets = getattr(gd, 'uv_sets', None)
    if not sets or len(sets) == 0:
        return []
    return [(uv.u, uv.v) for uv in sets[0]]


# ---------------------------------------------------------------------------
# Planning the bakes
# ---------------------------------------------------------------------------

def morph_applies(src_nif_path, src_tri_path) -> bool:
    """Whether the sibling .tri's HairMorph can reach this mesh's geometry.

    The bake pairs morph to geometry by VERTEX COUNT (see bake_hair_variant),
    so a .tri matching no shape -- or absent, as on every Fallout NV hair --
    leaves every length bucket baking byte-identical output.
    """
    if not src_tri_path:
        return False
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat
    import io

    try:
        with open(src_tri_path, 'rb') as fh:
            deltas = TriFile.from_bytes(fh.read()).hair_morph()
    except (TriError, OSError):
        return False
    if deltas is None:
        return False
    try:
        data = NifFormat.Data()
        with open(src_nif_path, 'rb') as fh:
            data.read(io.BytesIO(fh.read()))
    except Exception:
        return False
    for root in data.roots:
        if root is None:
            continue
        for block in root.tree():
            if not isinstance(block, (NifFormat.NiTriShape,
                                      NifFormat.NiTriStrips)):
                continue
            gd = block.data
            if gd is not None and gd.num_vertices == len(deltas):
                return True
    return False


def _bake_one(job):
    """Bake + convert one hair variant, writing every name that shares it.

    A job carries EVERY out_stem whose bake inputs are identical; the extra
    names are copies, byte-exact because convert_nif and _retype_hair_shader
    are functions of the baked BYTES alone.  `written`/`tinted` count every
    name, so the stage's totals do not change.

    Returns (out_stem, written, tinted, error) -- errors come back as data
    so one bad mesh cannot kill the pool.
    """
    (rel, src_nif_path, src_tri_path, weight, female, race, group, source_head,
     out_stems, out_dir, src_root, src_tex_root) = job
    out_stem = out_stems[0]

    # THE JOB CARRIES PATHS, NOT BYTES.  One HAIR record becomes many
    # variants (bucket x gender x race group), and embedding the mesh bytes
    # in every job tuple duplicated 3.5 MB of unique NIFs into 102.9 MB
    # spread over ~1605 tuples -- all of which pool.map pickles into the
    # queue up front, on top of 29 spawned interpreters.  The worker reads
    # the file itself instead; the OS page cache makes the re-read free.
    try:
        with open(src_nif_path, 'rb') as fh:
            nif_bytes = fh.read()
        tri_bytes = None
        if src_tri_path:
            with open(src_tri_path, 'rb') as fh:
                tri_bytes = fh.read()
    except OSError as exc:
        return out_stem, 0, 0, 'source unreadable: %s' % exc

    try:
        baked_nif, baked_tri = bake_hair_variant(
            nif_bytes, tri_bytes, weight, female, race=race, group=group,
            source_head=source_head)
    except Exception as exc:
        return out_stem, 0, 0, 'bake failed: %s' % exc

    return _emit_variant(baked_nif, baked_tri, rel, out_stems, out_dir,
                         src_root, src_tex_root)


def _emit_variant(baked_nif, baked_tri, rel, out_stems, out_dir, src_root,
                  src_tex_root):
    """Convert one baked mesh and write every out_stem that shares it.

    The names after the first are COPIES: convert_nif and _retype_hair_shader
    read only the baked bytes, so a copy is byte-identical to re-running them
    under the other name.  A name REPEATED in `out_stems` is one file on disk
    (several records can ask for the same variant) but still counts once
    each, so the stage reports the variants the plugin asked for.
    """
    import shutil
    import tempfile
    from asset_convert.nif.nif_converter import convert_nif

    out_stem = out_stems[0]
    # convert_nif works on paths, so stage the baked mesh.  It is written
    # under the source tree's basename so the converter's own path-derived
    # decisions (texture namespacing) see a hair path.
    tmp_dir = tempfile.mkdtemp(prefix='tes4hair_')
    try:
        staged = os.path.join(tmp_dir, os.path.basename(rel))
        with open(staged, 'wb') as fh:
            fh.write(baked_nif)
        dst_nif = os.path.join(out_dir, out_stem + '.nif')
        result = convert_nif(staged, dst_nif, src_meshes_dir=src_root,
                             hair=True)
        if result.get('error'):
            return out_stem, 0, 0, 'convert failed: %s' % result['error']
        if not os.path.isfile(dst_nif):
            return out_stem, 0, 0, 'convert produced no file'
    finally:
        _rmtree_quiet(tmp_dir)

    # Retype to the Hair Tint shader.  Runs on the CONVERTED file so it lands
    # after the generic property conversion has built the
    # BSLightingShaderProperty; doing it earlier would just be overwritten.
    tinted = _retype_hair_shader(dst_nif, src_tex_root)

    dst_tri = os.path.join(out_dir, out_stem + '.tri')
    if baked_tri:
        with open(dst_tri, 'wb') as fh:
            fh.write(baked_tri)
    for stem in dict.fromkeys(out_stems[1:]):
        if stem == out_stem:
            continue
        shutil.copyfile(dst_nif, os.path.join(out_dir, stem + '.nif'))
        if baked_tri:
            shutil.copyfile(dst_tri, os.path.join(out_dir, stem + '.tri'))
    n = len(out_stems)
    return out_stem, n, tinted * n, None


def _job_cost(src_nif) -> int:
    """Source size, a free monotone proxy for a mesh's bake cost."""
    try:
        return os.path.getsize(src_nif)
    except OSError:
        return 0


def _entry_sources(entry: dict) -> tuple:
    """(source nif, source tri or None, meshes root, textures root) of a hair.

    The files come from the asset folder of the export that OWNS the hair (a
    master's, for a master's hair), not from the record folder.
    """
    root = str(assets_for(entry['owner']))
    src_nif = os.path.join(root, 'meshes', *norm_model(entry['model']).split('/'))
    src_tri = os.path.splitext(src_nif)[0] + '.tri' if entry['has_tri'] else None
    return (src_nif, src_tri, os.path.join(root, 'meshes'),
            os.path.join(root, 'textures'))


def _variant_jobs(entry, src, out_dir, heads, shared, order, morphed) -> int:
    """Fold one hair's planned variants into `shared`/`order`; returns how many.

    A variant whose bake inputs match an earlier one only adds its NAME to that
    bake; `weight` enters the identity only when the .tri morph reaches the
    mesh (`morphed`), so a hair with no usable .tri bakes once for every length.
    """
    rel = norm_model(entry['model'])
    stem = os.path.splitext(os.path.basename(rel))[0]
    for v in sorted(entry['variants']):
        fam = mesh_family(v.family)
        weight = bucket_weight(v.bucket) if morphed else 0.0
        key = (src[0], weight, v.female, fam, heads[v.female])
        if key not in shared:
            shared[key] = []
            order.append((rel, src[0], src[1], weight, v.female) + FIT_ARGS[fam]
                         + (heads[v.female], shared[key], out_dir, src[2], src[3]))
        shared[key].append(variant_stem(stem, v.bucket, v.female,
                                        mesh_name_family(v, entry)))
    return len(entry['variants'])


def _plan_jobs(plan, out_dir, stats, verbose: bool) -> list:
    """The bakes this plugin's plan needs, each carrying every name that shares it.

    One bake per distinct set of bake inputs rather than one per output
    name, ordered LONGEST FIRST so a slow mesh never starts with nothing
    left to overlap it.  `stats` gains hairs/variants/missing in place.

    See: docs/commentary/asset_convert_armor.md#hair-bake-sharing
    """
    shared, order, morphed = {}, [], {}
    for fid in sorted(plan.hairs):
        entry = plan.hairs[fid]
        if not entry['variants'] or not entry['model']:
            continue
        src = _entry_sources(entry)
        if not os.path.isfile(src[0]):
            stats['missing'] += 1
            if verbose:
                print('    hair: missing source mesh %s' % src[0])
            continue
        stats['hairs'] += 1
        if src[0] not in morphed:
            morphed[src[0]] = morph_applies(src[0], src[1])
        stats['variants'] += _variant_jobs(entry, src, out_dir, plan.heads,
                                           shared, order, morphed[src[0]])
    order.sort(key=lambda job: _job_cost(job[1]), reverse=True)
    return order


def _prune_stale(out_dir: str, plan, names: set) -> int:
    """Delete this plan's hairs' .nif/.tri files no planned variant names; returns how many.

    Only files named after one of the plan's hair meshes are touched, so a
    sibling plugin sharing the output folder keeps its own.
    """
    stems = {os.path.splitext(os.path.basename(norm_model(e['model'])))[0]
             for e in plan.hairs.values() if e['model']}
    if not os.path.isdir(out_dir):
        return 0
    stale = [f for f in os.listdir(out_dir)
             if os.path.splitext(f)[1].lower() in ('.nif', '.tri')
             and os.path.splitext(f)[0].lower().split('__')[0] in stems
             and os.path.splitext(f)[0].lower() not in names]
    for f in stale:
        os.remove(os.path.join(out_dir, f))
    return len(stale)


def run(export_dir, out_meshes_dir, *, verbose: bool = True) -> dict:
    """Bake every hair variant this plugin's plan asks for; returns stats.

    See hair_plan for what is planned.  Files a previous build left in the
    hair folder that the plan no longer names are deleted first.
    """
    stats = {'hairs': 0, 'variants': 0, 'written': 0, 'missing': 0,
             'errors': 0, 'tinted': 0}
    out_dir = os.path.join(str(out_meshes_dir), *out_rel_dir().split(os.sep))
    plan = build_plan(export_dir)
    jobs = _plan_jobs(plan, out_dir, stats, verbose)
    stats['pruned'] = _prune_stale(out_dir, plan,
                                   {n for job in jobs for n in job[8]})
    if not jobs:
        return stats
    os.makedirs(out_dir, exist_ok=True)

    # ProcessPoolExecutor, not threads: the per-variant work is CPU-bound
    # pure Python (pyffi parse/write plus the numpy head fit), which the GIL
    # serializes.  One worker is spawned in-process to keep small runs (and
    # the tests) free of pool overhead.
    workers = min(_WORKERS, len(jobs))
    if verbose and workers > 1:
        print('  Hair: baking %d variants (%d workers)...'
              % (len(jobs), workers))

    # STREAM the results instead of materialising all of them.  `list(...)`
    # around pool.map holds every finished result alongside every pending
    # job, and a worker being killed there surfaces only as an opaque
    # BrokenProcessPool with no indication of which mesh was in flight.
    # Consuming the iterator lets each result be folded into the stats and
    # dropped, and `chunksize` keeps the queue from being filled with all
    # 721 tuples at once.
    def _iter_results():
        """Yield each bake's result as it lands, holding none of them.

        `list(...)` around pool.map holds every finished result alongside
        every pending job, and a worker killed there surfaces only as an
        opaque BrokenProcessPool naming no mesh.  chunksize 1 because jobs
        are ordered longest-first and span an order of magnitude in cost:
        batching them re-strands the long ones.
        """
        if workers <= 1:
            for job in jobs:
                yield _bake_one(job)
            return
        with ProcessPoolExecutor(max_workers=workers) as pool:
            yield from pool.map(_bake_one, jobs, chunksize=1)

    for out_stem, written, tinted, error in _iter_results():
        if error:
            stats['errors'] += 1
            if verbose:
                print('    hair: %s for %s' % (error, out_stem))
            continue
        stats['written'] += written
        stats['tinted'] += tinted

    if verbose:
        print('  Hair: %d records, %d group/length variants, %d written, '
              '%d hair-tint shaders'
              % (stats['hairs'], stats['variants'], stats['written'],
                 stats['tinted'])
              + (', %d missing' % stats['missing'] if stats['missing'] else '')
              + (', %d errors' % stats['errors'] if stats['errors'] else ''))
    return stats


# Hair diffuses two source meshes name but Oblivion never shipped.  Both are
# authored typos, verified against the extracted texture tree:
#   Grey_Mane.dds  -> named by 3 meshes (khajiitdreds/khajiitheadband/...);
#                     only Mane.dds exists, which is the mane texture they mean.
#   Grey.dds       -> 5 meshes name it with NO folder at all, so it resolves to
#                     textures	es4\Grey.dds instead of the hair folder.
# Left alone the shape renders untextured (Skyrim draws it black/white), which
# is the "some hairs are missing their textures" symptom.
_BS = chr(92)          # backslash, the separator NIF texture paths use
_HAIR_TEX_DIR = 'characters/hair'
_HAIR_TEX_FIXUPS = {
    'grey_mane.dds': 'characters/hair/mane.dds',
    'grey.dds': 'characters/hair/grey.dds',
}


def resolve_hair_texture(rel: str, textures_root):
    """Repair a hair diffuse path that names a file Oblivion never shipped.

    Returns the corrected mesh-relative path (tes4-prefixed, backslashes) or
    None when the original is fine.
    """
    if not rel:
        return None
    norm = rel.replace(_BS, '/').lower().lstrip('/')
    if norm.startswith('textures/'):
        norm = norm[len('textures/'):]
    ns = current_namespace() + '/'
    if norm.startswith(ns):
        norm = norm[len(ns):]

    if textures_root and os.path.isfile(
            os.path.join(str(textures_root), *norm.split('/'))):
        return None                      # resolves already

    fixed = _HAIR_TEX_FIXUPS.get(os.path.basename(norm))
    if fixed is None:
        # Anything else that lost its folder: put it back in the hair folder.
        if '/' not in norm:
            fixed = '%s/%s' % (_HAIR_TEX_DIR, norm)
        else:
            return None
    if textures_root and not os.path.isfile(
            os.path.join(str(textures_root), *fixed.split('/'))):
        return None                      # the repair target is missing too
    return current_namespace() + _BS + fixed.replace('/', _BS)


# Vanilla hair specular masks (the normal map's alpha) average mean-alpha
# ~17 (HairLong_Old_n 14.2, HairShort_Old_n 17.2, hairlong_n 18.7); the
# converted Oblivion ones average 94 (khajiit_n 197, grey_n 110, short_n 37)
# — 5x hotter, which read in game as "hair still overly shiny" even with the
# vanilla flags/gloss (2026-08-24).  specular_strength is scaled per texture
# so mask*strength lands at the vanilla level.
_VANILLA_SPEC_MASK_MEAN = 17.0
_SPEC_STRENGTH_BASE = 0.8999999761581421
_spec_strength_cache: dict = {}


def _spec_strength_for_normal(path):
    """specular_strength scaled by the normal map's alpha mass, cached."""
    key = os.path.normcase(str(path))
    if key in _spec_strength_cache:
        return _spec_strength_cache[key]
    strength = _SPEC_STRENGTH_BASE
    try:
        from PIL import Image
        import numpy as np
        a = np.array(Image.open(path).convert('RGBA'))[:, :, 3]
        mean = max(float(a.mean()), 1.0)
        strength = float(min(_SPEC_STRENGTH_BASE, max(
            0.05, _SPEC_STRENGTH_BASE * _VANILLA_SPEC_MASK_MEAN / mean)))
    except Exception:
        pass
    _spec_strength_cache[key] = strength
    return strength


def _fix_hair_textures(data, textures_root):
    """Repair broken diffuse paths; return the tint for the texture in use."""
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat

    diffuse_rel = None
    for block in data.blocks:
        if not isinstance(block, NifFormat.BSLightingShaderProperty):
            continue
        ts = getattr(block, 'texture_set', None)
        if ts is None:
            continue
        rel = path_text(ts.textures[0])
        fixed = resolve_hair_texture(rel, textures_root)
        if fixed:
            ts.textures[0] = path_bytes(fixed)
            stem = fixed.rsplit('.', 1)[0]
            ts.textures[1] = path_bytes(stem + '_n.dds')
            rel = fixed
        diffuse_rel = diffuse_rel or rel

    if not diffuse_rel or not textures_root:
        return None, _SPEC_STRENGTH_BASE
    norm = diffuse_rel.replace(_BS, '/').lower().lstrip('/')
    for prefix in ('textures/', current_namespace() + '/'):
        if norm.startswith(prefix):
            norm = norm[len(prefix):]
    path = os.path.join(str(textures_root), *norm.split('/'))
    if not os.path.isfile(path):
        return None, _SPEC_STRENGTH_BASE
    spec = _SPEC_STRENGTH_BASE
    npath = os.path.splitext(path)[0] + '_n.dds'
    if os.path.isfile(npath):
        spec = _spec_strength_for_normal(npath)
    return hair_tint_for_texture(path), spec


def _retype_hair_shader(path, textures_root=None) -> int:
    """Apply the Hair Tint shader to a converted hair NIF on disk.

    Also repairs the diffuse path when the source names a texture that does not
    exist (see resolve_hair_texture) and derives the tint from the texture the
    mesh actually ends up using.
    """
    from asset_convert.nif.pyffi_monkey_patch import apply_patches
    apply_patches()
    from pyffi.formats.nif import NifFormat
    try:
        data = NifFormat.Data()
        with open(path, 'rb') as fh:
            data.read(fh)
        tint, spec = _fix_hair_textures(data, textures_root)
        n = apply_hair_shader(data, tint=tint, spec_strength=spec)
        if n:
            with open(path, 'wb') as fh:
                data.write(fh)
        return n
    except Exception:
        # A hair that will not re-open is already reported by the convert
        # step; never let the tint pass turn a written mesh into a failure.
        return 0


def _rmtree_quiet(path):
    import shutil
    try:
        shutil.rmtree(path)
    except OSError:
        pass
