"""The add-on loader: which add-ons a run loads, its hooks, what ends a run, and nothing at all when none is named.

Every add-on here is a stand-in made for one test, so this file needs neither the
``extensions/`` folder nor the shipped add-ons' own tests.

See: docs/reference/pipeline.md#optional-add-ons
"""

import collections
import json
import os
import struct
import subprocess
import sys
import types

import pytest

import convert
from core import extensions as loader
from output_layout import paths
from tes5_import import __main__ as import_main
from tes5_import import pipeline, pipeline_records
from tes5_import.base.writer import PluginWriter, pack_subrecord
from tes5_import.overrides.builder import generic_substitutions
from tes5_import.overrides.manifest import (MasterManifest, load_master_manifests, manifest_path,
                                            write_manifest)
from tes5_import.record_types.common import VENDOR_KYWD
from tes5_import.record_types import magic_variants
from tes5_import.record_types.creature import convert_CREA
from tes5_import.record_types.equipment import convert_WEAP
from tes5_import.record_types.npc import convert_NPC_
from tes5_import.registry import IMPORT_DISPATCH
from tests.extension_records import (CREA, EFFECTS, NPC, OTHER_ENTRIES, WEAPONS, VariantWriter, effect_entry,
                                     mgef, subrecords, weap)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: A subrecord and a keyword no converter writes.
MARK = pack_subrecord('XXXX', b'\x01\x02\x03')
MARK_KEYWORD = 0x01ABCDEF

#: A pair (archetype, actor value) no converter writes on an attribute effect.
MARK_EFFECT = (77, 99)

#: A tuple subclass holding a pair: a type of its own, so not the tuple a hook returns.
NAMED_PAIR = collections.namedtuple('NAMED_PAIR', 'archetype actor_value')

#: (pair at a bound of one of its two fields, the pair one beyond it): unsigned and signed 32 bits.
FIELD_BOUNDS = [((0, 30), (-1, 30)), ((2 ** 32 - 1, 30), (2 ** 32, 30)),
                ((34, -2 ** 31), (34, -2 ** 31 - 1)), ((34, 2 ** 31 - 1), (34, 2 ** 31))]

#: One source record a built master's manifest pairs with its converted FormID.
MANIFEST_RECORDS = {'00000A11': {'fid': 0x01000A11, 'companions': []}}

#: The manifest file of a master built with no add-on, byte for byte.
MANIFEST_WITHOUT_ADD_ONS = ('{"version":1,"source":"FakeMaster.esm",'
                            '"records":{"00000A11":{"fid":16779793,"companions":[]}}}')


# ---------------------------------------------------------------------------
# Stand-ins
# ---------------------------------------------------------------------------

class _Writer:
    """The two writer calls an add-on may make."""

    def __init__(self):
        """Start with nothing written."""
        self.records = []

    def derive_formid(self, site, key):
        """A FormID that is a function of (site, key) alone."""
        return 0x02000000 | (sum(f'{site}|{key}'.encode()) & 0xFFFF)

    def add_record(self, sig, data):
        """Keep each written record."""
        self.records.append((sig, data))


class _Index:
    """A master index answering `find_by_edid` from a table."""

    def __init__(self, table: dict):
        """`table` is {(sig bytes, edid): FormID}."""
        self.table = table

    def find_by_edid(self, sig: bytes, edid: str) -> int:
        """The master's FormID, or 0."""
        return self.table.get((sig, edid), 0)


@pytest.fixture(autouse=True)
def _no_add_on(monkeypatch):
    """Each test starts with no add-on named and none loaded."""
    monkeypatch.delenv(loader.EXTENSIONS_ENV_VAR, raising=False)
    monkeypatch.setitem(loader._LOADED, 'key', None)
    monkeypatch.setitem(loader._LOADED, 'modules', ())


@pytest.fixture
def add_on_folder(monkeypatch, tmp_path):
    """A stand-in ``extensions`` package whose add-ons are the folders of `tmp_path`, which it yields."""
    before = set(sys.modules)
    package = types.ModuleType(loader.PACKAGE)
    package.__path__ = [str(tmp_path)]
    monkeypatch.setitem(sys.modules, loader.PACKAGE, package)
    yield tmp_path
    for name in set(sys.modules) - before:
        if name.startswith(loader.PACKAGE + '.'):
            del sys.modules[name]


def _install(monkeypatch, name: str, **hooks):
    """Make `extensions.<name>` an importable add-on package defining `hooks`."""
    module = types.ModuleType(f'{loader.PACKAGE}.{name}')
    module.__file__ = f'{loader.PACKAGE}/{name}/__init__.py'
    for hook, function in hooks.items():
        setattr(module, hook, function)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return module


def _quiet(_kind, _rec) -> bytes:
    """An ``actor_subrecords`` adding nothing: the least an add-on defines."""
    return b''


def _variants(entries=(('FOAT', 4),)) -> list:
    """[(EditorID, (archetype, actor value))] of the effect variants the converter builds for `entries`."""
    writer = VariantWriter()
    magic_variants.reset()
    try:
        magic_variants.build_av_variants([mgef(code) for code in EFFECTS],
                                         [effect_entry(*entry) for entry in entries], writer)
    finally:
        magic_variants.reset()
    return [(dict(subrecords(record))['EDID'].rstrip(b'\0').decode(),
             struct.unpack_from('<Ii', dict(subrecords(record))['DATA'], 64)) for _sig, record in writer.records]


def _enable(monkeypatch, *names):
    """Name the add-ons the way convert.py pins them for the run."""
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, ','.join(names))


# ---------------------------------------------------------------------------
# The settings
# ---------------------------------------------------------------------------

def test_no_key_or_an_empty_list_enables_nothing():
    """Absent, null and [] all read as no add-on."""
    for config in ({}, {'extensions': None}, {'extensions': []}):
        assert loader.configured_names(config) == []
        loader.set_extensions(config)
        assert os.environ[loader.EXTENSIONS_ENV_VAR] == '' and loader.enabled() == ()


def test_the_listed_add_ons_are_pinned_in_order_in_this_process(monkeypatch, capsys):
    """The env var holds the names as listed, `enabled` loads those packages in that order, and the run says so."""
    first = _install(monkeypatch, 'fake_one', actor_subrecords=_quiet)
    second = _install(monkeypatch, 'fake_two', actor_subrecords=_quiet)
    loader.set_extensions({'extensions': ['fake_two', 'fake_one']})
    assert os.environ[loader.EXTENSIONS_ENV_VAR] == 'fake_two,fake_one'
    assert loader.enabled() == (second, first)
    assert capsys.readouterr().out == '  Add-ons: fake_two, fake_one\n'
    loader.set_extensions({'extensions': ['fake_one']})
    assert loader.enabled() == (first,)


