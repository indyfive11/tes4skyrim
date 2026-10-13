"""GetItemCount of an item the receiver's base record lists: the authored-count read.

See docs/commentary/script_convert.md#getitemcount-before-the-inventory-is-built.
"""

import itertools
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from script_convert import authored_items
from script_convert.constants import PLAYER_ALIAS_EXTENDS
from script_convert.converter import ScriptConverter
from script_convert.cross_ref import CrossRefGraph, _new_scan_out, _scan_record_lines
from script_convert.emit.dispatch import emit_command
from script_convert.tes4 import nodes as N
from script_convert.tes4.parser import Mode, parse
from tes5_import.pipeline import _index_xref_record

_POLYFILL = (Path(__file__).resolve().parent.parent / 'script_convert'
             / 'static_scripts' / 'TES4Polyfill.psc')

#: The read every test of a placed container converts, and what it was before.
_NEW = 'TES4Polyfill.AuthoredItemCount(DropBoxRef, Orders, 1)'
_OLD = 'DropBoxRef.GetItemCount(Orders)'

#: The same two for a script's own reference.
_OWN_NEW = 'TES4Polyfill.AuthoredItemCount(Self, Orders, 1)'
_OWN_OLD = 'GetItemCount(Orders)'


def _rec(sig, fid, edid, items=None, **fields):
    """One export record as text; `items` is its item list as (FormID, count) pairs."""
    if items is not None:
        fields['ItemCount'] = len(items)
        for i, (item, count) in enumerate(items):
            fields.update({f'Item[{i}].FormID': item, f'Item[{i}].Count': count})
    extra = ''.join(f'{k}={v}\n' for k, v in fields.items())
    return (f'---RECORD_BEGIN---\nSignature={sig}\nFormID={fid}\n'
            f'EditorID={edid}\n{extra}---RECORD_END---\n')


def _script(fid, edid):
    """An object script record: only its name and kind matter here."""
    return _rec('SCPT', fid, edid, **{'SCHR.Type': 0})


#: Export files of one plugin: items, scripts, the records listing items, their placements.
_WORLD = {
    'MISC.txt': (_rec('MISC', '0000000F', 'Gold001') + _rec('MISC', '0000B002', 'Hammer')
                 + _rec('MISC', '0000B003', 'Trinket')),
    'BOOK.txt': _rec('BOOK', '0000B001', 'Orders'),
    'LVLI.txt': _rec('LVLI', '0000B010', 'LootList', EntryCount=1,
                     **{'Entry[0].FormID': '0000B001', 'Entry[0].Count': 1}),
    'SCPT.txt': ''.join(_script(f'0000D00{i}', name) for i, name in enumerate(
        ('DropScript', 'TwinScript', 'OddScript', 'CourierScript', 'MixedScript',
         'PlayerScript', 'SpawnScript'), 1)),
    'CONT.txt': (
        _rec('CONT', '0000C001', 'DropBox', [('0000000F', 500), ('0000B001', 1)], SCRI='0000D001')
        + _rec('CONT', '0000C002', 'TwinBoxA', [('0000B002', 4)], SCRI='0000D002')
        + _rec('CONT', '0000C003', 'TwinBoxB', [('0000000F', 9), ('0000B002', 4)], SCRI='0000D002')
        + _rec('CONT', '0000C004', 'OddBoxA', [('0000B002', 1)], SCRI='0000D003')
        + _rec('CONT', '0000C005', 'OddBoxB', [('0000B002', 2)], SCRI='0000D003')
        + _rec('CONT', '0000C006', 'LeveledBox', [('0000B010', 1)])
        + _rec('CONT', '0000C007', 'ZeroBox', [('0000B001', 0)])
        + _rec('CONT', '0000C008', 'StockBox', [('0000B001', -5)])
        + _rec('CONT', '0000C009', 'DoubleBox', [('0000B001', 1), ('0000B001', 2)])
        + _rec('CONT', '0000C00A', 'MixedBox', [('0000B002', 1)], SCRI='0000D005')),
    'ACTI.txt': _rec('ACTI', '0000C00B', 'MixedSwitch', SCRI='0000D005'),
    'NPC_.txt': (_rec('NPC_', '0000A001', 'Courier', [('0000B001', 3)], SCRI='0000D004')
                 + _rec('NPC_', '00000007', 'Player', [('0000B001', 1)], SCRI='0000D006')),
    'CREA.txt': _rec('CREA', '0000A002', 'SpawnTemplate', [('0000B002', 2)], SCRI='0000D007'),
    'LVLC.txt': _rec('LVLC', '0000B020', 'SpawnList', **{'TNAM.Template': '0000A002'}),
    'REFR.txt': (''.join(_rec('REFR', f'0000E00{n}', f'{name}Ref', NAME=f'0000C00{n}')
                         for n, name in ((1, 'DropBox'), (6, 'LeveledBox'), (7, 'ZeroBox'),
                                         (8, 'StockBox'), (9, 'DoubleBox')))
                 + _rec('REFR', '0000E011', 'DropBoxTwinRef', NAME='0000C001')
                 + _rec('REFR', '0000E012', 'OrdersRef', NAME='0000B001')),
    'ACHR.txt': (_rec('ACHR', '0000E010', 'CourierRef', NAME='0000A001')
                 + _rec('ACHR', '00000014', 'PlayerRef', NAME='00000007')),
    'ACRE.txt': _rec('ACRE', '0000E013', 'SpawnTemplateRef', NAME='0000A002'),
}


