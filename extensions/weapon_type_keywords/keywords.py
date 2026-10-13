"""Skyrim's weapon-type keyword for a converted weapon, by its animation type.

Skyrim's own perks find a weapon's kind with HasKeyword: Armsman00 (Skyrim.esm
000BABE4) applies to WeapTypeSword, WeapTypeWarAxe, WeapTypeMace and
WeapTypeDagger. A weapon without its type keyword is outside every such perk.

Animation type 0 (hand to hand) has no type keyword in Skyrim.esm. Type 6 is
both battleaxes and warhammers (295 and 278 of Skyrim.esm's); the weapon's
mesh file name tells them apart: a name holding "axe" is a battleaxe, any
other a warhammer. Type 9, a crossbow, gets the bow's.

See: extensions/weapon_type_keywords/README.md
"""

from tes5_import.base.text_reader import get_str

#: Skyrim.esm KYWD FormIDs of the weapon-type keywords, read out of the master.
WEAP_TYPE_SWORD = 0x0001E711
WEAP_TYPE_WAR_AXE = 0x0001E712
WEAP_TYPE_DAGGER = 0x0001E713
WEAP_TYPE_MACE = 0x0001E714
WEAP_TYPE_BOW = 0x0001E715
WEAP_TYPE_STAFF = 0x0001E716
WEAP_TYPE_WARHAMMER = 0x0006D930
WEAP_TYPE_GREATSWORD = 0x0006D931
WEAP_TYPE_BATTLEAXE = 0x0006D932

#: WEAP DNAM animation type -> the keyword Skyrim.esm's weapons of that type carry.
TYPE_KEYWORDS = {
    1: WEAP_TYPE_SWORD,
    2: WEAP_TYPE_DAGGER,
    3: WEAP_TYPE_WAR_AXE,
    4: WEAP_TYPE_MACE,
    5: WEAP_TYPE_GREATSWORD,
    6: WEAP_TYPE_WARHAMMER,
    7: WEAP_TYPE_BOW,
    8: WEAP_TYPE_STAFF,
    9: WEAP_TYPE_BOW,
}

#: The animation type two keywords share, and the one its axes carry.
TWO_HANDED_BLUNT, TWO_HANDED_AXE = 6, WEAP_TYPE_BATTLEAXE


def _is_axe(rec: dict) -> bool:
    """Whether the weapon's mesh file name holds "axe"."""
    path = get_str(rec, 'Model.MODL').lower().replace('\\', '/')
    return 'axe' in path.rsplit('/', 1)[-1]


def weapon_keywords(rec: dict, anim_type: int) -> list:
    """[the type keyword] for Skyrim animation type `anim_type`; [] for none.

    See: extensions/weapon_type_keywords/README.md
    """
    if anim_type == TWO_HANDED_BLUNT and _is_axe(rec):
        return [TWO_HANDED_AXE]
    keyword = TYPE_KEYWORDS.get(anim_type)
    return [keyword] if keyword else []
