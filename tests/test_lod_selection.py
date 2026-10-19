"""Which references reach the LODGen input, and how their rows are written.

`write_lodgen_input` is driven with a synthetic `_parsed` tuple and a tmp
mesh tree; LODGEN_EXE points into tmp_path because the input file is written
next to the exe.  The NIF header reader is stubbed (a file holding b'NIF' is
a safe NiNode root, anything else is not) so the tests exercise selection,
not the parser.

`TestLodgenInput` is a set of characterization tests: they pass on
`write_lodgen_input` as it was BEFORE it was split into named phases and
after, by design.  They pin what the split moved -- the per-reference
filters, the kept-tile footprint, the master modes, mesh staging and
screening, the per-base memo and the file's shape -- so a later change to
any of them has to show up here.

`TestGameStartState` and `TestEnableParentChain` are ordinary tests of the
rule that a LOD reference the records leave off at game start is not listed.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset_convert.lod import lod_gen

WRLD = 0x0000003C
CELL = 0x00000100
LOD = 0x00008000
ROCK = 'tes4\\rocks\\rock01.nif'
ROCK2 = 'tes4\\rocks\\rock02.nif'
ROCK3 = 'tes4\\rocks\\rock03.nif'


@pytest.fixture
def gen(monkeypatch, tmp_path):
    """lod_gen with the exe in tmp_path, fresh caches and a stub header reader."""
    exe_dir = tmp_path / 'lodgen'
    exe_dir.mkdir()
    monkeypatch.setattr(lod_gen, 'LODGEN_EXE', exe_dir / 'LODGenx64.exe')
    monkeypatch.setattr(lod_gen, '_NIF_ROOT_SAFE_CACHE', {})
    monkeypatch.setattr(lod_gen, '_STAGED_MASTER_MESHES', set())
    monkeypatch.setattr(lod_gen, '_root_is_ninode',
                        lambda full: Path(full).read_bytes() == b'NIF')
    return lod_gen


def _far(model, suffix='_far'):
    """The LOD mesh path of `model` for one suffix."""
    return model[:-4] + suffix + '.nif'


def _mesh(root, *rels, data=b'NIF'):
    """Write each backslash path of `rels` under `root/meshes`; b'NIF' is safe."""
    for rel in rels:
        p = root / 'meshes'
        for part in rel.split('\\'):
            p = p / part
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)


def _stat(model, flags=LOD, edid='Rock01', sig='STAT', **lods):
    """A `parse_esm`-shaped base record, 100 units across."""
    stat = {'edid': edid, 'sig': sig, 'flags': flags, 'model': model,
            'obnd': (0, 0, 0, 100, 100, 100),
            'lod4': '', 'lod8': '', 'lod16': ''}
    stat.update(lods)
    return stat


def _ref(fid, base, flags=0, cell=(0, 0), **extra):
    """A `parse_esm`-shaped reference 100 units inside grid cell `cell`."""
    ref = {'form_id': fid, 'flags': flags, 'base_fid': base,
           'parent_wrld': WRLD, 'parent_cell': CELL,
           'x': cell[0] * 4096.0 + 100.0, 'y': cell[1] * 4096.0 + 100.0,
           'z': 0.0, 'rx': 0.0, 'ry': 0.0, 'rz': 0.0, 'scale': 1.0,
           'xesp': None}
    ref.update(extra)
    return ref


def _parsed(stats, refs):
    """A `parse_esm`-shaped tuple: one worldspace, one cell."""
    worldspaces = {WRLD: {'edid': 'TES4Tamriel', 'sw_x': -2, 'sw_y': -3,
                          'ne_x': 2, 'ne_y': 2}}
    cells = {CELL: {'parent_wrld': WRLD, 'grid_x': 0, 'grid_y': 0}}
    return worldspaces, cells, stats, refs


def _rows(path):
    """{ref FormID: row fields} from a written LODGen input; {} for None."""
    rows = {}
    for line in (Path(path).read_text(encoding='utf-8').splitlines()
                 if path else ()):
        if '\t' in line:
            f = line.split('\t')
            rows[int(f[0], 16)] = f
    return rows


def _write(gen, out, stats, refs, edid='TES4Tamriel', **kw):
    """Run `write_lodgen_input` on a synthetic parse; `cell_sw` defaults to (-32, -16)."""
    kw.setdefault('cell_sw', (-32, -16))
    return gen.write_lodgen_input(Path('x.esm'), out, edid,
                                  _parsed=_parsed(stats, refs), **kw)


class TestLodgenInput:
    """Characterization of `write_lodgen_input`; see the module docstring."""

    def test_header_and_file_place(self, gen, tmp_path, monkeypatch):
        """Five header lines, absolute paths from a relative dir, file by the exe."""
        monkeypatch.chdir(tmp_path)
        out = Path('AutoConvertLOD')
        _mesh(out, ROCK, _far(ROCK))
        txt = _write(gen, out, {0x10: _stat(ROCK)}, [_ref(0xA1, 0x10)])
        text = Path(txt).read_text(encoding='utf-8')
        head = [h.replace('/', '\\') for h in text.splitlines()[:5]]
        here = tmp_path.name + '\\AutoConvertLOD'
        assert head[:3] == ['GameMode=TES5', 'Worldspace=TES4Tamriel',
                            'CellSW=-32 -16']
        assert head[3].startswith('PathData=')
        assert head[3].endswith(here + '\\')
        assert head[4].startswith('PathOutput=')
        assert head[4].endswith(here + '\\meshes\\terrain\\TES4Tamriel\\Objects')
        assert text.endswith('\n') and not text.endswith('\n\n')
        assert Path(txt).parent == tmp_path / 'lodgen'

    def test_cell_sw_falls_back_to_the_worldspace_bounds(self, gen, tmp_path):
        """Without `cell_sw` the header carries the WRLD's own SW corner."""
        out = tmp_path / 'AutoConvertLOD'
        _mesh(out, ROCK, _far(ROCK))
        txt = _write(gen, out, {0x10: _stat(ROCK)}, [_ref(0xA1, 0x10)],
                     cell_sw=None)
        assert Path(txt).read_text(encoding='utf-8').splitlines()[2] == 'CellSW=-2 -3'

    def test_row_fields(self, gen, tmp_path):
        """9 REFR fields (degrees, 4 decimals), then the base half with its tiers."""
        out = tmp_path / 'AutoConvertLOD'
        _mesh(out, ROCK, _far(ROCK), _far(ROCK, '_far8'), _far(ROCK, '_far16'))
        ref = _ref(0xA1, 0x10, flags=0xC400, cell=(1, -2), z=5.0, rx=0.5,
                   rz=1.0, scale=1.23456)
        r = _rows(_write(gen, out, {0x10: _stat(ROCK)}, [ref]))[0xA1]
        assert r[:2] == ['000000A1', '0000C400']
        assert r[2:5] == ['4196.0000', '-8092.0000', '5.0000']
        assert r[5:9] == ['28.6479', '0.0000', '57.2958', '1.2346']
        assert r[9:12] == ['Rock01', '00008000', '']
        assert r[12:] == ['meshes\\' + m for m in (
            ROCK, _far(ROCK), _far(ROCK, '_far8'), _far(ROCK, '_far16'))]

    def test_per_reference_filters(self, gen, tmp_path):
        """Listed by own WRLD or parent CELL; base must be known, modelled, LOD-flagged."""
        out = tmp_path / 'AutoConvertLOD'
        _mesh(out, ROCK, _far(ROCK))
        unflagged = _stat(ROCK)
        del unflagged['flags']
        stats = {0x10: _stat(ROCK), 0x11: _stat(ROCK, flags=0),
                 0x12: _stat('', lod4=_far(ROCK)), 0x13: unflagged}
        refs = [_ref(0xA1, 0x10),
                _ref(0xA2, 0x11),
                _ref(0xA3, 0x10, parent_wrld=0x99, parent_cell=0x98),
                _ref(0xA4, 0x77),
                _ref(0xA5, 0x10, parent_cell=0x98),
                _ref(0xA6, 0x10, parent_wrld=0),
                _ref(0xA7, 0x12),
                _ref(0xA8, 0x13)]
        assert set(_rows(_write(gen, out, stats, refs))) == {0xA1, 0xA5, 0xA6}

    def test_nothing_listed_returns_none(self, gen, tmp_path, capsys):
        """No listable reference -> None, and the log says so."""
        out = tmp_path / 'AutoConvertLOD'
        stats = {0x11: _stat(ROCK, flags=0)}
        assert _write(gen, out, stats, [_ref(0xA2, 0x11)]) is None
        assert ("No LOD references found for worldspace 'TES4Tamriel'"
                in capsys.readouterr().out)

    def test_only_cells_lists_the_kept_tiles_footprint(self, gen, tmp_path,
                                                       capsys):
        """A changed cell keeps its level-4/8/16 tiles' cells, by floored position."""
        out = tmp_path / 'AutoConvertLOD'
        _mesh(out, ROCK, _far(ROCK))
        cells = [(0, 0), (5, 5), (15, 15), (-1, 0), (16, 0), (0, -1)]
        refs = [_ref(0xB0 + i, 0x10, cell=c) for i, c in enumerate(cells)]
        txt = _write(gen, out, {0x10: _stat(ROCK)}, refs,
                     only_cells={(0, 0)}, replace_tiles=True)
        log = capsys.readouterr().out
        assert set(_rows(txt)) == {0xB0, 0xB1, 0xB2}
        assert ('Restricted to the 256 cell(s) covered by the tiles this run '
                'keeps: 3 of 6 references listed') in log
        assert '(3 references)' in log

    def test_an_empty_only_cells_restricts_nothing(self, gen, tmp_path, capsys):
        """An empty set is "no restriction", not "list nothing"."""
        out = tmp_path / 'AutoConvertLOD'
        _mesh(out, ROCK, _far(ROCK))
        refs = [_ref(0xB0, 0x10), _ref(0xB1, 0x10, cell=(40, 40))]
        txt = _write(gen, out, {0x10: _stat(ROCK)}, refs, only_cells=set())
        assert set(_rows(txt)) == {0xB0, 0xB1}
        assert 'Restricted' not in capsys.readouterr().out

    def test_master_dirs_drop_a_shipped_base_unless_tiles_are_replaced(
            self, gen, tmp_path):
        """A base whose LOD a master ships is skipped only beside the master's tiles."""
        out, master = tmp_path / 'AutoConvertLOD', tmp_path / 'Master.esm'
        _mesh(out, ROCK, _far(ROCK), ROCK2, _far(ROCK2))
        _mesh(master, _far(ROCK))
        stats = {0x10: _stat(ROCK), 0x11: _stat(ROCK2, edid='Rock02')}
        refs = [_ref(0xC1, 0x10), _ref(0xC2, 0x11)]
        assert set(_rows(_write(gen, out, stats, refs))) == {0xC1, 0xC2}
        assert set(_rows(_write(gen, out, stats, refs,
                                master_dirs=[master]))) == {0xC2}
        assert set(_rows(_write(gen, out, stats, refs, master_dirs=[master],
                                replace_tiles=True))) == {0xC1, 0xC2}

    def test_master_mesh_dirs_stage_the_model_and_its_lod(self, gen, tmp_path):
        """A master-owned model is copied into the bake tree, with its `_far`."""
        out, master = tmp_path / 'AutoConvertLOD', tmp_path / 'Master.esm'
        (out / 'meshes').mkdir(parents=True)
        _mesh(master, ROCK3, _far(ROCK3))
        stats = {0x12: _stat(ROCK3)}
        refs = [_ref(0xD1, 0x12)]
        assert _write(gen, out, stats, refs) is None
        txt = _write(gen, out, stats, refs, master_mesh_dirs=[master])
        assert set(_rows(txt)) == {0xD1}
        rocks = out / 'meshes' / 'tes4' / 'rocks'
        assert (rocks / 'rock03.nif').exists()
        assert (rocks / 'rock03_far.nif').exists()
        assert len(gen._STAGED_MASTER_MESHES) == 2

    def test_an_unsafe_mesh_drops_its_base_and_is_named(self, gen, tmp_path,
                                                        capsys):
        """An unsafe full model or LOD mesh drops the base; the warning comes first."""
        out = tmp_path / 'AutoConvertLOD'
        _mesh(out, ROCK, _far(ROCK), _far(ROCK2), ROCK3)
        _mesh(out, ROCK2, _far(ROCK3), data=b'BAD')
        stats = {0x10: _stat(ROCK), 0x11: _stat(ROCK2), 0x12: _stat(ROCK3)}
        refs = [_ref(0xE1, 0x10), _ref(0xE2, 0x11), _ref(0xE3, 0x12)]
        assert set(_rows(_write(gen, out, stats, refs))) == {0xE1}
        log = capsys.readouterr().out
        assert 'WARNING: 2 LOD mesh(es) excluded' in log
        assert '    meshes\\tes4\\rocks\\rock02.nif\n' in log
        assert '    meshes\\tes4\\rocks\\rock03_far.nif\n' in log
        assert _write(gen, out, stats, refs[1:]) is None
        log = capsys.readouterr().out
        assert log.index('WARNING: 2 LOD') < log.index('No LOD references')

    def test_a_base_is_resolved_once_and_rows_keep_their_own_base(
            self, gen, tmp_path, monkeypatch):
        """Five refs of two listed bases and two of an unlisted one: three resolutions."""
        out = tmp_path / 'AutoConvertLOD'
        _mesh(out, ROCK, _far(ROCK), ROCK2, _far(ROCK2), ROCK3)
        stats = {0x10: _stat(ROCK), 0x11: _stat(ROCK2, edid='Rock02'),
                 0x12: _stat(ROCK3, edid='Rock03')}
        bases = [0x10, 0x11, 0x12, 0x10, 0x11, 0x12, 0x10]
        refs = [_ref(0xF0 + i, b) for i, b in enumerate(bases)]
        real, seen = gen._lod_meshes_for, []

        def spy(stat, *args):
            seen.append(stat['edid'])
            return real(stat, *args)
        monkeypatch.setattr(gen, '_lod_meshes_for', spy)
        rows = _rows(_write(gen, out, stats, refs))
        assert sorted(seen) == ['Rock01', 'Rock02', 'Rock03']
        assert {f: (r[9], r[12]) for f, r in rows.items()} == {
            0xF0 + i: (stats[b]['edid'], 'meshes\\' + stats[b]['model'])
            for i, b in enumerate(bases) if b != 0x12}

    def test_the_worldspace_is_matched_in_any_case_else_the_first_is_used(
            self, gen, tmp_path, capsys):
        """Case-blind EditorID match; an unknown name warns and uses the first."""
        out = tmp_path / 'AutoConvertLOD'
        _mesh(out, ROCK, _far(ROCK))
        stats, refs = {0x10: _stat(ROCK)}, [_ref(0xA1, 0x10)]
        assert set(_rows(_write(gen, out, stats, refs, 'tes4tamriel'))) == {0xA1}
        assert 'Warning' not in capsys.readouterr().out
        assert set(_rows(_write(gen, out, stats, refs, 'NoSuchWorld'))) == {0xA1}
        assert ("Warning: worldspace 'NoSuchWorld' not found, using "
                "'TES4Tamriel'") in capsys.readouterr().out
        assert gen.write_lodgen_input(Path('x.esm'), out, 'TES4Tamriel',
                                      _parsed=({}, {}, stats, refs)) is None
        assert 'Error: no worldspaces found in ESM' in capsys.readouterr().out

    def test_the_prefetch_gets_each_candidate_base_and_the_master_roots(
            self, gen, tmp_path, monkeypatch):
        """The warm-up is handed the LOD base's model and tier paths, and the masters."""
        out, master = tmp_path / 'AutoConvertLOD', tmp_path / 'Master.esm'
        _mesh(out, ROCK, _far(ROCK), ROCK2)
        stats = {0x10: _stat(ROCK), 0x11: _stat(ROCK2, flags=0)}
        refs = [_ref(0xA1, 0x10), _ref(0xA2, 0x10), _ref(0xA3, 0x11)]
        real, calls = gen._prescreen_meshes, []

        def spy(paths, *args, **kw):
            calls.append((list(paths), kw))
            return real(paths, *args, **kw)
        monkeypatch.setattr(gen, '_prescreen_meshes', spy)
        _write(gen, out, stats, refs, master_mesh_dirs=[master])
        paths, kw = calls[0]
        assert [p.replace('/', '\\') for p in paths] == [
            ROCK, _far(ROCK), _far(ROCK, '_far8'), _far(ROCK, '_far16')]
        assert kw == {'source_meshes': [master / 'meshes']}


