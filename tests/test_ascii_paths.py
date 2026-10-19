"""Converted asset paths ship as plain ASCII: Skyrim mis-resolves a non-ASCII mesh path.

See: docs/commentary/asset_convert_texture.md#ascii-asset-paths
"""
from asset_convert.game_paths import ascii_path, remove_non_ascii, set_namespace
from asset_convert.nif.tex_paths import path_bytes, path_text, rewrite_tex_path
from tes5_import.record_types.common import prefix_path


def test_ascii_path_folds_accents_and_escapes_the_rest():
    """Accents drop to the base letter; a letter with no base becomes _xx."""
    assert ascii_path('Arktwend\\Marktüberhang04.nif') == 'Arktwend\\Marktuberhang04.nif'
    assert ascii_path('KristallWeiß.dds') == 'KristallWei_df.dds'
    assert ascii_path('plain\\path.nif') == 'plain\\path.nif'


def test_nif_texture_string_reads_cp1252_and_writes_the_folded_name():
    """An authored 0xFC byte is a u-umlaut, so the written string names the folded file."""
    set_namespace('arktwendenglish')
    authored = b'w\xfcstea03rock.tga'
    assert path_text(authored) == 'wüstea03rock.tga'
    assert path_bytes(rewrite_tex_path(authored)) == \
        b'Textures\\arktwendenglish\\wustea03rock.dds'


def test_record_paths_match_the_folded_file_names():
    """The ESM names the same ASCII path the asset stages write."""
    set_namespace('arktwendenglish')
    assert prefix_path('Arktwend\\Marktüberhang04.nif') == \
        'arktwendenglish\\Arktwend\\Marktuberhang04.nif'


def test_remove_non_ascii_deletes_only_old_spellings(tmp_path):
    """A full run sweeps files an older build wrote under a non-ASCII name."""
    (tmp_path / 'Marktüberhang04.nif').write_bytes(b'old')
    (tmp_path / 'Marktuberhang04.nif').write_bytes(b'new')
    assert remove_non_ascii(tmp_path) == 1
    assert [p.name for p in tmp_path.iterdir()] == ['Marktuberhang04.nif']
