// Morrowind's character sheet: the stats window OpenMW draws from
// `openmw_stats_window.layout`, opened from Skyrim's perks menu
// (perks_button.h), and the level-up step that follows Skyrim's own level-up.
//
// Read-only over what the runtime already keeps: Skyrim's health, magicka
// and stamina, the eight attributes and 27 skills (actor_stats.h), level,
// reputation, bounty and factions. The attribute step is levelup_menu.h's.
// See: docs/commentary/morrowind_runtime.md#character-sheet

#pragma once

namespace tesruntime::mw {

// Registers the stats window and the level-up dialog, and starts the tick
// that watches the hotkey and the player's level. Once, after the store has
// loaded a Morrowind sidecar.
void InstallCharacterSheet();

// Whether the stats window is open.
bool StatsSheetOpen();

}  // namespace tesruntime::mw
