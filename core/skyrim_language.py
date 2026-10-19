"""The language the player's Skyrim runs in, and the code page it reads plugin text in.

Skyrim.ini's `[General] sLanguage` names the language. A plugin's text is
read in that language's code page: Windows-1252 for English, UTF-8 for every
other language, as xEdit's Skyrim SE defaults have it.

See: docs/commentary/tes4_export_text_encoding.md#writing-skyrim-text
"""

import configparser
import functools
from pathlib import Path

#: Where the install's language is set, and what an unset one reads as.
LANGUAGE_INI = Path.home() / 'Documents' / 'My Games' / 'Skyrim Special Edition' / 'Skyrim.ini'
DEFAULT_LANGUAGE = 'english'


def language() -> str:
    """Skyrim.ini's `[General] sLanguage`, lowercased, else English."""
    ini = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        ini.read(LANGUAGE_INI, encoding='utf-8')
    except (configparser.Error, UnicodeDecodeError):
        return DEFAULT_LANGUAGE
    return ini.get('General', 'sLanguage', fallback=DEFAULT_LANGUAGE).strip().lower() \
        or DEFAULT_LANGUAGE


@functools.lru_cache(maxsize=None)
def text_codec() -> str:
    """The code page Skyrim reads plugin text in: cp1252 in English, else UTF-8."""
    return 'cp1252' if language() == DEFAULT_LANGUAGE else 'utf-8'
