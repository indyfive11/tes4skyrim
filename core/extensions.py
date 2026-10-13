"""Optional add-ons: rules the conversion applies only when the settings name them.

An add-on is a package ``extensions/<name>/`` at the repo root. It is loaded
only when ``"extensions": ["<name>", ...]`` in conversion_config.json lists it.
convert.py pins the list in its own ``TESCONV_EXTENSIONS`` env var before the
import step converts a record, and a process started from it with that
environment inherits it; nothing more is promised of a pool worker. Absent or
empty means no add-on. ``python -m tes5_import`` does not read the list and
runs with none.

This file is the only add-on code in the converter. It offers three hooks, each
calling the same-named function of every enabled add-on that defines it, in the
listed order:

``create_records(writer, context) -> {EditorID: FormID}``
    Once per plugin, where the conversion creates its own support records. The
    add-on writes its records and returns the ones a script or a condition may
    name. ``context`` is a `RecordContext`. Neutral value: ``{}``.
``actor_subrecords(kind, rec) -> bytes``
    Packed subrecords that follow a converted actor's spell list; ``kind`` is
    'NPC_' or 'CREA' and ``rec`` the exported record. Neutral value: ``b''``.
``weapon_keywords(rec, anim_type) -> [FormID]``
    Keywords a converted WEAP carries after the conversion's own; ``anim_type``
    is the Skyrim animation type the weapon was given. A keyword two add-ons
    return, or one returns twice, is written once; the conversion's own are
    not compared, so an add-on returns none of those. Neutral value: ``[]``.

An add-on that keeps state per plugin defines ``reset()``, which runs for every
enabled add-on before the first ``create_records`` of each plugin.

**These end the run**, with a SystemExit that no ``except Exception`` around a
single record swallows (an `ExtensionError`; from `set_extensions`, one that
names the config key): a name no real package answers to, a package defining
none of the three hooks, a hook that raises or returns another type than
listed above, a returned EditorID the converter or an earlier add-on already
holds, and a master built with another add-on list than the plugin that
depends on it. That list is recorded in the master's manifest, and its order
counts. An error raised while an add-on's package is being imported is not
wrapped: it is passed on as it is.

**With no add-on enabled every file the converter writes is byte-identical to
what it writes without this file**: each hook returns its neutral value without
importing anything, the call sites append that value to what they already
wrote, and the manifest gets no key.

What an add-on may rely on, and nothing else of the converter:

- the hooks above and `RecordContext`;
- the record packing helpers of ``tes5_import.base.writer`` (``pack_record``,
  ``pack_subrecord``, ``pack_string_subrecord`` and their typed siblings);
- two calls on the ``writer`` it is handed: ``derive_formid(site, key)``, the
  FormID that is a function of an authored key alone, and
  ``add_record(signature, packed record)``;
- `RecordContext.adopt_or_write` and `RecordContext.master_formid`, by which a
  dependent plugin takes its master's record of the same EditorID;
- the text reader's accessors of an exported record
  (``tes5_import.base.text_reader``: ``get_str``, ``get_int``, ``get_float``,
  ``get_formid``);
- in an audit of a BUILT plugin only, never in a hook: the tree's plugin
  reader, ``tools.esm.tes5_esm_reader``.

See: docs/reference/pipeline.md#optional-add-ons
"""
import importlib
import json
import os
import re
from pathlib import Path

#: conversion_config.json key: the list of add-on names to load, in hook order.
EXTENSIONS_CONFIG_KEY = "extensions"

#: Spellings of that key, lower-cased, that are a slip of it and never another setting.
KEY_SLIPS = ("extension", "extensions", "extensionss")

EXTENSIONS_ENV_VAR = "TESCONV_EXTENSIONS"

#: The package every add-on is a sub-package of.
PACKAGE = "extensions"

#: The hooks; a package defining none of them is not an add-on.
HOOKS = ("create_records", "actor_subrecords", "weapon_keywords")

#: Key of a built plugin's manifest: the add-ons it was built with, in hook order; absent for none.
MANIFEST_KEY = "extensions"

#: An add-on's name: a lowercase Python identifier, so it is a folder and a module at once.
_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")

#: The add-ons loaded for the env var's current value: {'key': that value, 'modules': (module, ...)}.
_LOADED = {"key": None, "modules": ()}



# ---------------------------------------------------------------------------
# Which add-ons are enabled
# ---------------------------------------------------------------------------

class ExtensionError(SystemExit):
    """An add-on problem; a SystemExit, so it ends the run from inside any ``except Exception``."""

    def __init__(self, reason: str):
        """`reason` is the bare sentence; the exit message says it is about add-ons."""
        super().__init__(f"ERROR: add-ons: {reason}")
        self.reason = reason


def available() -> list:
    """The names of the add-on packages this install holds."""
    root = Path(__file__).resolve().parent.parent / PACKAGE
    return sorted(p.name for p in root.iterdir() if (p / "__init__.py").is_file()) if root.is_dir() else []


