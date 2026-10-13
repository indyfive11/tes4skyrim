"""Audit the attribute magic of built plugins: does every item's attribute effect act on its Skyrim value?

Reads the BUILT plugins (not the converter's functions, and none of its code)
and checks one thing: every effect entry of a spell, enchantment, potion,
ingredient or scroll that names an effect record of the five attribute
families points at a record whose archetype and actor value are the ones this
file's own tables give its family and attribute. It reads those two fields
and nothing else of a record: one whose flags are damaged passes.

An attribute effect record is identified twice, and the two must agree. By
its EditorID, ``TES4<code><Attribute>`` plus whatever a clone appended, which
gives the family and the attribute. And by its FormID in the list the build
staged for the runtime beside the plugin,
``SKSE/Plugins/MorrowindRuntime/<plugin stem>/teleports_formid.txt``, whose
rows ``plugin|FormID=index:attribute`` give the same two. A record only one
of them names, or one they name differently, fails the run: the list says
which records there must be, so a renamed record does not drop out of the
count. A Morrowind attribute effect the list names (``TES4MW<index>...``) is
left to the runtime and counted, not checked. Every family and attribute in
which a plugin's list names records must have an item entry pointing at one
of them.

It prints, per family and attribute, the effect records and the item entries
it found and how many are on their table value, then every other form it met
and what the two identifications agree on.

Usage, from the repo root; plugins in load order, a master before its dependents:
    python -m extensions.direct_attribute_magic.audit <built plugin>...
        [--other-master <file name>]...

Every master an item's effect entry points into must be among the plugins
given, or be named with --other-master as one the conversion did not build
(Skyrim.esm): a file NAME, not a path. Its effect records are not read, and
no entry into it is taken for an attribute effect.

Exit 0: every attribute entry and record is on its table value, and the
EditorIDs and the lists name the same records. Exit 1: a record or an entry
is not on its table value, or the two identifications disagree. Exit 2, it
cannot tell: an entry points into a plugin that was neither given nor named,
no attribute entry was found at all, a plugin holding attribute effect
records has no list beside it, or no item entry points into a family and
attribute whose records a list names.

Nothing here imports the add-on's own code: the tables and the names are a
second copy, so a mistake in one shows against the other.

See: extensions/direct_attribute_magic/README.md
"""
import argparse
import collections
import os
import re
import struct
import sys
import zlib

#: TES4 effect code -> (family name, Skyrim archetype).
FAMILIES = {'FOAT': ('Fortify', 34), 'DRAT': ('Drain', 34), 'DGAT': ('Damage', 0),
            'REAT': ('Restore', 0), 'ABAT': ('Absorb', 4)}

#: Attribute name in an EditorID -> Skyrim actor value.
ATTRIBUTES = {'Strength': 32, 'Intelligence': 25, 'Willpower': 28, 'Agility': 26,
              'Speed': 30, 'Endurance': 24, 'Personality': 17, 'Luck': 33}

#: An attribute effect record's EditorID: the variant's name, then a clone's suffix.
ATTRIBUTE_EDID = re.compile(r'TES4(%s)(%s)' % ('|'.join(FAMILIES), '|'.join(ATTRIBUTES)))

#: Where a built plugin's runtime lists lie, from the plugin's folder, and the list of its effect records.
LIST_FOLDER, LIST_NAME = ('SKSE', 'Plugins', 'MorrowindRuntime'), 'teleports_formid.txt'

#: The effect index of a list row ``plugin|FormID=index:attribute`` -> TES4 effect code.
LIST_FAMILIES = {79: 'FOAT', 17: 'DRAT', 22: 'DGAT', 74: 'REAT', 85: 'ABAT'}

#: A Morrowind attribute effect record's EditorID: its effect index, its name, the attribute, a clone's suffix.
MORROWIND_EDID = re.compile(r'TES4MW(\d{3})[A-Za-z]*?(%s)' % '|'.join(ATTRIBUTES))

ARCHETYPE_NAMES = {0: 'Value Modifier', 1: 'Script', 4: 'Absorb', 34: 'Peak Value Modifier'}
VALUE_NAMES = {-1: 'none', 17: 'Speech', 24: 'Health', 25: 'Magicka', 26: 'Stamina', 28: 'MagickaRate',
               30: 'SpeedMult', 32: 'CarryWeight', 33: 'CritChance'}

#: The record types whose effect entries are read, and the one they point at.
ITEM_TYPES = (b'SPEL', b'ENCH', b'ALCH', b'INGR', b'SCRL')
EFFECT_TYPE = b'MGEF'

#: MGEF DATA offsets of the archetype and the actor value, and the least length holding both.
O_ARCHETYPE, O_ACTOR_VALUE, DATA_MIN = 64, 68, 72

