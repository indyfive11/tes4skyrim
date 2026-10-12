// The stats window's Statistics tab: the birthsign chosen and Morrowind's
// reputation, then each converted game's bounty per realm, Fame, Infamy, the
// general statistics Skyrim does not keep and the rows of the game's own
// statistics page, read from what stats.txt names.
// See: docs/commentary/morrowind_runtime.md#statistics-tab

#pragma once

#include <string>
#include <vector>

namespace tesruntime::mw {

// One line of the tab: a heading (no value), a name with its value, or blank.
struct StatisticRow {
    std::string name;
    std::string value;
    bool heading = false;
};

// Every row, in order. Game thread: the globals are read in place.
std::vector<StatisticRow> StatisticRows();

// The chosen class as its menu spells it, a custom one capitalized.
std::string ChosenClassText();

}  // namespace tesruntime::mw
