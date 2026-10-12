// The way into the character sheet from Skyrim's perks menu: a key hint in
// the screen's bottom-left corner, Interface/morrowind_perks_button.swf, laid
// over the perks menu while it is open. Clicking it, or the sheet's hotkey,
// opens the sheet over the perks; the same again goes back to them.
// See: docs/commentary/morrowind_runtime.md#perks-button

#pragma once

namespace tesruntime::mw {

// Registers the button, showing `hotkey` (a virtual-key code) on its key cap.
bool InstallPerksButton(int hotkey);

// Whether Skyrim's perks menu is open.
bool PerksMenuOpen();

// Shows the button while the perks menu is open and hides it after, labelled
// for going back while `sheetOpen`. True when it was clicked since the last call.
bool TickPerksButton(bool sheetOpen);

}  // namespace tesruntime::mw
