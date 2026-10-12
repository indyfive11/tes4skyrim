"""How faithfully each TES4 script command converts to Papyrus, by call site.

Collects every command call in a plugin's object scripts, dialogue results and
stage results, samples up to N per command, and converts each sample with the
real converter: the statement inside a cut-down copy of its own script, after
the pipeline's own context build (`build_script_context`, run into a throwaway
folder) and per-record priming (`primed_converter`). The lines the statement
adds are classified, and flagged when they emit tick machinery.

Usage:
    python -m tools.script.command_fidelity_audit --export export/Oblivion.esm
    python -m tools.script.command_fidelity_audit --export A --export B --samples 8 \\
        --tsv out.tsv --markdown table.md

See: docs/audits/script_command_fidelity.md#method
"""

import argparse
import json
import os
import re
import shutil
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass

from asset_convert.game_paths import namespace_for, set_namespace
from output_layout import plugin_out_root
from script_convert import pipeline
from script_convert.command_rows import KNOWN_COMMANDS
from script_convert.constants import sanitize_name
from script_convert.quest_fragments import stage_fragments
from script_convert.tes4.nodes import (Call, Ident, If, Member, While,
                                       walk_expr, walk_stmts)
from script_convert.tes4.parser import Mode, parse
from tes5_import.base.text_reader import info_result_script

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ENGINE_TABLES = os.path.join(ROOT, 'tes4_export', 'oblivion_engine_tables.json')
TICKED_BLOCKS = frozenset({'gamemode', 'menumode', 'scripteffectupdate'})
CLASSES = ('native', 'inline', 'polyfill', 'partial', 'constant', 'disabled',
           'dropped', 'error')
#: Tie-break for a command's verdict: on equal sample counts the worse class wins.
WORST_FIRST = ('error', 'dropped', 'disabled', 'constant', 'partial', 'polyfill',
               'inline', 'native')
#: A variable the probe declares and sets in BOTH copies, so a block's own scaffolding cancels.
PAD = 'TES4AuditPad'

_MARKER = re.compile(r';\s*(NE|TODO):')
#: Code the converter emitted but commented out, as it does for a whole disabled block.
_COMMENTED_CODE = re.compile(r'^\s*;\s+(?:If\b|ElseIf\b|EndIf\b|[\w.]+\(|[\w.]+\s*=[^=])')
#: A condition or assignment whose whole value is a literal: a fixed stand-in for a live read.
_CONSTANT = re.compile(r'^(?:(?:Else)?If\s*\(*\s*(?:-?\d+(?:\.\d+)?|True|False)\s*\)*'
                       r'(?:\s*(?:==|!=|<=?|>=?)\s*\(*\s*-?[\w.]+\s*\)*)?|'
                       r'[\w.]+\s*=\s*\(*\s*(?:-?\d+(?:\.\d+)?|True|False|None)\s*\)*)$',
                       re.I)
_POLYFILL = re.compile(r'\bTES4Polyfill\.|\bTES4_\w+\.\w+\(|SendModEvent\(')
_TICK = re.compile(r'RegisterForSingleUpdate|SafeGameModeGate|TES4_LastStage_|'
                   r'SayLine|SpinAxis|GlideAxis|TES4_SecondsPassed|'
                   r'PlayerIsInDialogue|IsInDialogueWithPlayer')
_CALL = re.compile(r'\b(?!If\b|ElseIf\b|While\b|Return\b)[A-Za-z_]\w*\s*\(')
_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
_IF_HEAD = re.compile(r'^\s*(?:else)?if\b', re.I)
_WHILE_HEAD = re.compile(r'^\s*while\b', re.I)
_SCN = re.compile(r'^\s*(?:scn|scriptname)\b', re.I)
#: Lines that frame or declare rather than act: never evidence of a conversion.
_FRAME = re.compile(r'^(?:\w+\s+Property\b|(?!If\b|ElseIf\b|Return\b)\w+\s+\w+(?:\s*=.*)?$|'
                    r'(?:End)?(?:Event|Function|If|While)\b(?!\s*\S)|Else$|'
                    r'(?:Event|Function)\s)')


