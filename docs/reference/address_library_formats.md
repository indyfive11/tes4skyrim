# Address Library database formats

**Code:** `tes_runtime/morrowind/plugin/addresses.cpp`,
`tes_runtime/common/addresses.cpp`, `game_bridge/plugin/addresses.cpp`,
`tools/disasm/address_lib.py`.

The Address Library maps SKSE **stable ids** to per-build RVAs, so one id table
serves every Skyrim build. Three on-disk formats exist, and the format number is
the first `i32` of the file. All are little-endian.

| Build | Filename | Format | Body |
|---|---|---|---|
| SE 1.5.x | `version-1-5-97-0.bin` | 1 | delta-coded |
| SE/AE 1.6.x | `versionlib-1-6-<build>-0.bin` | 2 | delta-coded |
| AE 1.7.x | `versionlib-1-7-<build>-0.bin` | 5 | flat `u32[]` |
| VR 1.4.15 | `version-1-4-15-0.csv` | CSV | `id,hex-rva` |

**The filename stem differs by era**: 1.5.x ships as `version-`, 1.6+ as
`versionlib-`. A loader that only tries `versionlib-` silently finds no database
on 1.5.97. The trailing number is the storefront (`0` Bethesda/Steam, `1` GOG,
`2` Epic), not a patch level; try the exact one, then fall back to `-0`.

## Formats 1 and 2 — delta-coded

Identical layout; format 1 is simply the older stamp. Header:

| Offset | Field |
|---|---|
| 0 | format (`1` or `2`) |
| 4 | version quad, 4 × `i32` |
| 20 | name length `i32`, then that many bytes (`"SkyrimSE.exe"`) |
| +0 | pointer size `i32` (8) |
| +4 | entry count `i32` |

Each entry is one control byte, low nibble for the id, high nibble for the
offset, both decoded against the previous entry's value:

| Kind | Meaning | Kind | Meaning |
|---|---|---|---|
| 0 | absolute `u64` | 4 | prev + `u16` |
| 1 | prev + 1 | 5 | prev − `u16` |
| 2 | prev + `u8` | 6 | absolute `u16` |
| 3 | prev − `u8` | 7 | absolute `u32` |

Kinds 6 and 7 are `u16`/`u32`. **Reading them as `u64` desyncs the whole
stream**, because every entry is delta-coded against the last.

Bit 3 of the high nibble (`0x80` in the control byte) is the pointer-size flag:
the *previous* offset is divided by `ptrSize` before the delta and the result
multiplied back after. It is not a plain "scale the delta" — that yields wrong
RVAs for the absolute kinds 0/6/7.

## Format 5 — flat array

1.7.x dropped delta coding entirely. The header also changed: the
length-prefixed name became a **fixed 64-byte NUL-padded field**, followed by
pointer size, a `u32` pad and the count.

| Offset | Field |
|---|---|
| 0 | format (`5`) |
| 4 | version quad, 4 × `i32` |
| 20 | name, fixed 64 bytes NUL-padded |
| 84 | pointer size `i32` (8) |
| 88 | pad `i32` (0) |
| 92 | count `u32` |
| 96 | `u32[count]` of RVAs, **indexed by stable id** |

So `rva = array[id]`, and `0` means the build does not cover that id. Id 0 and
an unmapped id are indistinguishable; both are treated as absent.

**Integrity check:** `filesize - 96 == count * 4` exactly. The delta formats'
equivalent is that the stream consumes the file to the last byte. Both are
mandatory — a partial parse yields plausible-but-wrong addresses, which are then
called as function pointers.

Measured (2026-09-20):

| Database | Format | Count |
|---|---|---|
| `version-1-5-97-0.bin` | 1 | 778,674 |
| `versionlib-1-6-659-0.bin` | 2 | 416,102 |
| `versionlib-1-6-1179-0.bin` | 2 | 428,510 |
| `versionlib-1-7-104-0.bin` | 5 | 565,759 (435,162 non-zero) |

## <a id="vr"></a>VR is not usable as an id source

VR 1.4.15 ships a CSV, and the ids we need are not in it. Measured against the
VR Address Library (Nexus 58101, 0.267.0): **14,284 ids**, versus 435,162 for
AE 1.7.104 — about 3% of the space.

