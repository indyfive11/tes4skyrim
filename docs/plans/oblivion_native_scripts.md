# Oblivion scripts on the native interpreter
Status: PLAN

Run TES4 scripts (Oblivion, Nehrim, Morroblivion) inside MorrowindRuntime's
30 Hz tick on the vendored OpenMW interpreter, the way
[TES3 object scripts already run](morrowind_object_scripts.md). A script moves
off Papyrus once every command it calls is ported, so coverage grows one command
at a time.

**Code it would change:** `tes_runtime/morrowind/plugin/`, a new emitter beside
`script_convert/tes4/`, the sidecar staging in `tes5_import/dialogue/morrowind_sidecar.py`.

**Measured cost:** [oblivion_native_script_runtime.md](../audits/oblivion_native_script_runtime.md).

## <a id="decisions"></a>Fixed decisions

### It lives in MorrowindRuntime's DLL

OpenMW's interpreter is GPL-3.0, so whatever links it is GPL
([licensing](../commentary/morrowind_runtime.md#licensing)). MorrowindRuntime
is already that binary, and it already owns everything that does not care which
game wrote the script:

- the tick (`object_tick.cpp`), and its pause gate on the engine's menu count;
- the clock that stops while the game is paused (`GameSeconds`);
- the sweep that binds instances from the world, and the loaded/unloaded and
  death transitions;
- the Activate vtable hook, `RunOnGameThread`, and the `game_calls_*` engine
  calls.

A second GPL DLL would duplicate all of that, and would run a second tick over
the same references when Morroblivion and Oblivion load together.

### OpenMW's interpreter yes, its compiler no

`external/openmw/components/interpreter` is a generic stack machine (1,577
lines): int/float stack, `Return`, `SkipZero`/`SkipNonZero`, jumps,
local/global/member fetch and store, math, `MessageBox`. Its compiler only reads
MWScript. The front end is ours instead: `script_convert/tes4/` already parses
TES4 into a tree (`Script`, `Block`, `If`, `While`, `Assign`, `Call`,
`Member`, `Return`, `Label`/`Goto`, `SetFunctionValue`…). A Python emitter
walks that tree at convert time and writes an `Interpreter::Program` per block
(instructions, integers, floats, strings). So there is no second compiler and
no `SCDA` export.

What the interpreter lacks for TES4, added in the step that first needs it:

| Gap | TES4 form | Added in |
|---|---|---|
| A target held in a variable | `ref r` … `r.Enable` | [step 1](#s1) |
| Member access through a variable | `r.myVar`, `MQ01.var` | [step 1](#s1) |
| Several entry points per script | `Begin GameMode` / `Begin OnActivate` | [step 1](#s1) |
| Commands target a FormID, not a string id | MorrowindRuntime's explicit form pops a `RefId` string | [step 3](#s3) |
| Strings, arrays, `let`/`eval`, `call` with arguments and a return value | OBSE, Nehrim only | [step 9](#s9) |

### One opcode table

Opcode numbers come from one generated table that both the Python emitter and
the C++ install read (`tools/generators/`). The two sides can never disagree on
a number.

### <a id="variable-home"></a>A script's variables live in its Papyrus object

The converted Papyrus script stays, reduced to its variable declarations and the
event forwarders from [step 6](#s6). The interpreter reads and writes those
Papyrus variables directly.

- **6,001 conditions read script variables** in Oblivion.esm, measured from
  `Condition[].Raw` function ids 53 (`GetScriptVariable`) and 79
  (`GetQuestVariable`):

  | Record | 53 | 79 |
  |---|---:|---:|
  | INFO | 655 | 3,774 |
  | PACK | 363 | 779 |
  | QUST | 27 | 403 |

  Nehrim.esm has 138. These conditions run in Skyrim's engine, which reads
  Papyrus variables (`GetVMQuestVariable`/`GetVMScriptVariable`). With one
  home, they keep working unchanged.
- Papyrus-side scripts that read `Quest.var` keep working while their target
  is native.
- Saves already carry Papyrus variables, so switching a script to native loses
  nothing and needs no new co-save record for locals.

Step 2 checks that this access is safe. **If it is not,** the fallback is
MorrowindRuntime's own pattern: the DLL owns the locals (co-save) and copies each
changed value out to the Papyrus variable every tick (`PublishState`). The cost
is a write from Papyrus never reaching the DLL, which the fallback would have to
forbid.

### A script goes native when nothing in it is unported

The converter marks a script native when:

1. its tree has no `Raw` node;
2. every command it calls is ported;
3. every script it shares variables with qualifies too. Step 2 decides whether
   this rule is needed.

Otherwise it stays on Papyrus exactly as today. Each command ported moves scripts
across with no other change. The blast radius of a port is the list of scripts
it flips, which the converter prints.

### End users' saves

A save from a Papyrus build carries live `RegisterForSingleUpdate` polls. A
native script's Papyrus object keeps `OnUpdate` as an empty function, so an old
poll fires once and dies. No FormIDs move: no records are added, and only the
sidecar files are new.

## <a id="steps"></a>Steps

Each step ships on its own, is confirmed in game before the next, and leaves
every script it does not touch on Papyrus.

### <a id="s1"></a>1. Emit and run, headless

- A Python emitter from the `tes4` tree to `Program`, one per block:
  - arithmetic and comparison;
  - `If`/`ElseIf`/`Else`, `While`, `Label`/`Goto`, `Return`;
  - `short`/`long`/`float`/`ref` locals;
  - `quest.var` and `ref.var` members.
- New interpreter ops: push a local `ref` as a target, and fetch/store a member
  through a variable.
- Stage the programs in the sidecar.
- A headless C++ test like `script_test` that loads them and runs blocks against
  a stub context.
- **Measure:** how many SCPT bodies in Oblivion.esm, Nehrim.esm and
  Morrowind_ob.esm emit with no `Raw` node.
- **Ships:** nothing in game.

### <a id="s2"></a>2. The variable home

- Read and write a script's Papyrus variables from the game thread: the
  `BSScript::Object` variable array, layout from `references/skse64-master`,
  locked the way SKSE locks it.
- Check whether a write from native code is visible to a dialogue condition
  (`GetVMQuestVariable`) on the next evaluation.
- **In game:** set a quest variable from the console bridge through the native
  path and see an INFO gated on it appear. If the access is unsafe, switch to
  the [fallback](#variable-home) before going further.

### <a id="s3"></a>3. Quest scripts

The smallest real target: a quest script has no reference, no 3D, and no
instance to bind. TES4 runs its `GameMode` every `DATA.Delay` seconds (5 s by
default) while the quest is running.

- Run each running quest's `GameMode` program on the tick at its delay, using
  the paused-game clock.
- Commands target FormIDs. Reuse `game_calls_*` where the meaning matches; the
  audit's name match is an upper bound. Both runtimes' `Say` speak a topic, but
  TES4's also returns the line's length.
- First commands: `SetStage`, `GetStage`, `GetStageDone`, `StartQuest`,
  `StopQuest`, `GetSecondsPassed`, `GetGlobalValue` and global writes,
  `MessageBox`/`Message`, `GetDead`, `GetInCell`, `Enable`/`Disable`,
  `GetDistance`, `GetItemCount`.
- **Measure first:** the commands used by Oblivion's 265 `SCHR.Type=1`
  scripts, ranked; port in that order.
- **Deletes for native scripts:** the poll interval for quests, the
  `GetSecondsPassed` substitution.

### <a id="s4"></a>4. Result scripts

Stage results and INFO results run natively, so `SetStage` runs its stage
script inline before the next line, as TES4 did.

- Stage results run from the native `SetStage` itself.
- INFO results: the TIF End fragment calls one Papyrus native, `Run(info)`.
  This is the first native-function binding, so the binding mechanism gets built
  here. A mod event is not enough, because a reply's result must land before the
  menu re-evaluates the next topics.
- **Deletes:** `stage_latch.py`, and the stage race workarounds for native
  scripts.

### <a id="s5"></a>5. Object scripts on references

- Bind instances with MorrowindRuntime's sweep, changed to **Oblivion's
  rule**: a reference ticks while its cell is attached, disabled or not. The
  self-enable idiom depends on it. MorrowindRuntime binds only once 3D loads.
- Run `GameMode` every tick, and `MenuMode` while a menu holds the game.
- Motion just works: a per-tick `SetAngle` is a glide, exactly as `Move`/`Rotate`
  are in MorrowindRuntime.
- Scripts on carried items run while held, as TES4 did.
- **Deletes for native scripts:**
  - the OnCellAttach/OnLoad/OnInit arming and `ShouldRunGameMode`;
  - the relocation of bare self-reference calls;
  - `poll_motion.py`, `SpinAxis`/`TES4Track`;
  - the actor-poll dialogue gate.

### <a id="s6"></a>6. Events

- **`OnActivate`:** the Activate vtable hook MorrowindRuntime already has,
  because a TES4 `OnActivate` CLAIMS the activation unless the script calls
  `Activate` ([OnActivate claims](../commentary/morrowind_runtime.md#onactivate-claims-the-activation)).
- **`OnDeath`:** `PollDeath`.
- **The rest** (`OnHit`, `OnHitWith`, `OnTrigger*`, `OnPackageStart/Done/Change/End`,
  `OnEquip`/`OnUnequip`, `OnAdd`/`OnDrop`, `OnLoad`, `OnReset`, `OnAlarm`,
  `OnStartCombat`, `OnMagicEffectHit`, `ScriptEffect*`): the Papyrus object's
  event becomes a one-line forwarder to a native `Raise(event, args)`. Skyrim
  already raises these, so nothing new is hooked.
- **`OnTrigger` is per-frame in TES4:** OnTriggerEnter/Leave keep an "inside"
  set, and the tick runs the body while the set holds anything.
- **Measured block use:** Oblivion.esm 27 block types, Nehrim.esm 22
  ([audit](../audits/oblivion_native_script_runtime.md#block-types-event-hooks)).

### <a id="s7"></a>7. Say and SayTo

TES4's `Say` returns the line's length before the next line runs.

- The native `Say` returns the length from `say_durations.py`'s table.
- MorrowindRuntime's Say claim, on the paused-game clock, stops a pause from
  aging a line out
  ([the pause](../commentary/morrowind_runtime.md#the-tick-stops-while-the-game-is-paused)).
- **Deletes for native scripts:** `SayLine` and its seven actor-value slots,
  the adaptive tail, `PlayerIsInDialogue`.

### <a id="s8"></a>8. Command coverage

Port down the audit's ranking. The top 40 commands cover about 90% of unported
call sites in scripts with a ticked block. The rules carry over from the
Papyrus converter, so the decisions already made there are reused, not
re-derived. This applies to `SetFactionRank -1`, `StartCombat` bursts and
`SetDestroyed`. AI commands stay real packages on aliases:
[MorrowindRuntime reverted its own queue](../commentary/morrowind_runtime.md#ai-packages).
`evp` evaluates at once rather than posting the alias fill.

### <a id="s9"></a>9. OBSE, for Nehrim

Nehrim's 953 OBSE hits are mostly the expression language:

| Construct | Hits |
|---|---:|
| `call` | 474 |
| `eval` | 174 |
| `let` | 156 |
| `loop` | 21 |
| User `Function` blocks | 7 |

This step adds string and array values to the interpreter (its `Data` union is
int/float only), plus `call` with arguments and `SetFunctionValue`.
`references/xOBSE-master` is the source for each one's meaning.

### <a id="s10"></a>10. Morroblivion

`Morrowind_ob.esm`'s 950 scripts are TES4 scripts: 524 have a ticked block, and
they make 4,527 calls. They need nothing new beyond steps 1–9. This step
confirms that they share the tick with TES3 scripts when Morroblivion and the
authored Morrowind masters load together.

### <a id="s11"></a>11. Retire the Papyrus path

Once a whole plugin's scripts run native and are confirmed in game, delete the
Papyrus emission for the scripts they replace. After that, the remaining
Papyrus emitter serves only FO3/FNV, if those stay on Papyrus.

## <a id="risks"></a>Risks

- **The variable home** ([step 2](#s2)) is the one unproven mechanism; the
  fallback is known.
- **Event order:** an event forwarded from Papyrus runs when the VM delivers it,
  not inline. TES4 ran it at once. A body that depends on that order shows up in
  [step 6](#s6).
- **The DLL becomes required** for Oblivion and Nehrim quests, not only
  Morrowind's.
