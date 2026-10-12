"""Find each AE id a runtime looks up in the pre-AE builds: SE 1.5.97 and VR 1.4.15.

AE renumbered the Address Library, and VR's database lacks nearly every id we
use, so neither build can resolve an AE id. Both builds are frozen, so this
derives each one's address ONCE and writes a table the runtime loads on that
exact build. An id with no proven match is left out, which turns its feature
off instead of calling a wrong address.

Each match comes from an anchor both builds name identically, checked by shape:
    papyrus  the native's own registration site, by its position there
    string   a string literal only this function references, in both builds
    vtable   the RTTI class the vtable's locator names
    caller   the same call in an already-matched caller with the same calls
    data     the same reference in an already-matched function
VR is matched from the SE result: both are pre-AE code, so they differ less.

Usage:
    python -m tools.disasm.pre_ae_map --plugin tes_runtime/morrowind/plugin
    python -m tools.disasm.pre_ae_map --plugin ... --write
See: docs/reference/address_library_formats.md#pre-ae-tables
"""

from __future__ import annotations

import argparse
import bisect
import difflib
import re
import struct
import sys
from collections import defaultdict
from pathlib import Path

from tools.disasm import address_lib
from tools.disasm.skyrim_disasm import Binary
from tools.script import papyrus_native_locate as locate

#: The unpacked exes, one per build.
DEPOT = Path(r'C:\Program Files (x86)\Steam\steamapps\content\app_489830\depot_489833')
AE_VERSION, SE_VERSION = '1.6.1170', '1.5.97'
EXES = {AE_VERSION: DEPOT / 'SkyrimSE.1.6.1170.unpacked.exe',
        SE_VERSION: DEPOT / 'SkyrimSE.1.5.97.unpacked.exe',
        'VR': DEPOT / 'SkyrimVR.exe.unpacked.exe'}

#: The ids every runtime shares, declared beside the plugins.
COMMON = Path('tes_runtime/common')

#: Papyrus natives a plugin resolves through `Resolve` rather than `Native<>`.
RESOLVED_NATIVES = {'kGetFormFromFile': ('Game', 'GetFormFromFile')}

#: Rows no anchor reaches, proven by hand. See: docs/reference/address_library_formats.md#hand-proven
HAND_PROVEN = {'kTrainingMenuTrain': {SE_VERSION: 0x8ce8e0, 'VR': 0x8fb9c0},
               'kMenuManagerRegister': {SE_VERSION: 0xebf9c0, 'VR': 0xf1be20},
               'kControlMapAllowTextInput': {SE_VERSION: 0xc11f30, 'VR': 0xc4e8d0},
               'kControlMapSingleton': {SE_VERSION: 0x2ec5bd0, 'VR': 0x2f8aaa0},
               'kMenuManagerIsMenuOpen': {SE_VERSION: 0xebe150, 'VR': 0xf1a3b0},
               'kGfxReleaseManaged': {SE_VERSION: 0xecb0e0}}

#: SKSE's packed runtime version of each pre-AE build: major<<24 | minor<<16 | build<<4.
RUNTIMES = {SE_VERSION: 0x01050610, 'VR': 0x010400F0}

#: How deep the caller chain may go looking for an anchored caller.
MAX_DEPTH = 3

#: Callers tried per function, and the size ratio a match must stay inside.
MAX_CALLERS, SIZE_RATIO = 12, 1.6

#: Referencing functions tried for a global; it stops once three agree.
MAX_DATA_OWNERS = 80

#: Call sites read for the string-site anchor.
MAX_STRING_SITES = 30

