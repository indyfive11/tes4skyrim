# Add-on `direct_attribute_magic`: Oblivion's attribute magic acts directly on a Skyrim value

**Code:** `extensions/direct_attribute_magic/rule.py`, `audit.py` ·
**Tests:** `tests/extensions/test_direct_attribute_magic.py` ·
**Enable:** `"extensions": ["direct_attribute_magic"]` in `conversion_config.json`
([how add-ons work](../README.md))

Oblivion's five attribute effects (Fortify, Drain, Damage, Restore and Absorb
Attribute) convert to script-less Script effects with no actor value. Only the
runtime plugin acts on such an effect: it moves the attribute on the
character sheet, and the attribute then does what the runtime's own table
gives it
([TES4 attribute magic](../../docs/commentary/morrowind_runtime.md#tes4-attribute-magic),
[what the attributes do](../../docs/commentary/morrowind_runtime.md#attribute-buffs)).
With this add-on each of those effects also carries a Skyrim archetype and
actor value, so the engine itself applies its magnitude to a Skyrim value: a
Fortify Speed spell makes its target faster. Without it the records are what
the converter always wrote.

## The rule

Oblivion names the attribute on each item's effect entry, Skyrim on the magic
effect, so the converter writes one effect record per (family, attribute) an
item uses (`TES4FOATSpeed`), and clones of it per casting type and delivery.
The loader's `attribute_effect` hook is asked once for each such record the
runtime takes, with the exported effect record's EditorID (in an Oblivion
plugin the TES4 effect code), the attribute index and the exported effect
record, and answers with the pair the record is written with. The loader asks
for a Morrowind plugin's attribute effects in the same way; this add-on
answers only for the five TES4 codes and [leaves those alone](#limits).
The archetype goes by the family and the actor value by the attribute:

| TES4 code | Family | Archetype |
|---|---|---|
| `FOAT` | Fortify Attribute | 34 Peak Value Modifier |
| `DRAT` | Drain Attribute | 34 Peak Value Modifier |
| `DGAT` | Damage Attribute | 0 Value Modifier |
| `REAT` | Restore Attribute | 0 Value Modifier |
| `ABAT` | Absorb Attribute | 4 Absorb |

| TES4 attribute | Actor value |
|---|---|
| 0 Strength | 32 CarryWeight |
| 1 Intelligence | 25 Magicka |
| 2 Willpower | 28 MagickaRate |
| 3 Agility | 26 Stamina |
| 4 Speed | 30 SpeedMult |
| 5 Endurance | 24 Health |
| 6 Personality | 17 Speech |
| 7 Luck | 33 CritChance |

Both tables are the converter's own (`magic.EFFECT_ARCHETYPES` and
`magic.ATTRIBUTE_TO_AV` in `tes5_import/record_types/magic.py`), which it uses
for an attribute effect the runtime does not take. `rule.py` carries a copy,
and a test holds the copy equal to them.

## The records

The add-on writes no record of its own and moves no FormID. In each attribute
effect record it changes two fields of `DATA`, and a third when the source
effect is flagged No Magnitude:

| DATA offset | Field | Without the add-on | With it |
|---|---|---|---|
| 64 | Archetype | 1 Script | the family's |
| 68 | Actor Value | -1 none | the attribute's |
| 0 | Flags, only for a No Magnitude source effect of the Fortify, Drain, Damage or Restore family | No Magnitude, and Power Affects Duration unless the effect is No Duration too | neither; Power Affects Magnitude |

The third follows from the first two: the converter never leaves No Magnitude
on a value modifier, because the engine would apply a magnitude of 1
([why](../../docs/commentary/tes5_import_magic.md#no-magnitude-forces-one)),
and the Power Affects bit goes by what is left. Absorb is no value modifier
and keeps its flags. No attribute effect of `Oblivion.esm` is flagged No
Magnitude, so there the two fields are all that changes.

A delivery clone and an Ability clone copy their variant's `DATA`, so they
carry the same pair. The record keeps its EditorID, its FormID, its name and
every other subrecord, and the runtime's effect list
(`teleports_formid.txt`) names the same records in the same rows: the runtime
finds an effect by its FormID and reads neither field. No item changes: a
spell, enchantment, potion, ingredient or scroll points at the same effect
records with the same magnitude, area and duration.

Measured on `Oblivion.esm`, converted at function level with the add-on on and
off: 165 effect records differ, each at offsets 64 and 68 alone (40 variants
and 125 clones); the other 420 effect records, the 4,142 converted items and
the 165 rows of the effect list are byte-identical. A built `Oblivion.esm`
holds 1,351 item entries pointing at those 165 records.

## Limits

- **The runtime still acts on the effect.** The record stays in the runtime's
  list, so an effect acts twice on every actor the runtime keeps attributes
  for. On the player: once on the Skyrim value named above and once through
  the sheet's attribute and what the runtime gives that attribute. On a
  converted actor that holds its attributes as stat faction ranks
  ([NPC attributes](../../docs/commentary/morrowind_runtime.md#npc-attributes)):
  once on the Skyrim value and once on that rank while the effect lasts. An
  actor in no stat faction, such as one of Skyrim's own, gets the Skyrim
  value alone. The two are not reconciled.
- **The magnitude is applied as it stands.** Fortify Willpower 50 adds 50 to
  MagickaRate and Fortify Speed 50 adds 50 to SpeedMult; neither follows an
  Oblivion formula. Personality goes to Speech and Luck to CritChance, which
  stand in for what those attributes did.
- **Unverified in game:** how the runtime reads the magnitude of a detrimental
  effect (Drain, Damage, Absorb) that the engine now applies too; what a
  running save does with an effect that is active when its record changes;
  whether a change of SpeedMult by a magic effect shows at once.
- **A Morrowind effect is left alone.** The hook is asked for a Morrowind
  plugin's attribute effects too. A record that carries a Morrowind effect
  index stays the runtime's, whatever its EditorID.
- **Skill effects and every other effect are untouched.** The hook is asked
  only for the attribute effects the runtime takes.
- **A master and the plugins that depend on it are built with the same add-on
  list.** A dependent adopts its master's variant of the same EditorID and
  ships the master's record, while a clone it makes itself copies the `DATA`
  it built; under two lists a variant and its clone would disagree. The
  import of a dependent stops when its master was built with another list
  ([why and how](../README.md#one-list-for-a-master-and-the-plugins-that-depend-on-it)).

## The audit

`python -m extensions.direct_attribute_magic.audit <built plugin>...
[--other-master <file name>]...` (from the repo root; plugins in load order)
reads the BUILT plugins with its own reader and its own copy of the two
tables; it imports nothing of the converter or of the add-on. For every effect
entry of a `SPEL`, `ENCH`, `ALCH`, `INGR` or `SCRL` it looks up the effect
record the entry points at, in the plugin or in a master given before it, and
holds that record's archetype and actor value against its table pair. It
reads those two fields and nothing else of a record: one whose flags are
damaged passes.

It identifies an attribute effect record twice, and the two must agree:

- by its EditorID, which gives the family and the attribute;
- by its FormID in the list the build staged for the runtime beside the
  plugin, `SKSE/Plugins/MorrowindRuntime/<plugin stem>/teleports_formid.txt`,
  whose rows `plugin|FormID=index:attribute` give the same two.

The list says which records there must be, so a record that was renamed, or
that is missing, does not drop out of the count without a word. A Morrowind
attribute effect the list names is counted and not checked. Every family and
attribute in which a plugin's list names records must have an item entry
pointing at one of them.

It prints, per family and attribute, the records and the entries it found and
how many carry their table pair, then every form it met and how many records
the two identifications name alike. It exits:

- **0** when every record and entry is on its table pair and the EditorIDs
  and the lists name the same records;
- **1** when a record or an entry is not on its pair, or a record is named by
  one identification alone or differently by the two;
- **2** when it cannot tell: an entry points into a plugin that was neither
  given nor named with `--other-master`, no attribute entry was found at all,
  a plugin holding attribute effect records has no list beside it, or no item
  entry points into a family and attribute whose records a list names.

`--other-master` takes the file NAME of a master the conversion did not
build, such as `Skyrim.esm`, not a path. Run on a build made without the
add-on it reports every record and entry as not on its table value. A build
of a converter older than the runtime's attribute magic has no list and
exits 2.
