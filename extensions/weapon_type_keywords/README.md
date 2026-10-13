# Add-on `weapon_type_keywords`: a converted weapon carries Skyrim's keyword for its type

**Code:** `extensions/weapon_type_keywords/keywords.py` ·
**Tests:** `tests/extensions/test_weapon_type_keywords.py` ·
**Enable:** `"extensions": ["weapon_type_keywords"]` in `conversion_config.json`
([how add-ons work](../README.md))

The converter gives a WEAP one keyword, the vendor's (`VendorItemWeapon` or
`VendorItemStaff`). Skyrim's own weapons also carry a keyword naming their
type, and Skyrim's perks find a weapon's type with `HasKeyword`: `Armsman00`
(Skyrim.esm `000BABE4`) applies to `WeapTypeSword`, `WeapTypeWarAxe`,
`WeapTypeMace` and `WeapTypeDagger`. A converted weapon without its type
keyword is outside every such perk. With this add-on each converted weapon
carries the type keyword too; without it the record is what the converter
always wrote.

## The rule

The loader's `weapon_keywords` hook is handed the Skyrim animation type the
converter has just given the weapon (WEAP `DNAM` byte 0) and returns one
keyword, which the converter writes after the vendor keyword in `KWDA`, with
`KSIZ` counting both:

| Animation type | Keyword | Skyrim.esm FormID |
|---|---|---|
| 1 one-handed sword | `WeapTypeSword` | `0001E711` |
| 2 dagger | `WeapTypeDagger` | `0001E713` |
| 3 war axe | `WeapTypeWarAxe` | `0001E712` |
| 4 mace | `WeapTypeMace` | `0001E714` |
| 5 greatsword | `WeapTypeGreatsword` | `0006D931` |
| 6, mesh file name holds "axe" | `WeapTypeBattleaxe` | `0006D932` |
| 6, any other mesh | `WeapTypeWarhammer` | `0006D930` |
| 7 bow | `WeapTypeBow` | `0001E715` |
| 8 staff | `WeapTypeStaff` | `0001E716` |
| 9 crossbow | `WeapTypeBow` | `0001E715` |
| 0 hand to hand | none | |

The FormIDs were read out of Skyrim.esm's `KYWD` records. They are the
keywords its own weapons of each type carry: of its 2,484 `WEAP`, 366 of type
1 hold `WeapTypeSword`, 290 of type 2 `WeapTypeDagger`, 300 of type 3
`WeapTypeWarAxe`, 285 of type 4 `WeapTypeMace`, 289 of type 5
`WeapTypeGreatsword`, 253 of type 7 `WeapTypeBow`, 71 of type 8
`WeapTypeStaff`; none of the 18 of type 0 holds a type keyword.

## Limits

- **Type 6 is both battleaxes and warhammers** (295 and 278 of Skyrim.esm's),
  and Oblivion has one two-handed blunt type for both. The add-on reads the
  weapon's mesh FILE name, the last part of the path, lower-cased: a name
  holding "axe" gets `WeapTypeBattleaxe`, any other `WeapTypeWarhammer`. This
  is the add-on's own test, not the converter's: the converter tells a war
  axe from a mace by other words, looked for in the whole mesh path. In
  Oblivion.esm the test splits the 184 two-handed blunt weapons into 79
  battleaxes (`battleaxe.nif` 78, `battleaxefine.nif` 1) and 105 warhammers
  (`warhammer.nif` 97, `sylswarhammer.nif` 7, `warhammerfine.nif` 1); each of
  those five names holds "axe" or "hammer", so none is a warhammer only for
  want of "axe". A two-handed axe whose mesh file name does not hold "axe" is
  filed as a warhammer.
- **A crossbow gets `WeapTypeBow`**, so perks written for bows apply to it.
- **No material keyword** (`WeapMaterialIron` and the like) is written:
  nothing in the source record names a Skyrim material.
- The keyword follows the animation type the converter chose. Where that is
  wrong for a weapon (a mesh name the converter reads as an axe, say), the
  keyword is wrong with it.
- **A master and the plugins that depend on it are built with the same add-on
  list.** An override is the master's built record with the author's changes
  put in, so under two lists it would carry `KSIZ` from one build and `KWDA`
  from the other. The import of a dependent stops when its master was built
  with another list
  ([why and how](../README.md#one-list-for-a-master-and-the-plugins-that-depend-on-it)).
  Under one list an overridden weapon carries the keyword of what the
  override makes it.
