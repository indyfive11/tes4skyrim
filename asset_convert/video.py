"""Ship the videos a plugin's scripts play into output/<plugin>/Video/.

Skyrim SE's bink2w64.dll plays Bink 1 (`BIK`) and Bink 2 (`KB2`) files, so a
video is copied byte for byte. Only names a `PlayBink` in this plugin's own
scripts asks for are taken, minus any its masters ask for: the master ships
those. A name found in no source folder is reported, never invented.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from core.plugin_masters import export_root, master_chain, master_export_dir
from output_layout import asset_root, plugin_out_root, record_dir

#: The export files that hold script text: scripts, dialogue and quest result scripts, FO3/FNV package and terminal ones.
SCRIPT_TYPES = ('SCPT', 'INFO', 'QUST', 'MWIN', 'PACK', 'TERM')

#: `PlayBink "file.bik" ...` or `PlayBink file`; result scripts carry `\r\n` as text.
_PLAYBINK = re.compile(r'playbink\s+(?:"([^"\r\n]+)"|([^\s"\\]+))', re.I)

#: The header magics bink2w64.dll accepts: Bink 1 and Bink 2.
BINK_MAGICS = (b'BIK', b'KB2')


def played_videos(rec_dir) -> dict:
    """`{lowercased name: name as written}` for every `PlayBink` in `rec_dir`."""
    found = {}
    for rtype in SCRIPT_TYPES:
        path = Path(rec_dir) / f'{rtype}.txt'
        if not path.is_file():
            continue
        text = path.read_text(encoding='utf-8', errors='replace')
        for quoted, bare in _PLAYBINK.findall(text):
            name = (quoted or bare).strip()
            found.setdefault(name.lower(), name)
    return found


def _masters_videos(rec_dir) -> set:
    """Lowercased names any master of `rec_dir`, transitively, plays."""
    root = export_root(rec_dir)
    names = set()
    for master in master_chain(rec_dir):
        names.update(played_videos(master_export_dir(root, master)))
    return names


def _listing(folder: Path) -> dict:
    """`{lowercased file name: path}` for one folder, empty when it is absent."""
    if not folder.is_dir():
        return {}
    return {p.name.lower(): p for p in folder.iterdir() if p.is_file()}


def _is_bink(path: Path) -> bool:
    """Whether the file starts with a Bink 1 or Bink 2 header."""
    with open(path, 'rb') as fh:
        return fh.read(3) in BINK_MAGICS


def _copy(src: Path, dst: Path) -> bool:
    """Copy unless an equal-sized copy is there; True when it wrote.

    The old file is unlinked first: output/ may be hard-linked into a game.
    """
    if dst.is_file() and dst.stat().st_size == src.stat().st_size:
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        dst.unlink()
    shutil.copy2(src, dst)
    return True


def convert_videos(source_file, extract_dir, output_dir, data_dir=None) -> dict:
    """Copy each video this plugin plays into output/<plugin>/Video/.

    Looks in the plugin's Data folder's `Video/` first (loose wins, as in the
    engine), then in the export tree's `misc/video/` (mod archives and BSAs).
    Returns counts plus the names found nowhere.
    """
    rec_dir = record_dir(extract_dir, source_file)
    wanted = played_videos(rec_dir)
    for name in _masters_videos(rec_dir):
        wanted.pop(name, None)
    sources = _listing(asset_root(extract_dir, source_file) / 'misc' / 'video')
    if data_dir:
        sources.update(_listing(Path(data_dir) / 'Video'))
    stats = {'wanted': len(wanted), 'copied': 0, 'cached': 0,
             'missing': [], 'not_bink': []}
    out_dir = plugin_out_root(output_dir, source_file, extract_dir) / 'Video'
    for key, name in sorted(wanted.items()):
        src = sources.get(key)
        if src is None:
            stats['missing'].append(name)
        elif not _is_bink(src):
            stats['not_bink'].append(name)
        else:
            stats['copied' if _copy(src, out_dir / src.name) else 'cached'] += 1
    return stats


def report(file_name: str, stats: dict) -> None:
    """Print one summary line, and each name that could not ship."""
    print(f"[{file_name}] Videos: {stats['wanted']} played by its scripts, "
          f"{stats['copied']} copied, {stats['cached']} already in place")
    for name in stats['missing']:
        print(f"[{file_name}]   WARNING: video '{name}' is in no source folder")
    for name in stats['not_bink']:
        print(f"[{file_name}]   WARNING: video '{name}' is not a Bink file")