def _export(folder, files, masters=()):
    """Write one plugin's export folder and return its path."""
    folder.mkdir(parents=True)
    for name, text in files.items():
        (folder / name).write_text(text, encoding='utf-8')
    (folder / '_HEADER.txt').write_text(
        ''.join(f'Master[{i}]={m}\n' for i, m in enumerate(masters)), encoding='utf-8')
    return str(folder)


def _load(export_dir):
    """The graph of one export folder, scanned in this process."""
    graph = CrossRefGraph()
    graph.load_from_export(export_dir, workers=1)
    return graph


@pytest.fixture(scope='module')
def xref(tmp_path_factory):
    """The graph of `_WORLD`."""
    return _load(_export(tmp_path_factory.mktemp('items') / 'World.esm', _WORLD))


def _convert(graph, source, extends='ObjectReference', edid='T'):
    """One standalone script converted, as stripped lines."""
    text = ScriptConverter(graph).convert_standalone(edid, source, extends, edid)
    return [line.strip() for line in text.split('\n')]


def _read(graph, call, extends='ObjectReference', edid='T', declare='', also=''):
    """What `set n to <call>` becomes in the poll of a script named `edid`; `also` follows it."""
    source = f'scn {edid}\nshort n\n{declare}begin GameMode\nset n to {call}\n{also}end\n'
    found = [line[4:] for line in _convert(graph, source, extends, edid)
             if line.startswith('n = ')]
    assert len(found) == 1, found
    return found[0]


def _result(conv, call, also='', extends='Quest'):
    """What `set n to <call>` becomes in a stage or dialogue result; `also` follows it."""
    lines = conv.convert_fragment(f'short n\nset n to {call}\n{also}', extends)
    found = [line.strip()[4:] for line in lines if line.strip().startswith('n = ')]
    assert len(found) == 1, lines
    return found[0]


