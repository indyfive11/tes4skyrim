# Wiring audit: script-to-record links in converted plugins

**Tool:** `python -m tools.validate.wiring_audit -f <plugin> [--check …] [--script …] [--tsv F] [--md F]`
· **Measured:** 2026-10-02 over the current `output/` builds of Oblivion.esm,
Nehrim.esm and Morrowind_ob.esm.

The audit reads the converted plugin, its converted masters and their compiled
scripts once (Oblivion.esm 29 s, Morrowind_ob.esm with its master 104 s). It
reports the links that fail silently in game. Every class below was checked
against real data before it was kept.

## <a id="checks"></a>Checks

| Check | Fires when | In game |
|---|---|---|
| `not-compiled` | a VMAD names one of our scripts with no `.pex` | the script never binds |
| `missing-fragment` | a QUST/INFO fragment entry names a function the script lacks | the engine binds a missing function |
| `unbound` | an object property the script reads has no value **on that attachment** | reads None; the calling function aborts |
| `dangling` | a property points at a FormID no file holds | reads None |
| `type` | the bound record's type cannot satisfy the declared type (`ACCEPTS` in `vmad_property_typecheck.py`) | "cannot be bound", reads None |
| `script-type` | a property typed as a converted script is bound to a record that does not carry that script (own, base, or alias) | the cast reads None |
| `host` | a script whose native base cannot run on its record (`extends Actor` on an ACTI) | the script never binds |
| `condition-var` | a `GetVMQuestVariable`/`GetVMScriptVariable` reads a variable no script on the target (or its base) declares `Conditional` | the condition never passes |
| `lost-write` | a TES4 variable Oblivion sets (locally or `set X.v to`), the converted script reads, and no converted script sets | the value stays at its default |
| `coverage` | a TES4 record's script, or a result script with real statements, has no converted counterpart | the behaviour is gone |
| `dropped-topic` | the INFO is missing because the importer skipped its whole topic | by design (NPC-to-NPC chatter); listed, not a defect |

**Not flagged, by measurement:**
- `PlayerRef` (00000014): the engine creates it, so no file holds it.
- A TES4 script variable left unbound: Oblivion's own scripts never set it, so it was null there too. `lost-write` covers the case where Oblivion did set it.
- `TES4Movers_*`: the importer writes a mover list only for cells that have movers, and `TES4Polyfill.ResetInterior` treats None as "no movers".
- `::TES4NoSuchVariable_var` (`UNRESOLVED_VAR_SENTINEL`): the importer's deliberate stand-in for a TES4 condition that already read a missing variable.

## <a id="disproven-rules"></a>Two placement rules the vanilla census disproves

Both were candidate checks, and both come from notes that the tool now
contradicts with Skyrim.esm and the pristine `Data/Scripts.zip`.

| Claim | Vanilla Skyrim.esm |
|---|---|
| `GetVMScriptVariable` needs the script on the **placed reference's own** VMAD ([package §7](../commentary/tes5_import_package.md#7-getvmscriptvariable-package-gates-need)) | 27 of 62 targets carry the script **only on their base**; for package conditions alone, 8 of 28 (`MG01FaraldaBridgeForcegreet` → `MG05WinterholdTriggerRef::BridgeWarning`) |
| Reference events (`OnLoad`, `OnHit`, `OnDeath`…) never fire on a script bound to a base `NPC_` ([placed references](../commentary/script_convert.md#scripts-placed-references)) | 856 base-`NPC_` attachments of scripts with reference events; 553 of them on no placed reference at all (`defaultGhostScript` on summoned dragon priests: `OnDying`, `OnHit`, `OnLoad`) |

The relocation those notes motivated (`_relocate_actor_scripts_to_refs`) is
harmless and was left alone. Neither rule is a check.

## <a id="results"></a>Results — 2026-10-02

| Check | Oblivion.esm | Nehrim.esm | Morrowind_ob.esm |
|---|---:|---:|---:|
| `unbound` | 12 | 45 | 58 |
| `type` | 0 | 0 | 12 |
| `host` | 0 | 3 | 1 |
| `condition-var` | 0 | 0 | 6 |
| `dangling` | 1 | 2 | 0 |
| `script-type` | 2 | 0 | 0 |
| `lost-write` | 0 | 2 | 0 |
| `coverage` | 6 | 0 | 0 |
| `dropped-topic` (by design) | 1,030 | 0 | 0 |

### <a id="verified"></a>Findings verified against the data

| Plugin | Finding | Evidence |
|---|---|---|
| Oblivion | `DarkConversations` leaves `Dark04Guard4`, `DarkPirate4Ref`, `NelsTheNaughtyRef`, `TelaendrilRef`, `ValenDrethDark04Ref` unbound | each record exists in the export AND the output (EDID present); the property is empty |
| Oblivion | `PriorMaborelRef` (horse script), `WeebamNaRef` (INFO 00026ABA), `effectEnchantConjuration` (SE13, MS10), `effectEnchantMysticism` (TG11Heist) unbound | same: the record exists in the output |
| Oblivion | `NeeshaRef` unbound; `MQ07`'s `BurdTopic` dangling (01020067) | the ACHR / DIAL exists in the export but not in the output |
| Oblivion | 6 potion/soul-gem scripts (`SE39ObjectScript`, `SE38MuseumItemSCRIPT`…) lost; `SEUnaArminaSCRIPT.se38museumitem7ref` cannot cast | Skyrim ALCH and SLGM cannot carry a VMAD: 0 of 363 vanilla ALCH, 0 of 17 SLGM do |
| Nehrim | `MQ19DachScript`, `MQ23Trigger01Script`, `AranthealStaturSchwertSCN` extend `Actor` on activators | `ScriptName NEHRIM_MQ19DachScript extends Actor`, attached to ACTI `MQ19TrigZoneDach` |
| Morrowind_ob | `mwTrapDaedraSummonScript` declares `Actor Property mwDremoraLord`, bound to an NPC_ and used as `PlaceAtMe(mwDremoraLord)` | the property can never bind; `a5ef621d` types PlaceAtMe arguments as base forms, and this build may predate it |
| Morrowind_ob | `fbmwTRShrineDead` conditions read `timer` and `triggered` | the TES4 script declares only `follownow` and `approach`, so the condition's variable index resolved against the wrong script |
