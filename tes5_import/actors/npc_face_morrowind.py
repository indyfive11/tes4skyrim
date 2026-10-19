"""
Morrowind NPC faces: vanilla hair and beards for the authored parts, sliders measured from the head.

Morrowind gives every NPC a head mesh and a hair mesh, reused across many
NPCs. Each head becomes a preset: its shape measured against the other heads
of its race and sex, moved onto Skyrim's sliders by the same number of vanilla
spreads, and its texture's color as the skin tone. Each NPC then varies its
head's preset slightly, seeded by its EditorID. Hair and beards come from a
hand-reviewed table of the closest vanilla head parts.

See: docs/commentary/tes5_import_actors.md#morrowind-faces
"""

import json
import os
import zlib
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path

import numpy as np

from asset_convert import paths
from asset_convert.character.morrowind_head import FEATURES, measure_job
from output_layout import assets_for
from ..base.text_reader import get_int, get_str
from ..base.writer import pack_formid_subrecord

_DATA_PATH = Path(__file__).resolve().parent.parent / 'generated' / 'morrowind_faces.json'

#: Head feature -> (NAM9 slider, sign) pairs; directions from SKSE's FaceMorphList names.
_SLIDERS = {
    'nose_forward': ((0, 1.0),),
    'nose_drop': ((1, -1.0),),
    'chin_length': ((2, 1.0), (15, 1.0)),
    'jaw_width': ((3, 1.0), (14, 0.5)),
    'chin_forward': ((4, 1.0), (16, 0.5)),
    'cheek_width': ((6, 1.0),),
    'brow_forward': ((11, 1.0),),
}

#: Fraction of vanilla's per-slider spread each NPC varies its head's preset by.
_JITTER = 0.1

#: A head is placed at most this many vanilla spreads from its race's mean.
_Z_CLAMP = 2.5

#: A race and sex with fewer measured heads than this is compared against every head.
_MIN_GROUP = 6

#: NAM9 sliders measured or averaged (the 19th is the vampire slot, left 0).
_N_SLIDERS = 18

#: Parts below this many are measured in-process; a pool costs seconds to start.
_POOL_MIN = 200

#: ACBS.Flags bit 0: female.
_FEMALE = 0x1

#: part id (lower case) -> (head features or None, texture color or None), filled by load().
PARTS = {}

#: (Morrowind race, female) or None -> (feature mean, feature spread), filled by load().
GROUPS = {}


@lru_cache(maxsize=1)
def _data() -> dict:
    """The shipped table: hair, beard, head_part_races, hair_pools, faces."""
    with open(_DATA_PATH, encoding='utf-8') as fh:
        return json.load(fh)


def is_morrowind_face(rec: dict) -> bool:
    """Whether the record names a Morrowind head or hair part."""
    return bool(get_str(rec, 'MorrowindHead') or get_str(rec, 'MorrowindHair'))


def _group_key(rec: dict):
    """(Morrowind race, female) of an NPC record."""
    return get_str(rec, 'MorrowindRace').lower(), bool(get_int(rec, 'ACBS.Flags') & _FEMALE)


def load(npcs, record_dirs) -> int:
    """Measure every head and hair part `npcs` wear; returns how many parts were measured.

    `record_dirs` are the plugin's and its masters' record folders, whose asset
    trees are searched before the vanilla Morrowind install.
    """
    PARTS.clear()
    GROUPS.clear()
    mesh_roots = [str(assets_for(d) / 'meshes') for d in record_dirs if d]
    tex_roots = [str(assets_for(d) / 'textures') for d in record_dirs if d]
    jobs = {}
    for rec in npcs:
        for key, is_head in (('MorrowindHead', True), ('MorrowindHair', False)):
            part, model = get_str(rec, key).lower(), get_str(rec, key + 'Model')
            if part and model:
                jobs.setdefault(part, (model, mesh_roots, tex_roots, str(paths.EXPORT), is_head))
    PARTS.update(zip(jobs, _measure_all(list(jobs.values()))))
    heads = {}
    for rec in npcs:
        head = get_str(rec, 'MorrowindHead').lower()
        if PARTS.get(head, (None, None))[0] is not None:
            heads.setdefault(_group_key(rec), {})[head] = PARTS[head][0]
    _fill_groups(heads)
    return len(PARTS)


