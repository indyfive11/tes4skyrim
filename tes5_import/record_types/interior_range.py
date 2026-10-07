"""Census of interior references outside the range Skyrim keeps in place.

When Skyrim builds an interior cell's physics world it tests each reference's
X and Y against 30,000 units.  A reference beyond that is put back on its
start position or, failing that, moved onto the cell's default spot; lights,
actors and levelled spawns never reach the test.  Oblivion has no such rule,
so a converted interior that is larger than the square, or was built far from
its origin, arrives with its contents stacked on one point.

Nothing is changed here: the census names the cells, so whoever runs the
build knows which interiors need the rule switched off at run time.

See: docs/commentary/tes5_import_world.md#interior-range-census
"""

from collections import Counter

from ..base.text_reader import get_float, get_int
from ..overrides.nested import DELETED_FLAG
from .world import shifted_position

#: Half-width of the square Skyrim holds an interior to, on X and on Y.
INTERIOR_LIMIT = 30000.0
#: Source types the engine's test never sees: lights, actors, levelled actors.
_EXEMPT_BASES = frozenset({'LIGH', 'NPC_', 'CREA', 'LVLC', 'LVLN'})
_CELLS_SHOWN = 5


def _exempt_bases(by_type: dict, master_export: dict) -> set:
    """Raw FormIDs of every base the engine's test never sees.

    Spelled as a reference's `NAME` spells them.  The plugin's own records
    and its masters' are matched by id, never by master index: a plugin can
    inject a record into a master's id space.
    """
    own = {rec['FormID'].upper() for sig in _EXEMPT_BASES
           for rec in by_type.get(sig, ()) if rec.get('FormID')}
    return own | {fid for fid, rec in (master_export or {}).items()
                  if rec.get('Signature') in _EXEMPT_BASES}


def _own_cells(by_type: dict) -> dict:
    """This plugin's CELL records by raw FormID."""
    return {(rec.get('FormID') or '').upper(): rec
            for rec in by_type.get('CELL', ())}


def _is_exterior(cell: str, own_cells: dict, master_export: dict) -> bool:
    """True when the CELL record `cell`, the plugin's or a master's, has a worldspace."""
    rec = own_cells.get(cell) or (master_export or {}).get(cell) or {}
    return bool(rec.get('ParentWRLD'))


def census(by_type: dict, master_export: dict = None) -> list:
    """`(count, cell FormID)` per interior cell with out-of-range references.

    Largest first, ties by cell id.  A reference is exterior when it, or its
    cell, names a worldspace; exporters differ in which of the two they
    write.  The test is the engine's: strictly beyond the limit on X or on
    Y, at the position the importer writes; Z is not tested.

    See: docs/commentary/tes5_import_world.md#interior-range-census
    """
    exempt = _exempt_bases(by_type, master_export)
    counts = Counter()
    for rec in by_type.get('REFR', ()):
        cell = rec.get('ParentCELL')
        if not cell or rec.get('ParentWRLD'):
            continue
        if get_int(rec, 'RecordFlags') & DELETED_FLAG:
            continue
        if (rec.get('NAME') or '').upper() in exempt:
            continue
        x, y, _z = shifted_position(rec, get_float(rec, 'XSCL.Scale'))
        if abs(x) > INTERIOR_LIMIT or abs(y) > INTERIOR_LIMIT:
            counts[cell.upper()] += 1
    own_cells = _own_cells(by_type)
    return sorted(((n, cell) for cell, n in counts.items()
                   if not _is_exterior(cell, own_cells, master_export)),
                  key=lambda row: (-row[0], row[1]))


def report(by_type: dict, master_export: dict = None) -> int:
    """Print the census; return how many references it counted.

    Prints nothing when every interior reference is in range.

    See: docs/commentary/tes5_import_world.md#interior-range-census
    """
    rows = census(by_type, master_export)
    if not rows:
        return 0
    total = sum(n for n, _cell in rows)
    own_cells = _own_cells(by_type)
    print(f"  WARNING: {total} interior reference(s) in {len(rows)} cell(s) "
          f"lie beyond {INTERIOR_LIMIT:.0f} units on X or Y; Skyrim moves each "
          f"one onto a single spot in its cell")
    for n, cell in rows[:_CELLS_SHOWN]:
        rec = own_cells.get(cell) or (master_export or {}).get(cell) or {}
        print(f"    {rec.get('EditorID') or '?'} ({cell}): {n}")
    if len(rows) > _CELLS_SHOWN:
        print(f"    ... and {len(rows) - _CELLS_SHOWN} more cell(s)")
    return total
