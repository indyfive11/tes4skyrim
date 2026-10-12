"""Swapping a vanilla Morrowind texture for Morroblivion's higher-res twin.

Morroblivion renamed every texture '_' -> 'u' and repainted many at up to 16x
the resolution, so a Tamriel Rebuilt wall beside a Morroblivion one renders at
a fraction of its texel density. The compatibility patch's Export step proves
which twins are interchangeable and writes them as `MAP_NAME` in its record
dir (`tes4_export/morroblivion_texture_map.py`); every plugin whose master
chain reaches that map swaps those textures wherever a mesh names them.
Authored-mode Morrowind never has the patch as a master, so it never swaps.

See: docs/commentary/asset_convert_texture.md#morroblivion-texture-substitution
"""

import os

from asset_convert.nif.tex_paths import texture_rel_path
from asset_convert.texture.texture_prune import read_manifest, write_manifest
from core.plugin_masters import export_root, master_chain, master_export_dir

#: The map's file name in the record dir of the plugin that proved it.
MAP_NAME = 'morroblivion_textures.txt'

#: Carries the active map's folder into spawned workers and child processes.
MAP_ENV = 'TESCONV_MORROBLIVION_TEXTURES'

#: The map last read in this process, and the folder it was read from.
_LOADED = {'folder': None, 'table': {}}


def write_map(record_dir, table: dict):
    """Write {vanilla texture stem: Morroblivion path} beside a plugin's records."""
    lines = {stem + '\t' + path for stem, path in table.items()}
    return write_manifest(record_dir, lines, MAP_NAME)


def read_map(record_dir) -> dict:
    """The map written into `record_dir`, or {} when there is none."""
    rows = (line.split('\t', 1) for line in read_manifest(record_dir, MAP_NAME))
    return {row[0]: row[1] for row in rows if len(row) == 2}


def map_folder(record_dir) -> str:
    """The record dir whose map this plugin inherits: its own, else a master's; ''."""
    root = export_root(record_dir)
    folders = [str(record_dir)] + [master_export_dir(root, name)
                                   for name in master_chain(record_dir)]
    return next((f for f in folders
                 if os.path.isfile(os.path.join(f, MAP_NAME))), '')


def activate(record_dir) -> str:
    """Arm this plugin's map here and in every process spawned from here on."""
    folder = map_folder(record_dir)
    if folder:
        os.environ[MAP_ENV] = folder
    else:
        os.environ.pop(MAP_ENV, None)
    return folder


def _table() -> dict:
    """The active map, read once per process per folder."""
    folder = os.environ.get(MAP_ENV, '')
    if _LOADED['folder'] != folder:
        _LOADED.update(folder=folder, table=read_map(folder) if folder else {})
    return _LOADED['table']


def texture_key(authored_tex) -> str:
    """A texture's identity: its path below `textures\\`, lower case, no extension.

    See: docs/commentary/asset_convert_texture.md#texture-identity
    """
    return os.path.splitext(texture_rel_path(authored_tex))[0].lower()


def substitute(authored_tex):
    """Morroblivion's path for a vanilla diffuse the active map clears, or None."""
    twin = _table().get(texture_key(authored_tex)) if authored_tex else None
    return None if twin is None else twin.encode('utf-8')
