#!/usr/bin/env python3
"""Audit generated behavior graphs for BGSGamebryoSequenceGenerator problems.

Why this exists: the generator resolves `pSequence` BY NAME against the NIF's
NiControllerManager sequence map at runtime.  A name that resolves to nothing
gives a NULL sequence, which the engine dereferences the moment that state is
entered -- `movdqu xmm2,[rax]` with rax=0 inside VCRUNTIME140, under
BGSGamebryoSequenceGenerator (crash-2026-08-10-01-08-13).

Because the state machine STARTS in the Rest state, an unresolvable name there
crashes on the object's very first animation.

Three checks, calibrated against vanilla (51 Behavior00.hkx that use a
Gamebryo generator, extracted from Skyrim - Animations.bsa: 0 violations):

  1. every NON-Rest generator names a sequence the sibling NIF declares,
     and never an empty name (that null sequence is deref'd on activation)
  2. the Rest generator's pSequence is EMPTY.  Empty is CORRECT there --
     nothing should play on cell load, and the working doors ship exactly
     this (see "The Rest state is CORRECT" in docs/commentary/asset_convert_nif.md).
     A Rest naming a real sequence animates the object on load (the
     self-opening secret doors)
  3. no sequence in a graph-carrying NIF has an EMPTY text key value.  On
     activation the generator walks NiTextKeyExtraData and `strchr`s each
     value for '.' (GOG exe 0x505130, AddrLib ID 32774); an empty NiString
     loads as a NULL BSFixedString and the strchr crashes — the Spiddal
     Stick / Harrada Root CTDs (crash-2026-08-10-01-41-07 / -01-39-02).
     Vanilla ships empty keys ONLY on graph-less meshes (impjaildoor01,
     ruinscanopicjar02), never beside a behavior graph.

Usage:
    python tools/validate/gamebryo_seq_check.py <output-meshes-dir> [--quiet]
        [--expect-checked N] [--expect-end-graphs N] [--expect-holds N]
    python tools/validate/gamebryo_seq_check.py <output-meshes-dir> --build-gate

The build runs `build_gate` itself, after the mesh step and before any pack.

Each tree is paired with its NIF and its four members IGNORING CASE; a tree
that cannot be paired, lacks a member, or whose NIF or graph cannot be read
(no generators, no Rest generator) is a violation.  So is a leftover of an
interrupted build (`_hkxstage` / `_hkxaside` folder, `.ni~` file), a NIF
whose BGED names a `_behavior` project that does not exist, and ANY `.nif`
under the root whose header cannot be read (not a NIF, or cut off inside its
header): what it names cannot be determined, so it is refused, not passed.  Exit code is 0
only when at least one project was checked, there is NO violation, and every
--expect-* count matches; matching counts never excuse a violation.

NOT READ: transition rows.  Everything here comes from the graph's string
pool, so a graph whose hold states exist but which has no `End` row to reach
them passes.  The counts say holds are present, not that they are reachable.
See: docs/commentary/asset_convert_animation.md#graph-and-nif-move-together
"""
import argparse
import collections
import logging
import os
import re
import struct
import sys
import time
import warnings


#: Folder suffix of a generated project tree, compared in lower case.
_TREE_SUFFIX = '_behavior'

#: Generator name prefix of a pose-hold state.
_HOLD_GENERATOR = 'GamebryoSequenceGeneratorHold'

#: The graph event a NIF `end` text key raises, as the string pool stores it.
_END_EVENT = b'End'

#: A project tree's members, posix and lower case; the first is the behaviour graph.
_MEMBERS = ('behaviors/behavior00.hkx', 'characters/character01.hkx',
            'characterassets/skeleton.hkx', '{stem}.hkx')

#: Folder suffixes and NIF suffix an interrupted build leaves behind, lower case.
_LEFTOVER_DIRS = ('_hkxstage', '_hkxaside')
_LEFTOVER_NIF = '.ni~'

#: How every NIF begins, whichever engine version wrote it.
_NIF_MAGIC = (b'Gamebryo File Format', b'NetImmerse File Format')

