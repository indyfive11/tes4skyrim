"""esm_scan reads each REFR's XESP enable parent: normalized id, opposite bit.

A chain of enable parents can cross plugins, so the parent id must be in the
same global index space as `form_id`.  The fixture registers the files in a
global order that DIFFERS from the plugin's own master order (Oblivion.esm is
index 01 in the file and 00 globally), so a reader that kept the raw id
returns a different number and the test can tell.
"""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asset_convert.lod import esm_scan, lod_gen

MASTERS = ('Skyrim.esm', 'Oblivion.esm')
OBLIVION, SKYRIM, OWN = 0x00000000, 0x02000000, 0x03000000


@pytest.fixture
def scan(monkeypatch):
    """esm_scan with a fresh file registry: Oblivion.esm 0, a filler 1, Skyrim.esm 2."""
    monkeypatch.setattr(esm_scan, '_GLOBAL_FILES', [])
    monkeypatch.setattr(esm_scan, '_GLOBAL_FILE_IDX', {})
    monkeypatch.setattr(esm_scan, '_MASTERS_CACHE', {})
    for name in ('Oblivion.esm', 'Filler.esm', 'Skyrim.esm'):
        esm_scan.global_file_index(name)
    return esm_scan


def _rec(sig: bytes, fid: int, body: bytes = b'', flags: int = 0) -> bytes:
    """One record: header plus `body`."""
    return sig + struct.pack('<IIIII', len(body), flags, fid, 0, 0) + body


def _grup(label: int, gtype: int, payload: bytes) -> bytes:
    """One GRUP around `payload`."""
    return (b'GRUP' + struct.pack('<I', 24 + len(payload))
            + struct.pack('<I', label) + struct.pack('<III', gtype, 0, 0)
            + payload)


def _sub(sig: bytes, payload: bytes) -> bytes:
    """One subrecord."""
    return sig + struct.pack('<H', len(payload)) + payload


def _plugin(tmp_path: Path, payload: bytes) -> Path:
    """A minimal `ov.esp` whose master list is MASTERS, so its own index is 02."""
    hdr = _sub(b'HEDR', struct.pack('<fII', 1.7, 0, 0x800)) + b''.join(
        _sub(b'MAST', m.encode() + b'\0') + _sub(b'DATA', bytes(8))
        for m in MASTERS)
    p = tmp_path / 'ov.esp'
    p.write_bytes(_rec(b'TES4', 0, hdr) + payload)
    return p


def _refr(fid: int, xesp: bytes = b'') -> bytes:
    """A REFR placing base 0x01000100, with an optional raw XESP payload."""
    body = (_sub(b'NAME', struct.pack('<I', 0x01000100))
            + _sub(b'DATA', struct.pack('<6f', 1, 2, 3, 0, 0, 0)))
    if xesp:
        body += _sub(b'XESP', xesp)
    return _rec(b'REFR', fid, body)


def test_xesp_parent_and_opposite_bit_are_read(scan, tmp_path):
    """Parent normalized per file; bit 0 alone decides `opposite`; no or short XESP is None."""
    refs = (_refr(0x02000002, struct.pack('<II', 0x01005820, 1))
            + _refr(0x02000003)
            + _refr(0x02000004, struct.pack('<II', 0x02000002, 0))
            + _refr(0x02000005, struct.pack('<II', 0x00000ABC, 0x24468202))
            + _refr(0x02000006, struct.pack('<II', 0x00000ABC, 0x24468203))
            + _refr(0x02000007, struct.pack('<I', 0x01005820)))
    payload = _grup(0x0100003C, 1, _grup(0x02000001, 6, refs))
    _w, _c, _s, got = scan.parse_esm(_plugin(tmp_path, payload))
    xesp = {r['form_id']: r['xesp'] for r in got}
    assert xesp == {OWN | 2: (OBLIVION | 0x005820, True),
                    OWN | 3: None,
                    OWN | 4: (OWN | 2, False),
                    OWN | 5: (SKYRIM | 0xABC, False),
                    OWN | 6: (SKYRIM | 0xABC, True),
                    OWN | 7: None}
    assert set(got[0]) == {'form_id', 'flags', 'base_fid', 'parent_wrld',
                           'parent_cell', 'x', 'y', 'z', 'rx', 'ry', 'rz',
                           'scale', 'xesp'}


ROCK = 'tes4\\rocks\\rock01.nif'
DISABLED = 0x00000800


def _stat(fid: int, edid: str, model: str, flags: int) -> bytes:
    """A STAT 900 units across."""
    return _rec(b'STAT', fid, _sub(b'EDID', edid.encode() + b'\0')
                + _sub(b'OBND', struct.pack('<6h', 0, 0, 0, 900, 900, 900))
                + _sub(b'MODL', model.encode() + b'\0'), flags)


