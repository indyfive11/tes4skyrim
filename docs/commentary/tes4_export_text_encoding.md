# Text encoding: source code pages and Skyrim output

**Code:** `tes4_export/text_encoding.py`, `tes4_export/tes4_reader.py` (`get_string`,
`text_sample`), `tes4_export/tes3_reader.py` (`CodePage`, `text`),
`core/skyrim_language.py`, `tes5_import/base/writer.py` (`encode_string`).

TES3 and TES4 plugins have no encoding field. Every string is raw bytes in the
Windows code page of the edition that wrote it. Skyrim plugins are the same,
read in the code page of the language the game runs in. So the conversion
decodes with the source plugin's code page and encodes with the target game's.

## Choosing the code page

The `sourceEncoding` setting (`conversion_config.json`, or `--source-encoding`
for one run) is one of `cp1252` (Western), `cp1251` (Russian), `cp1250`
(Central European) — the three OpenMW's own `--encoding` offers — or `auto`,
the default.

Under `auto`, each plugin's player-visible text is read (TES4: `FULL`, `DESC`,
`NAM1`; TES3: `FNAM`, `TEXT`, `STRV`, `DESC` and an INFO's response `NAME`), up
to 64 KB, and the plugin is cp1251 when its letters in cp1251's Cyrillic range
(0xC0–0xFF, Ё/ё) outnumber its ASCII letters, else cp1252. EditorIDs and model
paths are left out because a Russian plugin keeps them in Latin letters.

Measured over every text payload (share of letters in the Cyrillic byte range):

| Plugin | Fields | Latin letters | Cyrillic-range letters |
|---|---|---|---|
| Oblivion.esm | 44,445 | 3,243,215 | 574 (0.02%) |
| Nehrim.esm (German) | 15,155 | 755,781 | 12,272 (1.60%) |
| Morrowind.esm | 38,064 | 4,108,988 | 5 (0.00%) |
| TR_Mainland.esm | 90,658 | 8,699,786 | 1 (0.00%) |
| Arktwend_English.esm | 23,825 | 1,037,058 | 231 (0.02%) |

Russian text is nearly all Cyrillic, so a simple majority separates the two
with a wide margin. cp1250 cannot be told from cp1252 this way (Polish and
Czech text is mostly ASCII letters too), so it has to be named in the setting.

The choice is per plugin, never carried from one plugin to the next:

- **TES4** is read only by the export. `use_source_codec` picks the plugin's
  code page, and every format job sent to a worker process carries it.
- **TES3** binaries are read all over the pipeline (export, the Morroblivion
  patch, the dialogue sidecar at import, body meshes), often several files in
  a row. Each `read_file` makes one `CodePage` for that file, chooses it after
  parsing and before naming records (Russian Morrowind's topic and cell IDs are
  Russian text), and every record and subrecord read from the file points at
  it. `get_string`/`text` decode with the subrecord's own page, so a master and
  its plugin can differ.

Both exports write the chosen page into `_HEADER.txt` as `Encoding=`.

## Writing Skyrim text

Skyrim reads a plugin's text in its language's code page. xEdit's Skyrim SE
defaults (`xEdit/xeInit.pas`, `wbAddLEncodingIfMissing('english', '1252')`
with `wbLEncodingDefault := UTF8`) give cp1252 for English and UTF-8 for every
other language. `core.skyrim_language.text_codec()` reads `sLanguage` from
`Skyrim.ini` and picks the same, and `encode_string` writes every string
subrecord with it. Text the target language cannot hold (Cyrillic on an
English install) becomes `?`. VMAD strings are UTF-8 in every language and are
written separately. This follows xEdit; it has not been checked against the
game exe.
