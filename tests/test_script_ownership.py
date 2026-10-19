"""Plugins sharing one `scripts/` folder must not wipe each other's scripts.

See: docs/commentary/script_convert.md#wipe-output-dir
"""
import json

from asset_convert.sources.source_registry import record_dir
from script_convert.context_setup import prepare_output_dir
from script_convert.ownership import (departed_owners, read_owned, sibling_owned,
                                      write_owned)


def _scripts(tmp_path, names):
    """A `scripts/source` tree holding `names` as .psc with matching .pex."""
    src = tmp_path / 'scripts' / 'source'
    src.mkdir(parents=True)
    for n in names:
        (src / f'{n}.psc').write_text('x', encoding='utf-8')
        (src.parent / f'{n}.pex').write_bytes(b'x')
    return src


def test_a_shared_folder_keeps_the_siblings_scripts(tmp_path):
    """Converting the ESP after the ESM deleted every one of the ESM's scripts."""
    src = _scripts(tmp_path, ['EsmOnly', 'EspOld', 'Both'])
    write_owned(src, 'Mod.esm', ['EsmOnly', 'Both'])
    write_owned(src, 'Mod.esp', ['EspOld', 'Both'])

    shared = prepare_output_dir(str(src), 'Mod.esp')

    assert shared == {'EsmOnly', 'Both'}
    assert sorted(p.stem for p in src.glob('*.psc')) == ['Both', 'EsmOnly']
    assert sorted(p.stem for p in src.parent.glob('*.pex')) == ['Both', 'EsmOnly']


def test_an_unshared_folder_is_still_wiped_whole(tmp_path):
    """Stale scripts a plugin stopped generating must not survive."""
    src = _scripts(tmp_path, ['Stale', 'Unlisted'])
    write_owned(src, 'Oblivion.esm', ['Stale'])

    assert prepare_output_dir(str(src), 'Oblivion.esm') == set()
    assert not list(src.glob('*.psc'))
    assert not list(src.parent.glob('*.pex'))


def test_owned_lists_round_trip(tmp_path):
    """A written list reads back, and a sibling sees it."""
    src = _scripts(tmp_path, [])
    write_owned(src, 'A.esm', ['X', 'Y', 'X'])
    assert read_owned(src, 'A.esm') == {'X', 'Y'}
    assert sibling_owned(src, 'B.esp') == {'X', 'Y'}
    assert sibling_owned(src, 'A.esm') == set()


def _folder_mod(tmp_path, kind):
    """A registered three-plugin mod whose source folder has lost Gone.esp."""
    folder = tmp_path / 'original'
    folder.mkdir(parents=True)
    for name in ('A.esm', 'B.esp'):
        (folder / name).write_bytes(b'x')
    names = ['A.esm', 'B.esp', 'Gone.esp']
    exp = tmp_path / 'export'
    exp.mkdir(parents=True)
    (exp / 'sources.json').write_text(json.dumps({'version': 2, 'sources': {
        n: {'kind': kind, 'plugin': n, 'plugin_member': n, 'payload_root': [],
            'archive_original': str(folder), 'group_id': 'g', 'group_label': 'Pack',
            'group_plugins': names} for n in names}}), encoding='utf-8')
    return record_dir(exp, 'A.esm')


def test_a_member_deleted_from_its_folder_loses_its_scripts(tmp_path):
    """Issue #84: a removed patch's list kept an old TES4Polyfill alive."""
    records = _folder_mod(tmp_path, 'folder')
    src = _scripts(tmp_path, ['AOld', 'Live', 'Shared', 'TES4Polyfill', 'OldOnly'])
    write_owned(src, 'A.esm', ['AOld'])
    write_owned(src, 'B.esp', ['Live', 'Shared'])
    write_owned(src, 'Gone.esp', ['Shared', 'TES4Polyfill'])
    write_owned(src, 'Unregistered.esp', ['OldOnly'])

    departed = departed_owners(str(src), records)
    assert sorted(departed) == ['Gone.esp', 'Unregistered.esp']
    assert prepare_output_dir(str(src), 'A.esm', departed) == {'Live', 'Shared'}
    assert sorted(p.stem for p in src.parent.glob('*.pex')) == ['Live', 'Shared']
    assert sorted(p.name for p in src.parent.glob('*.owned.txt')) == [
        'A.esm.owned.txt', 'B.esp.owned.txt']


def test_an_archive_or_offline_folder_keeps_every_member(tmp_path):
    """Nothing but a reachable folder can say a registered member was removed."""
    archive = _folder_mod(tmp_path / 'archive', 'archive')
    offline = _folder_mod(tmp_path / 'offline', 'folder')
    (tmp_path / 'offline' / 'original' / 'A.esm').unlink()
    (tmp_path / 'offline' / 'original' / 'B.esp').unlink()
    (tmp_path / 'offline' / 'original').rmdir()
    for records in (archive, offline):
        src = _scripts(records.parent.parent.parent, ['Kept'])
        write_owned(src, 'Gone.esp', ['Kept'])
        assert departed_owners(str(src), records) == []


def test_a_plugin_outside_any_mod_judges_no_list(tmp_path):
    """A game Data plugin has no registered membership to judge siblings by."""
    src = _scripts(tmp_path, [])
    write_owned(src, 'Other.esp', [])
    assert departed_owners(str(src), tmp_path / 'export' / 'Oblivion.esm') == []