Of `MorrowindRuntime`'s 91 ids and `TESRuntime`'s 44, **zero** appear in the VR
database. The gap is structural rather than incidental: the Papyrus-native
bands are almost entirely absent (`54000-55000`: 7 of 986; `55000-56000`: 24 of
998; `56000-57000`: 16 of 983), and those natives are nearly everything the
Morrowind runtime calls.

The VR ids that *do* exist share SE/AE numbering — 9,420 of 14,284 also appear
in the AE database — so a lookup looks valid while resolving nothing we need.
VR therefore resolves by **signature only**, as `CreatureRuntime`'s `ids.h` does with
`|`-separated prologue alternates.

## <a id="pre-ae-tables"></a>Pre-AE builds get a generated table

SE 1.5.97 and VR 1.4.15 are both frozen: neither will ship another exe. So
rather than translating ids at runtime, `tools/disasm/pre_ae_map.py` finds,
once, each AE id's function or global in each build and writes
`ids_pre_ae.h` beside the plugin's `ids.h`. On exactly one of those runtimes,
`VersionDb::LoadPreAe` loads that build's rows keyed by **AE id**, so no call
site changes. An id the tool could not prove is simply absent, which leaves
its feature unresolved and off, never pointed at a wrong function.

How a row is proven (1.6.1170 → 1.5.97, then 1.5.97 → VR):
- **papyrus:** the native's position among the callbacks its registration
  loads.
- **string:** a string literal only that function references, found in both
  builds.
- **vtable:** the RTTI class the vtable's object locator names, at the same
  locator offset.
- **bytes:** the function's first 16–28 bytes, with every 4-byte displacement
  and immediate masked, occur exactly once in each build.
- **slot:** the same slot of the vtable matched by its class.
- **vtref:** for a constructor or destructor, the other build's function
  referencing the matched vtable, picked by best similarity with a clear
  margin.
- **strsite:** the call or jump that follows the same string load, agreed by
  at least three sites (this finds `MenuManager::Register`).
- **caller:** the same call position in an already-matched caller that makes
  the same number of calls.
- **data:** a global at the same reference position in matched functions,
  agreed by up to three.

Every function match must also pass a shape check: size within 1.6×, and a
similar call count. Anchors that disagree drop the id. The report adds a
mnemonic similarity score as a second check.

**Only checked ids ship.** A matched id is only safe if every struct offset
used beside it is also right on that build, and offsets are not ids. So the
header holds just the ids the plugin's `pre_ae_ids.txt` lists, the ones whose
paths were checked per build. MorrowindRuntime's list covers the character
sheet, the perks menu's button and skill colors, the attribute globals, the
skill cap and attribute magic, with
`Actor.Get/SetFactionRank` (54686, 54750) for a TES4 NPC's attributes. The
two natives were matched by masked body bytes at similarity 1.00 (and
GetFactionRank by its registration string too); SetFactionRank's
registration was then read in both exes, and its callback is the address
matched (SE `0x94c9c0`, VR `0x986c30`). What those checks found:

| What | AE 1.6.1170 | SE 1.5.97 | VR 1.4.15 |
|---|---|---|---|
| Actor fields past `TESObjectREFR` (MagicTarget, actor state) | `0xa0`, `0xc0` | `-8` | `-8` (`ActorField`) |
| MagicTarget active-effect list slot, list at `+0x58` | slot 7 | same | same |
| ActiveEffect item `+0x48`, magnitude `+0x78`, flags `+0x7c`; item's base `+0x10` | — | same | same |
| TESGlobal value | `+0x34` | same | same |
| TESNPC sex flag | `+0x38` | same | same |
| MenuManager pause count | `+0x160` | same | same |
| IMenu base size; view, context, flags, depth at `+0x10/18/1c/20` | `0x30` | same | `0x40`: also sets `+0x30 = -1`, `+0x34 = 1`; MessageBox context 0xb, flags 0x40013 |
| IMenu virtuals | 9 | 9 | 11 (slots 9 and 10 added; ours are no-ops) |
| IMenu `ProcessMessage` / `NextFrame` / `Render` offsets (UIMessage, movie view slots) | — | same | same |
| `PlayerCharacter::AdvanceSkill` slot, and PlayerSkills | 247, `+0x9b8` | 247, `+0x9b0` | **249**, `+0x10b0` (the hook finds the slot and reads the load) |
| TrainingMenu skill | `+0x40` | `+0x40` | `+0x50` |
| Scaleform state-bag log | used | not checked; skipped | not checked; skipped |

### <a id="hand-proven"></a>Rows proven by hand

