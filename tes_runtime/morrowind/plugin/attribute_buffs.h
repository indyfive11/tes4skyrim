// What the player's Morrowind attributes do in Skyrim while the character
// sheet is on. One rule: a buff is its normal amount times
// (0.5 + attribute / 100), so 50 plays like vanilla, 0 gives half and 100 one
// and a half.
//
//   Intelligence  Magicka from each Magicka pick       } at the level-up step,
//   Endurance     Health from each Health pick         } from the BASE
//   Agility       Stamina from each Stamina pick       } attribute; never
//   Strength      carry weight from each Stamina pick  } retroactive
//   Willpower     Magicka regen (MagickaRateMult)      } always, from the
//   Speed         Stamina regen (StaminaRateMult)      } attribute as magic
//   Luck          critical chance, (Luck - 50) / 10    } moves it
//
// A Fortify or Drain moves the pick buffs too, for as long as it lasts: each
// point is 1/100 of a pick's amount for every pick of that pool so far.
//
// Every buff is HELD: the runtime remembers what it added to each Skyrim
// value and moves the value by the difference, so turning the sheet off hands
// every point back.
// See: docs/commentary/morrowind_runtime.md#attribute-buffs

#pragma once

namespace tesruntime::mw {

// Remembers the pools' bases, less what the buffs added, as the point the
// next step counts picks from. The first read of a game and a race change.
void RecordPools();

// At a completed level-up step: each pool's rise since the last record is
// that many picks, which earn their bonus at the attributes as they now stand.
void CreditPoolPicks();

// A chargen choice (race now; class and birthsign later) moved `attribute`'s
// base by `delta`: every pick it already earned is re-scored as if the
// attribute had always stood there. Level-ups never are.
// See: docs/commentary/morrowind_runtime.md#race-attributes
void RescorePickBuffs(int attribute, float delta);

// Moves every buffed value to its target, or back to vanilla with the sheet
// off. Game thread, out in the world.
void HoldAttributeBuffs();

}  // namespace tesruntime::mw