class TestPlacedReceiver:
    """A receiver named by a placed reference's EditorID."""

    def test_a_listed_item_reads_through_the_authored_count(self, xref):
        """The base record of the reference lists the item once: the count rides along."""
        assert _read(xref, 'DropBoxRef.GetItemCount Orders') == _NEW

    def test_the_count_is_the_one_the_base_record_lists(self, xref):
        """Each item carries its own count, of a container and of an actor."""
        assert _read(xref, 'DropBoxRef.GetItemCount Gold001') == \
            'TES4Polyfill.AuthoredItemCount(DropBoxRef, Gold001, 500)'
        assert _read(xref, 'CourierRef.GetItemCount Orders') == \
            'TES4Polyfill.AuthoredItemCount(CourierRef, Orders, 3)'

    def test_a_condition_keeps_its_comparison(self, xref):
        """The read is an operand like the plain call was."""
        lines = _convert(xref, 'scn T\nshort n\nbegin GameMode\n'
                               'if DropBoxRef.GetItemCount Orders < 1\nset n to 1\nendif\nend\n')
        assert f'If {_NEW} < 1' in lines

    def test_only_the_call_text_differs_from_the_plain_read(self, xref, tmp_path):
        """Properties, types and every other line are those of the plain call."""
        source = ('scn T\nshort n\nfloat f\nbegin GameMode\nset n to DropBoxRef.GetItemCount Orders\n'
                  'set f to DropBoxRef.GetItemCount Orders\nend\n')
        plain = _load(_export(tmp_path / 'World.esm', _WORLD))
        plain.record_items.clear()
        new, old = _convert(xref, source), _convert(plain, source)
        assert f'n = {_NEW}' in new and f'f = {_NEW}' in new
        assert [line.replace(_NEW, 'DropBoxRef.GetItemCount(Orders)') for line in new] == old

    def test_an_item_the_base_does_not_list_is_plain(self, xref):
        """Nothing is known about an item the base record does not hold."""
        assert _read(xref, 'DropBoxRef.GetItemCount Trinket') == 'DropBoxRef.GetItemCount(Trinket)'
        assert _read(xref, 'CourierRef.GetItemCount Hammer') == 'CourierRef.GetItemCount(Hammer)'

    def test_an_item_inside_a_leveled_list_is_plain(self, xref):
        """The base lists the leveled list, not the item it may yield."""
        assert _read(xref, 'LeveledBoxRef.GetItemCount Orders') == \
            'LeveledBoxRef.GetItemCount(Orders)'

    def test_a_leveled_list_named_as_the_item_is_plain(self, xref):
        """The base lists the leveled list once, but a list is not an item with a count."""
        assert xref.record_items['0000C006'] == (('0000B010', 1),)
        assert _read(xref, 'LeveledBoxRef.GetItemCount LootList') == \
            'LeveledBoxRef.GetItemCount(LootList)'

    def test_a_placed_reference_named_as_the_item_is_plain(self, xref):
        """A placed copy of the book is a reference, not the book the container lists."""
        assert xref.record_base['0000E012'] == '0000B001'
        assert _read(xref, 'DropBoxRef.GetItemCount OrdersRef') == \
            'DropBoxRef.GetItemCount(OrdersRef)'

    def test_a_count_of_zero_or_below_is_plain(self, xref):
        """Zero and a restocking (negative) count say nothing about the contents."""
        assert _read(xref, 'ZeroBoxRef.GetItemCount Orders') == 'ZeroBoxRef.GetItemCount(Orders)'
        assert _read(xref, 'StockBoxRef.GetItemCount Orders') == 'StockBoxRef.GetItemCount(Orders)'

    def test_an_item_listed_twice_is_plain(self, xref):
        """Two entries of one item are not added up."""
        assert _read(xref, 'DoubleBoxRef.GetItemCount Orders') == \
            'DoubleBoxRef.GetItemCount(Orders)'

    def test_the_player_is_plain(self, xref):
        """The player's inventory always exists, under either spelling."""
        assert _read(xref, 'player.GetItemCount Orders') == 'Game.GetPlayer().GetItemCount(Orders)'
        assert _read(xref, 'PlayerRef.GetItemCount Orders') == \
            'Game.GetPlayer().GetItemCount(Orders)'

    def test_a_reference_variable_is_plain(self, xref):
        """A variable holds any reference, even one named like a placed container."""
        assert _read(xref, 'box.GetItemCount Orders', declare='ref box\n') == \
            'box.GetItemCount(Orders)'
        assert _read(xref, 'DropBoxRef.GetItemCount Orders', declare='ref DropBoxRef\n') == \
            'DropBoxRef.GetItemCount(Orders)'

    def test_an_item_variable_is_plain(self, xref):
        """A variable holds any item, even one named like a listed record."""
        assert _read(xref, 'DropBoxRef.GetItemCount Orders', declare='ref Orders\n') == \
            'DropBoxRef.GetItemCount(Orders)'
        assert _read(xref, 'DropBoxRef.GetItemCount Holder.Orders') == \
            'DropBoxRef.GetItemCount(Holder.Orders)'

    def test_either_variable_table_hides_a_record(self, xref):
        """The converter knows a variable by its name or by its type; each alone is enough."""
        for name in ('dropboxref', 'orders'):
            named, typed = ScriptConverter(xref), ScriptConverter(xref)
            named.sc.local_vars.add(name)
            typed.sc.var_types[name] = 'ObjectReference'
            for conv in (named, typed):
                assert emit_command(conv, 'DropBoxRef', 'GetItemCount', 'ObjectReference',
                                    [N.Ident('Orders')]) == 'DropBoxRef.GetItemCount(Orders)'

    def test_a_base_record_or_an_unknown_name_is_plain(self, xref):
        """Only a placed reference leads to one base record."""
        assert _read(xref, 'DropBox.GetItemCount Orders') == 'DropBox.GetItemCount(Orders)'
        assert _read(xref, 'NoSuchRef.GetItemCount Orders') == 'NoSuchRef.GetItemCount(Orders)'

    def test_a_call_without_exactly_one_item_is_plain(self, xref):
        """No argument or a second one: the call converts as it did."""
        assert _read(xref, 'DropBoxRef.GetItemCount') == 'DropBoxRef.GetItemCount()'
        assert _read(xref, 'DropBoxRef.GetItemCount Orders 2') == \
            'DropBoxRef.GetItemCount(Orders, 2)'

    def test_quoted_names_are_the_same_names(self, xref):
        """Oblivion accepts quotes around an EditorID, on either side of the call."""
        assert _read(xref, 'DropBoxRef.GetItemCount "Orders"') == _NEW
        assert _read(xref, '"DropBoxRef".GetItemCount Orders') == _NEW

    def test_any_script_may_name_the_reference(self, xref):
        """A quest or a dialogue result reads a placed container the same way."""
        assert _read(xref, 'DropBoxRef.GetItemCount Orders', 'Quest') == _NEW
        assert _read(xref, 'DropBoxRef.GetItemCount Orders', 'TopicInfo') == _NEW


