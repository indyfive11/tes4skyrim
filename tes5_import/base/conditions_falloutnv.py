"""FO3/FNV CTDA fields that TES4's 24-byte layout does not carry.

A Fallout CTDA is 28 bytes: TES4's 20 shared bytes, then an explicit Run On
u32 and a Reference u32 in place of TES4's unused tail.  Its function indices
follow Fallout's own table, so they are rewritten to Skyrim's before any
index-keyed lookup.

See: docs/commentary/tes5_import_conditions.md#fallout-ctda
"""

import struct

from ..generated.ctda_fnv_remap import FNV_FUNC_ABSENT, FNV_FUNC_REMAP
from ..record_types.world_falloutnv import is_fallout_source

#: A CTDA at least this long carries Fallout's Run On / Reference tail.
FALLOUT_CTDA_SIZE = 28

#: GetDisposition: absent in Skyrim, but evaluated at a fixed tier by convert_ctda.
_GET_DISPOSITION = 76

#: FO3/FNV actor value -> TES5; any other drops. See: docs/commentary/tes5_import_conditions.md#fallout-actor-values
FALLOUT_AV_TO_TES5 = {
    0: 0, 1: 1, 2: 2, 3: 3, 4: 4,   # Aggression, Confidence, Energy, Responsibility, Mood
    13: 32,   # Carry Weight
    14: 33,   # Critical Chance
    15: 27,   # Heal Rate
    16: 24,   # Health
    17: 34,   # Melee Damage
    18: 39,   # Damage Resistance
    19: 40,   # Poison Resistance
    21: 30,   # Speed Multiplier
    32: 17,   # Barter           -> Speech
    33: 8,    # Big Guns         -> Archery (guns convert to crossbows)
    34: 8,    # Energy Weapons   -> Archery
    36: 14,   # Lockpick         -> Lockpicking
    37: 22,   # Medicine         -> Restoration
    38: 6,    # Melee Weapons    -> OneHanded (and TwoHanded, split_skill_conditions)
    39: 10,   # Repair           -> Smithing
    41: 8,    # Guns             -> Archery
    42: 15,   # Sneak
    43: 17,   # Speech
    45: 6,    # Unarmed          -> OneHanded
    46: 31,   # Inventory Weight
    47: 53,   # Paralysis
    48: 54,   # Invisibility
    49: 54,   # Chameleon        -> Invisibility
    50: 55,   # Night Eye
    52: 41,   # Fire Resistance
    53: 57,   # Water Breathing
    56: 35,   # Unarmed Damage
    57: 5,    # Assistance
    58: 42,   # Electric Resistance -> ResistShock
    59: 43,   # Frost Resistance
    62: 68, 63: 69, 64: 70, 65: 71, 66: 72,   # Variable01-05
    67: 73, 68: 74, 69: 75, 70: 76, 71: 77,   # Variable06-10
}

#: Fallout Run On values: Subject, Target, Reference, Combat Target, Linked Ref.
_RUN_ON_TARGET = 1
_RUN_ON_REFERENCE = 2


def fallout_ctda(raw: bytes) -> bytes:
    """`raw`, zero-padded to 28 bytes when the source is FO3/FNV.

    See: docs/commentary/tes5_import_conditions.md#fallout-short-ctda
    """
    if len(raw) < FALLOUT_CTDA_SIZE and is_fallout_source():
        return raw.ljust(FALLOUT_CTDA_SIZE, b'\0')
    return raw


def fallout_function(func_idx: int) -> 'int | None':
    """The Skyrim index for a Fallout condition function, or None if it has none."""
    if func_idx in FNV_FUNC_ABSENT and func_idx != _GET_DISPOSITION:
        return None
    return FNV_FUNC_REMAP.get(func_idx, func_idx)


def fallout_run_on(raw: bytes, remap) -> tuple:
    """(is_target, run_on, reference) from the Fallout tail.

    Run On = Target is reported as `is_target` so the caller applies the
    same Say-topic retargeting it gives TES4's run-on-target flag; every
    other Run On passes through with its reference load-order remapped.
    """
    run_on, reference = struct.unpack_from('<II', raw, 20)
    if run_on == _RUN_ON_TARGET:
        return True, 0, 0
    if run_on == _RUN_ON_REFERENCE:
        reference = remap(reference)
    return False, run_on, reference
