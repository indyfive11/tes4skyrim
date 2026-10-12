# How each TES4 command converts to Papyrus

**Tool:** `python -m tools.script.command_fidelity_audit --export export/Oblivion.esm --export export/Nehrim.esm --export export/Morrowind_ob.esm --samples 8 --tsv <file>`

## <a id="method"></a>Method

- **Call sites:** every command in each plugin's object scripts (`SCPT`),
  dialogue results (`INFO`) and stage results (`QUST`), found on the parse tree
  `script_convert/tes4/` builds. A command is:
  - a name in Oblivion.exe's command table (long name or alias,
    `tes4_export/oblivion_engine_tables.json`);
  - a name the converter knows (`KNOWN_COMMANDS`);
  - any other call that has arguments and is not a declared variable.
- **Probe:** up to N sites per command per plugin, evenly spaced, taken from
  statements where that command is the only one when enough exist. Each site
  is converted by `ScriptConverter` in a cut-down copy of its own script: the
  header, every variable, and the site's own block holding that one statement.
  The setup matches the pipeline: SCRO table, SCRO aliases, the extends class,
  and the fragment's `TopicInfo`/`Quest` base. The same copy with an empty
  block is the baseline. The lines the statement adds are its conversion.
- **Classes**, worst first. A command's verdict is the worst class any of its
  samples produced:

  | Class | The statement emitted |
  |---|---|
  | `error` | the converter raised |
  | `dropped` | no acting code at all: only comments, markers, declarations |
  | `partial` | code, plus a `;NE:` (no equivalent) or `;TODO:` marker |
  | `polyfill` | a `TES4Polyfill.` call, a generated `TES4_<script>.<fn>()` wrapper, or a mod event |
  | `inline` | code with no call: the command became a value, a variable or a constant |
  | `native` | a Papyrus call, none of the above |

- **Tick machinery:** a sample emitted or changed poll and tick lines
  (`RegisterForSingleUpdate`, `SafeGameModeGate`, a stage latch, `SayLine`,
  `SpinAxis`/`GlideAxis`, the seconds-passed prologue, the dialogue gate).
- **Ticked:** the share of ALL the command's call sites (not samples) inside
  `GameMode`, `MenuMode` or `ScriptEffectUpdate`.
- **What it cannot see:** a `native` conversion whose meaning is wrong. The
  example columns show the emitted code so those can be read.
- **Not commands:** a few lowercase rows (`respawnhorse`, `offerhorse`,
  `bookread`, `starttimer`, `isdead`, fewer than 30 calls together) are remote
  script variables (`ref.var`) the collector mistakes for calls.

## <a id="results"></a>Results — 2026-10-02

82,160 call sites at 8 samples per command per plugin: Oblivion.esm 31,920
(254 commands), Nehrim.esm 27,330 (218), Morrowind_ob.esm 22,910 (277). A
re-run gives the same numbers.

| Class | Commands (verdict) | Call sites (estimated) | Share |
|---|---:|---:|---:|
| native | 195 | 59,028 | 71.8% |
| inline | 8 | 1,240 | 1.5% |
| polyfill | 46 | 15,966 | 19.4% |
| partial | 55 | 313 | 0.4% |
| constant | 14 | 152 | 0.2% |
| disabled | 0 | 176 | 0.2% |
| dropped | 89 | 5,285 | 6.4% |
| error | 0 | 0 | 0.0% |

The estimate is each plugin's call count × the share of its samples in that
class, summed over the three plugins.

### <a id="dropped"></a>Dropped: the statement emits nothing

| Command | Est. dropped | Calls | What happens |
|---|---:|---:|---|
| `ModDisposition` | ~1,640 | 1,971 | `;NE: ModDisposition`. Oblivion 490, Morroblivion 1,470. Only a full −100 converts, to `StartCombat` (`commands.py:mod_disposition`); `GetDisposition` reads a fixed 50 (below), so disposition otherwise has no conversion |
| `runScriptLine` | 1,530 | 1,530 | Morroblivion only: OBSE console execution |
| `AddTopic` | ~680 | 2,031 | Only topics with no unlock gate: already visible, so a no-op **by design**. Gated ones set `TES4Unlock_<topic>` |
| `SetQuestObject` | 226 | 226 | `;NE:` |
| `DisableLinkedPathPoints` / `EnableLinkedPathPoints` | 397 | 397 | `;NE:` |
| `SetCellPublicFlag` | 105 | 105 | `;NE:` |
| `UnlockAchievement` / `AddAchievement` | 159 | 159 | By design |
| `SetActorValue` / `ModActorValue` | ~126 | 1,755 | Writes to a removed TES4 attribute (Strength, Agility, Willpower, Luck…): a plain `;TES4 attribute … write dropped` comment with **no `;NE:` marker** |
| `AddFlames` / `RemoveFlames` | 78 | 78 | Nehrim; `HasFlames` (59) reads `false`, below |
| `SetSceneIsComplex`, `SetAllVisible`, `EssentialDeathReload`, `PositionCell`, `RefreshTopicList`, `TrapUpdate`, `SetClass`, `Wait` | 41, 35, 33, 38, 28, 27, 23, 22 | | `;NE:` |

### <a id="constant"></a>Constant: a live read replaced by a fixed value, no marker

`GetDisposition` → `50` (46 calls), `MenuMode` → `0` (16), `IsRaining` → `0`
(13), `IsSwimming` → `0` (25, partly marked), `GetIsAlerted`,
`IsIdlePlaying`, `IsTimePassing`, `IsInDangerousWater`, `IsThirdPerson`.
`IsXBox` → `False` (53) and `GetGameLoaded` are correct by design.

### <a id="partial"></a>Partial: a condition forced false, with a marker

`HasFlames` 59, `GetIsCurrentPackage` ~36 of 119, `GetTalkedToPC` 20,
`GetCrimeKnown` 20, `GetCurrentAIPackage` ~18 of 36, `GetCurrentAIProcedure`
16, `GetPlayerHasLastRiddenHorse` 12. Most of the remaining partial calls are
AI and package reads, which quest gates depend on.

### <a id="disabled"></a>Disabled: the whole block is commented out

About 176 call sites sit in `begin MenuMode <menu id>` blocks the converter
emits as comments (seen for ids 1008, 1009, 1012 and 1044). That loss belongs
to the block, not the command.

### <a id="polyfill"></a>Polyfill: what runs through a workaround (19.4%)

| Mechanism | Commands (calls) |
|---|---|
| `TES4SetStage`: start keeping the quest's variables, set, re-check alias packages | `SetStage` ~7,300 of 9,228 (a quest with no script gets a plain `SetStage`) |
| Combat approach machinery | `StartCombat` 1,103, `StopCombat` 217 |
| Destroyed-flag mirror (`TES4DestroyedRefs`) | `GetDisabled` 830, `SetDestroyed` 188, `GetDestroyed` 27, `CloseCurrentOblivionGate` 16 |
| `TES4Polyfill.IsModLoaded` | `IsModLoaded` 777 (Morroblivion) |
| `SayLine` and the conversation helpers | `Say` ~620 of 935, `SayTo` ~540 of 721, `StartConversation` ~340 of 369 |
| Button MessageBoxes as MESG records | `MessageBox` ~410 of 986, `GetButtonPressed` 182 |
| Crime faction mapping | `GetCrimeGold` 107, `SetCrimeGold` 66, `ModCrimeGold` 61 |
| Player-faction reaction mirror | `ModFactionReaction` 212, `SetFactionReaction` ~70 |
| Glide and spin | `SetPos` ~190 of 306, `Rotate` 94, `SetAngle` ~66 of 83 |

### <a id="tick"></a>Tick machinery is confined to eight commands

About 3,660 call sites (4.5%) emit tick machinery: `GetSecondsPassed` 2,117,
`Say` ~585, `SayTo` ~540, `SetPos` ~190, `Rotate` 94, `SetAngle` ~66,
`StartConversation` ~46, `StartCombat` ~46. That bounds what
[the native-tick plan](../plans/oblivion_native_scripts.md) has to replace.

## <a id="every-command"></a>Every command

Generated by the tool's `--markdown` on 2026-10-02.