class TestOwnReference:
    """A bare call acts on the reference the script is attached to."""

    def test_one_base_record(self, xref):
        """The script sits on one container, which lists the item."""
        assert _read(xref, 'GetItemCount Orders', edid='DropScript') == \
            'TES4Polyfill.AuthoredItemCount(Self, Orders, 1)'

    def test_an_actor_script(self, xref):
        """An actor's own inventory is read the same way."""
        assert _read(xref, 'GetItemCount Orders', 'Actor', 'CourierScript') == \
            'TES4Polyfill.AuthoredItemCount(Self, Orders, 3)'

    def test_getself_is_the_own_reference(self, xref):
        """`GetSelf.` names the same reference as no receiver."""
        assert _read(xref, 'GetSelf.GetItemCount Orders', edid='DropScript') == \
            'TES4Polyfill.AuthoredItemCount(Self, Orders, 1)'

    def test_two_base_records_that_agree(self, xref):
        """Both containers carrying the script list four of the item."""
        assert _read(xref, 'GetItemCount Hammer', edid='TwinScript') == \
            'TES4Polyfill.AuthoredItemCount(Self, Hammer, 4)'

    def test_two_base_records_that_disagree(self, xref):
        """One lists one and the other two: the count of this reference is not known."""
        assert _read(xref, 'GetItemCount Hammer', edid='OddScript') == 'GetItemCount(Hammer)'

    def test_a_base_record_without_an_item_list_disagrees(self, xref):
        """A container and an activator share the script: the activator lists nothing."""
        assert _read(xref, 'GetItemCount Hammer', edid='MixedScript') == 'GetItemCount(Hammer)'

    def test_a_script_on_no_record_is_plain(self, xref):
        """Without a record carrying the script there is no base to ask."""
        assert _read(xref, 'GetItemCount Orders') == 'GetItemCount(Orders)'

    def test_a_script_that_is_no_reference_is_plain(self, xref):
        """A quest has no inventory of its own, whatever record names the script."""
        assert _read(xref, 'GetItemCount Orders', 'Quest', 'DropScript') == 'GetItemCount(Orders)'

    def test_a_magic_effect_or_a_dialogue_result_has_no_reference_of_its_own(self, xref):
        """Their bare call acts on the target or the speaker, whatever record names the script."""
        plain = {'ActiveMagicEffect': 'GetTargetActor().GetItemCount(Orders)',
                 'TopicInfo': 'akSpeakerRef.GetItemCount(Orders)', 'Quest': _OWN_OLD,
                 'ObjectReference': _OWN_NEW, 'Actor': _OWN_NEW}
        for extends, text in plain.items():
            conv = ScriptConverter(xref)
            conv.sc.edid = 'DropScript'
            assert emit_command(conv, None, 'GetItemCount', extends, [N.Ident('Orders')]) == text

    def test_a_leveled_lists_template_is_plain(self, xref):
        """Its script also runs on the list's spawn, whose items are not the template's."""
        assert xref.listed_item_count(xref.script_owner_actors('SpawnScript'), '0000B002') == 2
        assert _read(xref, 'GetItemCount Hammer', 'Actor', 'SpawnScript') == 'GetItemCount(Hammer)'

    def test_a_placed_copy_of_a_template_is_that_record(self, xref):
        """Named by its reference, a placement of the template holds what it lists."""
        assert _read(xref, 'SpawnTemplateRef.GetItemCount Hammer') == \
            'TES4Polyfill.AuthoredItemCount(SpawnTemplateRef, Hammer, 2)'

    def test_a_script_on_the_player_is_plain(self, xref):
        """The player's script runs on an alias, and the player is never asked."""
        assert 'AuthoredItemCount' not in _read(
            xref, 'GetItemCount Orders', PLAYER_ALIAS_EXTENDS, 'PlayerScript')

    def test_a_self_named_variable_is_a_variable(self, xref):
        """A script may call its own variable `mySelf`; it can hold any reference."""
        assert _read(xref, 'mySelf.GetItemCount Orders', edid='DropScript',
                     declare='ref mySelf\n') == 'mySelf.GetItemCount(Orders)'