def _load(name: str):
    """The add-on package `name`; ExtensionError unless a real package defining a hook answers to it."""
    if not _NAME.match(name):
        raise ExtensionError(f'"{name}" is not an add-on name (available: {", ".join(available()) or "none"})')
    try:
        module = importlib.import_module(f"{PACKAGE}.{name}")
    except ModuleNotFoundError as exc:
        if exc.name not in (PACKAGE, f"{PACKAGE}.{name}"):
            raise
        raise ExtensionError(f'there is no add-on "{name}" '
                             f'(available: {", ".join(available()) or "none"})') from exc
    if not getattr(module, "__file__", None):
        raise ExtensionError(f'"{name}" is a folder without a package (no {PACKAGE}/{name}/__init__.py), '
                             f'not an add-on (available: {", ".join(available()) or "none"})')
    if not any(callable(getattr(module, hook, None)) for hook in HOOKS):
        raise ExtensionError(f'the package "{name}" defines none of the hooks {", ".join(HOOKS)}')
    return module


def _load_all(names) -> tuple:
    """The packages of `names`, in order; ExtensionError for an unknown or repeated one."""
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise ExtensionError(f'add-on listed more than once: {", ".join(repeated)}')
    return tuple(_load(name) for name in names)


def configured_names(config: dict) -> list:
    """The add-on names a config lists; ExtensionError for a misspelled key or a value that is no list of names."""
    slips = sorted(key for key in config if key != EXTENSIONS_CONFIG_KEY and key.lower() in KEY_SLIPS)
    if slips:
        raise ExtensionError(f'the key "{slips[0]}" is not read: spell it "{EXTENSIONS_CONFIG_KEY}"')
    names = config.get(EXTENSIONS_CONFIG_KEY) or []
    if not isinstance(names, list) or not all(isinstance(name, str) for name in names):
        raise ExtensionError(f'"{EXTENSIONS_CONFIG_KEY}" must be a list of add-on names, not {names!r}')
    return list(names)


def set_extensions(config: dict) -> None:
    """Pin the config's add-ons in this process's environment; one that cannot be loaded ends the run."""
    try:
        names = configured_names(config)
        _load_all(names)
    except ExtensionError as exc:
        raise SystemExit(f'ERROR: conversion_config.json "{EXTENSIONS_CONFIG_KEY}": {exc.reason}') from exc
    os.environ[EXTENSIONS_ENV_VAR] = ",".join(names)
    if names:
        print(f"  Add-ons: {', '.join(names)}")


def clear_extensions() -> None:
    """Run this process with no add-on, whatever it inherited: for an entry point that does not read the list."""
    inherited = os.environ.get(EXTENSIONS_ENV_VAR, "")
    os.environ[EXTENSIONS_ENV_VAR] = ""
    if inherited.strip(", "):
        print(f"  Add-ons: none ({EXTENSIONS_ENV_VAR}={inherited} is ignored here: "
              f"add-ons are enabled through convert.py)")


def enabled() -> tuple:
    """The add-on packages this run loads, in the listed order; () when none is named."""
    raw = os.environ.get(EXTENSIONS_ENV_VAR, "")
    if raw != _LOADED["key"]:
        modules = _load_all([name.strip() for name in raw.split(",") if name.strip()])
        _LOADED.update(key=raw, modules=modules)
    return _LOADED["modules"]


def _short(module) -> str:
    """An add-on package's name as the settings list it."""
    return module.__name__.rpartition(".")[2]


# ---------------------------------------------------------------------------
# A master and its dependents are built with one add-on list
# ---------------------------------------------------------------------------

def build_stamp() -> dict:
    """What a built plugin's manifest records of this run's add-ons; {} for none."""
    names = [_short(module) for module in enabled()]
    return {MANIFEST_KEY: names} if names else {}


def check_master_build(path: str, manifest: dict) -> None:
    """End the run unless the master with this loaded `manifest` was built with this run's add-on list.

    A manifest without the key was built with none. The order counts: hooks
    run in the listed order, so it is part of what a build writes.
    See: docs/reference/pipeline.md#optional-add-ons
    """
    built, now = manifest.get(MANIFEST_KEY, []), build_stamp().get(MANIFEST_KEY, [])
    if built != now:
        raise ExtensionError(
            f'master {manifest.get("source") or path} was built with the add-ons {json.dumps(built)}, '
            f'this run has {json.dumps(now)}. A master and the plugins that depend on it need the same '
            f'"{EXTENSIONS_CONFIG_KEY}" list, in the same order: rebuild the one that differs.')


# ---------------------------------------------------------------------------
# What create_records is handed
# ---------------------------------------------------------------------------

