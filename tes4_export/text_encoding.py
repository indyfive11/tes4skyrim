"""The Windows code page a source plugin's text is stored in.

TES3 and TES4 plugins carry no encoding field: every string is raw bytes in
the code page of the edition that wrote it -- cp1252 Western, cp1251 Russian,
cp1250 Central European, the three OpenMW's `--encoding` offers. The
`sourceEncoding` setting names one for every plugin; `auto` reads each
plugin's own player-visible text and takes cp1251 when Cyrillic letters
outnumber Latin ones, else cp1252 (cp1250 bytes look Western, so it must be
named). Each reader calls `use` with its plugin's code page before decoding.

See: docs/commentary/tes4_export_text_encoding.md#choosing-the-code-page
"""

import os

#: The code pages a source plugin can be in, as OpenMW's --encoding offers them.
CODECS = ('cp1252', 'cp1251', 'cp1250')

#: The setting that reads each plugin's own text to choose.
AUTO = 'auto'

#: conversion_config.json key holding the setting.
CONFIG_KEY = 'sourceEncoding'

#: Carries the setting into every stage's worker processes.
ENV_VAR = 'TESCONV_SOURCE_ENCODING'

#: The `_HEADER.txt` key recording the code page a plugin's export was decoded in.
HEADER_KEY = 'Encoding'

#: How much text `choose` reads; real plugins split 98:2 or wider well before it.
SAMPLE_BYTES = 1 << 16

#: Every byte but ASCII letters, and every byte but cp1251's Cyrillic letters.
_NOT_LATIN = bytes(b for b in range(256)
                   if not (0x41 <= b <= 0x5A or 0x61 <= b <= 0x7A))
_NOT_CYRILLIC = bytes(b for b in range(0xC0) if b not in (0xA8, 0xB8))

_codec = CODECS[0]


def configure(setting) -> None:
    """Make `setting` (a CODECS entry, else auto) this run's choice."""
    value = str(setting or AUTO).strip().lower()
    os.environ[ENV_VAR] = value if value in CODECS else AUTO


def choose(texts) -> str:
    """The configured code page, or under auto the one `texts` are written in.

    `texts` yields one plugin's player-visible string payloads as bytes.
    """
    setting = os.environ.get(ENV_VAR, AUTO)
    if setting in CODECS:
        return setting
    sample = bytearray()
    for text in texts:
        sample += text
        if len(sample) >= SAMPLE_BYTES:
            break
    sample = bytes(sample)
    latin = len(sample.translate(None, _NOT_LATIN))
    cyrillic = len(sample.translate(None, _NOT_CYRILLIC))
    return 'cp1251' if cyrillic > latin else 'cp1252'


def use(codec: str) -> None:
    """Decode every later string in `codec`, the code page of the plugin being read."""
    global _codec
    _codec = codec


def current() -> str:
    """The code page strings decode in now."""
    return _codec


def decode(data) -> str:
    """Source plugin bytes as text, in the current plugin's code page."""
    return bytes(data).decode(_codec, errors='replace')