class TestRemovals:
    """A script that also takes the counted item out of the same receiver keeps the plain read."""

    def test_the_same_reference_and_item(self, xref):
        """RemoveItem of the item on the reference read, in any spelling and form."""
        for removal in ('DropBoxRef.RemoveItem Orders 1', '"dropboxref".removeitem "ORDERS" 1',
                        'DropBoxRef.RemoveItemNS Orders 1', 'DropBoxRef.RemoveItem Orders'):
            assert _read(xref, 'DropBoxRef.GetItemCount Orders', also=removal + '\n') == _OLD

    def test_anywhere_in_the_script(self, xref):
        """Another block, or a condition, is the same script."""
        other_block = 'end\nbegin OnActivate\nDropBoxRef.RemoveItem Orders 1\n'
        assert _read(xref, 'DropBoxRef.GetItemCount Orders', also=other_block) == _OLD
        in_condition = 'if DropBoxRef.RemoveItem Orders 1\nendif\n'
        assert _read(xref, 'DropBoxRef.GetItemCount Orders', also=in_condition) == _OLD

    def test_everything_taken_out(self, xref):
        """RemoveAllItems takes the counted item with the rest, with or without a target."""
        for removal in ('DropBoxRef.RemoveAllItems', 'DropBoxRef.RemoveAllItems PlayerRef'):
            assert _read(xref, 'DropBoxRef.GetItemCount Orders', also=removal + '\n') == _OLD

    def test_another_item_does_not_count(self, xref):
        """Gold taken out of the box says nothing about the orders, and the other way round."""
        gold = 'DropBoxRef.RemoveItem Gold001 5\n'
        assert _read(xref, 'DropBoxRef.GetItemCount Orders', also=gold) == _NEW
        assert _read(xref, 'DropBoxRef.GetItemCount Gold001', also=gold) == \
            'DropBoxRef.GetItemCount(Gold001)'

    def test_an_item_that_cannot_be_told_counts(self, xref):
        """A variable holds any item; so may a name of no record, or another script's variable."""
        assert _read(xref, 'DropBoxRef.GetItemCount Orders', declare='ref thing\n',
                     also='DropBoxRef.RemoveItem thing 1\n') == _OLD
        assert _read(xref, 'DropBoxRef.GetItemCount Orders', declare='ref Gold001\n',
                     also='DropBoxRef.RemoveItem Gold001 1\n') == _OLD
        for item in ('NoSuchItem', 'Holder.thing'):
            assert _read(xref, 'DropBoxRef.GetItemCount Orders',
                         also=f'DropBoxRef.RemoveItem {item} 1\n') == _OLD

    def test_the_player_and_another_reference_do_not_count(self, xref):
        """Not the reference read: the player, another box, a second placement of the same box."""
        for other in ('player', 'PlayerRef', 'StockBoxRef', 'DropBoxTwinRef'):
            assert _read(xref, 'DropBoxRef.GetItemCount Orders',
                         also=f'{other}.RemoveItem Orders 1\n') == _NEW
            assert _read(xref, 'DropBoxRef.GetItemCount Orders',
                         also=f'{other}.RemoveAllItems\n') == _NEW
            assert _read(xref, 'DropBoxRef.GetItemCount Orders',
                         also=f'if {other}.RemoveAllItems\nendif\n') == _NEW

    def test_a_receiver_that_cannot_be_told_counts(self, xref):
        """A variable holds any reference, also one named like another box; an unknown name may be it."""
        assert _read(xref, 'DropBoxRef.GetItemCount Orders', declare='ref box\n',
                     also='box.RemoveItem Orders 1\n') == _OLD
        assert _read(xref, 'DropBoxRef.GetItemCount Orders', declare='ref StockBoxRef\n',
                     also='StockBoxRef.RemoveItem Orders 1\n') == _OLD
        for other in ('NoSuchRef', 'DropBox', 'Holder.box'):
            assert _read(xref, 'DropBoxRef.GetItemCount Orders',
                         also=f'{other}.RemoveItem Orders 1\n') == _OLD

    def test_the_own_reference_on_both_sides(self, xref):
        """A bare read and a bare or `GetSelf.` removal act on the same reference."""
        for removal in ('RemoveItem Orders 1', 'GetSelf.RemoveItem Orders 1', 'RemoveAllItems'):
            assert _read(xref, 'GetItemCount Orders', edid='DropScript',
                         also=removal + '\n') == _OWN_OLD
        assert _read(xref, 'GetItemCount Orders', edid='DropScript',
                     also='RemoveItem Gold001 1\n') == _OWN_NEW

    def test_the_own_reference_named_by_its_placement(self, xref):
        """A placement of a record carrying the script may be the script's own reference."""
        for own in ('DropBoxRef', 'DropBoxTwinRef'):
            assert _read(xref, 'GetItemCount Orders', edid='DropScript',
                         also=f'{own}.RemoveItem Orders 1\n') == _OWN_OLD
        for other in ('StockBoxRef', 'player', 'CourierRef'):
            assert _read(xref, 'GetItemCount Orders', edid='DropScript',
                         also=f'{other}.RemoveItem Orders 1\n') == _OWN_NEW

    def test_a_bare_removal_beside_a_named_read(self, xref):
        """The own reference may be the one named only when its record carries the script."""
        bare = 'RemoveItem Orders 1\n'
        assert _read(xref, 'DropBoxRef.GetItemCount Orders', edid='DropScript', also=bare) == _OLD
        assert _read(xref, 'DropBoxRef.GetItemCount Orders', 'Actor', 'CourierScript',
                     also=bare) == _NEW
        assert _read(xref, 'DropBoxRef.GetItemCount Orders', also=bare) == _OLD
        assert _read(xref, 'DropBoxRef.GetItemCount Orders', 'Quest', also=bare) == _OLD

    def test_a_result_script_is_a_script_of_its_own(self, xref):
        """A removal in one stage or dialogue result says nothing about the next one."""
        conv = ScriptConverter(xref)
        removal = 'DropBoxRef.RemoveItem Orders 1\n'
        assert _result(conv, 'DropBoxRef.GetItemCount Orders') == _NEW
        assert _result(conv, 'DropBoxRef.GetItemCount Orders', removal) == _OLD
        assert _result(conv, 'DropBoxRef.GetItemCount Orders') == _NEW
        assert _result(conv, 'DropBoxRef.GetItemCount Orders', 'RemoveItem Orders 1\n',
                       'TopicInfo') == _OLD

    def test_a_script_does_not_inherit_the_one_before(self, xref):
        """One converter runs every script; each starts without the last one's removals."""
        conv = ScriptConverter(xref)
        head = 'short n\nbegin GameMode\nset n to DropBoxRef.GetItemCount Orders\n'
        first = conv.convert_standalone('A', f'scn A\n{head}DropBoxRef.RemoveItem Orders 1\nend\n')
        second = conv.convert_standalone('B', f'scn B\n{head}end\n')
        assert f'n = {_OLD}' in first and f'n = {_NEW}' in second

    def test_the_removers_are_the_rows_that_convert_to_them(self):
        """Every TES4 command whose row emits RemoveItem or RemoveAllItems, and no other."""
        assert authored_items._REMOVERS == {
            'removeitem': 'RemoveItem', 'removeitemns': 'RemoveItem',
            'removeallitems': 'RemoveAllItems'}

    def test_what_a_script_takes_out(self):
        """Receiver and item as lowercase source names; '' for the own reference and for any item."""
        tree = parse('"BoxRef".RemoveItem "Orders" 1\nRemoveItem thing 2\nBoxRef.RemoveAllItems\n'
                     'RemoveAllItems\nif GetSelf.RemoveItemNS Gold001 1\nendif\n'
                     'if SackRef.RemoveAllItems\nendif\nset n to UrnRef.RemoveAllItems PlayerRef\n'
                     'BoxRef.AddItem Orders 1\nset n to BoxRef.GetItemCount Orders\n', Mode.FRAGMENT)
        assert authored_items.removals(N.walk_exprs_in(tree.body)) == {
            ('boxref', 'orders'), ('', 'thing'), ('boxref', ''), ('', ''), ('getself', 'gold001'),
            ('sackref', ''), ('urnref', '')}