# --------------------------------------------------------------------------
# Command names and call sites
# --------------------------------------------------------------------------


@dataclass
class Site:
    """One call of one command: where it is and the source a probe needs."""
    plugin: str
    kind: str
    rec: dict
    block: tuple
    line_text: str
    wrap: str
    ticked: bool
    only: bool


def command_names() -> dict:
    """Lowercased long name or alias -> canonical name (engine table + converter)."""
    names = {c.lower(): c.lower() for c in KNOWN_COMMANDS}
    with open(ENGINE_TABLES, encoding='utf-8') as f:
        functions = json.load(f)['functions']
    for fn in functions:
        long_name = fn['name']
        names[long_name.lower()] = long_name
        if fn.get('description'):
            names[fn['description'].lower()] = long_name
    return names


def _commands_in(exprs, names: dict, variables: set) -> set:
    """Canonical command names called anywhere in `exprs`."""
    found = set()
    for top in exprs:
        for node in walk_expr(top):
            low = getattr(node, 'name', '').lower()
            if not low or low in variables:
                continue
            if isinstance(node, Call) and (low in names or node.args):
                found.add(names.get(low, low))
            elif isinstance(node, (Ident, Member)) and low in names:
                found.add(names[low])
    return found


def _headers(stmt) -> list:
    """(line, condition-or-expressions, wrap) for each source line `stmt` owns."""
    if isinstance(stmt, If):
        heads = [(stmt.line, [stmt.cond], 'if')]
        return heads + [(line, [cond], 'if') for cond, _body, line in stmt.elifs]
    if isinstance(stmt, While):
        return [(stmt.line, [stmt.cond], 'while')]
    exprs = [getattr(stmt, a, None) for a in ('expr', 'value', 'target')]
    return [(stmt.line, [e for e in exprs if e is not None], '')]


def _body_sites(body, ctx: dict, out: dict) -> None:
    """Add a Site per command per source line in `body` to `out`."""
    lines, names, variables = ctx['lines'], ctx['names'], ctx['variables']
    for stmt in walk_stmts(body):
        for line, exprs, wrap in _headers(stmt):
            found = _commands_in(exprs, names, variables)
            if not found or not 0 < line <= len(lines):
                continue
            for cmd in found:
                out[cmd].append(Site(ctx['plugin'], ctx['kind'], ctx['rec'],
                                     ctx['block'], lines[line - 1], wrap,
                                     ctx['ticked'], len(found) == 1))


def _script_sites(plugin: str, rec: dict, names: dict, out: dict) -> None:
    """Sites in one SCPT body, block by block."""
    source = rec.get('SCTX', '')
    tree = parse(source)
    ctx = {'plugin': plugin, 'kind': 'scpt', 'rec': rec, 'names': names,
           'lines': source.splitlines(),
           'variables': {v.name.lower() for v in tree.variables}}
    for block in tree.blocks:
        ctx.update(block=(block.btype, block.filter),
                   ticked=block.btype.lower() in TICKED_BLOCKS)
        _body_sites(block.body, ctx, out)


def _fragment_sites(plugin: str, kind: str, rec: dict, source: str,
                    names: dict, out: dict) -> None:
    """Sites in one dialogue or stage result script."""
    tree = parse(source, Mode.FRAGMENT)
    ctx = {'plugin': plugin, 'kind': kind, 'rec': rec, 'names': names,
           'lines': source.splitlines(), 'block': ('', ''), 'ticked': False,
           'variables': {v.name.lower() for v in tree.variables}}
    _body_sites(tree.body, ctx, out)


