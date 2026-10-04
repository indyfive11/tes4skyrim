"""Per-plugin setup for script conversion: output directory, caches and plans.

The phases `pipeline.build_script_context` runs before it hands work to the
converter pool.  Each is a pure function of the export/output directories or
of the parsed record tables, so a subset rebuild and the full stage share them.

See: docs/commentary/script_convert.md#script-output-dir
"""

import os
import shutil

from asset_convert.collision.collision_extract import bounds_cache_is_current
from output_layout import assets_for
from script_convert.cross_ref import CrossRefGraph, master_names
from script_convert.message_menus import build_chargen_menus
from tes5_import.base.mesh_bounds import load_mesh_bounds
from tes5_import.base.text_reader import parse_export_file

#: Static Papyrus sources deployed beside the generated scripts of a masterless plugin.
_STATIC_DIR = os.path.join(os.path.dirname(__file__), 'static_scripts')


def prepare_output_dir(output_dir: str) -> None:
    """Wipe the generated .psc tree and every sibling .pex, then recreate it.

    See: docs/commentary/script_convert.md#wipe-output-dir
    """
    if os.path.isdir(output_dir):
        shutil.rmtree(output_dir)
    pex_dir = os.path.dirname(output_dir)
    if os.path.isdir(pex_dir):
        for name in os.listdir(pex_dir):
            if name.lower().endswith('.pex'):
                _remove_quietly(os.path.join(pex_dir, name))
    os.makedirs(output_dir, exist_ok=True)


def _remove_quietly(path: str) -> None:
    """Remove a file, tolerating one that vanished or is locked."""
    try:
        os.remove(path)
    except OSError:
        pass


def load_bounds_cache(export_dir: str) -> str:
    """Load the mesh-bounds cache and return its path, warning when stale.

    See: docs/commentary/script_convert.md#bounds-cache-schema
    """
    cache = str(assets_for(export_dir) / 'mesh_bounds_cache.json')
    if not bounds_cache_is_current(cache):
        print("  WARNING: mesh bounds cache is missing or predates the current "
              "schema.\n"
              "           Breakaway/trap havok releases will NOT be emitted "
              "(planks and traps\n"
              "           will hang instead of falling).  Run the import or "
              "meshes step to\n"
              f"           rebuild it: {cache}")
    load_mesh_bounds(cache, quiet=True)
    return cache


def _static_script_names() -> list:
    """The .psc files shipped in static_scripts/."""
    if not os.path.isdir(_STATIC_DIR):
        return []
    return [n for n in os.listdir(_STATIC_DIR) if n.endswith('.psc')]


def deploy_static_scripts(export_dir: str, output_dir: str) -> None:
    """Copy the static scripts for a masterless plugin; purge them for a dependent.

    See: docs/commentary/script_convert.md#static-scripts-ownership
    """
    names = _static_script_names()
    if not master_names(export_dir):
        for name in names:
            shutil.copy2(os.path.join(_STATIC_DIR, name),
                         os.path.join(output_dir, name))
        return
    print('  Static scripts: skipped (owned by this plugin\'s master)')
    for name in names:
        for stale in (os.path.join(output_dir, name),
                      os.path.join(os.path.dirname(output_dir),
                                   name[:-4] + '.pex')):
            if os.path.isfile(stale):
                os.remove(stale)
                print(f'    removed stale master-owned copy: {stale}')


def build_xref(export_dir: str) -> CrossRefGraph:
    """The cross-reference graph, with the cross-script ref-as-int analysis."""
    print('  Building cross-reference graph...')
    xref = CrossRefGraph()
    xref.load_from_export(export_dir)
    print(f'    {len(xref.formid_to_edid)} FormID->EditorID mappings')
    print(f'    {len(xref.script_formid_to_edid)} scripts, '
          f'{len(xref.quest_edids)} quests')
    scpt_path = os.path.join(export_dir, 'SCPT.txt')
    if os.path.exists(scpt_path):
        xref.build_script_indexes(scpt_path)
        if xref.ref_as_int:
            print(f'    {len(xref.ref_as_int)} ref variables detected as '
                  f'integer-only (cross-script)')
    return xref


def load_records(export_dir: str, sigs: tuple) -> dict:
    """signature -> parsed records; a missing export file is an empty list."""
    out = {}
    for sig in sigs:
        path = os.path.join(export_dir, f'{sig}.txt')
        out[sig] = parse_export_file(path) if os.path.exists(path) else []
    return out


def service_menu_topics(by_type: dict, menu_topics: dict,
                        service_type: int) -> dict:
    """DIAL FormID -> service menu name, for the service-typed menu topics."""
    out = {}
    for rec in by_type.get('DIAL', []):
        edid = rec.get('EditorID', '')
        if edid in menu_topics and rec.get('DATA.Type', '') == str(service_type):
            out[rec.get('FormID', '')] = menu_topics[edid][0]
    return out


def topic_unlock_globals(by_type: dict, unlock_plan: dict) -> dict:
    """DIAL EditorID (lower) -> unlock global, so a script `AddTopic X` opens the same gate."""
    out = {}
    for d in by_type.get('DIAL', []):
        edid = (d.get('EditorID') or '').lower()
        fid = d.get('FormID', '')
        if not edid or not fid:
            continue
        gname = unlock_plan['gated'].get(int(fid, 16) & 0xFFFFFF)
        if gname:
            out[edid] = gname
    return out


def quest_edids_by_fid(by_type: dict) -> dict:
    """QUST low-24 FormID -> EditorID."""
    return {int(r['FormID'], 16) & 0xFFFFFF: (r.get('EditorID') or '')
            for r in by_type.get('QUST', []) if r.get('FormID')}


def chargen_menu_plan(export_dir: str) -> dict:
    """The ShowBirthsignMenu / ShowClassMenu page plan, shared with the importer."""
    bsgn_p = os.path.join(export_dir, 'BSGN.txt')
    clas_p = os.path.join(export_dir, 'CLAS.txt')
    if not (os.path.exists(bsgn_p) or os.path.exists(clas_p)):
        return {}
    spel_map = {int(r['FormID'], 16) & 0xFFFFFF: r['EditorID']
                for r in load_records(export_dir, ('SPEL',))['SPEL']
                if r.get('FormID') and r.get('EditorID')}
    menus = build_chargen_menus(
        parse_export_file(bsgn_p) if os.path.exists(bsgn_p) else [],
        parse_export_file(clas_p) if os.path.exists(clas_p) else [],
        spel_map)
    if menus:
        print('    Chargen menus: ' + ', '.join(
            f"{k} ({len(v['actions'])} options, {len(v['pages'])} pages)"
            for k, v in sorted(menus.items())))
    return menus