def _placed(fid: int, base: int, flags: int = 0, xesp=None) -> bytes:
    """A REFR of `base` at (100, 100) with an optional (parent, flags) XESP."""
    body = (_sub(b'NAME', struct.pack('<I', base))
            + _sub(b'DATA', struct.pack('<6f', 100, 100, 0, 0, 0, 0)))
    if xesp:
        body += _sub(b'XESP', struct.pack('<II', *xesp))
    return _rec(b'REFR', fid, body, flags)


def _world(wrld: int, cell: int, refs: bytes, with_wrld: bool = True) -> bytes:
    """A WRLD group holding one exterior cell at (0, 0) and its temporary refs."""
    grid = struct.unpack('<I', struct.pack('<hh', 0, 0))[0]
    cell_rec = _rec(b'CELL', cell, _sub(b'XCLC', struct.pack('<iiI', 0, 0, 0)))
    inner = _grup(grid, 4, _grup(grid, 5, cell_rec + _grup(
        cell, 6, _grup(cell, 9, refs))))
    head = (_rec(b'WRLD', wrld, _sub(b'EDID', b'TestWorld\0'))
            if with_wrld else b'')
    return _grup(struct.unpack('<I', b'WRLD')[0], 0,
                 head + _grup(wrld, 1, inner))


def _file(folder: Path, name: str, masters, payload: bytes) -> Path:
    """Write plugin `name` with the given master list into `folder/name/`."""
    hdr = _sub(b'HEDR', struct.pack('<fII', 1.7, 0, 0x800)) + b''.join(
        _sub(b'MAST', m.encode() + b'\0') + _sub(b'DATA', bytes(8))
        for m in masters)
    (folder / name).mkdir()
    path = folder / name / name
    path.write_bytes(_rec(b'TES4', 0, hdr) + payload)
    return path


@pytest.fixture
def bake(scan, monkeypatch, tmp_path):
    """`generate_lod` up to the written input: no mesh derive, no LODGen, stub reader."""
    (tmp_path / 'lodgen').mkdir()
    monkeypatch.setattr(lod_gen, 'LODGEN_EXE', tmp_path / 'lodgen' / 'x.exe')
    monkeypatch.setattr(lod_gen, '_NIF_ROOT_SAFE_CACHE', {})
    monkeypatch.setattr(lod_gen, '_derive_far_meshes', lambda *a, **k: None)
    monkeypatch.setattr(lod_gen, 'run_lodgen', lambda *a, **k: False)
    monkeypatch.setattr(lod_gen, '_root_is_ninode', lambda full: True)
    out = tmp_path / 'AutoConvertLOD'
    for name in ('rock01.nif', 'rock01_far.nif'):
        (out / 'meshes' / 'tes4' / 'rocks').mkdir(parents=True, exist_ok=True)
        (out / 'meshes' / 'tes4' / 'rocks' / name).write_bytes(b'NIF')

    def run(owner, overlays):
        """Local ids of the rows written for TestWorld."""
        lod_gen.generate_lod(esm_path=owner, output_dir=out,
                             worldspace_edid='TestWorld',
                             overlay_paths=overlays)
        txt = tmp_path / 'lodgen' / 'LODGen TestWorld.txt'
        rows = [ln for ln in txt.read_text(encoding='utf-8').splitlines()
                if '\t' in ln]
        txt.unlink()
        return {int(ln.split('\t')[0], 16) & 0xFFFFFF for ln in rows}
    return run


def test_a_later_plugin_changes_the_state_through_the_parent(bake, tmp_path):
    """The owner's parent is off; an overlay naming it by another index byte turns it on."""
    owner = _file(tmp_path, 'A.esm', ['Skyrim.esm'], _grup(
        struct.unpack('<I', b'STAT')[0], 0,
        _stat(0x01000900, 'Rock01', ROCK, 0x8000)
        + _stat(0x01000901, 'Marker', '', 0)) + _world(
        0x01000A00, 0x01000A01,
        _placed(0x01000C01, 0x01000901, DISABLED)
        + _placed(0x01000B01, 0x01000900, xesp=(0x01000C01, 0))
        + _placed(0x01000B02, 0x01000900)))
    overlay = _file(tmp_path, 'B.esp', ['Skyrim.esm', 'Other.esm', 'A.esm'],
                    _world(0x02000A00, 0x02000A01, with_wrld=False, refs=(
                        _placed(0x02000C01, 0x02000901)
                        + _placed(0x03000D01, 0x02000900, xesp=(0x02000C01, 1))
                        + _placed(0x03000D02, 0x02000900,
                                  xesp=(0x02000B01, 0)))))
    assert bake(owner, []) == {0x000B02}
    assert bake(owner, [overlay]) == {0x000B01, 0x000B02, 0x000D02}