| Command | Verdict | Oblivion.esm | Nehrim.esm | Morrowind_ob.esm | Ticked | native | inline | polyfill | partial | constant | disabled | dropped | error | Tick machinery | Example source | Emitted |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|
| `SetStage` | polyfill | 3,479 | 1,310 | 4,439 | 20% | 5 | 0 | 19 | 0 | 0 | 0 | 0 | 0 | 0 | SetStage SE05 92 | TES4_SE05QuestScript.TES4SetStage(SE05 as TES4_SE05QuestScript, 92) |
| `Enable` | native | 1,112 | 1,857 | 732 | 53% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SE02GardensSpawn1Ref.enable | SE02GardensSpawn1Ref.Enable() |
| `GetStage` | native | 1,737 | 1,011 | 920 | 63% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if getStage SE13 >= 200 | If SE13.GetStage() >= 200 |
| `Disable` | native | 769 | 2,381 | 508 | 52% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | disable | Disable() |
| `AddItem` | native | 1,129 | 589 | 1,758 | 16% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | AddItem WeapIronDagger 1 | AddItem(WeapIronDagger, 1) |
| `RemoveItem` | native | 974 | 373 | 1,250 | 23% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | myActivator.removeitem SEOrderPriestWidget 10 | myActivator.RemoveItem(SEOrderPriestWidget, 10) |
| `MoveToMarker` | native | 850 | 1,468 | 201 | 54% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SEHaskillREF.MoveTo SEHaskillSummonReturnMarker | SEHaskillREF.MoveTo(SEHaskillSummonReturnMarker) |
| `GetSecondsPassed` | native | 664 | 1,349 | 104 | 99% | 23 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 23 | set timer to timer - getSecondsPassed | RegisterForSingleUpdate(0.1) \| RegisterForSingleUpdate(0.1) \| RegisterForSingleUpdate(0.1) \| RegisterForSingleUpdate(0.1) \| RegisterForSingleUpdate(0.1) \| RegisterForSingleUpdate(0.1) \| TES4_SecondsPa |
| `EvaluatePackage` | native | 1,163 | 699 | 225 | 43% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SESheogorathRef.EvaluatePackage | (SESheogorathRef as Actor).EvaluatePackage() |
| `AddTopic` | native | 228 | 18 | 1,785 | 10% | 16 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | AddTopic contract | TES4Unlock_contract.SetValue(1) |
| `ModDisposition` | dropped | 490 | 11 | 1,470 | 4% | 0 | 0 | 4 | 0 | 0 | 0 | 20 | 0 | 0 | modDisposition player -25 | ;NE: ModDisposition |
| `PlaySound` | native | 119 | 1,534 | 305 | 60% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | player.playSound AMBStoneShift03 | AMBStoneShift03.Play(Game.GetPlayer()) |
| `PlayGroup` | native | 841 | 815 | 84 | 49% | 21 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | playgroup forward 0 | Self.PlayAnimation("Forward") |
| `Activate` | native | 1,056 | 483 | 102 | 22% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | myParent.activate mySelf 1 | myParent.Activate(mySelf) |
| `GetDistance` | native | 695 | 718 | 183 | 88% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( GetDistance Player < 200 ) | If (GetDistance(Player) < 200) |
| `SetActorValue` | native | 416 | 868 | 257 | 70% | 22 | 0 | 1 | 0 | 0 | 0 | 1 | 0 | 0 | SetAv Aggression 5 | SetActorValue("Aggression", 0) |
| `runscriptline` | dropped | 0 | 0 | 1,530 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | runScriptLine "set ObXPMain.interOpGainedXPMessage to sv_Construct %qCompleted Mages Guilt: Wizard's Staff quest%q" | ;NE: runScriptLine - OBSE console execution, no Papyrus equivalent (runScriptLine "set ObXPMain.interOpGainedXPMessage to sv_Construct %qCompleted Mages Guilt: Wizard's Staff quest%q") |
| `Message` | native | 310 | 917 | 166 | 32% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | message " The roots will not budge." | TES4_Notify(" The roots will not budge.") \| TES4_NoteAt = Utility.GetCurrentRealTime() \| Debug.Notification(asText) \| If asText != TES4_NoteText \|\| TES4_since >= 3.33 \|\| TES4_since < 0.0 \| TES4_NoteTe |
| `StartCombat` | polyfill | 308 | 364 | 431 | 51% | 0 | 0 | 24 | 0 | 0 | 0 | 0 | 0 | 1 | startCombat SE09Battle04Atronach02REF | If (TES4Polyfill.SafeGameModeGate(Self)) \| If (TES4Polyfill.SafeGameModeGate(Self)) \| If (TES4Polyfill.SafeGameModeGate(Self)) \| TES4Polyfill.ForceCombatApproach(Self, SE09Battle04Atronach02REF, TES4F |
| `GetItemCount` | native | 564 | 327 | 172 | 67% | 22 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | if player.getitemcount SEKnightHeart > 0 | If Game.GetPlayer().GetItemCount(SEKnightHeart) > 0 |
| `MessageBox` | native | 482 | 255 | 249 | 26% | 14 | 0 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | MessageBox "The mysterious crystals have sealed the door." | Debug.MessageBox("The mysterious crystals have sealed the door.") |
| `IsActionRef` | native | 704 | 260 | 7 | 0% | 20 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if isActionRef player == 1 | If akActionRef == Game.GetPlayer() |
| `Say` | polyfill | 529 | 361 | 45 | 81% | 8 | 0 | 16 | 0 | 0 | 0 | 0 | 0 | 15 | Set RoomTwoTalkLength to SEGrommokRef.Say SE03GrommokChamberTwoDem01 | If IsInDialogueWithPlayer() \|\| TES4Polyfill.PlayerIsInDialogue()  ; TES4 GameMode did not run while a menu was open \| If (TES4Polyfill.SafeGameModeGate(Self)) \| RoomTwoTalkLength = TES4Polyfill.SayLin |
| `AddSpell` | native | 290 | 549 | 86 | 58% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | player.addspell SESuicidePower | Game.GetPlayer().AddSpell(SESuicidePower) |
| `Cast` | native | 336 | 452 | 124 | 72% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Cast SELpScalonInvisibility OnMyself | SELpScalonInvisibility.Cast(Self, OnMyself) |
| `RemoveSpell` | native | 600 | 181 | 81 | 45% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | player.removeSpell SE09PwGKHead1 | Game.GetPlayer().RemoveSpell(SE09PwGKHead1) |
| `GetDisabled` | polyfill | 134 | 545 | 151 | 79% | 0 | 0 | 24 | 0 | 0 | 0 | 0 | 0 | 0 | if GetDisabled == 1 | If TES4Polyfill.GetDisabled(Self, TES4DestroyedRefs) |
| `GetSelf` | inline | 618 | 186 | 24 | 39% | 3 | 21 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | set mySelf to getSelf | mySelf = Self |
| `ShowMap` | native | 171 | 14 | 642 | 1% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | ShowMap SEDeepwallowMapMarker | SEDeepwallowMapMarker.AddToMap(true) |
| `SetFactionRank` | native | 542 | 90 | 160 | 29% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | setFactionRank CreatureFaction 0						; add to critter faction to avoid disputes | SetFactionRank(CreatureFaction, 0)  ; add to critter faction to avoid disputes |
| `ismodloaded` | polyfill | 0 | 0 | 777 | 0% | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | if IsModLoaded "Cobl Main.esm" == 1 | If TES4Polyfill.IsModLoaded("Cobl Main.esm") == 1 |
| `StartQuest` | native | 324 | 8 | 397 | 7% | 15 | 0 | 9 | 0 | 0 | 0 | 0 | 0 | 0 | startquest ms47FIN | ms47FIN.Start() |
| `SayTo` | polyfill | 318 | 366 | 37 | 84% | 6 | 0 | 18 | 0 | 0 | 0 | 0 | 0 | 18 | set convtimer to SayTo SESheogorathRef SE10MessengerGreeting | If IsInDialogueWithPlayer() \|\| TES4Polyfill.PlayerIsInDialogue()  ; TES4 GameMode did not run while a menu was open \| If (TES4Polyfill.SafeGameModeGate(Self)) \| convtimer = TES4Polyfill.SayLine(Self,  |
| `GetParentRef` | native | 426 | 145 | 0 | 38% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | set myParent to getParentRef | myParent = GetLinkedRef() |
| `Look` | native | 177 | 375 | 17 | 78% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SESheogorathRef.look SE10SaintRef | (SESheogorathRef as Actor).SetLookAt(SE10SaintRef) |
| `GetDead` | native | 391 | 75 | 101 | 66% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if tempRef.getdead == 0 | If !(TempRef.IsDead()) |
| `call` | native | 0 | 474 | 87 | 29% | 14 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | Call GlobalScriptExpGained EPWert, TrefferPlayer, TrefferGesamt, VarSpecialKill | GlobalScriptExpGained.TES4Call(Self, EPWert, TrefferPlayer, TrefferGesamt, VarSpecialKill.GetValue() as Int) |
| `StopQuest` | native | 441 | 55 | 39 | 10% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | StopQuest SE46 | SE46.Stop() |
| `GetInCell` | native | 370 | 52 | 103 | 83% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( Player.GetInCell SENSSacellumArdenSul == 0 ) | If (!(TES4_IsInSENSSacellumArdenSul(Game.GetPlayer()))) \| Bool Function TES4_IsInSENSSacellumArdenSul(ObjectReference akRef) \| If TES4_parentCell == SENSSacellumArdenSul \| Return true \| Return true \|  |
| `PlayMagicShaderVisuals` | native | 149 | 333 | 5 | 33% | 21 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PMS SE10BrellachChimeEffect | SE10BrellachChimeEffect.Play(Self, -1.0) |
| `SetAlert` | native | 263 | 179 | 0 | 54% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | seducer02.SetAlert 1 | seducer02.SetAlert(true) |
| `GetActorValue` | native | 79 | 120 | 239 | 83% | 16 | 5 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | If GetAv Magicka >= 10 | If GetActorValue("Magicka") >= 10 |
| `GetLevel` | native | 259 | 135 | 2 | 62% | 17 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | if ( player.GetLevel >=26 ) && ( rewardVAR < 600 ) | If Game.GetPlayer().GetLevel() >= 26 && rewardVAR < 600 |
| `AddScriptPackage` | native | 275 | 101 | 4 | 28% | 20 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SEXiditteArbiterREF.addScriptPackage SEXiditteArbiterTrapPKG | (SEXiditteArbiterREF as Actor).EvaluatePackage() |
| `SetEssential` | native | 279 | 84 | 13 | 16% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SetEssential SEMuurine 0 | SEMuurine.SetEssential(false) |
| `StartConversation` | polyfill | 165 | 124 | 80 | 88% | 2 | 0 | 22 | 0 | 0 | 0 | 0 | 0 | 3 | Self.StartConversation Player SE05PostTortureGreet | TES4Polyfill.ForceGreet(TES4ForceGreets, 20, 2, GetTargetActor()) |
| `UnLock` | native | 235 | 97 | 34 | 34% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | XPBloodPoolDoorREF.unlock | XPBloodPoolDoorREF.Lock(false) |
| `GetStageDone` | native | 274 | 63 | 16 | 60% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if getstagedone SEObelisks 35 == 1 | If SEObelisks.GetStageDone(35) |
| `ModPCFame` | native | 138 | 0 | 186 | 6% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | modpcfame 1 | TES4Fame.Mod(1 as Float) |
| `KillActor` | native | 167 | 113 | 34 | 51% | 23 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | player.kill | Game.GetPlayer().Kill() |
| `SetPos` | polyfill | 9 | 288 | 9 | 96% | 9 | 0 | 15 | 0 | 0 | 0 | 0 | 0 | 15 | SetPos Y fy | TES4Polyfill.GlideAxis(Self, 1, fy, TES4_GlideRefs, TES4_GlideGoals, TES4_SecondsPassed) \| RegisterForSingleUpdate(0.1) \| RegisterForSingleUpdate(0.1) \| RegisterForSingleUpdate(0.1) \| RegisterForSingl |
| `GetPos` | native | 12 | 274 | 10 | 92% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | set posX to getPos X | posX = Self.GetPositionX() |
| `Lock` | native | 210 | 39 | 46 | 35% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SE32ArmoryDoorRef.Lock 1											; reset the lock level | SE32ArmoryDoorRef.Lock(1)  ; reset the lock level |
| `PlaySound3D` | native | 55 | 191 | 47 | 76% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | PlaySound3d AMBChimesHit01 | AMBChimesHit01.Play(Self) |
| `PickIdle` | native | 189 | 96 | 4 | 64% | 20 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | pickidle | Debug.SendAnimationEvent(Self, "IdleForceDefaultState") |
| `IsAnimPlaying` | native | 211 | 62 | 13 | 88% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if isAnimPlaying == 0 && busy == 1 | If (Self.GetAnimationVariableBool("bAnimPlaying") as Int) == 0 && busy == 1 |
| `GetDeadCount` | native | 51 | 106 | 108 | 86% | 21 | 0 | 0 | 0 | 0 | 3 | 0 | 0 | 0 | if ( GetDeadCount SEHorkvirBearArmDementia == 1 ) \|\| ( GetDeadCount SEJastiraNanusDementia == 1 ) \|\| ( GetDeadCount SEJzidzoDementia == 1 ) \|\| ( GetDeadCount SEAtrabhiDementia == 1 ) \|\| ( GetDeadCount SEUrulgoAgamphDementia == 1 ) | If SEHorkvirBearArmDementia.GetDeadCount() == 1 \|\| SEJastiraNanusDementia.GetDeadCount() == 1 \|\| SEJzidzoDementia.GetDeadCount() == 1 \|\| SEAtrabhiDementia.GetDeadCount() == 1 \|\| SEUrulGoAgamphDementia |
| `SetGhost` | native | 89 | 168 | 0 | 58% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | setGhost 1 | SetGhost(1) |
| `EquipItem` | native | 157 | 46 | 36 | 52% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Player.EquipItem SE03Duskfang01 | Game.GetPlayer().EquipItem(SE03Duskfang01) |
| `SetQuestObject` | dropped | 191 | 30 | 5 | 3% | 0 | 0 | 0 | 0 | 0 | 0 | 21 | 0 | 0 | SetQuestObject SQ09SlaughterfishScales 0 | ;NE: SetQuestObject |
| `DisableLinkedPathPoints` | dropped | 141 | 79 | 0 | 35% | 0 | 0 | 0 | 0 | 0 | 0 | 16 | 0 | 0 | disableLinkedPathPoints | ;NE: disableLinkedPathPoints |
| `StopCombat` | polyfill | 97 | 55 | 65 | 62% | 0 | 0 | 24 | 0 | 0 | 0 | 0 | 0 | 0 | SEXiditteArbiterREF.stopcombat player | TES4Polyfill.EndCombatApproach((SEXiditteArbiterREF as Actor), TES4ForceCombatAttackers, TES4CombatApproaches) |
| `ModActorValue` | native | 91 | 18 | 105 | 57% | 17 | 0 | 0 | 0 | 0 | 0 | 7 | 0 | 0 | myActivator.modav aggression 100			; set aggression back to normal | myActivator.ModActorValue("Aggression", 100)  ; set aggression back to normal |
| `eval` | inline | 0 | 174 | 39 | 14% | 8 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if eval (spells[resoult] == 0blackTheartSblight) | If spells == d0blackTheartSblight |
| `ModFactionReaction` | polyfill | 12 | 1 | 199 | 0% | 0 | 0 | 17 | 0 | 0 | 0 | 0 | 0 | 0 | modfactionreaction MalacathOgreFaction playerfaction -50 | TES4Polyfill.MirrorPlayerFactionRelation(MalacathOgreFaction, 1) \| MalacathOgreFaction.SetEnemy(PlayerFaction, false, false) |
| `EnablePlayerControls` | native | 90 | 92 | 27 | 63% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | EnablePlayerControls | Game.EnablePlayerControls() \| TES4ControlsDisabled.SetValue(0) |
| `Reset3DState` | native | 125 | 81 | 0 | 6% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | reset3DState | Self.MoveTo(Self) |
| `RemoveMe` | native | 7 | 192 | 0 | 12% | 15 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | RemoveMe | Delete() |
| `DisablePlayerControls` | native | 72 | 97 | 26 | 74% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | DisablePlayerControls | Game.DisablePlayerControls() \| TES4ControlsDisabled.SetValue(1) |
| `ResurrectActor` | native | 91 | 72 | 26 | 71% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SEUshnarsDogRef.Resurrect 1 | (SEUshnarsDogRef as Actor).Resurrect() |
| `SetDestroyed` | polyfill | 157 | 26 | 5 | 15% | 0 | 0 | 21 | 0 | 0 | 0 | 0 | 0 | 0 | SEObelisk13.setdestroyed 0 | TES4Polyfill.SetDestroyed(SEObelisk13, TES4DestroyedRefs, false) |
| `GetButtonPressed` | polyfill | 49 | 65 | 68 | 96% | 0 | 0 | 24 | 0 | 0 | 0 | 0 | 0 | 0 | set button to getbuttonpressed | Return TES4_akMsg.Show(afArg1, afArg2, afArg3, afArg4, afArg5, afArg6, afArg7, afArg8, afArg9) \| button = TES4_TakeMsgButton() \| Int Function TES4_ShowMsg(Message TES4_akMsg, Float afArg1 = 0.0, Float |
| `EnableLinkedPathPoints` | dropped | 110 | 67 | 0 | 19% | 0 | 0 | 0 | 0 | 0 | 0 | 16 | 0 | 0 | enableLinkedPathPoints | ;NE: enableLinkedPathPoints |
| `GetRandomPercent` | native | 91 | 64 | 21 | 55% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | If GetRandomPercent <33 | If Utility.RandomInt(0, 99) < 33 |
| `GetFactionRank` | native | 23 | 2 | 147 | 69% | 18 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | If Player.GetFactionRank SEHeretic == -1 | If Game.GetPlayer().GetFactionRank(SEHeretic) == -1 |
| `StopMagicShaderVisuals` | native | 92 | 79 | 0 | 51% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SMS SE10BrellachChimeEffect | SE10BrellachChimeEffect.Stop(Self) |
| `SetOwnership` | native | 112 | 0 | 51 | 83% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | setownership SEOrderPriestFaction | Self.SetFactionOwner(SEOrderPriestFaction) |
| `SetOpenState` | native | 73 | 65 | 18 | 62% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SE32ArmoryDoorRef.SetOpenState 0 | SE32ArmoryDoorRef.SetOpen(0) |
| `PlaceAtMe` | native | 118 | 27 | 10 | 67% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | XPCorpserotARGuardSpotREF.placeatme SELLCorpserotArenaGuard01 2 | XPCorpserotARGuardSpotREF.PlaceAtMe(SELLCorpserotArenaGuard01, 2) |
| `TriggerHitShader` | native | 36 | 102 | 0 | 70% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | TriggerHitShader 1 | Game.TriggerScreenBlood(3) |
| `GetInSameCell` | native | 40 | 45 | 44 | 92% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( GetInSameCell Player == 0 ) | If (!(Self.GetParentCell() == Player.GetParentCell())) |
| `GetBaseActorValue` | native | 30 | 50 | 40 | 26% | 17 | 6 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | set myAggression to getbaseav aggression | myAggression = GetTargetActor().GetBaseActorValue("Aggression") as Int |
| `GetIsCurrentPackage` | native | 107 | 8 | 4 | 77% | 14 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 | If SEMuurineRef.GetIsCurrentPackage SEMuurineFightTue20x2 == 0 | If !((SEMuurineRef as Actor).GetCurrentPackage() == SEMuurineFightTue20x2) |
| `SetIgnoreFriendlyHits` | native | 74 | 13 | 32 | 30% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | setignorefriendlyhits 1 | Self.IgnoreFriendlyHits(true) |
| `PlayMagicEffectVisuals` | native | 109 | 1 | 7 | 51% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | mySpawn.pme LISH | effectShockShield.Play(mySpawn, -1.0) |
| `ModPCMiscStat` | native | 40 | 76 | 1 | 15% | 9 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | ModPCMiscStat 27 1 | Game.IncrementStat("Nirnroots Found", 1) |
| `IsInCombat` | native | 31 | 80 | 2 | 67% | 17 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | If IsInCombat == 0 | If !(IsInCombat()) |
| `GetQuestRunning` | native | 48 | 37 | 25 | 52% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | If GetQuestRunning SE32 == 1 | If SE32.IsRunning() |
| `GetCrimeGold` | polyfill | 45 | 6 | 56 | 48% | 0 | 0 | 22 | 0 | 0 | 0 | 0 | 0 | 0 | If GetCrimeGold == 0 | If TES4Polyfill.CrimeFaction(TES4CrimeFactions).GetCrimeGold() == 0 |
| `SetCellPublicFlag` | dropped | 101 | 0 | 4 | 16% | 0 | 0 | 0 | 0 | 0 | 0 | 12 | 0 | 0 | SetCellPublicFlag ICPalaceLibrary 1 | ;NE: SetCellPublicFlag |
| `StopLook` | native | 88 | 11 | 6 | 29% | 22 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | StopLook | Self.ClearLookAt() |
| `SetFactionReaction` | polyfill | 102 | 1 | 0 | 19% | 3 | 0 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | setfactionreaction SE06DarkSeducerFaction playerFaction -100 | TES4Polyfill.MirrorPlayerFactionRelation(SE06DarkSeducerFaction, 1) \| SE06DarkSeducerFaction.SetEnemy(PlayerFaction, false, false) |
| `GetIsID` | native | 85 | 15 | 1 | 13% | 17 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | elseif myActivator.GetIsID SEObeliskNEW == 1 | If myActivator.GetBaseObject() == SEObeliskNEW |
| `unlockachievement` | dropped | 0 | 100 | 0 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | UnlockAchievement "NEH_SHADOW_AND_LIGHT"									; Unlock Steam Achievement | ;NE: UnlockAchievement "NEH_SHADOW_AND_LIGHT"  ;no Papyrus equivalent |
| `SetPCExpelled` | native | 35 | 16 | 46 | 60% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SetPCExpelled DarkBrotherhood 0 | myDarkBrotherhood.SetPlayerExpelled(false) |
| `GetInFaction` | native | 59 | 7 | 28 | 39% | 23 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if myTrigger.GetInFaction SE06DarkSeducerFaction == 0 && active == 1 | If !(myTrigger.IsInFaction(SE06DarkSeducerFaction)) && active == 1 |
| `GetAngle` | native | 23 | 66 | 5 | 96% | 21 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | set degCurr to (myParent.getAngle z) | degCurr = myParent.GetAngleZ() |
| `Rotate` | polyfill | 9 | 56 | 29 | 100% | 0 | 0 | 24 | 0 | 0 | 0 | 0 | 0 | 24 | myParent.rotate z 15 | TES4Polyfill.SpinAxis(myParent, 5, myParent.GetAngleZ() + (15) * TES4_SecondsPassed, 15, TES4_GlideRefs, TES4_GlideGoals, TES4_SecondsPassed) \| RegisterForSingleUpdate(0.1) \| RegisterForSingleUpdate(0 |
| `SetAngle` | polyfill | 4 | 65 | 14 | 89% | 4 | 0 | 16 | 0 | 0 | 0 | 0 | 0 | 16 | myParent.setAngle z degGoal | TES4Polyfill.GlideAxis(myParent, 5, degGoal, TES4_GlideRefs, TES4_GlideGoals, TES4_SecondsPassed) \| RegisterForSingleUpdate(0.1) \| RegisterForSingleUpdate(0.1) \| RegisterForSingleUpdate(0.1) \| Registe |
| `RemoveScriptPackage` | native | 12 | 64 | 4 | 41% | 20 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SEXiditteArbiterREF.removeScriptPackage SEXiditteArbiterTrapPKG | (SEXiditteArbiterREF as Actor).EvaluatePackage() |
| `ForceWeather` | native | 45 | 15 | 18 | 53% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | fw SEWaitingRoomWeather | SEWaitingRoomWeather.ForceActive(False) |
| `GetActionRef` | inline | 60 | 15 | 2 | 3% | 0 | 18 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | set myTrigger to GetActionRef | myTrigger = akActionRef |
| `GetEquipped` | native | 13 | 50 | 7 | 23% | 23 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | If Player.GetEquipped TGGrayFoxCowl == 1 | If Game.GetPlayer().IsEquipped(TGGrayFoxCowl) |
| `StopCombatAlarmOnActor` | native | 64 | 0 | 5 | 19% | 13 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Player.SCAOnActor | Game.GetPlayer().StopCombatAlarm() |
| `ModPCInfamy` | native | 68 | 0 | 0 | 0% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | modpcinfamy 1 | TES4Infamy.Mod(1 as Float) |
| `SetCrimeGold` | polyfill | 18 | 32 | 16 | 29% | 0 | 0 | 23 | 0 | 0 | 1 | 0 | 0 | 0 | Player.SetCrimeGold 0 | TES4Polyfill.SetCrimeGold(TES4Polyfill.CrimeFaction(TES4CrimeFactions), 0) |
| `SetActorsAI` | native | 5 | 60 | 0 | 26% | 13 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | self.setactorsai 0 | GetTargetActor().EnableAI(false) |
| `SetActorAlpha` | native | 36 | 17 | 10 | 49% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | saa 0.01 | GetTargetActor().SetAlpha(0.01) |
| `ModCrimeGold` | polyfill | 18 | 9 | 34 | 20% | 0 | 0 | 24 | 0 | 0 | 0 | 0 | 0 | 0 | player.modCrimeGold SECrimeGold | TES4Polyfill.CrimeFaction(TES4CrimeFactions).ModCrimeGold(SECrimeGold.GetValue() as Int, false) |
| `AddAchievement` | dropped | 59 | 0 | 0 | 2% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | addachievement 40 | ;NE: addachievement |
| `HasFlames` | partial | 0 | 58 | 1 | 100% | 0 | 0 | 0 | 9 | 0 | 0 | 0 | 0 | 0 | if ( AbteiTirinLightPost01Ref.HasFlames == 1 ) | If (false)  ;NE: HasFlames has no Skyrim equivalent |
| `GetDetectionLevel` | native | 54 | 0 | 4 | 67% | 12 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | If GetDetectionLevel Player >= 3 | If ((Player.IsDetectedBy(Self) as Int) * 3) >= 3 |
| `PushActorAway` | native | 8 | 50 | 0 | 67% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | bossRef.pushActorAway selfRef 5 | bossRef.PushActorAway((selfRef as Actor), 5) |
| `SetRestrained` | native | 47 | 8 | 2 | 37% | 18 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SetRestrained 1 | Self.SetDontMove(true) |
| `GetLineOfSight` | native | 9 | 43 | 2 | 89% | 18 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if player.getLOS SE08c5SheldenGate == 1				; check line-of-sight so player is spoken to when looking at Shelden NPC | If Game.GetPlayer().HasLOS(SE08c5SheldenGate)  ; check line-of-sight so player is spoken to when looking at Shelden NPC |
| `IsXBox` | constant | 53 | 0 | 0 | 2% | 0 | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | if ( isxbox == 1 ) | If (False == 1) |
| `togglespecialanim` | dropped | 0 | 0 | 50 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | ActorRef.ToggleSpecialAnim "CurseOfHircine\SneakBackward_werewolf.kf" 1 | ;NE: ActorRef.ToggleSpecialAnim - no Papyrus equivalent (ActorRef.ToggleSpecialAnim "CurseOfHircine\SneakBackward_werewolf.kf", 1) |
| `StreamMusic` | dropped | 0 | 35 | 12 | 81% | 6 | 0 | 0 | 0 | 0 | 0 | 10 | 0 | 0 | StreamMusic "data\music\special\Theme_06_Part01.mp3" | ;NE: StreamMusic - no converted music for ("data\music\special\Theme_06_Part01.mp3") |
| `GetDisposition` | constant | 43 | 2 | 1 | 11% | 3 | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | if getDisposition Player > 25 | If 50 > 25 |
| `GetInWorldspace` | native | 16 | 12 | 18 | 76% | 24 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if getinworldspace SENSPalace == 1 | If Game.GetPlayer().GetWorldSpace() == SENSPalace |
| `AddFlames` | dropped | 0 | 46 | 0 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | AddFlames | ;NE: AddFlames has no Skyrim equivalent |
| `Dispel` | native | 40 | 1 | 2 | 14% | 10 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | player.dispel SE09PwGKHead1 | Game.GetPlayer().DispelSpell(SE09PwGKHead1) |
| `GetOpenState` | native | 11 | 32 | 0 | 86% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if myParentRef.getOpenState == 3 | If myParentRef.GetOpenState() == 3 |
| `SetWeather` | native | 34 | 8 | 0 | 86% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SetWeather SE09SummoningWeather 1 | SE09SummoningWeather.SetActive(False, False) |
| `ResetInterior` | polyfill | 41 | 0 | 0 | 0% | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | ResetInterior ArenaMatchCell | TES4Polyfill.ResetInterior(ArenaMatchCell, TES4Movers_arenamatchcell) |
| `GetDayOfWeek` | native | 36 | 5 | 0 | 15% | 11 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | if GetDayofWeek == DayofLastUse | If (GameDaysPassed.GetValueInt() % 7) == DayofLastUse |
| `SetSceneIsComplex` | dropped | 9 | 32 | 0 | 95% | 0 | 0 | 0 | 0 | 0 | 0 | 16 | 0 | 0 | SetSceneIsComplex 1 | ;NE: SetSceneIsComplex |
| `GetPCMiscStat` | native | 1 | 40 | 0 | 100% | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( GetPCMiscStat 7 >= 30 ) | If (Game.QueryStat("Locations Discovered") >= 30) |
| `sv_construct` | inline | 0 | 0 | 40 | 100% | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | set quizQuestion to sv_Construct "On a clear day you chance upon a strange animal, its leg trapped in a hunter's clawsnare. Judging from the bleeding it will not survive long." | quizQuestion = "On a clear day you chance upon a strange animal, its leg trapped in a hunter's clawsnare. Judging from the bleeding it will not survive long." |
| `GetPCFame` | native | 39 | 0 | 0 | 3% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( GetPCFame >= 10 ) | If (TES4Fame.GetValueInt() >= 10) |
| `SetPCFactionSteal` | native | 14 | 0 | 25 | 41% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | setPCFactionSteal DarkBrotherhood 0 | myDarkBrotherhood.SetCrimeGold(0) |
| `GetAmountSoldStolen` | native | 38 | 0 | 0 | 37% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | If GetAmountSoldStolen >= 1000 | If TES4GoldFenced.GetValue() >= 1000 |
| `SetForceRun` | native | 37 | 0 | 1 | 13% | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | setforcerun 1 | Self.SetActorValue("SpeedMult", 150.0) |
| `GetIsReference` | inline | 36 | 0 | 2 | 13% | 1 | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if myActionRef.getIsReference SEObeliskTurnOff == 1 | If myActionRef == SEObeliskTurnOff |
| `PositionCell` | dropped | 0 | 4 | 34 | 58% | 0 | 0 | 0 | 0 | 0 | 0 | 12 | 0 | 0 | ErothinTorwacheTrigBoxRef.PositionCell 0, 0, 0, 0, TrashCell | ;NE: PositionCell needs a target marker; Papyrus MoveTo takes a reference, not cell coordinates (0, 0, 0, 0, TrashCell) |
| `GetLocked` | native | 26 | 4 | 7 | 68% | 17 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | If GetLocked > 0 | If IsLocked() |
| `GetCurrentAIPackage` | partial | 13 | 9 | 14 | 100% | 12 | 0 | 0 | 12 | 0 | 0 | 0 | 0 | 0 | if ( KimFermaleRef.getCurrentAIPackage == 6 ) && ( KimFermaleRef.getPackageTarget == player ) | If ((KimFermaleRef as Actor).GetCurrentPackage() == MQ29KimFolgtPlayer \|\| (KimFermaleRef as Actor).GetCurrentPackage() == MQ29KimZumPlatz \|\| (KimFermaleRef as Actor).GetCurrentPackage() == MQ23KimGeht |
| `IsSpellTarget` | native | 24 | 0 | 11 | 89% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if isSpellTarget SERelmynaExperimentSpell | If Self.HasMagicEffectWithKeyword(TES4FX_seff) |
| `SetPCFactionMurder` | native | 18 | 0 | 17 | 31% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | setPCFactionMurder DarkBrotherhood 0 | myDarkBrotherhood.SetCrimeGoldViolent(0) |
| `SetAllVisible` | dropped | 2 | 33 | 0 | 94% | 0 | 0 | 0 | 0 | 0 | 0 | 10 | 0 | 0 | SetAllVisible 1 | ;NE: SetAllVisible |
| `GetSitting` | native | 27 | 3 | 4 | 88% | 10 | 0 | 1 | 4 | 0 | 0 | 0 | 0 | 0 | If GetSitting == 0 \|\| GetSitting == 3 \|\| GetSitting == 13 | If GetSitState() == 0 \|\| GetSitState() == 3 \|\| GetSitState() == 13 |
| `RemoveAllItems` | native | 26 | 2 | 6 | 53% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | player.removeAllItems SE38HoldingChestREF 1 | Game.GetPlayer().RemoveAllItems(SE38HoldingChestREF, 1) |
| `EssentialDeathReload` | dropped | 4 | 29 | 0 | 79% | 0 | 0 | 0 | 0 | 0 | 0 | 12 | 0 | 0 | EssentialDeathReload "Martin has been slain. All hope is now lost." | ;NE: EssentialDeathReload |
| `IsInInterior` | native | 22 | 9 | 1 | 94% | 16 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | If IsInInterior == 0 | If !(GetTargetActor().GetParentCell().IsInterior()) |
| `IsPCSleeping` | native | 16 | 0 | 16 | 100% | 8 | 0 | 2 | 0 | 0 | 6 | 0 | 0 | 10 | if ( IsPCSleeping == 1 ) | TES4_MenuModeSleepBody() \| TES4_MenuModeSleepBody() \| RegisterForSleep() \| If (TES4_PCSleeping == 1) \| TES4_PCSleeping = 1 \| TES4_PCSleeping = 0 |
| `SetPCFactionAttack` | native | 6 | 0 | 26 | 38% | 14 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SetPCFactionAttack ThievesGuild 0 | ThievesGuild.SetCrimeGoldViolent(0) |
| `RemoveFlames` | dropped | 0 | 31 | 1 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 0 | RemoveFlames | ;NE: RemoveFlames has no Skyrim equivalent |
| `GetGold` | native | 25 | 4 | 2 | 10% | 14 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | set goldCount to OrumGangCourier1Ref.getgold | goldCount = (OrumGangCourier1Ref as Actor).GetGoldAmount() |
| `ForceActorValue` | native | 26 | 1 | 2 | 45% | 11 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | forceav aggression 0 | SetActorValue("Aggression", 0) |
| `GetCombatTarget` | native | 25 | 3 | 1 | 62% | 10 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | if getCombatTarget == player | If GetCombatTarget() == Player |
| `RefreshTopicList` | dropped | 28 | 0 | 0 | 14% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | RefreshTopicList | ;NE: RefreshTopicList |
| `SetUnconscious` | native | 26 | 2 | 0 | 68% | 9 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | self.setunconscious 1 | mySelf.SetUnconscious(1) |
| `GetCurrentTime` | native | 26 | 0 | 2 | 64% | 9 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | if getCurrentTime < 21 | If GameHour.GetValue() < 21 |
| `GetDestroyed` | polyfill | 26 | 1 | 0 | 81% | 0 | 0 | 9 | 0 | 0 | 0 | 0 | 0 | 0 | if ( SEGadeneriRalvelREF.GetDestroyed == 1 ) && ( GadeneriVAR == 0 ) | If TES4Polyfill.GetDestroyed(SEGadeneriRalvelREF, TES4DestroyedRefs) == 1 && GadeneriVAR == 0 |
| `ModPCSkill` | native | 25 | 1 | 1 | 52% | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | ModPCSkill HandToHand 5 | Game.GetPlayer().ModActorValue("UnarmedDamage", 5) |
| `TrapUpdate` | dropped | 22 | 5 | 0 | 96% | 0 | 0 | 0 | 0 | 0 | 0 | 13 | 0 | 0 | trapUpdate | ;NE: trapUpdate |
| `PositionWorld` | native | 0 | 8 | 19 | 70% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | ReittierEsel01GiliadGesatteltRef.PositionWorld TeleportX, TeleportY, TeleportZ, 0, NehrimWorldspace | ReittierEsel01GiliadGesatteltRef.SetPosition(TeleportX.GetValue() as Int, TeleportY.GetValue() as Int, TeleportZ.GetValue() as Int) \| ReittierEsel01GiliadGesatteltRef.SetAngle(0.0, 0.0, 0) |
| `ReleaseWeatherOverride` | native | 22 | 1 | 3 | 42% | 12 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | ReleaseWeatherOverride | Weather.ReleaseOverride() |
| `GetDetected` | native | 22 | 0 | 4 | 88% | 12 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( BejeenRef.GetDetected Player == 0 && WeebamNaRef.GetDetected Player == 0 ) && DANocturnal.PlayerHere == 0 | If !(Player.IsDetectedBy((BejeenRef as Actor))) && !(Player.IsDetectedBy((WeebamNaRef as Actor))) && DANocturnal.PlayerHere == 0 |
| `SetScale` | native | 15 | 4 | 7 | 88% | 19 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SetScale TestScale | SetScale(TestScale) |
| `ResetHealth` | native | 10 | 16 | 0 | 65% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | player.resethealth | Game.GetPlayer().RestoreActorValue("Health", 9999) |
| `IsSwimming` | partial | 20 | 3 | 2 | 96% | 0 | 4 | 1 | 5 | 3 | 0 | 0 | 0 | 1 | if ( Player.IsSwimming == 1 ) | If (false)  ;NE: IsSwimming |
| `setquestitem` | dropped | 0 | 0 | 25 | 96% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | SetQuestItem 0 mwAVSlavesFreed | ;NE: SetQuestItem - no Papyrus equivalent (SetQuestItem 0, mwAVSlavesFreed) |
| `GetCurrentAIProcedure` | partial | 7 | 17 | 0 | 100% | 2 | 3 | 0 | 10 | 0 | 0 | 0 | 0 | 0 | if ( FalcarRef.GetCurrentAiProcedure != 0 ) | If (false)  ;NE: FalcarRef.GetCurrentAiProcedure |
| `getavmodf` | partial | 0 | 0 | 24 | 100% | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | if Player.GetAVModF mwAVSlavesFreed base == 0 | If false  ;NE: Player.GetAVModF - no Papyrus equivalent (Player.GetAVModF mwAVSlavesFreed, base) |
| `Autosave` | native | 7 | 16 | 0 | 9% | 15 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | autosave | Game.RequestAutoSave() |
| `SetClass` | dropped | 2 | 0 | 21 | 96% | 0 | 0 | 0 | 0 | 0 | 0 | 10 | 0 | 0 | SEFelasSarandasRef.setClass SEOrderPriestClass | ;NE: SEFelasSarandasRef.setClass |
| `Wait` | dropped | 20 | 2 | 0 | 9% | 0 | 0 | 0 | 0 | 0 | 0 | 10 | 0 | 0 | Wait TG06AmuseiFollowPCSneak | ;NE: Wait is a package instruction |
| `UnequipItem` | native | 11 | 7 | 4 | 45% | 19 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Player.UnEquipItem SE32SilverDaggerAbsMagicka 1 | Game.GetPlayer().UnequipItem(SE32SilverDaggerAbsMagicka, 1) |
| `emcplaytrack` | dropped | 0 | 22 | 0 | 50% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | emcPlayTrack "Data\Music\Special\Theme_01.mp3" | ;NE: TODO: emcPlayTrack "Data\Music\Special\Theme_01.mp3" |
| `PayFine` | polyfill | 20 | 0 | 1 | 0% | 4 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | 0 | player.PayFine | TES4Polyfill.CrimeFaction(TES4CrimeFactions).PlayerPayCrimeGold(true, true) |
| `GetPlayerInSEWorld` | polyfill | 16 | 0 | 5 | 43% | 0 | 0 | 13 | 0 | 0 | 0 | 0 | 0 | 0 | if ( GetPlayerInSEWorld == 0 ) | If (TES4Polyfill.CrimeRealm(TES4CrimeFactions) == 0) |
| `GetIsCurrentWeather` | native | 14 | 0 | 7 | 90% | 15 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | If GetIsCurrentWeather SE32GloomStorm == 0			; Safetey net. If for some reason the wrong weather is here, change it. | If !(Weather.GetCurrentWeather() == SE32GloomStorm)  ; Safetey net. If for some reason the wrong weather is here, change it. |
| `GetPCFactionSteal` | native | 12 | 0 | 9 | 90% | 16 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | If GetPCFactionSteal MagesGuild == 1 | If (MagesGuild.GetCrimeGoldNonViolent() > 0) as Int == 1 |
| `GetPCInfamy` | polyfill | 20 | 0 | 0 | 0% | 4 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | If Player.GetCrimeGold > 0 \|\| GetPCInfamy > GetPCFame | If TES4Polyfill.CrimeFaction(TES4CrimeFactions).GetCrimeGold() > 0 \|\| TES4Infamy.GetValueInt() > TES4Fame.GetValueInt() |
| `GetTalkedToPC` | partial | 20 | 0 | 0 | 100% | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | if ( Imusthedullref.GetTalkedtoPC == 1 ) | If (false)  ;NE: GetTalkedToPC |
| `GetCrimeKnown` | partial | 20 | 0 | 0 | 100% | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | If ( GetCrimeKnown 0 Player NivanDalviluRef == 1 ) \|\| ( GetCrimeKnown 0 Player HrolUlfgarRef == 1 ) | If False  ;NE: \|\| GetCrimeKnown GetCrimeKnown |
| `SetCellOwnership` | dropped | 16 | 1 | 3 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 12 | 0 | 0 | SetCellOwnership SENSDukesQuarters | ;NE: SetCellOwnership |
| `GetPCIsSex` | native | 1 | 19 | 0 | 90% | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( GetPCIsSex Male == 1 ) | If (Game.GetPlayer().GetActorBase().GetSex() == 0) |
| `GetIsSex` | native | 0 | 20 | 0 | 100% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( State == 2 )  && ( Player.GetIsSex Male == 1 ) | If myState == 2 && (Game.GetPlayer().GetActorBase().GetSex() == 0) |
| `getstringgamesetting` | dropped | 0 | 19 | 1 | 0% | 0 | 0 | 0 | 1 | 0 | 0 | 8 | 0 | 0 | Let arDayNameEval[0] := GetStringGameSetting sDaySundas	;=> Das sind die Default-Werte, die von | ;let arDayNameEval[0] := GetStringGameSetting sDaySundas  ;NE: OBSE array write, no Papyrus equivalent |
| `PreloadMagicEffect` | dropped | 0 | 0 | 20 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | PreloadMagicEffect CUDI | ;NE: PreloadMagicEffect - no Papyrus equivalent (PreloadMagicEffect CUDI) |
| `CompleteQuest` | native | 10 | 2 | 7 | 5% | 17 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | completequest ms91 | ms91.CompleteQuest() |
| `PlayBink` | dropped | 1 | 4 | 14 | 32% | 0 | 0 | 0 | 0 | 0 | 0 | 13 | 0 | 0 | playbink "OblivionOutro.bik" | ;NE: playbink |
| `SetCombatStyle` | dropped | 18 | 0 | 0 | 78% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | SetCombatStyle SE08Archer | ;NE: SetCombatStyle |
| `StopWaiting` | native | 18 | 0 | 0 | 0% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | StopWaiting MG01ErthorFollowPlayer | (akSpeakerRef as Actor).EvaluatePackage() |
| `IsWeaponOut` | native | 15 | 1 | 2 | 100% | 11 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | If IsWeaponOut == 0 | If !(IsWeaponDrawn()) |
| `GetIsRace` | native | 17 | 0 | 0 | 94% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( target.GetIsRace Argonian == 1 ) | If (target.GetRace() == Argonian) |
| `HasMagicEffect` | native | 6 | 0 | 11 | 88% | 14 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | If HasMagicEffect INVI == 0 && HasMagicEffect SLNC == 0 | If !(Self.HasMagicEffectWithKeyword(TES4FX_invi)) && !(Self.HasMagicEffectWithKeyword(TES4FX_slnc)) |
| `additemns` | native | 0 | 0 | 17 | 29% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Player.AddItemNS Gold001 Wealth | Game.GetPlayer().AddItem(Gold001, Wealth) |
| `setnumericgamesetting` | dropped | 0 | 0 | 17 | 53% | 2 | 0 | 0 | 0 | 0 | 0 | 6 | 0 | 0 | SetNumericGameSetting fActorStrengthEncumbranceMult oldencoumbrancesetting | ;TODO: SetNumericGameSetting fActorStrengthEncumbranceMult oldencoumbrancesetting.GetValue()  ;no vanilla Papyrus GMST writer and no actor-value equivalent (SKSE Game.SetGameSetting* would be needed) |
| `enablecontrol` | dropped | 0 | 0 | 17 | 24% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | EnableControl 5 | ;NE: EnableControl 5  ;OBSE input command, no Papyrus equivalent |
| `CloseCurrentOblivionGate` | polyfill | 16 | 0 | 0 | 12% | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | CloseCurrentOblivionGate | TES4Polyfill.CloseCurrentOblivionGate(TES4DestroyedRefs) |
| `IsOwner` | native | 15 | 0 | 1 | 50% | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if myActionRef.isOwner SE12OrderedGnarlFaction  == 1 | If (myActionRef.GetFactionOwner() == SE12OrderedGnarlFaction) |
| `MenuMode` | constant | 3 | 6 | 7 | 88% | 1 | 2 | 0 | 4 | 9 | 0 | 0 | 0 | 0 | if MenuMode == 0 | If 0 == 0 |
| `GetPCFactionAttack` | native | 6 | 0 | 9 | 87% | 14 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | If GetPCFactionAttack ThievesGuild == 1 \|\| GetPCFactionAttack ICWaterfrontResident == 1 | If (ThievesGuild.GetCrimeGoldViolent() > 0 && ThievesGuild.GetCrimeGoldViolent() < 1000) as Int == 1 \|\| (ICWaterfrontResident.GetCrimeGoldViolent() > 0 && ICWaterfrontResident.GetCrimeGoldViolent() <  |
| `WakeUpPC` | dropped | 5 | 2 | 8 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 15 | 0 | 0 | WakeUpPC | ;NE: WakeUpPC (no Skyrim equivalent; body runs in OnSleepStart) |
| `disablecontrol` | dropped | 0 | 0 | 15 | 13% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | DisableControl 5 | ;NE: DisableControl 5  ;OBSE input command, no Papyrus equivalent |
| `respawnhorse` | constant | 14 | 0 | 0 | 0% | 0 | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | Set HorsePCWhiteAnvilRef.RespawnHorse to 0 | HorsePCWhiteAnvilRef.RespawnHorse = 0 |
| `GetGameSetting` | native | 5 | 0 | 9 | 50% | 13 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | set temp to getgs iCrimeGoldAttack | Temp = Game.GetGameSettingInt("iCrimeGoldAttack") |
| `messageboxex` | native | 0 | 2 | 12 | 86% | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | MessageBoxEX  "Es ist %z Uhr am %z, %.0f %z 3E%g. Wie lange wollt Ihr warten?\|Eine halbe Stunde\|1 Stunde\|2 Stunden\|3 Stunden\|4 Stunden\|5 Stunden\|6 Stunden\|12 Stunden\|1 Tag\|Abbrechen",svTimeFormatted, svDayNameText, GameDay, svMonthNameText, GameYear | Debug.MessageBox("Es ist " + (svTimeFormatted as String) + " Uhr am " + (svDayNameText as String) + ", " + (GameDay.GetValue() as Int as String) + " " + (svMonthNameText as String) + " 3E" + (GameYear |
| `rand` | native | 0 | 0 | 14 | 64% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | set Timer to Rand 5 15 | Timer = Utility.RandomFloat(5, 15) |
| `removeitemns` | native | 0 | 0 | 14 | 43% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Player.RemoveItemNS 0GoldU001 Wealth | Game.GetPlayer().RemoveItem(d0GoldU001, Wealth) |
| `GetGlobalValue` | native | 0 | 0 | 14 | 43% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | set attackclaws to (4)*(GetGlobalValue fbmwbmclawcost) | attackclaws = (4 * fbmwbmclawcost.GetValue() as Int) as Int |
| `IsRaining` | inline | 12 | 1 | 0 | 100% | 3 | 4 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | If IsRaining == 1 && IsSwimming == 0 | If 0 == 1 && !(0) |
| `sv_destruct` | dropped | 0 | 3 | 10 | 54% | 0 | 0 | 0 | 0 | 0 | 0 | 11 | 0 | 0 | sv_destruct sTime | ;NE: sv_destruct - OBSE array/string command, no Papyrus equivalent (sv_destruct sTime) |
| `closeallmenus` | dropped | 0 | 2 | 11 | 77% | 0 | 0 | 0 | 0 | 0 | 0 | 10 | 0 | 0 | CloseAllMenus | ;  ;NE: CloseAllMenus - no Papyrus equivalent (CloseAllMenus ) |
| `GetSleeping` | native | 12 | 0 | 0 | 83% | 7 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 1 | If GetSleeping == 0 | If GetSleepState() == 0 |
| `GetPCIsRace` | native | 11 | 0 | 1 | 83% | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( GetPCisRace Argonian == 1 ) | If (Game.GetPlayer().GetRace() == Argonian) |
| `IsSneaking` | native | 10 | 0 | 2 | 33% | 10 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( player.issneaking == 1 ) | If (Game.GetPlayer().IsSneaking()) |
| `IsActor` | native | 3 | 8 | 1 | 0% | 9 | 1 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | if ( IsActor == 1 ) | If ((GetTargetActor() as Actor) != None) |
| `GetPlayerHasLastRiddenHorse` | partial | 0 | 12 | 0 | 0% | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | if ( GetPlayerHasLastRiddenHorse == 1 ) | If (false)  ;NE: GetPlayerHasLastRiddenHorse has no Skyrim equivalent |
| `con_runmemorypass` | dropped | 0 | 12 | 0 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | con_RunMemoryPass 0 | ;NE: TODO: con_RunMemoryPass 0 |
| `setavmodf` | dropped | 0 | 0 | 12 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | Player.SetAVModF mwAVSlavesFreed base FreedSlavesCounter | ;NE: Player.SetAVModF - no Papyrus equivalent (Player.SetAVModF mwAVSlavesFreed, base, FreedSlavesCounter) |
| `modav2` | native | 0 | 0 | 12 | 42% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Player.ModAV2 health 10000 | Game.GetPlayer().ModActorValue("Health", 10000) |
| `SetNoRumors` | dropped | 11 | 0 | 0 | 18% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | FarwilRef.SetNoRumors 0 | ;NE: FarwilRef.SetNoRumors |
| `GetPCFactionMurder` | native | 11 | 0 | 0 | 100% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( GetPCFactionMurder BlackwoodCompanyFaction == 1 ) | If (BlackwoodCompanyFACTION.GetCrimeGoldViolent() >= 1000) as Int == 1 |
| `IsPlayerInJail` | native | 9 | 0 | 2 | 91% | 9 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | if IsPlayerInJail == 1 | If Game.GetPlayer().IsArrested() |
| `ScriptEffectElapsedSeconds` | inline | 7 | 0 | 4 | 100% | 0 | 11 | 0 | 0 | 0 | 0 | 0 | 0 | 11 | set timer to timer + ScriptEffectElapsedSeconds | TES4_SecondsPassed = TES4_Now - TES4_LastTick \| If TES4_SecondsPassed < 0.0 \|\| TES4_SecondsPassed > 2.0 \| TES4_SecondsPassed = 0.25 \| TES4_LastTick = TES4_Now \| timer = timer + TES4_SecondsPassed |
| `PurgeCellBuffers` | dropped | 4 | 6 | 1 | 64% | 0 | 0 | 0 | 0 | 0 | 0 | 11 | 0 | 0 | pcb | ;NE: pcb |
| `IsRidingHorse` | native | 4 | 7 | 0 | 27% | 11 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( Player.IsInCombat == 0 ) && Player.IsRidingHorse == 0 | If !(Game.GetPlayer().IsInCombat()) && !(Game.GetPlayer().IsOnMount()) |
| `SetForceSneak` | dropped | 4 | 4 | 3 | 91% | 0 | 0 | 0 | 0 | 0 | 0 | 11 | 0 | 0 | setForceSneak 1 | ;NE: SetForceSneak |
| `equipitemsilent` | native | 0 | 0 | 11 | 9% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | ActorRef.EquipItemSilent fbmwWereBodyPC | ActorRef.EquipItem(fbmwWereBodyPC) |
| `ModAmountSoldStolen` | native | 10 | 0 | 0 | 0% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | ModAmountSoldStolen 200 | TES4GoldFenced.Mod(200 as Float) |
| `GoToJail` | polyfill | 7 | 1 | 2 | 0% | 0 | 0 | 10 | 0 | 0 | 0 | 0 | 0 | 0 | Player.GotoJail | TES4Polyfill.CrimeFaction(TES4CrimeFactions).SendPlayerToJail() |
| `PayFineThief` | polyfill | 1 | 0 | 9 | 0% | 0 | 0 | 9 | 0 | 0 | 0 | 0 | 0 | 0 | player.payfinethief | TES4Polyfill.CrimeFaction(TES4CrimeFactions).PlayerPayCrimeGold(false, false) |
| `emcmusicstop` | dropped | 0 | 10 | 0 | 70% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | emcMusicStop 2 1 | ;NE: TODO: emcMusicStop 2, 1 |
| `getgameloaded` | constant | 0 | 2 | 8 | 100% | 0 | 0 | 0 | 0 | 10 | 0 | 0 | 0 | 0 | if ( getGameLoaded )															; check key/button bindings | If (0)  ; check key/button bindings |
| `printtoconsole` | native | 0 | 2 | 8 | 40% | 9 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | printToConsole "attack button == %.0f" attackButton | Debug.Trace("attack button == " + (attackButton as String)) |
| `seteventhandler` | dropped | 0 | 0 | 10 | 70% | 0 | 0 | 0 | 2 | 0 | 0 | 6 | 0 | 0 | SetEventHandler "OnDeath" fbmwbmhandlesdeathbywerewolf "object"::Player | ;NE: SetEventHandler - OBSE event registration; Papyrus binds events by declaring them on the attached script (SetEventHandler "OnDeath", fbmwbmhandlesdeathbywerewolf, "object") |
| `SetActorFullName` | dropped | 7 | 2 | 0 | 44% | 0 | 0 | 0 | 0 | 0 | 0 | 9 | 0 | 0 | summon.SetActorFullName "Corrupted Clone" | ;NE: SetActorFullName |
| `GetContainer` | polyfill | 6 | 2 | 1 | 56% | 0 | 3 | 4 | 2 | 0 | 0 | 0 | 0 | 0 | if GetContainer == 0 | If !TES4Polyfill.IsInContainer(Self) |
| `IsTalking` | native | 1 | 0 | 8 | 100% | 9 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 9 | if ( GetStage MS11 >= 115 ) && ( GetStage MS11 < 120 ) && ( MS11.ForceGive == 1 ) && ( IsTalking == 0 ) | If MS11.GetStage() >= 115 && MS11.GetStage() < 120 && MS11.ForceGive == 1 && !(IsInDialogueWithPlayer()) |
| `iscasting` | native | 0 | 9 | 0 | 0% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( Player.IsCasting ) | If (Game.GetPlayer().GetAnimationVariableBool("bIsCastingRight") \|\| Game.GetPlayer().GetAnimationVariableBool("bIsCastingLeft")) |
| `addspellns` | native | 0 | 0 | 9 | 11% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | ActorRef.AddSpellNS 0werewolfSregeneration | ActorRef.AddSpell(d0werewolfSregeneration) |
| `update3d` | polyfill | 0 | 0 | 9 | 56% | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | 0 | ActorRef.Update3D | TES4Polyfill.Update3D(ActorRef as ObjectReference) |
| `ClearOwnership` | native | 8 | 0 | 0 | 0% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | clearownership | Self.SetActorOwner(Game.GetPlayer().GetActorBase()) |
| `IsActorUsingATorch` | native | 8 | 0 | 0 | 100% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( GetCombatTarget == Player ) && ( Player.IsActorUsingATorch == 1 ) | If GetCombatTarget() == Player && (Game.GetPlayer().GetEquippedItemType(0) == 11) |
| `SetCellFullName` | dropped | 7 | 1 | 0 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | SetCellFullName ICWaterfrontShackforSale "My Imperial City House" | ;NE: SetCellFullName |
| `con_save` | dropped | 0 | 7 | 1 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 8 | 0 | 0 | con_Save Autosave3							; Save Cell Change | ;NE: con_Save |
| `iskeypressed2` | partial | 0 | 3 | 5 | 50% | 0 | 0 | 0 | 8 | 0 | 0 | 0 | 0 | 0 | elseif ( isKeyPressed2 attackKey == 0 && isKeyPressed2 attackButton == 0 )	; player released attack | If (True)  ;NE: && isKeyPressed2 has no Papyrus equivalent (read as 0) isKeyPressed2 has no Papyrus equivalent (read as 0)  ; player released attack |
| `removespellns` | native | 0 | 0 | 8 | 38% | 8 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Target.removespellNS fbmwbmwerewolfbloodab | Target.RemoveSpell(fbmwbmwerewolfbloodab) |
| `SetDoorDefaultOpen` | native | 7 | 0 | 0 | 14% | 7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SE06DoorExteriorToRaptureRef.setDoorDefaultOpen 1 | SE06DoorExteriorToRaptureRef.SetOpen(true) |
| `SetPlayerInSEWorld` | polyfill | 4 | 0 | 3 | 0% | 0 | 0 | 7 | 0 | 0 | 0 | 0 | 0 | 0 | setPlayerInSEWorld 0 | TES4Polyfill.SetCrimeRealm(TES4CrimeFactions, 0) |
| `GetIsCreature` | polyfill | 4 | 1 | 2 | 0% | 0 | 0 | 7 | 0 | 0 | 0 | 0 | 0 | 0 | if ( DASkullofCorruption.spellworking == 0 ) && ( IsActor == 1 ) && ( IsGuard == 0 ) && ( GetDead == 0 ) && ( GetIsCreature == 0 ) && ( GetItemCount DASkullCorruption == 0 ) | If DASkullofCorruption.spellworking == 0 && ((GetTargetActor() as Actor) != None) && !(TES4Polyfill.IsGuard(GetTargetActor())) && !(GetTargetActor().IsDead()) && TES4Polyfill.GetIsCreature(GetTargetAc |
| `GetStartingAngle` | native | 3 | 0 | 4 | 57% | 7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | set fx to getstartingangle x | fx = Self.GetAngleX() |
| `GetWeaponAnimType` | native | 3 | 0 | 4 | 71% | 7 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( Player.GetWeaponAnimType == 0 ) | If (Game.GetPlayer().GetEquippedItemType(1) == 0) |
| `SetNoAvoidance` | dropped | 2 | 5 | 0 | 71% | 0 | 0 | 0 | 0 | 0 | 0 | 7 | 0 | 0 | SetNoAvoidance 1 | ;NE: SetNoAvoidance |
| `setattackdamage` | dropped | 0 | 0 | 7 | 86% | 0 | 0 | 0 | 0 | 0 | 0 | 7 | 0 | 0 | SetAttackDamage attackclaws fbmwbmwerewolfclawPC | ;NE: SetAttackDamage - no Papyrus equivalent (SetAttackDamage attackclaws, fbmwbmwerewolfclawPC) |
| `SetRigidBodyMass` | dropped | 6 | 0 | 0 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 0 | 0 | Target.setrigidbodymass 0 | ;NE: SetRigidBodyMass |
| `SetShowQuestItems` | dropped | 6 | 0 | 0 | 67% | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 0 | 0 | SetShowQuestItems 0 | ;NE: SetShowQuestItems |
| `IsCurrentFurnitureRef` | polyfill | 0 | 6 | 0 | 83% | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 | 0 | if (Player.IsCurrentFurnitureRef "MQ01BootPlayerStuhl" == 1 ) && ( reden == 0 ) | If TES4Polyfill.IsCurrentFurnitureRef(Game.GetPlayer(), MQ01BootPlayerStuhl) == 1 && reden == 0 |
| `hasspell` | native | 0 | 1 | 5 | 83% | 6 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( Player.HasSpell refRuneSpell != 1) | If (!(Game.GetPlayer().HasSpell(refRuneSpell))) |
| `getgamerestarted` | partial | 0 | 1 | 5 | 83% | 0 | 0 | 0 | 4 | 0 | 2 | 0 | 0 | 0 | if ( GetGameRestarted ) | If (0)  ;NE: GetGameRestarted has no Papyrus equivalent (read as 0) |
| `printc` | native | 0 | 0 | 6 | 67% | 5 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | printc "Rollback Save Game" | Debug.Trace("Rollback Save Game") |
| `setmodlocaldata` | dropped | 0 | 0 | 6 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 0 | 0 | SetModLocalData "LastModelPath"  modelpath | ;NE: SetModLocalData - no Papyrus equivalent (SetModLocalData "LastModelPath", modelpath) |
| `setmodelpath` | dropped | 0 | 0 | 6 | 17% | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 0 | 0 | ActorRef.SetModelPath "Characters\_male\skeletonWerewolf.nif" | ;NE: ActorRef.SetModelPath - no Papyrus equivalent (ActorRef.SetModelPath "Characters\_male\skeletonWerewolf.nif") |
| `ar_size` | partial | 0 | 0 | 6 | 33% | 0 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 | while i < ar_Size fbmwBMAAAImAWere.equippeditem | While i < 0  ;NE: ar_Size - OBSE array/string command, no Papyrus equivalent (ar_Size fbmwBMAAAImAWere.equippeditem) |
| `IsWaiting` | partial | 0 | 0 | 6 | 100% | 0 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 | if ChunaREF.GetCurrentAIPackage != 16 \|\| ChunaREF.GetSitting != 3 \|\| Player.GetInCell "BalmoraVSLuckySLockup" == 0 \|\| Player.IsWaiting == 1  \|\| IsPCSleeping == 1 | If ChunaREF.GetSitState() != 3 \|\| !(Game.GetPlayer().GetParentCell() == BalmoraVSLuckySLockup) \|\| Game.GetPlayer().GetSleepState() == 1  ;NE: \|\| ChunaREF.GetCurrentAIPackage  ;NE: \|\| Player.IsWaiting  |
| `setharvested` | dropped | 0 | 0 | 6 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 6 | 0 | 0 | mwRavenRockEbony1.SetHarvested 0 | ;NE: mwRavenRockEbony1.SetHarvested - no Papyrus equivalent (mwRavenRockEbony1.SetHarvested 0) |
| `getvelocity` | partial | 0 | 0 | 6 | 50% | 0 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 | set JDLevitationData.netVelX to (player.getVelocity X) | JDLevitationData.netVelX = 0  ;NE: player.getVelocity - no Papyrus equivalent (player.getVelocity X) |
| `fileexists` | partial | 0 | 0 | 6 | 0% | 0 | 0 | 0 | 6 | 0 | 0 | 0 | 0 | 0 | if FileExists "Data\Morrowind_ob - Meshes.bsa" == 0 | If 1 == 0  ;NE: FileExists - converted assets are deployed by the pipeline, not under the TES4 path |
| `GetStartingPos` | partial | 5 | 0 | 0 | 20% | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | set fx to getstartingpos x | fx = 0  ;NE: getstartingpos |
| `ShowDialogSubtitles` | dropped | 5 | 0 | 0 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 0 | showdialogsubtitles 0 | ;NE: showdialogsubtitles |
| `GetIsAlerted` | constant | 5 | 0 | 0 | 100% | 2 | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | if ( GetIsAlerted == 0 ) | If (0 == 0) |
| `SetAllReachable` | dropped | 3 | 2 | 0 | 80% | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 0 | SetAllReachable 0 | ;NE: SetAllReachable |
| `GetKnockedState` | native | 3 | 1 | 1 | 60% | 4 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | if RelmynaSayLength <= 0 && getKnockedState != 1 | If RelmynaSayLength <= 0 && !(IsBleedingOut()) |
| `CreateFullActorCopy` | native | 3 | 2 | 0 | 60% | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | set summon to target.CreateFullActorCopy | summon = target.PlaceAtMe(target.GetActorBase()) |
| `GetPlayerControlsDisabled` | native | 3 | 1 | 1 | 100% | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( GetPlayerControlsDisabled == 1 ) | If (TES4ControlsDisabled.GetValue() == 1) |
| `ShowBirthSignMenu` | polyfill | 2 | 1 | 2 | 80% | 0 | 0 | 3 | 0 | 0 | 0 | 2 | 0 | 0 | showbirthsignmenu | TES4_menuPartner1.EvaluatePackage()  ; re-greet now: TES4 kept the dialogue open under its menu \| Utility.Wait(0.5) \| TES4_menuPick1 = TES4Msg_ChargenBirthsign_01.Show() \| TES4_menuPick1 = 9 + TES4Msg |
| `GetPackageTarget` | partial | 0 | 5 | 0 | 100% | 0 | 0 | 0 | 5 | 0 | 0 | 0 | 0 | 0 | if ( KimFermaleRef.getCurrentAIPackage == 6 ) && ( KimFermaleRef.getPackageTarget == player ) | If ((KimFermaleRef as Actor).GetCurrentPackage() == MQ29KimFolgtPlayer \|\| (KimFermaleRef as Actor).GetCurrentPackage() == MQ29KimZumPlatz \|\| (KimFermaleRef as Actor).GetCurrentPackage() == MQ23KimGeht |
| `emcsetmusictype` | dropped | 0 | 5 | 0 | 60% | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 0 | emcSetMusicType 2 -1 ;Ends the Music override switch to next music | ;NE: TODO: emcSetMusicType 2, -1 |
| `emcsetbattleoverride` | dropped | 0 | 5 | 0 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 0 | emcSetBattleOverride 0 ; Combat Music Enabled | ;NE: TODO: emcSetBattleOverride 0 |
| `messageex` | native | 0 | 1 | 4 | 20% | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | MessageEx  "Warte bis um %z Uhr am %z, %.0f %z 3E%g", svTimeFormatted, svDayNameText, targetDay, svMonthNameText, targetYear | Debug.MessageBox("Warte bis um " + (svTimeFormatted as String) + " Uhr am " + (svDayNameText as String) + ", " + (targetDay as String) + " " + (svMonthNameText as String) + " 3E" + (targetYear as Stri |
| `setstringgamesettingex` | dropped | 0 | 0 | 5 | 40% | 0 | 0 | 0 | 0 | 0 | 0 | 5 | 0 | 0 | SetStringGameSettingEX "sPCControlsTextPrefix\|push" | ;NE: SetStringGameSettingEX - no Papyrus equivalent (SetStringGameSettingEX "sPCControlsTextPrefix\|push") |
| `print` | native | 0 | 0 | 5 | 60% | 5 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | print "Senies Lupinus ability (pseudo-disease) is removed" | Debug.Trace("Senies Lupinus ability (pseudo-disease) is removed") |
| `IsEssential` | native | 4 | 0 | 0 | 50% | 3 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | if GatekeeperRef.IsEssential == 0 | If !(GatekeeperRef.IsEssential()) |
| `offerhorse` | constant | 4 | 0 | 0 | 0% | 0 | 1 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | set PriorMaborelRef.offerHorse to 2 | PriorMaborelRef.offerHorse = 2 |
| `GetPCExpelled` | native | 3 | 0 | 1 | 75% | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( GetPCExpelled FightersGuild == 0 ) | If (FightersGuild.IsPlayerExpelled() == 0) |
| `ResetFallDamageTimer` | polyfill | 2 | 0 | 2 | 100% | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | ResetFallDamageTimer	;this should prevent the player from training a low level Gatekeeper over the rocks and falling to his death | TES4Polyfill.SuppressFallDamage(Self, TES4NoFallDamage)  ;this should prevent the player from training a low level Gatekeeper over the rocks and falling to his death |
| `IsInDangerousWater` | constant | 2 | 2 | 0 | 100% | 0 | 0 | 0 | 2 | 2 | 0 | 0 | 0 | 0 | if IsInDangerousWater == 1 | If 0 == 1 |
| `ShowClassMenu` | dropped | 2 | 0 | 2 | 50% | 0 | 0 | 2 | 0 | 0 | 0 | 2 | 0 | 0 | ShowClassMenu | ;NE: ShowClassMenu |
| `GetIgnoreFriendlyHits` | native | 0 | 4 | 0 | 0% | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if Target.GetIgnoreFriendlyHits == 0 | If !(Target.IsIgnoringFriendlyHits()) |
| `setnumericinisetting` | dropped | 0 | 4 | 0 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | SetNumericINISetting "bSaveOnWait:Gameplay" 0  					;Disable on wait autosave | ;NE: SetNumericINISetting "bSaveOnWait:Gameplay", 0  ;no Papyrus INI access |
| `foreach` | dropped | 0 | 2 | 2 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | forEach aIterator <- rCrosshairsCurrent.getItems | ;NE: forEach - OBSE array/string command, no Papyrus equivalent (forEach aIterator) |
| `setaltcontrol` | dropped | 0 | 0 | 4 | 50% | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | SetAltControl 18 OldControl | ;NE: SetAltControl - no Papyrus equivalent (SetAltControl 18, OldControl) |
| `getmodelpath` | partial | 0 | 0 | 4 | 100% | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | 0 | Let modelpath := Actor.GetModelPath | modelpath = 0  ;NE: Actor.GetModelPath - no Papyrus equivalent (Actor.GetModelPath ) |
| `togglefirstperson` | native | 0 | 0 | 4 | 50% | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | ToggleFirstPerson 0 | Game.ForceThirdPerson() |
| `modavmod` | dropped | 0 | 0 | 4 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 4 | 0 | 0 | modavmod health "max" modHealth | ;NE: modavmod - no Papyrus equivalent (modavmod health, "max", modHealth) |
| `starttimer` | constant | 0 | 0 | 4 | 0% | 0 | 0 | 0 | 0 | 4 | 0 | 0 | 0 | 0 | set fbmwmvpoorpilgrim.StartTimer to 2 | fbmwMVPoorPilgrim.StartTimer = 2 |
| `ModFactionRank` | native | 0 | 0 | 4 | 0% | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Player.modFactionRank 0blades, 1 | Game.GetPlayer().ModFactionRank(d0Blades, 1) |
| `IsIdlePlaying` | constant | 3 | 0 | 0 | 100% | 1 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | if isIdlePlaying == 0 | If 0 == 0 |
| `EnableFastTravel` | native | 3 | 0 | 0 | 0% | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | EnableFastTravel 0 | Game.EnableFastTravel(false) |
| `ForceCloseOblivionGate` | polyfill | 3 | 0 | 0 | 0% | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | ForceCloseOblivionGate | TES4Polyfill.CloseOblivionGate(Self, TES4DestroyedRefs) |
| `GetRestrained` | native | 3 | 0 | 0 | 100% | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( GetSitting == 3 ) && ( GetRestrained == 0 ) && ( doOnce == 0 ) | If GetSitState() == 3 && 0 == 0 && doOnce == 0 |
| `CloseOblivionGate` | polyfill | 3 | 0 | 0 | 0% | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | MQ13Gate1.CloseOblivionGate | TES4Polyfill.CloseOblivionGate(MQ13Gate1, TES4DestroyedRefs) |
| `SetInChargen` | dropped | 2 | 1 | 0 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 0 | setinchargen 0 | ;NE: SetInCharGen |
| `ForceFlee` | polyfill | 1 | 2 | 0 | 33% | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | forceflee ParadiseGrotto01, MQ15ResurrectPad3 | TES4Polyfill.FillPoolSlot(TES4ForceFlees, 0, 1, (akSpeakerRef as Actor)) |
| `getparentcell` | native | 0 | 3 | 0 | 100% | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Let rCurrentCell := Player.GetParentCell | rCurrentCell = Game.GetPlayer().GetParentCell() |
| `ar_construct` | partial | 0 | 2 | 1 | 33% | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | Let arDayNameEval := ar_Construct Array	;=> Wir initialisieren einen Array | arDayNameEval = 0  ;NE: ar_Construct - OBSE array/string command, no Papyrus equivalent (ar_Construct Array)  ;=> Wir initialisieren einen Array |
| `getcrosshairref` | partial | 0 | 1 | 2 | 100% | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | let rCrosshairsCurrent := getCrosshairRef | rCrosshairsCurrent = None  ;NE: getCrosshairRef has no Papyrus equivalent (read as None) |
| `getobjecttype` | partial | 0 | 1 | 2 | 33% | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | elseIf ( rCrosshairsCurrent.getObjectType == 23 \|\| (( rCrosshairsCurrent.getObjectType == 35 \|\| rCrosshairsCurrent.getObjectType == 36 ) && rCrosshairsCurrent.getDead )) && ( rCrosshairsCurrent.getDistance player <= 400 ) | If ((False) && rCrosshairsCurrent.IsDead()) && rCrosshairsCurrent.GetDistance(Player) <= 400  ;NE: \|\| rCrosshairsCurrent.getObjectType has no Papyrus equivalent (read as 0) rCrosshairsCurrent.getObjec |
| `isplugininstalled` | polyfill | 0 | 0 | 3 | 33% | 0 | 0 | 2 | 1 | 0 | 0 | 0 | 0 | 0 | if IsPluginInstalled "AddActorValues" == 0 | If TES4Polyfill.IsModLoaded("AddActorValues") == 0 |
| `getname` | partial | 0 | 0 | 3 | 33% | 0 | 0 | 0 | 3 | 0 | 0 | 0 | 0 | 0 | let playername := player.Getname | playername = 0  ;NE: player.Getname - no Papyrus equivalent (player.Getname ) |
| `GetPCIsClass` | native | 0 | 0 | 3 | 33% | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if (GetPCIsClass CharactergenClass == 0) | If (!(Game.GetPlayer().GetActorBase().GetClass() == CharactergenClass)) |
| `removeeventhandler` | dropped | 0 | 0 | 3 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 0 | RemoveEventHandler "OnDeath" fbmwbmhandlesdeathbywerewolf "object"::Player | ;NE: RemoveEventHandler - OBSE event registration; Papyrus binds events by declaring them on the attached script (RemoveEventHandler "OnDeath", fbmwbmhandlesdeathbywerewolf, "object") |
| `getlocalgravity` | inline | 0 | 0 | 3 | 0% | 0 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | set velX to JDLevitationData.netVelX - ((GetLocalGravity X) * secPassed) | velX = JDLevitationData.netVelX - 0.0 * secPassed |
| `runbatchscript` | dropped | 0 | 0 | 3 | 33% | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 0 | RunBatchScript "Data\ini\Morroblivion.ini" | ;  ;NE: RunBatchScript - OBSE console execution, no Papyrus equivalent (RunBatchScript "Data\ini\Morroblivion.ini") |
| `setstringinisetting` | dropped | 0 | 0 | 3 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 3 | 0 | 0 | SetStringIniSetting "SOblivionIntro:General\|mw_intro.bik" | ;NE: SetStringIniSetting - no Papyrus equivalent (SetStringIniSetting "SOblivionIntro:General\|mw_intro.bik") |
| `IsActorDetected` | partial | 2 | 0 | 0 | 100% | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | if player.IsActorDetected == 1 | If false  ;NE: IsActorDetected (no Skyrim equivalent) |
| `SendTrespassAlarm` | dropped | 2 | 0 | 0 | 50% | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | sendTrespassAlarm player | ;NE: sendTrespassAlarm |
| `IsTimePassing` | constant | 2 | 0 | 0 | 100% | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | if isTimePassing == 1 | If 0 == 1 |
| `bookread` | constant | 2 | 0 | 0 | 0% | 0 | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | set GinWulmRef.bookread to 1 | GinWulmRef.bookread = 1 |
| `SetPCFame` | native | 2 | 0 | 0 | 0% | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SetPCFame GFFame | TES4Fame.SetValueInt(GFFame as Int) |
| `SetPCInfamy` | native | 2 | 0 | 0 | 0% | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | SetPCInfamy GFInfamy | TES4Infamy.SetValueInt(GFInfamy as Int) |
| `SetPackDuration` | dropped | 2 | 0 | 0 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | setpackduration 3 | ;NE: setpackduration |
| `GetHeadingAngle` | native | 2 | 0 | 0 | 100% | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if player.getheadingangle MS45HorseRef2 < 45 && player.getheadingangle MS45HorseRef2 > -45 | If Game.GetPlayer().GetHeadingAngle(MS45HorseRef2) < 45 && Game.GetPlayer().GetHeadingAngle(MS45HorseRef2) > -45 |
| `SetLevel` | dropped | 2 | 0 | 0 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | AudensAvidiusRef.SetLevel 0 | ;NE: SetLevel |
| `SetActorRefraction` | polyfill | 1 | 0 | 1 | 100% | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | 0 | SetActorRefraction 1 | TES4Polyfill.SetActorRefraction(Self, 1) |
| `ShowSpellMaking` | dropped | 1 | 0 | 1 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | showSpellmaking | ;NE: showSpellmaking |
| `ShowEnchantment` | dropped | 1 | 0 | 1 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | showEnchantment | ;NE: showEnchantment |
| `ShowRaceMenu` | native | 1 | 0 | 1 | 100% | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | showracemenu | Game.ShowRaceMenu() |
| `Drop` | native | 1 | 0 | 1 | 100% | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Drop TG05MagesNote 1 | DropObject(TG05MagesNote, 1) |
| `SetInvestmentGold` | dropped | 1 | 0 | 1 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | SetInvestmentGold 500 | ;NE: SetInvestmentGold |
| `IsPlayerMovingIntoNewSpace` | partial | 0 | 2 | 0 | 50% | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | if ( DoOnce == 0 ) && ( IsPlayerMovingIntoNewSpace == 1 ) | If DoOnce == 0  ;NE: && IsPlayerMovingIntoNewSpace has no Papyrus equivalent (read as 0) |
| `SkipAnim` | dropped | 0 | 2 | 0 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | ErothinVorburgFallgitter01Ref.SkipAnim | ;NE: SkipAnim  ;no Papyrus equivalent |
| `GetFriendHit` | partial | 0 | 2 | 0 | 100% | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | if ( GetDistance Player <= 700 ) && ( GetStage mq04 >= 70 ) && ( KimFermaleRef.GetFriendHit Player < 4 ) && ( GetCombatTarget != player ) | If GetDistance(Player) <= 700 && mq04.GetStage() >= 70 && GetCombatTarget() != Player  ;NE: && KimFermaleRef.GetFriendHit has no Papyrus equivalent (read as 0) |
| `player` | native | 0 | 2 | 0 | 100% | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( GetDistance, Player <= Player 500 ) | If (GetDistance(Player) <= 500) |
| `emcsetmusichold` | dropped | 0 | 2 | 0 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | emcSetMusicHold 0 ; Playlist Enabled | ;NE: TODO: emcSetMusicHold 0 |
| `enablekey` | dropped | 0 | 2 | 0 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | enableKey attackButton | ;NE: enableKey attackButton  ;OBSE input command, no Papyrus equivalent |
| `isplayable2` | partial | 0 | 2 | 0 | 100% | 0 | 0 | 1 | 1 | 0 | 0 | 0 | 0 | 0 | let sTotalGold += getFullGoldValue rTemp * rCrosshairsCurrent.getItemCount rTemp * isPlayable2 rTemp | sTotalGold = sTotalGold + 0 * rCrosshairsCurrent.GetItemCount(rTemp) * TES4Polyfill.IsPlayable(rTemp)  ;NE: getFullGoldValue has no Papyrus equivalent (read as 0) |
| `setmenufloatvalue` | dropped | 0 | 2 | 0 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | setMenuFloatValue "%z\user1" svText 1005 sTotalGold | ;NE: setMenuFloatValue "%z\user1", svText, 1005, sTotalGold  ;OBSE menu query, no Papyrus equivalent |
| `getaltcontrol` | partial | 0 | 2 | 0 | 100% | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | set attackButton to getAltControl 4										; attack binding for mouse | attackButton = 0  ;NE: getAltControl has no Papyrus equivalent (read as 0)  ; attack binding for mouse |
| `getcontrol` | partial | 0 | 2 | 0 | 100% | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | set attackKey to getControl 4											; keyboard binding for attack | attackKey = 0  ;NE: getControl has no Papyrus equivalent (read as 0)  ; keyboard binding for attack |
| `disablekey` | dropped | 0 | 2 | 0 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | disableKey attackButton | ;NE: disableKey attackButton  ;OBSE input command, no Papyrus equivalent |
| `AdvancePCLevel` | native | 0 | 2 | 0 | 100% | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | AdvancePCLevel | Game.GetPlayer().ModActorValue("Level", 1) |
| `setname` | dropped | 0 | 0 | 2 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | SUMN.SetName $newname | ;NE: SUMN.SetName |
| `getformfrommod` | partial | 0 | 0 | 2 | 50% | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | set rLug to GetFormFromMod "Cobl Main.esm" 0016a3 | rLug = None  ;NE: GetFormFromMod - no Papyrus equivalent (GetFormFromMod "Cobl Main.esm", 0016a3) |
| `getaltcontrol2` | partial | 0 | 0 | 2 | 0% | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | set MorroDefaultQuest.OldControl to GetAltControl2 18 | MorroDefaultQuest.OldControl = 0  ;NE: GetAltControl2 - no Papyrus equivalent (GetAltControl2 18) |
| `getmodlocaldata` | partial | 0 | 0 | 2 | 100% | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | set lastActor to GetModLocalData "LastActor" | lastActor = None  ;NE: GetModLocalData - no Papyrus equivalent (GetModLocalData "LastActor") |
| `isdoor` | partial | 0 | 0 | 2 | 100% | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | if(crosshairRef.IsDoor == 1)&&(crosshairRef.GetLocked == 0);To avoid being blocked in an interior. | If !(crosshairRef.IsLocked())  ;NE: && TODO: crosshairRef.IsDoor  ;To avoid being blocked in an interior. |
| `getfirstref` | partial | 0 | 0 | 2 | 0% | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | Let potentialAttacker := GetFirstRef 35 | potentialAttacker = None  ;NE: GetFirstRef over form type 35 - Papyrus iterates actors only |
| `getnextref` | native | 0 | 0 | 2 | 0% | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Let potentiAlattacker := GetNextRef | potentiAlattacker = Game.FindRandomActorFromRef(Game.GetPlayer(), 4096.0) |
| `isthirdperson` | constant | 0 | 0 | 2 | 50% | 0 | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | if ( ActorRef.IsThirdPerson ) | If (False) |
| `getspells` | partial | 0 | 0 | 2 | 0% | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | Let spells := GetSpells | spells = 0  ;NE: GetSpells - no Papyrus equivalent (GetSpells ) |
| `setavmod` | dropped | 0 | 0 | 2 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | setavmod health "damage" 0 | ;NE: setavmod - no Papyrus equivalent (setavmod health, "damage", 0) |
| `getitems` | partial | 0 | 0 | 2 | 100% | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | let bagcontents := mwColonySack1REF.GetItems | bagcontents = 0  ;NE: mwColonySack1REF.GetItems has no Papyrus equivalent (read as 0) |
| `isonground` | partial | 0 | 0 | 2 | 100% | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | if actor.IsSwimming \|\| actor.GetDead \|\| Actor.IsOnGround | If 0 \|\| myActor.IsDead() \|\| !(myActor.IsFlying())  ;NE: IsSwimming |
| `equipitem2` | native | 0 | 0 | 2 | 100% | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | EquipItem2 fbmwBMringhircine | EquipItem(fbmwBMringhircine) |
| `getequippedobject` | native | 0 | 0 | 2 | 50% | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | set weapon to (Player.GetEquippedObject 16) | myWeapon = Game.GetPlayer().GetEquippedWeapon(16) |
| `setplayerskeletonpath` | dropped | 0 | 0 | 2 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | SetPlayerSkeletonPath "Morroblivion\MOtrapanims\skeleton.nif" | ;NE: SetPlayerSkeletonPath - no Papyrus equivalent (SetPlayerSkeletonPath "Morroblivion\MOtrapanims\skeleton.nif") |
| `sin` | native | 0 | 0 | 2 | 0% | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | set sinAngleX to sin angleX | sinAngleX = Math.sin(angleX) |
| `cos` | native | 0 | 0 | 2 | 0% | 2 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | set cosAngleX to cos angleX | cosAngleX = Math.cos(angleX) |
| `setvelocity` | dropped | 0 | 0 | 2 | 50% | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 0 | 0 | player.setVelocity velX velY velZ | ;NE: player.setVelocity - no Papyrus equivalent (player.setVelocity velX, velY, velZ) |
| `setcanfasttravelfromworld` | partial | 0 | 0 | 2 | 0% | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | SetCanFastTravelFromWorld WrldMorrowind 1 | Game.EnableFastTravel({b1})  ;NE: Skyrim fast travel is global, not per-worldspace |
| `getgodmode` | partial | 0 | 0 | 2 | 0% | 0 | 0 | 0 | 2 | 0 | 0 | 0 | 0 | 0 | if (GetGodMode == 0) | If (false)  ;NE: GetGodMode - no Papyrus equivalent (GetGodMode ) |
| `GetShouldAttack` | partial | 1 | 0 | 0 | 0% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | elseif ( self != player && self.GetDead != 1 && self.GetInFaction NoWabbaFaction  == 0 && ( self.getshouldattack player > 0 \|\| self.IsInCombat == 1 )) | If (mySelf != Player && !(mySelf.IsDead()) && !(mySelf.IsInFaction(NoWabbaFaction)) && (mySelf.IsInCombat()))  ;NE: \|\| self.getshouldattack player  (no Papyrus equivalent; 0 -- sibling IsInCombat term |
| `GetForceSneak` | native | 1 | 0 | 0 | 100% | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | elseif getForceSneak == 1 | If IsSneaking() |
| `StopMagicEffectVisuals` | native | 1 | 0 | 0 | 0% | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | sme frsh		; frost shield effect | effectFrostShield.Stop(GetTargetActor())  ; frost shield effect |
| `GetClothingValue` | partial | 1 | 0 | 0 | 100% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | if MS04.undressing == 1 && player.getClothingValue == 0 && ms04 < 55 && player.isweaponout == 0 | If MS04.undressing == 1 && False && !(Game.GetPlayer().IsWeaponDrawn())  ;NE: && player.getClothingValue   (clothing value not tracked in Skyrim; 0)  ;NE: Type mismatch fix (ms04 < 55) |
| `SetItemValue` | dropped | 1 | 0 | 0 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | SetItemValue 0 | ;NE: SetItemValue |
| `DeleteFullActorCopy` | native | 1 | 0 | 0 | 100% | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | summon.DeleteFullActorCopy | summon.Delete() |
| `IsGuard` | polyfill | 1 | 0 | 0 | 0% | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | if ( DASkullofCorruption.spellworking == 0 ) && ( IsActor == 1 ) && ( IsGuard == 0 ) && ( GetDead == 0 ) && ( GetIsCreature == 0 ) && ( GetItemCount DASkullCorruption == 0 ) | If DASkullofCorruption.spellworking == 0 && ((GetTargetActor() as Actor) != None) && !(TES4Polyfill.IsGuard(GetTargetActor())) && !(GetTargetActor().IsDead()) && TES4Polyfill.GetIsCreature(GetTargetAc |
| `GetIsPlayerBirthsign` | partial | 1 | 0 | 0 | 0% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | if GetisPlayerBirthsign BirthSignApprentice == 1 | If false  ;NE: GetisPlayerBirthsign |
| `GetIsPlayableRace` | polyfill | 1 | 0 | 0 | 0% | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | if actionRef.getiscreature == 0 && actionRef.getisplayablerace == 1 | If TES4Polyfill.GetIsCreature(actionRef) == 0 && true == 1 |
| `HasVampireFed` | polyfill | 1 | 0 | 0 | 100% | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | if ( Player.HasVampireFed == 1 ) | If (TES4Polyfill.HasVampireFed() == 1) |
| `GetArmorRating` | native | 1 | 0 | 0 | 0% | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if AdamusPhillidaRef.GetArmorRating == 0 | If (AdamusPhillidaRef as Actor).GetActorValue("DamageResist") == 0 |
| `IsPCAMurderer` | polyfill | 1 | 0 | 0 | 100% | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | if IsPCAMurderer == 1 | If (TES4Polyfill.CrimeFaction(TES4CrimeFactions).GetCrimeGoldViolent() >= 1000) |
| `emcmusicnexttrack` | dropped | 0 | 1 | 0 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | emcMusicNextTrack | ;NE: TODO: emcMusicNextTrack |
| `achievesunvar` | partial | 0 | 1 | 0 | 0% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | Set Achievements.AchieveSunVar to Achievements.AchieveSunVar 1 | Achievements.AchieveSunVar = 0  ;NE: TODO: Achievements.AchieveSunVar 1 |
| `emcismusiconhold` | partial | 0 | 1 | 0 | 0% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | if ( emcIsMusicOnHold == 1 ) | If (false)  ;NE: TODO: emcIsMusicOnHold |
| `GetAttacked` | native | 0 | 1 | 0 | 100% | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if ( GetAttacked == 1 ) | If (Self.IsAlarmed() as Int) == 1 |
| `getfullgoldvalue` | partial | 0 | 1 | 0 | 100% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | let sTotalGold += getFullGoldValue rTemp * rCrosshairsCurrent.getItemCount rTemp * isPlayable2 rTemp | sTotalGold = sTotalGold + 0 * rCrosshairsCurrent.GetItemCount(rTemp) * TES4Polyfill.IsPlayable(rTemp)  ;NE: getFullGoldValue has no Papyrus equivalent (read as 0) |
| `getmenuhastrait` | partial | 0 | 1 | 0 | 100% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | if getMenuHasTrait "iteminfo_rect\x" 1005 | If 0  ;NE: TODO: getMenuHasTrait "iteminfo_rect\x", 1005 |
| `setmenustringvalue` | dropped | 0 | 1 | 0 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | setMenuStringValue "%z\string\|%z" svText svGold 1005 | ;NE: setMenuStringValue "%z\string\|%z", svText, svGold, 1005  ;OBSE menu query, no Papyrus equivalent |
| `emcisbattleoverridden` | partial | 0 | 1 | 0 | 100% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | if ( emcIsBattleOverridden == 0 ) | If (false)  ;NE: TODO: emcIsBattleOverridden |
| `emcmusicresume` | dropped | 0 | 1 | 0 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | emcMusicResume 1 | ;NE: TODO: emcMusicResume 1 |
| `GetWeaponSkillType` | partial | 0 | 1 | 0 | 100% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | if ( player.getWeaponSkillType == 3 )								; player has bow equipped and out | If (false)  ;NE: player.getWeaponSkillType has no Papyrus equivalent (read as 0)  ; player has bow equipped and out |
| `isinair` | native | 0 | 1 | 0 | 100% | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | elseif ( Player.IsInAir ) | If (Game.GetPlayer().IsFlying() as Int) |
| `isunderwater` | partial | 0 | 0 | 1 | 100% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | if Player.IsUnderWater && (Player.GetAV health < 15 \|\| Player.HasMagicEffect WABR) | If 0 && (Game.GetPlayer().GetActorValue("Health") < 15 \|\| Game.GetPlayer().HasMagicEffectWithKeyword(TES4FX_wabr))  ;NE: Player.IsUnderWater - no Papyrus equivalent (Player.IsUnderWater ) |
| `SelectPlayerSpell` | dropped | 0 | 0 | 1 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | SelectPlayerSpell fbmwViperBoltSpell | ;NE: SelectPlayerSpell - no Papyrus equivalent (SelectPlayerSpell fbmwViperBoltSpell) |
| `unequipitemns` | native | 0 | 0 | 1 | 100% | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Actor.UnequipItemNS fbmwBMringhircine | myActor.UnequipItem(fbmwBMringhircine) |
| `getrace` | native | 0 | 0 | 1 | 0% | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | if(ActorRef.GetRace == Argonian \|\| ActorRef.GetRace == Khajiit) | If (ActorRef.GetRace() == Argonian \|\| ActorRef.GetRace() == Khajiit) |
| `equipitem2ns` | native | 0 | 0 | 1 | 0% | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Player.EquipItem2NS item | Game.GetPlayer().EquipItem(item) |
| `isdead` | constant | 0 | 0 | 1 | 0% | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | set fbmwMercCalvusQuest.isdead to 1 | fbmwMercCalvusQuest.isdead = 1 |
| `equipme` | dropped | 0 | 0 | 1 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | player.EquipMe | ;NE: player.EquipMe - no Papyrus equivalent (player.EquipMe ) |
| `LoopGroup` | native | 0 | 0 | 1 | 100% | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | fbmwTRSothaPowertubesREF.LoopGroup Idle, 6 ,1 | fbmwTRSothaPowertubesREF.PlayGamebryoAnimation(myIdle, 6, 1) |
| `setcellwaterheight` | dropped | 0 | 0 | 1 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | SetCellWaterHeight OldSMournholdXSTempleSSewersSWest 850 | ;NE: SetCellWaterHeight - no Papyrus equivalent (SetCellWaterHeight OldSMournholdXSTempleSSewersSWest, 850) |
| `GetVampire` | partial | 0 | 0 | 1 | 0% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | if Player.GetVampire == 1 | If false  ;NE: Player.GetVampire - no Papyrus equivalent (Player.GetVampire ) |
| `setcurrenthealth` | native | 0 | 0 | 1 | 100% | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | player.SetCurrentHealth VAhalfhealth | Game.GetPlayer().SetActorValue("Health", VAhalfhealth) |
| `sv_compare` | partial | 0 | 0 | 1 | 100% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | if(sv_compare "Characters\_male\skeletonWerewolf.nif" modelpath == 0) | If (false)  ;NE: sv_compare - OBSE array/string command, no Papyrus equivalent (sv_compare "Characters\_male\skeletonWerewolf.nif", modelpath) |
| `setpcamurderer` | dropped | 0 | 0 | 1 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | SetPCAMurderer 0 | ;NE: SetPCAMurderer - no Papyrus equivalent (SetPCAMurderer 0) |
| `modactorvalue2` | native | 0 | 0 | 1 | 0% | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | ModActorValue2 Health health_delta | ModActorValue("Health", health_delta) |
| `tapcontrol` | dropped | 0 | 0 | 1 | 0% | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | TapControl 8 | ;NE: TapControl 8  ;OBSE input command, no Papyrus equivalent |
| `setlowlevelprocessing` | dropped | 0 | 0 | 1 | 100% | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | SetLowLevelProcessing 1 | ;NE: SetLowLevelProcessing - no Papyrus equivalent (SetLowLevelProcessing 1) |
| `unequipitemsilent` | native | 0 | 0 | 1 | 100% | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Player.UnequipItemSilent XWeaponXREF | Game.GetPlayer().UnequipItem(XWeaponXREF) |
| `exp` | polyfill | 0 | 0 | 1 | 0% | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | set dampNorm to exp dampExp | dampNorm = TES4Polyfill.Exp(dampExp) |
| `getweapontype` | partial | 0 | 0 | 1 | 100% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | if ( Player.GetWeaponType == -1 ) | If (false)  ;NE: Player.GetWeaponType - no Papyrus equivalent (Player.GetWeaponType ) |
| `getnumfollowers` | partial | 0 | 0 | 1 | 0% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | set numFollowers to (Player.GetNumFollowers) | numFollowers = 0  ;NE: Player.GetNumFollowers - no Papyrus equivalent (Player.GetNumFollowers ) |
| `getnthfollower` | partial | 0 | 0 | 1 | 0% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | set follower to (Player.GetNthFollower index) | follower = None  ;NE: Player.GetNthFollower - no Papyrus equivalent (Player.GetNthFollower index) |
| `GetUnconscious` | polyfill | 0 | 0 | 1 | 0% | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | if NextActor != Player && NextActor.IsCreature == 0 && NextActor.GetDead == 0 && NextActor.GetIsGhost == 0 && NextActor.GetKnockedState == 0 && NextActor.GetUnconscious == 0 | If NextActor != Player && TES4Polyfill.GetIsCreature(NextActor) == 0 && !(NextActor.IsDead()) && !(NextActor.IsGhost()) && !(NextActor.IsBleedingOut()) && !(NextActor.IsUnconscious()) |
| `GetIsGhost` | polyfill | 0 | 0 | 1 | 0% | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | if NextActor != Player && NextActor.IsCreature == 0 && NextActor.GetDead == 0 && NextActor.GetIsGhost == 0 && NextActor.GetKnockedState == 0 && NextActor.GetUnconscious == 0 | If NextActor != Player && TES4Polyfill.GetIsCreature(NextActor) == 0 && !(NextActor.IsDead()) && !(NextActor.IsGhost()) && !(NextActor.IsBleedingOut()) && !(NextActor.IsUnconscious()) |
| `iscreature` | polyfill | 0 | 0 | 1 | 0% | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | if NextActor != Player && NextActor.IsCreature == 0 && NextActor.GetDead == 0 && NextActor.GetIsGhost == 0 && NextActor.GetKnockedState == 0 && NextActor.GetUnconscious == 0 | If NextActor != Player && TES4Polyfill.GetIsCreature(NextActor) == 0 && !(NextActor.IsDead()) && !(NextActor.IsGhost()) && !(NextActor.IsBleedingOut()) && !(NextActor.IsUnconscious()) |
| `getmodindex` | partial | 0 | 0 | 1 | 0% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | if GetModIndex "Morrowind_ob.esm" > 1 && IsPluginInstalled "EngineBugFixes" == 0 | If 1 > 1 && TES4Polyfill.IsModLoaded("EngineBugFixes") == 0  ;NE: GetModIndex - Papyrus cannot read load order |
| `uncompletequest` | native | 0 | 0 | 1 | 0% | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | UncompleteQuest fbmwEBBone | fbmwEBBone.Reset() |
| `getplayerbirthsign` | partial | 0 | 0 | 1 | 0% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | if GetPlayerBirthsign == 0 | If false  ;NE: GetPlayerBirthsign - no Papyrus equivalent (GetPlayerBirthsign ) |
| `getobseversion` | partial | 0 | 0 | 1 | 0% | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | set mwOBSECheck.Version to GetOBSEVersion | mwOBSECheck.Version = 0  ;NE: GetOBSEVersion - no Papyrus equivalent (GetOBSEVersion ) |
