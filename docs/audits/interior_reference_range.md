# Interior references beyond ±30,000 units — 2026-10-11

What Skyrim does with an interior reference placed far from its cell's origin, what the converted games hold of
that kind, and what `InteriorBounds.dll` (`tes_runtime/interior_bounds/`) was seen to do about it in the game.
`docs/audits/ck_warnings.md` §10 lists the same references as a Creation Kit warning.

## The rule

When an interior cell is entered, the function that adds each reference to the cell's physics world tests the
reference's position: `|x| > 30000` or `|y| > 30000` sends it to a block that puts it on its start position or,
failing that, on one default spot of the cell. Z is not tested; lights, actors and levelled spawns never reach the
test. The move is written into the save: a save that entered the cell keeps those references where they were moved.

| Build | Function | The block | The limit |
|---|---|---|---|
| 1.6.1170 (the build run here) | `+0x2cbdc0` | `+0x2cc037`, function exit `+0x2cc0cf` | one float `30000.0` at `+0x17ab1d0`: 1 load, 5 `mulss` |
| 1.7.104 (read statically) | `+0x2d2150` | `+0x2d23c7`, function exit `+0x2d245f` | the same shape |

The plugin finds the block by that shape (one function, one load of the limit, 14 self-checks) and does nothing,
with the failed check named in its log, when anything differs.

## What the converted games hold

Counted by the importer's own census (`tes5_import/record_types/interior_range.py`, a warning at the end of the
prescans):

| Plugin | References beyond the square | Cells |
|---|---|---|
| Oblivion.esm | 38 | 12 |
| Knights.esp, DLCFrostcrag.esp | 0 | 0 |
| a third-party plugin built far from its cells' origins | 2,723 | 4 (2,681 · 39 · 2 · 1) |

## Seen in the game (1.6.1170, SKSE 2.2.8, one install, 2026-10-11)

Each line is one launch on a save that had never entered the cell; the plugin's log is
`Documents\My Games\Skyrim Special Edition\SKSE\InteriorBounds.log`.

| Mode | Cell | Plugin log | Game |
|---|---|---|---|
| look (`iMode=0`) | — | one site at `+0x2cc037`, 14 of 14 checks, nothing written | unchanged |
| count (`iMode=1`) | 1 reference beyond the square | `moves seen: 1` | as without the plugin (a second launch with the hook off gave the same script errors at the same rate) |
| keep (`iMode=2`) | the same cell | `moves suppressed: 1` | the save written there holds no stored move for the reference; saves made earlier without the plugin hold one |
| keep | 39 references | `moves suppressed: 39` | the level is laid out as authored |
| keep | 2,681 references | `moves suppressed: 2681` | loads in ordinary time and runs at a normal frame rate; the Papyrus log of the session has 782 lines |

Without the plugin the last cell had a long load and then ran at about one frame every two seconds, with 1.59
million Papyrus log lines in one session: its walls, floors, traps and triggers were stacked on one point.

## Limits and what is not verified

- It protects a save from BEFORE the first visit. A save that already entered such a cell keeps the stored moves;
  putting those references back is not done here.
- Other game builds than 1.6.1170 were not run. On 1.7.104 the locator finds its site in the file; whether the
  plugin loads there is untested.
- `tes_runtime/dist/InteriorBounds.dll` was built with `clang-cl` and `lld` (static runtime, KERNEL32 only);
  `tes_runtime/interior_bounds/build.bat` under MSVC has not been run.
- A cell reset was not tried. The function's second test (distance from the running centre of the references
  accepted so far) ends in the same block and is switched off with it; no cell here depended on it.
