"""Tests for the Nehrim no-start developer patch (tools/release/make_nehrim_no_start_patch.py)."""
import os
import struct
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.release.make_nehrim_no_start_patch import (
    PLAYER_SCRIPT, START_DONE, START_PROPERTY, _wstring, clear_start_flags,
    preset_start_quest)


def _script_entry(name: str, props: dict) -> bytes:
    """One objectFormat-2 script entry whose properties are all Int, Edited."""
    out = _wstring(name) + struct.pack('<BH', 0, len(props))
    for prop, value in props.items():
        out += _wstring(prop) + struct.pack('<BBi', 3, 1, value)
    return out


def test_start_flags_are_cleared_and_the_rest_of_dnam_kept():
    """Start Game Enabled (1) and Starts Enabled (0x10) go; priority and type stay."""
    dnam = struct.pack('<HBBII', 0x0019, 80, 0, 0, 8)
    assert clear_start_flags('DNAM', dnam) == struct.pack('<HBBII', 0x0008, 80, 0, 0, 8)
    assert clear_start_flags('EDID', b'MQ00\0') == b'MQ00\0'


def test_start_quest_property_is_added_to_the_player_script_only():
    """The count grows by one, the new Int is -1, and other scripts are byte-identical."""
    other = _script_entry('OtherScript', {'A': 7})
    ours = _script_entry(PLAYER_SCRIPT, {'EPMultiplikator': 1})
    patched = preset_start_quest('VMAD', other + ours)
    assert patched.startswith(other)
    expected = (_wstring(PLAYER_SCRIPT) + struct.pack('<BH', 0, 2)
                + _wstring(START_PROPERTY) + struct.pack('<BBi', 3, 1, START_DONE)
                + _wstring('EPMultiplikator') + struct.pack('<BBi', 3, 1, 1))
    assert patched == other + expected


def test_preset_refuses_a_changed_layout():
    """A missing script or an existing StartQuest means the record drifted: refuse."""
    with pytest.raises(SystemExit):
        preset_start_quest('VMAD', _script_entry('OtherScript', {}))
    with pytest.raises(SystemExit):
        preset_start_quest('VMAD', _script_entry(PLAYER_SCRIPT, {START_PROPERTY: 0}))
