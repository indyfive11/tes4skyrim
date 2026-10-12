"""Which TES4 general statistics converted scripts keep in a global of ours:
one Skyrim keeps no stat for, or one Oblivion's engine kept, which a script
writing it has made its own; and the commands whose engine code counted one.
And the values a game's own statistics page
showed from a quest script's variable, which the converted writes mirror into
a global the Statistics tab reads.

See: docs/commentary/morrowind_runtime.md#statistics-tab
"""

from script_convert.constants import (PAGE_VARIABLES, TES4_MISC_STAT_NAMES,
                                      TES4_SCRIPT_OWNED_MISC_STATS)

#: A command whose TES4 engine code also added 1 to a stat: Oblivion.exe's CloseCurrentOblivionGate, Gates Shut.
COMMAND_STATS = {'closecurrentobliviongate': 13}


def misc_stat_global(index: int) -> str:
    """The global holding stat `index` for the converted scripts."""
    return f'TES4MiscStat{index:02d}'


def is_script_kept(index: int) -> bool:
    """Whether stat `index` is held in a global of ours."""
    if not 0 <= index < len(TES4_MISC_STAT_NAMES):
        return False
    return index not in TES4_SCRIPT_OWNED_MISC_STATS or not TES4_MISC_STAT_NAMES[index]


def page_variable_global(quest: str, variable: str) -> str:
    """The global mirroring one PAGE_VARIABLES entry."""
    return f'TES4PageStat_{quest}_{variable}'


def page_stat_global(source_target: str) -> str:
    """The mirror global of an assignment's TES4 target `Quest.Variable`, or ''."""
    for quest, variable, _label in PAGE_VARIABLES:
        if source_target.strip().lower() == f'{quest}.{variable}'.lower():
            return page_variable_global(quest, variable)
    return ''