`pre_ae_map.HAND_PROVEN` holds the few rows that no anchor reaches. Each
row was read out of the disassembly:

| Id | SE 1.5.97 | VR 1.4.15 | Proof |
|---|---|---|---|
| `kTrainingMenuTrain` (52667) | `0x8ce8e0` | `0x8fb9c0` | It is the 4th call of its caller (52662, proven by vtable slot) in all three builds. On SE and VR it runs the same steps as AE's `0x96e710`: the session limit (player `+0x930` SE, `+0x1030` VR), the trainer's maximum, the gold check, taking the gold, then incrementing the menu's skill. The skill is at `+0x40` on SE and at `+0x50` on VR, where IMenu is 0x10 longer. AE compiles it differently, so the shape check alone rejects it |
| `kMenuManagerRegister` (82086) | `0xebf9c0` | `0xf1be20` | The jump that follows a menu-name string load (`MessageBoxMenu`, `Console`, `TweenMenu`, …) lands on one function in each build, at 14 of 14 such sites. On AE that function is `0xfa5480`, which `ids.h` records |
| `kControlMapAllowTextInput` (68552) | `0xc11f30` | `0xc4e8d0` | The one match in each build for AE's body (`0xcd5910`: raise or lower the byte at `rcx+0x128`) with the offset left free: SE `+0x120` (its SE id 67252 agrees), VR `+0x140`. Too small for the shape match |
| `kControlMapSingleton` (400863) | `0x2ec5bd0` | `0x2f8aaa0` | The global loaded into `rcx` before the calls to that function: 18 of its call sites on SE (SE id 514705 agrees), 20 on VR |
| `kMenuManagerIsMenuOpen` (82074) | `0xebe150` | `0xf1a3b0` | The sleep/wait toggle (AE `0x95e0d0`) calls it with the menu manager singleton in `rcx` and an interned menu name in `rdx`; the same instructions before it, `mov dword [rdi], 0x46` onward, occur twice in each build. SE and VR inline the menu-table lookup that AE calls (`0xfa7020`), with the same layout (entries `+0x150`, capacity `+0x134`), then test `byte [menu+0x1c], 0x40` as AE does |
| `kGfxReleaseManaged` (82270) | `0xecb0e0` | — | The perks menu's skill fill (AE `0x962450`, SE `0x8c20c0`) releases each value it overwrites: AE calls `0xfac750` 19 times and SE `0xecb0e0` 18, both as `rcx=[v]`, `rdx=v`, `r8=[v+0x10]`, and the two bodies are the same 155 bytes but for displacements. Its add-ref partner is called 7 times in each. VR is not needed: its perks menu has no skill labels |

## <a id="two-id-generations"></a>There are TWO id generations

A stable id is stable **within a generation**, not across the AE boundary. AE
inserted and removed functions, so the id space was regenerated; the SE-era
databases kept the old numbers. Both are correct, and they are different
dictionaries:

| Function | SE id (1.5.x) | AE id (1.6+) |
|---|---|---|
| `ObjectReference.GetPositionX` | 55649 | 56178 |
| `Actor.GetCurrentPackage` | 53872 | 54681 |
| `Game.AdvanceSkill` | 54817 | 55449 |
| `TESNPC` vtable | 241857 | 195816 |

`ids.h` holds AE ids. Looking one up in an SE database returns whichever SE-era
function carries that number — a real function, the wrong one. Measured with
`stable_id_check --identity --identity-version 1.5.97`, resolving each
`Native<>` id and comparing it against that script's own Papyrus registration:

| Build | Correct | Wrong |
|---|---|---|
| 1.7.104 (AE ids) | **62** | 0 |
| 1.5.97 (AE ids) | **0** | **62** |
| 1.5.97 (derived SE ids) | **62** | 0 |

The delta between the two generations is not a constant — it takes 26 distinct
values across 62 natives, because it counts the functions inserted before each
one. There is no shift to apply, so the mapping must be **derived per id**.

🛑 **Never look up an AE id in an SE database.** `VersionDb` selects the
generation from the runtime version; a wrong address is called as a function
pointer, which is worse than no address at all.

### <a id="deriving-the-se-ids"></a>Deriving the SE ids

The runtime never translates an AE id into an SE id. Each frozen pre-AE build
gets its own table of addresses keyed by AE id instead
([pre-AE tables](#pre-ae-tables)). That replaced `se_id_map.py`, which derived
SE ids, was never wired up, and was deleted.