def test_the_environment_list_may_carry_spaces_and_empty_items(monkeypatch):
    """A hand-set variable reads as its names, in order."""
    first = _install(monkeypatch, 'fake_one', actor_subrecords=_quiet)
    second = _install(monkeypatch, 'fake_two', actor_subrecords=_quiet)
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, ' fake_two , fake_one ,,')
    assert loader.enabled() == (second, first)
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, ' ,, ')
    assert loader.enabled() == ()


@pytest.mark.parametrize('names', [['no_such_add_on'], ['fake_one', 'no_such_add_on'], ['Fake_One'],
                                   ['../fake_one'], ['fake_one.sub'], [''], [' fake_one'], ['fake_one\n'],
                                   ['fake_one', 'fake_one']])
def test_an_unknown_or_repeated_name_stops_the_run_and_pins_nothing(monkeypatch, names):
    """SystemExit with the key and the name in its message; the env var is left as it was.

    A name is taken as written: one with a stray space is not an add-on name.
    """
    _install(monkeypatch, 'fake_one', actor_subrecords=_quiet)
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, 'fake_one')
    with pytest.raises(SystemExit) as stop:
        loader.set_extensions({'extensions': names})
    message = str(stop.value)
    assert message.startswith('ERROR: conversion_config.json "extensions"')
    assert (names[-1] or '""') in message
    assert os.environ[loader.EXTENSIONS_ENV_VAR] == 'fake_one'


@pytest.mark.parametrize('value', ['fake_one', {'fake_one': True}, [1], True])
def test_a_value_that_is_not_a_list_of_names_stops_the_run(monkeypatch, value):
    """A string is not read as one name, nor a mapping as its keys."""
    _install(monkeypatch, 'fake_one', actor_subrecords=_quiet)
    with pytest.raises(loader.ExtensionError):
        loader.configured_names({'extensions': value})
    with pytest.raises(SystemExit):
        loader.set_extensions({'extensions': value})


@pytest.mark.parametrize('key', ['extension', 'Extensions', 'EXTENSIONS', 'Extension', 'extensionss'])
def test_a_slip_of_the_key_stops_the_run(monkeypatch, key):
    """The key in another case, or with one "s" less or more, is never read as no add-on."""
    _install(monkeypatch, 'fake_one', actor_subrecords=_quiet)
    for config in ({key: ['fake_one']}, {key: [], 'extensions': ['fake_one']}):
        with pytest.raises(loader.ExtensionError, match=f'"{key}" is not read'):
            loader.configured_names(config)
        with pytest.raises(SystemExit) as stop:
            loader.set_extensions(config)
        assert f'the key "{key}" is not read: spell it "extensions"' in str(stop.value)
        assert loader.EXTENSIONS_ENV_VAR not in os.environ


def test_only_a_slip_of_the_key_is_refused_and_any_other_key_is_another_setting():
    """No guessing beyond case and one "s": these keys name no add-on."""
    for key in ('addons', 'extensions ', 'extensionsDir', 'xtensions', 'extensionsss', 'outputDir'):
        assert loader.configured_names({key: ['no_such_add_on']}) == []


def test_an_unknown_name_in_the_environment_ends_the_run_at_every_hook(monkeypatch):
    """A process that inherits a name it cannot load never skips it: every hook raises the run-ending error."""
    _enable(monkeypatch, 'no_such_add_on')
    for call in (loader.enabled, loader.reset, loader.build_stamp, lambda: loader.actor_subrecords('NPC_', NPC),
                 lambda: loader.weapon_keywords({}, 1), lambda: loader.attribute_effect('FOAT', 4, {}),
                 _variants, lambda: loader.create_records(_Writer(), loader.RecordContext(True, {})),
                 lambda: loader.check_master_build('FakeMaster.esm.manifest.json', {})):
        with pytest.raises(loader.ExtensionError, match='there is no add-on "no_such_add_on"'):
            call()
    with pytest.raises(loader.ExtensionError):
        convert_NPC_(NPC)


def test_the_error_is_a_system_exit_and_not_an_exception():
    """What a per-record ``except Exception`` cannot swallow, and its message says it is about add-ons."""
    error = loader.ExtensionError('the reason')
    assert isinstance(error, SystemExit) and not isinstance(error, Exception)
    assert str(error) == 'ERROR: add-ons: the reason'
    assert error.reason == 'the reason'


def test_an_add_on_that_fails_to_import_is_not_reported_as_unknown(add_on_folder):
    """A missing module INSIDE an add-on is its own error, not 'there is no add-on'."""
    (add_on_folder / 'fake_broken').mkdir()
    (add_on_folder / 'fake_broken' / '__init__.py').write_text('import no_such_module_anywhere\n', encoding='utf-8')
    with pytest.raises(ModuleNotFoundError, match='no_such_module_anywhere'):
        loader._load('fake_broken')
    with pytest.raises(loader.ExtensionError, match='there is no add-on "fake_absent"'):
        loader._load('fake_absent')


def test_a_package_on_disk_that_defines_a_hook_is_an_add_on(add_on_folder):
    """The control for the two refusals below: the same folder, with a package and a hook in it, loads."""
    (add_on_folder / 'fake_real').mkdir()
    (add_on_folder / 'fake_real' / '__init__.py').write_text(
        'def weapon_keywords(rec, anim_type):\n    return [0x11]\n', encoding='utf-8')
    loader.set_extensions({'extensions': ['fake_real']})
    assert loader.weapon_keywords({}, 1) == [0x11]


def test_a_leftover_folder_without_a_package_is_not_an_add_on(monkeypatch, add_on_folder):
    """A folder holding only ``__pycache__`` imports as an empty namespace: refused, by the settings and at a hook."""
    (add_on_folder / 'fake_ghost' / '__pycache__').mkdir(parents=True)
    with pytest.raises(SystemExit) as stop:
        loader.set_extensions({'extensions': ['fake_ghost']})
    assert '"fake_ghost" is a folder without a package (no extensions/fake_ghost/__init__.py)' in str(stop.value)
    assert loader.EXTENSIONS_ENV_VAR not in os.environ
    _enable(monkeypatch, 'fake_ghost')
    with pytest.raises(loader.ExtensionError, match='folder without a package'):
        loader.actor_subrecords('NPC_', NPC)


@pytest.mark.parametrize('members', [{}, {'actor_subrecord': _quiet}, {'reset': lambda: None},
                                     {'weapon_keywords': [MARK_KEYWORD]}],
                         ids=['empty', 'misspelled hook', 'reset alone', 'a hook name that is not a function'])
def test_a_package_that_defines_no_hook_is_not_an_add_on(monkeypatch, members):
    """It would load, be named as enabled and do nothing: refused, with the four hook names in the message."""
    _install(monkeypatch, 'fake_idle', **members)
    with pytest.raises(SystemExit) as stop:
        loader.set_extensions({'extensions': ['fake_idle']})
    assert ('the package "fake_idle" defines none of the hooks '
            'create_records, actor_subrecords, weapon_keywords, attribute_effect') == str(stop.value).split(': ', 2)[2]
    _enable(monkeypatch, 'fake_idle')
    with pytest.raises(loader.ExtensionError, match='defines none of the hooks'):
        loader.weapon_keywords({}, 1)


