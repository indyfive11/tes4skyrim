"""The script stage's graph reads a master's records in THIS plugin's index space.

Every export numbers its ids by its OWN master list, so two masters that each
have one master both write their own records as `01xxxxxx`. The importer
re-keys them (`tests/test_master_export_fid_space.py`); `load_from_export`
must do the same or the later master's records overwrite the earlier one's.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from script_convert.cross_ref import CrossRefGraph, _index_remaps, _rekey


def _record(sig: str, fid: str, edid: str, **fields) -> str:
    """One export record block; `fields` keys spell `.` as `__`."""
    extra = ''.join('%s=%s\n' % (k.replace('__', '.'), v) for k, v in fields.items())
    return ('---RECORD_BEGIN---\nSignature=%s\nFormID=%s\nEditorID=%s\n%s'
            '---RECORD_END---\n' % (sig, fid, edid, extra))


def _export(root: Path, name: str, masters: list, files: dict, folder: str = '') -> Path:
    """An export folder (`folder`, else `name`): `_HEADER.txt` naming `masters`, plus `<SIG>.txt` per entry of `files`."""
    folder = root / (folder or name)
    folder.mkdir()
    header = ''.join('Master[%d]=%s\n' % (i, m) for i, m in enumerate(masters))
    (folder / '_HEADER.txt').write_text(header, encoding='utf-8')
    for sig, text in files.items():
        (folder / ('%s.txt' % sig)).write_text(text, encoding='utf-8')
    return folder


def _graph(folder: Path, workers: int = 1) -> CrossRefGraph:
    """The script stage's graph for the plugin in `folder`."""
    xref = CrossRefGraph()
    xref.load_from_export(str(folder), workers=workers)
    return xref


def _two_dlc_masters(root: Path) -> Path:
    """A plugin whose masters are Oblivion.esm and two files that each master only Oblivion.esm."""
    _export(root, 'Oblivion.esm', [], {
        'SCPT': _record('SCPT', '00012345', 'BaseScript', SCHR__Type=0),
        'PACK': _record('PACK', '00033333', 'BasePackage', PKDT__Type=5)})
    _export(root, 'First.esp', ['Oblivion.esm'], {
        'SCPT': _record('SCPT', '01000D29', 'FirstScript', SCHR__Type=0),
        'CREA': _record('CREA', '01000D26', 'FirstCreature', SCRI='01000D29')})
    _export(root, 'Second.esp', ['Oblivion.esm'], {
        'SCPT': _record('SCPT', '01000D26', 'SecondScript', SCHR__Type=0),
        'NPC_': _record('NPC_', '01000F54', 'SecondKnight', SCRI='01000D29',
                        **{'AIPackage[0]': '01000AAA', 'AIPackage[1]': '00033333',
                           'AIPackage[2]': '01000BBB'}),
        'MISC': _record('MISC', '01000F90', '0100FACEDoorKey'),
        'ACHR': _record('ACHR', '01000F60', 'SecondKnightRef', NAME='01000F54'),
        'DOOR': _record('DOOR', '01000F70', 'SecondDoor', SCRI='00012345')})
    return _export(root, 'Plugin.esp', ['Oblivion.esm', 'First.esp', 'Second.esp'], {
        'ACTI': _record('ACTI', '03000001', 'PluginActivator', SCRI='01000D29')})


def test_two_masters_with_the_same_own_index_do_not_collide(tmp_path):
    """Both write `01000D26`: a creature in one, a script in the other."""
    xref = _graph(_two_dlc_masters(tmp_path))
    assert xref.formid_to_edid['01000D26'] == 'FirstCreature'
    assert xref.record_type['01000D26'] == 'CREA'
    assert xref.formid_to_edid['02000D26'] == 'SecondScript'
    assert xref.record_type['02000D26'] == 'SCPT'
    assert xref.edid_to_formid['secondscript'] == '02000D26'
    assert xref.script_formid_to_edid == {'00012345': 'BaseScript', '01000D29': 'FirstScript',
                                          '02000D26': 'SecondScript'}