#: The NIF version whose header layout `_header_strings` knows (Skyrim LE and SE).
_NIF_VERSION = 0x14020007

#: A BGED path naming a generated project: `...<stem>_behavior\<stem>.hkx`.
_PROJECT_PATH = re.compile(rb'[\x20-\x7e]*_behavior[\\/][\x20-\x7e]*?\.hkx', re.I)

#: (totals key, printed label) of the counts a run reports and can be held to.
_COUNTS = (('checked', 'behavior projects checked'),
           ('end_graphs', 'graphs declaring End'),
           ('holds', 'hold sequences'))


def _read_quietly(nif_path):
    """(parsed NIF, None), or (None, why it could not be parsed).

    pyffi logs a full traceback for a block it cannot read; logging is held
    off for the read so the failure reaches the output as one violation line.
    """
    from asset_convert.nif import sse_nif
    held = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    try:
        return sse_nif.read_nif(nif_path), None
    except Exception as exc:
        return None, f'{type(exc).__name__}: {exc}'
    finally:
        logging.disable(held)


def _nif_sequences(nif_path):
    """(declared sequence names, [(seq name, empty text key count), ...]).

    (None, the reason) when the NIF cannot be parsed.
    """
    from pyffi.formats.nif import NifFormat
    data, why = _read_quietly(nif_path)
    if data is None:
        return None, why
    names = set()
    empty = []
    for root in data.roots:
        for block in root.tree():
            if isinstance(block, NifFormat.NiControllerManager):
                for seq in (block.controller_sequences or ()):
                    if seq is None:
                        continue
                    sname = bytes(seq.name or b'').decode('latin-1')
                    names.add(sname)
                    tk = getattr(seq, 'text_keys', None)
                    if tk is not None:
                        n = sum(1 for k in tk.text_keys
                                if not bytes(k.value or b'').strip())
                        if n:
                            empty.append((sname, n))
    return names, empty


def _graph_sequences(hkx_path):
    """(generator name, pSequence) pairs from the packed behavior graph.

    Binary hkx pools its strings NUL-separated, so split on NUL rather than
    scanning for printable runs: an EMPTY pSequence leaves no printable bytes
    at all, and a printable-run scan silently skips it -- which is exactly the
    defect this tool exists to catch, so that version reported 0 violations on
    a file built with the bug deliberately injected.
    """
    with open(hkx_path, 'rb') as fh:
        blob = fh.read()
    parts = [p.decode('latin-1', 'replace') for p in blob.split(b'\x00')]
    out = []
    for i, s in enumerate(parts):
        if not s.startswith('GamebryoSequenceGenerator'):
            continue
        # The pool pads with NULs, so the sequence name is the next NON-EMPTY
        # string -- but only within a short window, because a generator whose
        # pSequence really IS empty must report '' rather than running on and
        # picking up the next generator's name.  Vanilla-shaped files put it
        # within ~6 slots (measured: +5 for numbered generators, +3 for Rest).
        nxt = ''
        for j in range(i + 1, min(i + 8, len(parts))):
            cand = parts[j]
            if not cand:
                continue
            if cand.startswith('GamebryoSequenceGenerator'):
                break        # ran into the next generator: this one is empty
            nxt = cand
            break
        out.append((s, nxt))
    return out


def _find_ci(folder, name):
    """Paths in `folder` whose entry name equals `name`, ignoring case."""
    try:
        entries = os.listdir(folder)
    except OSError:
        return []
    return [os.path.join(folder, e) for e in sorted(entries)
            if e.lower() == name.lower()]


def _find_member(tree, rel):
    """Paths of the tree member `rel` (posix, lower case), ignoring case."""
    found = [tree]
    for part in rel.split('/'):
        found = [hit for folder in found for hit in _find_ci(folder, part)]
    return found