HEADER, COMPRESSED = 24, 0x00040000
OK, MISMATCH, UNDETERMINED = 0, 1, 2


# ---------------------------------------------------------------------------
# Reading a built plugin
# ---------------------------------------------------------------------------

def _subrecords(body: bytes):
    """(signature, payload) of each subrecord; an XXXX gives the next one's length."""
    pos, long_size = 0, None
    while pos + 6 <= len(body):
        sig, size = body[pos:pos + 4], struct.unpack_from('<H', body, pos + 4)[0]
        pos += 6
        if sig == b'XXXX':
            long_size = struct.unpack_from('<I', body, pos)[0]
            pos += size
            continue
        size, long_size = (long_size, None) if long_size is not None else (size, None)
        yield sig, body[pos:pos + size]
        pos += size


def _record(blob: bytes, pos: int) -> tuple:
    """(FormID, subrecords) of the record whose header starts at `pos`."""
    size, flags, formid = struct.unpack_from('<III', blob, pos + 4)
    body = blob[pos + HEADER:pos + HEADER + size]
    if flags & COMPRESSED:
        body = zlib.decompress(body[4:])
    return formid, list(_subrecords(body))


def read_plugin(path: str) -> tuple:
    """(masters, {record type: [(FormID, subrecords)]}) of a built plugin's effect and item records.

    Only the top groups of EFFECT_TYPE and ITEM_TYPES are entered; every
    other group is stepped over by its length.
    """
    with open(path, 'rb') as handle:
        blob = handle.read()
    if blob[:4] != b'TES4':
        raise ValueError(f'{path} is not a plugin: it does not start with a TES4 record')
    masters = [payload.rstrip(b'\0').decode('cp1252')
               for sig, payload in _record(blob, 0)[1] if sig == b'MAST']
    found = {kind: [] for kind in (EFFECT_TYPE,) + ITEM_TYPES}
    pos = HEADER + struct.unpack_from('<I', blob, 4)[0]
    while pos + HEADER <= len(blob):
        if blob[pos:pos + 4] != b'GRUP':
            raise ValueError(f'{path}: no top group at offset {pos}')
        size, label, kind = struct.unpack_from('<I4sI', blob, pos + 4)
        if kind == 0 and label in found:
            at = pos + HEADER
            while at < pos + size:
                found[label].append(_record(blob, at))
                at += HEADER + struct.unpack_from('<I', blob, at + 4)[0]
        pos += size
    return masters, found


def list_path(plugin_path: str) -> str:
    """Where the build staged the runtime's list of a built plugin's effect records."""
    stem = os.path.splitext(os.path.basename(plugin_path))[0]
    return os.path.join(os.path.dirname(plugin_path), *LIST_FOLDER, stem, LIST_NAME)


def read_list(path: str):
    """{(plugin in lower case, local FormID): (family code, attribute name)} of a list's attribute rows.

    None when there is no file. A row is ``plugin|FormID=index:attribute``;
    one without an attribute names another runtime effect and is passed over.
    """
    if not os.path.isfile(path):
        return None
    rows, names = {}, dict(enumerate(ATTRIBUTES))
    with open(path, encoding='utf-8') as handle:
        for line in filter(None, map(str.strip, handle)):
            key, _, value = line.partition('=')
            index, colon, attribute = value.partition(':')
            if not colon:
                continue
            plugin, _, formid = key.partition('|')
            try:
                cell = LIST_FAMILIES[int(index)], names[int(attribute)]
                rows[(plugin.lower(), int(formid, 16) & 0x00FFFFFF)] = cell
            except (ValueError, KeyError):
                raise ValueError(f'{path}: not a row of an attribute effect: {line}') from None
    return rows


# ---------------------------------------------------------------------------
# The check
# ---------------------------------------------------------------------------

def _owner(formid: int, masters: list, name: str) -> tuple:
    """(owning plugin's file name in lower case, the FormID without its index byte)."""
    index = formid >> 24
    return (masters[index] if index < len(masters) else name).lower(), formid & 0x00FFFFFF


def effect_table(plugins: list) -> dict:
    """{(owner, local FormID): (EditorID, archetype, actor value)} of every effect record; a later plugin wins."""
    table = {}
    for name, masters, found in plugins:
        for formid, subs in found[EFFECT_TYPE]:
            fields = dict(subs)
            data = fields.get(b'DATA', b'')
            pair = struct.unpack_from('<Ii', data, O_ARCHETYPE) if len(data) >= DATA_MIN else (None, None)
            table[_owner(formid, masters, name)] = (fields.get(b'EDID', b'').rstrip(b'\0').decode('cp1252'),) + pair
    return table