def collect_sites(plugin: str, records: dict, names: dict) -> dict:
    """Canonical command -> every Site in the plugin's three script corpora."""
    out = defaultdict(list)
    for rec in records['SCPT']:
        if rec.get('SCTX', '').strip():
            _script_sites(plugin, rec, names, out)
    for rec in records['INFO']:
        source = info_result_script(rec)
        if source.strip():
            _fragment_sites(plugin, 'info', rec, source, names, out)
    for rec in records['QUST']:
        for frag in stage_fragments(rec):
            if frag[3].strip():
                _fragment_sites(plugin, 'stage', rec, frag[3], names, out)
    return out


def sample(sites: list, n: int) -> list:
    """Up to `n` evenly spaced sites, from single-command statements when enough exist."""
    single = [s for s in sites if s.only]
    pool = single if len(single) >= n else single + [s for s in sites if not s.only]
    if len(pool) <= n:
        return pool
    step = len(pool) / n
    return [pool[int(i * step)] for i in range(n)]


# --------------------------------------------------------------------------
# Probing: convert the statement in context, keep what it added
# --------------------------------------------------------------------------


def _statement(site: Site) -> list:
    """The probed source line(s), wrapped so an `if`/`while` head stands alone."""
    text = site.line_text.strip()
    if site.wrap == 'if':
        return ['if ' + _IF_HEAD.sub('', text, count=1).strip(), 'endif']
    if site.wrap == 'while':
        return ['while ' + _WHILE_HEAD.sub('', text, count=1).strip(), 'loop']
    return [text]


def _script_source(site: Site, body: list) -> str:
    """A cut-down copy of the site's script: header, variables, one block."""
    source = site.rec.get('SCTX', '')
    lines = source.splitlines()
    head = [ln for ln in lines if _SCN.match(ln)][:1]
    decls = [lines[v.line - 1] for v in parse(source).variables
             if 0 < v.line <= len(lines)]
    begin = f'begin {site.block[0]} {site.block[1]}'.rstrip()
    return '\n'.join(head + decls + [f'short {PAD}', begin, f'set {PAD} to 1']
                     + body + ['end'])


def _convert(site: Site, xref, body: list) -> list:
    """Papyrus lines for `body` placed where the site's statement was."""
    rec, fid = site.rec, site.rec.get('FormID', '')
    if site.kind == 'scpt':
        conv = pipeline.primed_converter(rec, xref, rec.get('SCTX', ''))
        edid = rec.get('EditorID', '')
        return conv.convert_standalone(sanitize_name(edid or f'Script_{fid}'),
                                       _script_source(site, body),
                                       xref.get_extends_class(fid), edid).splitlines()
    if site.kind == 'info':
        conv = pipeline.primed_converter(rec, xref, info_result_script(rec))
        return conv.convert_fragment('\n'.join(body), 'TopicInfo')
    conv = pipeline.primed_converter(rec, xref, '')
    return conv.convert_fragment('\n'.join(body), 'Quest')


def _code(line: str) -> str:
    """The acting code on a line: no comment, string, declaration or frame."""
    code = _STRING.sub('""', line).split(';', 1)[0].strip()
    return '' if _FRAME.match(code) else code


def classify(added: list, removed: list) -> tuple:
    """(class, tick flag) for the lines one statement added and removed."""
    code = [_code(ln) for ln in added if _code(ln)]
    marked = any(_MARKER.search(ln) for ln in added)
    tick = any(_TICK.search(ln) for ln in added + removed)
    if not code and any(_COMMENTED_CODE.match(ln) for ln in added):
        return 'disabled', tick
    if not code:
        return 'dropped', tick
    if marked:
        return 'partial', tick
    if any(_POLYFILL.search(c) for c in code):
        return 'polyfill', tick
    if any(_CALL.search(c) for c in code):
        return 'native', tick
    if all(_CONSTANT.match(c) for c in code):
        return 'constant', tick
    return 'inline', tick