@pytest.mark.parametrize('name', loader.available())
def test_every_shipped_add_on_passes_the_loaders_test_of_an_add_on(name):
    """Each package under extensions/ loads by its folder name; none shipped, none run."""
    assert loader._load(name).__name__ == f'{loader.PACKAGE}.{name}'


# ---------------------------------------------------------------------------
# The entry points: who pins the list, and who runs without add-ons
# ---------------------------------------------------------------------------

def test_the_import_step_pins_exactly_what_its_config_names(monkeypatch, tmp_path):
    """Through `convert.phase_import`: the config's list is pinned before the importer runs, an inherited one is
    replaced, and a list that cannot be loaded stops the step before the importer."""
    first = _install(monkeypatch, 'fake_one', actor_subrecords=_quiet)
    second = _install(monkeypatch, 'fake_two', actor_subrecords=_quiet)
    seen = []

    def importer(**_run):
        """Note what the importer would load, and convert nothing."""
        seen.append((os.environ[loader.EXTENSIONS_ENV_VAR], loader.enabled()))
        return 0, 0

    def step(config: dict) -> bool:
        """Run the import step of ``Fake.esp`` with `config`."""
        return convert.phase_import('Fake.esp', str(tmp_path / 'data'), '', str(tmp_path / 'export'), config,
                                    str(tmp_path / 'output'))

    (tmp_path / 'export' / 'Fake.esp').mkdir(parents=True)
    monkeypatch.setattr(convert, '_use_plugin_namespace', lambda _name: None)
    monkeypatch.setattr(convert, '_install_navmesh_cache', lambda _name, _config: None)
    monkeypatch.setattr(pipeline, 'import_plugin', importer)
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, 'fake_one')
    assert step({'extensions': ['fake_two', 'fake_one']}) is True
    assert step({}) is True
    assert seen == [('fake_two,fake_one', (second, first)), ('', ())]
    with pytest.raises(SystemExit, match='no_such_add_on'):
        step({'extensions': ['no_such_add_on']})
    assert len(seen) == 2


@pytest.mark.parametrize('entry', [import_main, pipeline], ids=['python -m tes5_import', 'pipeline.main'])
def test_an_import_main_that_does_not_read_the_list_runs_without_add_ons(monkeypatch, tmp_path, capsys, entry):
    """An inherited list is dropped, and said to be, before the importer runs."""
    _install(monkeypatch, 'fake_one', actor_subrecords=_quiet)
    seen = []

    def importer(*_args, **_run):
        """Note what the importer would load, and convert nothing."""
        seen.append((os.environ[loader.EXTENSIONS_ENV_VAR], loader.enabled()))
        return 0, 0

    monkeypatch.setattr(entry, 'import_plugin', importer)
    monkeypatch.setattr(sys, 'argv', ['import', str(tmp_path), '-o', str(tmp_path / 'Fake.esp')])
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, 'fake_one')
    entry.main()
    assert seen == [('', ())]
    assert ('  Add-ons: none (TESCONV_EXTENSIONS=fake_one is ignored here: '
            'add-ons are enabled through convert.py)\n') in capsys.readouterr().out


@pytest.mark.parametrize('inherited', [None, '', ' ,, '])
def test_clearing_says_nothing_when_no_add_on_was_inherited(monkeypatch, capsys, inherited):
    """The variable ends empty either way; only a dropped name is worth a line."""
    if inherited is not None:
        monkeypatch.setenv(loader.EXTENSIONS_ENV_VAR, inherited)
    loader.clear_extensions()
    assert os.environ[loader.EXTENSIONS_ENV_VAR] == '' and loader.enabled() == ()
    assert capsys.readouterr().out == ''


# ---------------------------------------------------------------------------
# The pipeline's create_records call
# ---------------------------------------------------------------------------

#: The pipeline's support-record step in a fresh process, with a stand-in add-on reporting what it is handed.
PRESCAN_PROBE = '''
import json, sys, types
from tes5_import import pipeline
from tes5_import.actors.stat_factions import STAT_FACTIONS
from tes5_import.base.owned_records import WELL_KNOWN_PROPERTIES
from tes5_import.base.writer import PluginWriter

case, folder = sys.argv[1], sys.argv[2]
seen = {}


class Index:
    def find_by_edid(self, sig, edid):
        return 0x01000C10 if (sig, edid) == (b"GLOB", "FakeMasterSwitch") else 0

    def find_all_by_edid(self, sig, edid):
        return [fid for fid in [self.find_by_edid(sig, edid)] if fid]


by_type = {"TERM": [{"Signature": "TERM", "FormID": "00000900"}]} if case == "not tes4" else {}
ctx = types.SimpleNamespace(master_index=Index(), master_export={"00000001": {"Signature": "GMST"}},
                            export_dir=None) if case == "dependent" else None
plugin_writer = PluginWriter(masters=["Skyrim.esm"] + ["FakeMaster.esm"] * bool(ctx), is_esm=True)


def create_records(writer, context):
    seen.update(
        calls=seen.get("calls", 0) + 1,
        own_records=context.records is by_type, own_writer=writer is plugin_writer,
        source_is_tes4=context.source_is_tes4,
        masters_export=context.master_export is getattr(ctx, "master_export", None),
        master_form=context.master_formid("GLOB", "FakeMasterSwitch"),
        stat_factions=sum(1 for edid in STAT_FACTIONS.values() if context.form(edid)))
    return {"FakeProbeForm": 0x02000ABC}


probe = types.ModuleType("extensions.fake_probe")
probe.__file__, probe.create_records = "stand-in", create_records
sys.modules[probe.__name__] = probe
pipeline._prescan_special_records(by_type, ctx, plugin_writer, folder, lambda _name: None)
seen.update(in_table=WELL_KNOWN_PROPERTIES.get("FakeProbeForm"), all_stat_factions=len(STAT_FACTIONS))
print(json.dumps(seen))
'''


