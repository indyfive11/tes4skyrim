# PHP ScriptConverter (Skyblivion) — Comprehensive Analysis

## Overview

The PHP ScriptConverter is an AST-based TES4 (Oblivion) → TES5 (Skyrim) script converter by "Ormin", used in the Skyblivion project. Unlike our line-by-line regex approach (`script_convert/converter.py`), it implements a full compiler pipeline: **Lexer → Parser → AST → Transform → Emit**.

**Location**: `references/ScriptConverter/`

---

## 1. Architecture

### Pipeline Stages

```
Source Text (.txt)
    │
    ▼
┌──────────────────┐
│  OBScriptLexer   │  Tokenize TES4 script into tokens
│  ArithLexer      │  (separate lexer for arithmetic expressions)
│  FragmentLexer   │  (for TIF/QF fragments)
└──────────────────┘
    │
    ▼
┌─────────────────────────────┐
│  TES4OBScriptGrammar        │  Parse tokens into TES4 AST nodes
│  TES4ObscriptCodeGrammar    │  (recursive-descent parser)
│  ArithGrammar               │  (expression sub-parser)
└─────────────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│  TES4 AST                    │
│  - TES4Script                │
│  - TES4CodeBlock             │  Block (GameMode, OnActivate, etc.)
│  - TES4CodeChunks            │  Statement list
│  - TES4VariableAssignation   │  set x to y
│  - TES4Expression            │  Binary expressions
│  - TES4Function              │  Function calls
│  - TES4Return                │  Return statements
│  - Branch (if/elseif/else)   │
│  - Primitive values           │
│  - TES4Reference             │  Named references
└──────────────────────────────┘
    │
    ▼
┌───────────────────────────────┐
│  TES4ToTES5ASTConverter       │  Main conversion orchestrator
│  TES5BlockFactory             │  Block type mapping + initial code
│  TES5ValueFactory             │  Value/function dispatch (107+ factories)
│  TES5AdditionalBlockChangesPass │ Post-conversion block modifications
│  TES5ReferenceFactory         │  Reference resolution + special vars
│  TES5PropertiesFactory        │  Variable → Property conversion
└───────────────────────────────┘
    │
    ▼
┌──────────────────────────────┐
│  TES5 AST                    │
│  - TES5Script                │  Full Papyrus script
│  - TES5EventCodeBlock        │  Events
│  - TES5FunctionCodeBlock     │  Helper functions
│  - TES5ObjectCall            │  Method calls
│  - TES5Branch                │  If/ElseIf/Else
│  - TES5Property              │  Script properties
│  - Values, References, etc.  │
│  implements TES5Outputtable  │  → string output
└──────────────────────────────┘
    │
    ▼
  .psc file (Papyrus source)
```

### Key Design Decisions

1. **Full AST**: Every TES4 construct has an AST node. Transformations are tree-to-tree, not text-to-text.
2. **Function Factory Pattern**: Each TES4 function that needs special handling has a dedicated `FunctionFactory` class implementing `convertFunction()`.
3. **Type Inference**: `TES5TypeInferencer` infers return types from method calls so property types propagate.
4. **ESM-Aware**: `ESMAnalyzer` reads the actual Oblivion.esm binary to resolve EditorIDs to record types (ACTI, NPC_, WEAP, etc.) for type-safe property declarations.
5. **Multi-Script Scope**: `TES5MultipleScriptsScope` allows cross-script reference resolution during conversion.
6. **Metadata Logging**: `MetadataLogService` writes deferred record creation commands (ADD_MESSAGE, ADD_SPEAK_AS_ACTOR) to a file for post-processing by external tools.

---

## 2. Block Type Mapping

The PHP converter maps TES4 block types in `TES5BlockFactory::mapBlockType()`:

| TES4 Block | TES5 Event | Notes |
|---|---|---|
| `GameMode` | `OnUpdate` | + RegisterForSingleUpdate pattern |
| `MenuMode` | *(skipped entirely)* | Block dropped, `menuMode()` function returns 0 |
| `OnActivate` | `OnActivate` | + BlockActivation() in OnInit |
| `OnInit` | `OnInit` | Direct |
| `OnSell` | `OnSell` | Direct |
| `OnDeath` | `OnDeath` | Direct |
| `OnLoad` | `OnLoad` | Direct |
| `OnActorEquip` | `OnObjectEquipped` | + Equipment check branch wrapping |
| `OnTriggerActor` | `OnTriggerEnter` | + Actor cast + null check |
| `OnAdd` | `OnContainerChanged` | Direct |
| `OnEquip` | `OnEquipped` | Direct |
| `OnUnequip` | `OnUnequipped` | Direct |
| `OnDrop` | `OnContainerChanged` | Direct |
| `OnTriggerMob` | `OnTriggerEnter` | Direct |
| `OnTrigger` | `OnTrigger` | Direct |
| `OnHitWith` | `OnHit` | Direct |
| `OnHit` | `OnHit` | Direct |
| `OnAlarm` | `OnUpdate` | (mapped to update loop) |
| `OnStartCombat` | `OnCombatStateChanged` | Direct |
| `OnPackageStart` | `OnPackageStart` | Direct |
| `OnPackageDone` | `OnPackageEnd` | Direct |
| `OnPackageEnd` | `OnPackageEnd` | Direct |
| `OnPackageChange` | `OnPackageChange` | Direct |
| `OnMagicEffectHit` | `OnMagicEffectApply` | Direct |
| `OnReset` | `OnReset` | Direct |
| `ScriptEffectStart` | `OnEffectStart` | Direct |
| `ScriptEffectUpdate` | `OnUpdate` | + RegisterForSingleUpdate pattern |
| `ScriptEffectFinish` | `OnEffectFinish` | Direct |

### GameMode → OnUpdate Pattern (Critical)

The PHP converter does NOT just rename GameMode to OnUpdate. It implements a full polling pattern:

1. **OnInit block is created** with `RegisterForSingleUpdate(1)` to start the update loop
2. **OnUpdate block** re-registers at the end: `RegisterForSingleUpdate(1)` (1-second tick)
3. **For Quest scripts**: Adds an early-return bailout if `IsRunning() == false` (prevents updates when quest is stopped)
4. **For ObjectReference scripts**: Wraps entire body in `if GetParentCell() == Game.GetPlayer().GetParentCell()` (only runs when player is nearby — performance optimization)

### OnActivate → BlockActivation Pattern

