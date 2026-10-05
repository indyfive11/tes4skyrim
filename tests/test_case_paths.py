"""The shared case-blind resolver, its write rule and its census.

Oblivion's output holds twin folders that differ only by case (an empty
`Dementia/` beside `dementia/x.dds`); the two private `_join_nocase` copies
kept one spelling per folder, so the listing order decided hit or miss.
"""
import os
from pathlib import PurePosixPath, PureWindowsPath

import pytest

from asset_convert import case_paths
from asset_convert.game_paths import win_join
from asset_convert.nif import nif_batch, shaders
from asset_convert.ui import book_inam


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_state():
    """Every test starts with no cached listings, no counts and no clamp log."""
    case_paths.invalidate()
    case_paths.snapshot_counts()
    case_paths._CLAMPED.clear()
    yield
    case_paths.invalidate()
    case_paths.snapshot_counts()


def _put(path, data=b'DDS '):
    """Write `data` at `path`, creating its folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _force_listdir(monkeypatch, capital_first):
    """Make os.listdir return names with capitalised spellings first or last."""
    real = os.listdir

    def ordered(path='.'):
        """The real listing, in the forced order."""
        return sorted(real(path), key=lambda n: (n.islower() == capital_first, n))
    monkeypatch.setattr(os, 'listdir', ordered)


def _twin_tree(tmp_path, top):
    """A plugin dir whose `top` holds an empty `Dementia/` beside `dementia/x.dds`."""
    plugin = tmp_path / 'Plugin.esm'
    (plugin / top / 'Dementia').mkdir(parents=True)
    _put(plugin / top / 'dementia' / 'x.dds')
    (plugin / 'meshes').mkdir(exist_ok=True)
    return plugin


# ---------------------------------------------------------------------------
# Twin folders, whatever the listing order
# ---------------------------------------------------------------------------


@pytest.mark.parametrize('capital_first', [True, False])
@pytest.mark.usefixtures('case_twins')
def test_shader_texture_found_past_an_empty_twin_folder(tmp_path, monkeypatch,
                                                        capital_first):
    """The empty `Dementia/` twin cannot hide `dementia/x.dds`."""
    plugin = _twin_tree(tmp_path, 'textures')
    _force_listdir(monkeypatch, capital_first)
    got = shaders.resolve_source_texture(
        'textures\\Dementia\\X.dds', str(plugin / 'meshes' / 'a.nif'))
    assert got == str(plugin / 'textures' / 'dementia' / 'x.dds')


@pytest.mark.parametrize('capital_first', [True, False])
@pytest.mark.usefixtures('case_twins')
def test_book_mesh_found_past_an_empty_twin_folder(tmp_path, monkeypatch,
                                                   capital_first):
    """BOOK MODL lookups branch into every case-variant folder."""
    plugin = tmp_path / 'Plugin.esm'
    (plugin / 'meshes' / 'Clutter').mkdir(parents=True)
    _put(plugin / 'meshes' / 'clutter' / 'book01.nif')
    _force_listdir(monkeypatch, capital_first)
    got = book_inam.find_source_mesh(str(plugin), 'Clutter\\Book01.NIF')
    assert got == str(plugin / 'meshes' / 'clutter' / 'book01.nif')


@pytest.mark.parametrize('capital_first', [True, False])
@pytest.mark.usefixtures('case_twins')
def test_resolve_branches_into_every_variant(tmp_path, monkeypatch, capital_first):
    """resolve itself is order-independent over twin folders."""
    plugin = _twin_tree(tmp_path, 'textures')
    _force_listdir(monkeypatch, capital_first)
    got = case_paths.resolve([plugin], 'Textures\\DEMENTIA\\x.DDS', 't')
    assert got == plugin / 'textures' / 'dementia' / 'x.dds'


# ---------------------------------------------------------------------------
# Priority, freshness, collisions
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures('case_twins')
def test_exact_path_wins_over_a_case_variant(tmp_path):
    """An exact hit is taken even when a lowercase spelling also exists."""
    exact = _put(tmp_path / 'Tex' / 'Stone.dds')
    _put(tmp_path / 'tex' / 'stone.dds')
    assert case_paths.resolve([tmp_path], 'Tex\\Stone.dds', 't') == exact
    assert case_paths.snapshot_counts()['t']['exact'] == 1


def test_root_order_beats_an_exact_hit_in_a_later_root(tmp_path):
    """Root 1's case-blind hit wins over root 2's exact hit."""
    first = _put(tmp_path / 'a' / 'tex' / 'stone.dds')
    _put(tmp_path / 'b' / 'Tex' / 'Stone.DDS')
    got = case_paths.resolve([tmp_path / 'a', tmp_path / 'b'], 'Tex\\Stone.DDS', 't')
    assert got.samefile(first)


def test_file_created_after_the_listing_is_found_in_the_first_root(tmp_path):
    """A file written into root 1 after its folder was listed still wins over root 2."""
    a, b = tmp_path / 'a', tmp_path / 'b'
    (a / 'tex').mkdir(parents=True)
    (b / 'tex').mkdir(parents=True)
    assert case_paths.resolve([a, b], 'Tex\\Other.dds', 't') is None
    first = _put(a / 'tex' / 'stone.dds')
    _put(b / 'tex' / 'stone.dds')
    assert case_paths.resolve([a, b], 'Tex\\Stone.DDS', 't').samefile(first)


def test_deleted_file_is_not_returned(tmp_path):
    """A cached listing never returns a file that has gone."""
    f = _put(tmp_path / 'tex' / 'stone.dds')
    assert case_paths.resolve([tmp_path], 'TEX\\Stone.dds', 't').samefile(f)
    f.unlink()
    assert case_paths.resolve([tmp_path], 'TEX\\Stone.dds', 't') is None


@pytest.mark.usefixtures('case_twins')
def test_collision_resolves_to_the_lowercase_spelling_and_logs_once(tmp_path, capsys):
    """Two spellings of one file: the all-lowercase one, logged and counted once."""
    _put(tmp_path / 'Tex' / 'STONE.dds')
    low = _put(tmp_path / 'tex' / 'stone.dds')
    _put(tmp_path / 'tex' / 'Stone.dds')
    for _ in range(2):
        assert case_paths.resolve([tmp_path], 'TEX\\Stone.DDS', 't') == low
    assert capsys.readouterr().out.count('CASE COLLISION') == 1
    assert case_paths.snapshot_counts()['t'] == {
        'exact': 0, 'resolved': 2, 'missed': 0, 'collisions': 1}


@pytest.mark.usefixtures('case_twins')
def test_collision_without_a_lowercase_spelling_takes_the_smallest_path(tmp_path):
    """With no all-lowercase spelling the answer is the smallest path string."""
    _put(tmp_path / 'tex' / 'A.dds')
    smallest = _put(tmp_path / 'Tex' / 'A.dds')
    assert case_paths.resolve([tmp_path], 'TEX\\a.dds', 't') == smallest


@pytest.mark.usefixtures('case_twins')
def test_list_prefix_and_rglob_ignore_case(tmp_path):
    """Listing and globbing see every spelling."""
    _put(tmp_path / 'Lod' / 'Tamriel.4.0.0.DDS')
    _put(tmp_path / 'lod' / 'tamriel.4.0.4.dds')
    names = [p.name for p in case_paths.list_prefix(tmp_path, 'LOD', 'tamriel.4.')]
    assert names == ['Tamriel.4.0.0.DDS', 'tamriel.4.0.4.dds']
    assert len(case_paths.rglob(tmp_path, '*.dds')) == 2


# ---------------------------------------------------------------------------
# Counts across a worker pool
# ---------------------------------------------------------------------------


def test_worker_counts_merge_into_the_report(tmp_path):
    """A worker's snapshot, folded in by nif_batch, reaches report()."""
    stats = nif_batch._empty_batch_stats(1)
    result = {'converted': False, 'copied': True, 'case_counts': {
        's': {'exact': 2, 'resolved': 3, 'missed': 1, 'collisions': 0}}}
    nif_batch._merge_result(stats, [], tmp_path, str(tmp_path / 'a.nif'), result)
    lines = []
    case_paths.report(lines.append)
    assert lines == ['  Case paths: s exact 2 / resolved 3 / missed 1 / collisions 0']
    assert case_paths.snapshot_counts() == {}


