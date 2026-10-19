"""Morrowind NPC animation files: each idle group retargeted onto the Skyrim body.

An NPC_ record may name its own animation file (`MODL`), whose `x<name>.kf`
overrides groups of the shared base animation, and its wander package's eight
idle chances pick among `idle2`..`idle9`: seated, sleeping and dancing NPCs
are authored this way. Each group is retargeted (clip_retarget) and written
64-bit under `meshes\\actors\\character\\animations\\<ns>anims\\`, with a
manifest for the graph stage and the table MorrowindRuntime plays them from.
See: docs/commentary/asset_convert_animation.md#morrowind-npc-animation-files
"""

import json
import os
import re
import zlib
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from asset_convert import paths
from asset_convert.character.skin_retarget import SKEL_MORROWIND
from asset_convert.character.skyrim_overrides import OBLIVION_TO_SKYRIM_BONE_MAP
from asset_convert.game_paths import current_namespace
from asset_convert.havok.clip_retarget import (Skeleton, load_pose_deltas,
                                               retarget_clip)
from asset_convert.havok.creature_split_morrowind import (group_clips, slashed,
                                                          write_skeleton)
from asset_convert.havok.hkx_anim import write_clip_hkx
from asset_convert.havok.hkx_xml import convert_hkx_to_amd64
from asset_convert.havok.npc_anim_table import placement_pose, write_table
from asset_convert.sources import base_plugins
from asset_convert.sources.morrowind_assets import resolve_mesh
from core.worker_budget import worker_count
from output_layout import assets_for
from tes5_import.base.text_reader import parse_export_file

#: The engine's default pose group, then the eight a wander package's chances pick among.
IDLE_GROUPS = ('idle',) + tuple(f'idle{n}' for n in range(2, 10))

#: A group only a whole base animation set carries; a file defining it is not a pose file.
LOCOMOTION_GROUP = 'walkforward'

#: The shared human rig every NPC animation file is keyed against.
BASE_ANIM = 'base_anim.nif'

#: The record lines naming an NPC's animation file and a wander package's idle chances.
ANIM_KEY = 'MorrowindAnim'
IDLE_KEY = 'MorrowindIdle'

#: The graph event that starts one clip is this prefix plus the clip's stem.
EVENT_PREFIX = 'TES3Anim_'

ANIM_MANIFEST = 'anims_manifest.json'

_POSE_DELTAS = 'best_animation_pose_morrowind.json'


# ---------------------------------------------------------------------------
# What the records author
# ---------------------------------------------------------------------------

def anim_slug(model: str) -> str:
    """An ASCII name for one authored animation file path, unique per path."""
    key = slashed(model).lower()
    stem = os.path.splitext(os.path.basename(key))[0]
    return '%s_%08x' % (re.sub('[^a-z0-9]', '', stem)[:24],
                        zlib.crc32(key.encode('utf-8')))


def _wanders(folder: str) -> dict:
    """{PACK FormID: 'c2,..,c9|distance'} for every wander package of one export."""
    return {rec['FormID']: f"{rec[IDLE_KEY]}|{rec.get('PLDT.Radius', 0)}"
            for rec in parse_export_file(os.path.join(folder, 'PACK.txt'))
            if rec.get(IDLE_KEY)}


def _first_wander(rec: dict, wanders: dict) -> str:
    """`chances|distance` of an NPC's first wander package; '|0' without one."""
    for index in range(int(rec.get('AIPackageCount') or 0)):
        found = wanders.get(rec.get(f'AIPackage[{index}]', ''))
        if found:
            return found
    return '|0'


def npc_bindings(export_dirs: list) -> dict:
    """{NPC FormID: (id, animation file, `chances|distance`)} per NPC naming its own file.

    `export_dirs` lists the plugin first, then its masters; the plugin's
    record of an NPC wins.
    """
    out = {}
    for folder in export_dirs:
        wanders = _wanders(folder)
        for rec in parse_export_file(os.path.join(folder, 'NPC_.txt')):
            model = (rec.get(ANIM_KEY) or '').strip()
            if model and rec.get('FormID') not in out:
                out[rec['FormID']] = (rec.get('EditorID', ''), slashed(model),
                                      _first_wander(rec, wanders))
    return out


def placements(export_dir: str, bindings: dict) -> list:
    """(placement FormID, NPC id, pose) per reference the plugin places of a bound NPC."""
    return [(rec['FormID'], bindings[rec['NAME']][0], placement_pose(rec))
            for rec in parse_export_file(os.path.join(export_dir, 'ACHR.txt'))
            if rec.get('NAME') in bindings]


# ---------------------------------------------------------------------------
# Per-file conversion (runs in a worker process)
# ---------------------------------------------------------------------------

_RIG = {}


def _rig(skeleton_nif: str) -> dict:
    """Source and target skeletons, bone map and pose deltas, once per process.

    The pose deltas were mined against the T-posed bind skeleton, so that is
    the source's reference pose, not the animation rig's own rest.
    See: docs/commentary/asset_convert_animation.md#morrowind-npc-animation-files
    """
    if skeleton_nif not in _RIG:
        src = Skeleton.from_nif(skeleton_nif)
        with open(SKEL_MORROWIND[False], encoding='utf-8') as handle:
            bind = json.load(handle)
        for name in set(bind) & set(src.index):
            src.world[src.index[name]] = np.array(bind[name], dtype=np.float64)
        generated = paths.REPO / 'asset_convert' / 'generated'
        dst = Skeleton.from_hkx_json(str(generated / 'skeleton_hkx_skyrim_male.json'))
        hkx_name = {n.lower(): n for n in dst.names}
        _RIG[skeleton_nif] = {
            'src': src, 'dst': dst, 'dst_bones': dst.bones(),
            'bone_map': {s: hkx_name.get(d.lower(), d)
                         for s, d in OBLIVION_TO_SKYRIM_BONE_MAP.items()},
            'deltas': load_pose_deltas(str(generated / _POSE_DELTAS))}
    return _RIG[skeleton_nif]