When `OnActivate` is found, the converter:
1. Creates an `OnInit` block with `BlockActivation()` call
2. This prevents Papyrus's default activation behavior (which would ignore the script's OnActivate)

### OnActorEquip → Equipment Check Wrapping

For `OnActorEquip` with a parameter (the specific item), the converter wraps the entire event body in:
```papyrus
if akBaseObject == <specific_item_ref>
    ; ... original code
endif
```

### OnTriggerActor → Actor Cast + Null Check

Wraps body in:
```papyrus
Actor akAsActor = akActivateRef as Actor
if akAsActor != None
    ; ... original code (with parameter filtering if present)
endif
```

### Duplicate Block Merging

When multiple TES4 blocks map to the same event (e.g., two `OnHit` blocks), the converter:
1. Creates separate private functions (`OnHit_1`, `OnHit_2`)
2. Creates a proxy `OnHit` event that calls both functions

---

## 3. Complete Function Registry

The DI container (`TES5ValueFactoryFunctionFiller`) registers **107+ function factories**. Here is the complete list organized by category:

### 3.1 Functions Passed Through (DefaultFunctionFactory)
These keep the same name and arguments:

`AddItem`, `AddSpell`, `DuplicateAllItems`, `Disable`, `DropMe`, `Enable`, `EquipItem`, `EvaluatePackage`, `GetCombatTarget`, `GetCurrentAIProcedure`, `GetDestroyed`, `GetFactionRank`, `GetHeadingAngle`, `GetLevel`, `GetOpenState`, `GetRestrained`, `GetStartingAngle`, `GetStartingPos`, `IsAnimPlaying`, `IsEssential`, `IsGuard`, `IsInCombat`, `IsInInterior`, `IsSneaking`, `IsSwimming`, `Kill`, `MoveTo`, `PlaceAtMe`, `PushActorAway`, `RemoveAllItems`, `RemoveItem`, `RemoveSpell`, `SetFactionRank`, `SetForceRun`(?), `SetGhost`, `SetScale`, `SetUnconscious`, `StartCombat`, `UnequipItem`, `Yield`

### 3.2 Simple Renames (RenamedFunctionFactory)

| TES4 Function | TES5 Function |
|---|---|
| `DeleteFullActorCopy` | `Delete` |
| `Dispel` | `DispelSpell` |
| `Drop` | `DropObject` |
| `GetAttacked` | `IsInCombat` |
| `GetDead` | `IsDead` |
| `GetDisabled` | `IsDisabled` |
| `GetEquipped` | `IsEquipped` |
| `GetGold` | `GetGoldAmount` |
| `GetInFaction` | `IsInFaction` |
| `GetIsAlerted` | `IsAlerted` |
| `GetLocked` | `IsLocked` |
| `GetLOS` | `HasLOS` |
| `GetParentRef` | `GetEnableParent` |
| `GetSleeping` | `GetSleepState` |
| `IsActorUsingATorch` | `IsTorchOut` |
| `IsRidingHorse` | `IsOnMount` |
| `IsTalking` | `IsInDialogueWithPlayer` |
| `IsWeaponOut` | `IsWeaponDrawn` |
| `Look` | `SetLookAt` |
| `MoveToMarker` | `MoveTo` |
| `Reset3DState` | `Reset` |
| `SetActorsAI` | `EnableAI` |
| `SetDestroyed` | `BlockActivation` |
| `StopCombatAlarmOnActor` / `scaonactor` | `StopCombatAlarm` |
| `EVP` | `EvaluatePackage` |
| `IsActorDetected` | `IsInCombat` |

### 3.3 Pop-First-Arg-to-CalledOn (PopCalledRenameFunctionFactory)
These move the first argument to become the object the function is called on:

| TES4 (global call) | TES5 (object call) |
|---|---|
| `AddTopic(topicRef)` | `topicRef.Add()` |
| `CompleteQuest(questRef)` | `questRef.CompleteQuest()` |
| `GetDeadCount(actorBase)` | `actorBase.GetDeadCount()` |
| `GetPCExpelled(factionRef)` | `factionRef.IsPlayerExpelled()` |
| `GetQuestRunning(questRef)` | `questRef.IsRunning()` |
| `GetStageDone(questRef, stage)` | `questRef.GetStageDone(stage)` |
| `GetStage(questRef)` | `questRef.GetStage()` |
| `ModFactionReaction(fact, ...)` | `fact.ModReaction(...)` |
| `ResetInterior(cellRef)` | `cellRef.Reset()` |
| `SetEssential(actorBase, flag)` | `actorBase.SetEssential(flag)` |
| `SetFactionReaction(fact, ...)` | `fact.SetReaction(...)` |
| `SetPCExpelled(factionRef, flag)` | `factionRef.SetPlayerExpelled(flag)` |
| `SetStage(questRef, stage)` | `questRef.SetStage(stage)` |
| `StartQuest(questRef)` | `questRef.Start()` |
| `StopQuest(questRef)` | `questRef.Stop()` |

### 3.4 Filler (No-Op, Safely Dropped)

`AddAchievement`, `CloseCurrentOblivionGate`, `CloseOblivionGate`, `DisableLinkedPathPoints`, `EnableLinkedPathPoints`, `EssentialDeathReload`, `ForceCloseOblivionGate`, `ModPCMiscStat`, `PickIdle`, `PlayMagicEffectVisuals`, `PlayMagicShaderVisuals`, `PurgeCellBuffers`, `RefreshTopicList`, `RemoveScriptPackage`, `ResetFallDamageTimer`, `SendTrespassAlarm`, `SetActorFullName`, `SetActorRefraction`, `SetAllReachable`, `SetAllVisible`, `SetClass`, `SetCombatStyle`, `SetDoorDefaultOpen`, `SetIgnoreFriendlyHits`, `SetInvestmentGold`, `SetItemValue`, `SetNoAvoidance`, `SetNoRumors`, `SetPackDuration`, `SetPlayerInSEWorld`, `SetQuestObject`, `SetRestrained`, `SetRigidBodyMass`, `SetSceneIsComplex`, `SetShowQuestItems`, `ShowBirthSignMenu`, `ShowClassMenu`, `ShowDialogSubtitles`, `ShowEnchantment`, `ShowRaceMenu`, `ShowSpellMaking`, `StopMagicEffectVisuals`, `StopMagicShaderVisuals`, `StopWaiting`, `TrapUpdate`, `TriggerHitShader`, `Wait`, `PME`, `PMS`, `PCB`, `SME`, `SMS`

### 3.5 Return Constant Value