def test_a_script_is_carried_by_its_own_masters_records_only(tmp_path):
    """Read raw, the second master's knight carried the first master's script."""
    xref = _graph(_two_dlc_masters(tmp_path))
    assert sorted(xref.attached_records('01000D29')) == ['01000D26', '03000001']
    assert xref.record_scri['02000F54'] == '02000D29'
    assert xref.attached_records('02000D29') == ['02000F54']


def test_a_masters_reference_fields_move_with_its_own_id(tmp_path):
    """SCRI, NAME and the package list name the same file after the move; another file's id stays."""
    xref = _graph(_two_dlc_masters(tmp_path))
    assert xref.record_base['02000F60'] == '02000F54'
    assert xref.actor_packages['02000F54'] == ['02000AAA', '00033333', '02000BBB']
    assert xref.edid_to_formid['0100facedoorkey'] == '02000F90'
    assert xref.record_scri['02000F70'] == '00012345'
    assert '02000F54' in xref.npc_formids and '01000F54' not in xref.npc_formids


def test_a_masters_cell_and_effect_ids_move_too(tmp_path):
    """A cell's worldspace and an effect's two shader ids are id fields like SCRI and NAME."""
    plugin = _two_dlc_masters(tmp_path)
    second = tmp_path / 'Second.esp'
    (second / 'CELL.txt').write_text(_record(
        'CELL', '01000C01', 'SecondCell', DATA__Flags=0, ParentWRLD='01000B01',
        XCLC__X=1, XCLC__Y=2), encoding='utf-8')
    (second / 'MGEF.txt').write_text(_record(
        'MGEF', '01000E01', 'SCND', DATA__EffectShader='01000E02',
        DATA__EnchantEffect='01000E03', DATA__School=1), encoding='utf-8')
    xref = _graph(plugin)
    assert xref.cell_geom['02000C01'] == (False, '02000B01', 1, 2)
    assert xref.mgef_shaders['scnd'] == ('02000E02', '02000E03', 1)


def test_a_master_is_read_by_its_own_master_list_not_by_a_shift(tmp_path):
    """The master lists the files in another order than the plugin: each index maps by NAME."""
    _export(tmp_path, 'Oblivion.esm', [], {})
    _export(tmp_path, 'Lands.esp', ['Oblivion.esm'], {
        'ACTI': _record('ACTI', '01000111', 'LandsMarker')})
    _export(tmp_path, 'Towns.esp', ['Oblivion.esm', 'Lands.esp'], {
        'REFR': _record('REFR', '02000222', 'TownsMarkerRef', NAME='01000111')})
    plugin = _export(tmp_path, 'Plugin.esp', ['Oblivion.esm', 'Towns.esp', 'Lands.esp'], {})
    xref = _graph(plugin)
    assert xref.formid_to_edid['01000222'] == 'TownsMarkerRef'
    assert xref.formid_to_edid['02000111'] == 'LandsMarker'
    assert xref.record_base['01000222'] == '02000111'


def test_a_master_with_its_own_index_in_place_and_the_others_swapped_is_moved(tmp_path):
    """Its own index equals its slot, yet two of its masters sit the other way round in the plugin."""
    _export(tmp_path, 'Oblivion.esm', [], {})
    _export(tmp_path, 'Lands.esp', ['Oblivion.esm'], {
        'ACTI': _record('ACTI', '01000111', 'LandsMarker')})
    _export(tmp_path, 'Towns.esp', ['Oblivion.esm'], {})
    _export(tmp_path, 'Roads.esp', ['Oblivion.esm', 'Towns.esp', 'Lands.esp'], {
        'REFR': _record('REFR', '03000222', 'RoadsMarkerRef', NAME='02000111')})
    plugin = _export(tmp_path, 'Plugin.esp',
                     ['Oblivion.esm', 'Lands.esp', 'Towns.esp', 'Roads.esp'], {})
    assert _graph(plugin).record_base['03000222'] == '01000111'


