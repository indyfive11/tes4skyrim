"""FO3/FNV authored navmeshes: their door links name doors the output can find.

See: docs/commentary/tes5_import_navmesh.md#fallout-door-links-renumbered
"""
import struct

from tes5_import.base.text_reader import get_formid, set_formid_index_offset
from tes5_import.base.writer import PluginWriter
from tes5_import.navmesh.edge_links import NavMeshView, build_edge_links, extract_nvnm
from tes5_import.record_types.navm_falloutnv import parse_door_links, precompute_fallout_navmeshes


def test_a_door_link_names_the_door_as_the_output_numbers_it():
    """The Prospector Saloon's door 0010618E is 0110618E once Skyrim.esm sits before FalloutNV.esm."""
    set_formid_index_offset(1)
    try:
        links = parse_door_links(struct.pack('<IHxx', 0x0010618E, 46))
        door = get_formid({'FormID': '0010618E'}, 'FormID')
    finally:
        set_formid_index_offset(0)
    assert links == [(46, 0x0110618E)] and door == 0x0110618E


def _navm(fid, link_to=None):
    """A one-triangle FNV NAVM export record; with `link_to`, edge 1-2 is flagged as NVEX link 0 to it."""
    verts = struct.pack('<9f', 0, 0, 0, 100, 0, 0, 0, 100, 0)
    flags = 0x2 if link_to else 0
    rec = {'Signature': 'NAVM', 'FormID': fid, 'DATA.Cell': '00106185', 'ParentCELL': '00106185',
           'NVVX': verts.hex(), 'NVTR': struct.pack('<3H3hHH', 0, 1, 2, -1, 0, -1, flags, 0).hex()}
    if link_to:
        rec['NVEX'] = struct.pack('<IIH', 0, int(link_to, 16), 0).hex()
    return rec


def test_an_authored_edge_link_reaches_the_converted_neighbour():
    """NVEX link 0 on a flagged edge becomes a Skyrim edge link to the neighbour's output FormID.

    Unflagged, the same edge value 0 would read as the triangle itself.
    """
    by_type = {'NAVM': [_navm('00000A01', link_to='00000A02'), _navm('00000A02')], 'CELL': []}
    writer = PluginWriter(masters=['Skyrim.esm'])
    cache = precompute_fallout_navmeshes(by_type, writer)
    (bytes_a, meta_a), (_bytes_b, meta_b) = cache[(0x00106185, 0xA01)], cache[(0x00106185, 0xA02)]
    blob, _pre, _post = extract_nvnm(bytes_a)
    view = NavMeshView(meta_a['fid'], blob)
    assert view.links == [[0, meta_b['fid'], 0]]
    assert view.tris[0][4] == 0 and view.tris[0][6] & 0x2
    assert meta_a['edge_link_fids'] == [meta_b['fid']]


def _links_of(cache_entry) -> list:
    """The edge links of one converted NAVM cache entry."""
    blob, _pre, _post = extract_nvnm(cache_entry[0])
    return NavMeshView(cache_entry[1]['fid'], blob).links


def test_links_between_two_meshes_in_one_exterior_cell_survive_the_seam_pass():
    """Both meshes stay in the stitch pass, so neither's authored link to the other is pruned.

    See: docs/commentary/tes5_import_navmesh.md#every-mesh-in-a-cell
    """
    recs = [_navm('00000A01', link_to='00000A02'), _navm('00000A02', link_to='00000A01')]
    for rec in recs:
        rec['ParentWRLD'] = '0000003C'
    by_type = {'NAVM': recs, 'CELL': [{'FormID': '00106185', 'XCLC.X': '0', 'XCLC.Y': '0'}]}
    cache = precompute_fallout_navmeshes(by_type, PluginWriter(masters=['Skyrim.esm']))
    build_edge_links(cache, verbose=False)
    a, b = cache[(0x00106185, 0xA01)], cache[(0x00106185, 0xA02)]
    assert _links_of(a) == [[0, b[1]['fid'], 0]] and _links_of(b) == [[0, a[1]['fid'], 0]]
