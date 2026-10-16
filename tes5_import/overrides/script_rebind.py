"""
Records bound to a master's script that this plugin EDITS carry this plugin's properties.

An override is the master's converted bytes plus the author's field changes,
so a record bound to a master's script keeps the MASTER's VMAD.  When the
plugin edits that script's source, the script that runs is the plugin's (its
compiled script shadows the master's), and every object property the edit
added binds to nothing: a third-party plugin's journal script pulled a lever
and started a quest through two such properties, and both stayed None
(Papyrus log "Cannot call Start() on a None object").

Only the edited script's entry in the record's VMAD is replaced, by this
run's; every other script the record carries stays as the master bound it.
A record the plugin overrides gets the entry on its override, and one the
plugin leaves alone is emitted as an override of the master's bytes with only
that entry changed.  A quest keeps its own path
(`pipeline_records._rebind_edited_script`).

See: docs/commentary/tes5_import_override.md#edited-master-script-rebinds
"""

import struct
from collections import Counter

from ..base.text_reader import remap_formid
from .builder import replace_vmad, split_subrecords
from .nested import DELETED_FLAG

#: Payload size of each fixed-size VMAD property type: object, int, float, bool.
_FIXED_SIZE = {1: 8, 3: 4, 4: 4, 5: 1}

#: Item size of each fixed-size VMAD array property type.
_ARRAY_ITEM = {11: 8, 13: 4, 14: 4, 15: 1}

#: VMAD property type of a string, and of an array of strings.
_STRING, _STRING_ARRAY = 2, 12


def _wstring_end(buf: bytes, pos: int) -> int:
    """Offset just past the u16-length-prefixed string at `pos`."""
    return pos + 2 + struct.unpack_from('<H', buf, pos)[0]


def _value_end(buf: bytes, pos: int, ptype: int) -> int:
    """Offset just past one property value of type `ptype` at `pos`; ValueError for a type not read here."""
    if ptype in _FIXED_SIZE:
        return pos + _FIXED_SIZE[ptype]
    if ptype == _STRING:
        return _wstring_end(buf, pos)
    count = struct.unpack_from('<I', buf, pos)[0]
    pos += 4
    if ptype in _ARRAY_ITEM:
        return pos + count * _ARRAY_ITEM[ptype]
    if ptype != _STRING_ARRAY:
        raise ValueError(f'VMAD property type {ptype}')
    for _ in range(count):
        pos = _wstring_end(buf, pos)
    return pos


def _entry_end(buf: bytes, pos: int) -> int:
    """Offset just past the script entry whose name ends at `pos` (flags, property count, properties)."""
    count = struct.unpack_from('<H', buf, pos + 1)[0]
    pos += 3
    for _ in range(count):
        pos = _wstring_end(buf, pos)
        pos = _value_end(buf, pos + 2, buf[pos])
    if pos > len(buf):
        raise ValueError('VMAD entry runs past the payload')
    return pos


def rebind_entry(vmad: bytes, name: str, entry: bytes):
    """`vmad` (a payload) with script `name`'s entry replaced by `entry`.

    None when the payload is not one this reads (version 5, object format 2)
    or holds no script of that name: the caller keeps the master's binding.
    """
    try:
        if struct.unpack_from('<hh', vmad, 0) != (5, 2):
            return None
        pos = 6
        for _ in range(struct.unpack_from('<H', vmad, 4)[0]):
            start, end = pos, _wstring_end(vmad, pos)
            pos = _entry_end(vmad, end)
            if vmad[start + 2:end].decode('latin-1').lower() == name.lower():
                return vmad[:start] + entry + vmad[pos:]
    except (struct.error, ValueError, IndexError):
        return None
    return None


def rebound_record(record: bytes, name: str, entry: bytes) -> bytes:
    """`record` with `name`'s VMAD entry replaced; b'' if none, or if unchanged."""
    vmad = next((p for sig, p in split_subrecords(record) if sig == b'VMAD'), None)
    new = rebind_entry(vmad, name, entry) if vmad else None
    return replace_vmad(record, new) if new not in (None, vmad) else b''


def edited_scripts(by_type: dict, ctx) -> dict:
    """{SCPT FormID: (Papyrus name, VMAD entry)} of each master script this plugin edits, with this run's properties."""
    from ..base.object_scripts import edited_script_entry
    out = {}
    for scpt in by_type.get('SCPT', []):
        if ctx.edits_master_script(scpt):
            name, entry = edited_script_entry(scpt.get('FormID') or '')
            if entry:
                out[scpt.get('FormID')] = (name, entry)
    return out


def rebound_override(ov, rec: dict, edited: dict, master_index) -> bytes:
    """`ov`'s record with the edited master script it runs rebound; b'' when that does not apply."""
    hit = edited.get(rec.get('SCRI') or '')
    if hit is None or ov.status not in ('emitted', 'unchanged') or rec.get('Signature') == 'QUST':
        return b''
    base = ov.record_bytes or master_index.record(ov.out_fid)
    return rebound_record(base, *hit) if base else b''


def master_rebinds(by_type: dict, ctx, xref, edited: dict, offset: int) -> tuple:
    """(records, kept) for the master records this plugin leaves alone that run a script it edits.

    `records` are the overrides to write: the master's bytes with only the
    edited script's entry replaced.  `kept` counts by signature the records
    that keep the master's binding: a quest (a quest the plugin does not
    override is not rebound), a VMAD with no readable entry for the script,
    or one the entry would leave unchanged (no override that changes nothing).
    """
    own = {rec.get('FormID') for recs in by_type.values() for rec in recs}
    records, kept = [], Counter()
    for scri, hit in sorted(edited.items()):
        for key in xref.attached_records(scri):
            master = ctx.master_export.get(key)
            if key in own or master is None or int(master.get('RecordFlags') or 0) & DELETED_FLAG:
                continue
            base = (b'' if master.get('Signature') == 'QUST'
                    else ctx.master_index.record(remap_formid(int(key, 16), offset, is_own_id=True)))
            record = rebound_record(base, *hit) if base else b''
            if record:
                records.append(record)
            else:
                kept[master.get('Signature') or '?'] += 1
    return records, kept