def _pair(base, sub):
    """(nif, behavior graph, problems) for the project tree `sub` under `base`.

    The NIF and all four tree members are matched ignoring case, as the engine
    does: a mod ships `Gate.NIF`, and a BSA extract or loose deploy lowercases
    `behaviors/behavior00.hkx`.  An unpaired or ambiguous tree, or one missing
    its project, character or skeleton file, is a problem, never a silent skip.
    """
    stem = sub[:-len(_TREE_SUFFIX)]
    nifs = _find_ci(base, stem + '.nif')
    problems = []
    if len(nifs) != 1:
        problems.append(f'{len(nifs)} NIF(s) named {stem}.nif beside the tree '
                        f'(need exactly 1)')
    members = {rel: _find_member(os.path.join(base, sub), rel.format(stem=stem))
               for rel in _MEMBERS}
    problems += [f'{len(hits)} {rel.format(stem=stem)} in the tree '
                 f'(need exactly 1)'
                 for rel, hits in members.items() if len(hits) != 1]
    graphs = members[_MEMBERS[0]]
    return (nifs[0] if len(nifs) == 1 else None,
            graphs[0] if len(graphs) == 1 else None, problems)


def _generator_problems(declared, generators):
    """What is wrong with each (generator, pSequence) against the NIF's names.

    Empty is CORRECT on Rest and a null deref on any other generator; a Rest
    naming a real sequence plays it on cell load; any other generator, a hold
    included, must name a sequence the NIF declares.
    """
    problems = []
    for gen, seq in generators:
        is_rest = gen.endswith('Rest')
        if not seq and not is_rest:
            problems.append(f'{gen}: EMPTY pSequence (null deref)')
        elif seq and is_rest and seq in declared:
            problems.append(
                f'{gen}: names REAL sequence {seq!r} -- plays on load')
        elif seq and not is_rest and seq not in declared:
            problems.append(
                f'{gen}: names {seq!r}, NIF has {sorted(declared)}')
    return problems


def _declares_end(hkx_path):
    """Whether the graph's string pool holds the `End` event."""
    with open(hkx_path, 'rb') as fh:
        return _END_EVENT in fh.read().split(b'\x00')


def check_project(base, sub):
    """One project tree's verdict: problems, End declared, holds, NIF names.

    An unreadable NIF is a problem: a graph beside a mesh nobody could inspect
    is not known to be safe.  So is a behaviour file that yields no generator,
    or no Rest generator: nothing was read from it.
    """
    verdict = {'problems': [], 'end': False, 'holds': 0, 'declared': []}
    nif, hkx, verdict['problems'] = _pair(base, sub)
    if verdict['problems']:
        return verdict
    declared, empty_keys = _nif_sequences(nif)
    if declared is None:
        verdict['problems'] = [f'NIF cannot be read: {nif}: {empty_keys}']
        return verdict
    generators = _graph_sequences(hkx)
    if not any(gen.endswith('Rest') for gen, _seq in generators):
        verdict['problems'] = [
            f'{len(generators)} generator(s) and no Rest generator read from '
            f'{hkx}: not a graph this tool can vouch for']
        return verdict
    holds = [gen for gen, _seq in generators if gen.startswith(_HOLD_GENERATOR)]
    verdict.update(end=_declares_end(hkx), holds=len(holds),
                   declared=sorted(declared))
    verdict['problems'] = [
        f'sequence {sname!r}: {n} EMPTY text key value(s) '
        f'(NULL BSFixedString -> strchr crash on activation)'
        for sname, n in empty_keys] + _generator_problems(declared, generators)
    if holds and not verdict['end']:
        verdict['problems'].append(
            f'{len(holds)} hold generator(s) but no End event to reach them')
    return verdict


def _take(fh, size):
    """The next `size` bytes of `fh`; ValueError when the file ends first."""
    data = fh.read(size)
    if len(data) != size:
        raise ValueError('the file ends inside its header')
    return data


