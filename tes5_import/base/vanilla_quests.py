"""Quest EditorIDs the vanilla Skyrim masters already use, and our clash-free rename.

See: docs/commentary/tes5_import_quest.md#quest-editorids-that-clash-with-skyrim
"""

import functools
import mmap
import os

from asset_convert.sources.skyrim_assets import find_skyrim_data

from .tes5_reader import records, zstr

#: The masters every SSE install loads, in load order.
VANILLA_MASTERS = ('Skyrim.esm', 'Update.esm', 'Dawnguard.esm',
                   'HearthFires.esm', 'Dragonborn.esm')

#: Prefix that moves a clashing quest EditorID off the vanilla name.
CLASH_PREFIX = 'TES4'


def _quest_edids(path: str) -> set:
    """Lowercased QUST EditorIDs in one plugin file."""
    with open(path, 'rb') as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as raw:
        return {zstr(edid).lower() for rec in records(raw, b'QUST')
                if (edid := rec.sub(b'EDID'))}


@functools.lru_cache(maxsize=1)
def vanilla_quest_edids() -> frozenset:
    """Lowercased QUST EditorIDs across the installed vanilla masters; empty if none is found."""
    data = find_skyrim_data()
    found = set()
    for esm in VANILLA_MASTERS:
        path = os.path.join(data, esm) if data else ''
        if os.path.isfile(path):
            found |= _quest_edids(path)
    return frozenset(found)


def engine_quest_edid(edid: str) -> str:
    """The quest EditorID we write: prefixed only when a vanilla quest owns it."""
    if edid and edid.lower() in vanilla_quest_edids():
        return CLASH_PREFIX + edid
    return edid