# ---------------------------------------------------------------------------
# The write rule
# ---------------------------------------------------------------------------


def test_write_path_reuses_the_one_existing_spelling(tmp_path):
    """An existing `Oblivion/` is reused, never twinned with `oblivion/`."""
    (tmp_path / 'Oblivion').mkdir()
    out = case_paths.write_path(tmp_path, 'oblivion\\New_n.dds')
    assert out == tmp_path / 'Oblivion' / 'new_n.dds'
    assert sorted(os.listdir(tmp_path)) == ['Oblivion']


@pytest.mark.usefixtures('case_twins')
def test_write_path_creates_lowercase_and_reuses_a_file_spelling(tmp_path):
    """New segments are lowercase; an existing file keeps its spelling."""
    out = case_paths.write_path(tmp_path, 'Textures\\Fire\\A.dds')
    assert out == tmp_path / 'textures' / 'fire' / 'a.dds'
    assert out.parent.is_dir()
    _put(tmp_path / 'textures' / 'fire' / 'B.DDS')
    assert case_paths.write_path(tmp_path, 'textures\\fire\\b.dds').name == 'B.DDS'


@pytest.mark.usefixtures('case_twins')
def test_write_path_takes_the_lowercase_twin(tmp_path, capsys):
    """With two spellings on disk the lowercase one is used and logged."""
    (tmp_path / 'Oblivion').mkdir()
    (tmp_path / 'oblivion').mkdir()
    out = case_paths.write_path(tmp_path, 'OBLIVION\\x.dds')
    assert out == tmp_path / 'oblivion' / 'x.dds'
    assert 'CASE COLLISION' in capsys.readouterr().out


