"""A dependent plugin's edits of a master's scripts reach the master's records and placements.

Two shapes, one cause: the import planned scripts only for the plugin's own
records.  A master record running a script the plugin edits kept the master's
property list, so the edit's new object properties bound to nothing; and a
placement of a master's base whose script reads its enable parent got no
linked ref, or kept the master's old one when the plugin moved its parent.

See: docs/commentary/tes5_import_override.md#edited-master-script-rebinds
See: docs/commentary/tes5_import_override.md#master-bases-keep-their-script-flags
"""
import struct
from collections import Counter
from types import SimpleNamespace

from script_convert.cross_ref import CrossRefGraph
from script_convert.pipeline import append_vmad_object_script, build_vmad_object_script
from tes5_import import pipeline_records as pr
from tes5_import.base import object_scripts
from tes5_import.base.writer import pack_record, pack_subrecord
from tes5_import.overrides import script_rebind as sr
from tes5_import.overrides.builder import generic_substitutions, split_subrecords
from tes5_import.overrides.nested import Override
from tes5_import.record_types.world import convert_REFR

OLD = build_vmad_object_script('TES4_Journal', {'Marker': 0x01000001})
NEW_ENTRY = build_vmad_object_script('TES4_Journal', {'Marker': 0x01000001, 'Lever': 0x03000002})[6:]


def _two_scripts() -> bytes:
    """A two-script VMAD whose first script holds every simple property type."""
    first = build_vmad_object_script('TES4_Other', {'Ref': 0x01000009},
                                     {'n': ('int', 3), 'f': ('float', 1.5), 'b': ('bool', True)})
    return append_vmad_object_script(first, 'TES4_Journal', {'Marker': 0x01000001})


def _record(vmad: bytes = None, sig: str = 'BOOK', fid: int = 0x01000D56) -> bytes:
    """A packed record with an EDID and, when given, a VMAD."""
    subs = pack_subrecord('EDID', b'Journal\0')
    if vmad is not None:
        subs += pack_subrecord('VMAD', vmad)
    return pack_record(sig, fid, 0, subs + pack_subrecord('FULL', b'x\0'))


def _vmad_of(record: bytes) -> bytes:
    """The record's VMAD payload."""
    return dict(split_subrecords(record))[b'VMAD']


class TestRebindEntry:

    def test_only_the_named_script_is_replaced(self):
        """The other script and every property it holds stay as the master bound them."""
        old = _two_scripts()
        out = sr.rebind_entry(old, 'tes4_journal', NEW_ENTRY)
        keep = old[6:old.index(b'TES4_Journal') - 2]
        assert out[6:6 + len(keep)] == keep
        assert out.endswith(NEW_ENTRY)
        assert struct.unpack_from('<H', out, 4)[0] == 2

    def test_a_single_script_vmad(self):
        """The common case: the entry is the whole body."""
        assert sr.rebind_entry(OLD, 'TES4_Journal', NEW_ENTRY) == OLD[:6] + NEW_ENTRY

    def test_unreadable_or_absent_keeps_the_master(self):
        """None tells the caller to keep the master's binding."""
        assert sr.rebind_entry(OLD, 'TES4_Elsewhere', NEW_ENTRY) is None
        assert sr.rebind_entry(b'\x04\x00' + OLD[2:], 'TES4_Journal', NEW_ENTRY) is None
        assert sr.rebind_entry(OLD[:-3], 'TES4_Journal', NEW_ENTRY) is None

    def test_an_unknown_property_type_keeps_the_master(self):
        """A type this does not walk cannot be skipped safely."""
        bad = bytearray(OLD)
        bad[bad.index(b'Marker') + 6] = 7
        assert sr.rebind_entry(bytes(bad), 'TES4_Journal', NEW_ENTRY) is None


