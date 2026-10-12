"""
Skyrim's own name and description for each skill, read from the player's
Skyrim when the runtime is packaged, for the stats window's skill list and
its tooltips.

Skyrim.esm is localized: each skill's AVIF (FormIDs 0x44C..0x45D, actor value
6 first) holds string ids for FULL and DESC, and the text is in
`strings\\skyrim_<language>.strings` (FULL) and `.dlstrings` (DESC). Nothing
here is committed: the table goes into TESRuntime.zip as
`SKSE/Plugins/MorrowindRuntime/skyrim_skills.txt`, one `av=name|description`
line per skill, in the language the install is set to.

See: docs/commentary/morrowind_runtime.md#skyrim-skill-list
"""

import configparser
import struct
from pathlib import Path

from asset_convert.sources.skyrim_assets import find_skyrim_data, get_asset_bytes
from tes5_import.base.tes5_reader import records

#: Where the runtime reads the table, inside TESRuntime.zip.
SKILL_TABLE = Path('SKSE') / 'Plugins' / 'MorrowindRuntime' / 'skyrim_skills.txt'

#: The first skill's actor value and AVIF FormID; the 18 skills run on in both.
FIRST_SKILL = 6
FIRST_SKILL_AVIF = 0x0000044C
SKILL_COUNT = 18

#: Where the install's language is set, and what an unset one reads as.
LANGUAGE_INI = Path.home() / 'Documents' / 'My Games' / 'Skyrim Special Edition' / 'Skyrim.ini'
DEFAULT_LANGUAGE = 'english'


def language() -> str:
    """Skyrim.ini's `[General] sLanguage`, else English."""
    ini = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        ini.read(LANGUAGE_INI, encoding='utf-8')
    except (configparser.Error, UnicodeDecodeError):
        return DEFAULT_LANGUAGE
    return ini.get('General', 'sLanguage', fallback=DEFAULT_LANGUAGE).strip().lower() \
        or DEFAULT_LANGUAGE


def _decode(raw: bytes) -> str:
    """A string table entry's text: UTF-8 where it is, else Windows-1252."""
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        return raw.decode('cp1252', errors='replace')


def parse_strings(data: bytes, prefixed: bool) -> dict:
    """`{id: text}` from a .strings file, or a .dlstrings/.ilstrings one (`prefixed`)."""
    count = struct.unpack_from('<I', data)[0]
    base = 8 + count * 8
    out = {}
    for i in range(count):
        sid, offset = struct.unpack_from('<II', data, 8 + i * 8)
        at = base + offset
        if prefixed:
            length = struct.unpack_from('<I', data, at)[0]
            raw = data[at + 4:at + 4 + length]
        else:
            raw = data[at:data.index(b'\0', at)]
        out[sid] = _decode(raw.rstrip(b'\0'))
    return out


def skill_string_ids(esm: Path) -> dict:
    """`{actor value: (FULL id, DESC id)}` for the 18 skills' AVIFs."""
    raw = esm.read_bytes()
    out = {}
    for rec in records(raw, b'AVIF'):
        index = (rec.form_id & 0x00FFFFFF) - FIRST_SKILL_AVIF
        if not 0 <= index < SKILL_COUNT:
            continue
        subs = rec.sub_map()
        ids = tuple(struct.unpack_from('<I', subs[tag])[0] if len(subs.get(tag, b'')) >= 4
                    else None for tag in (b'FULL', b'DESC'))
        out[FIRST_SKILL + index] = ids
    return out


def skill_table_text() -> str:
    """The table's text, or '' when no Skyrim install or strings file is found."""
    data_dir = find_skyrim_data()
    esm = Path(data_dir) / 'Skyrim.esm' if data_dir else None
    if not esm or not esm.is_file():
        return ''
    stem = 'strings' + chr(92) + 'skyrim_' + language()
    names = get_asset_bytes(stem + '.strings')
    descriptions = get_asset_bytes(stem + '.dlstrings')
    if not names or not descriptions:
        return ''
    names, descriptions = parse_strings(names, False), parse_strings(descriptions, True)
    lines = []
    for av, (name_id, desc_id) in sorted(skill_string_ids(esm).items()):
        text = descriptions.get(desc_id, '').replace(chr(92), chr(92) * 2).replace('\r', '')
        lines.append(f"{av}={names.get(name_id, '')}|{text.replace(chr(10), chr(92) + 'n')}")
    return '\n'.join(lines) + '\n'