def probe(site: Site, xref, baselines: dict) -> tuple:
    """(class, tick flag, emitted code) for one sampled site; a converter crash is 'error'."""
    key = (site.kind, site.rec.get('FormID'), site.block)
    try:
        if key not in baselines:
            baselines[key] = _convert(site, xref, [])
        out = _convert(site, xref, _statement(site))
    except Exception as exc:
        return 'error', False, f'{type(exc).__name__}: {exc}'[:160]
    base = Counter(baselines[key])
    added = list((Counter(out) - base).elements())
    removed = list((base - Counter(out)).elements())
    kind, tick = classify(added, removed)
    shown = ' | '.join(ln.strip() for ln in sorted(
        (ln for ln in added if _code(ln) or _MARKER.search(ln)), key=_salience))
    return kind, tick, shown[:200]


def _salience(line: str) -> int:
    """Sort key: markers and polyfill calls first, then calls, then the rest."""
    if _MARKER.search(line) or _POLYFILL.search(line):
        return 0
    return 1 if _CALL.search(_code(line)) else 2


# --------------------------------------------------------------------------
# Aggregation and output
# --------------------------------------------------------------------------


def _new_row() -> dict:
    """An empty per-command accumulator."""
    return {'calls': Counter(), 'ticked': 0, 'classes': Counter(), 'tick': 0,
            'examples': {}, 'estimate': Counter()}


def pipeline_context(export_dir: str, scratch: str) -> dict:
    """The script stage's own context for a plugin, built into `scratch`, workers seeded.

    The plugin's music manifest is copied beside the scratch scripts folder,
    where the context build looks for it, so StreamMusic resolves as it does.
    """
    plugin = os.path.basename(os.path.normpath(export_dir))
    set_namespace(namespace_for(export_dir))
    manifest = plugin_out_root(os.path.join(ROOT, 'output'), plugin,
                               export_dir) / 'music_tracks.json'
    os.makedirs(os.path.join(scratch, plugin), exist_ok=True)
    if manifest.is_file():
        shutil.copy(manifest, os.path.join(scratch, plugin, 'music_tracks.json'))
    ctx = pipeline.build_script_context(
        export_dir, os.path.join(scratch, plugin, 'Scripts', 'Source'))
    pipeline.script_worker_init(*ctx['initargs'])
    return ctx


def audit_plugin(export_dir: str, names: dict, n: int, rows: dict,
                 keep: set = None) -> None:
    """Collect, sample and probe one plugin (only `keep` commands if given) into `rows`."""
    plugin = os.path.basename(os.path.normpath(export_dir))
    started = time.time()
    scratch = tempfile.mkdtemp(prefix='fidelity_')
    try:
        ctx = pipeline_context(export_dir, scratch)
        records = {'SCPT': ctx['scpt_work'], 'INFO': ctx['info_work'],
                   'QUST': ctx['qust_work']}
        sites = collect_sites(plugin, records, names)
        if keep:
            sites = {cmd: s for cmd, s in sites.items() if cmd in keep}
        _probe_plugin(plugin, sites, ctx['initargs'][0], n, rows)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    print(f'{plugin}: done ({time.time() - started:.0f}s)', flush=True)


def _probe_plugin(plugin: str, sites: dict, xref, n: int, rows: dict) -> None:
    """Sample and probe each command's sites, merging counts into `rows`."""
    baselines = {}
    print(f'{plugin}: {sum(map(len, sites.values()))} call sites, '
          f'{len(sites)} commands', flush=True)
    for cmd, cmd_sites in sorted(sites.items(), key=lambda kv: -len(kv[1])):
        row = rows.setdefault(cmd, _new_row())
        row['calls'][plugin] += len(cmd_sites)
        row['ticked'] += sum(s.ticked for s in cmd_sites)
        picked = sample(cmd_sites, n)
        here = Counter()
        for site in picked:
            kind, tick, shown = probe(site, xref, baselines)
            here[kind] += 1
            row['tick'] += tick
            row['examples'].setdefault(kind, (site.line_text.strip(), shown))
        row['classes'] += here
        for kind, count in here.items():
            row['estimate'][kind] += len(cmd_sites) * count / len(picked)


