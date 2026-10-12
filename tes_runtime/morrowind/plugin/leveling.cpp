#include "leveling.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

#include "actor_stats.h"
#include "attribute_buffs.h"
#include "dialogue_state.h"
#include "log.h"
#include "script_tables.h"

namespace tesruntime::mw {

namespace {

// Where the progress is kept among DialogueState's variables.
constexpr const char* kOwner = "leveling|player";
constexpr const char* kLevelVar = "level";
constexpr const char* kPendingVar = "pending";

constexpr const char* kPlayer = "player";

// The attribute and increase caps OpenMW's LevelupDialog applies.
constexpr int kAttributeMax = 100;
constexpr int kMaxCountedIncreases = 10;

// Morrowind.esm's iLevelUp01Mult..10Mult, for a game whose sidecars stage
// none: every game levels by the same rule. Oblivion keeps the same values in
// its exe, not its ESM.
// See: docs/commentary/morrowind_runtime.md#tes4-tables
constexpr int kLevelUpMult[kMaxCountedIncreases + 1] = {1, 2, 2, 2, 2, 3, 3, 3, 4, 4, 5};

// A Skyrim skill, its actor value index (xEdit's TES5 enum, 6..23), and the
// Morrowind skill whose SKIL record governs it: its namesake, or for a folded
// skill the one it is named after (One- and Two-Handed are Long Blade,
// Morrowind's sword skill; Pickpocket is Sneak, which picked pockets in
// Morrowind).
// See: docs/commentary/morrowind_runtime.md#leveling
struct Tracked {
    const char* skyrim;
    int av;
    int tes3;
};

constexpr Tracked kTracked[] = {
    {"OneHanded", 6, 5},     {"TwoHanded", 7, 5},     {"Marksman", 8, 23},
    {"Block", 9, 0},         {"Smithing", 10, 1},     {"HeavyArmor", 11, 3},
    {"LightArmor", 12, 21},  {"Pickpocket", 13, 19},  {"Lockpicking", 14, 18},
    {"Sneak", 15, 19},       {"Alchemy", 16, 16},     {"Speechcraft", 17, 25},
    {"Alteration", 18, 11},  {"Conjuration", 19, 13}, {"Destruction", 20, 10},
    {"Illusion", 21, 12},    {"Restoration", 22, 15}, {"Enchanting", 23, 9},
};

// The race the last read saw. In memory only: a load restores the skills and
// the samples together, so treating the first read after one as a change
// costs nothing.
std::uint32_t g_race = 0;

std::string BaseKey(const char* skill) { return std::string("base.") + skill; }

// The starting attribute the player's base was last built on, so a new
// race or sex moves it by the difference and keeps every level-up gain.
std::string StartKey(int attribute) { return "start" + std::to_string(attribute); }

// The class and birthsign chosen: the favored attributes (index + 1, 0 for
// none), the specialization, and each name under `class:` / `sign:`.
// See: docs/commentary/morrowind_runtime.md#chargen-menus
constexpr const char* kChargenOwner = "chargen|player";
constexpr const char* kFavoredVars[] = {"fav0", "fav1"};
constexpr const char* kSpecializationVar = "spec";
constexpr const char* kClassPrefix = "class:";
// What the class bonus has added to each attribute so far.
std::string BonusKey(int attribute) { return "bonus" + std::to_string(attribute); }
constexpr const char* kSignPrefix = "sign:";

// OpenMW's MechanicsManager::buildPlayer: each favored attribute starts 10 higher.
constexpr int kFavoredBonus = 10;

std::string IncreaseKey(int attribute) {
    return "inc" + std::to_string(attribute);
}

std::string SpecializationKey(int specialization) {
    return "spec" + std::to_string(specialization);
}

int Counter(const std::string& name) {
    return static_cast<int>(State().Var(kOwner, name));
}

void AddTo(const std::string& name, int amount) {
    State().SetVar(kOwner, name, static_cast<float>(Counter(name) + amount));
}

void Credit(const Tracked& skill, int amount) {
    const SkillDef* def = FindSkill(skill.tes3);
    if (!def) return;
    if (def->attribute >= 0 && def->attribute < kAttributeCount) {
        AddTo(IncreaseKey(def->attribute), amount);
    }
    if (def->specialization >= 0 && def->specialization < kSpecializationCount) {
        AddTo(SpecializationKey(def->specialization), amount);
    }
}

// Records each skill's base, crediting what rose when `credit` is set.
void SampleSkills(bool credit) {
    for (const Tracked& skill : kTracked) {
        const int now = static_cast<int>(
            std::floor(Hooks().baseActorValue(kPlayer, skill.skyrim)));
        const std::string key = BaseKey(skill.skyrim);
        const bool known = State().HasVar(kOwner, key);
        const int rise = known ? now - Counter(key) : 0;
        if (credit && rise > 0) Credit(skill, rise);
        State().SetVar(kOwner, key, static_cast<float>(now));
    }
}

void SampleLevel(bool credit) {
    const int now = Hooks().playerLevel();
    const int seen = Counter(kLevelVar);
    if (credit && now > seen) {
        AddTo(kPendingVar, now - seen);
        Log("leveling: level %d -> %d, %d step(s) pending", seen, now,
            PendingLevelUps());
    }
    State().SetVar(kOwner, kLevelVar, static_cast<float>(now));
}

// The chosen class's favored attributes: TES3 indices, -1 for none.
int Favored(int slot) {
    return static_cast<int>(State().Var(kChargenOwner, kFavoredVars[slot])) - 1;
}

int ClassBonus(int attribute) {
    return Favored(0) == attribute || Favored(1) == attribute ? kFavoredBonus : 0;
}

// Moves a base attribute by `delta` and re-scores the picks it earned.
void MoveStart(int attribute, float delta) {
    const float base = ActorBaseAttribute(kPlayer, attribute);
    SetActorAttribute(kPlayer, attribute, std::max(0.0f, base + delta));
    RescorePickBuffs(attribute, delta);
}

// The race (and sex) now worn sets the starting attributes: each base moves
// by how far the new start is from the one it was built on -- before any, the
// player record's own -- and the picks it earned are re-scored. A race no
// sidecar describes changes nothing.
void ApplyRaceStart(std::uint32_t race) {
    const RaceDef* def = race ? FindRaceStart(race) : nullptr;
    if (!def) return;
    const bool female = Hooks().female && Hooks().female(kPlayer);
    const int* start = female ? def->female : def->male;
    for (int a = 0; a < kAttributeCount; ++a) {
        const std::string key = StartKey(a);
        const float was = State().HasVar(kOwner, key) ? State().Var(kOwner, key)
                                                      : ActorAuthoredAttribute(kPlayer, a);
        const float delta = static_cast<float>(start[a]) - was;
        State().SetVar(kOwner, key, static_cast<float>(start[a]));
        if (delta == 0.0f) continue;
        MoveStart(a, delta);
        Log("leveling: %s %s starts attribute %d at %d", def->id.c_str(),
            female ? "female" : "male", a, start[a]);
    }
}

// The chosen class's favored attributes stand 10 over the start, held apart
// from the race's so either can change alone, and re-scored the same way.
void ApplyClassBonus() {
    for (int a = 0; a < kAttributeCount; ++a) {
        const std::string key = BonusKey(a);
        const float delta = static_cast<float>(ClassBonus(a)) - State().Var(kChargenOwner, key);
        if (delta == 0.0f) continue;
        State().SetVar(kChargenOwner, key, static_cast<float>(ClassBonus(a)));
        MoveStart(a, delta);
        Log("leveling: the class moves attribute %d by %.0f", a, delta);
    }
}

}  // namespace

void SampleLeveling() {
    if (!Hooks().baseActorValue || !Hooks().playerLevel) return;
    const std::uint32_t race = Hooks().race ? Hooks().race(kPlayer) : 0;
    const bool raceChanged = race != g_race;
    g_race = race;
    ApplyRaceStart(race);
    ApplyClassBonus();
    const bool first = !State().HasVar(kOwner, kLevelVar);
    SampleSkills(!first && !raceChanged);
    SampleLevel(!first);
    if (first || raceChanged) RecordPools();
}

int GoverningAttribute(int skyrimSkill) {
    for (const Tracked& skill : kTracked) {
        if (skill.av != skyrimSkill) continue;
        const SkillDef* def = FindSkill(skill.tes3);
        return def ? def->attribute : -1;
    }
    return -1;
}

const char* SkillName(int skyrimSkill) {
    for (const Tracked& skill : kTracked) {
        if (skill.av == skyrimSkill) return skill.skyrim;
    }
    return nullptr;
}

bool PlayerAttributesKnown() {
    return FindActor(kPlayer) != nullptr || State().HasVar(kOwner, StartKey(0));
}

float SettleAttributeGlobal(int attribute, float read, float written) {
    if (!std::isnan(written) && read != written) {
        const float base = ActorBaseAttribute(kPlayer, attribute);
        SetActorAttribute(kPlayer, attribute, std::max(0.0f, base + read - written));
        Log("leveling: a script moved attribute %d by %g", attribute, read - written);
    }
    return ActorAttribute(kPlayer, attribute);
}

int PendingLevelUps() { return std::max(0, Counter(kPendingVar)); }

int PendingStepLevel() {
    return Counter(kLevelVar) - PendingLevelUps() + 1;
}

int AttributeIncreases(int attribute) {
    return Counter(IncreaseKey(attribute));
}

int SpecializationIncreases(int specialization) {
    return Counter(SpecializationKey(specialization));
}

int AttributeGain(int attribute) {
    const int current = static_cast<int>(ActorBaseAttribute(kPlayer, attribute));
    const int count = std::min(kMaxCountedIncreases, AttributeIncreases(attribute));
    int gain = 1;
    if (count > 0) {
        char name[16];
        std::snprintf(name, sizeof(name), "iLevelUp%02dMult", count);
        gain = static_cast<int>(GmstNumber(name, static_cast<float>(kLevelUpMult[count])));
    }
    return std::max(0, std::min(gain, kAttributeMax - current));
}

void CompleteLevelUp(const std::vector<int>& attributes) {
    for (int attribute : attributes) {
        const int gain = AttributeGain(attribute);
        const float now = ActorBaseAttribute(kPlayer, attribute);
        SetActorAttribute(kPlayer, attribute, now + static_cast<float>(gain));
        Log("leveling: attribute %d %.0f -> %.0f", attribute, now, now + gain);
    }
    CreditPoolPicks();
    for (int i = 0; i < kAttributeCount; ++i) {
        State().SetVar(kOwner, IncreaseKey(i), 0.0f);
    }
    for (int i = 0; i < kSpecializationCount; ++i) {
        State().SetVar(kOwner, SpecializationKey(i), 0.0f);
    }
    AddTo(kPendingVar, -1);
    if (Counter(kPendingVar) < 0) State().SetVar(kOwner, kPendingVar, 0.0f);
}

namespace {

// Records `name` under `prefix`, the one marked chosen.
void MarkChosen(const char* prefix, const std::string& name) {
    const std::string start(prefix);
    for (const std::string& held : State().VarNames(kChargenOwner)) {
        if (held.rfind(start, 0) == 0) State().SetVar(kChargenOwner, held, 0.0f);
    }
    State().SetVar(kChargenOwner, start + name, 1.0f);
}

std::string Chosen(const char* prefix) {
    const std::string start(prefix);
    for (const std::string& held : State().VarNames(kChargenOwner)) {
        if (held.rfind(start, 0) == 0 && State().Var(kChargenOwner, held) != 0.0f) {
            return held.substr(start.size());
        }
    }
    return std::string();
}

}  // namespace

void ChooseClass(const std::string& name, int specialization, int first, int second) {
    MarkChosen(kClassPrefix, name);
    State().className = name;
    State().SetVar(kChargenOwner, kSpecializationVar, static_cast<float>(specialization));
    const int favored[] = {first, second};
    for (int slot = 0; slot < 2; ++slot) {
        const bool valid = favored[slot] >= 0 && favored[slot] < kAttributeCount;
        State().SetVar(kChargenOwner, kFavoredVars[slot],
                       static_cast<float>(valid ? favored[slot] + 1 : 0));
    }
    Log("leveling: class %s, specialization %d, favored %d and %d", name.c_str(),
        specialization, first, second);
    ApplyClassBonus();
}

void ChooseBirthsign(const std::string& name) {
    MarkChosen(kSignPrefix, name);
    Log("leveling: birthsign %s", name.c_str());
}

// The chosen class as it was named; a save from before the name was kept
// answers the lowercased one.
std::string ChosenClass() {
    const std::string chosen = Chosen(kClassPrefix);
    const std::string& named = State().className;
    return !named.empty() && _stricmp(named.c_str(), chosen.c_str()) == 0 ? named : chosen;
}

std::string ChosenBirthsign() { return Chosen(kSignPrefix); }

int ChosenSpecialization() {
    return static_cast<int>(State().Var(kChargenOwner, kSpecializationVar));
}

int FavoredAttribute(int slot) { return slot >= 0 && slot < 2 ? Favored(slot) : -1; }

void ResetLevelingForTest() { g_race = 0; }

}  // namespace tesruntime::mw
