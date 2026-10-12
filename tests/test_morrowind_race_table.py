"""
The sidecar's RACE.txt rows the character sheet reads: a race's starting
attributes under every Skyrim race a player of it wears.

See: docs/commentary/morrowind_runtime.md#race-attributes
"""

from tes5_import.dialogue.morrowind_sidecar_source import race_lines, skyrim_races

#: Morrowind.esm's Dark Elf starting attributes, male then female.
DARK_ELF = {'bonus': {}, 'male': [40, 40, 30, 40, 50, 40, 30, 40],
            'female': [40, 40, 30, 40, 50, 30, 40, 40]}


def test_a_race_maps_to_its_skyrim_race_and_vampire_race():
    """Dark Elf is Skyrim's DarkElfRace (0x13742) and DarkElfRaceVampire (0x8883D)."""
    assert skyrim_races('Dark Elf') == (0x00013742, 0x0008883D)


def test_a_race_no_player_wears_has_no_row():
    """A custom race the converter maps to no playable Skyrim race stages nothing."""
    assert skyrim_races('T_Mw_Custom') == ()
    assert race_lines({'t_mw_custom': DARK_ELF}) == {}


def test_each_skyrim_race_carries_the_starting_attributes():
    """One row per Skyrim race, both sexes in TES3 order."""
    rows = race_lines({'dark elf': DARK_ELF})
    assert rows[0x00013742] == ('00013742=dark elf|40,40,30,40,50,40,30,40|'
                                '40,40,30,40,50,30,40,40')
    assert rows[0x0008883D].startswith('0008883D=dark elf|')
