// Skyrim's perks menu names a skill red while the skill cap holds it: the
// skill is at or past its governing attribute, so use and trainers raise it
// no further.
// See: docs/commentary/morrowind_runtime.md#capped-skills

#pragma once

namespace tesruntime::mw {

// Recolors the perks menu's skill names now and then; call every tick while
// that menu is open. Does nothing on VR, whose perks menu has no such names.
void TickPerksSkills();

}  // namespace tesruntime::mw