| TES4 Function | Returns | Notes |
|---|---|---|
| `GetCrimeKnown` | `false` | No equivalent |
| `GetForceSneak` | `false` | No equivalent |
| `GetKnockedState` | `true` | Deemed unimportant |
| `GetPlayerControlsDisabled` | `false` | Stub |
| `GetShouldAttack` | `false` | No equivalent |
| `GetTalkedToPC` | `true` | Deemed unimportant |
| `HasVampireFed` | `false` | No equivalent |
| `IsIdlePlaying` | `true` | Deemed unimportant |
| `IsInDangerousWater` | `false` | No equivalent |
| `IsTimePassing` | `true` | Always true |
| `IsXbox` | `false` | Always false |
| `IsPCAMurderer` | Check crime gold > 0 | Custom factory |

### 3.6 Custom Factory Functions (Complex Transformations)

#### `MessageBox` → MESSAGE record creation
- **1 argument**: Converts to `Debug.MessageBox("text")` (simple notification)
- **2+ arguments** (text + buttons): Logs `ADD_MESSAGE` metadata with EDID + all button texts, creates a property reference to a MESSAGE record, calls `TES4MessageBox<hash>.Show()`, stores result in `TES4_MESSAGEBOX_RESULT` variable

#### `GetButtonPressed` → Read message box result
- Returns `TES4_MESSAGEBOX_RESULT` property (an Int set by the MESSAGE record's Show())

#### `Message` → `Debug.Notification()`
- Simple rename to static `Debug.Notification()` call

#### `Say(topic, ...)` → `tTimer.LegacySay(self, topic, speakerRef, true)`
- Redirects to a **helper quest** called `TES4TimerHelper` (property: `tTimer`)
- If the optional 3rd argument (speak-as actor) is NOT a REFR, logs `ADD_SPEAK_AS_ACTOR` metadata and appends "Ref" to the name
- The `LegacySay` function is a custom Papyrus function on the helper quest script

#### `SayTo(target, topic)` → `tTimer.LegacySay(self, topic, None, true)`
- Same pattern as Say, redirected through `TES4TimerHelper`

#### `GetSecondsPassed` → `tTimer.getSecondsPassed(tGSPLocalTimer)`
- Uses the `TES4TimerHelper` quest's `getSecondsPassed()` method
- Passes a local timer float (`tGSPLocalTimer`) for delta time calculation
- The helper quest tracks time between calls

#### `GetActorValue` / `GetBaseActorValue` → Actor value remapping
- TES4 attributes (Strength, Intelligence, etc.) → reads from `TES4Attr<Name>` GlobalVariable properties
- Skill renames: `Fatigue→Stamina`, `Armorer→Smithing`, `Security→Lockpicking`, `Acrobatics→Sneak`, `Mercantile→Speechcraft`, `Mysticism→Illusion`, `Blade→OneHanded`, `Blunt→OneHanded`, `Encumbrance→InventoryWeight`, resist values remapped
- Other values passed through unchanged

#### `SetActorValue` / `ForceActorValue` / `ModActorValue` → Complex routing
- Attributes (Str, Int, etc.) on player → `TES4Attr<Name>.SetValue(value)` (GlobalVariable)
- `Speed` → `ForceMovementSpeed(value)` instead (separate Papyrus function)
- `Aggression` → Quantized: 0→0, 1-49→1, 50-79→2, 80+→3 (Skyrim's 4-level system)
- `Confidence` → Quantized: 0-29→0, 30-69→2, 70+→3
- `ModActorValue` for attributes → `TES4Attr<Name>.SetValue(TES4Attr<Name>.GetValueInt() + delta)`
- Skill renames applied as with GetActorValue

#### `GetInCell` → String comparison via `StringUtil.Substring`
```papyrus
StringUtil.Substring(self.GetParentCell(), 0, len) == "cellName"
```
Uses substring match on the cell name (prefix match of the given length).

#### `GetPos(axis)` → `GetPositionX/Y/Z`
- Pops the axis argument ("x"/"y"/"z") and maps to `GetPositionX()`, `GetPositionY()`, `GetPositionZ()`

#### `SetPos(axis, value)` → `SetPositionX/Y/Z`
- Same pattern as GetPos

#### `GetAngle(axis)` / `SetAngle(axis, value)` → `GetAngleX/Y/Z` / `SetAngleX/Y/Z`
- Same axis-popping pattern

#### `Rotate(axis, speed)` → TBD
- Custom factory (axis-based rotation mapping)

#### `Activate(target, blockActivate)` → `Activate(target, bDefaultProcessingOnly)`
- If no arguments: uses event parameter (ACTIVATOR meaning) or player as target, `true` for skip-activation-block
- If arguments present: inverts the `blockOnActivate` flag (Oblivion `0` = skip → Skyrim `true` = skip)

#### `GetActionRef` → Event parameter lookup
- Returns the `akActivateRef` parameter (ACTIVATOR meaning variable) from the current event scope

#### `GetSelf` → Implicit self reference
- For ObjectReference scripts: returns `self`
- For ActiveMagicEffect scripts: returns `self.GetTargetActor()`
- For Quest scripts: returns `self`
- For TopicInfo fragments: returns `akSpeakerRef`

#### `GetContainer` → Event parameter lookup
- Returns the CONTAINER meaning variable from the current event scope
- Only valid in OnEquip/OnUnequip contexts

#### `RemoveMe` → `self.Delete()`
- Redirects calledOn to `self`, renames to `Delete`

#### `Lock(level, lockAsOwner)` → `Lock(true, lockAsOwner)`
- Forces first arg to `true` (always lock), preserves lock-as-owner flag

#### `Unlock` → `Lock(false, false)`
- Forces `Lock(false, false)` (unlock, not as owner)

#### `ShowMap(marker)` → `marker.AddToMap()`
- Pops first arg to calledOn, renames to `AddToMap`

#### `GoToJail` → `TES4CyrodiilCrimeFaction.SendPlayerToJail()`
- Redirects to a Faction property representing Cyrodiil's crime faction

#### `GetCrimeGold` → `TES4CyrodiilCrimeFaction.GetCrimeGold()`
- Same faction redirect pattern

#### `SetCrimeGold(amount)` → `TES4CyrodiilCrimeFaction.SetCrimeGold(amount)`
- Same pattern

#### `ModCrimeGold(amount)` → `TES4CyrodiilCrimeFaction.ModCrimeGold(amount)`
- Same pattern

#### `PayFine` → `TES4CyrodiilCrimeFaction.PlayerPayCrimeGold(...)`
- Routes through the crime faction

#### `DisablePlayerControls` → `Game.DisablePlayerControls(true,true,false,false,false,true,true,true)`
- Adds all 8 boolean arguments to match Skyrim's expanded signature
- Emulates Oblivion's simple disable

#### `EnablePlayerControls` → `Game.EnablePlayerControls()`
- Redirects calledOn to `Game` static reference, passes args through

#### `EnableFastTravel(flag)` → `Game.EnableFastTravel(flag)`
- Redirects to `Game` static reference

#### `GetDisposition` → `calledOn.GetActorValue("Variable01")`
- Maps disposition to the generic "Variable01" actor value (placeholder)

#### `ModDisposition` → Filler/no-op equivalent
- No direct Skyrim equivalent for modifying disposition

#### `GetDetected(target)` → `target.IsDetectedBy(self)` (inverted arguments)
- Swaps caller and argument: TES4's `self.GetDetected(target)` becomes `target.IsDetectedBy(self)`

#### `GetPCFame/GetPCInfamy` → GlobalVariable reads
- Maps to `TES4AttrFame`/`TES4AttrInfamy` global variable properties

#### `SetPCFame/SetPCInfamy/ModPCFame/ModPCInfamy`
- Write/modify the same global variables

#### `GetPCFactionMurder/Attack/Steal` → Crime gold check on a faction
- Routes through the faction reference

#### `GetPCIsRace/GetPCIsSex` → `Game.GetPlayer().GetRace()/GetActorBase().GetSex()`
- Redirects to player with appropriate chain

#### `GetIsId/GetIsRace/GetIsReference/GetIsSex` → Type-checked comparisons
- Custom expression generation

#### `IsActionRef(ref)` → `akActivateRef == ref`
- Compares event parameter to the given reference

#### `IsOwner` → Custom handling (likely cell ownership check)

#### `Autosave` → `; TODO` or Game function call

#### `Cast(spell, target)` → Complex spell cast routing

#### `CreateFullActorCopy` → Actor placement

#### `ClearOwnership` → Property clearing

#### Various weather functions → `Weather.` static calls

#### `PlayGroup(group, flag)` → Animation call or no-op

#### `PlaySound/PlaySound3D` → Sound descriptor routing

#### `GetRandomPercent` → `Utility.RandomInt(0, 99)`

#### `GetCurrentTime` → `Utility.GetCurrentGameTime()`

#### `GetDayOfWeek` → Date calculation

#### `GetItemCount` → Complex routing (may need form resolution)

#### `GetArmorRating` → Actor value read

#### `GetClothingValue` → Actor value read

#### `GetAmountSoldStolen` → Crime gold check

#### `IsPCSleeping` → `Game.GetPlayer().GetSleepState()`

#### `IsPlayerInJail` → Custom check

#### `IsRaining` → Weather comparison

#### `IsSpellTarget` → HasMagicEffect check

#### `HasMagicEffect` → Default or custom routing

#### `StartConversation` → Custom dialogue trigger

#### `GetIsCurrentPackage/Weather` → Package/weather comparisons

#### `GetInSameCell/GetInWorldspace` → Cell/worldspace comparisons

#### `StopCombat` → Custom (may add arguments)

#### `StopLook` → `ClearLookAt()`

#### `StopQuest(questRef)` → `questRef.Stop()`

#### `ResetHealth` → `RestoreActorValue("Health", 999)`

#### `Resurrect` → Custom (with flag handling)

#### `GetGameSetting(name)` → `Game.GetGameSettingFloat/Int/String(name)`

#### `SetAlert/SetForceRun/SetForceSneak` → Flag-based actor value setting

#### `SetCellPublicFlag` → Cell property modification

#### `SetOpenState` → Custom open/close routing

#### `SetOwnership` → Property setting

#### `SetActorAlpha` → `SetAlpha(value)` (value scaling)

#### `AddScriptPackage` → Package assignment

---

## 4. Virtual Properties & Helper Systems

### 4.1 Special Virtual Properties (`TES5ReferenceFactory::$special_conversions`)

| Property Name | Type | Purpose |
|---|---|---|
| `TES4AttrStrength` | GlobalVariable | Oblivion Strength attribute (no TES5 equivalent) |
| `TES4AttrIntelligence` | GlobalVariable | Oblivion Intelligence attribute |
| `TES4AttrWillpower` | GlobalVariable | Oblivion Willpower attribute |
| `TES4AttrAgility` | GlobalVariable | Oblivion Agility attribute |
| `TES4AttrSpeed` | GlobalVariable | Oblivion Speed attribute |
| `TES4AttrEndurance` | GlobalVariable | Oblivion Endurance attribute |
| `TES4AttrPersonality` | GlobalVariable | Oblivion Personality attribute |
| `TES4AttrLuck` | GlobalVariable | Oblivion Luck attribute |
| `tContainer` | Quest (TES4Container) | Data container for shared state |
| `tTimer` | Quest (TES4TimerHelper) | Timer/utility functions (GetSecondsPassed, LegacySay) |
| `tGSPLocalTimer` | Float | Local timer for GetSecondsPassed delta calculation |
| `TES4CyrodiilCrimeFaction` | Faction | Global crime faction (replaces TES4's single faction crime) |
| `TES4_MESSAGEBOX_RESULT` | Int | Stores result of MessageBox.Show() for GetButtonPressed |

### 4.2 Helper Quests Required

1. **`TES4TimerHelper`** (referenced as `tTimer`): Custom quest script providing:
   - `getSecondsPassed(float localTimer)` — Delta time calculation
   - `LegacySay(ObjectReference speaker, Topic topic, Actor speakAs, bool wait)` — Say() wrapper
   
2. **`TES4Container`** (referenced as `tContainer`): Data container quest for shared cross-script state

### 4.3 GlobalVariable Records Required

Eight GLOB records for TES4 attributes: `TES4AttrStrength`, `TES4AttrIntelligence`, `TES4AttrWillpower`, `TES4AttrAgility`, `TES4AttrSpeed`, `TES4AttrEndurance`, `TES4AttrPersonality`, `TES4AttrLuck`

Plus: `TES4AttrFame`, `TES4AttrInfamy` (for PC fame/infamy system)

---

## 5. Metadata Logging System

`MetadataLogService` appends tab-delimited commands to a `Metadata` file during conversion. These commands describe records that must be created in the CK/xEdit after script conversion:

### Known Metadata Commands

| Command | Arguments | Purpose |
|---|---|---|
| `ADD_MESSAGE` | `EDID, text, button1, button2, ...` | Create a MESSAGE record with buttons for MessageBox |
| `ADD_SPEAK_AS_ACTOR` | `editorID` | Create a REFR alias for a non-REFR actor used in Say() |

The metadata file is consumed by post-processing tools to create the actual ESM records.

---

## 6. Expression-Level Transformations

The `TES5ValueFactory::convertArithmeticExpression()` handles special cases where TES4 comparisons need structural changes:

### `GetWeaponAnimType == N` → Multi-type comparison
TES4 weapon anim types map to multiple TES5 types:
- Type 0 → `GetEquippedWeapon().GetWeaponType() == 0`
- Type 1 (1H) → `... == 1 || ... == 2 || ... == 3 || ... == 4`
- Type 2 (2H) → `... == 5 || ... == 6 || ... == 8`
- Type 3 (Bow) → `... == 7 || ... == 9`

### `GetDetected(target) == N` → Inverted `target.IsDetectedBy(self)`
Arguments are swapped.

### `GetDetectionLevel(target) == 3` → `target.IsDetectedBy(self) == true`
Only comparison with 3 (full detection) maps to a boolean.

### `GetCurrentAIProcedure == N` → Various checks
- 4 → `IsInDialogueWithPlayer()`
- 8 → `GetSleepState() == 3`
- 13 → `IsInCombat()`
- 0, 7, 15, 17 → Always true (wander/patrol, no equivalent)

### `GetSitting == N` → `GetSitState()` with mapped values
TES4 sitting states (0-14) map to TES5 sit states (0-4).

### `IsIdlePlaying/GetKnockedState/GetTalkedToPC == N` → Always true
Deemed unimportant, returns constant.

### ObjectReference == Integer → Null check
If comparing an ObjectReference to 0, converts to `ref == None`.

---

## 7. Comparison with Our Python Converter

### Architecture Comparison

| Aspect | PHP ScriptConverter | Our Python (`script_convert/converter.py`) |
|---|---|---|
| **Approach** | Full AST pipeline (lex→parse→transform→emit) | Line-by-line regex substitution |
| **Type System** | Full type inference from ESM binary | Limited (cross-ref graph for ref types) |
| **Function Handling** | 107+ dedicated Factory classes | Regex patterns in FUNCTION_MAP + inline |
| **Variable Types** | Resolved from ESM (ACTI→Activator, NPC_→Actor, etc.) | Heuristic (ref→ObjectReference, short→Int) |
| **Cross-Script** | MultipleScriptsScope with property resolution | CrossRefGraph for remote vars |
| **Error Handling** | ConversionException per function | Comment TODO / pass-through |
| **Record Creation** | Metadata log → deferred xEdit/CK creation | Not implemented |
| **Branching** | AST branch nodes | Regex line-by-line if/else |
| **Expression Parsing** | Full arithmetic grammar | Regex-based infix conversion |

### Critical Features We're MISSING

#### 1. **MessageBox → MESSAGE Record Creation** ⚠️ HIGH PRIORITY
The PHP converter creates actual TES5 MESSAGE records (with EDID, text, and button labels) via metadata logging. The converted script then calls `messageRef.Show()` which returns a button index. We just convert to `Debug.MessageBox()` which has no buttons.

**Impact**: Any script with multi-button MessageBox (hundreds in Oblivion) will lose interactive choices.

#### 2. **Helper Quest System (TES4TimerHelper, TES4Container)** ⚠️ HIGH PRIORITY
The PHP creates properties that reference helper quest scripts providing:
- `getSecondsPassed()` via delta time tracking
- `LegacySay()` function for Say/SayTo
- Shared data storage

We have no equivalent infrastructure.

#### 3. **GameMode → OnUpdate with Performance Guards** ⚠️ MEDIUM
The PHP adds:
- For Quest scripts: `if !IsRunning(); RegisterForSingleUpdate(1); return; endif`
- For ObjectReference scripts: wraps in `if GetParentCell() == player.GetParentCell()`

We add `RegisterForSingleUpdate` but NOT the performance guards.

#### 4. **OnActivate → BlockActivation()** ⚠️ MEDIUM
The PHP automatically adds `BlockActivation()` in `OnInit` when a script has `OnActivate`. Without this, Skyrim's default activation (open container, open door) runs INSTEAD OF the script's handler.

We do NOT add `BlockActivation()`.

#### 5. **TES4 Attribute GlobalVariables** ⚠️ MEDIUM
The PHP routes ALL attribute reads/writes (Strength, Intelligence, etc.) through global variable properties. This preserves attribute state that scripts depend on. We map them directly to unrelated Skyrim actor values (Strength→UnarmedDamage, Intelligence→Magicka) which changes the semantics.

#### 6. **Crime Faction Routing** ⚠️ MEDIUM
`GoToJail`, `GetCrimeGold`, `SetCrimeGold`, `ModCrimeGold`, `PayFine` all route through a `TES4CyrodiilCrimeFaction` property. We convert some of these but may not have proper faction routing.

#### 7. **Say()/SayTo() → Helper Quest LegacySay** ⚠️ LOW-MEDIUM
The PHP routes Say through a helper quest function that manages voice playback timing. We drop Say or convert to a basic call.

#### 8. **GetSecondsPassed → Delta Time Helper** ⚠️ LOW-MEDIUM  
The PHP uses `tTimer.getSecondsPassed(localTimer)` for proper delta time. We convert to `; TODO` or hardcoded values.

#### 9. **OnActorEquip Parameter Filtering** ⚠️ LOW
The PHP wraps OnObjectEquipped body in `if akBaseObject == specificItem`. We produce the event but may not add the filtering branch.

#### 10. **OnTriggerActor → Actor Cast + Null Check** ⚠️ LOW
The PHP casts the reference to Actor and null-checks. Done 2026-10-07: a bare `OnTriggerActor` block is gated on `akActionRef as Actor` (`blocks.BLOCK_TYPE_TESTS`).

#### 11. **Type-Safe Property Declarations** ⚠️ LOW
The PHP reads the ESM to determine that a `ref` variable pointing to an NPC_ should be typed `Actor Property`, not `ObjectReference Property`. Our heuristic approach may produce wrong types.

#### 12. **DisablePlayerControls Argument Expansion** ⚠️ LOW
TES4 takes no args; TES5 takes 8 booleans. The PHP fills in specific defaults `(true,true,false,false,false,true,true,true)`.

#### 13. **MenuMode Handling** ⚠️ LOW
The PHP drops MenuMode blocks entirely and makes the `MenuMode()` function return 0 (always false). We map MenuMode to OnUpdate which is potentially incorrect.

#### 14. **GetInCell → String Prefix Match** ⚠️ LOW
The PHP uses `StringUtil.Substring()` for cell name prefix matching since GetInCell doesn't exist in Papyrus. We may not implement this correctly.

#### 15. **GetDisposition → Variable01 Placeholder** ⚠️ LOW
The PHP maps disposition to ActorValue "Variable01". This is a placeholder but at least compiles. We may not handle it at all.

#### 16. **Expression-Level Transformations** ⚠️ LOW
The PHP handles `GetWeaponAnimType`, `GetCurrentAIProcedure`, `GetSitting` comparisons at the expression level, generating complex multi-comparison OR chains. Our regex approach cannot do this.

---

## 8. Build System & Fragment Handling

### Script Types

The PHP converter handles three distinct script types:
1. **Standalone scripts** (SCPT → .psc): Full script conversion via `TES4ToTES5ASTConverter`
2. **QF Fragments** (Quest stage fragments): `TES4ToTES5ASTQFFragmentConverter` — extracts quest stage script fragments from INFO/QUST records
3. **TIF Fragments** (Topic Info fragments): `TES4ToTES5ASTTIFFragmentConverter` — extracts dialogue response script fragments

### Build Graph

`BuildInteroperableCompilationGraphs` determines compilation order accounting for cross-script dependencies. Scripts are compiled in dependency order so type information propagates.

### Name Transformation

`TES5NameTransformer` standardizes script names (TES4 scripts have freeform names; Papyrus has restrictions).

---

## 9. ESMAnalyzer — Binary Data Integration

The `ESMAnalyzer` loads Oblivion.esm with a selective schema (EDID + SCRI for 40+ record types) to:

1. **Resolve EditorID → Record Type**: Given an EditorID like "SkinGoblin", determines it's a CREA record, so the Papyrus type should be `Actor`
2. **Determine Script Attachment Type**: Given a script name, finds what record types it's attached to (ACTI, NPC_, WEAP, etc.) to determine the script's `extends` clause
3. **Check SCRI assignments**: Finds which records have SCRI pointing to which scripts
4. **List GLOB records**: Determines which variable names are global variables (GLOB records)

The `TypeMapper` maps record type signatures to Papyrus types:
- `ACHR/ACRE/NPC_/CREA` → `Actor`
- `CLOT/ARMO` → `Armor`
- `ACTI` → `Activator`
- `DOOR` → `Door`
- `WEAP` → `Weapon`
- `QUST` → `Quest`
- `DIAL` → `Topic`
- `CELL` → `Cell`
- `WRLD` → `WorldSpace`
- etc. (60+ mappings)

---

## 10. Summary of Actionable Items for Our Converter

### Must-Have (breaks gameplay if missing)
1. **MessageBox multi-button → MESSAGE record + Show() + result variable**
2. **OnActivate → add BlockActivation() in OnInit**
3. **GameMode performance guards** (quest IsRunning check, ObjectReference cell check)

### Should-Have (improves fidelity)
4. **TES4 Attribute GlobalVariables** instead of mapping to unrelated actor values
5. **Crime faction routing** for GoToJail/CrimeGold functions
6. **GetSecondsPassed delta time** via helper quest or Utility.GetCurrentRealTime()
7. **DisablePlayerControls** → expand to 8-arg form with proper defaults
8. **MenuMode** → drop block instead of mapping to OnUpdate

### Nice-to-Have (edge cases)
9. Say/SayTo helper quest routing
10. OnActorEquip item filtering branch
11. ~~OnTriggerActor actor cast~~ (done 2026-10-07)
12. Expression-level GetWeaponAnimType multi-comparison
13. Type-safe property declarations from ESM data
14. GetInCell string prefix matching
15. Metadata logging for deferred record creation


Perfect! I've read all 50+ files. Here's the comprehensive PHP converter factory mapping for TES4→TES5 Papyrus conversions:

## **Base Frameworks**

### **DefaultFunctionFactory.php**
- **Purpose**: Converts functions with simple 1:1 signature mapping
- **Behavior**: Directly passes TES4 function call to Papyrus with same name and arguments—no transformation

### **FillerFactory.php**
- **Purpose**: Placeholder conversions
- **Behavior**: Returns `TES5Filler()` (empty/no-op conversion)

### **NotSupportedFactory.php**
- **Purpose**: Functions that cannot be converted
- **Behavior**: Throws `ConversionException` with message "Function [name] not supported"

---

## **Individual Function Conversions**

### **1. ModDispositionFactory.php**
- **TES4 Function**: `ModDisposition(actor, value)`
- **Handles**: NPC faction reaction modifier
- **Special Handling**: Only supports value -100 → converts to `StartCombat()` on target
- **Papyrus Output**: `calledOn.StartCombat()` OR returns `TES5Filler()` for other values

### **2. GetDispositionFactory.php**
- **TES4 Function**: `GetDisposition(target)`
- **Papyrus Output**: `target.GetActorValue("Variable01")`
- **Special Handling**: Disposition doesn't exist in TES5 AV system; uses placeholder variable

### **3. GetCrimeGoldFactory.php**
- **TES4 Function**: `GetCrimeGold()`
- **Papyrus Output**: `TES4CyrodiilCrimeFaction.GetCrimeGold()`
- **Special Handling**: All crime gold functions redirect to the TES4 crime faction reference. Sums bounties from all factions as comment notes.

### **4. ModCrimeGoldFactory.php**
- **TES4 Function**: `ModCrimeGold(value)`
- **Papyrus Output**: `TES4CyrodiilCrimeFaction.ModCrimeGold(value)`
- **Redirect**: `calledOn` replaced with `TES4CyrodiilCrimeFaction` reference

### **5. SetCrimeGoldFactory.php**
- **TES4 Function**: `SetCrimeGold(value)`
- **Papyrus Output**: `TES4CyrodiilCrimeFaction.SetCrimeGold(value)`
- **Redirect**: `calledOn` replaced with `TES4CyrodiilCrimeFaction` reference

### **6. GetPCFameFactory.php**
- **TES4 Function**: `GetPCFame()`
- **Papyrus Output**: `Fame` (variable reference)
- **Behavior**: Returns direct reference to Fame global variable (no function call)

### **7. GetPCInfamyFactory.php**
- **TES4 Function**: `GetPCInfamy()`
- **Papyrus Output**: `Infamy` (variable reference)
- **Behavior**: Returns direct reference to Infamy global variable

### **8. ModPCFameFactory.php**
- **TES4 Function**: `ModPCFame(delta)`
- **Papyrus Output**: `Fame.SetValue(Fame + delta)`
- **Special Handling**: Uses binary ADD expression with current value

### **9. ModPCInfamyFactory.php**
- **TES4 Function**: `ModPCInfamy(delta)`
- **Papyrus Output**: `Infamy.SetValue(Infamy + delta)`
- **Special Handling**: Same as ModPCFame but for Infamy

### **10. SetPCFameFactory.php**
- **TES4 Function**: `SetPCFame(value)`
- **Papyrus Output**: `Fame.SetValue(value)`
- **Note**: Uses write-action reference (createReference not createReadReference)

### **11. SetPCInfamyFactory.php**
- **TES4 Function**: `SetPCInfamy(value)`
- **Papyrus Output**: `Infamy.SetValue(value)`

### **12. PlayGroupFactory.php**
- **TES4 Function**: `PlayGroup(groupName, [force])`
- **Papyrus Output**: `calledOn.playGamebryoAnimation("groupName", true)`
- **Parameter Reorder**: First arg becomes string, second arg always `true`
- **Note**: Comments indicate animation names not understood, requires manual review

### **13. PlaySoundFactory.php**
- **TES4 Function**: `PlaySound(soundId)`
- **Papyrus Output**: `soundRef.play(Game.GetPlayer())`
- **Special Handling**: First arg (soundId) becomes calledOn reference; adds Game.GetPlayer() as argument

### **14. PlaySound3DFactory.php**
- **TES4 Function**: `PlaySound3D(soundId)`
- **Papyrus Output**: `soundRef.play(self)`
- **Difference from PlaySound**: Uses `self` instead of player reference

### **15. ShowMapFactory.php**
- **TES4 Function**: `ShowMap(mapMarker, [args])`
- **Papyrus Output**: `mapRef.AddToMap([remaining args])`
- **Parameter Reorder**: First arg becomes calledOn reference; remaining args passed through

### **16. SetAlertFactory.php**
- **TES4 Function**: `SetAlert(state, [args])`
- **Papyrus Output**: 
  - `calledOn.SheatheWeapon()` if state == 0
  - `calledOn.DrawWeapon()` if state == 1
- **Conversion**: State enum → weapon draw/sheathe functions
- **Exception**: Throws ConversionException for unknown states

### **17. GetButtonPressedFactory.php**
- **TES4 Function**: `GetButtonPressed()`
- **Papyrus Output**: `MessageBoxVariable` (variable reference)
- **Behavior**: Returns the messagebox result variable constant

### **18. StartConversationFactory.php**
- **TES4 Function**: `StartConversation(target, [topicId])`
- **Papyrus Output**: `TES5Filler()` (commented-out complex scene creation)
- **Status**: Conversion scaffolding present but disabled; returns empty conversion

### **19. ForceWeatherFactory.php**
- **TES4 Function**: `ForceWeather(weatherId, force)`
- **Papyrus Output**: `weatherRef.ForceActive(force_bool)`
- **Parameter Handling**: Second arg converted to boolean

### **20. SetWeatherFactory.php**
- **TES4 Function**: `SetWeather(weatherId, [args])`
- **Papyrus Output**: `weatherRef.SetActive([remaining args])`
- **Parameter Reorder**: First arg becomes calledOn; remaining args passed

### **21. ReleaseWeatherOverrideFactory.php**
- **TES4 Function**: `ReleaseWeatherOverride()`
- **Papyrus Output**: `Weather.ReleaseOverride()`
- **Behavior**: Static class method call with no arguments

### **22. GetIsReferenceFactory.php**
- **TES4 Function**: `GetIsReference(targetRef)`
- **Papyrus Output**: `calledOn == targetRef` (equality expression)
- **Conversion**: Function call → comparison operator

### **23. SetCellPublicFlagFactory.php**
- **TES4 Function**: `SetCellPublicFlag(cell, [args])`
- **Papyrus Output**: `cellRef.SetPublic([remaining args])`
- **Parameter Reorder**: First arg becomes calledOn; remaining passed

### **24. GoToJailFactory.php**
- **TES4 Function**: `GoToJail()`
- **Papyrus Output**: `TES4CyrodiilCrimeFaction.SendPlayerToJail()`
- **Redirect**: Calls method on crime faction reference

### **25. PayFineFactory.php**
- **TES4 Function**: `PayFine()`
- **Papyrus Output**: `Game.GetPlayer().PayCrimeGold(1, 1, TES4CyrodiilCrimeFaction)`
- **Hardcoded Args**: First two args=1, third arg=crime faction reference

### **26. IsPlayerInJailFactory.php**
- **TES4 Function**: `IsPlayerInJail()`
- **Papyrus Output**: `tContainer.isInJail` (property access)
- **Note**: Uses legacy TES4 connector plugin tContainer object property

### **27. GetPCFactionMurderFactory.php**
- **Inheritance**: Extends `GetPCFactionAttackFactory`
- **Behavior**: Identical to GetPCFactionAttack

### **28. SetPCFactionMurderFactory.php**
- **Inheritance**: Extends `SetPCFactionAttackFactory`
- **Behavior**: Identical to SetPCFactionAttack

### **29. GetPCFactionStealFactory.php**
- **TES4 Function**: `GetPCFactionSteal(faction)`
- **Papyrus Output**:
  ```papyrus
  Game.GetPlayer().IsInFaction(faction) && faction.GetCrimeGoldNonViolent() > 0
  ```
- **Special Handling**: Complex AND expression with warnings about approximation

### **30. SetPCFactionStealFactory.php**
- **TES4 Function**: `SetPCFactionSteal(faction, value)`
- **Papyrus Output**: 
  - If value==0: `faction.SetCrimeGold(0)`
  - If value==1: `faction.SetCrimeGold(100)`
- **Exception**: Throws for unknown values
- **Note**: Maps binary flag to specific crime gold values

### **31. GetPCFactionAttackFactory.php**
- **TES4 Function**: `GetPCFactionAttack(faction)`
- **Papyrus Output**:
  ```papyrus
  Game.GetPlayer().IsInFaction(faction) && faction.GetCrimeGoldViolent() > 0
  ```
- **Special Handling**: Similar to Steal but checks violent crimes

### **32. SetPCFactionAttackFactory.php**
- **TES4 Function**: `SetPCFactionAttack(faction, value)`
- **Papyrus Output**:
  - If value==0: `faction.SetCrimeGoldViolent(0)`
  - If value==1: `faction.SetCrimeGoldViolent(1000)`
- **Exception**: Throws for unknown values

### **33. ModActorValueFactory.php**
- **TES4 Function**: `ModActorValue(actorValue, delta)` or `ModPCSkill(skillName, delta)`
- **Papyrus Output**: Complex with skill name mapping
- **Special Handling**:
  - Attributes (strength, intelligence, etc.) → TES4Attr[Name] reference + SetValue
  - Skills map via table: 'fatigue'→'Stamina', 'armorer'→'Smithing', 'blade'/'blunt'→'OneHanded', etc.
  - Fallback: `calledOn.ModActorValue(mappedName, delta)`

### **34. GetDetectedFactory.php**
- **TES4 Function**: `GetDetected(actor)`
- **Papyrus Output**: `actor.IsDetectedBy(calledOn)`
- **Parameter Swap**: Target becomes calledOn; calledOn becomes argument

### **35. MessageBoxFactory.php**
- **TES4 Function**: `MessageBox(text, [button1, button2, ...])`
- **Papyrus Output**:
  - Single arg: `Debug.MessageBox(text)`
  - Multiple args: Creates metadata entry, returns `MessageBoxVariable = messageRef.show()`
- **Special Handling**: Complex conversion with metadata logging

### **36. MessageFactory.php**
- **TES4 Function**: `Message(formatString, [args...])` (printf-style)
- **Papyrus Output**: `Debug.Notification(concatenated_string)`
- **Complex Handling**:
  - Parses printf format specifiers (%.2f, %g, etc.)
  - Reconstructs as string concatenation
  - Handles dynamic format strings

### **37. GetInCellFactory.php**
- **TES4 Function**: `GetInCell(cellName)`
- **Papyrus Output**:
  ```papyrus
  StringUtil.Substring(calledOn.GetParentCell(), 0, cellName.length) == cellName
  ```
- **Conversion**: Uses StringUtil for substring comparison

### **38. GetIsCurrentWeatherFactory.php**
- **TES4 Function**: `GetIsCurrentWeather(weatherId)`
- **Papyrus Output**: `Weather.GetCurrentWeather() == weatherRef`
- **Special Handling**: Static Weather class method call

### **39. GetCurrentTimeFactory.php**
- **TES4 Function**: `GetCurrentTime()`
- **Papyrus Output**: `GameHour` (variable reference)
- **Behavior**: Direct variable reference (no function call)

### **40. GetDayOfWeekFactory.php**
- **TES4 Function**: `GetDayOfWeek()`
- **Papyrus Output**: `tTimer.GetDayOfWeek()` (method call on alias)
- **Redirect**: Calls tTimer alias object

### **41. SetActorAlphaFactory.php**
- **TES4 Function**: `SetActorAlpha(alpha, [unused])`
- **Papyrus Output**: `calledOn.SetAlpha(alpha, true)`
- **Parameter Handling**: Second arg always set to `true` (boolean)

### **42. ResurrectFactory.php**
- **TES4 Function**: `Resurrect()`
- **Papyrus Output**: `calledOn.Resurrect()`
- **Behavior**: Simple 1:1 function call with no arguments

### **43. CreateFullActorCopyFactory.php**
- **TES4 Function**: `CreateFullActorCopy(baseActor, [args])`
- **Papyrus Output**: `Game.GetPlayer().placeAtMe(baseActorRef, true, [args...])`
- **Parameter Reorder**: Swaps calledOn with player reference; passes baseActor as first param

### **44. ClearOwnershipFactory.php**
- **TES4 Function**: `ClearOwnership()`
- **Papyrus Output**: `calledOn.SetActorOwner(None)`
- **Special Handling**: Uses `TES5None()` for None value

### **45. SetOwnershipFactory.php**
- **TES4 Function**: `SetOwnership(ownerRef, [args])`
- **Papyrus Output**:
  - If owner is Actor: `calledOn.SetActorOwner(actorRef)`
  - If owner is Faction: `calledOn.SetFactionOwner(factionRef)`
  - If no args: `calledOn.SetActorOwner(Game.GetPlayer().GetActorBase())`
- **Type Check**: Uses ESMAnalyzer to determine owner type

### **46. CastFactory.php**
- **TES4 Function**: `Cast(actorRef, spell, [target])`
- **Papyrus Output**: `actorRef.Cast(calledOn, [target])`
- **Parameter Reorder**: References get swapped; calledOn becomes middle argument

### **47. LockFactory.php**
- **TES4 Function**: `Lock(lockLevel, [lockAsOwner])`
- **Papyrus Output**: `calledOn.Lock(true, lockAsOwner_bool)`
- **Hardcoded**: First arg always `true`; second arg converted to boolean

### **48. UnlockFactory.php**
- **TES4 Function**: `Unlock(lockLevel, [unused])`
- **Papyrus Output**: `calledOn.Lock(false, unused_bool)`
- **Parameter Handling**: First arg always `false`; mirrors Lock structure

### **49. GetAmountSoldStolenFactory.php**
- **TES4 Function**: `GetAmountSoldStolen()`
- **Papyrus Output**: `Game.GetAmountSoldStolen()`
- **Redirect**: Static Game class method call

### **50. GetIsIdFactory.php**
- **TES4 Function**: `GetIsId(actorId)` 
- **Papyrus Output**: `calledOn.GetBaseObject() == actorRef`
- **Conversion**: Function call → equality expression with GetBaseObject()

---

## **Key Conversion Patterns**

1. **Parameter Reordering**: Some functions swap calledOn with first argument
2. **Reference Mapping**: Crime functions → TES4CyrodiilCrimeFaction; Weather → Weather static class
3. **Type-Based Dispatch**: SetOwnership uses ESMAnalyzer to determine SetActorOwner vs SetFactionOwner
4. **Enum Conversion**: SetAlert (0/1) → SheatheWeapon/DrawWeapon; SetCrimeGold values mapped
5. **Expression Conversion**: GetIsReference/GetIsCurrentWeather convert to equality expressions
6. **Variable References**: GetPCFame/Infamy/GameHour return variable refs instead of function calls
7. **Complex Parsing**: Message handles printf-style format strings with concatenation rebuilding