class TestSourceFormat:
    """Sources whose actors take items and scripts from a template keep every read plain."""

    def test_a_fallout_export_is_plain(self, xref, tmp_path):
        """A record type only FO3/FNV has marks the export; the same world without it converts."""
        files = dict(_WORLD, **{'TERM.txt': _rec('TERM', '0000F001', 'Terminal')})
        graph = _load(_export(tmp_path / 'Waste.esm', files))
        assert graph.is_fallout_source() and not xref.is_fallout_source()
        assert graph.listed_item_count(['0000C001'], '0000B001') == 1
        assert _read(graph, 'DropBoxRef.GetItemCount Orders') == _OLD
        assert _read(graph, 'GetItemCount Orders', edid='DropScript') == _OWN_OLD
        assert _read(xref, 'DropBoxRef.GetItemCount Orders') == _NEW

    def test_the_importers_graph_knows_the_source_too(self):
        """Hand-built from records, it answers from the record types it was given."""
        plain, waste = CrossRefGraph(), CrossRefGraph()
        for graph, sig in ((plain, 'CONT'), (waste, 'TERM')):
            _index_xref_record(graph, '0000F001', {'Signature': sig, 'FormID': '0000F001'},
                               lambda v, own_raw, own_key: v)
        assert waste.is_fallout_source() and not plain.is_fallout_source()


class TestItemLists:
    """The item lists the graph keeps, from the export and from the importer."""

    def test_every_listing_record_has_a_row(self, xref):
        """Entries stay in order, a leveled list is one entry, a record without a list has none."""
        assert xref.record_items['0000C001'] == (('0000000F', 500), ('0000B001', 1))
        assert xref.record_items['0000C006'] == (('0000B010', 1),)
        assert xref.record_items['0000C009'] == (('0000B001', 1), ('0000B001', 2))
        assert '0000C00B' not in xref.record_items and '0000B010' not in xref.record_items

    def test_a_masters_reference_and_record_resolve(self, tmp_path):
        """A dependent plugin's script names its master's placed container."""
        _export(tmp_path / 'World.esm', _WORLD)
        plugin = _export(tmp_path / 'Plugin.esp', {}, masters=['World.esm'])
        assert _read(_load(plugin), 'DropBoxRef.GetItemCount Orders') == _NEW

    def test_a_plugins_emptied_copy_replaces_the_masters_list(self, tmp_path):
        """The plugin overrides the container with no items: nothing is authored any more."""
        _export(tmp_path / 'World.esm', _WORLD)
        plugin = _export(tmp_path / 'Plugin.esp', {
            'CONT.txt': _rec('CONT', '0000C001', 'DropBox', [], SCRI='0000D001')},
            masters=['World.esm'])
        graph = _load(plugin)
        assert graph.record_items['0000C001'] == ()
        assert _read(graph, 'DropBoxRef.GetItemCount Orders') == 'DropBoxRef.GetItemCount(Orders)'

    def test_a_masters_item_ids_follow_the_plugins_load_order(self, tmp_path):
        """A second master numbers its own records 01; the plugin loads it as 02."""
        _export(tmp_path / 'World.esm', _WORLD)
        _export(tmp_path / 'Other.esp', {
            'MISC.txt': _rec('MISC', '01000B01', 'OtherThing')}, masters=['World.esm'])
        _export(tmp_path / 'Addon.esp', {
            'MISC.txt': _rec('MISC', '01000B01', 'AddonCoin'),
            'CONT.txt': _rec('CONT', '01000C01', 'AddonBox',
                             [('01000B01', 7), ('0000B001', 2)]),
            'REFR.txt': _rec('REFR', '01000E01', 'AddonBoxRef', NAME='01000C01')},
            masters=['World.esm'])
        graph = _load(_export(tmp_path / 'Plugin.esp', {},
                              masters=['World.esm', 'Other.esp', 'Addon.esp']))
        assert graph.record_items['02000C01'] == (('02000B01', 7), ('0000B001', 2))
        assert _read(graph, 'AddonBoxRef.GetItemCount AddonCoin') == \
            'TES4Polyfill.AuthoredItemCount(AddonBoxRef, AddonCoin, 7)'
        assert _read(graph, 'AddonBoxRef.GetItemCount OtherThing') == \
            'AddonBoxRef.GetItemCount(OtherThing)'

    def test_a_leveled_lists_template_is_kept(self, xref, tmp_path):
        """The record a leveled creature list names as its template, in the plugin's load order."""
        assert xref.leveled_templates == {'0000A002'}
        _export(tmp_path / 'World.esm', _WORLD)
        _export(tmp_path / 'Other.esp', {}, masters=['World.esm'])
        _export(tmp_path / 'Addon.esp', {
            'LVLC.txt': _rec('LVLC', '01000F01', 'AddonList', **{'TNAM.Template': '01000A01'})},
            masters=['World.esm'])
        graph = _load(_export(tmp_path / 'Plugin.esp', {},
                              masters=['World.esm', 'Other.esp', 'Addon.esp']))
        assert graph.leveled_templates == {'0000A002', '02000A01'}

    def test_the_importers_graph_keeps_the_template_too(self):
        """Both graph builders agree on the templates, a master's id re-keyed."""
        graph = CrossRefGraph()
        rekey = lambda v, own_raw, own_key: v.replace('01', '02', 1) if v.startswith('01') else v
        _index_xref_record(graph, '02000F01', {'Signature': 'LVLC', 'FormID': '01000F01',
                                               'TNAM.Template': '01000A01'}, rekey)
        _index_xref_record(graph, '02000F02', {'Signature': 'CREA', 'FormID': '01000F02',
                                               'TNAM.Template': '01000A02'}, rekey)
        assert graph.leveled_templates == {'02000A01'}

    def test_a_count_is_never_taken_for_an_id(self):
        """Eight digits of count read like a FormID; only the id fields are re-keyed."""
        out = _new_scan_out()
        _scan_record_lines('CONT', [
            'Signature=CONT', 'FormID=10000C01', 'ItemCount=1',
            'Item[0].FormID=10000B01', 'Item[0].Count=10000005'], out, remap={16: 3})
        assert out['record_items'] == {'03000C01': (('03000B01', 10000005),)}

    def test_the_importers_graph_indexes_the_item_list_too(self):
        """Both graph builders must agree, or the two conversion passes diverge."""
        graph = CrossRefGraph()
        _index_xref_record(
            graph, '02000C01',
            {'Signature': 'CONT', 'FormID': '01000C01', 'ItemCount': '2',
             'Item[0].FormID': '01000B01', 'Item[0].Count': '7',
             'Item[1].FormID': '0000B001', 'Item[1].Count': '-1'},
            lambda v, own_raw, own_key: v.replace('01', '02', 1) if v.startswith('01') else v)
        assert graph.record_items == {'02000C01': (('02000B01', 7), ('0000B001', -1))}

    def test_a_missing_or_unreadable_count_is_no_count(self, xref):
        """An entry without a usable count is listed with 0, which never becomes a read."""
        graph = CrossRefGraph()
        _index_xref_record(
            graph, '0000C001',
            {'Signature': 'CONT', 'FormID': '0000C001', 'ItemCount': '2',
             'Item[0].FormID': '0000B001', 'Item[1].FormID': '0000B002',
             'Item[1].Count': 'x'}, lambda v, own_raw, own_key: v)
        assert graph.record_items == {'0000C001': (('0000B001', 0), ('0000B002', 0))}
        assert graph.listed_item_count(['0000C001'], '0000B001') == 0


