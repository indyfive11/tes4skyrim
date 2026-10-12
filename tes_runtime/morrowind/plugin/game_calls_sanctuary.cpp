// Sanctuary: TES3's flat chance to dodge a weapon hit. It converts as a
// script-less Script effect whose PerkToApply is MWSanctuaryPerk: 100 Mod
// Incoming Damage entries, each testing one rank of the conversion-owned
// MWSanctuaryFaction and rolling that many percent, vanilla DeftMovement's
// dodge. The rank is all the runtime supplies: each tick it holds it at the
// actor's summed Sanctuary, capped at 100, as OpenMW's getEvasion adds it.
// See: docs/commentary/morrowind_runtime.md#sanctuary

#include "game_calls_internal.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <set>

#include "ids.h"

namespace tesruntime::mw {
namespace gamecalls {

namespace {

// OpenMW: evasion += min(100, Sanctuary).
constexpr int kSanctuaryCap = 100;

// The row effects_formid.txt names the faction by.
constexpr const char* kFactionRow = "sanctuary";

// A placed actor's form type, which the watched ids are tested against: an id
// saved in one session may name something else in the next.
constexpr std::uint8_t kFormTypeCharacter = 0x3E;

// The staged faction, once it resolves; the load order cannot change after.
void* Faction() {
    static void* faction = nullptr;
    if (!faction) {
        const FormRef* row = EffectForm(kFactionRow);
        faction = row ? Form(row) : nullptr;
    }
    return faction;
}

// Sets `actor`'s rank to its summed Sanctuary; true while it has any.
bool Rank(void* actor, std::uint32_t id, void* faction) {
    const long summed = std::lround(ActiveMagnitude(actor, kSanctuaryEffect));
    const int rank = static_cast<int>(std::clamp<long>(summed, 0, kSanctuaryCap));
    const auto known = State().sanctuaryRanks.try_emplace(id, -1).first;
    if (known->second != rank && SetFactionRank(actor, faction, rank)) known->second = rank;
    return rank > 0;
}

}  // namespace

bool IsActorRef(void* ref) {
    return ref && At<std::uint8_t>(ref, ids::kOffFormType) == kFormTypeCharacter;
}

void WatchSanctuary(std::uint32_t actorId) {
    if (actorId) State().sanctuaryHolders.insert(actorId);
}

// The player every tick, since a save can carry a Sanctuary the apply sink
// never saw; every other holder until its Sanctuary is gone. A holder whose
// reference is not loaded keeps its rank and its place.
void TickSanctuary(void* player) {
    void* faction = Faction();
    if (!faction) return;
    if (player) Rank(player, FormIdOf(player), faction);
    std::set<std::uint32_t>& holders = State().sanctuaryHolders;
    for (auto it = holders.begin(); it != holders.end();) {
        void* actor = RefByRuntimeId(*it);
        if (IsActorRef(actor) && !Rank(actor, *it, faction)) {
            it = holders.erase(it);
        } else {
            ++it;
        }
    }
}

}  // namespace gamecalls
}  // namespace tesruntime::mw