def _prescan_probe(tmp_path, case: str) -> dict:
    """What the stand-in add-on was handed by that step (it leaves run state behind, hence the fresh process)."""
    env = dict(os.environ, **{loader.EXTENSIONS_ENV_VAR: 'fake_probe'})
    done = subprocess.run([sys.executable, '-B', '-c', PRESCAN_PROBE, case, str(tmp_path)], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize('case, tes4, master_form', [('root', True, 0), ('dependent', True, 0x01000C10),
                                                     ('not tes4', False, 0)])
def test_the_pipeline_hands_create_records_the_plugin_and_its_masters_and_keeps_what_it_returns(
        tmp_path, case, tes4, master_form):
    """Through `_prescan_special_records`, once: the plugin's own records and writer, the masters' context, the
    source kind, a table already holding a TES4 source's stat factions; the returned form lands in that table."""
    seen = _prescan_probe(tmp_path, case)
    assert seen['all_stat_factions'] > 0
    assert seen == {'own_records': True, 'own_writer': True, 'source_is_tes4': tes4, 'masters_export': True,
                    'master_form': master_form, 'stat_factions': seen['all_stat_factions'] if tes4 else 0,
                    'calls': 1, 'in_table': 0x02000ABC, 'all_stat_factions': seen['all_stat_factions']}


# ---------------------------------------------------------------------------
# A master and its dependents are built with one add-on list
# ---------------------------------------------------------------------------

def _built_master(monkeypatch, tmp_path, *add_ons) -> str:
    """The manifest path of ``FakeMaster.esm`` built under `tmp_path` with `add_ons` enabled."""
    for name in add_ons:
        _install(monkeypatch, name, actor_subrecords=_quiet)
    _enable(monkeypatch, *add_ons)
    plugin = paths('FakeMaster.esm', out_root=str(tmp_path)).esm
    plugin.parent.mkdir(parents=True, exist_ok=True)
    return write_manifest(str(plugin), 'FakeMaster.esm', MANIFEST_RECORDS)


def _load_as_dependent(tmp_path) -> MasterManifest:
    """What the importer loads for a plugin with the one master ``FakeMaster.esm``."""
    return load_master_manifests(['Skyrim.esm', 'FakeMaster.esm'], 1, str(tmp_path))


def test_a_build_without_add_ons_writes_the_manifest_it_always_wrote(monkeypatch, tmp_path):
    """No new key, no changed byte: with the variable empty, and with it absent."""
    path = _built_master(monkeypatch, tmp_path)
    assert path == manifest_path(str(paths('FakeMaster.esm', out_root=str(tmp_path)).esm))
    assert open(path, encoding='utf-8').read() == MANIFEST_WITHOUT_ADD_ONS
    monkeypatch.delenv(loader.EXTENSIONS_ENV_VAR)
    _built_master(monkeypatch, tmp_path)
    assert open(path, encoding='utf-8').read() == MANIFEST_WITHOUT_ADD_ONS
    assert loader.build_stamp() == {}


def test_a_build_with_add_ons_adds_one_key_holding_their_names_in_order(monkeypatch, tmp_path):
    """The file is the old one plus ``"extensions"``, last; nothing else moves."""
    path = _built_master(monkeypatch, tmp_path, 'fake_two', 'fake_one')
    assert open(path, encoding='utf-8').read() == (
        MANIFEST_WITHOUT_ADD_ONS[:-1] + ',"extensions":["fake_two","fake_one"]}')
    assert loader.build_stamp() == {loader.MANIFEST_KEY: ['fake_two', 'fake_one']}


@pytest.mark.parametrize('add_ons', [(), ('fake_one',), ('fake_two', 'fake_one')])
def test_a_dependent_built_with_its_masters_add_on_list_loads_the_manifest(monkeypatch, tmp_path, add_ons):
    """Same names in the same order, none included: the import goes on with the master's records."""
    _built_master(monkeypatch, tmp_path, *add_ons)
    manifest = _load_as_dependent(tmp_path)
    assert len(manifest) == 1
    assert manifest.output_formid('00000A11') == 0x01000A11


@pytest.mark.parametrize('master, dependent', [
    ((), ('fake_one',)), (('fake_one',), ()), (('fake_one',), ('fake_two',)),
    (('fake_one', 'fake_two'), ('fake_one',)), (('fake_one',), ('fake_one', 'fake_two')),
    (('fake_one', 'fake_two'), ('fake_two', 'fake_one'))],
    ids=['off then on', 'on then off', 'another add-on', 'one dropped', 'one added', 'another order'])
def test_a_dependent_built_with_another_add_on_list_than_its_master_stops(monkeypatch, tmp_path, master, dependent):
    """The run ends naming the master, the list it was built with and this run's; the order counts."""
    path = _built_master(monkeypatch, tmp_path, *master)
    for name in dependent:
        _install(monkeypatch, name, actor_subrecords=_quiet)
    _enable(monkeypatch, *dependent)
    with pytest.raises(loader.ExtensionError) as stop:
        _load_as_dependent(tmp_path)
    assert (f'master FakeMaster.esm was built with the add-ons {json.dumps(list(master))}, '
            f'this run has {json.dumps(list(dependent))}.') in str(stop.value)
    assert 'need the same "extensions" list, in the same order' in str(stop.value)
    merged = MasterManifest()
    with pytest.raises(loader.ExtensionError):
        merged.load(path)
    assert len(merged) == 0


@pytest.mark.parametrize('recorded', ['fake_one', None, {'fake_one': True}, [['fake_one']]])
def test_a_manifest_whose_add_on_list_cannot_be_read_as_this_runs_stops(monkeypatch, tmp_path, recorded):
    """A hand-edited value is never taken for a match, whatever this run enables."""
    path = _built_master(monkeypatch, tmp_path)
    payload = dict(json.loads(MANIFEST_WITHOUT_ADD_ONS), extensions=recorded)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle)
    for add_ons in ((), ('fake_one',)):
        _install(monkeypatch, 'fake_one', actor_subrecords=_quiet)
        _enable(monkeypatch, *add_ons)
        with pytest.raises(loader.ExtensionError, match='master FakeMaster.esm was built with'):
            _load_as_dependent(tmp_path)


def test_the_check_names_the_manifest_file_when_the_manifest_names_no_plugin(monkeypatch):
    """A manifest without ``source`` is still named, by its path."""
    _install(monkeypatch, 'fake_one', actor_subrecords=_quiet)
    _enable(monkeypatch, 'fake_one')
    with pytest.raises(loader.ExtensionError, match='master out/Fake.esm.manifest.json was built with'):
        loader.check_master_build('out/Fake.esm.manifest.json', {})
    assert loader.check_master_build('out/Fake.esm.manifest.json', {'extensions': ['fake_one']}) is None


# ---------------------------------------------------------------------------
# The hooks
# ---------------------------------------------------------------------------

def test_with_no_add_on_every_hook_returns_its_neutral_value_untouched():
    """Nothing is read off the writer, the context or the record, and a manifest gets nothing."""
    assert loader.enabled() == ()
    assert loader.create_records(None, None) == {}
    assert loader.actor_subrecords('NPC_', None) == b''
    assert loader.weapon_keywords(None, 1) == []
    assert loader.attribute_effect(None, None, None) is None
    assert loader.reset() is None
    assert loader.build_stamp() == {}
    assert loader.check_master_build(None, {}) is None