#: `AuthoredItemCount` in TES4Polyfill.psc from its signature to its EndFunction, whitespace aside.
_FUNCTION = (
    'Int Function AuthoredItemCount(ObjectReference akRef, Form akItem, Int aiAuthored) Global',
    'If akRef == None',
    'Return 0',
    'EndIf',
    'Int count = akRef.GetItemCount(akItem)',
    'If count != 0 || aiAuthored <= 0',
    'Return count',
    'EndIf',
    'If akRef.GetNumItems() > 0 && akRef.GetContainerForms().Length == 0 && akRef.GetNumItems() > 0',
    'If akRef.GetItemCount(akItem) == 0',
    'Return aiAuthored',
    'EndIf',
    'EndIf',
    'Return akRef.GetItemCount(akItem)',
    'EndFunction',
)

#: The item asked for, and the count its record lists.
_ITEM, _LISTED = 'item', 3


class _Inventory:
    """A reference's inventory as the three natives answer for it.

    `listed` is what the base record lists; `changes` what the reference holds
    on top, None while the inventory was never built.  Then the native count
    is 0 (measured once, in game), GetContainerForms is empty and GetNumItems
    counts the record's entries (both as the script extender's source reads).
    `then` maps a native call's number to a change made just before it.
    """

    def __init__(self, listed=(), changes=None, then=()):
        """Start in one state; `truth` collects the true count at each native call."""
        self.listed, self.then = dict(listed), dict(then)
        self.changes = None if changes is None else dict(changes)
        self.truth = []

    def held(self, form) -> int:
        """What Oblivion answers: the listed count and every change since."""
        return max(0, self.listed.get(form, 0) + (self.changes or {}).get(form, 0))

    def _forms(self) -> list:
        """The forms held with a count above 0."""
        return [form for form in {**self.listed, **(self.changes or {})} if self.held(form) > 0]

    def _native(self, answer):
        """Make the change due before this call, note the true count, then answer."""
        change = self.then.get(len(self.truth))
        if change:
            change(self)
        self.truth.append(self.held(_ITEM))
        return answer()

    def GetItemCount(self, form) -> int:
        """The native count: nothing at all while the inventory was never built."""
        return self._native(lambda: self.held(form) if self.changes is not None else 0)

    def GetNumItems(self) -> int:
        """How many forms are held, the record's own included in either state."""
        return self._native(lambda: len(self._forms()))

    def GetContainerForms(self) -> list:
        """The forms held; none while the reference carries no changes."""
        return self._native(lambda: self._forms() if self.changes is not None else [])


def _opened(inv) -> None:
    """The engine builds the inventory; nothing is taken."""
    inv.changes = dict(inv.changes or {})


def _item_taken(inv) -> None:
    """Someone takes every copy of the item."""
    _opened(inv)
    inv.changes[_ITEM] = -inv.listed.get(_ITEM, 0)


def _emptied(inv) -> None:
    """Someone takes everything."""
    inv.changes = {form: -count for form, count in inv.listed.items()}


def _filled(inv) -> None:
    """Someone puts another thing in."""
    _opened(inv)
    inv.changes['extra'] = inv.changes.get('extra', 0) + 1


