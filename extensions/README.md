# Add-ons

An add-on is an optional rule the conversion applies only when the settings
name it. The converter's own job is a faithful conversion; a change to how the
converted game PLAYS (how hard a weapon hits, which perks a weapon answers to)
is a choice, so it lives here, one package per rule, and is off unless asked
for.

| Add-on | What it does |
|---|---|
| [`oblivion_weapon_rule`](oblivion_weapon_rule/README.md) | A weapon hit a converted actor deals or takes follows Oblivion's damage rule |
| [`weapon_type_keywords`](weapon_type_keywords/README.md) | A converted weapon carries Skyrim's keyword for its weapon type |

## Enabling one

List the names in `conversion_config.json`:

```json
{ "extensions": ["oblivion_weapon_rule", "weapon_type_keywords"] }
```

The key absent, or an empty list, loads nothing. The run stops, with an error
that names the cause, when a listed name is not an add-on:

- no package under `extensions/` answers to it (the error lists what is
  available), or it is listed twice;
- its folder holds no package, as a leftover folder with only `__pycache__`
  in it does;
- its package defines none of the three hooks below.

A name is taken as written; one with a stray space is not an add-on name. The
run also stops on a slip of the key itself: `extensions` in another case, or
with one "s" less or more (`extension`, `Extensions`). Nothing else is guessed
at: any other key is another setting, and names no add-on.

`convert.py` is the entry point that reads the list. It pins it in its own
`TESCONV_EXTENSIONS` environment variable before the import step converts a
record, and a process started from it with that environment inherits it. That
is all that is promised: a pool worker gets its environment from Python's
process start method, and no worker converts an actor or a weapon today.
`python -m tes5_import` does not read the list, so it runs with no add-on and
says so when it drops a list it inherited. The hooks run in the listed order.

## One list for a master and the plugins that depend on it

An add-on changes the plugin it is built into, and a dependent's overrides are
worked out against its master's built records. So a master and every plugin
that depends on it are built with the same add-on list, in the same order. The
order counts because it is part of what a build writes: keywords and actor
subrecords follow it, and a later add-on sees the records of the earlier ones.

The build holds you to it. A plugin built with add-ons records their names, in
order, as `"extensions"` in its `<plugin>.manifest.json`; a plugin built with
none writes the manifest it always wrote, without the key. When a dependent is
imported, the manifest of each master its header names is compared with the
run's own list, and a difference stops the run with the master's name, the
list it was built with and the list of this run. A master without the key was
built with none. A master further up the chain was compared when the plugin
below it was built.

To add or drop an add-on, change the list and rebuild the master, then the
plugins that depend on it. A tool that loads a master's manifest outside
`convert.py` (the override audit) is held to the same comparison; give it the
list in `TESCONV_EXTENSIONS`.

## Off means byte-identical

With no add-on enabled every file the converter writes is byte for byte what
it writes without the `extensions/` folder: each hook returns a neutral value
(`{}`, `b''`, `[]`) without importing any add-on, the call sites only append
that value, and the manifest gets no key. `tests/test_extensions.py` holds the
proof for NPC_, CREA and WEAP records and for the manifest file; it uses
stand-in add-ons only, so it runs without this folder. Each add-on's own tests
show that switching it on changes nothing but what it documents.

## The three hooks

The loader, `core/extensions.py`, is the only add-on code in the converter.
An add-on's package defines any of these functions:

| Hook | Called | Returns |
|---|---|---|
| `create_records(writer, context)` | once per plugin, where the converter creates its own support records | `{EditorID: FormID}` of the records it wrote or adopted that a script or a later add-on may name |
| `actor_subrecords(kind, rec)` | for every converted actor, right after its spell list; `kind` is `'NPC_'` or `'CREA'`, `rec` the exported record | packed subrecords, `b''` for none |
| `weapon_keywords(rec, anim_type)` | for every converted WEAP; `anim_type` is the Skyrim animation type it was given | a list of keyword FormIDs written after the converter's own, `[]` for none |
| `reset()` | before the first `create_records` of each plugin | nothing; clears state kept per plugin |

A hook that raises, or returns another type than this table names, ends the
run with an error naming the add-on and the hook. It is never turned into a
skipped record. Two more rules on what a hook returns:

- `create_records` may not return an EditorID the converter holds under
  another FormID, nor one an earlier add-on returned; either ends the run.
- A keyword `weapon_keywords` returns twice, or two add-ons return, is written
  once. The converter's own keywords are not compared, so return none of
  those.

`context` is a `core.extensions.RecordContext`:

- `source_is_tes4`: whether the source plugin is an Oblivion-format one;
- `records`: the plugin's own exported records, `{signature: [record]}`;
- `master_export`: its masters' exported records, as the importer loaded them;
- `game_settings(defaults)`: the source game's settings named in `defaults`,
  overlaid by the masters' GMSTs and then the plugin's own, and whether the
  plugin authors any of them;
- `form(edid)`: the FormID of a record the conversion owns, or an earlier
  add-on returned, by EditorID. The context reads a copy of the converter's
  table; a record gets into the table only by being returned;
- `master_formid(sig, edid)` and `adopt_or_write(writer, sig, edid, build)`:
  how a dependent plugin takes its master's record of the same EditorID
  instead of writing a second one.

## Writing one

1. Create `extensions/<name>/__init__.py`; `<name>` is lowercase letters,
   digits and underscores, and is what the settings list.
2. Define the hooks it needs, at least one of the three, at package level
   (import them from a module beside it).
3. Import nothing of the converter except what the loader's docstring lists:
   the packing helpers of `tes5_import.base.writer`, the record accessors of
   `tes5_import.base.text_reader`, and the two calls on the `writer` it is
   handed, `derive_formid(site, key)` and `add_record(signature, record)`.
   Carry a copy of anything else (a formula, a table, a packer), so a change
   inside the converter cannot break the add-on.
4. Give every record a FormID from `writer.derive_formid(<signature>,
   <EditorID>)` and let a dependent adopt its master's with
   `context.adopt_or_write`: FormIDs then never move between builds.
5. Name no plugin, and no record that is not a cited vanilla one.
6. Add `README.md` (the rule, its records, its limits) and
   `tests/extensions/test_<name>.py`, which must show that with the add-on off
   the records are byte-identical and with it on only the documented bytes
   differ.

An audit that reads a BUILT plugin may use the tree's plugin reader
(`tools.esm.tes5_esm_reader`) and is run as `python -m
extensions.<name>.audit`.