def test_index_zero_moves_when_the_plugin_lists_another_file_first(tmp_path):
    """A master's first master is not always the plugin's first."""
    _export(tmp_path, 'Base.esm', [], {})
    _export(tmp_path, 'Oblivion.esm', [], {
        'SCPT': _record('SCPT', '00012345', 'BaseScript', SCHR__Type=0)})
    _export(tmp_path, 'Addon.esp', ['Oblivion.esm'], {
        'DOOR': _record('DOOR', '01000F70', 'AddonDoor', SCRI='00012345')})
    plugin = _export(tmp_path, 'Plugin.esp', ['Base.esm', 'Oblivion.esm', 'Addon.esp'], {})
    xref = _graph(plugin)
    assert xref.script_formid_to_edid == {'01012345': 'BaseScript'}
    assert xref.record_scri == {'02000F70': '01012345'}


def test_a_master_in_a_folder_not_named_after_it_is_still_read(tmp_path):
    """A mod that ships ONE plugin keeps its records in `export/<mod label>/` (`source_registry.record_dir`)."""
    (tmp_path / 'sources.json').write_text(json.dumps({'version': 1, 'sources': {'Solo.esp': {
        'kind': 'archive', 'plugin': 'Solo.esp', 'group_id': 'g1',
        'group_label': 'Solo Pack 1.2', 'group_plugins': ['Solo.esp']}}}), encoding='utf-8')
    _export(tmp_path, 'Oblivion.esm', [], {})
    _export(tmp_path, 'Solo.esp', ['Oblivion.esm'], {
        'SCPT': _record('SCPT', '01000BBB', 'SoloQuestScript', SCHR__Type=1),
        'QUST': _record('QUST', '01000AAA', 'SoloQuest', SCRI='01000BBB')}, folder='Solo Pack 1.2')
    plugin = _export(tmp_path, 'Plugin.esp', ['Oblivion.esm', 'Solo.esp'], {})
    xref = _graph(plugin)
    assert xref.edid_to_formid.get('soloquest') == '01000AAA'
    assert xref.get_quest_script_type('SoloQuest') == 'TES4_SoloQuestScript'


def test_an_index_that_names_an_unlisted_file_is_not_read_as_another(tmp_path):
    """`Shown.esp` has two masters and sits at slot 2, but its index 01 is `Hidden.esp`, not the plugin's `Other.esp`."""
    _export(tmp_path, 'Oblivion.esm', [], {})
    _export(tmp_path, 'Other.esp', ['Oblivion.esm'], {
        'ACTI': _record('ACTI', '01000333', 'OtherActivator')})
    _export(tmp_path, 'Hidden.esp', ['Oblivion.esm'], {
        'SCPT': _record('SCPT', '01000333', 'HiddenScript', SCHR__Type=0),
        'NPC_': _record('NPC_', '01000555', 'HiddenNpc')})
    _export(tmp_path, 'Shown.esp', ['Oblivion.esm', 'Hidden.esp'], {
        'ACTI': _record('ACTI', '02000444', 'ShownActivator', SCRI='01000333'),
        'NPC_': _record('NPC_', '01000555', 'HiddenNpcAsShownChangedIt'),
        'ACHR': _record('ACHR', '02000666', 'ShownRef', NAME='01000333')})
    plugin = _export(tmp_path, 'Plugin.esp', ['Oblivion.esm', 'Other.esp', 'Shown.esp'], {})
    xref = _graph(plugin)
    assert xref.formid_to_edid['02000444'] == 'ShownActivator'
    assert '02000444' not in xref.record_scri and '02000666' not in xref.record_base
    assert '01000555' not in xref.formid_to_edid
    assert xref.formid_to_edid['01000333'] == 'OtherActivator'


def test_an_unlisted_masters_change_to_a_listed_files_record_is_kept(tmp_path):
    """`Hidden.esp` (a master's master) gives an Oblivion.esm NPC a script: the game loads that file."""
    _export(tmp_path, 'Oblivion.esm', [], {
        'SCPT': _record('SCPT', '00012345', 'BaseScript', SCHR__Type=0),
        'NPC_': _record('NPC_', '00044444', 'BaseNpc')})
    _export(tmp_path, 'Hidden.esp', ['Oblivion.esm'], {
        'NPC_': _record('NPC_', '00044444', 'BaseNpc', SCRI='00012345')})
    _export(tmp_path, 'Shown.esp', ['Oblivion.esm', 'Hidden.esp'], {})
    plugin = _export(tmp_path, 'Plugin.esp', ['Oblivion.esm', 'Shown.esp'], {})
    assert _graph(plugin).record_scri.get('00044444') == '00012345'


