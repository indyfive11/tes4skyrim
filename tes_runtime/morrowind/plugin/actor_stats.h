// One actor's skill or attribute by its TES3 index, for callers that have no
// business including the interpreter.
//
// Separate from script_ops.h because that header pulls Interpreter::Runtime
// and Context, which the dialogue filter's actor does not have and does not
// need. Both are implemented in script_ops_stats.cpp beside the Get commands,
// so there is ONE stat read and one table.
// See: docs/commentary/morrowind_runtime.md#the-player-stats-are-real

#pragma once

#include <string>

namespace tesruntime::mw {

// `tes3Index` is 0..26 for a skill and 0..7 for an attribute -- the order
// NPC_.txt stores them in. Skyrim's actor value where the stat has one, else
// the DLL's own number over what the NPC_ record authored. An index outside
// its family answers 0.
//
// The PLAYER's attributes follow the character sheet: 100 while it is off
// (Personality is Speech), else the base moved by active magic.
// See: docs/commentary/morrowind_runtime.md#sheet-off
float ActorSkill(const std::string& actor, int tes3Index);
float ActorAttribute(const std::string& actor, int tes3Index);

// The Skyrim actor value names ActorSkill reads for a TES3 skill: the one it
// writes, then the second a split skill also reads; null where there is none.
void SkyrimSkillsOf(int tes3Index, const char** first, const char** second);

// An attribute before active magic moves it, which a level-up raises.
float ActorBaseAttribute(const std::string& actor, int tes3Index);

// Writes an attribute through the same store `SetStrength` and its kin write,
// so every reader above sees it. An index outside 0..7 does nothing.
void SetActorAttribute(const std::string& actor, int tes3Index, float value);

// What the actor's NPC_ record authors for an attribute, before any write.
float ActorAuthoredAttribute(const std::string& actor, int tes3Index);
// MorrowindRuntime.ini's [CharacterSheet] Enabled and SkillCap; both on until
// set. The cap only ever applies with the sheet on, and the sheet only turns
// it on once its menus and tick are running.
void SetSheetEnabled(bool on);
bool SheetEnabled();
void SetSkillCapEnabled(bool on);
bool SkillCapEnabled();

// Whether the cap holds the player's Skyrim skill (actor value 6..23): it is
// at or past its governing attribute. Always false with the cap off.
bool SkillCapped(int skill);

}  // namespace tesruntime::mw
