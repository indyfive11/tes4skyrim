"""The one class menu and one birthsign menu every converted game shares.

MorrowindRuntime shows a single class list and a single birthsign list, the
shared `chargen.txt` the packaged runtime carries. Its rows come from one
exported game -- `chargenSource` in conversion_config.json, picked in the GUI --
read with its direct masters as the importer reads them. A menu that game has
none of comes from the next game that has it. Each plugin's own sidecar names
only its globals and its own entries, so the runtime answers a converted script
with that plugin's index of the pick.

See: docs/commentary/morrowind_runtime.md#chargen-menus
"""

import functools
import os
from pathlib import Path

from asset_convert.sources import source_registry
from core.birthsign_text import (WORDS, icon_key, sign_lines, tes3_spell, tes3_tables,
                                 tes3_words, tes4_spell)
from output_layout import record_dir
from script_convert.context_setup import chargen_menu_plan, chargen_records
from script_convert.message_menus import build_chargen_menus, class_line, sign_line
from tes5_import.base.text_reader import parse_export_file
from tes5_import.dialogue.morrowind_sidecar_source import source_chain
from tes5_import.record_types.world_falloutnv import FALLOUT_ONLY_SIGS

#: The config key naming the game whose classes and birthsigns the menus show.
CHARGEN_SOURCE_KEY = 'chargenSource'

#: Where the shared table sits in the packaged runtime.
SHARED_CHARGEN = Path('SKSE') / 'Plugins' / 'MorrowindRuntime' / 'chargen.txt'

#: The plugin file extensions an export folder can be named for.
_PLUGIN_EXTS = ('.esm', '.esp')


def exported_plugins(export_root) -> dict:
    """{plugin: record folder} for every exported plugin: the game-data ones
    named for their folder, then every imported mod's."""
    root = str(export_root)
    found = {name: os.path.join(root, name) for name in sorted(os.listdir(root))
             if name.lower().endswith(_PLUGIN_EXTS)
             and os.path.isfile(os.path.join(root, name, '_HEADER.txt'))} \
        if os.path.isdir(root) else {}
    for name in source_registry.plugins(root):
        folder = str(record_dir(root, name))
        if os.path.isfile(os.path.join(folder, '_HEADER.txt')):
            found.setdefault(name, folder)
    return found


def _is_fallout(folder: str) -> bool:
    """Whether the export is FO3/FNV, which has no TES chargen."""
    return any(os.path.isfile(os.path.join(folder, f'{sig}.txt')) for sig in FALLOUT_ONLY_SIGS)


def _own_records(folder: str, sig: str) -> list:
    """The export's own records of one type."""
    path = os.path.join(folder, f'{sig}.txt')
    return parse_export_file(path) if os.path.isfile(path) else []


def candidates(export_root) -> list:
    """The plugins whose OWN records hold a playable class or a birthsign, sorted."""
    out = []
    for name, folder in exported_plugins(export_root).items():
        if not _is_fallout(folder) and build_chargen_menus(
                _own_records(folder, 'BSGN'), _own_records(folder, 'CLAS'), []):
            out.append(name)
    return sorted(out, key=str.lower)


def _complete(plan: dict) -> bool:
    """Whether a plan has signs, and classes all naming favored attributes."""
    rows = plan.get('class', {}).get('rows', ())
    return bool(rows) and 'birthsign' in plan and all(-1 not in row['attributes'] for row in rows)


def chosen_source(export_root, names: list, chosen) -> str:
    """The configured game when it is one of `names`; else the first whose own
    table is complete, else the first; '' for none."""
    match = [n for n in names if n.lower() == str(chosen or '').lower()]
    if match or not names:
        return (match or [''])[0]
    plugins = exported_plugins(export_root)
    return next((n for n in names if _complete(chargen_menu_plan(plugins[n], n))), names[0])


def _tes4_spells(folder: str, plugin: str) -> tuple:
    """({(owner plugin, local FormID): SPEL}, {effect code: MGEF}) of an export and its masters."""
    records = chargen_records(folder, plugin, ('SPEL', 'MGEF'))
    spells = {}
    for rec in records['SPEL']:
        fid = int(rec.get('FormID') or '0', 16)
        masters, slot = rec.get('_masters') or [], fid >> 24
        owner = masters[slot] if slot < len(masters) else rec.get('_plugin', '')
        spells[(owner.lower(), fid & 0xFFFFFF)] = rec
    return spells, {rec.get('EditorID'): rec for rec in records['MGEF']}


def _spell(spell_id: str, tes4: tuple, tes3: 'dict | None') -> 'dict | None':
    """One sign spell, `Owner.esm@FormID` from the export, a TES3 id from the binaries."""
    owner, at, local = spell_id.partition('@')
    if at:
        rec = tes4[0].get((owner.lower(), int(local or '0', 16)))
        return tes4_spell(rec, tes4[1]) if rec else None
    return tes3_spell(spell_id, tes3) if tes3 and spell_id else None


def sign_display(export_root, folder: str, plugin: str, rows: list) -> dict:
    """Each sign row's `display` lines, OpenMW's (core.birthsign_text), set on the
    row; returns `{icon key: icon path}` for every effect icon they show. The
    TES3 binaries are read only when a sign names a TES3 spell."""
    tes4 = _tes4_spells(folder, plugin)
    needs_tes3 = any('@' not in spell for row in rows for spell in row['ids'])
    tes3 = tes3_tables(source_chain(str(export_root), plugin)) if needs_tes3 else None
    words = tes3_words(tes3) if tes3 else WORDS
    icons = {}
    for row in rows:
        lines = sign_lines([s for s in (_spell(i, tes4, tes3) for i in row['ids']) if s], words)
        row['display'] = [(kind, icon_key(path) if path else '', text)
                          for kind, path, text in lines]
        icons.update({icon_key(path): path for _kind, path, _text in lines if path})
    return icons


@functools.lru_cache(maxsize=4)
def shared_lines(export_root, chosen=None) -> tuple:
    """(the game the menus come from, the shared table's lines, `{icon key: path}`
    of the birthsign menu's effect icons). A menu the game has none of is filled
    from the next candidate that has it. Read once per run."""
    names = candidates(export_root)
    source = chosen_source(export_root, names, chosen)
    plugins = exported_plugins(export_root)
    plan, owners = {}, {}
    for name in [source] + [n for n in names if n != source] if source else []:
        for key, menu in chargen_menu_plan(plugins[name], name).items():
            owners.setdefault(key, name)
            plan.setdefault(key, menu)
        if {'class', 'birthsign'} <= set(plan):
            break
    signs = plan.get('birthsign', {}).get('rows', [])
    icons = sign_display(export_root, plugins[owners['birthsign']], owners['birthsign'],
                         signs) if signs else {}
    lines = [class_line(i, row) for i, row in enumerate(plan.get('class', {}).get('rows', ()))]
    lines += [sign_line(i, row) for i, row in enumerate(signs)]
    return source, lines, icons