class TestRebindRecords:

    def test_rebound_record(self):
        """Only the VMAD changes; a record without one is not invented a binding."""
        out = sr.rebound_record(_record(OLD), 'TES4_Journal', NEW_ENTRY)
        assert _vmad_of(out) == OLD[:6] + NEW_ENTRY
        assert [s for s, _ in split_subrecords(out)] == [b'EDID', b'VMAD', b'FULL']
        assert sr.rebound_record(_record(), 'TES4_Journal', NEW_ENTRY) == b''

    def test_an_override_takes_the_entry_even_when_nothing_else_changed(self):
        """An unchanged override of a record running an edited script still ships."""
        index = SimpleNamespace(record=lambda fid: _record(OLD))
        edited = {'01000D58': ('TES4_Journal', NEW_ENTRY)}
        rec = {'Signature': 'BOOK', 'SCRI': '01000D58'}
        out = sr.rebound_override(Override('unchanged', 0x01000D56, b''), rec, edited, index)
        assert _vmad_of(out) == OLD[:6] + NEW_ENTRY
        assert sr.rebound_override(Override('deleted', 1, b'x'), rec, edited, index) == b''
        assert sr.rebound_override(Override('emitted', 1, b'x'), dict(rec, SCRI='0100AAAA'),
                                   edited, index) == b''
        assert sr.rebound_override(Override('emitted', 1, b'x'), dict(rec, Signature='QUST'),
                                   edited, index) == b''

    def test_master_records_the_plugin_leaves_alone(self):
        """Each is rebound once; the plugin's own, a deleted one and a quest are not."""
        master_export = {
            '01000D56': {'Signature': 'BOOK'}, '01000D57': {'Signature': 'BOOK'},
            '01000D59': {'Signature': 'BOOK', 'RecordFlags': '32'},
            '01000D5A': {'Signature': 'QUST'}, '01000D5B': {'Signature': 'ACTI'}}
        index = {0x01000D56: _record(OLD), 0x01000D5B: _record(build_vmad_object_script('TES4_X'))}
        ctx = SimpleNamespace(master_export=master_export,
                              master_index=SimpleNamespace(record=lambda fid: index.get(fid, b'')))
        xref = SimpleNamespace(attached_records=lambda scri: list(master_export))
        by_type = {'BOOK': [{'FormID': '01000D57'}]}
        records, kept = sr.master_rebinds(by_type, ctx, xref, {'01000D58': ('TES4_Journal', NEW_ENTRY)}, 0)
        assert [_vmad_of(r) for r in records] == [OLD[:6] + NEW_ENTRY]
        assert kept == Counter({'QUST': 1, 'ACTI': 1})


class TestMasterBasesKeepTheirScriptFlags:
    """A placement reads its BASE's script: a master's base must be flagged too."""

    PAD = '01008001'

    def setup_method(self):
        """A master pad base whose script reads its enable parent, and one whose script does not."""
        object_scripts._GETPARENTREF_BASES.clear()
        object_scripts._PLAN_CTX['scpt_by_fid'] = {
            '01000D17': ('PadScript', 'scn PadScript\nset t to getParentRef\n', 'ObjectReference', frozenset()),
            '01000D18': ('LampScript', 'scn LampScript\nset t to 1\n', 'ObjectReference', frozenset())}
        master_export = {self.PAD: {'Signature': 'ACTI'}, '01008002': {'Signature': 'ACTI'}}
        xref = SimpleNamespace(record_scri={self.PAD: '01000D17', '01008002': '01000D18'})
        object_scripts._note_master_bases(xref, master_export)

    def teardown_method(self):
        """Leave no plan state for the next test."""
        object_scripts._GETPARENTREF_BASES.clear()
        object_scripts._PLAN_CTX.clear()

    @staticmethod
    def _pad(parent: str, base: str = PAD) -> dict:
        """A placed pad with an enable parent."""
        return {'Signature': 'REFR', 'FormID': '0100EFB9', 'RecordFlags': '1024', 'NAME': base,
                'XESP.Reference': parent, 'XESP.Flags': '0', 'PosX': '0', 'PosY': '0', 'PosZ': '0',
                'RotX': '0', 'RotY': '0', 'RotZ': '0'}

    def test_only_the_base_whose_script_reads_its_parent(self):
        """The other master base keeps no flag."""
        assert object_scripts.base_uses_parent_ref(self.PAD)
        assert not object_scripts.base_uses_parent_ref('01008002')

    def test_a_new_placement_links_its_parent(self):
        """A plugin's new pad in a master's chain."""
        out = dict(split_subrecords(convert_REFR(self._pad('0100EFBE'))))
        assert struct.unpack('<II', out[b'XLKR'])[1] == struct.unpack('<II', out[b'XESP'])[0]

    def test_a_moved_parent_moves_the_link(self):
        """An override that re-parents a master's pad substitutes the link as well."""
        subs = generic_substitutions(self._pad('03013B54'), self._pad('0100EFBC'))
        assert set(subs) == {b'XESP', b'XLKR'}
        assert struct.unpack('<II', subs[b'XLKR'][0])[1] == struct.unpack('<II', subs[b'XESP'][0])[0]


