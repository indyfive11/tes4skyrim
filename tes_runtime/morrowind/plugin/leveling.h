// Morrowind's attribute step on top of Skyrim's own level-up.
//
// Skyrim keeps the skills and decides when the player levels. This keeps
// what Morrowind adds: every skill increase is credited to its GOVERNING
// attribute (the SKIL record's), and each level the player gains is a pending
// step where three attributes rise by the multiplier those credits earned
// (iLevelUp01Mult..iLevelUp10Mult), as OpenMW's NpcStats does.
//
// Everything lives in DialogueState's variables, so it rides the co-save.
// See: docs/commentary/morrowind_runtime.md#leveling

#pragma once

#include <string>
#include <vector>

namespace tesruntime::mw {

// TES3's attribute count, and its three specializations.
constexpr int kAttributeCount = 8;
constexpr int kSpecializationCount = 3;

// Reads the player's skills and level once. A skill whose BASE rose is
// credited to its namesake Morrowind skill's governing attribute and
// specialization; a level gained queues one step. The first read of a game,
// and any read after the player's race changed, only records what it sees.
// The race and sex worn set the starting attributes, retroactively (RACE.txt).
// Game thread.
// See: docs/commentary/morrowind_runtime.md#race-attributes
void SampleLeveling();

// Level-ups whose attribute step has not been taken yet.
int PendingLevelUps();

// The level the next pending step belongs to.
int PendingStepLevel();

// Skill increases credited to an attribute or a specialization since the
// last step.
int AttributeIncreases(int attribute);
int SpecializationIncreases(int specialization);

// What choosing `attribute` adds at the step: iLevelUpNNMult for its
// increases (at most 10), 1 with none, and never past 100.
int AttributeGain(int attribute);

// Takes one step: each chosen BASE attribute rises by its gain (a Fortify is
// never counted, as in Morrowind), the pools picked since the last step earn
// their bonus, the credits reset, and one pending level-up is used.
void CompleteLevelUp(const std::vector<int>& attributes);

// The TES3 attribute governing a Skyrim skill by actor value index (6..23),
// through the same namesake skill the credits use; -1 for anything else.
int GoverningAttribute(int skyrimSkill);

// That skill's actor value name ("OneHanded"), or null.
const char* SkillName(int skyrimSkill);

// Whether the player's attributes have an authored start: a sidecar's player
// NPC_ row or a race start from RACE.txt. Without one they read 0, so the
// skill cap must not hold.
// See: docs/commentary/morrowind_runtime.md#skill-cap
bool PlayerAttributesKnown();

// One player attribute global's turn: `read` is what it holds now and
// `written` what was last written into it (NaN before the first write). A
// difference is a script's Set or Mod, and moves the player's BASE attribute
// by as much. Returns what the global should hold: the attribute as read.
// See: docs/commentary/morrowind_runtime.md#tes4-tables
float SettleAttributeGlobal(int attribute, float read, float written);

// The class menu's choice: its name, specialization (0..2) and two favored
// attributes (TES3 order, -1 for none). Each favored attribute starts 10
// higher, retroactively as a race's start does.
// See: docs/commentary/morrowind_runtime.md#chargen-menus
void ChooseClass(const std::string& name, int specialization, int first, int second);
void ChooseBirthsign(const std::string& name);

// What was chosen: the names lowercase ("" for none), the specialization,
// and a favored attribute (-1 for none).
std::string ChosenClass();
std::string ChosenBirthsign();
int ChosenSpecialization();
int FavoredAttribute(int slot);

// Forgets the race the last read saw, so a case starts from a first read.
void ResetLevelingForTest();

}  // namespace tesruntime::mw
