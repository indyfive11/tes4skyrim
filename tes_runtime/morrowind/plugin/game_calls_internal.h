// What the game_calls_*.cpp files share: the resolvers every engine call goes
// through, and the few natives more than one of them needs.
// See: docs/commentary/morrowind_runtime.md#game-calls

#pragma once

#include <cstdint>
#include <functional>
#include <string>
#include <vector>

#include "activation.h"
#include "addresses.h"
#include "dialogue_state.h"
#include "scope.h"
#include "script_tables.h"

namespace tesruntime::mw {
namespace gamecalls {

using SetValueFn = void (*)(void* vm, std::uint32_t stack, void* actor,
                            void* name, float value);
// ObjectReference.PlaceAtMe(base, count, forcePersist, initiallyDisabled).
using PlaceAtMeFn = void* (*)(void* vm, std::uint32_t stack, void* self,
                              void* base, std::int32_t count,
                              bool forcePersist, bool initiallyDisabled);

extern SetValueFn  g_setValue;
extern PlaceAtMeFn g_placeAtMe;
// The live reference the player is talking to (SetSpeakerRef), or null.
extern void*       g_speakerRef;

std::string Lower(std::string text);
// Logs an id that does not resolve, once per id.
void ReportOnce(const char* what, const std::string& id);
void* Form(const FormRef* ref);
void* ItemForm(const std::string& item);
// The reference a TES3 id names: the player, the speaker, the instance
// running the current script, or a staged placement.
void* OwnerRef(const std::string& owner);
void* PlayerRef();
void* RefByRuntimeId(std::uint32_t runtimeFormId);
// World units between two references, or -1 when either cannot be measured.
// The string form `Distance` resolves ids; this one takes what the caller
// already holds.
float DistanceBetween(void* from, void* to);
bool StartQuest(void* form, bool* justStarted);
// Puts `ref` at a position with a Z rotation in degrees. game_calls_move.cpp.
void PlaceAt(void* ref, float x, float y, float z, float zRot);
// Puts `ref` at a spot inside `place`, a CELL or a WORLDSPACE form, in ONE
// engine move; false when `place` is neither. Game thread only.
bool MoveInto(void* ref, void* place, float x, float y, float z, float zRot);
// The cell the player stands in, or null before a game is loaded.
void* PlayerCell();
bool PlayerInInterior();

template <typename Fn>
Fn Native(const char* name, std::uint64_t id) {
    return reinterpret_cast<Fn>(Resolve(name, id, nullptr));
}

// Sends every reference to a spot in the named cell, `zRot` in degrees, in
// ONE engine move each, so the player's cell load cannot land between a move
// and a reposition. game_calls_move.cpp.
void SendToCell(const std::vector<void*>& refs, const std::string& cell,
                float x, float y, float z, float zRot);
// Sends every reference onto a persistent marker, taking its rotation.
void SendToMarker(const std::vector<void*>& refs, const FormRef& marker);
// The actors in a follow slot aimed at the player. game_calls_ai.cpp.
std::vector<void*> PlayerFollowers();

// Each file resolves its own natives and supplies its own hooks.
void InstallMoveCalls(GameHooks& hooks);
void InstallAiCalls(GameHooks& hooks);
void InstallQueryCalls(GameHooks& hooks);
// The spell natives: the actor's spell list, casting, and active effects.
// See: docs/commentary/morrowind_runtime.md#spell-commands
void InstallSpellCalls(GameHooks& hooks);
// The player's factions onto the converted FACTs, and the tick's copy of the
// state barks test into their GLOBs. game_calls_state.cpp.
// See: docs/commentary/morrowind_runtime.md#published-state
void InstallStateCalls(GameHooks& hooks);
// The value of the GLOB at `formId` in `plugin`, or null. Game thread.
float* GlobalSlot(const std::string& plugin, std::uint32_t formId);
// An actor's rank in a faction, -2 when it is no member or the native is
// missing; and setting one, which joins the faction. False when it could not.
int FactionRank(void* actor, void* faction);
bool SetFactionRank(void* actor, void* faction, int rank);
// PC Crime Level on the engine's crime gold, and the fine and jail opcodes.
// See: docs/commentary/morrowind_runtime.md#crime-is-the-engines
void InstallCrimeCalls(GameHooks& hooks);
void PublishState();
// Mark, Recall and the two Interventions, on the VM's OnMagicEffectApply sink.
// See: docs/commentary/morrowind_runtime.md#teleport-effects
void InstallTeleportCalls();
// The TES3 effect index and attribute of a runtime-carried MGEF (or delivery
// copy); index -1 for any other.
RuntimeEffect RuntimeEffectOf(std::uint32_t effectId);
// The TES3 indices of the duration effects the runtime carries.
constexpr int kSwiftSwimEffect = 1;
constexpr int kLevitateEffect = 10;
constexpr int kSlowFallEffect = 11;
constexpr int kSanctuaryEffect = 42;
// Levitate and SlowFall on the InAir and OnGround states' simulate, SwiftSwim
// on the Swimming state's, and Sanctuary's faction rank, all read from the
// active-effect list.
// See: docs/commentary/morrowind_runtime.md#levitate-and-slowfall
void InstallFlightCalls(GameHooks& hooks);
// The summed magnitude of every active instance of a runtime-carried effect
// on `actor`, read off its active-effect list; with `attribute` set, only the
// instances of that attribute's variant. Game thread.
float ActiveMagnitude(void* actor, int tes3Index, int attribute = -1);
// Each active, runtime-carried effect instance on `actor` with its magnitude:
// one walk of the list for a caller that needs many sums. Game thread.
void ForEachActiveEffect(void* actor,
                         const std::function<void(const RuntimeEffect&, float)>& fn);
// Morrowind's attribute effects on the player, and the skill cap on its
// skill use and trainers. game_calls_attributes.cpp.
// See: docs/commentary/morrowind_runtime.md#attribute-effects
void TickAttributeEffects(void* player);
void InstallAttributeCalls(GameHooks& hooks);
// Game thread: an attribute effect just landed on `actorId`, cast by
// `casterId` (0 for none), so the tick sums it -- and pays an Absorb's
// magnitude to its caster for as long as the target carries it.
void WatchAttributes(std::uint32_t actorId, std::uint32_t casterId, const RuntimeEffect& row);
// Game thread: `actorId` just had a Sanctuary applied, so the tick ranks it.
void WatchSanctuary(std::uint32_t actorId);
// Whether `ref` is a placed actor: an id saved in one session may name
// something else in the next.
bool IsActorRef(void* ref);
// Keeps the player's and each watched actor's Sanctuary faction rank at its
// summed Sanctuary. game_calls_sanctuary.cpp.
void TickSanctuary(void* player);
// `PlayGroup` / `LoopGroup`: the object animation queue on the mesh's own
// sequences, and the tick that advances it. game_calls_anim.cpp.
void InstallAnimCalls(GameHooks& hooks);
// The seven player-control switches and Game.ShowRaceMenu.
// See: docs/commentary/morrowind_runtime.md#the-control-switches
void InstallControlCalls(GameHooks& hooks);
// The engine's buttoned message box, which Debug.MessageBox cannot raise.
// Hooked through ShowMessage rather than a hook of its own.
// See: docs/commentary/morrowind_runtime.md#messagebox-buttons
void InstallMessageCalls();
void ShowButtonMessage(const std::string& text,
                       const std::vector<std::string>& buttons);
// Skyrim's corner notification, which is what OpenMW's buttonless message box
// is: the line a refused action shows. Nothing for empty text.
void Notify(const std::string& text);

}  // namespace gamecalls
}  // namespace tesruntime::mw