class TestThroughTheEntryPoints:
    """The plan builder and phase 1 reach the masters' records themselves."""

    def teardown_method(self):
        """Leave no plan state for the next test."""
        object_scripts._GETPARENTREF_BASES.clear()
        object_scripts._OBJECT_VMAD.clear()
        object_scripts._PLAN_CTX.clear()

    def test_the_plan_flags_a_masters_base(self):
        """A plugin with no scripted records of its own still flags the master's pad."""
        xref = CrossRefGraph()
        xref.record_scri = {'01008001': '01000D17'}
        master_export = {
            '01000D17': {'Signature': 'SCPT', 'EditorID': 'PadScript', 'SCTX': 'scn P\nset t to getParentRef\n'},
            '01008001': {'Signature': 'ACTI', 'SCRI': '01000D17'}}
        object_scripts.build_object_script_plan({}, xref, {}, master_export)
        assert object_scripts.base_uses_parent_ref('01008001')

    def test_phase_one_writes_the_rebound_records(self):
        """Every rebound master record goes to the writer; nothing when no master script is edited."""
        written = []
        writer = SimpleNamespace(add_record=lambda sig, data: written.append((sig, data)))
        master_export = {'01000D58': {'Signature': 'SCPT', 'SCTX': 'scn A'},
                         '01000D56': {'Signature': 'BOOK'}}
        ctx = SimpleNamespace(master_export=master_export,
                              master_index=SimpleNamespace(record=lambda fid: _record(OLD)),
                              edits_master_script=lambda scpt: scpt['SCTX'] != 'scn A')
        xref = SimpleNamespace(attached_records=lambda scri: ['01000D56'])
        object_scripts._PLAN_CTX.update(
            scpt_by_fid={'01000D58': ('Journal', 'scn A\nx', 'ObjectReference', frozenset())},
            props_memo={'01000D58': {'Marker': 0x01000001, 'Lever': 0x03000002}})
        st = SimpleNamespace(ctx=ctx, xref=xref, writer=writer, converted=0, edited_scripts=None,
                             by_type={'SCPT': [{'FormID': '01000D58', 'SCTX': 'scn A\nx'}]})
        pr._emit_master_rebinds(st)
        assert [(sig, _vmad_of(data)) for sig, data in written] == [('BOOK', OLD[:6] + NEW_ENTRY)]
        assert st.converted == 1
        st.by_type['SCPT'][0]['SCTX'], st.edited_scripts, written[:] = 'scn A', None, []
        pr._emit_master_rebinds(st)
        assert written == []


def _wstr(text: str) -> bytes:
    """A u16-length-prefixed VMAD string."""
    return struct.pack('<H', len(text)) + text.encode('ascii')


