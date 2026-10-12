"""Resting-item placements for the Morroblivion patch, read from its source ESMs.

The patch is exported before anything that places its records, so its shelves
are stocked by the vanilla ESMs it fills gaps from -- the same Morrowind,
Tribunal and Bloodmoon files `export_patch` reads from the registered Data
folder. Placements are matched by Morrowind object id, which the patch keeps
as each record's EditorID.
See: docs/commentary/asset_convert_collision.md#morrowind-stand-in-boxes
"""

from collections import defaultdict

from asset_convert.collision.clutter_plan import CLUTTER_TYPES, WEARABLE_TYPES
from asset_convert.nif.fixture_plan import fixture_model_ids
from tes4_export.morrowind_cell import parse_cell
from tes4_export.morrowind_patch import PATCH_SOURCES, source_dir, source_paths
from tes4_export.record_types.morrowind import tes4_signature
from tes4_export.tes3_reader import read_file

#: TES4 export file names of the item types whose origins open a box.
_ITEM_FILES = frozenset(CLUTTER_TYPES + WEARABLE_TYPES)


def _source_records(export_root) -> list:
    """Every record of the patch's source ESMs, in load order."""
    paths = source_paths(source_dir(str(export_root)), PATCH_SOURCES)[0]
    return [rec for path in paths for rec in read_file(path)[1]]


def _cell_refs(records):
    """(cell key, CellRef) of every live reference; interiors keyed by name."""
    for rec in records:
        if rec.type != 'CELL':
            continue
        cell = parse_cell(rec)
        key = cell.name.lower() if cell.interior else cell.grid
        for ref in cell.refs:
            if not ref.deleted:
                yield key, ref


def patch_layout(export_root, own) -> tuple:
    """(item origins per cell, {model: [(cell, placement)]}) for the patch at `own`.

    Only fixtures placed in a cell that holds an item are kept.
    """
    records = _source_records(export_root)
    items = {(rec.record_id or '').lower() for rec in records
             if tes4_signature(rec) + '.txt' in _ITEM_FILES}
    fixtures = {edid.lower(): model for edid, model in
                fixture_model_ids(own, field='EditorID').items() if edid}
    points, refs = defaultdict(list), []
    for cell, ref in _cell_refs(records):
        rid = ref.record_id.lower()
        if rid in items:
            points[cell].append(ref.pos)
        elif rid in fixtures:
            refs.append((fixtures[rid], cell, [*ref.pos, *ref.rot, ref.scale]))
    placed = defaultdict(list)
    for model, cell, place in refs:
        if cell in points:
            placed[model].append((cell, place))
    return points, placed