class RecordContext:
    """What an add-on's ``create_records`` may read about the plugin being built.

    ``source_is_tes4``  whether the source plugin is an Oblivion-format one.
    ``records``         the plugin's own exported records: {signature: [record]}.
    ``master_export``   its masters' exported records, as the importer loaded them
                        (None for a plugin without masters); read through
                        `game_settings`, or handed back to the converter.

    `form` answers for the records the conversion owns and the ones an earlier
    add-on returned. `game_settings` reads only the names in the defaults it
    is given, each as a float, and tells whether THIS plugin authors any.
    """

    def __init__(self, source_is_tes4: bool, records: dict, import_context=None, owned: dict = None):
        """`import_context` is the importer's master context or None; `owned` its {EditorID: FormID}, copied."""
        self.source_is_tes4 = bool(source_is_tes4)
        self.records = records
        self.master_export = getattr(import_context, "master_export", None)
        self._master_index = getattr(import_context, "master_index", None)
        self._owned = dict(owned or {})

    def form(self, edid: str) -> int:
        """A conversion-owned or earlier add-on's record by EditorID; 0 for none."""
        return self._owned.get(edid, 0)

    def master_formid(self, sig: str, edid: str) -> int:
        """A master's converted record `edid` of type `sig`; 0 when none has it."""
        if self._master_index is None:
            return 0
        return self._master_index.find_by_edid(sig.encode(), edid)

    def adopt_or_write(self, writer, sig: str, edid: str, build) -> tuple:
        """(FormID, written): a master's record `edid`, else ``build(FormID)`` written as this plugin's.

        The new FormID is ``writer.derive_formid(sig, edid)``, so it never moves between builds.
        """
        fid = self.master_formid(sig, edid)
        if fid:
            return fid, False
        fid = writer.derive_formid(sig, edid)
        writer.add_record(sig, build(fid))
        return fid, True

    def game_settings(self, defaults: dict) -> tuple:
        """(settings, authors any): `defaults`, then the masters' GMSTs, then its own."""
        from tes5_import.actors.combat_style import game_settings
        return game_settings(self.records, self.master_export, defaults)


# ---------------------------------------------------------------------------
# The hooks
# ---------------------------------------------------------------------------

def _is_formid(value) -> bool:
    """Whether `value` is an int a FormID field holds."""
    return type(value) is int and 0 <= value <= 0xFFFFFFFF


def _is_forms(value) -> bool:
    """Whether `value` is {EditorID: FormID}."""
    return isinstance(value, dict) and all(isinstance(edid, str) and _is_formid(fid)
                                           for edid, fid in value.items())


def _is_keywords(value) -> bool:
    """Whether `value` is a list or tuple of FormIDs."""
    return isinstance(value, (list, tuple)) and all(_is_formid(fid) for fid in value)


#: Hook -> (the test its returned value must pass, that type in words).
RETURNS = {
    "create_records": (_is_forms, "a dict {EditorID: FormID}"),
    "actor_subrecords": (lambda value: isinstance(value, bytes), "bytes"),
    "weapon_keywords": (_is_keywords, "a list of FormIDs"),
    "reset": (lambda value: True, "anything"),
}


def _defining(hook: str) -> list:
    """The enabled add-ons that define `hook`, in the listed order."""
    return [module for module in enabled() if callable(getattr(module, hook, None))]


def _call(module, hook: str, *args):
    """The add-on's ``hook(*args)``; ExtensionError when it raises or returns another type than documented."""
    accepts, wanted = RETURNS[hook]
    try:
        value = getattr(module, hook)(*args)
    except Exception as exc:
        raise ExtensionError(f'"{_short(module)}" {hook} raised {type(exc).__name__}: {exc}') from exc
    if not accepts(value):
        raise ExtensionError(f'"{_short(module)}" {hook} returned {value!r:.80}, not {wanted}')
    return value


def reset() -> None:
    """Clear every enabled add-on's per-plugin state."""
    for module in _defining("reset"):
        _call(module, "reset")


def create_records(writer, context: RecordContext) -> dict:
    """Let each enabled add-on write its records; {EditorID: FormID} of those they name, {} for none.

    An add-on sees through ``context.form`` what the add-ons before it
    returned. An EditorID an earlier add-on returned, or one the converter
    holds under another FormID, ends the run.
    """
    made = {}
    reset()
    for module in _defining("create_records"):
        found = _call(module, "create_records", writer, context)
        taken = sorted(edid for edid, fid in found.items()
                       if edid in made or context._owned.get(edid, fid) != fid)
        if taken:
            raise ExtensionError(f'"{_short(module)}" create_records returned {", ".join(taken)}, '
                                 f'which the converter or an earlier add-on already holds')
        context._owned.update(found)
        made.update(found)
    return made


def actor_subrecords(kind: str, rec: dict) -> bytes:
    """The add-ons' packed subrecords after an actor's spell list; b'' for none."""
    return b"".join(_call(module, "actor_subrecords", kind, rec) for module in _defining("actor_subrecords"))


def weapon_keywords(rec: dict, anim_type: int) -> list:
    """The add-ons' keyword FormIDs for a WEAP, each once among them; [] for none."""
    found = (fid for module in _defining("weapon_keywords")
             for fid in _call(module, "weapon_keywords", rec, anim_type) if fid)
    return list(dict.fromkeys(found))