def _header_strings(fh):
    """The header string table of an open 20.2.0.7 NIF; None for another version.

    ValueError when the file does not start with a NIF header, or when a
    20.2.0.7 header cannot be read to the end of its string table.
    """
    if not fh.readline(96).startswith(_NIF_MAGIC):
        raise ValueError('not a NIF: no file-format line')
    if struct.unpack('<I', _take(fh, 4))[0] != _NIF_VERSION:
        return None
    blocks = struct.unpack('<BIII', _take(fh, 13))[2]
    for _ in range(3):
        _take(fh, _take(fh, 1)[0])
    for _ in range(struct.unpack('<H', _take(fh, 2))[0]):
        _take(fh, struct.unpack('<I', _take(fh, 4))[0])
    _take(fh, 6 * blocks)
    count, longest = struct.unpack('<II', _take(fh, 8))
    strings = []
    for _ in range(count):
        size = struct.unpack('<I', _take(fh, 4))[0]
        if size > longest:
            raise ValueError('a header string is longer than the header allows')
        strings.append(_take(fh, size))
    return strings


def bged_projects(nif_path):
    """Generated-project paths a NIF's BGED names, read without parsing blocks.

    A 20.2.0.7 NIF keeps the BGED's path in its header string table, so only
    the header is read; another NIF version is searched whole.  Raises OSError
    or ValueError when the answer cannot be determined: a file that is not a
    NIF, or one cut off inside its header, names nothing anyone can vouch for.
    """
    with open(nif_path, 'rb') as fh:
        strings = _header_strings(fh)
        if strings is None:
            fh.seek(0)
            strings = _PROJECT_PATH.findall(fh.read())
    return [m.group(0).decode('latin-1') for m in map(_PROJECT_PATH.search, strings)
            if m]


def _project_exists(nif_path, project):
    """Whether the project file a BGED names exists, ignoring case.

    The path is relative to a `meshes` folder above the NIF; when there is no
    such folder the tree is looked for beside the NIF.
    """
    parts = project.replace('/', '\\').split('\\')
    folder = os.path.dirname(os.path.abspath(nif_path))
    roots = []
    here = folder
    while os.path.dirname(here) != here:
        if os.path.basename(here).lower() == 'meshes':
            roots.append((here, '/'.join(parts).lower()))
        here = os.path.dirname(here)
    roots.append((folder, '/'.join(parts[-2:]).lower()))
    return any(_find_member(base, rel) for base, rel in roots)


def _nif_problems(nif_path, totals):
    """Problems with one NIF: unreadable, or its BGED names a missing project."""
    try:
        projects = bged_projects(nif_path)
    except (OSError, ValueError) as exc:
        return [f'NIF cannot be read: {nif_path}: {exc}']
    totals.update(nifs=1, nifs_with_project=int(bool(projects)))
    return [f'BGED names {project!r}, which does not exist (object not drawn)'
            for project in projects if not _project_exists(nif_path, project)]


def _loose_problems(base, dirs, files, totals):
    """[(label, problems)] for what is under `base` that is not a project tree.

    Leftovers of an interrupted build (a staging or set-aside folder, a
    half-written NIF) get packed like any other file, and a NIF whose BGED
    names a missing project is not drawn; no tree walk would see either.
    """
    found = [(os.path.join(base, d), ['leftover folder of an interrupted build'])
             for d in sorted(dirs) if d.lower().endswith(_LEFTOVER_DIRS)]
    for name in sorted(files):
        path = os.path.join(base, name)
        if name.lower().endswith(_LEFTOVER_NIF):
            found.append((path, ['leftover half-written NIF']))
        elif name.lower().endswith('.nif'):
            problems = _nif_problems(path, totals)
            if problems:
                found.append((path, problems))
    return found


def _report(label, verdict, totals, quiet):
    """Print one verdict and count it as a violation if it has problems."""
    totals.update(violations=int(bool(verdict['problems'])))
    if verdict['problems']:
        print(f'  BAD {label}')
        print(''.join(f'      {p}\n' for p in verdict['problems']), end='')
    elif not quiet:
        print(f'  ok  {label}: {verdict["declared"]}')


def _named_trees(base, files):
    """Lower-case names of the project folders the NIFs in `base` name in a BGED.

    This is what makes a `_behavior` folder one of OURS: a converted NIF beside
    it points at it.  A NIF whose header cannot be read names nothing here, so
    a tree beside it may be skipped as unnamed; `_nif_problems` makes that NIF
    a violation in the same walk, so the run fails all the same.
    """
    named = set()
    for name in files:
        if not name.lower().endswith('.nif'):
            continue
        try:
            projects = bged_projects(os.path.join(base, name))
        except (OSError, ValueError):
            continue
        named.update(p.replace('/', '\\').split('\\')[-2].lower() for p in projects)
    return named


