"""Morrowind NPC animation files: what the records bind and the runtime table says."""

import os
from pathlib import Path

from asset_convert.havok.creature_pipeline import humanoid_appends
from asset_convert.havok.npc_anim_morrowind import (anim_layout, anim_slug,
                                                    npc_bindings, placements,
                                                    write_runtime_table)


def _record(sig: str, lines: list) -> str:
    """One export record block."""
    body = '\n'.join([f'Signature={sig}'] + lines)
    return f'---RECORD_BEGIN---\n{body}\n---RECORD_END---\n\n'


def _export(tmp_path) -> str:
    """A record dir with a seated NPC, an NPC with no file, and their placements."""
    folder = tmp_path / 'Test.esm'
    folder.mkdir()
    (folder / 'PACK.txt').write_text(_record('PACK', [
        'FormID=00000A01', 'PKDT.Type=5', 'PLDT.Radius=0',
        'MorrowindIdle=0,0,0,0,0,0,0,100']),
        encoding='utf-8')
    (folder / 'NPC_.txt').write_text(
        _record('NPC_', ['FormID=00000B01', 'EditorID=Sitter',
                         'MorrowindAnim=Ani\\\\Sit.nif', 'AIPackageCount=1',
                         'AIPackage[0]=00000A01'])
        + _record('NPC_', ['FormID=00000B02', 'EditorID=Stander',
                           'AIPackageCount=0']), encoding='utf-8')
    (folder / 'ACHR.txt').write_text(
        _record('ACHR', ['FormID=00000C01', 'NAME=00000B01', 'PosX=1.5',
                         'PosY=2.0', 'PosZ=-3.0', 'RotZ=3.141592653589793'])
        + _record('ACHR', ['FormID=00000C02', 'NAME=00000B02']), encoding='utf-8')
    return str(folder)


def test_slug_is_ascii_and_unique_per_path():
    """Two files with one stem in different folders must not share a clip name."""
    one, two = anim_slug('Ani\\\\SitzGespräch.nif'), anim_slug('Other/SitzGespräch.nif')
    assert one != two
    assert one.isascii() and one.startswith('sitzgesprch_')
    assert anim_slug('ANI/sitzgespräch.NIF') == one


def test_bindings_carry_the_file_and_the_wander_chances(tmp_path):
    """Only an NPC naming its own file is bound, with its wander package."""
    bindings = npc_bindings([_export(tmp_path)])
    assert bindings == {'00000B01': ('Sitter', 'Ani/Sit.nif', '0,0,0,0,0,0,0,100|0')}


def test_runtime_table_rows(tmp_path):
    """The table names each clip's event, each bound NPC and each of its placements."""
    export_dir = _export(tmp_path)
    bindings = npc_bindings([export_dir])
    slug = anim_slug('Ani/Sit.nif')
    clips = [{'slug': slug, 'group': 'idle9', 'event': 'TES3Anim_x', 'duration': 2.0}]
    path = write_runtime_table(str(tmp_path / 'out'), 'Test.esm', clips, bindings,
                               placements(export_dir, bindings))
    assert path.endswith('Test.anims.txt')
    with open(path, encoding='utf-8') as handle:
        rows = handle.read().splitlines()
    assert rows == [f'clip.{slug}.idle9=TES3Anim_x|2.000',
                    f'actor.Sitter={slug}|0,0,0,0,0,0,0,100|0',
                    'place.00000C01=Test.esm|Sitter|'
                    '1.500,2.000,-3.000,0.000,0.000,180.000']


def test_switched_off_build_removes_earlier_outputs(tmp_path):
    """With the conversion off, a rebuild deletes the clips and graphs, not the import's table."""
    export_dir = _export(tmp_path)
    meshes = tmp_path / 'out' / 'meshes'
    table = write_runtime_table(str(tmp_path / 'out'), 'Test.esm', [], {}, [])
    shipped = [meshes / 'actors' / 'character' / 'behaviors' / '0_master.hkx',
               meshes / 'actors' / 'character' / 'characters' / 'defaultmale.hkx',
               meshes / 'actors' / 'character' / 'characters female' / 'defaultfemale.hkx',
               Path(anim_layout(str(meshes))['dir']) / 'x_idle9.hkx']
    for path in shipped:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x')
    assert humanoid_appends(export_dir, str(meshes), log=lambda _m: None) == {}
    assert not any(p.exists() for p in shipped)
    assert not Path(anim_layout(str(meshes))['dir']).exists()
    assert os.path.exists(table)
