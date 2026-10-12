# asset_convert/sources/bsa_pack.py — BSA packing

**Code:** `asset_convert/sources/bsa_pack.py`

## Contents

- [Staging past the Windows path limit](#staging-past-the-path-limit)
- [What `pack_bsas` packs, and from which roots](#pack-bsas)
- [One archive set per imported mod](#one-archive-set-per-mod)
- [What a pack writes](#pack-layout)

## <a id="pack-layout"></a>What a pack writes

**Code:** `pack_bsas` and its helpers in `asset_convert/sources/bsa_pack.py`

- **Where.** The output folder is resolved from the export ROOT
  (`plugin_out_root`), never a plugin's record dir. A record dir holds no
  `sources.json`, so the registry reads as empty and the folder resolves to
  the pre-group `output/<plugin>/`, which does not exist for an imported mod.
  The pack then aborted with "output directory not found".
- **Which archives.** `<stem> - Textures.bsa` holds `textures/`. `<stem>.bsa`
  holds `meshes/` plus every other non-empty top-level folder (sound, scripts,
  …) except `SKSE/`, which stays loose (SKSE never sees an archived file).
- **Overflow.** A spec whose files exceed the per-archive budget is binned; the
  first bin keeps the auto-mounted name, and bin N is `<stem>_loader[_N-1]`.
  A loader ESL mounts both its `.bsa` and its `- Textures.bsa`, so each spec
  counts its own overflow and the specs share loader slots by index.
- **Stale sweep.** Overflow archives and loaders a previous, larger run left are
  removed. Otherwise a later run needing that loader slot again re-creates
  `<stem>_loader.esl` over a stale `<stem>_loader.bsa` and serves assets from
  the old conversion. `oblivion_loader*` is swept too, since loaders used to
  carry that name for every plugin. The stem is `glob.escape`d because a plugin
  name may hold `[` or `?`.

## <a id="one-archive-set-per-mod"></a>One archive set per imported mod

**Code:** `archive_stem` in `asset_convert/sources/bsa_pack.py`

An imported mod's plugins share one `output/<Mod>/` folder, and a pack reads the
whole folder, so each plugin's pack held the same assets. The archives were
named for the plugin being packed. Morrowind_ob.esp therefore wrote
`Morrowind_ob.bsa` / `Morrowind_ob - Textures.bsa` over the ESM's (same stem).
Plugins with different stems got a second, duplicate set that Skyrim mounts
twice. The archives are now named by following the packed plugin's masters
while they belong to the same mod, up to the member that masters no other
member. Every dependent packs the same set under that member's name, which is
always loaded when they are. Archives and `_loader` plugins named for a
dependent's own stem are deleted, since older builds wrote them. A member
that masters no other member keeps its own name. Aesthesia registers many
alternative grass plugins and converts one: a "root of the whole mod" rule
named that one's archive after `Grass Bloodmoon.esp`, a plugin the user never
loads, so it would never mount. Skyrim mounts `<stem>.bsa` for whichever
extension is loaded (`Morrowind_ob.esm` or `.esp`).

## Staging past the Windows path limit
<a id="staging-past-the-path-limit"></a>

**Code:** `long_path` in `asset_convert/sources/bsa_pack.py`

Each archive is staged into `output/<plugin>/_bsa_staging_<type>/` as a tree of
hardlinks, and BSArch is pointed at that root. The staged path is therefore
longer than the source path it mirrors, by the whole
`_bsa_staging_<type>\` segment.

For a plugin with a long folder name that is enough to cross Windows'
260-character `MAX_PATH`. Measured over the 15 plugins in `output/`, the
longest staged paths are:

```
_bsa_staging_misc = 266   Unique Landscapes Compilation v2.2.0   <-- FAILS
                    228   Oblivion.esm, Nehrim.esm
                    215   Morrowind_ob.esm
```

Only creature animdata reaches these lengths -- the offender is
`meshes\animationsetdata\tes4<plugin>_clear stream fishprojectData\
tes4<plugin>_clear stream fishproject.txt`, where the plugin name appears
TWICE below the staging root.

The failure is badly disguised. Under `-mt` BSArch reports only
`EAggregateException: One or more errors occurred` with no path, and the
pipeline truncates its output at 200 characters, so the message that reaches
the log is the tool's copyright banner. Dropping `-mt` produces the real
diagnosis: `"...evilspritecharacter.hkx". The system cannot find the path
specified`. The file is present; the path is simply too long to open.

`long_path` prefixes `\\?\`, which raises the limit to ~32,767 characters. It
is applied in two places, and both are needed:

1. `_link_or_copy`, so `os.link` can CREATE the staged path.
2. BSArch's INPUT root, so BSArch can WALK it.

Measured against a synthetic 381-character staging tree:

| input | output | result |
|---|---|---|
| plain | plain | `EAggregateException` |
| `\\?\` | plain | **159.2 MB packed** |
| plain | `\\?\` | `EAggregateException` |
| `\\?\` | `\\?\` | 159.2 MB packed |

Only the input root matters -- the archive being written sits directly in the
plugin dir and is never near the limit -- but prefixing both is harmless and
leaves nothing to rediscover.

The prefix is Windows-only and requires a normalized absolute path: `\\?\`
disables all path parsing, so a `/` separator or a `..` segment inside one is
passed through to the filesystem verbatim and fails. `long_path` returns its
argument unchanged off Windows, on a relative path, and on a path that already
carries the prefix.

### Why not just shorten the staging directory

`_bsa_misc` clears the limit by 2 characters and `_bsm` by 7, against a path
whose length the USER controls through both the repo location and the plugin
folder name. That is a reprieve, not a fix: the next long plugin name fails
again, and the failure mode is the disguised one above.

## What `pack_bsas` packs, and from which roots
<a id="pack-bsas"></a>

**Code:** `pack_bsas`, `_plugin_dir`, `_pack_spec`, `_pack_bin` in
`asset_convert/sources/bsa_pack.py`; `phase_pack` in `convert.py`

`pack_bsas` builds these inside the plugin's output folder:
- `<stem>.bsa` from `meshes/` plus every other non-texture folder (the misc
  dirs; `LOOSE_ONLY_DIRS` stay loose);
- `<stem> - Textures.bsa` from `textures/`.

Content past the 2 GiB budget spills into overflow bins. Bin 0 keeps the name
the plugin auto-mounts. Bin N lands in `<stem>_loader[_N]`, mounted by a
generated record-free ESL of the same stem.

The texture prune is a pack-time FILTER, not a delete. `output/<plugin>/textures/`
keeps the full tree, so loose-file testing is unaffected and re-packing is
idempotent. The source folders are never modified.

Three roots reach it, and they are not interchangeable:

| argument | is | used for |
|---|---|---|
| `export_root` | the export ROOT (`export/`, holding `sources.json`) | which output folder the plugin converted into |
| `export_dir` | the plugin's RECORD dir (`export/<plugin>` or `export/<mod>/<plugin>`) | the master names in `_HEADER.txt` |
| `manifest_dir` | the plugin's ASSET dir (`export/<mod group>/`) | `textures_used.txt`; see [pruned-dir references](asset_convert_texture.md#pruned-dir-references) |

An imported mod's plugins all convert into their MOD's folder, so the output
folder must be resolved from the export root. Resolving it from a record dir
reads no registry, falls back to `output/<plugin>/`, and aborts the pack with
"output directory not found". Without an `export_root`, the repo's own
`export/` is used.