def test_the_worker_pool_moves_ids_like_the_serial_scan(tmp_path):
    """A real run scans in a process pool: the map has to reach the workers."""
    xref = _graph(_two_dlc_masters(tmp_path), workers=2)
    assert xref.record_scri['02000F54'] == '02000D29'
    assert xref.formid_to_edid['01000D26'] == 'FirstCreature'


def test_what_the_plugin_does_not_load_is_not_in_the_graph(tmp_path):
    """A master's master the plugin does not list: its own records and every id naming it are left out."""
    _export(tmp_path, 'Oblivion.esm', [], {})
    _export(tmp_path, 'Hidden.esp', ['Oblivion.esm'], {
        'SCPT': _record('SCPT', '01000333', 'HiddenScript', SCHR__Type=0)})
    _export(tmp_path, 'Shown.esp', ['Oblivion.esm', 'Hidden.esp'], {
        'ACTI': _record('ACTI', '02000444', 'ShownActivator', SCRI='01000333')})
    plugin = _export(tmp_path, 'Plugin.esp', ['Oblivion.esm', 'Shown.esp'], {
        'SCPT': _record('SCPT', '02000333', 'PluginScript', SCHR__Type=0)})
    xref = _graph(plugin)
    assert xref.formid_to_edid['01000444'] == 'ShownActivator'
    assert '01000444' not in xref.record_scri
    assert 'hiddenscript' not in xref.edid_to_formid
    assert xref.attached_records('02000333') == []
    assert set(xref.formid_to_edid) == {'01000444', '02000333'}


def test_a_chain_whose_indexes_agree_is_read_as_written(tmp_path):
    """One plugin master, the common case: no id is rewritten, lower-case hex included."""
    _export(tmp_path, 'Oblivion.esm', [], {
        'GLOB': _record('GLOB', '0000abcd', 'BaseGlobal')})
    _export(tmp_path, 'Master.esp', ['Oblivion.esm'], {
        'ACTI': _record('ACTI', '0100beef', 'MasterActivator', SCRI='0000abcd')})
    plugin = _export(tmp_path, 'Plugin.esp', ['Oblivion.esm', 'Master.esp'], {})
    dirs = [str(tmp_path / n) for n in ('Oblivion.esm', 'Master.esp', 'Plugin.esp')]
    assert _index_remaps(str(plugin), dirs) == [None, None, None]
    xref = _graph(plugin)
    assert xref.record_scri == {'0100beef': '0000abcd'}
    assert set(xref.formid_to_edid) == {'0000abcd', '0100beef'}


def test_the_maps_name_each_folder_of_the_chain(tmp_path):
    """None for the plugin and an agreeing master, a map for a moved one, empty for an unlisted one."""
    plugin = _two_dlc_masters(tmp_path)
    (tmp_path / 'Unlisted.esp').mkdir()
    dirs = [str(tmp_path / n) for n in
            ('Oblivion.esm', 'First.esp', 'Second.esp', 'Unlisted.esp', 'Plugin.esp')]
    assert _index_remaps(str(plugin), dirs) == [None, None, {0: 0, 1: 2}, {}, None]


def test_rekey_keeps_the_text_after_an_id_and_non_ids():
    """A package line carries text after its id; a value that is no id is returned as it is."""
    remap = {0: 0, 1: 2}
    assert _rekey('01000AAA', remap) == '02000AAA'
    assert _rekey('01000aaa [PACK:Patrol]', remap) == '02000AAA [PACK:Patrol]'
    assert _rekey('00000000', remap) == '00000000'
    assert _rekey('', remap) == '' and _rekey('none', remap) == 'none'
    assert _rekey('05000001', remap) == ''
