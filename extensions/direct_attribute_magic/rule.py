"""The Skyrim archetype and actor value an Oblivion attribute effect acts through.

Oblivion's five attribute effect families name the attribute on each item's
effect entry; the converter writes one magic effect per (family, attribute) an
item uses. Without this add-on that record is a script-less Script effect with
no actor value, which only the runtime plugin acts on. With it the record
carries the archetype of its family and the actor value standing in for its
attribute, so the engine itself applies the magnitude.

The two tables are this add-on's own copy; a test holds them equal to the
converter's.

See: extensions/direct_attribute_magic/README.md
"""

#: TES4 effect code -> Skyrim archetype (0 Value Modifier, 4 Absorb, 34 Peak Value Modifier).
FAMILY_ARCHETYPE = {'FOAT': 34, 'DRAT': 34, 'DGAT': 0, 'REAT': 0, 'ABAT': 4}

#: TES4 attribute -> actor value: CarryWeight, Magicka, MagickaRate, Stamina, SpeedMult, Health, Speech, CritChance.
ATTRIBUTE_VALUE = {0: 32, 1: 25, 2: 28, 3: 26, 4: 30, 5: 24, 6: 17, 7: 33}

#: The key an exported effect record of a Morrowind plugin carries; those stay the runtime's.
MORROWIND_KEY = 'MorrowindEffectIndex'


def attribute_effect(code: str, attribute: int, rec: dict):
    """(archetype, actor value) for an Oblivion attribute effect's variant; None leaves the converter's form.

    `code` is the exported effect record's EditorID, an Oblivion record's
    four-letter effect code; `attribute` the attribute index of the item's
    effect entry and `rec` the exported effect record. The loader asks for
    a Morrowind plugin's attribute effects too: those get None.
    See: extensions/direct_attribute_magic/README.md
    """
    if rec.get(MORROWIND_KEY) not in (None, ''):
        return None
    if code not in FAMILY_ARCHETYPE or attribute not in ATTRIBUTE_VALUE:
        return None
    return FAMILY_ARCHETYPE[code], ATTRIBUTE_VALUE[attribute]
