#include "stat_rows.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <set>

#include "chargen_menu.h"
#include "chargen_tables.h"
#include "dialogue_state.h"
#include "leveling.h"
#include "scope.h"
#include "script_tables.h"

namespace tesruntime::mw {

namespace {

// Oblivion.exe's labels for the two standing values, and its two bounties:
// the main realm's and the Shivering Isles'.
constexpr const char* kFameSetting = "sMiscFame";
constexpr const char* kInfamySetting = "sMiscInfamy";
constexpr const char* kBountySetting = "sMiscBounty";
constexpr const char* kSecondBountySetting = "sMiscSEBounty";

// The games Reputation belongs to, by their heading: Morrowind's own stat,
// which the MWScript dialogue raises.
constexpr const char* kMorrowind = "Morrowind";
constexpr const char* kMorroblivion = "Morroblivion";

// A sidecar folder's heading: Morroblivion's master is titled for the game.
std::string GameTitle(const std::string& plugin) {
    return _stricmp(plugin.c_str(), "Morrowind_ob") == 0 ? kMorroblivion : plugin;
}

bool IsMorrowindGame(const std::string& title) {
    return title == kMorrowind || title == kMorroblivion;
}

std::string Number(float value) { return std::to_string(static_cast<int>(std::lround(value))); }

std::string GlobalText(const FormRef& ref) {
    float* slot = ref.plugin.empty() || !Hooks().globalSlot
                      ? nullptr
                      : Hooks().globalSlot(ref.plugin, ref.formId);
    return slot ? Number(*slot) : std::string();
}

std::string BountyText(const FormRef& faction) {
    float gold = 0;
    const bool read = Hooks().factionCrimeGold &&
                      Hooks().factionCrimeGold(faction.plugin, faction.formId, &gold);
    return read ? Number(gold) : std::string();
}

// A page row: its global, or the first stage rule whose quest is still
// below its stage.
std::string PageText(const PageStatRow& row) {
    if (row.rules.empty()) return GlobalText(row.global);
    for (const StageRule& rule : row.rules) {
        const int stage = Hooks().questStageOf
                              ? Hooks().questStageOf(rule.quest.plugin, rule.quest.formId)
                              : -1;
        if (stage >= 0 && stage < rule.stage) return Number(rule.value);
    }
    return Number(row.otherwise);
}

std::string SignText() {
    const std::string chosen = ChosenBirthsign();
    const SignRow* row = chosen.empty() ? nullptr : FindSignRow(chosen);
    return row ? row->name : TitleCase(chosen);
}

// One row per form: a dependent (Translation.esp) lists the master's globals
// its scripts also write, which the master's own rows already show.
bool FirstSight(std::set<std::string>& shown, const FormRef& ref) {
    return !ref.plugin.empty() &&
           shown.insert(ref.plugin + '|' + std::to_string(ref.formId & 0xFFFFFF)).second;
}

// A realm's bounty label, as Oblivion's own page wrote its two: the main
// realm's, the second's (Shivering Isles Bounty); a further one names its realm.
std::string BountyLabel(const StatsTable& table, std::size_t index) {
    const std::string word = GmstText("sBounty", "Bounty");
    if (index == 0) return StatLabel(kBountySetting, word, table.layer);
    const std::string named = table.bounties[index].name + " " + word;
    return index == 1 ? StatLabel(kSecondBountySetting, named, table.layer) : named;
}

std::vector<StatisticRow> GameRows(std::set<std::string>& shown, const StatsTable& table) {
    std::vector<StatisticRow> game;
    for (std::size_t i = 0; i < table.bounties.size(); ++i) {
        if (FirstSight(shown, table.bounties[i].faction)) {
            game.push_back({BountyLabel(table, i), BountyText(table.bounties[i].faction)});
        }
    }
    if (FirstSight(shown, table.fame)) {
        game.push_back({StatLabel(kFameSetting, "Fame", table.layer), GlobalText(table.fame)});
    }
    if (FirstSight(shown, table.infamy)) {
        game.push_back({StatLabel(kInfamySetting, "Infamy", table.layer), GlobalText(table.infamy)});
    }
    for (const MiscStatRow& stat : table.misc) {
        if (!FirstSight(shown, stat.global)) continue;
        game.push_back({StatLabel(stat.setting, stat.fallback, table.layer), GlobalText(stat.global)});
    }
    for (const PageStatRow& page : table.page) {
        if (page.rules.empty() && !FirstSight(shown, page.global)) continue;
        game.push_back({page.label, PageText(page)});
    }
    return game;
}

StatisticRow ReputationRow() {
    return {GmstText("sReputation", "Reputation"), std::to_string(State().reputation)};
}

// One game's heading and rows; Reputation leads a Morrowind game's, once.
void AddGame(std::vector<StatisticRow>& rows, std::vector<StatisticRow> game,
             const std::string& title, bool& reputation) {
    if (!reputation && IsMorrowindGame(title)) {
        game.insert(game.begin(), ReputationRow());
        reputation = true;
    }
    if (game.empty()) return;
    rows.push_back({});
    rows.push_back({title, "", true});
    rows.insert(rows.end(), game.begin(), game.end());
}

// The Morrowind game a sidecar is loaded for, by heading, or "".
std::string LoadedMorrowindGame() {
    if (LayerIndex("Morrowind_ob") >= 0) return kMorroblivion;
    return LayerIndex(kMorrowind) >= 0 ? kMorrowind : "";
}

}  // namespace

std::string ChosenClassText() {
    const std::string chosen = ChosenClass();
    const ClassRow* row = chosen.empty() ? nullptr : FindClassRow(chosen);
    return row ? row->name : TitleCase(chosen);
}

std::vector<StatisticRow> StatisticRows() {
    std::vector<StatisticRow> rows = {{GmstText("sBirthSign", "Birth Sign"), SignText()}};
    // Masters first, so a form is listed under the game that owns it.
    std::vector<const StatsTable*> tables;
    for (const StatsTable& table : StatsTables()) tables.push_back(&table);
    std::stable_sort(tables.begin(), tables.end(), [](const StatsTable* a, const StatsTable* b) {
        return LayerDepth(a->layer) < LayerDepth(b->layer);
    });
    std::set<std::string> shown;
    bool reputation = false;
    for (const StatsTable* table : tables) {
        AddGame(rows, GameRows(shown, *table), GameTitle(table->plugin), reputation);
    }
    const std::string morrowind = LoadedMorrowindGame();
    if (!reputation && !morrowind.empty()) AddGame(rows, {}, morrowind, reputation);
    return rows;
}

}  // namespace tesruntime::mw
