"""A dependent adopts its master's ForceCombat factions and destroyed-refs list.

See: docs/commentary/tes5_import_dialogue.md#synthesized-menus-factions-and-formlists
"""

from tes5_import.base.owned_records import (create_destroyed_formlist,
                                            create_force_combat_factions)


class _Writer:
    """The writer surface the creators touch."""

    chargen_fid_base = 0x03000800

    def __init__(self):
        """Record every signature added."""
        self.added = []

    def add_record(self, sig: str, _data: bytes) -> None:
        """Keep the signature."""
        self.added.append(sig)


class _Index:
    """A master index answering `find_by_edid` from a table."""

    def __init__(self, table: dict):
        """`table` is {(sig, edid): FormID}."""
        self.table = table

    def find_by_edid(self, sig: bytes, edid: str) -> int:
        """The master's FormID, or 0."""
        return self.table.get((sig, edid), 0)


_MASTER = _Index({(b'FACT', 'TES4ForceCombatAttackers'): 0x01000842,
                  (b'FACT', 'TES4ForceCombatVictims'): 0x01000843,
                  (b'FLST', 'TES4DestroyedRefs'): 0x01000844})


def test_master_records_are_adopted_not_recreated():
    """With a master supplying all three, nothing is written."""
    writer = _Writer()
    got = create_force_combat_factions(writer, _MASTER)
    got.update(create_destroyed_formlist(writer, _MASTER))
    assert writer.added == []
    assert got == {'TES4ForceCombatAttackers': 0x01000842,
                   'TES4ForceCombatVictims': 0x01000843,
                   'TES4DestroyedRefs': 0x01000844}


def test_a_half_pair_is_not_adopted():
    """One faction alone cannot be the enemy of the other, so both are made."""
    writer = _Writer()
    half = _Index({(b'FACT', 'TES4ForceCombatAttackers'): 0x01000842})
    got = create_force_combat_factions(writer, half)
    assert writer.added == ['FACT', 'FACT']
    assert got['TES4ForceCombatAttackers'] == 0x03000842


def test_root_plugin_creates_its_own():
    """No master index: the records are created at their fixed ids."""
    writer = _Writer()
    assert create_destroyed_formlist(writer) == {'TES4DestroyedRefs': 0x03000844}
    assert writer.added == ['FLST']