DISABLED = 0x00000800
MARKER = 0x30
R = 0x01000D00
ON, OFF = 0x01000C01, 0x01000C02


class TestGameStartState:
    """A LOD reference the records leave off at game start gets no row."""

    def _listed(self, gen, tmp_path, refs):
        """Form ids `write_lodgen_input` lists; ON and OFF are non-LOD parent markers."""
        out = tmp_path / 'AutoConvertLOD'
        _mesh(out, ROCK, _far(ROCK))
        stats = {0x10: _stat(ROCK), MARKER: _stat('marker.nif', flags=0)}
        parents = [_ref(ON, MARKER), _ref(OFF, MARKER, flags=DISABLED)]
        return set(_rows(_write(gen, out, stats, parents + refs)))

    def test_the_own_flag_or_the_parent_chain_decides(self, gen, tmp_path,
                                                      capsys):
        """No parent: the own flag. A parent: its state, inverted by `opposite`."""
        refs = [_ref(R | 8, 0x10, xesp=(R | 5, False)),
                _ref(R | 9, 0x10, xesp=(R | 4, True)),
                _ref(R | 1, 0x10),
                _ref(R | 2, 0x10, flags=DISABLED),
                _ref(R | 3, 0x10, xesp=(ON, False)),
                _ref(R | 4, 0x10, xesp=(ON, True)),
                _ref(R | 5, 0x10, xesp=(OFF, True)),
                _ref(R | 6, 0x10, xesp=(OFF, False)),
                _ref(R | 7, 0x10, flags=LOD, xesp=(OFF, False)),
                _ref(R | 10, 0x10, flags=DISABLED, xesp=(ON, False))]
        assert self._listed(gen, tmp_path, refs) == {
            R | 1, R | 3, R | 5, R | 8, R | 9, R | 10}
        assert ('4 LOD-flagged reference(s) off at game start left out, '
                '0 kept with an unresolved enable parent'
                ) in capsys.readouterr().out

    def test_an_unresolved_parent_keeps_the_reference(self, gen, tmp_path,
                                                      capsys):
        """Unscanned parent, a self-parent and a cycle all stay listed.

        The player is always enabled, so the opposite of the player is off.
        """
        refs = [_ref(R | 1, 0x10, xesp=(0x7F000001, False)),
                _ref(R | 2, 0x10, flags=DISABLED, xesp=(0x00000014, True)),
                _ref(R | 3, 0x10, xesp=(R | 3, True)),
                _ref(R | 4, 0x10, xesp=(R | 5, False)),
                _ref(R | 5, 0x10, xesp=(R | 4, True)),
                _ref(R | 6, 0x10, xesp=(R | 4, False))]
        assert self._listed(gen, tmp_path, refs) == {R | i for i in range(1, 7)
                                                     if i != 2}
        assert ('1 LOD-flagged reference(s) off at game start left out, '
                '5 kept with an unresolved enable parent'
                ) in capsys.readouterr().out

    def test_the_parent_is_found_by_its_full_id(self, gen, tmp_path):
        """Records of several files share a local id; the named one is read, or none."""
        twin_on, twin_off = 0x01000C05, 0x02000C05
        refs = [_ref(twin_on, MARKER), _ref(twin_off, MARKER, flags=DISABLED),
                _ref(0x00000C06, MARKER, flags=DISABLED),
                _ref(R | 1, 0x10, xesp=(twin_on, False)),
                _ref(R | 2, 0x10, xesp=(twin_off, False)),
                _ref(R | 3, 0x10, xesp=(0x05000C06, False))]
        assert self._listed(gen, tmp_path, refs) == {R | 1, R | 3}

    def test_each_call_reads_the_records_afresh(self, gen, tmp_path):
        """The same ids with another parent state give another answer."""
        child = _ref(R | 1, 0x10, xesp=(R | 2, False))
        off = [child, _ref(R | 2, MARKER, flags=DISABLED)]
        on = [child, _ref(R | 2, MARKER)]
        assert self._listed(gen, tmp_path, off) == set()
        assert self._listed(gen, tmp_path, on) == {R | 1}

    def test_a_base_placed_only_by_an_off_reference_is_not_resolved(
            self, gen, tmp_path, monkeypatch):
        """Its meshes are neither prefetched nor resolved; the other base's are."""
        out = tmp_path / 'AutoConvertLOD'
        _mesh(out, ROCK, _far(ROCK), ROCK2, _far(ROCK2))
        stats = {0x10: _stat(ROCK), 0x11: _stat(ROCK2, edid='Rock02')}
        refs = [_ref(R | 1, 0x10, flags=DISABLED), _ref(R | 2, 0x11)]
        warm, resolved = gen._prescreen_meshes, gen._lod_meshes_for
        seen = {'warm': [], 'resolved': []}

        def spy_warm(paths, *args, **kw):
            seen['warm'] += [p.replace('/', '\\') for p in paths]
            return warm(paths, *args, **kw)

        def spy_resolved(stat, *args):
            seen['resolved'].append(stat['edid'])
            return resolved(stat, *args)
        monkeypatch.setattr(gen, '_prescreen_meshes', spy_warm)
        monkeypatch.setattr(gen, '_lod_meshes_for', spy_resolved)
        assert set(_rows(_write(gen, out, stats, refs))) == {R | 2}
        assert seen['resolved'] == ['Rock02']
        assert ROCK2 in seen['warm'] and ROCK not in seen['warm']


