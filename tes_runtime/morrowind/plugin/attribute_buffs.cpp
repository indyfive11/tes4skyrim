#include "attribute_buffs.h"

#include <algorithm>
#include <cmath>
#include <string>

#include "actor_stats.h"
#include "dialogue_state.h"
#include "log.h"

namespace tesruntime::mw {

namespace {

// Where the buffs keep their numbers among DialogueState's variables, so they
// ride the co-save beside the attributes they follow.
constexpr const char* kOwner = "buffs|player";
constexpr const char* kPlayer = "player";

// TES3 attribute indices.
constexpr int kStrength = 0, kIntelligence = 1, kWillpower = 2, kAgility = 3;
constexpr int kSpeed = 4, kEndurance = 5, kLuck = 7;

// The attribute every buff is centered on: 50 plays like vanilla Skyrim.
constexpr float kNeutral = 50.0f;

// Skyrim's level-up pick (Skyrim.esm): iAVDhmsLevelUp 10 to the chosen pool,
// and fLevelUpCarryWeightMod 5 carry weight with a Stamina pick.
constexpr float kPoolPerPick = 10.0f;
constexpr float kCarryPerPick = 5.0f;

// A buff the level-up picks earn: `value` grows by `perPick` times the
// attribute's scale for every pick of `pool`.
struct PickBuff {
    const char* value;
    const char* pool;
    int attribute;
    float perPick;
};

constexpr PickBuff kPickBuffs[] = {
    {"Health", "Health", kEndurance, kPoolPerPick},
    {"Magicka", "Magicka", kIntelligence, kPoolPerPick},
    {"Stamina", "Stamina", kAgility, kPoolPerPick},
    {"CarryWeight", "Stamina", kStrength, kCarryPerPick},
};

constexpr const char* kPools[] = {"Health", "Magicka", "Stamina"};

// A buff that follows the attribute at every moment: `perPoint` per point
// past neutral. The two regen multipliers are percentages over a base of 100,
// the ones vanilla's Fortify Regenerate effects move (Skyrim.esm MGEF av
// 156/157); CritChance is percentage points.
struct RateBuff {
    const char* value;
    int attribute;
    float perPoint;
};

constexpr RateBuff kRateBuffs[] = {
    {"MagickaRateMult", kWillpower, 1.0f},
    {"StaminaRateMult", kSpeed, 1.0f},
    {"CritChance", kLuck, 0.1f},
};

// Below this a held value is already where it should be.
constexpr float kHoldEpsilon = 0.01f;

float Var(const std::string& name) { return State().Var(kOwner, name); }

void SetVar(const std::string& name, float value) {
    State().SetVar(kOwner, name, value);
}

std::string Applied(const char* value) { return std::string("applied.") + value; }
std::string Bonus(const char* value) { return std::string("bonus.") + value; }
std::string Picks(const char* pool) { return std::string("picks.") + pool; }
std::string Raw(const char* pool) { return std::string("raw.") + pool; }

// A pool's base without what the buffs hold on it.
float RawBase(const char* pool) {
    return Hooks().baseActorValue(kPlayer, pool) - Var(Applied(pool));
}

// What one point past neutral is worth, as a fraction of a pick.
float Scale(float attribute) { return attribute / 100.0f - kNeutral / 100.0f; }

// Moves `value` by what its target gained or lost since the last hold.
void Hold(const char* value, float target) {
    const float applied = Var(Applied(value));
    if (std::fabs(target - applied) < kHoldEpsilon) return;
    const float base = Hooks().baseActorValue(kPlayer, value);
    Hooks().setActorValue(kPlayer, value, base + target - applied);
    SetVar(Applied(value), target);
}

// The pick buff's target: the bonus earned at the steps, plus the active
// magic on its attribute for every pick of its pool.
float PickTarget(const PickBuff& buff) {
    const float magic = ActorAttribute(kPlayer, buff.attribute) -
                        ActorBaseAttribute(kPlayer, buff.attribute);
    return Var(Bonus(buff.value)) +
           Var(Picks(buff.pool)) * buff.perPick * magic / 100.0f;
}

float RateTarget(const RateBuff& buff) {
    return (ActorAttribute(kPlayer, buff.attribute) - kNeutral) * buff.perPoint;
}

bool CanHold() {
    return Hooks().baseActorValue && Hooks().setActorValue;
}

}  // namespace

void RecordPools() {
    if (!Hooks().baseActorValue) return;
    for (const char* pool : kPools) SetVar(Raw(pool), RawBase(pool));
}

void CreditPoolPicks() {
    if (!Hooks().baseActorValue) return;
    for (const char* pool : kPools) {
        if (!State().HasVar(kOwner, Raw(pool))) continue;
        const float rise = RawBase(pool) - Var(Raw(pool));
        const long picks = std::max(0L, std::lround(rise / kPoolPerPick));
        SetVar(Picks(pool), Var(Picks(pool)) + static_cast<float>(picks));
        for (const PickBuff& buff : kPickBuffs) {
            if (std::string(buff.pool) != pool || picks == 0) continue;
            const float attribute = ActorBaseAttribute(kPlayer, buff.attribute);
            const float bonus = picks * buff.perPick * Scale(attribute);
            SetVar(Bonus(buff.value), Var(Bonus(buff.value)) + bonus);
            Log("buffs: %ld %s pick(s) at attribute %.0f -> %s %+.1f", picks, pool,
                attribute, buff.value, bonus);
        }
    }
    RecordPools();
}

void RescorePickBuffs(int attribute, float delta) {
    for (const PickBuff& buff : kPickBuffs) {
        const float picks = Var(Picks(buff.pool));
        if (buff.attribute != attribute || picks == 0.0f) continue;
        const float shift = picks * buff.perPick * delta / 100.0f;
        SetVar(Bonus(buff.value), Var(Bonus(buff.value)) + shift);
        Log("buffs: attribute %d %+.0f re-scores %.0f %s pick(s) -> %s %+.1f", attribute,
            delta, picks, buff.pool, buff.value, shift);
    }
}

void HoldAttributeBuffs() {
    if (!CanHold()) return;
    const bool on = SheetEnabled();
    for (const PickBuff& buff : kPickBuffs) Hold(buff.value, on ? PickTarget(buff) : 0.0f);
    for (const RateBuff& buff : kRateBuffs) Hold(buff.value, on ? RateTarget(buff) : 0.0f);
    // Off, no step counts picks: keep the record current so turning the sheet
    // on later counts only the picks made after it.
    if (!on) RecordPools();
}

}  // namespace tesruntime::mw
