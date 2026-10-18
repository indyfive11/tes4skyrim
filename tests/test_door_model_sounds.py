"""Door sounds lifted from a model's `sound:` text keys (tes5_import/record_types/items.py).

See: docs/commentary/asset_convert_audio.md#mesh-door-sounds
"""

import pytest

from asset_convert.audio import door_sounds
from tes5_import.record_types import items

DOOR = {'Signature': 'DOOR', 'FormID': '01000800', 'EditorID': 'FRDoor',
        'Model.MODL': 'Dungeons\\FRDoorUpper.NIF'}


@pytest.fixture
def parsed(monkeypatch):
    """Every door NIF parses to an open sound `DRSOpen`; the table is cleared after."""
    monkeypatch.setattr(door_sounds, 'door_sounds_from_nif', lambda path: {'open': 'DRSOpen'})
    yield
    items._DOOR_MODEL_SOUNDS.clear()


def _nif(root):
    """A door model under `root` in the author's mixed case, carrying a sound key."""
    path = root / 'Dungeons' / 'FRDoorUpper.NIF'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'..sound: DRSOpen..')


def test_a_sound_only_a_master_defines_resolves_to_its_master_key(tmp_path, parsed):
    """The SOUN comes from master_export, keyed in this plugin's index space."""
    _nif(tmp_path)
    master = {'00012345': {'Signature': 'SOUN', 'EditorID': 'DRSOpen'}}
    assert items.load_door_model_sounds([tmp_path], {'DOOR': [DOOR]}, master) == 1
    assert items._door_model_sounds(DOOR) == {'open': 0x00012345}


def test_a_model_only_a_master_ships_is_found(tmp_path, parsed):
    """The own tree lacks the model; the master's mixed-case copy supplies it, own SOUN wins."""
    own, master_tree = tmp_path / 'own', tmp_path / 'master'
    own.mkdir()
    _nif(master_tree)
    by_type = {'DOOR': [DOOR], 'SOUN': [{'FormID': '01000900', 'EditorID': 'drsopen'}]}
    master = {'00012345': {'Signature': 'SOUN', 'EditorID': 'DRSOpen'}}
    assert items.load_door_model_sounds([own, master_tree], by_type, master) == 1
    assert items._door_model_sounds(DOOR) == {'open': 0x01000900}


def test_a_master_sound_is_shifted_into_the_output_index_space(tmp_path, parsed, monkeypatch):
    """With Skyrim.esm prepended, a master key 00xxxxxx must land at 01xxxxxx, as a record SNAM does.

    Unshifted, an Oblivion.esm sound keeps index 00 = Skyrim.esm (CG-67: 54 Frostcrag Reborn
    door slots on Skyrim REFRs or nothing, DynDOLOD aborting on the unresolved id).
    """
    from tes5_import.base import text_reader
    monkeypatch.setattr(text_reader, '_formid_index_offset', 1)
    _nif(tmp_path)
    master = {'00012345': {'Signature': 'SOUN', 'EditorID': 'DRSOpen'}}
    assert items.load_door_model_sounds([tmp_path], {'DOOR': [DOOR]}, master) == 1
    assert items._door_model_sounds(DOOR) == {'open': 0x01012345}


def test_a_slot_no_resolver_answers_is_warned_about(capsys):
    """A left-alone slot that names no master SNDR is counted in the log, and kept as it was."""
    import struct

    class Writer:
        _top_groups = {}

    body = b'SNAM' + struct.pack('<H', 4) + struct.pack('<I', 0x0008CEE9)
    blob = b'DOOR' + struct.pack('<I', len(body)) + bytes(16) + body
    Writer._top_groups = {'DOOR': [blob]}
    assert items.patch_sound_descriptor_slots(Writer, 'DOOR', set(), lambda f: 0,
                                              lambda f: False) == 0
    assert Writer._top_groups['DOOR'] == [blob]
    assert '1 DOOR sound slot(s) name no SNDR (e.g. 0008CEE9)' in capsys.readouterr().out
