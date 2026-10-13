# Add-on `oblivion_weapon_rule`: Oblivion's weapon-hit rule, only where a converted actor is in the hit

**Code:** `extensions/oblivion_weapon_rule/rule.py`, `audit.py` ·
**Tests:** `tests/extensions/test_oblivion_weapon_rule.py` ·
**Enable:** `"extensions": ["oblivion_weapon_rule"]` in `conversion_config.json`
([how add-ons work](../README.md))

A converted weapon keeps Oblivion's damage number and a converted actor
Oblivion's health, but Skyrim turns a weapon's number into a much bigger hit
than Oblivion does. With this add-on a weapon hit follows Oblivion's rule where
a converted actor deals or takes it. Skyrim's own records and game settings
stay untouched, and the player is given nothing. Without it the converter
writes none of the records below.

## The rule

Oblivion's hit, at full weapon condition and fatigue (UESP, "Oblivion:The
Complete Damage Formula"; `rule.py` holds its own copy of the formula and, in
`OBLIVION_HIT_SETTINGS`, the Oblivion.exe default of each setting a plugin does
not author):

    hit = base x fDamageWeaponMult x (fDamageWeaponConditionBase + fDamageWeaponConditionMult)
               x fFatigueBase
               x (fDamageSkillBase + fDamageSkillMult x skill/100)
               x (fDamageStrengthBase + fDamageStrengthMult x attribute/100)

with skill and attribute capped at 100, the attribute being Strength for a
melee weapon and Agility for a bow. Skyrim's own skill factor is taken as
`Min + (Max - Min) x skill/100`. The four settings are named in ONE place,
`SKYRIM_SKILL_SETTINGS` in `rule.py`: Skyrim.esm authors only
`fDamagePCSkillMax` = 1.5; `fDamagePCSkillMin`, `fDamageSkillMin` and
`fDamageSkillMax` are taken as 1.0, 1.0 and 1.5 and are **unverified** engine
defaults.

## The records

**One hidden perk**, `TES4OblivionWeaponRulePerk`, is listed with `PRKZ` +
`PRKR` (rank 1) on every converted TES4 NPC_ and creature, in the vanilla
position `SPCT SPLO PRKZ PRKR COCT` (the loader's `actor_subrecords` hook). The
root plugin writes it and a dependent adopts it by EditorID
(`create_records`). Its FormID is `derive_formid('PERK', <EditorID>)` and the
switch global's `derive_formid('GLOB', <EditorID>)`, so neither moves between
builds. Its entries all use function Multiply Value and come in two families:

| Family | Entry point | Owner | Whose hits | Vanilla shape |
|---|---|---|---|---|
| Dealt | 35 Mod Attack Damage | the converted attacker | on any target | `MS02IncreaseDamage` (Skyrim.esm `0010C708`), `CWSoldierExtraDamageToPlayer` (`0010B1D9`) |
| Taken | 36 Mod Incoming Damage | the converted target | from an attacker that is NOT converted | `MS02ReduceDamage` (`001069BB`), `DLC2ArrowDefense` (Dragonborn.esm `0203CA73`), `crDragonResistNPCs` (`001046BD`) |

"Converted" is membership of the conversion's hidden stat faction
`TES4AttributeStrength`, which every converted TES4 NPC_ and creature joins.
The Taken family's attacker tab carries `GetInFaction == 0` on it, so the
player and Skyrim's actors are covered and a converted actor hitting a
converted actor is scaled once, by Dealt. `MS02ReduceDamage` authors
`GetInFaction` on that same tab.

A hit gets two independent multipliers, so each family holds two kinds of
entries:

- **Skill bands**, one set per weapon skill. The weapon tab tests
  `IsWeaponSkillType` 6 OneHanded / 7 TwoHanded / 8 Marksman (vanilla
  `Backstab`, `00058210`); the attacker's skill is read with
  `GetBaseActorValue` bands (vanilla `DLC2MageArmor30NPC`, Dragonborn.esm
  `02017745`), on the owner tab for Dealt and on the attacker tab for Taken.
  The value is Oblivion's weapon, condition, fatigue and skill factors over
  Skyrim's skill factor at that skill. The player gets a set of its own only
  when its two Skyrim settings differ from the NPCs'.
- **Attribute bands**, Strength for skills 6 and 7 and Agility for 8. A
  converted attacker's attribute is its rank in `TES4AttributeStrength` /
  `TES4AttributeAgility` (`GetFactionRank`); the player's is the conversion's
  global `TES4PlayerStrength` / `TES4PlayerAgility`, which reads 100 without
  the runtime that keeps it current; a Skyrim actor's is taken as 50, and gets
  an entry only when the factor at 50 is not exactly 1.0, which it is under
  both the exe defaults and Oblivion.esm's own values.

Every FormID test and every byte of an entry (`PRKE`, `DATA`, `PRKC` + `CTDA`
per tab, `EPFT`, `EPFD`, `PRKF`) follows the cited vanilla perks; the tests
hold their bytes as fixtures and rebuild five of them byte for byte with the
add-on's own packers.

The four records the add-on names but does not own (the two stat factions and
the two player globals) are the converter's; it finds them by EditorID through
the context it is handed. A source without them gets the switch global and no
perk.

## The bands are derived, not typed in

`derive_bands` grows a band from its low end, one integer at a time, while
`(hi - lo) / (hi + lo)` of the exact factor at its two ends stays within
`BAND_ERROR_BUDGET` (5 %). The band's value is the harmonic mean of the two
end factors, which is equally far, in ratio, from both. Bands tile 0-100 with
no gap or overlap; the first has no floor, and one open band above 100 holds
the factor at 100, which is exact for an attribute (Oblivion caps it) and
Oblivion's cap over Skyrim's factor at 100 for a skill. Two factors each
within 5 % keep their product within 10.25 %.

Every number comes from the source game's settings at build time (the
plugin's and its masters' GMSTs, else the exe defaults). A dependent that
authors one of them rewrites the perk as an override of the same FormID.

