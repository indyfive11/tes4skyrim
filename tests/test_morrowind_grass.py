"""Morrowind groundcover as Skyrim grass: planter density, ground and masters.

See: docs/commentary/tes4_export_morrowind.md#groundcover-as-grass
"""

import os
import struct
from types import SimpleNamespace

from tes4_export import tes3_reader as reader
from tes4_export.export_morrowind import (convert_plugin, load_context,
                                          write_export, write_header)
from tes4_export.morrowind_grass import (QUAD_VERTS_TOTAL, GrassTally,
                                         _quadrant_weights, _texture_key,
                                         grass_master_list)

TEXTURE = 'Base.esm|000001'


def _clump(record_id: str, scale: float = 1.0):
    """A placed reference at the origin quad of grid (0, 0)."""
    return SimpleNamespace(record_id=record_id, pos=(100.0, 100.0, 0.0),
                           scale=scale)


def _one_cell_tally() -> GrassTally:
    """A tally over one fully planted cell of TEXTURE, one grass model."""
    grid = {(0, 0): ([TEXTURE] * 4, [{TEXTURE: 1.0}] * 4)}
    return GrassTally(grid, {'grass_a': 'Grass\\\\a.nif'})


def test_density_solves_against_the_256_unit_planter_blocks():
    """576 candidates per quad at PositionRange 85: 8x8 blocks of 3x3.

    See: docs/commentary/asset_convert_terrain.md#grass-placement-parity
    """
    tally = _one_cell_tally()
    for _ in range(4 * 288):
        tally.absorb(_clump('grass_a'))
    assert tally.density(TEXTURE, 'grass_a') == 50


def test_height_range_is_the_relative_spread_and_stays_positive():
    """Scales 1.0/1.5 spread 0.2 about 1.25: sqrt(3) * 0.25 / 1.25 = 0.35."""
    tally = _one_cell_tally()
    for scale in (1.0, 1.5) * 50:
        tally.absorb(_clump('grass_a', scale))
    assert abs(tally.height_range('grass_a') - 0.3464) < 1e-3
    tally.scales['grass_a'] = [0.5, 2.0] * 50
    assert tally.height_range('grass_a') <= 0.9


def test_a_faint_alpha_plants_its_whole_quad_at_full_density():
    """Any visible vertex counts fully; the blend share still picks the ground.

    See: docs/commentary/tes4_export_morrowind.md#groundcover-density
    """
    verts = {p: 0.1 for p in range(QUAD_VERTS_TOTAL)}
    layers = {'BTXT.0': 'base', 'ATXT.0': [('alpha', verts)]}
    blend, planted = _quadrant_weights(layers, 0)
    assert abs(blend['alpha'] - 0.1) < 1e-9 and abs(blend['base'] - 0.9) < 1e-9
    assert abs(planted['alpha'] - 1.0) < 1e-9 and abs(planted['base'] - 1.0) < 1e-9


def test_a_texture_no_bindable_file_owns_has_no_key():
    """Only a texture of a master (or a master's master) can carry grass."""
    owners = ['Base.esm', 'Land.esm']
    allowed = {'base.esm': 'Base.esm'}
    assert _texture_key('00ABCDEF', owners, allowed) == 'Base.esm|ABCDEF'
    assert _texture_key('01ABCDEF', owners, allowed) == ''


def _header(root, name: str, masters=()) -> None:
    """An export folder `root/name` whose header lists `masters`."""
    folder = os.path.join(str(root), name)
    os.makedirs(folder, exist_ok=True)
    write_header(folder, list(masters), 0, 'fixture', flags=1)


def test_an_ancestor_under_grass_is_listed_ahead_of_its_master(tmp_path):
    """The file owning ground under a clump joins the list before its child.

    See: docs/commentary/tes4_export_morrowind.md#land-texture-fold
    """
    _header(tmp_path, 'Data.esm')
    _header(tmp_path, 'Land.esm', ['Data.esm'])
    names = ['Land.esm']
    assert grass_master_list(str(tmp_path), names, {'Data.esm'}) == [
        'Data.esm', 'Land.esm']
    assert grass_master_list(str(tmp_path), names, set()) == names


def _ltex(record_id: str, index: int, image: str) -> reader.Tes3Record:
    """A TES3 land texture record."""
    rec = reader.Tes3Record(type='LTEX', flags=0, record_id=record_id)
    rec.subrecords = [
        reader.Subrecord(type='NAME', data=record_id.encode() + b'\x00'),
        reader.Subrecord(type='INTV', data=struct.pack('<I', index)),
        reader.Subrecord(type='DATA', data=image.encode() + b'\x00')]
    return rec


def test_a_redeclared_master_texture_folds_onto_the_masters_record(tmp_path):
    """Same id and image is the master's LTEX; a new image stays the plugin's.

    See: docs/commentary/tes4_export_morrowind.md#land-texture-fold
    """
    master_dir = str(tmp_path / 'Data.esm')
    master_ctx = load_context(str(tmp_path))
    counts = write_export(convert_plugin(
        [_ltex('grass', 0, 'tx_grass.dds')], master_ctx), master_dir)
    write_header(master_dir, [], sum(counts.values()), 'fixture', flags=1)
    ctx = load_context(str(tmp_path), [('Data.esm', master_dir)])
    out = convert_plugin([_ltex('grass', 0, 'tx_grass.dds'),
                          _ltex('grass2', 1, 'tx_other.dds')], ctx)
    master_id = master_ctx.ltex_by_index[0]
    assert ctx.ltex_by_index[0][2:] == master_id[2:]
    assert ctx.ltex_by_index[0][:2] == '00'
    assert [fid[:2] for fid, _lines in out.get('LTEX', [])] == ['01']