def _aged(root):
    """Backdate every folder under `root` past the racy window, as a settled tree is."""
    old = os.stat(root).st_mtime - 60
    for base, dirs, _files in os.walk(root):
        for d in dirs:
            os.utime(os.path.join(base, d), (old, old))
    os.utime(root, (old, old))


def _count_listdir(monkeypatch):
    """Count os.listdir calls made by case_paths."""
    calls = []
    real = os.listdir

    def _listdir(path):
        calls.append(str(path))
        return real(path)

    monkeypatch.setattr(case_paths.os, 'listdir', _listdir)
    return calls


def test_write_path_lists_an_unchanged_folder_once(tmp_path, monkeypatch):
    """Overwriting siblings reuses the cached listings (Windows lists slowly)."""
    for n in ('A.dds', 'B.dds'):
        _put(tmp_path / 'Textures' / 'Effects' / n)
    _aged(tmp_path)
    calls = _count_listdir(monkeypatch)

    first = case_paths.write_path(tmp_path, 'textures\\effects\\a.dds')
    listed = len(calls)
    second = case_paths.write_path(tmp_path, 'textures\\effects\\b.dds')

    assert first.name == 'A.dds' and second.name == 'B.dds'
    assert listed == 3 and len(calls) == listed, calls


def test_write_path_drops_the_listing_of_a_folder_it_grows(tmp_path):
    """A new file or folder invalidates its parent's cached listing only."""
    _put(tmp_path / 'textures' / 'old.dds')
    _aged(tmp_path)
    case_paths.variants(tmp_path, 'textures\\old.dds')

    case_paths.write_path(tmp_path, 'textures\\new.dds')

    assert str(tmp_path) in case_paths._LISTINGS
    assert str(tmp_path / 'textures') not in case_paths._LISTINGS


def test_a_trailing_separator_root_invalidates_the_same_key(tmp_path):
    """`root/` and `root` are one folder: its stale listing must not survive."""
    root = str(tmp_path) + os.sep
    case_paths.variants(root, 'fire')

    case_paths.write_path(root, 'Fire\\a.dds')

    assert root not in case_paths._LISTINGS
    assert str(tmp_path) not in case_paths._LISTINGS
    assert case_paths.variants(root, 'FIRE') == [str(tmp_path / 'fire')]


# ---------------------------------------------------------------------------
# The census
# ---------------------------------------------------------------------------


def _census_tree(tmp_path):
    """Two twin-folder groups, plus one file present under Textures/ and textures/."""
    plugin = tmp_path / 'Plugin.esm'
    land = plugin / 'textures' / 'landscape'
    (land / 'Dementia').mkdir(parents=True)
    _put(land / 'dementia' / 'x.dds')
    _put(land / 'Oblivion' / 'a.dds')
    _put(land / 'oblivion' / 'b.dds')
    _put(plugin / 'Textures' / 'landscape' / 'dementia' / 'x.dds')
    return plugin