Measured on `Oblivion.esm`, which authors seven of the eight settings
(`fDamageWeaponMult` 0.5, both condition settings 0.5, `fDamageSkillMult` 1.5,
`fDamageStrengthBase` 0.75, `fDamageStrengthMult` 0.5, `fFatigueBase` 1.0):
17 skill bands from 0.1034 (skill 0-1) to 0.5603 (96-100), 7 attribute bands
from 0.7857 (0-15) to 1.25 (100), 130 entries, a 30,214-byte record, and a
worst error over the whole sweep of 10.12 %. The record's length and hash are
pinned in the tests, so a change to its bytes cannot pass unseen.

## Two switches

- In game: every entry also requires the hidden global
  `TES4OblivionWeaponRule` == 1 (a short the add-on writes, default 1) on its
  owner tab. Set to 0, no entry applies.
- At build: leave `oblivion_weapon_rule` out of `"extensions"`. The perk, the
  global and the actors' perk lists are then not written at all.

## Limits of this version

- Weapon condition and fatigue below full are not followed: both factors are
  the full ones.
- Luck's part in the effective skill is left out.
- Oblivion's power-attack and sneak multipliers are not applied; Skyrim's own
  are.
- Hand-to-hand and a creature's natural attack have no weapon, so no
  weapon-skill test passes and the hit is untouched. Spells are untouched.
- The rule goes by the ACTORS in the hit, not by where the weapon came from: a
  Skyrim weapon in a converted actor's hand, or swung at one, is scaled too.
- The skill read is the actor's Skyrim skill for the weapon (OneHanded,
  TwoHanded, Marksman), which stands in for Oblivion's Blade, Blunt or
  Marksman. It is not always that number: the converter gives a converted NPC
  the larger of Blade and Blunt (and Hand-to-Hand, for OneHanded) as both
  melee skills. A fixed-level converted creature has auto-calculated stats, so
  the engine derives its skills from its class and level; a level-scaled one
  has manual stats and its skills are its `DNAM`'s.
- `IsWeaponSkillType` reads the WEAP record's skill field, so an entry applies
  to a converted weapon only when that field names the right skill.
- **A master and the plugins that depend on it are built with the same add-on
  list.** An override keeps its master's bytes, so an actor overridden by a
  dependent lists the perk exactly when the master's record does. The import
  of a dependent stops when its master was built with another list
  ([why and how](../README.md#one-list-for-a-master-and-the-plugins-that-depend-on-it)).

### Known limits a review found (the bytes are unchanged on purpose)

None of these was fixed, because each fix changes the record's bytes. Each is
a candidate for a later version.

- **The 10 % bound holds for whole values 0-100 only.** The bands are derived
  and checked at integers. A fractional skill or attribute (a fortify effect,
  the player's global between two integers) can sit past the last integer a
  band was checked at and still inside that band, a little over the bound;
  above 100 a SKILL is
  held at the factor of 100 over Skyrim's factor at 100, while Skyrim's own
  factor keeps growing, so the error there is not bounded by 10 %.
- **Four redundant entries.** The closing band of each attribute table
  (attribute 100) and the open band above it (101 and up) hold the same value,
  once per attribute in Dealt and once per attribute in Taken. Merging them
  would shorten the record and change its bytes.
- **`IsWeaponSkillType` is authored in vanilla only on entry point 18**
  (`Backstab`); on 35 and 36 vanilla tests the weapon by keyword
  (`HasKeyword`, as `DLC2ArrowDefense` does on 36). Its use on the weapon tabs
  of 35 and 36 is built as designed and unverified in game.
- **A script that writes an attribute on a Skyrim actor** joins that actor to
  the stat faction without giving it the perk list. Such an actor then counts
  as converted for the Taken family's `GetInFaction == 0` test (its hits on
  converted actors are no longer scaled) while dealing unscaled hits itself.

**Unverified engine links.** No official perk reads the attacker's skill on
entry point 36's attacker tab, and none uses `GetFactionRank` in a perk
condition; both are built as designed. What IS authored on a non-owner tab:
`GetLevel` (`CWSoldierExtraDamageToPlayer`), `HasKeyword`
(`crDragonResistNPCs`, `DLC2ArrowDefense`) and `GetInFaction`
(`MS02IncreaseDamage`, `MS02ReduceDamage`). A look in game decides.

## The audit

`python -m extensions.oblivion_weapon_rule.audit <built plugin> <export dir>
[--master <built master>]... [--master-export <dir>]...` (from the repo root)
reads the BUILT plugin, not the add-on's functions. It recomputes the band
tables from the export's GMSTs with its own copy of the formula and compares
every entry value; evaluates the perk's conditions for each weapon skill x
skill x attribute x attacker (a converted actor, the player, a Skyrim actor),
requiring exactly one skill entry and at most one attribute entry per hit, and
prints the worst error against the exact formula; checks that no entry applies
with the switch off and that a converted pair is scaled once; and requires
that every actor in the stat faction lists the perk at rank 1, in its place,
and no other actor does. It exits 1 on any mismatch and 2 when it cannot find
the perk or a form it names, so a dependent that adopts them needs `--master`.