def _measure_all(jobs: list) -> list:
    """`measure_part` over `jobs`, in a process pool once there are enough to repay its start-up."""
    if len(jobs) < _POOL_MIN:
        return [measure_job(job) for job in jobs]
    with ProcessPoolExecutor(max_workers=max(1, (os.cpu_count() or 2) - 1)) as pool:
        return list(pool.map(measure_job, jobs, chunksize=16))


def _fill_groups(heads: dict) -> None:
    """GROUPS from {group: {head: features}}; small groups fall back to every head (key None)."""
    every = [f for group in heads.values() for f in group.values()]
    if not every:
        return
    GROUPS[None] = (np.mean(every, axis=0), np.std(every, axis=0) + 1e-6)
    for key, group in heads.items():
        if len(group) >= _MIN_GROUP:
            feats = list(group.values())
            GROUPS[key] = (np.mean(feats, axis=0), np.std(feats, axis=0) + 1e-6)


def _stats(skyrim_race: int, female: bool):
    """Vanilla {mean, sd, weight} for a Skyrim race and sex, or None."""
    return _data()['faces'].get(f"{skyrim_race:08X}|{'F' if female else 'M'}")


def _rng(rec: dict) -> np.random.Generator:
    """A generator seeded by the NPC's EditorID, so its variation never changes."""
    return np.random.default_rng(zlib.crc32(get_str(rec, 'EditorID').lower().encode()))


def face_morphs(rec: dict, skyrim_race: int, female: bool):
    """The 19 NAM9 floats for a Morrowind NPC, or None when its race has no vanilla profile."""
    stats = _stats(skyrim_race, female)
    if stats is None:
        return None
    mean, sd = np.array(stats['mean']), np.array(stats['sd'])
    morphs = mean.copy()
    feats = PARTS.get(get_str(rec, 'MorrowindHead').lower(), (None, None))[0]
    group = GROUPS.get(_group_key(rec)) or GROUPS.get(None)
    if feats is not None and group is not None:
        z = np.clip((feats - group[0]) / group[1], -_Z_CLAMP, _Z_CLAMP)
        for name, value in zip(FEATURES, z):
            for slider, sign in _SLIDERS[name]:
                morphs[slider] += sign * value * sd[slider]
    morphs += _rng(rec).normal(0.0, _JITTER, _N_SLIDERS) * sd
    return [float(v) for v in np.clip(morphs, -1.0, 1.0)] + [0.0]


def weight(rec: dict, skyrim_race: int, female: bool):
    """NAM7 body weight (0-100): the race's vanilla mean, varied slightly; None without a profile."""
    stats = _stats(skyrim_race, female)
    if stats is None:
        return None
    mean, sd = stats['weight']
    return float(np.clip(mean + _rng(rec).normal(0.0, _JITTER) * sd, 0.0, 100.0))


def skin_color(rec: dict):
    """The (r, g, b) of the NPC's head texture, or None."""
    return PARTS.get(get_str(rec, 'MorrowindHead').lower(), (None, None))[1]


def hair_color(rec: dict):
    """The (r, g, b) of the NPC's hair texture, or None."""
    return PARTS.get(get_str(rec, 'MorrowindHair').lower(), (None, None))[1]


def _allowed(fid_hex: str, skyrim_race: int) -> bool:
    """Whether a vanilla head part lists `skyrim_race` among its valid races."""
    return f'{skyrim_race:08X}' in _data()['head_part_races'].get(fid_hex, ())


def _hair_fid(hair: str, skyrim_race: int, female: bool) -> int:
    """The vanilla hair for a Morrowind hair part: its table entry, else one from the race's pool."""
    data = _data()
    pick = data['hair'].get(hair)
    if pick == '':
        return 0
    if pick and _allowed(pick, skyrim_race):
        return int(pick, 16)
    pool = data['hair_pools'].get(f"{skyrim_race:08X}|{'F' if female else 'M'}")
    return int(pool[zlib.crc32(hair.encode()) % len(pool)], 16) if pool else 0


def head_part_subs(rec: dict, skyrim_race: int, female: bool) -> bytes:
    """PNAM subrecords for a Morrowind NPC's hair and beard (b'' for neither)."""
    subs = b''
    hair = get_str(rec, 'MorrowindHair').lower()
    fid = _hair_fid(hair, skyrim_race, female) if hair else 0
    if fid:
        subs += pack_formid_subrecord('PNAM', fid)
    beard = _data()['beard'].get(get_str(rec, 'MorrowindHead').lower())
    if beard and _allowed(beard, skyrim_race):
        subs += pack_formid_subrecord('PNAM', int(beard, 16))
    return subs
