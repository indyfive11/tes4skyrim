// Morrowind's level-up dialog (`openmw_levelup_dialog.layout`): the class
// image the level's skill increases earned, three coins, and the eight
// attributes with the multiplier each would rise by. It cannot be closed
// until the coins are spent.
// See: docs/commentary/morrowind_runtime.md#character-sheet

#pragma once

namespace tesruntime::mw {

// Registers the dialog. False when the engine's menu entry points are missing.
bool InstallLevelUpMenu();

// Opens it for the next pending step (leveling.h).
void OpenLevelUp();

// Whether it is open.
bool LevelUpOpen();

}  // namespace tesruntime::mw