def verdict(row: dict) -> str:
    """A command's verdict: its most common sample class, the worse on a tie."""
    return max(WORST_FIRST, key=lambda c: (row['classes'][c], -WORST_FIRST.index(c)))


def _ranked(rows: dict) -> list:
    """(command, row) by total call sites, most first."""
    return sorted(rows.items(), key=lambda kv: -sum(kv[1]['calls'].values()))


def _fields(cmd: str, row: dict, plugins: list) -> list:
    """The per-command columns shared by the TSV and the Markdown table."""
    total = sum(row['calls'].values())
    src, shown = row['examples'][verdict(row)]
    return ([cmd, verdict(row)] + [f"{row['calls'][p]:,}" for p in plugins]
            + [f"{100 * row['ticked'] / total:.0f}%"]
            + [str(row['classes'][c]) for c in CLASSES]
            + [str(row['tick']), src, shown])


def write_tsv(path: str, rows: dict, plugins: list) -> None:
    """One line per command with per-plugin calls and per-class sample counts."""
    header = (['command', 'verdict'] + plugins + ['ticked'] + list(CLASSES)
              + ['tick_machinery', 'source', 'emitted'])
    with open(path, 'w', encoding='utf-8', newline='') as f:
        f.write('\t'.join(header) + '\n')
        for cmd, row in _ranked(rows):
            f.write('\t'.join(_fields(cmd, row, plugins)) + '\n')


def _cell(text: str) -> str:
    """Markdown-table-safe text."""
    return text.replace('|', '\\|').replace('`', "'")


def summary(rows: dict) -> str:
    """Per class: commands whose verdict it is, and estimated call sites converted so."""
    cmds, calls = Counter(), Counter()
    for _cmd, row in rows.items():
        cmds[verdict(row)] += 1
        calls += row['estimate']
    total = sum(calls.values())
    out = ['| Class | Commands (verdict) | Call sites (estimated) | Share |',
           '|---|---:|---:|---:|']
    out += [f'| {c} | {cmds[c]} | {calls[c]:,.0f} | {100 * calls[c] / total:.1f}% |'
            for c in CLASSES]
    return '\n'.join(out) + '\n'


def markdown_table(rows: dict, plugins: list) -> str:
    """Every command ranked by calls, with its sample classes and one example."""
    head = (['Command', 'Verdict'] + plugins + ['Ticked'] + list(CLASSES)
            + ['Tick machinery', 'Example source', 'Emitted'])
    out = ['| ' + ' | '.join(head) + ' |',
           '|---|---|' + '---:|' * (len(plugins) + len(CLASSES) + 2) + '---|---|']
    for cmd, row in _ranked(rows):
        cells = _fields(cmd, row, plugins)
        cells[0] = f'`{cmd}`'
        cells[-2:] = [_cell(cells[-2]), _cell(cells[-1])]
        out.append('| ' + ' | '.join(cells) + ' |')
    return '\n'.join(out) + '\n'


def main():
    """Run the audit over each `--export` and write the requested outputs."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--export', action='append', required=True)
    ap.add_argument('--samples', type=int, default=8)
    ap.add_argument('--only', nargs='*', help='limit to these commands')
    ap.add_argument('--tsv')
    ap.add_argument('--markdown', help='write the summary and table here')
    args = ap.parse_args()
    names = command_names()
    keep = {names.get(c.lower(), c.lower()) for c in args.only or ()}
    rows = {}
    plugins = [os.path.basename(os.path.normpath(e)) for e in args.export]
    for export_dir in args.export:
        audit_plugin(export_dir, names, args.samples, rows, keep)
    if args.tsv:
        write_tsv(args.tsv, rows, plugins)
    if args.markdown:
        with open(args.markdown, 'w', encoding='utf-8') as f:
            f.write(summary(rows) + '\n' + markdown_table(rows, plugins))
    print(summary(rows))


if __name__ == '__main__':
    main()