class TestReviewCases:
    """The cases an independent review found untested or wrong."""

    def teardown_method(self):
        """Leave no plan state for the next test."""
        object_scripts._PLAN_CTX.clear()

    def test_an_entry_that_changes_nothing_writes_nothing(self):
        """An override identical to its master would only revert another plugin's edit of it."""
        assert sr.rebound_record(_record(OLD), 'TES4_Journal', OLD[6:]) == b''
        ctx = SimpleNamespace(master_export={'01000D56': {'Signature': 'BOOK'}},
                              master_index=SimpleNamespace(record=lambda fid: _record(OLD)))
        xref = SimpleNamespace(attached_records=lambda scri: ['01000D56'])
        records, kept = sr.master_rebinds({}, ctx, xref, {'01000D58': ('TES4_Journal', OLD[6:])}, 0)
        assert records == [] and kept == Counter({'BOOK': 1})

    def test_the_masters_id_is_shifted_into_this_plugins_space(self):
        """Real runs prepend masters, so the master's record sits at a shifted index."""
        ctx = SimpleNamespace(master_export={'01000D56': {'Signature': 'BOOK'}},
                              master_index=SimpleNamespace(record={0x02000D56: _record(OLD)}.get))
        xref = SimpleNamespace(attached_records=lambda scri: ['01000D56'])
        records, _kept = sr.master_rebinds({}, ctx, xref, {'01000D58': ('TES4_Journal', NEW_ENTRY)}, 1)
        assert [_vmad_of(r) for r in records] == [OLD[:6] + NEW_ENTRY]

    def test_phase_one_ships_an_unchanged_override_with_the_rebound_entry(self):
        """`_emit_override` writes the rebind; a record running an unedited script is left as it was."""
        written = []
        ctx = SimpleNamespace(master_index=SimpleNamespace(record=lambda fid: _record(OLD)),
                              build_outfit_companion=lambda rec: b'', renamed_copies=lambda rec: [])
        st = SimpleNamespace(ctx=ctx, converted=0, edited_scripts={'01000D58': ('TES4_Journal', NEW_ENTRY)},
                             writer=SimpleNamespace(add_record=lambda sig, data: written.append(data)))
        pr._emit_override(st, Override('unchanged', 0x01000D56, b''), {'Signature': 'BOOK', 'SCRI': '01000D58'})
        assert [_vmad_of(r) for r in written] == [OLD[:6] + NEW_ENTRY]
        pr._emit_override(st, Override('unchanged', 0x01000D56, b''), {'Signature': 'BOOK', 'SCRI': '01000D59'})
        assert len(written) == 1

    def test_array_and_string_properties_are_walked(self):
        """A first script holding every array type still leaves the second one findable."""
        props = [(b'\x0b', struct.pack('<I', 2) + bytes(16)), (b'\x0c', struct.pack('<I', 2) + _wstr('a') + _wstr('bc')),
                 (b'\x0d', struct.pack('<I', 1) + bytes(4)), (b'\x0e', struct.pack('<I', 1) + bytes(4)),
                 (b'\x0f', struct.pack('<I', 3) + bytes(3)), (b'\x02', _wstr('text'))]
        first = _wstr('TES4_Other') + b'\0' + struct.pack('<H', len(props)) + b''.join(
            _wstr(f'p{i}') + ptype + b'\x01' + data for i, (ptype, data) in enumerate(props))
        vmad = struct.pack('<hhH', 5, 2, 2) + first + OLD[6:]
        assert sr.rebind_entry(vmad, 'TES4_Journal', NEW_ENTRY) == vmad[:6] + first + NEW_ENTRY

    def test_a_script_whose_properties_failed_to_resolve_is_not_rebound(self):
        """An empty entry would unbind what the master bound."""
        object_scripts._PLAN_CTX.update(
            scpt_by_fid={'01000D58': ('Journal', 'scn A', 'ObjectReference', frozenset())},
            props_memo={'01000D58': {}}, props_failed={'01000D58'})
        assert object_scripts.edited_script_entry('01000D58') == ('', b'')
