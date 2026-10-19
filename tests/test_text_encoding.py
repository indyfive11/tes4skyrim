"""Source code page per plugin, and the Skyrim output code page."""

import struct

import pytest

from core import skyrim_language
from tes4_export import tes3_reader, text_encoding
from tes5_import.base import writer

RUSSIAN = 'Меч Аркея'
GERMAN = 'Schwert des Kämpfers für Größe'


@pytest.fixture(autouse=True)
def _auto(monkeypatch):
    """Every test starts on the default `auto` setting."""
    monkeypatch.setenv(text_encoding.ENV_VAR, text_encoding.AUTO)


def test_auto_reads_the_plugins_own_letters():
    """Cyrillic text picks cp1251; German umlauts stay cp1252."""
    assert text_encoding.choose([RUSSIAN.encode('cp1251')] * 4) == 'cp1251'
    assert text_encoding.choose([GERMAN.encode('cp1252')] * 4) == 'cp1252'
    assert text_encoding.choose([]) == 'cp1252'


def test_named_setting_wins_over_the_text():
    """cp1250 cannot be detected, so a named setting is used as given."""
    text_encoding.configure('CP1250')
    assert text_encoding.choose([RUSSIAN.encode('cp1251')]) == 'cp1250'
    text_encoding.configure('nonsense')
    assert text_encoding.choose([RUSSIAN.encode('cp1251')]) == 'cp1251'


def _sub(sig: str, data: bytes) -> bytes:
    """One TES3 subrecord."""
    return sig.encode() + struct.pack('<I', len(data)) + data


def _record(sig: str, body: bytes) -> bytes:
    """One TES3 record."""
    return sig.encode() + struct.pack('<III', len(body), 0, 0) + body


def _tes3_file(path, name: str, codec: str) -> None:
    """A TES3 plugin with one book whose ID and title are `name` in `codec`."""
    raw = name.encode(codec) + b'\x00'
    book = _record('BOOK', _sub('NAME', raw) + _sub('FNAM', raw * 8))
    path.write_bytes(_record('TES3', _sub('HEDR', bytes(300))) + book)


def test_each_tes3_file_keeps_its_own_code_page(tmp_path):
    """A Russian master read after its English plugin does not re-decode the plugin."""
    _tes3_file(tmp_path / 'plugin.esp', 'Sword of Ages', 'cp1252')
    _tes3_file(tmp_path / 'master.esm', RUSSIAN, 'cp1251')
    _, plugin = tes3_reader.read_file(str(tmp_path / 'plugin.esp'))
    header, master = tes3_reader.read_file(str(tmp_path / 'master.esm'))
    assert master[0].record_id == RUSSIAN
    assert header.page.codec == 'cp1251'
    fnam = tes3_reader.get_subrecord(plugin[0], 'FNAM')
    assert plugin[0].page.codec == 'cp1252'
    assert tes3_reader.get_string(fnam) == 'Sword of Ages'


@pytest.mark.parametrize('language, codec', [('english', 'cp1252'), ('russian', 'utf-8')])
def test_output_follows_the_skyrim_language(monkeypatch, language, codec):
    """English Skyrim reads plugin text as cp1252, every other language as UTF-8."""
    monkeypatch.setattr(skyrim_language, 'language', lambda: language)
    skyrim_language.text_codec.cache_clear()
    try:
        assert writer.encode_string('Größe') == 'Größe'.encode(codec) + b'\x00'
    finally:
        skyrim_language.text_codec.cache_clear()