class TestEnableParentChain:
    """The state of a chain is the same whichever of its references is asked first."""

    @staticmethod
    def _chain(n, opposite=False):
        """{id: ref}: n refs from R up, each enabled by the next, the last by none."""
        refs = [_ref(R + i, 0x10, xesp=(R + i + 1, opposite))
                for i in range(n - 1)] + [_ref(R + n - 1, 0x10)]
        return {r['form_id']: r for r in refs}

    def test_walk_order_does_not_change_a_deep_refs_state(self, gen):
        """Ref 17 of a 21-ref chain is on, asked first or after the chain's head."""
        by_fid, alone, after = self._chain(21), {}, {}
        gen._initially_enabled(R, by_fid, after)
        assert gen._initially_enabled(R + 17, by_fid, alone) is True
        assert gen._initially_enabled(R + 17, by_fid, after) is True

    def test_a_long_chain_is_resolved_link_by_link(self, gen):
        """40 refs, every link `opposite`: the state alternates down from the last."""
        by_fid, state = self._chain(40, opposite=True), {}
        assert gen._initially_enabled(R, by_fid, state) is False
        assert [state[R + i] for i in (36, 37, 38, 39)] == [
            False, True, False, True]

    def test_an_opposite_bit_applies_from_its_own_link_down(self, gen):
        """A is a plain child of B, B the opposite of C, C is on: A and B are off."""
        refs = [_ref(R | 1, 0x10, xesp=(R | 2, False)),
                _ref(R | 2, 0x10, xesp=(R | 3, True)), _ref(R | 3, 0x10)]
        state = {}
        gen._initially_enabled(R | 1, {r['form_id']: r for r in refs}, state)
        assert state == {R | 1: False, R | 2: False, R | 3: True}

    def test_a_cycle_is_unknown_from_every_entry(self, gen):
        """A and B enable each other; both, and a ref hanging off them, are unknown."""
        refs = [_ref(R | 1, 0x10, xesp=(R | 2, False)),
                _ref(R | 2, 0x10, xesp=(R | 1, True)),
                _ref(R | 3, 0x10, xesp=(R | 1, False))]
        by_fid = {r['form_id']: r for r in refs}
        for first in by_fid:
            state = {}
            gen._initially_enabled(first, by_fid, state)
            assert [gen._initially_enabled(f, by_fid, state)
                    for f in by_fid] == [None, None, None]