def expected(edid: str):
    """(family code, attribute name, archetype, actor value) an EditorID calls for; None when it is no attribute effect."""
    match = ATTRIBUTE_EDID.match(edid)
    if not match:
        return None
    code, attribute = match.groups()
    return code, attribute, FAMILIES[code][1], ATTRIBUTES[attribute]


def _morrowind(edid: str):
    """The (family code, attribute name) a Morrowind EditorID stands for, or None."""
    match = MORROWIND_EDID.match(edid)
    return (LIST_FAMILIES.get(int(match.group(1))), match.group(2)) if match else None


class Tally:
    """What the audit counted, per family and attribute."""

    def __init__(self):
        """Nothing counted yet."""
        self.records = collections.Counter()
        self.records_on = collections.Counter()
        self.entries = collections.Counter()
        self.entries_on = collections.Counter()
        self.forms = collections.Counter()
        self.by_type = collections.Counter()
        self.other_entries = 0
        self.outside = collections.Counter()
        self.unknown = collections.Counter()
        self.examples = []
        self.agreed = {}
        self.morrowind = 0
        self.disagree = []
        self.unlisted = collections.Counter()
        self.empty = []

    def compare(self, table: dict, lists: dict) -> None:
        """Hold the records the EditorIDs name against the ones the runtime's `lists` name.

        `lists` is {plugin in lower case: its list's rows, None without a list}.
        """
        named = {key: expected(row[0])[:2] for key, row in table.items() if expected(row[0])}
        listed = {key: cell for rows in lists.values() for key, cell in (rows or {}).items()}
        for key in sorted(named.keys() | listed.keys()):
            edid, where = table.get(key, (None,))[0], f'{key[0]}|{key[1]:06X}'
            fault = self._settle(key, edid, named.get(key), listed.get(key), lists.get(key[0]) is not None)
            if fault:
                self.disagree.append(f'{edid} ({where}) {fault}' if edid else f'{where} {fault}')

    def _settle(self, key: tuple, edid, by_name, by_list, has_list: bool) -> str:
        """Count one record under both ways of naming it; what is wrong with it, '' for nothing.

        `by_name` and `by_list` are its (family code, attribute name) by its
        EditorID and by the list, None where that one does not name it;
        `edid` is None when the record was not read.
        """
        if by_list is None and not has_list:
            self.unlisted[key[0]] += 1
            return ''
        if by_list is None:
            return 'is in no list'
        if edid is None:
            return f'is listed as {" ".join(by_list)} and is no effect record read'
        if by_name == by_list:
            self.agreed[key] = by_list
            return ''
        if by_name is None and _morrowind(edid) == by_list:
            self.morrowind += 1
            return ''
        return f'is listed as {" ".join(by_list)}'

    def find_empty(self, used: set) -> None:
        """Note each listed (plugin, family, attribute) no entry of `used` points into."""
        filled = {(key[0],) + self.agreed[key] for key in used if key in self.agreed}
        self.empty = sorted({(key[0],) + cell for key, cell in self.agreed.items()} - filled)

    def count_record(self, row: tuple) -> None:
        """One effect record of the table: counted when its EditorID names an attribute effect."""
        want = expected(row[0])
        if want:
            self.records[want[:2]] += 1
            self.records_on[want[:2]] += row[1:] == want[2:]

    def count_entry(self, kind: str, item: str, row: tuple) -> None:
        """One item entry pointing at the effect record `row`."""
        want = expected(row[0])
        if not want:
            self.other_entries += 1
            return
        on = row[1:] == want[2:]
        self.entries[want[:2]] += 1
        self.entries_on[want[:2]] += on
        self.by_type[kind] += 1
        self.forms[(want[0], row[1], row[2], on)] += 1
        if not on and len(self.examples) < 5:
            self.examples.append(f'{kind} {item} -> {row[0]}: {_form(row[1], row[2])}, '
                                 f'table {_form(want[2], want[3])}')


def _effect_entries(plugins: list):
    """(item type, item name, (owner, local FormID) of the effect) for every effect entry of every item."""
    for name, masters, found in plugins:
        for kind in ITEM_TYPES:
            for formid, subs in found[kind]:
                item = dict(subs).get(b'EDID', b'').rstrip(b'\0').decode('cp1252') or f'{formid:08X}'
                yield from ((kind.decode(), item, _owner(struct.unpack('<I', payload)[0], masters, name))
                            for sig, payload in subs if sig == b'EFID')