_DECL = re.compile(r'constexpr\s+std::uint64_t\s+(\w+)\s*=\s*(\d+)\s*;')
_ROW = re.compile(r'\{"(\w+)",\s*(\d+),\s*(\d+)\}')
_NATIVE = re.compile(r'Native<\w+>\(\s*"(\w+)\.(\w+)",\s*ids::(\w+)\)')
_USE = re.compile(r'(?:Resolve\([^;]*?|Native<[^>]+>\([^;]*?)ids::(\w+)', re.S)
_LEA_MOV = re.compile(rb'[\x48\x4c][\x8d\x8b\x89][\x05\x0d\x15\x1d\x25\x2d\x35\x3d]', re.S)
_PRINTABLE = re.compile(rb'[\x20-\x7e]{4,}\0')


class Image:
    """One build: its binary plus call, reference and function-start indexes."""

    def __init__(self, exe: Path):
        """Read the exe and index every direct call and rip-relative lea/mov."""
        self.bin = Binary(str(exe))
        table = self.bin.runtime_functions()
        self.starts = [b for b, _e, _u in table]
        self.ends = dict((b, e) for b, e, _u in table)
        self.start_set = set(self.starts)
        self.callers = defaultdict(set)
        self.refs = defaultdict(set)
        self.leas = defaultdict(list)
        self.slots = defaultdict(list)
        self.call_sites = defaultdict(list)
        self._facts = {}
        self._index()
        self._index_slots()

    def func_of(self, rva: int):
        """The function start containing `rva`, or None."""
        i = bisect.bisect_right(self.starts, rva) - 1
        return self.starts[i] if i >= 0 and rva < self.ends[self.starts[i]] else None

    def _text(self) -> tuple:
        """(file offset, rva, length) of `.text`."""
        for s in self.bin.pe.sections:
            if s.Name.rstrip(b'\0') == b'.text':
                return s.PointerToRawData, s.VirtualAddress, s.SizeOfRawData
        raise SystemExit('no .text in %s' % self.bin.path)

    def _index(self) -> None:
        """Fill `callers` (target -> calling functions) and `refs` (target -> referencing functions)."""
        off, rva, size = self._text()
        data = self.bin.data
        for at in (m.start() for m in re.finditer(rb'[\xe8\xe9]', data[off:off + size])):
            target = rva + at + 5 + struct.unpack_from('<i', data, off + at + 1)[0]
            if self.is_start(target):
                self._note(self.callers, target, rva + at)
                self.call_sites[target].append(rva + at)
        for m in _LEA_MOV.finditer(data, off, off + size):
            site = rva + m.start() - off
            target = site + 7 + struct.unpack_from('<i', data, m.start() + 3)[0]
            self._note(self.refs, target, site)
            if data[m.start() + 1] == 0x8d:
                self.leas[target].append(site)

    def _index_slots(self) -> None:
        """Fill `slots` (function -> [(vtable, slot)]) from every `.rdata` vtable a locator heads."""
        for s in self.bin.pe.sections:
            if s.Name.rstrip(b'\0') != b'.rdata':
                continue
            base, raw = s.VirtualAddress, self.bin.data[s.PointerToRawData:s.PointerToRawData + s.SizeOfRawData]
            vtable, slot = None, 0
            for i, (va,) in enumerate(struct.iter_unpack('<Q', raw[:len(raw) // 8 * 8])):
                rva = va - self.bin.base if va > self.bin.base else -1
                if vtable is not None and self.is_start(rva):
                    self.slots[rva].append((vtable, slot))
                    slot += 1
                else:
                    vtable = base + (i + 1) * 8 if _is_vtable(self, base + (i + 1) * 8) else None
                    slot = 0

    def _note(self, index: dict, target: int, site: int) -> None:
        """Record that the function holding `site` reaches `target`."""
        owner = self.func_of(site)
        if owner is not None:
            index[target].add(owner)

    def is_start(self, rva: int) -> bool:
        """A function start: a `.pdata` entry, or a leaf right after int3/ret padding."""
        return rva in self.start_set or (self.is_code(rva) and self.bin.read(rva - 1, 1) in (b'\xcc', b'\xc3'))

    def is_code(self, rva: int) -> bool:
        """Whether `rva` lies in `.text`."""
        if not hasattr(self, '_code'):
            _off, start, size = self._text()
            self._code = (start, start + size)
        return self._code[0] <= rva < self._code[1]

    def end_of(self, start: int) -> int:
        """The function's end: its `.pdata` entry, or a leaf function's first `ret`."""
        if start in self.ends:
            return self.ends[start]
        for insn in self.bin.disasm(start, 64):
            if insn.mnemonic in ('ret', 'int3'):
                return insn.address - self.bin.base + insn.size
        return start

    def facts(self, start: int) -> list:
        """The function's ordered ('call', target) and ('ref', target) events, by disassembly."""
        if start not in self._facts:
            out = []
            end = self.end_of(start)
            for insn in self.bin.md.disasm(self.bin.read(start, end - start), self.bin.base + start):
                out.extend(_events(self, insn))
            self._facts[start] = out
        return self._facts[start]

    def string_at(self, rva: int):
        """The printable NUL-terminated string at `rva`, or None."""
        m = _PRINTABLE.match(self.bin.read(rva, 256))
        return m.group(0)[:-1] if m else None

    def size(self, start: int) -> int:
        """The function's byte length."""
        return self.end_of(start) - start


def _events(image: Image, insn) -> list:
    """A capstone instruction's call target or rip-relative reference, as events."""
    out = []
    if insn.mnemonic in ('call', 'jmp') and insn.op_str.startswith('0x'):
        target = int(insn.op_str, 16) - image.bin.base
        if image.is_start(target):
            out.append(('call', target))
    for op in insn.operands:
        if op.type == 3 and op.mem.base == 41:
            out.append(('ref', insn.address + insn.size + op.mem.disp - image.bin.base))
    return out


def body_pattern(image: Image, start: int):
    """A regex of the function's first bytes with every 4-byte displacement and
    immediate masked, or None when under 16 bytes are left to match on."""
    out, length = [], 0
    for insn in image.bin.md.disasm(image.bin.read(start, 64), image.bin.base + start):
        raw = [re.escape(bytes([b])) for b in insn.bytes]
        for at, size in ((insn.disp_offset, insn.disp_size), (insn.imm_offset, insn.imm_size)):
            if size == 4:
                raw[at:at + 4] = [b'.'] * 4
        out += raw
        length += insn.size
        if length >= 28 or insn.mnemonic in ('ret', 'jmp', 'int3'):
            break
    return re.compile(b''.join(out), re.S) if length >= 16 else None


def calls(image: Image, start: int) -> list:
    """The function's direct call targets, in order."""
    return [t for kind, t in image.facts(start) if kind == 'call']


def data_refs(image: Image, start: int) -> list:
    """The function's rip-relative references that are not strings, in order."""
    return [t for kind, t in image.facts(start) if kind == 'ref' and image.string_at(t) is None]


def similarity(src: Image, a: int, dst: Image, b: int) -> float:
    """How alike two functions' instruction mnemonics run, 0..1 (difflib ratio)."""
    def ops(image, start):
        """The function's mnemonic sequence."""
        code = image.bin.read(start, image.end_of(start) - start)
        return [i.mnemonic for i in image.bin.md.disasm(code, image.bin.base + start)]
    return difflib.SequenceMatcher(None, ops(src, a), ops(dst, b), autojunk=False).ratio()


def same_shape(src: Image, a: int, dst: Image, b: int) -> bool:
    """Whether two functions are close enough in size and calls to be one function."""
    sa, sb = max(src.size(a), 1), max(dst.size(b), 1)
    ca, cb = len(calls(src, a)), len(calls(dst, b))
    return max(sa, sb) / min(sa, sb) <= SIZE_RATIO and abs(ca - cb) <= max(2, ca // 4)


class Matcher:
    """Maps `src` functions and globals to `dst` ones, memoised."""

    def __init__(self, src: Image, dst: Image, natives: dict):
        """`natives` is `{src start: (script, name)}` for the Papyrus natives to anchor."""
        self.src, self.dst = src, dst
        self.natives = natives
        self.memo = {}
        self.tried = {}
        self.how = {}

    def function(self, start: int, depth: int = MAX_DEPTH):
        """The dst function for src function `start`, or None when unproven."""
        if start in self.memo and (self.memo[start] is not None or self.tried.get(start, -1) >= depth):
            return self.memo[start]
        self.memo[start], self.tried[start] = None, depth
        votes = defaultdict(set)
        for kind, cand in self._strong(start):
            votes[cand].add(kind)
        if not votes and depth > 0:
            for cand in self._by_callers(start, depth - 1):
                votes[cand].add('caller')
        result = self._decide(start, votes)
        self.memo[start] = result
        return result

    def _decide(self, start: int, votes: dict):
        """The one candidate the anchors agree on that passes the shape check."""
        good = {c: k for c, k in votes.items() if same_shape(self.src, start, self.dst, c)}
        strong = {c: k for c, k in good.items() if k - {'caller'} or len(k) > 1}
        pick = strong or {c: k for c, k in good.items() if len(votes) == 1}
        if len(pick) != 1:
            return None
        (cand, kinds), = pick.items()
        self.how[start] = '+'.join(sorted(kinds))
        return cand

    def _strong(self, start: int) -> list:
        """(kind, dst candidate) from the native registration and unique strings."""
        out = []
        if start in self.natives:
            cand = native_in(self.src, self.dst, start, *self.natives[start])
            if cand is not None:
                out.append(('papyrus', cand))
        for kind, cand in (('bytes', self._by_body(start)), ('vtref', self._by_vtable_ref(start)),
                           ('strsite', self._by_string_site(start))):
            if cand is not None:
                out.append((kind, cand))
        for vtable, slot in self.src.slots.get(start, ())[:MAX_CALLERS]:
            dst_vtable = self._vtable(vtable)
            target = self.dst.bin.u64(dst_vtable + slot * 8) if dst_vtable else None
            if target and target - self.dst.bin.base in self.dst.start_set:
                out.append(('slot', target - self.dst.bin.base))
        for text in self._own_strings(start):
            owners = {f for r in locate.find_strings(self.dst.bin, text.decode('latin1'))
                      for f in self.dst.refs.get(r, ())}
            if len(owners) == 1:
                out.append(('string', owners.pop()))
        return out

    def _by_body(self, start: int):
        """The one dst function opening with `start`'s masked bytes, when src has it once too."""
        pattern = body_pattern(self.src, start)
        if pattern is None:
            return None
        hits = [self.dst.bin.off_to_rva(m.start()) for m in pattern.finditer(self.dst.bin.data)]
        own = sum(1 for _m in pattern.finditer(self.src.bin.data))
        hits = [h for h in hits if h is not None and self.dst.is_start(h)]
        return hits[0] if len(hits) == 1 and own == 1 else None

    def _by_vtable_ref(self, start: int):
        """The dst function referencing the matched vtable that `start` references (a
        constructor or destructor), by best similarity with a clear margin."""
        for kind, target in self.src.facts(start):
            if kind != 'ref' or not _is_vtable(self.src, target):
                continue
            dst_vtable = self._vtable(target)
            owners = sorted(self.dst.refs.get(dst_vtable, ())) if dst_vtable else []
            scored = sorted(((similarity(self.src, start, self.dst, o), o) for o in owners), reverse=True)
            if scored and scored[0][0] >= 0.8 and (len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.15):
                return scored[0][1]
        return None

    def _by_string_site(self, start: int):
        """The dst function that the call or jump after each same string load reaches,
        when at least three such sites agree on it."""
        votes = defaultdict(int)
        for site in self.src.call_sites.get(start, ())[:MAX_STRING_SITES]:
            text = _string_before(self.src, site)
            for dst_text in locate.find_strings(self.dst.bin, text) if text else ():
                for dst_site in self.dst.leas.get(dst_text, ()):
                    target = _first_branch(self.dst, dst_site)
                    if target is not None:
                        votes[target] += 1
        best = sorted(votes.items(), key=lambda kv: -kv[1])
        if best and best[0][1] >= 3 and best[0][1] * 4 >= sum(votes.values()) * 3:
            return best[0][0]
        return None

    def _vtable(self, vtable: int):
        """`vtable_in`, memoised: the RTTI scan behind it reads the whole image."""
        key = ('vtable', vtable)
        if key not in self.memo:
            self.memo[key] = vtable_in(self.src, self.dst, vtable)
        return self.memo[key]

    def _own_strings(self, start: int) -> list:
        """Strings `start` references that no other src function references."""
        out = []
        for kind, target in self.src.facts(start):
            text = self.src.string_at(target) if kind == 'ref' else None
            if text and len(text) >= 6 and self.src.refs.get(target) == {start}:
                if len(locate.find_strings(self.src.bin, text.decode('latin1'))) == 1:
                    out.append(text)
        return out[:4]

    def _by_callers(self, start: int, depth: int) -> list:
        """dst candidates: the same call position in each matched caller."""
        out = []
        for caller in sorted(self.src.callers.get(start, ()))[:MAX_CALLERS]:
            mapped = self.function(caller, depth)
            if mapped is None:
                continue
            src_calls, dst_calls = calls(self.src, caller), calls(self.dst, mapped)
            if len(src_calls) == len(dst_calls):
                out.extend(dst_calls[i] for i, t in enumerate(src_calls) if t == start)
        return out

    def data(self, target: int):
        """The dst global at the same reference in matched src functions, or None."""
        votes = defaultdict(int)
        for owner in sorted(self.src.refs.get(target, ()))[:MAX_DATA_OWNERS]:
            if sum(votes.values()) >= 3:
                break
            mapped = self.function(owner, 1)
            if mapped is None:
                continue
            src_refs, dst_refs = data_refs(self.src, owner), data_refs(self.dst, mapped)
            if len(src_refs) == len(dst_refs):
                for i, t in enumerate(src_refs):
                    if t == target:
                        votes[dst_refs[i]] += 1
        if len(votes) != 1:
            return None
        self.how[target] = 'data x%d' % next(iter(votes.values()))
        return next(iter(votes))


def _string_before(image: Image, site: int):
    """The string a `lea` loads within 48 bytes before `site`, the nearest one, or None."""
    data, off = image.bin.data, image.bin.rva_to_off(site)
    best = None
    for m in _LEA_MOV.finditer(data, off - 48, off):
        if data[m.start() + 1] != 0x8d:
            continue
        lea = image.bin.off_to_rva(m.start())
        text = image.string_at(lea + 7 + struct.unpack_from('<i', data, m.start() + 3)[0])
        if text and len(text) >= 4:
            best = text.decode('latin1')
    return best


def _first_branch(image: Image, site: int):
    """The target of the first direct call or jmp within 12 instructions of `site`."""
    for insn in image.bin.disasm(site, 12):
        if insn.mnemonic in ('call', 'jmp') and insn.op_str.startswith('0x'):
            target = int(insn.op_str, 16) - image.bin.base
            return target if image.is_start(target) else None
    return None


def native_in(src: Image, dst: Image, start: int, script: str, name: str):
    """The dst native: the callback loaded first after the name at `script.name`'s
    registration, when that is what `start` is on src."""
    src_cands = _registration(src, script, name)
    dst_cands = _registration(dst, script, name)
    if not src_cands or not dst_cands or src_cands[0] != start:
        return None
    return dst_cands[0]


def _registration(image: Image, script: str, name: str) -> list:
    """Code candidates at `script`'s registration of `name`, those loaded after the
    name first and nearest first: a window can also catch a neighbour's callback."""
    for text in locate.find_strings(image.bin, name):
        for site in image.leas.get(text, ()):
            if locate.class_at(image.bin, site) == script:
                near = [(r, t) for r, t in locate.code_leas_near(image.bin, site) if image.is_code(t)]
                return [t for r, t in sorted(near, key=lambda rt: (rt[0] < site, abs(rt[0] - site)))]
    return []


def vtable_in(src: Image, dst: Image, vtable: int):
    """The dst vtable whose locator names the same class at the same offset, or None."""
    col = src.bin.u64(vtable - 8)
    if not col:
        return None
    col -= src.bin.base
    name = _class_name(src, col)
    for cand in dst.bin.vtables_for(name) if name else ():
        dcol = dst.bin.u64(cand - 8) - dst.bin.base
        if dst.bin.u32(dcol + 4) == src.bin.u32(col + 4):
            return cand
    return None


def vtable_by_slots(matcher: Matcher, vtable: int):
    """The one dst vtable of the same class holding, in the same slot, the match of
    each of src's first matchable slots: a sub-object's offset can differ by build."""
    src, dst = matcher.src, matcher.dst
    name = _class_name(src, src.bin.u64(vtable - 8) - src.bin.base)
    pairs = []
    for slot in range(4):
        fn = src.bin.u64(vtable + slot * 8)
        mapped = matcher.function(fn - src.bin.base) if fn and src.is_code(fn - src.bin.base) else None
        if mapped is not None:
            pairs.append((slot, mapped))
    hits = [v for v in (dst.bin.vtables_for(name) if name and pairs else ())
            if all(dst.bin.u64(v + s * 8) == dst.bin.base + m for s, m in pairs)]
    return hits[0] if len(hits) == 1 else None


def _class_name(image: Image, col: int):
    """The bare class name a complete object locator's type descriptor carries."""
    desc = image.bin.u32(col + 12)
    raw = image.bin.read(desc + 16, 160).split(b'\0', 1)[0].decode('latin1')
    m = re.fullmatch(r'\.\?A[UV](\w+)@@', raw)
    return m.group(1) if m else None


def declared(plugin: Path) -> tuple:
    """(`{constant: AE id}` of ids looked up, `{constant: (script, name)}` of natives)."""
    header = (plugin / 'ids.h').read_text(encoding='utf-8')
    shared = (COMMON / 'engine_ids.h').read_text(encoding='utf-8')
    ids = {n: int(v) for n, v in _DECL.findall(shared + header)}
    used, natives = set(), dict(RESOLVED_NATIVES)
    for cpp in sorted(plugin.glob('*.cpp')) + sorted(COMMON.glob('*.cpp')):
        text = cpp.read_text(encoding='utf-8', errors='replace')
        used |= set(_USE.findall(text))
        natives.update({c: (s, f) for s, f, c in _NATIVE.findall(text)})
    for name, vt, act in _ROW.findall(header):
        ids.update({'%s vtable' % name: int(vt), '%s Activate' % name: int(act)})
        used |= {'%s vtable' % name, '%s Activate' % name}
    return {n: v for n, v in ids.items() if n in used}, natives


def map_build(src: Image, dst: Image, wanted: dict, natives: dict) -> tuple:
    """(`{name: dst rva}`, `{name: how}`) for `wanted` `{name: src rva}`."""
    matcher = Matcher(src, dst, {wanted[c]: natives[c] for c in natives if c in wanted})
    found, how = {}, {}
    for name, rva in sorted(wanted.items()):
        if src.is_code(rva):
            hit = matcher.function(rva)
        elif src.func_of(rva) is None and _is_vtable(src, rva):
            hit = vtable_in(src, dst, rva)
            matcher.how[rva] = 'vtable'
            if hit is None:
                hit = vtable_by_slots(matcher, rva)
                matcher.how[rva] = 'vtable slots'
        else:
            hit = matcher.data(rva)
        if hit is not None:
            found[name], how[name] = hit, matcher.how.get(rva, '?')
            if src.is_code(rva):
                how[name] += ' sim %.2f' % similarity(src, rva, dst, hit)
        print('  %-34s %s %s' % (name, hex(hit) if hit is not None else '-', how.get(name, '')),
              flush=True)
    return found, how


def _is_vtable(image: Image, rva: int) -> bool:
    """Whether the qword before `rva` points at a signature-1 object locator."""
    col = image.bin.u64(rva - 8)
    return bool(col) and col > image.bin.base and image.bin.u32(col - image.bin.base) == 1


def render(tables: dict, ids: dict) -> str:
    """The generated header: one `{AE id, rva}` table per pre-AE build."""
    lines = ['// GENERATED by tools/disasm/pre_ae_map.py -- DO NOT EDIT.',
             '// Each pre-AE build\'s address for an AE id, proven by an anchor and',
             '// a shape check; an id with no proof is absent and stays unresolved.',
             '// See: docs/reference/address_library_formats.md#pre-ae-tables', '',
             '#pragma once', '', '#include "addresses.h"', '',
             'namespace tesruntime::pre_ae {', '']
    for build, found in tables.items():
        var = 'k' + re.sub(r'\W', '', 'Build' + build)
        lines.append('constexpr IdRva %s[] = {' % var)
        for name in sorted(found, key=lambda n: ids[n]):
            lines.append('    {%d, 0x%x},  // %s' % (ids[name], found[name], name))
        lines += ['};', '']
    lines.append('constexpr PreAeTable kTables[] = {')
    for build in tables:
        var = 'k' + re.sub(r'\W', '', 'Build' + build)
        lines.append('    {0x%08X, %s, sizeof(%s) / sizeof(%s[0])},' % (RUNTIMES[build], var, var, var))
    lines += ['};', '', '}  // namespace tesruntime::pre_ae', '']
    return '\n'.join(lines)


def main() -> int:
    """Map every looked-up id into SE and VR; print the report, optionally write the header."""
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--plugin', required=True, help='plugin source folder holding ids.h')
    ap.add_argument('--write', action='store_true', help='write ids_pre_ae.h beside ids.h')
    args = ap.parse_args()
    plugin = Path(args.plugin)
    ids, natives = declared(plugin)
    table = address_lib.load(address_lib.find_versionlib(AE_VERSION))
    wanted = {n: table[v] for n, v in ids.items() if v in table}
    images = {b: Image(p) for b, p in EXES.items()}
    print('indexed; %d ids to map' % len(wanted), flush=True)
    se, se_how = map_build(images[AE_VERSION], images[SE_VERSION], wanted, natives)
    vr, vr_how = map_build(images[SE_VERSION], images['VR'], se, natives)
    for name, rows in HAND_PROVEN.items():
        for build, found, how in ((SE_VERSION, se, se_how), ('VR', vr, vr_how)):
            if name not in found and build in rows:
                found[name], how[name] = rows[build], 'by hand'
    for name in sorted(ids):
        print('%-34s SE %-10s %-16s VR %-10s %s' % (
            name, hex(se[name]) if name in se else '-', se_how.get(name, ''),
            hex(vr[name]) if name in vr else '-', vr_how.get(name, '')))
    print('%d ids: SE %d, VR %d' % (len(ids), len(se), len(vr)))
    subset = verified(plugin)
    for build, found in (('SE', se), ('VR', vr)):
        print('%s: subset missing %s' % (build, sorted(subset - set(found)) or 'nothing'))
    if args.write:
        out = plugin / 'ids_pre_ae.h'
        tables = {b: {n: r for n, r in f.items() if n in subset}
                  for b, f in ((SE_VERSION, se), ('VR', vr))}
        out.write_text(render(tables, ids), encoding='utf-8')
        print('wrote %s' % out)
    return 0


def verified(plugin: Path) -> set:
    """The ids `pre_ae_ids.txt` lists, whose offsets were checked per build."""
    lines = (plugin / 'pre_ae_ids.txt').read_text(encoding='utf-8').splitlines()
    return {line.strip() for line in lines if line.strip() and not line.startswith('#')}


if __name__ == '__main__':
    sys.exit(main())
