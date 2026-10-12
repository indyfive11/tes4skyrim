// Headless gate for leveling.cpp: skill increases credited to the governing
// attribute, level-ups queued, the step's multipliers and caps. The engine is
// three fake hooks; the SKIL and GMST rows are Morrowind.esm's, in testdata.
// See: docs/commentary/morrowind_runtime.md#leveling

#include <windows.h>

#include <cmath>
#include <cstdio>
#include <map>
#include <string>

#include "actor_stats.h"
#include "attribute_buffs.h"
#include "chargen_tables.h"
#include "dialogue_state.h"
#include "leveling.h"
#include "script_tables.h"

using namespace tesruntime::mw;

namespace {

int g_failures = 0;

void Check(bool ok, const char* what) {
    std::printf("  %s  %s\n", ok ? "ok  " : "FAIL", what);
    if (!ok) ++g_failures;
}

std::map<std::string, float> g_base;
int g_level = 1;
// Races the fixture's RACE.txt does not know, then Morrowind.esm's Dark Elf
// under its Skyrim race and that race's vampire.
constexpr std::uint32_t kRaceA = 1, kRaceB = 2;
constexpr std::uint32_t kDarkElf = 0x00013742, kDarkElfVampire = 0x0008883D;
std::uint32_t g_race = kRaceA;
bool g_female = false;

float FakeBase(const std::string&, const char* name) {
    const auto found = g_base.find(name);
    return found == g_base.end() ? 15.0f : found->second;
}

void FakeSet(const std::string&, const char* name, float value) { g_base[name] = value; }

float FakeValue(const std::string& actor, const char* name) { return FakeBase(actor, name); }

float g_magic[8] = {};
float FakeMagic(const std::string&, int attribute) { return g_magic[attribute]; }

// Whether the fake engine's base of `name` is `want`, read without adding it.
bool BaseIs(const char* name, float want) {
    const float base = FakeBase("player", name);
    return base > want - 0.01f && base < want + 0.01f;
}

int FakeLevel() { return g_level; }
std::uint32_t FakeRace(const std::string&) { return g_race; }
bool FakeFemale(const std::string&) { return g_female; }

std::string FixtureDir() {
    char exe[MAX_PATH] = {0};
    const DWORD n = GetModuleFileNameA(nullptr, exe, MAX_PATH);
    std::string path(exe, n);
    return path.substr(0, path.find_last_of('\\') + 1) + "testdata\\scripts\\";
}

// TES3 attribute indices, as SKIL rows name them.
constexpr int kStrength = 0, kIntelligence = 1, kWillpower = 2, kAgility = 3;
constexpr int kEndurance = 5, kPersonality = 6, kLuck = 7;
constexpr int kCombat = 0, kStealth = 2;

void CreditCases() {
    std::printf("the first read only records\n");
    SampleLeveling();
    Check(PendingLevelUps() == 0 && AttributeIncreases(kStrength) == 0,
          "nothing credited, nothing pending");

    std::printf("an increase goes to the namesake's governing attribute\n");
    g_base["OneHanded"] = 17.0f;
    g_base["Pickpocket"] = 16.0f;
    SampleLeveling();
    Check(AttributeIncreases(kStrength) == 2, "One-Handed +2 is Long Blade's Strength");
    Check(AttributeIncreases(kAgility) == 1, "Pickpocket +1 is Sneak's Agility");
    Check(SpecializationIncreases(kCombat) == 2 &&
              SpecializationIncreases(kStealth) == 1,
          "and their specializations");

    std::printf("a race change moves skills without crediting them\n");
    g_race = kRaceB;
    g_base["Destruction"] = 25.0f;
    SampleLeveling();
    Check(AttributeIncreases(kWillpower) == 0, "racial Destruction is not an increase");

    std::printf("a skill that drops is followed from its new value\n");
    g_base["OneHanded"] = 15.0f;
    SampleLeveling();
    g_base["OneHanded"] = 16.0f;
    SampleLeveling();
    Check(AttributeIncreases(kStrength) == 3, "the drop took nothing, the rise +1");
}

void StepCases() {
    std::printf("a level gained is a pending step\n");
    g_level = 3;
    SampleLeveling();
    Check(PendingLevelUps() == 2, "two levels, two steps");
    Check(PendingStepLevel() == 2, "the first step is for level 2");

    std::printf("the step's gains\n");
    SetActorAttribute("player", kStrength, 40.0f);
    SetActorAttribute("player", kAgility, 99.0f);
    SetActorAttribute("player", kLuck, 40.0f);
    Check(AttributeGain(kStrength) == 2, "3 increases: iLevelUp03Mult is 2");
    Check(AttributeGain(kAgility) == 1, "never past 100");
    Check(AttributeGain(kLuck) == 1, "no increases: 1");

    CompleteLevelUp({kStrength, kAgility, kLuck});
    Check(ActorAttribute("player", kStrength) == 42.0f, "Strength 40 -> 42");
    Check(ActorAttribute("player", kAgility) == 100.0f, "Agility 99 -> 100");
    Check(ActorAttribute("player", kLuck) == 41.0f, "Luck 40 -> 41");
    Check(AttributeIncreases(kStrength) == 0 && SpecializationIncreases(kCombat) == 0,
          "the credits reset");
    Check(PendingLevelUps() == 1 && PendingStepLevel() == 3,
          "one step left, for level 3");
}

// One Health pick at Endurance 100 and two Magicka picks at Intelligence 0.
void PickCases() {
    std::printf("the picks since the last step earn their bonus\n");
    SetActorAttribute("player", kEndurance, 100.0f);
    SetActorAttribute("player", kIntelligence, 0.0f);
    SetActorAttribute("player", kWillpower, 70.0f);
    g_base["Health"] = 15.0f + 10.0f;
    g_base["Magicka"] = 15.0f + 20.0f;
    CompleteLevelUp({kLuck});
    HoldAttributeBuffs();
    Check(BaseIs("Health", 30.0f), "a Health pick at Endurance 100 gives 15");
    Check(BaseIs("Magicka", 25.0f), "two Magicka picks at Intelligence 0 give 5 each");
    Check(BaseIs("Stamina", 15.0f) && BaseIs("CarryWeight", 15.0f),
          "no Stamina pick, no Stamina or carry weight");
    Check(BaseIs("MagickaRateMult", 35.0f), "Willpower 70: magicka regen +20%");
    Check(BaseIs("CritChance", 14.2f), "Luck 42: critical chance -0.8");

    std::printf("a held buff moves by its change, never twice\n");
    HoldAttributeBuffs();
    Check(BaseIs("Health", 30.0f) && BaseIs("MagickaRateMult", 35.0f),
          "holding again changes nothing");
    CompleteLevelUp({kLuck});
    HoldAttributeBuffs();
    Check(BaseIs("Health", 30.0f), "the buff's own points are not a pick");
}

void MagicCases() {
    std::printf("active magic moves the attribute and its buffs while it lasts\n");
    Hooks().attributeEffect = FakeMagic;
    g_magic[kEndurance] = -20.0f;
    g_magic[kWillpower] = 10.0f;
    Check(ActorAttribute("player", kEndurance) == 80.0f &&
              ActorBaseAttribute("player", kEndurance) == 100.0f,
          "Drain Endurance 20: reads 80, base stays 100");
    HoldAttributeBuffs();
    Check(BaseIs("Health", 28.0f), "1 Health pick: 20 drained is -2 Health");
    Check(BaseIs("MagickaRateMult", 45.0f), "Fortify Willpower 10: +10% regen");
    g_magic[kEndurance] = 0.0f;
    g_magic[kWillpower] = 0.0f;
    HoldAttributeBuffs();
    Check(BaseIs("Health", 30.0f) && BaseIs("MagickaRateMult", 35.0f),
          "and gives it back when it ends");
}

void SheetOffCases() {
    std::printf("with the sheet off every buff is handed back\n");
    SetSheetEnabled(false);
    HoldAttributeBuffs();
    Check(BaseIs("Health", 25.0f) && BaseIs("Magicka", 35.0f) &&
              BaseIs("MagickaRateMult", 15.0f) && BaseIs("CritChance", 15.0f),
          "Health, Magicka, regen and critical chance back to vanilla");
    Check(ActorAttribute("player", kStrength) == 100.0f, "Strength reads 100");
    g_base["Speechcraft"] = 37.0f;
    Check(ActorAttribute("player", kPersonality) == 37.0f, "Personality reads Speech");
    Check(!SkillCapEnabled(), "the skill cap is off with it");
    Check(PlayerAttributesKnown(), "the fixture's player row gives the attributes a start");
    Check(SettleAttributeGlobal(kStrength, 55.0f, std::nanf("")) == 100.0f,
          "a TES4 script's global reads 100 with the sheet off");
    SetSheetEnabled(true);
    HoldAttributeBuffs();
    Check(BaseIs("Health", 30.0f), "and back on, the earned bonus returns");
}

// After PickCases the player has one Health pick, holding 30 Health, with
// Endurance at 100 over an authored start the fixture leaves at 0.
void RaceCases() {
    std::printf("the race worn sets the starting attributes, earned picks re-scored\n");
    const float authored = ActorAuthoredAttribute("player", kEndurance);
    const float before = ActorBaseAttribute("player", kEndurance);
    g_race = kDarkElf;
    SampleLeveling();
    HoldAttributeBuffs();
    Check(ActorBaseAttribute("player", kEndurance) == before + 40.0f - authored,
          "Endurance moves to a Dark Elf man's 40, gains kept");
    Check(BaseIs("Health", 30.0f + (40.0f - authored) / 10.0f),
          "the Health pick earns as if Endurance had always been there");
    std::printf("a vampire of the race starts where the race does\n");
    g_race = kDarkElfVampire;
    SampleLeveling();
    HoldAttributeBuffs();
    Check(ActorBaseAttribute("player", kEndurance) == before + 40.0f - authored,
          "nothing moves");
    std::printf("the other sex's start moves it again\n");
    g_female = true;
    SampleLeveling();
    HoldAttributeBuffs();
    Check(ActorBaseAttribute("player", kEndurance) == before + 30.0f - authored,
          "a Dark Elf woman's Endurance is 30");
    Check(BaseIs("Health", 30.0f + (30.0f - authored) / 10.0f), "and the pick follows");
    std::printf("a race no table knows changes nothing\n");
    g_race = kRaceA;
    SampleLeveling();
    Check(ActorBaseAttribute("player", kEndurance) == before + 30.0f - authored,
          "the start stays the last known race's");
}

// After RaceCases the player wears a race no table knows, with one Health pick.
void ClassCases() {
    std::printf("a class's favored attributes start 10 higher, the picks re-scored\n");
    const float strength = ActorBaseAttribute("player", kStrength);
    const float endurance = ActorBaseAttribute("player", kEndurance);
    const float health = FakeBase("player", "Health");
    ChooseClass("Barbarian", kCombat, kStrength, kEndurance);
    HoldAttributeBuffs();
    Check(ActorBaseAttribute("player", kStrength) == strength + 10.0f &&
              ActorBaseAttribute("player", kEndurance) == endurance + 10.0f,
          "Strength and Endurance +10");
    Check(BaseIs("Health", health + 1.0f), "the Health pick earns Endurance's 10 more: +1");
    Check(ChosenClass() == "Barbarian" && ChosenSpecialization() == kCombat &&
              FavoredAttribute(1) == kEndurance,
          "the choice is kept, as it was named");
    std::printf("another class moves only what differs; a race change keeps it\n");
    ChooseClass("Mage", 1, kIntelligence, kWillpower);
    SampleLeveling();
    HoldAttributeBuffs();
    Check(ActorBaseAttribute("player", kEndurance) == endurance &&
              ActorBaseAttribute("player", kStrength) == strength,
          "Strength and Endurance back");
    Check(BaseIs("Health", health), "and the pick with them");
    Check(ChosenClass() == "Mage", "the last class is the one chosen");
    std::printf("a custom class keeps its capitals through a save\n");
    ChooseClass("SpellBlade McKay", 1, kIntelligence, kStrength);
    const std::string saved = State().Serialize();
    State().Deserialize(saved);
    Check(ChosenClass() == "SpellBlade McKay", "the name as typed comes back");
    State().className.clear();
    Check(ChosenClass() == "spellblade mckay", "a save from before answers it lowercased");
    std::printf("a chargen row parses\n");
    const ClassRow row = ParseClassRow("Battle mage|1|0,1|Wizard\\nwarriors");
    Check(row.name == "Battle mage" && row.specialization == 1 && row.favored[1] == 1 &&
              row.description == "Wizard\nwarriors",
          "class: name, specialization, favored, description");
    const SignRow sign = ParseSignRow(
        "The Lady|lady|Charge|Lady's Favor;Lady's Grace|a;b|"
        "h~~Abilities:;s~~Lady's Favor;e~icons_s_fortify~Fortify Personality 25 pts");
    Check(sign.image == "lady" && sign.spells.size() == 2 && sign.spellIds[1] == "b",
          "sign: picture, spells, ids");
    Check(sign.lines.size() == 3 && sign.lines[0].kind == 'h' && sign.lines[0].icon.empty() &&
              sign.lines[2].icon == "icons_s_fortify" &&
              sign.lines[2].text == "Fortify Personality 25 pts",
          "sign: its spell list's lines, each effect with its icon");
    Check(IndexOfName({"Agent", "Mage"}, "mAGE") == 1 && IndexOfName({"Agent"}, "Bard") == -1,
          "a pick is the asking plugin's index of its name, or none");
    std::printf("a statistics page row parses\n");
    const PageStatRow bank = ParsePageRow("Bank balance|Nehrim.esm|0001A2B3");
    Check(bank.label == "Bank balance" && bank.global.plugin == "Nehrim.esm" && bank.rules.empty(),
          "a mirrored variable: its global");
    const PageStatRow rate =
        ParsePageRow("Interest|Nehrim.esm@00000101,20,2;Nehrim.esm@00000102,70,1|3");
    Check(rate.rules.size() == 2 && rate.rules[1].quest.plugin == "Nehrim.esm" &&
              rate.rules[1].stage == 70 && rate.rules[1].value == 1.0f && rate.otherwise == 3.0f,
          "a stage rule: each quest, stage and value, then the otherwise");
}

void GoverningCases() {
    std::printf("each Skyrim skill's governing attribute\n");
    Check(GoverningAttribute(6) == kStrength, "One-Handed: Long Blade's Strength");
    Check(GoverningAttribute(17) == kPersonality, "Speech: Speechcraft's Personality");
    Check(GoverningAttribute(24) == -1 && SkillName(24) == nullptr, "Health is no skill");}

void GlobalCases() {
    std::printf("a TES4 script's attribute global follows the player both ways\n");
    const float base = ActorBaseAttribute("player", kLuck);
    const float now = SettleAttributeGlobal(kLuck, 100.0f, std::nanf(""));
    Check(now == ActorAttribute("player", kLuck) && ActorBaseAttribute("player", kLuck) == base,
          "the first sight writes the attribute and moves nothing");
    const float modded = SettleAttributeGlobal(kLuck, now + 1.0f, now);
    Check(ActorBaseAttribute("player", kLuck) == base + 1.0f && modded == now + 1.0f,
          "player.modav Luck 1 raises the base by 1");
    Check(SettleAttributeGlobal(kLuck, modded, modded) == modded, "an unmoved global moves nothing");
}

}  // namespace

int main() {
    ClearScriptTables();
    LoadScriptTables(FixtureDir());
    Hooks().baseActorValue = FakeBase;
    Hooks().setActorValue = FakeSet;
    Hooks().actorValue = FakeValue;
    Hooks().playerLevel = FakeLevel;
    Hooks().race = FakeRace;
    Hooks().female = FakeFemale;
    CreditCases();
    StepCases();
    PickCases();
    MagicCases();
    SheetOffCases();
    RaceCases();
    ClassCases();
    GoverningCases();
    GlobalCases();
    std::printf(g_failures ? "%d FAILED\n" : "all passed\n", g_failures);
    return g_failures ? 1 : 0;
}