def audit(plugins: list, other_masters: set, lists: dict) -> Tally:
    """Count every effect record and every item entry of `plugins` against the tables and the runtime's `lists`."""
    table, tally = effect_table(plugins), Tally()
    for row in table.values():
        tally.count_record(row)
    tally.compare(table, lists)
    tally.find_empty({key for _kind, _item, key in _effect_entries(plugins)})
    for kind, item, key in _effect_entries(plugins):
        if key in table:
            tally.count_entry(kind, item, table[key])
        elif key[0] in other_masters:
            tally.outside[key[0]] += 1
        else:
            tally.unknown[key[0]] += 1
    return tally


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def _form(archetype, value) -> str:
    """An archetype and actor value in words."""
    return (f'{ARCHETYPE_NAMES.get(archetype, f"archetype {archetype}")} on '
            f'{VALUE_NAMES.get(value, f"actor value {value}")}')


def _print_counts(tally: Tally) -> tuple:
    """Print the counts; (attribute entries, records off their pair, entries off their pair)."""
    print('family   attribute      table value                         records  on table   entries  on table')
    for code, (family, archetype) in FAMILIES.items():
        for attribute, value in ATTRIBUTES.items():
            key = (code, attribute)
            if tally.records[key] or tally.entries[key]:
                print(f'{family:8} {attribute:14} {_form(archetype, value):34} {tally.records[key]:8} '
                      f'{tally.records_on[key]:9} {tally.entries[key]:9} {tally.entries_on[key]:9}')
    records, entries = sum(tally.records.values()), sum(tally.entries.values())
    records_off = records - sum(tally.records_on.values())
    entries_off = entries - sum(tally.entries_on.values())
    print(f'attribute effect records: {records}, NOT on their table value: {records_off}')
    print(f'attribute effect entries: {entries} ({dict(sorted(tally.by_type.items()))}), '
          f'NOT on their table value: {entries_off}')
    print(f'other effect entries read (no attribute effect): {tally.other_entries}; '
          f'entries into masters named as not built by the conversion: {dict(sorted(tally.outside.items()))}')
    print('forms the attribute entries point at:')
    for (code, archetype, value, on), count in sorted(tally.forms.items(), key=str):
        print(f'  {FAMILIES[code][0]:8} {_form(archetype, value):34} {count:6}  {"on table" if on else "NOT on table"}')
    for line in tally.examples:
        print(f'  e.g. {line}')
    print(f'attribute effect records the EditorIDs and the runtime\'s lists name alike: {len(tally.agreed)}; '
          f'Morrowind ones the lists name (the runtime\'s, not checked): {tally.morrowind}; '
          f'named differently or by one alone: {len(tally.disagree)}')
    for line in tally.disagree[:5]:
        print(f'  e.g. {line}')
    return entries, records_off, entries_off


def report(tally: Tally) -> int:
    """Print the counts and the verdict; the exit code."""
    entries, records_off, entries_off = _print_counts(tally)
    if tally.unknown:
        print(f'UNDETERMINED: effect entries point into plugins neither given nor named with --other-master: '
              f'{dict(sorted(tally.unknown.items()))}')
        return UNDETERMINED
    if not entries:
        print('UNDETERMINED: no attribute effect entry was found, so nothing was checked')
        return UNDETERMINED
    if records_off or entries_off:
        print('MISMATCH: attribute effects that do not act on their table value (see above)')
        return MISMATCH
    if tally.disagree:
        print('MISMATCH: the EditorIDs and the runtime\'s lists name different attribute effect records (see above)')
        return MISMATCH
    if tally.unlisted:
        print(f'UNDETERMINED: attribute effect records of plugins with no {LIST_NAME} beside them, so their '
              f'records are identified once only: {dict(sorted(tally.unlisted.items()))}')
        return UNDETERMINED
    if tally.empty:
        print(f'UNDETERMINED: no item entry points at a record of {len(tally.empty)} listed (plugin, family, '
              f'attribute): {[" ".join(cell) for cell in tally.empty[:5]]}')
        return UNDETERMINED
    print('OK: every attribute effect record and entry is on its table value, and the lists name the same records')
    return OK


def main(argv=None) -> int:
    """Audit the plugins named on the command line; the exit code."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('plugins', nargs='+', help='built plugins, in load order')
    parser.add_argument('--other-master', action='append', default=[], metavar='NAME',
                        help='file NAME, not a path, of a master the conversion did not build (Skyrim.esm); '
                             'its records are not read')
    args = parser.parse_args(argv)
    plugins, lists = [], {}
    for path in args.plugins:
        masters, found = read_plugin(path)
        name, rows = os.path.basename(path), read_list(list_path(path))
        plugins.append((name, masters, found))
        lists[name.lower()] = rows
        print(f'{name}: {len(found[EFFECT_TYPE])} effect records, '
              f'{sum(len(found[kind]) for kind in ITEM_TYPES)} items, '
              f'{"no list" if rows is None else f"{len(rows)} attribute effects listed"}')
    return report(audit(plugins, {name.lower() for name in args.other_master}, lists))


if __name__ == '__main__':
    sys.exit(main())
