"""A converted quest whose EditorID a vanilla Skyrim quest owns is renamed; others keep theirs."""

import pytest

from tes5_import.base import vanilla_quests
from tes5_import.base.tes5_reader import first_sub, zstr
from tes5_import.dialogue.quest import convert_QUST


@pytest.fixture(autouse=True)
def _vanilla(monkeypatch):
    """Pin the vanilla quest names so the test never depends on the local install."""
    monkeypatch.setattr(vanilla_quests, 'vanilla_quest_edids', lambda: frozenset({'ms11'}))


def _edid(edid: str) -> str:
    """The EDID convert_QUST writes for a one-stage quest named `edid`."""
    rec = {'Signature': 'QUST', 'FormID': '00017839', 'EditorID': edid,
           'DATA.Flags': '0', 'DATA.Priority': '10', 'StageCount': '1',
           'Stage[0].Index': '10'}
    return zstr(first_sub(convert_QUST(rec)[24:], b'EDID'))


def test_clashing_quest_editorid_is_prefixed():
    """Oblivion MS11 must not share Blood on the Ice's name."""
    assert _edid('MS11') == 'TES4MS11'


def test_unclashing_quest_editorid_is_kept():
    """Every other quest keeps its authored name, so nothing else moves."""
    assert _edid('MS48') == 'MS48'


def test_voice_prefix_follows_the_rename():
    """The engine builds the voice path from the written EditorID."""
    assert vanilla_quests.engine_quest_edid('ms11') == 'TES4ms11'
    assert vanilla_quests.engine_quest_edid('') == ''