def test_with_no_add_on_no_add_on_code_is_imported():
    """A fresh process that converts an actor, a weapon and an attribute effect never imports the extensions package."""
    code = ('import sys\n'
            'from core import extensions\n'
            'from tes5_import.record_types.npc import convert_NPC_\n'
            'from tes5_import.record_types.creature import convert_CREA\n'
            'from tes5_import.record_types.equipment import convert_WEAP\n'
            'from tes5_import.record_types.magic_variants import build_av_variants\n'
            'from tests.extension_records import CREA, NPC, VariantWriter, effect_entry, mgef, weap\n'
            'convert_NPC_(NPC), convert_CREA(CREA), convert_WEAP(weap(0, "Weapons\\\\Iron\\\\LongSword.NIF"))\n'
            'assert build_av_variants([mgef("FOAT")], [effect_entry("FOAT", 4)], VariantWriter()) == 1\n'
            'extensions.create_records(None, None)\n'
            'print(sorted(m for m in sys.modules if m.split(".")[0] == extensions.PACKAGE))\n')
    env = {k: v for k, v in os.environ.items() if k != loader.EXTENSIONS_ENV_VAR}
    done = subprocess.run([sys.executable, '-B', '-c', code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip().splitlines()[-1] == '[]'


def test_hooks_run_in_the_listed_order_and_an_add_on_may_define_any_of_them(monkeypatch):
    """Each hook calls only the add-ons that define it; results are joined in the settings' order."""
    _install(monkeypatch, 'fake_one', actor_subrecords=lambda kind, rec: kind.encode() + b'1',
             weapon_keywords=lambda rec, anim: [0x11, 0x22])
    _install(monkeypatch, 'fake_two', actor_subrecords=lambda kind, rec: b'2',
             weapon_keywords=lambda rec, anim: (0x22, 0, anim))
    _install(monkeypatch, 'fake_records', create_records=lambda writer, context: {})
    _enable(monkeypatch, 'fake_one', 'fake_records', 'fake_two')
    assert loader.actor_subrecords('CREA', CREA) == b'CREA12'
    assert loader.weapon_keywords({}, 7) == [0x11, 0x22, 7]
    _enable(monkeypatch, 'fake_two', 'fake_one')
    assert loader.actor_subrecords('NPC_', NPC) == b'2NPC_1'
    assert loader.weapon_keywords({}, 7) == [0x22, 7, 0x11]
    assert loader.create_records(_Writer(), loader.RecordContext(True, {})) == {}


def test_weapon_keywords_gives_each_of_the_add_ons_keywords_once(monkeypatch):
    """A keyword one add-on returns twice, or two add-ons return, is kept where it came first; a zero is dropped."""
    _install(monkeypatch, 'fake_one', weapon_keywords=lambda rec, anim: [0x11, 0x11, 0, 0x22, 0x11])
    _install(monkeypatch, 'fake_two', weapon_keywords=lambda rec, anim: [0x33, 0x22, 0x33])
    _enable(monkeypatch, 'fake_one')
    assert loader.weapon_keywords({}, 1) == [0x11, 0x22]
    _enable(monkeypatch, 'fake_one', 'fake_two')
    assert loader.weapon_keywords({}, 1) == [0x11, 0x22, 0x33]
    _enable(monkeypatch, 'fake_two', 'fake_one')
    assert loader.weapon_keywords({}, 1) == [0x33, 0x22, 0x11]


def test_create_records_resets_every_add_on_first_and_hands_on_what_each_made(monkeypatch):
    """`reset` of all, then `create_records` in order; a later add-on finds an earlier one's forms."""
    calls = []

    def first(writer, context):
        """Write one record and name it."""
        calls.append(('first', context.form('FakeOne'), context.source_is_tes4))
        writer.add_record('GLOB', b'one')
        return {'FakeOne': 0x0200AAAA}

    def second(writer, context):
        """Read the first add-on's form and name one of its own."""
        calls.append(('second', context.form('FakeOne'), context.form('Owned'), context.source_is_tes4))
        return {'FakeTwo': 0x0200BBBB}

    _install(monkeypatch, 'fake_one', create_records=first, reset=lambda: calls.append('reset one'))
    _install(monkeypatch, 'fake_two', create_records=second, reset=lambda: calls.append('reset two'))
    _enable(monkeypatch, 'fake_one', 'fake_two')
    writer, owned = _Writer(), {'Owned': 0x01000001}
    made = loader.create_records(writer, loader.RecordContext(False, {}, None, owned))
    assert made == {'FakeOne': 0x0200AAAA, 'FakeTwo': 0x0200BBBB}
    assert calls == ['reset one', 'reset two', ('first', 0, False), ('second', 0x0200AAAA, 0x01000001, False)]
    assert writer.records == [('GLOB', b'one')]


# ---------------------------------------------------------------------------
# What an add-on cannot do: fail in silence, or take the converter's records
# ---------------------------------------------------------------------------

def _raising(*_args, **_named):
    """A hook, or a converter, that fails."""
    raise ZeroDivisionError('division by zero')


#: (hook, what the add-on's function does, the words the run-ending message must hold).
BAD_HOOKS = [
    ('create_records', _raising, 'create_records raised ZeroDivisionError: division by zero'),
    ('create_records', lambda writer, context: None, 'returned None, not a dict {EditorID: FormID}'),
    ('create_records', lambda writer, context: [('FakeForm', 1)], 'not a dict {EditorID: FormID}'),
    ('create_records', lambda writer, context: 'FakeForm', 'not a dict {EditorID: FormID}'),
    ('create_records', lambda writer, context: {'FakeForm': '0200AAAA'}, 'not a dict {EditorID: FormID}'),
    ('create_records', lambda writer, context: {0x0200AAAA: 0x0200AAAA}, 'not a dict {EditorID: FormID}'),
    ('create_records', lambda writer, context: {'FakeForm': True}, 'not a dict {EditorID: FormID}'),
    ('create_records', lambda writer, context: {'FakeForm': -1}, 'not a dict {EditorID: FormID}'),
    ('create_records', lambda writer, context: {'FakeForm': 2 ** 32}, 'not a dict {EditorID: FormID}'),
    ('actor_subrecords', _raising, 'actor_subrecords raised ZeroDivisionError: division by zero'),
    ('actor_subrecords', lambda kind, rec: None, 'actor_subrecords returned None, not bytes'),
    ('actor_subrecords', lambda kind, rec: 'PRKZ', "actor_subrecords returned 'PRKZ', not bytes"),
    ('actor_subrecords', lambda kind, rec: bytearray(MARK), 'not bytes'),
    ('weapon_keywords', _raising, 'weapon_keywords raised ZeroDivisionError: division by zero'),
    ('weapon_keywords', lambda rec, anim: None, 'weapon_keywords returned None, not a list of FormIDs'),
    ('weapon_keywords', lambda rec, anim: MARK_KEYWORD, 'not a list of FormIDs'),
    ('weapon_keywords', lambda rec, anim: {MARK_KEYWORD}, 'not a list of FormIDs'),
    ('weapon_keywords', lambda rec, anim: ['0001E711'], 'not a list of FormIDs'),
    ('weapon_keywords', lambda rec, anim: [True], 'not a list of FormIDs'),
    ('weapon_keywords', lambda rec, anim: [-1], 'weapon_keywords returned [-1], not a list of FormIDs'),
    ('weapon_keywords', lambda rec, anim: [2 ** 32], 'not a list of FormIDs'),
    ('attribute_effect', _raising, 'attribute_effect raised ZeroDivisionError: division by zero'),
    ('attribute_effect', lambda code, attribute, rec: [34, 30],
     'attribute_effect returned [34, 30], not None or a tuple (archetype, actor value) of two ints'),
    ('attribute_effect', lambda code, attribute, rec: 34, 'not None or a tuple'),
    ('attribute_effect', lambda code, attribute, rec: (34,), 'not None or a tuple'),
    ('attribute_effect', lambda code, attribute, rec: (34, 30, 0), 'not None or a tuple'),
    ('attribute_effect', lambda code, attribute, rec: ('34', 30), 'not None or a tuple'),
    ('attribute_effect', lambda code, attribute, rec: (34, 30.0), 'not None or a tuple'),
    ('attribute_effect', lambda code, attribute, rec: (True, 30), 'not None or a tuple'),
    ('attribute_effect', lambda code, attribute, rec: False, 'attribute_effect returned False, not None or'),
    ('attribute_effect', lambda code, attribute, rec: {34: 30}, 'not None or a tuple'),
    ('attribute_effect', lambda code, attribute, rec: NAMED_PAIR(34, 30), 'not None or a tuple'),
    ('reset', _raising, 'reset raised ZeroDivisionError: division by zero'),
]


def _state(*records):
    """The record loop's state for a root plugin holding `records`."""
    by_type = {}
    for rec in records:
        by_type.setdefault(rec['Signature'], []).append(rec)
    return types.SimpleNamespace(by_type=by_type, all_skip=set(), ctx=None, converted=0, errors=0,
                                 writer=PluginWriter(masters=['Skyrim.esm'], is_esm=True))


@pytest.mark.parametrize('hook, function, words', BAD_HOOKS, ids=[f'{row[0]}: {row[2]}' for row in BAD_HOOKS])
def test_a_hook_that_raises_or_returns_another_type_ends_the_run_naming_itself(monkeypatch, hook, function, words):
    """The run-ending error names the add-on and the hook; no value of the wrong type reaches a record."""
    _install(monkeypatch, 'fake_bad', **{'create_records': lambda writer, context: {}, hook: function})
    _enable(monkeypatch, 'fake_bad')
    calls = {'create_records': lambda: loader.create_records(_Writer(), loader.RecordContext(True, {})),
             'actor_subrecords': lambda: convert_NPC_(NPC),
             'weapon_keywords': lambda: convert_WEAP(weap(0, 'Weapons\\Iron\\LongSword.NIF')),
             'attribute_effect': _variants,
             'reset': loader.reset}
    with pytest.raises(loader.ExtensionError) as stop:
        calls[hook]()
    assert str(stop.value).startswith(f'ERROR: add-ons: "fake_bad" {hook} ') and words in str(stop.value)


def test_a_failing_hook_ends_the_run_from_inside_the_record_loop_and_an_override(monkeypatch):
    """The loop that turns a converter's own error into a counted one, and the override builder that turns it
    into "no answer", both let the add-on's error through."""
    monkeypatch.setattr(pipeline_records, '_stage_runtime_sidecars', lambda _st, _export_dir: None)
    sword = weap(0, 'Weapons\\Iron\\LongSword.NIF')
    mace = dict(sword, **{'DATA.Type': '2'})
    with monkeypatch.context() as broken_converter:
        broken_converter.setitem(IMPORT_DISPATCH, 'WEAP', _raising)
        broken = _state(sword, NPC)
        pipeline_records._phase1_simple_records(broken, '.', lambda _label: None, None)
        assert (broken.converted, broken.errors) == (1, 1)
        assert generic_substitutions(mace, sword) is None
    _install(monkeypatch, 'fake_bad', weapon_keywords=_raising, actor_subrecords=lambda kind, rec: 'PRKZ')
    _enable(monkeypatch, 'fake_bad')
    for records, hook in (((sword,), 'weapon_keywords'), ((NPC,), 'actor_subrecords')):
        state = _state(*records)
        with pytest.raises(loader.ExtensionError, match=f'"fake_bad" {hook} '):
            pipeline_records._phase1_simple_records(state, '.', lambda _label: None, None)
        assert (state.converted, state.errors) == (0, 0)
    with pytest.raises(loader.ExtensionError, match='"fake_bad" weapon_keywords raised'):
        generic_substitutions(mace, sword)


def test_the_context_reads_a_copy_of_the_converters_table(monkeypatch):
    """What an add-on returns comes back to the caller; the table the context was made from is not written."""
    _install(monkeypatch, 'fake_one', create_records=lambda writer, context: {'FakeOne': 0x0200AAAA})
    _enable(monkeypatch, 'fake_one')
    owned = {'Owned': 0x01000001}
    context = loader.RecordContext(True, {}, None, owned)
    owned['Later'] = 0x01000002
    assert context.form('Owned') == 0x01000001 and context.form('Later') == 0
    assert loader.create_records(_Writer(), context) == {'FakeOne': 0x0200AAAA}
    assert owned == {'Owned': 0x01000001, 'Later': 0x01000002}
    assert context.form('FakeOne') == 0x0200AAAA


def test_an_add_on_cannot_take_an_editor_id_the_converter_holds(monkeypatch):
    """Another FormID for a converter-owned EditorID ends the run; the same FormID is no change, and passes."""
    owned = {'Owned': 0x01000001, 'OwnedToo': 0x01000002}
    _install(monkeypatch, 'fake_same', create_records=lambda writer, context: {'Owned': 0x01000001})
    _install(monkeypatch, 'fake_thief', create_records=lambda writer, context: {
        'Mine': 0x0200AAAA, 'OwnedToo': 0x0200DEAD, 'Owned': 0})
    _enable(monkeypatch, 'fake_same')
    assert loader.create_records(_Writer(), loader.RecordContext(True, {}, None, owned)) == {'Owned': 0x01000001}
    _enable(monkeypatch, 'fake_thief')
    with pytest.raises(loader.ExtensionError) as stop:
        loader.create_records(_Writer(), loader.RecordContext(True, {}, None, owned))
    assert ('"fake_thief" create_records returned Owned, OwnedToo, '
            'which the converter or an earlier add-on already holds') in str(stop.value)
    assert owned == {'Owned': 0x01000001, 'OwnedToo': 0x01000002}


def test_an_add_on_cannot_take_an_editor_id_an_earlier_add_on_returned(monkeypatch):
    """Even under the same FormID: two add-ons never name one record."""
    _install(monkeypatch, 'fake_one', create_records=lambda writer, context: {'FakeOne': 0x0200AAAA})
    _install(monkeypatch, 'fake_two', create_records=lambda writer, context: {'FakeOne': 0x0200AAAA})
    _enable(monkeypatch, 'fake_one', 'fake_two')
    with pytest.raises(loader.ExtensionError, match='"fake_two" create_records returned FakeOne, which'):
        loader.create_records(_Writer(), loader.RecordContext(True, {}))


# ---------------------------------------------------------------------------
# The context
# ---------------------------------------------------------------------------

def test_the_context_adopts_a_masters_record_or_writes_one_under_a_derived_formid():
    """`adopt_or_write` and `master_formid`: by EditorID in the masters, else this plugin's own."""
    importer = types.SimpleNamespace(master_export=None,
                                     master_index=_Index({(b'GLOB', 'FakeSwitch'): 0x01000C10}))
    writer = _Writer()
    dependent = loader.RecordContext(True, {}, importer)
    assert dependent.master_formid('GLOB', 'FakeSwitch') == 0x01000C10
    assert dependent.master_formid('PERK', 'FakeSwitch') == 0
    assert dependent.adopt_or_write(writer, 'GLOB', 'FakeSwitch', None) == (0x01000C10, False)
    assert writer.records == []
    root = loader.RecordContext(True, {})
    assert root.master_formid('GLOB', 'FakeSwitch') == 0 and root.master_export is None
    fid, written = root.adopt_or_write(writer, 'GLOB', 'FakeSwitch', lambda new: struct.pack('<I', new))
    assert written and fid == writer.derive_formid('GLOB', 'FakeSwitch')
    assert writer.records == [('GLOB', struct.pack('<I', fid))]


def test_the_context_tells_the_source_kind_it_was_given():
    """True and False stay what they were, as booleans."""
    assert loader.RecordContext(True, {}).source_is_tes4 is True
    assert loader.RecordContext(False, {}).source_is_tes4 is False
    assert loader.RecordContext(0, {}).source_is_tes4 is False


def test_the_context_reads_the_source_games_settings_masters_first():
    """Only the asked names; a master's value over the default, the plugin's own over both."""
    defaults = {'fFakeBase': 0.5, 'fFakeMult': 2.0}
    master = {'00000001': {'Signature': 'GMST', 'EditorID': 'fFakeBase', 'DATA.Value': '0.75'},
              '00000002': {'Signature': 'GMST', 'EditorID': 'fFakeMult', 'DATA.Value': '3.0'}}
    own = {'GMST': [{'Signature': 'GMST', 'EditorID': 'fFakeMult', 'DATA.Value': '4.0'},
                    {'Signature': 'GMST', 'EditorID': 'fOther', 'DATA.Value': '9'}]}
    importer = types.SimpleNamespace(master_export=master, master_index=None)
    assert loader.RecordContext(True, {}).game_settings(defaults) == (defaults, False)
    assert loader.RecordContext(True, {}, importer).game_settings(defaults) == (
        {'fFakeBase': 0.75, 'fFakeMult': 3.0}, False)
    assert loader.RecordContext(True, own, importer).game_settings(defaults) == (
        {'fFakeBase': 0.75, 'fFakeMult': 4.0}, True)
    assert defaults == {'fFakeBase': 0.5, 'fFakeMult': 2.0}


# ---------------------------------------------------------------------------
# The call sites: where a hook's value lands, and nothing when it is neutral
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('kind, convert_actor, rec', [('NPC_', convert_NPC_, NPC), ('CREA', convert_CREA, CREA)])
def test_an_actors_hook_value_follows_its_spell_list_and_is_the_only_difference(
        monkeypatch, kind, convert_actor, rec):
    """Off: no trace. On: the bytes sit right after SPLO, before the items, and nothing else moved."""
    seen = []
    _install(monkeypatch, 'fake_one', actor_subrecords=lambda k, r: seen.append((k, r)) or MARK)
    off = convert_actor(rec)
    assert MARK not in off and seen == []
    _enable(monkeypatch, 'fake_one')
    on = convert_actor(rec)
    assert seen == [(kind, rec)]
    assert on[24:].replace(MARK, b'') == off[24:] and on[24:].count(MARK) == 1
    sigs = [sig for sig, _data in subrecords(on)]
    assert sigs[sigs.index('XXXX') - 1] == 'SPLO' and sigs[sigs.index('XXXX') + 1] == 'COCT'
    monkeypatch.delenv(loader.EXTENSIONS_ENV_VAR)
    assert convert_actor(rec) == off


@pytest.mark.parametrize('name', sorted(WEAPONS))
def test_a_weapons_hook_keywords_follow_the_vendor_keyword_and_are_the_only_difference(monkeypatch, name):
    """Off: KSIZ 1 and the vendor keyword. On: the hook's keywords after it; every other subrecord equal."""
    tes4_type, model, anim = WEAPONS[name]
    rec, seen = weap(tes4_type, model), []
    _install(monkeypatch, 'fake_one',
             weapon_keywords=lambda r, a: seen.append((r, a)) or [MARK_KEYWORD])
    off = subrecords(convert_WEAP(rec))
    vendor = VENDOR_KYWD['Staff' if tes4_type == 4 else 'Weapon']
    assert dict(off)['KSIZ'] == struct.pack('<I', 1) and dict(off)['KWDA'] == struct.pack('<I', vendor)
    assert seen == []
    _enable(monkeypatch, 'fake_one')
    on = subrecords(convert_WEAP(rec))
    assert seen == [(rec, anim)]
    assert dict(on)['KSIZ'] == struct.pack('<I', 2)
    assert dict(on)['KWDA'] == struct.pack('<II', vendor, MARK_KEYWORD)
    assert [sig for sig, _d in on] == [sig for sig, _d in off]
    assert [pair for pair in on if pair[0] not in ('KSIZ', 'KWDA')] == \
        [pair for pair in off if pair[0] not in ('KSIZ', 'KWDA')]
    monkeypatch.delenv(loader.EXTENSIONS_ENV_VAR)
    assert subrecords(convert_WEAP(rec)) == off


# ---------------------------------------------------------------------------
# The attribute effect hook
# ---------------------------------------------------------------------------

def test_a_package_defining_the_attribute_hook_alone_is_an_add_on_and_a_build_names_it(monkeypatch, add_on_folder):
    """The fourth hook makes a package an add-on by itself, and its name goes into the build's stamp."""
    (add_on_folder / 'fake_effect').mkdir()
    (add_on_folder / 'fake_effect' / '__init__.py').write_text(
        'def attribute_effect(code, attribute, rec):\n    return (77, 99)\n', encoding='utf-8')
    loader.set_extensions({'extensions': ['fake_effect']})
    assert [module.__name__ for module in loader.enabled()] == ['extensions.fake_effect']
    assert loader.attribute_effect('FOAT', 4, {}) == MARK_EFFECT
    assert loader.build_stamp() == {'extensions': ['fake_effect']}
    assert loader.create_records(_Writer(), loader.RecordContext(True, {})) == {}
    assert loader.actor_subrecords('NPC_', NPC) == b'' and loader.weapon_keywords({}, 1) == []


@pytest.mark.parametrize('held, beyond', FIELD_BOUNDS, ids=[str(beyond) for _held, beyond in FIELD_BOUNDS])
def test_an_attribute_pair_must_fit_the_two_fields_it_is_packed_into(monkeypatch, held, beyond):
    """A pair at a field's bound is written as it is; one beyond it ends the run naming the add-on and the value."""
    answers = []
    _install(monkeypatch, 'fake_wide', attribute_effect=lambda code, attribute, rec: answers[-1])
    _enable(monkeypatch, 'fake_wide')
    answers.append(held)
    assert loader.attribute_effect('FOAT', 4, {}) == held
    assert _variants() == [('TES4FOATSpeed', held)]
    answers.append(beyond)
    for ask in (lambda: loader.attribute_effect('FOAT', 4, {}), _variants):
        with pytest.raises(loader.ExtensionError) as stop:
            ask()
        assert str(stop.value) == (
            f'ERROR: add-ons: "fake_wide" attribute_effect returned {beyond!r}, not None or a tuple (archetype, '
            f'actor value) of two ints that fit their fields (0 to 4294967295, -2147483648 to 2147483647)')


def test_the_two_ranges_are_what_the_two_fields_pack():
    """The loader's ranges are what the converter's pack of the pair takes: each bound fits, one beyond does not."""
    for held, beyond in FIELD_BOUNDS:
        struct.pack('<Ii', *held)
        assert held[0] in loader.ARCHETYPE_RANGE and held[1] in loader.ACTOR_VALUE_RANGE
        assert not (beyond[0] in loader.ARCHETYPE_RANGE and beyond[1] in loader.ACTOR_VALUE_RANGE)
        with pytest.raises(struct.error):
            struct.pack('<Ii', *beyond)


def test_the_attribute_hook_is_asked_for_a_morrowind_attribute_effect_too(monkeypatch):
    """A record with a Morrowind effect index of an attribute effect reaches the hook under its own EditorID;
    one of a skill effect, like an Oblivion skill effect, does not."""
    seen = []
    _install(monkeypatch, 'fake_one', attribute_effect=lambda code, attribute, rec: seen.append(
        (code, attribute, rec.get('MorrowindEffectIndex'))))
    _enable(monkeypatch, 'fake_one')
    fortify = dict(mgef('FOAT'), EditorID='MW079FortifyAttribute', MorrowindEffectIndex='79')
    skill = dict(mgef('FOSK'), EditorID='MW083FortifySkill', MorrowindEffectIndex='83')
    writer = VariantWriter()
    magic_variants.reset()
    magic_variants.build_av_variants(
        [fortify, skill, mgef('DRAT'), mgef('FOSK')],
        [effect_entry('MW079FortifyAttribute', 4), effect_entry('MW083FortifySkill', 14), effect_entry('DRAT', 0),
         effect_entry('FOSK', 14)], writer)
    magic_variants.reset()
    assert seen == [('DRAT', 0, None), ('MW079FortifyAttribute', 4, '79')]
    assert len(writer.records) == 4


def test_the_attribute_hook_gives_the_one_answer_and_none_is_no_answer(monkeypatch):
    """An add-on answering None is passed over; add-ons that agree give their one answer."""
    _install(monkeypatch, 'fake_silent', attribute_effect=lambda code, attribute, rec: None)
    _install(monkeypatch, 'fake_one', attribute_effect=lambda code, attribute, rec: (34, 30 + attribute))
    _install(monkeypatch, 'fake_same', attribute_effect=lambda code, attribute, rec: (34, 30 + attribute))
    _install(monkeypatch, 'fake_other', actor_subrecords=_quiet)
    for names, answer in ((('fake_silent',), None), (('fake_other',), None), (('fake_one',), (34, 31)),
                          (('fake_silent', 'fake_one'), (34, 31)), (('fake_one', 'fake_silent'), (34, 31)),
                          (('fake_one', 'fake_other', 'fake_same'), (34, 31))):
        _enable(monkeypatch, *names)
        assert loader.attribute_effect('FOAT', 1, {}) == answer, names


@pytest.mark.parametrize('names', [('fake_one', 'fake_two'), ('fake_two', 'fake_one'),
                                   ('fake_one', 'fake_silent', 'fake_two')])
def test_two_add_ons_giving_one_effect_different_forms_end_the_run(monkeypatch, names):
    """The run-ending error names both add-ons, the effect and the two forms; neither answer is used."""
    _install(monkeypatch, 'fake_one', attribute_effect=lambda code, attribute, rec: (34, 30))
    _install(monkeypatch, 'fake_two', attribute_effect=lambda code, attribute, rec: (34, 26) if attribute == 4 else None)
    _install(monkeypatch, 'fake_silent', attribute_effect=lambda code, attribute, rec: None)
    _enable(monkeypatch, *names)
    assert loader.attribute_effect('FOAT', 3, {}) == (34, 30)
    with pytest.raises(loader.ExtensionError) as stop:
        loader.attribute_effect('FOAT', 4, {})
    assert str(stop.value) == ('ERROR: add-ons: the add-ons fake_one, fake_two give the effect FOAT of attribute 4 '
                               'different forms: [(34, 26), (34, 30)]')
    with pytest.raises(loader.ExtensionError, match='different forms'):
        _variants()


def test_an_attribute_effects_hook_value_is_its_archetype_and_actor_value_and_the_only_difference(monkeypatch):
    """Off: no call, a Script effect on no actor value. On: the hook is asked for the attribute effects alone,
    with the code, the attribute and the exported record, and its pair lands in the record's two fields."""
    seen = []
    _install(monkeypatch, 'fake_one', attribute_effect=lambda code, attribute, rec: seen.append(
        (code, attribute, rec)) or (MARK_EFFECT if attribute == 4 else None))
    entries = (('FOAT', 4), ('DRAT', 0)) + OTHER_ENTRIES
    off = _variants(entries)
    assert seen == [] and off == [('TES4DRATStrength', (1, -1)), ('TES4FOATSpeed', (1, -1)), ('TES4FOSKBlade', (34, 6))]
    _enable(monkeypatch, 'fake_one')
    assert _variants(entries) == [('TES4DRATStrength', (1, -1)), ('TES4FOATSpeed', MARK_EFFECT),
                                  ('TES4FOSKBlade', (34, 6))]
    assert seen == [('DRAT', 0, mgef('DRAT')), ('FOAT', 4, mgef('FOAT'))]
    monkeypatch.delenv(loader.EXTENSIONS_ENV_VAR)
    assert _variants(entries) == off