def anim_prefix() -> str:
    """The clip folder relative to the humanoid project folder."""
    return 'Animations\\' + current_namespace().upper() + 'Anims\\'


def convert_one(kf_path: str, skeleton_nif: str, root_name: str, out_dir: str,
                slug: str, prefix: str) -> list:
    """Retarget one animation file's idle groups to 64-bit hkx; a manifest entry per group."""
    rig = _rig(skeleton_nif)
    entries = []
    clips = group_clips(kf_path, root_name, IDLE_GROUPS, LOCOMOTION_GROUP)
    for group, clip in clips.items():
        stem = f'{slug}_{group}'
        out_hkx = os.path.join(out_dir, stem + '.hkx')
        write_clip_hkx(retarget_clip(clip, rig['src'], rig['dst'], rig['bone_map'],
                                     rig['deltas']), rig['dst_bones'], out_hkx)
        convert_hkx_to_amd64(out_hkx)
        entries.append({'stem': stem, 'slug': slug, 'group': group,
                        'anim': f'{prefix}{stem}.hkx',
                        'event': EVENT_PREFIX + stem,
                        'duration': float(clip.duration)})
    return entries


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def anim_layout(out_meshes_dir: str) -> dict:
    """The clip folder on disk and its Data-relative path (shared by every plugin)."""
    leaf = current_namespace() + 'anims'
    project = ['actors', 'character', 'animations', leaf]
    return {'dir': os.path.join(out_meshes_dir, *project),
            'anim_dir': '\\'.join(['meshes', *project])}


def _sources(bindings: dict, roots: list, log) -> dict:
    """{slug: the `x<name>.kf` path} for every bound animation file that ships one."""
    out = {}
    for model in sorted({model for _id, model, _c in bindings.values()}):
        folder, name = os.path.split(model)
        rel = os.path.join(folder, 'x' + os.path.splitext(name)[0] + '.kf')
        path = resolve_mesh(roots, rel, paths.EXPORT)
        if path is None:
            log(f'  [skip] {model}: no {os.path.basename(rel)}')
        else:
            out[anim_slug(model)] = str(path)
    return out


def _convert_pool(sources: dict, skeleton: tuple, out_dir: str, workers: int,
                  log) -> list:
    """Convert every animation file in a process pool; the manifest entries, sorted."""
    clips = []
    prefix = anim_prefix()
    with ProcessPoolExecutor(max_workers=min(workers, len(sources))) as pool:
        futs = {pool.submit(convert_one, kf, skeleton[0], skeleton[1], out_dir,
                            slug, prefix): slug
                for slug, kf in sources.items()}
        for fut in as_completed(futs):
            try:
                clips += fut.result()
            except (OSError, ValueError, AttributeError, RuntimeError) as exc:
                log(f'  [FAIL] {futs[fut]}: {type(exc).__name__}: {exc}')
    return sorted(clips, key=lambda c: c['stem'])


def write_runtime_table(plugin_out: str, plugin_name: str, clips: list,
                        bindings: dict, placed: list) -> str:
    """Write the table MorrowindRuntime plays the clips from (npc_anim_table); its path."""
    have = {c['slug'] for c in clips}
    bound = {npc: (anim_slug(model), wander)
             for npc, model, wander in bindings.values()
             if anim_slug(model) in have}
    return write_table(
        plugin_out, plugin_name,
        [(c['slug'], c['group'], c['event'], c['duration']) for c in clips],
        [(npc, slug, wander) for npc, (slug, wander) in bound.items()],
        [row for row in placed if row[1] in bound])


def convert_npc_anims(export_dir: str, out_meshes_dir: str, work_dir: str,
                      workers: int = None, log=print) -> dict:
    """Retarget every animation file the plugin's NPCs (and its masters') name.

    Writes the clips, their manifest and the runtime table; returns the
    manifest, {} when no NPC names a file that ships an animation.
    See: docs/commentary/asset_convert_animation.md#morrowind-npc-animation-files
    """
    dirs = [export_dir] + list(base_plugins.export_dirs(export_dir))
    bindings = npc_bindings(dirs)
    roots = [Path(assets_for(d)) / 'meshes' for d in dirs]
    plugin_out = os.path.dirname(os.path.normpath(out_meshes_dir))
    plugin_name = os.path.basename(os.path.normpath(export_dir))
    sources = _sources(bindings, roots, log) if bindings else {}
    base = resolve_mesh(roots, BASE_ANIM, paths.EXPORT) if sources else None
    if base is None:
        return {}
    layout = anim_layout(out_meshes_dir)
    os.makedirs(layout['dir'], exist_ok=True)
    os.makedirs(work_dir, exist_ok=True)
    log(f'  Retargeting {len(sources)} NPC animation files for '
        f'{len(bindings)} NPCs...')
    clips = _convert_pool(sources, write_skeleton(base, work_dir), layout['dir'],
                          workers or worker_count(), log)
    manifest = {'anim_dir': layout['anim_dir'], 'clips': clips}
    with open(os.path.join(layout['dir'], ANIM_MANIFEST), 'w',
              encoding='utf-8') as handle:
        json.dump(manifest, handle)
    table = write_runtime_table(plugin_out, plugin_name, clips, bindings,
                                placements(export_dir, bindings))
    log(f'  NPC animations: {len(clips)} clips -> {layout["anim_dir"]}, '
        f'table {os.path.relpath(table, plugin_out)}')
    return manifest