def audit(root, quiet=False, named_only=False):
    """Check every project tree, NIF and leftover under `root`; the totals.

    `named_only` leaves out, and counts as `unnamed`, any `_behavior` folder
    no NIF beside it names: the build gate must not judge a tree the converter
    did not generate, or one a mesh no longer uses.
    Keys: checked, end_graphs, holds, violations, nifs, nifs_with_project,
    unnamed.
    """
    totals = collections.Counter()
    for base, dirs, files in os.walk(root):
        subs = sorted(d for d in dirs if d.lower().endswith(_TREE_SUFFIX))
        named = _named_trees(base, files) if named_only and subs else None
        for sub in subs:
            if named is not None and sub.lower() not in named:
                totals.update(unnamed=1)
                print(f'  skip {os.path.join(base, sub)}: no NIF beside it '
                      f'names it')
                continue
            verdict = check_project(base, sub)
            totals.update(checked=1, end_graphs=int(verdict['end']),
                          holds=verdict['holds'])
            _report(os.path.join(base, sub[:-len(_TREE_SUFFIX)]), verdict,
                    totals, quiet)
        for label, problems in _loose_problems(base, dirs, files, totals):
            _report(label, {'problems': problems}, totals, quiet)
    return totals


def build_gate(root):
    """The automatic build gate over a meshes tree: the totals, problems printed.

    Passes when `violations` is 0.  Unlike a hand run it judges only projects
    a converted NIF names, and a tree with no animated object at all passes:
    a texture-only plugin is not a failure.
    See: docs/commentary/asset_convert_animation.md#build-gate
    """
    totals = audit(root, quiet=True, named_only=True)
    print('  animated objects: '
          + '   '.join(f'{text}: {totals[key]}' for key, text in _COUNTS)
          + f'   not named by a NIF: {totals["unnamed"]}'
          + f'   violations: {totals["violations"]}')
    return totals


def _parser():
    """The command line."""
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('root')
    ap.add_argument('--quiet', action='store_true')
    ap.add_argument('--build-gate', action='store_true',
                    help='judge as the build does: only projects a NIF names, '
                         'and no project at all is a pass')
    for key, text in _COUNTS:
        ap.add_argument('--expect-' + key.replace('_', '-'), type=int,
                        metavar='N', help=f'fail unless {text} == N')
    return ap


def main(argv=None):
    """Audit a meshes tree; 0 only if something was checked and all is clean.

    A run that inspected nothing proves nothing, so `checked == 0` fails, and
    so does any `--expect-*` count the tree does not match.
    """
    args = _parser().parse_args(argv)
    if args.build_gate:
        return 1 if build_gate(args.root)['violations'] else 0
    totals = audit(args.root, args.quiet)
    print('\n' + '   '.join(f'{text}: {totals[key]}' for key, text in _COUNTS)
          + f'   violations: {totals["violations"]}')
    mismatched = [
        f'{text}: expected {getattr(args, "expect_" + key)}, found {totals[key]}'
        for key, text in _COUNTS
        if getattr(args, 'expect_' + key) not in (None, totals[key])]
    print(f'NIFs read for a BGED: {totals["nifs"]}   naming a generated '
          f'project: {totals["nifs_with_project"]}')
    for line in mismatched:
        print(f'  COUNT MISMATCH {line}')
    if not totals['checked']:
        print('  NOTHING CHECKED -- no <stem>_behavior tree under this root')
    clean = totals['checked'] and not totals['violations'] and not mismatched
    return 0 if clean else 1


def _run_as_script():
    """Quiet pyffi and make the repo importable, then run `main`."""
    warnings.filterwarnings('ignore')
    if not hasattr(time, '_original_clock'):
        time.clock = time.perf_counter
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))))
    return main()


if __name__ == '__main__':
    sys.exit(_run_as_script())