@pytest.mark.usefixtures('case_twins')
def test_census_warns_on_twin_folders_without_a_collision(tmp_path):
    """Folder twins alone are a warning: no file collides."""
    plugin = _census_tree(tmp_path)
    c = case_paths.census(plugin / 'textures')
    assert len(c.dup_groups) == 2 and c.file_collisions == [] and c.files == 3
    assert 'WARN' in case_paths.census_line('t', c)


@pytest.mark.usefixtures('case_twins')
def test_census_fails_on_a_file_collision_across_merged_tops(tmp_path):
    """Merged top folders are not twins, but a file under both collides."""
    plugin = _census_tree(tmp_path)
    c = case_paths.census(plugin, ['textures', 'meshes'])
    assert len(c.dup_groups) == 2 and c.files == 4
    assert c.file_collisions == [tuple(sorted([
        str(plugin / 'Textures' / 'landscape' / 'dementia' / 'x.dds'),
        str(plugin / 'textures' / 'landscape' / 'dementia' / 'x.dds')]))]
    assert 'FAIL' in case_paths.census_line('t', c)


# ---------------------------------------------------------------------------
# A rel cannot leave its root
# ---------------------------------------------------------------------------


#: Rels that stay inside on their own, with the segments they split into.
_INSIDE_RELS = [('tes4\\landscape\\x_n.dds', ['tes4', 'landscape', 'x_n.dds']),
                ('\\\\lead//double\\x.dds', ['lead', 'double', 'x.dds']),
                ('a\\..\\b.dds', ['b.dds']),
                ('a\\.\\name..dds', ['a', 'name..dds'])]

#: Rels that would leave the root by `..` or by a drive designator.
_HOSTILE_RELS = ['..\\escaped.dds', 'tes4\\..\\..\\..\\escaped.dds', '..',
                 'f:\\elsewhere\\x.dds', 'tes4\\f:\\x.dds', 'f:x.dds',
                 'a:b:c.dds', 'f:..\\x.dds', 'f:..\\..\\x.dds', 'z:',
                 '1:\\x.dds', '::x.dds']


@pytest.mark.parametrize('rel, parts', _INSIDE_RELS)
def test_split_rel_keeps_what_stays_inside(rel, parts, capsys):
    """Separators of either kind go; an inner `..` steps back; nothing is logged."""
    assert case_paths.split_rel(rel) == parts
    assert capsys.readouterr().out == ''


@pytest.mark.parametrize('rel', _HOSTILE_RELS)
@pytest.mark.parametrize('flavour, root', [(PureWindowsPath, 'C:/out/textures'),
                                           (PurePosixPath, '/out/textures')])
def test_split_rel_never_leaves_the_root(rel, flavour, root):
    """By Windows' own join rules and by POSIX's, a hostile rel stays below the root."""
    parts = case_paths.split_rel(rel)
    assert '..' not in parts
    assert flavour(root).joinpath(*parts).is_relative_to(root)


def test_a_clamped_rel_is_logged_once(capsys):
    """The first clamp of a rel prints PATH CLAMPED; a repeat is silent."""
    for rel in ('..\\..\\up.dds', 'tes4\\f:\\drive.dds'):
        case_paths.split_rel(rel)
        assert capsys.readouterr().out.count('PATH CLAMPED') == 1
        case_paths.split_rel(rel)
        assert capsys.readouterr().out == ''


def test_the_clamp_log_is_ascii_and_bounded(capsys):
    """A rel no console can encode still logs; past the limit one closing line is all."""
    case_paths.split_rel('..\\bad\ufffdname\u0142.dds')
    assert capsys.readouterr().out.isascii()
    for i in range(case_paths._CLAMP_LOG_LIMIT + 20):
        case_paths.split_rel('..\\n%d.dds' % i)
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == case_paths._CLAMP_LOG_LIMIT
    assert lines[-1].endswith('further paths are not listed')


def test_a_write_and_a_lookup_stay_below_the_root(tmp_path):
    """A record path with `..` is written below the root and cannot find a file above it."""
    root = tmp_path / 'out' / 'Plugin.esp' / 'textures'
    root.mkdir(parents=True)
    _put(tmp_path / 'out' / 'above.dds')
    rel = '..\\..\\above.dds'
    assert (root / '..' / '..' / 'above.dds').is_file()
    assert case_paths.resolve([root], rel, 't') is None
    assert case_paths.write_path(root, rel) == root / 'above.dds'
    assert win_join(root, rel) == root / 'above.dds'
