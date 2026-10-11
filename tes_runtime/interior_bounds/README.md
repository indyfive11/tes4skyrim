# InteriorBounds

**Keeps the contents of a very large interior where its author put them.**
Useful with any interior that is bigger than Skyrim expects, or sits far from
its own origin, converted or not.

## What it does for you

Skyrim holds every object of an interior to a square that reaches 30,000 units
from the origin, east to west and north to south. When you enter the cell,
anything outside that square is put back on its start position or, failing
that, moved onto one default spot in the cell. Skyrim's own interiors all fit,
so the rule never shows.

Oblivion has no such rule. A converted cave or level that is larger than the
square, or was simply built far from the origin, arrives with hundreds of
walls, floors, traps and triggers stacked on one point: missing rooms, things
dying at the entrance, and a frame rate that collapses while every trigger in
the pile fires at once.

InteriorBounds leaves those objects where they were placed.

## Should I keep it enabled?

Only if you play a mod with such an interior. It changes no records and adds
nothing to your save; removing it puts the old rule back.

**It has to be active before you first enter such a cell.** Skyrim stores the
move in the save: once a cell has been entered without this plugin (or with
it in one of its two test modes), that save keeps those objects on the default
spot, and the plugin does not move them back. Use a save from before the
first visit.

### Settings

`Data\SKSE\Plugins\InteriorBounds.ini`, read once when the game starts:

- `iMode=0` — **look**: find the place in the game's code and log it. Changes
  nothing. This is what the plugin does when the ini is missing.
- `iMode=1` — **count**: also log how often the game moves such an object. The
  game still moves it.
- `iMode=2` — **keep** (as shipped): the objects stay where they were placed.

It needs only SKSE (not the Address Library). On a game build it does not
recognise it changes nothing and says why in its log.

---

## For developers

The rule lives in the function that adds a reference to an interior cell's
physics world: `|x| > 30000` or `|y| > 30000` (Z is not tested; lights, actors
and levelled spawns never reach the test) sends the reference to a block that
calls its set-position with the cell's default spot. The patch makes that one
block a jump to the function's exit, through a stub that counts the visit.

Two things about that block. It is the common end of both of the function's
tests — the 30,000 square, and a second one on the distance from the running
centre of the references accepted so far — so neither moves a reference to
the default spot any more. And it comes after the first stage, which puts a
reference back on its recorded start position when it has one: that stage is
left alone, and is not counted. The engine's own set-position marks the
reference as moved, which is why a move that has happened stays in the save.

**Finding it** ([locate.cpp](locate.cpp)) uses no address and no database:

1. Sections by flags: exactly one code section, and the read-only data ones.
2. The float `30000.0`, 4-byte aligned, exactly once in read-only data.
3. Every reference to it from code, by kind: exactly one `movss` load; any
   number of `mulss`; a compare against it in memory or a reference of an
   unknown kind refuses.
4. The function-table entry that holds the load: a whole function of length
   `0x363`, and the same one a second, code-free method names (the entry with
   the known unwind record and state map).
5. Fourteen self-checks inside that function: some 80 exact bytes around the
   load, the site and the exit; the four branches to the site agree on it; the
   jump before the site gives the exit; both set-position calls go to one
   function; exactly four near branches in the whole code section reach the
   site and none reaches the six bytes after its first; no short branch within
   reach lands there either.

Any miss is a refusal named in the log, with offsets and counts but no code
bytes. The search reads only ranges `VirtualQuery` reports as committed and
readable.

**Changing it** ([apply.cpp](apply.cpp), [detour.cpp](detour.cpp)): two fresh
pages are reserved within rel32 reach, outside the image. The first holds the
stub (`lock inc` of a counter, then either the site's two original
instructions and a jump back, or a jump to the exit) and ends execute-read;
the second holds the counter. The site's first seven bytes — two whole
instructions, no RIP-relative operand, flags dead across them — become a
`jmp` to the stub, written once at load, after the stub has been read back and
the site compared again. A thread logs the counter when it changes. The count
is per build of a cell's physics world, not per reference.

The look-only variant is compiled without `apply.cpp`; its import table has
no `VirtualProtect`, `VirtualAlloc`, `CreateThread` or
`FlushInstructionCache`, which `llvm-readobj --coff-imports` shows.

Shares no code with the other runtimes. Log:
`Documents\My Games\Skyrim Special Edition\SKSE\InteriorBounds.log`.

### Building and testing

`build.bat` → `..\dist\InteriorBounds.dll` (the plugin that ships) and
`build\look\InteriorBounds.dll` (look-only, without the write path) (MSVC, `/MT`). The same sources build on Linux with `clang-cl` and `lld-link`
against an [xwin](https://github.com/Jake-Shadle/xwin) copy of the CRT and SDK.

[test/locate_test.cpp](test/locate_test.cpp) compiles `locate.cpp` and
`detour.cpp` natively (`g++`, AddressSanitizer). Given a game executable it
prints the search's report, plants about 130 faults that must each be refused
for the right reason (and a few look-alikes that must not be), and executes
the stub against a mock of the site. [test/apply_test.cpp](test/apply_test.cpp) is the Windows half: it lays
an executable out in memory, runs the write path on that copy and checks every
byte and page protection it left behind.