def _item_added(inv) -> None:
    """Someone puts two more of the item in."""
    _opened(inv)
    inv.changes[_ITEM] = inv.changes.get(_ITEM, 0) + 2


def _forgotten(inv) -> None:
    """The reference loses its changes again, as after a reset."""
    inv.changes = None


_CHANGES = (_opened, _item_taken, _emptied, _filled, _item_added, _forgotten)

#: State -> (what the record lists, the changes or None for never built, the true count, native calls made).
_STATES = {
    'never built': ({_ITEM: _LISTED, 'other': 1}, None, _LISTED, 5),
    'never built, the item alone': ({_ITEM: _LISTED}, None, _LISTED, 5),
    'built, untouched': ({_ITEM: _LISTED, 'other': 1}, {}, _LISTED, 1),
    'built, more of the item put in': ({_ITEM: _LISTED}, {_ITEM: 2}, _LISTED + 2, 1),
    'item taken, another left': ({_ITEM: _LISTED, 'other': 1}, {_ITEM: -_LISTED}, 0, 4),
    'everything taken': ({_ITEM: _LISTED, 'other': 1}, {_ITEM: -_LISTED, 'other': -1}, 0, 3),
    'emptied, then something else put in': ({_ITEM: _LISTED}, {_ITEM: -_LISTED, 'extra': 1}, 0, 4),
    'not a container': ({}, None, 0, 3),
}


def _authored_item_count(ref, form, authored: int) -> int:
    """`_FUNCTION` statement for statement, over an `_Inventory`; None is no reference."""
    if ref is None:
        return 0
    count = ref.GetItemCount(form)
    if count != 0 or authored <= 0:
        return count
    if ref.GetNumItems() > 0 and len(ref.GetContainerForms()) == 0 and ref.GetNumItems() > 0:
        if ref.GetItemCount(form) == 0:
            return authored
    return ref.GetItemCount(form)


def _count_read_before_the_state(ref, form, authored: int) -> int:
    """The order `_FUNCTION` replaces: the count read first is returned after the state test."""
    count = ref.GetItemCount(form)
    if count != 0 or authored <= 0:
        return count
    if ref.GetNumItems() > 0 and len(ref.GetContainerForms()) == 0:
        return authored
    return count


def _misread(function) -> tuple:
    """(wrong answers, changes tried) over every state and ONE change before each later native call.

    An answer is right when it is the true count at one of the call's own
    native calls: the reference held that many at some instant of the call.
    A wrong one is listed as (state, change, call number, answer, true counts).
    """
    wrong, tried = [], 0
    for state, change, at in itertools.product(_STATES, _CHANGES, range(1, 6)):
        inv = _Inventory(*_STATES[state][:2], then={at: change})
        answer = function(inv, _ITEM, _LISTED)
        if at < len(inv.truth):
            tried += 1
            if answer not in inv.truth:
                wrong.append((state, change.__name__, at, answer, inv.truth))
    return wrong, tried


class TestPolyfill:
    """The runtime half: `TES4Polyfill.AuthoredItemCount`."""

    def test_the_function_is_exactly_this(self):
        """Line for line; whoever changes it changes `_authored_item_count` and its tables too."""
        lines = [' '.join(line.split()) for line in _POLYFILL.read_text(encoding='utf-8').split('\n')]
        start = lines.index(_FUNCTION[0])
        assert tuple(lines[start:lines.index('EndFunction', start) + 1]) == _FUNCTION
        assert lines.count(_FUNCTION[0]) == 1

    @pytest.mark.parametrize('state', _STATES)
    def test_each_state_reads_its_true_count(self, state):
        """Never built: the listed count.  Built: the native count, whatever it is."""
        listed, changes, true, calls = _STATES[state]
        inv = _Inventory(listed, changes)
        assert inv.held(_ITEM) == true
        assert _authored_item_count(inv, _ITEM, _LISTED) == true
        assert len(inv.truth) == calls

    def test_no_reference_and_no_authored_count(self):
        """None holds nothing; without an authored count the native count stands in either state."""
        assert _authored_item_count(None, _ITEM, _LISTED) == 0
        for authored in (0, -1):
            assert _authored_item_count(_Inventory({_ITEM: _LISTED}), _ITEM, authored) == 0
            assert _authored_item_count(_Inventory({_ITEM: _LISTED}, {}), _ITEM, authored) == _LISTED

    def test_the_authored_count_is_the_one_handed_in(self):
        """The function knows no record: never built, it answers what it was handed."""
        assert _authored_item_count(_Inventory({_ITEM: _LISTED}), _ITEM, 7) == 7
        assert _authored_item_count(_Inventory({_ITEM: _LISTED}, {}), _ITEM, 7) == _LISTED

    def test_one_change_during_the_call_is_never_misread(self):
        """Opened, emptied, filled or forgotten mid-call: the answer was true once."""
        wrong, tried = _misread(_authored_item_count)
        gaps = sum(calls - 1 for *_, calls in _STATES.values())
        assert wrong == [] and tried == len(_CHANGES) * gaps == 108

    def test_the_check_sees_a_stale_read(self):
        """The order replaced: opened after its count was read, it still answers 0."""
        wrong, tried = _misread(_count_read_before_the_state)
        assert ('never built', '_opened', 1, 0, [_LISTED] * 3) in wrong
        assert ('never built', '_opened', 2, 0, [_LISTED] * 3) in wrong and tried == 60
