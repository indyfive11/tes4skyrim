#include "parts.h"

#include <cstdint>
#include <memory>

#include "addresses.h"
#include "engine.h"
#include "gamebryo_sequence.h"
#include "ids.h"
#include "log.h"

namespace tesruntime {

namespace {

using ObjectByNameFn = void* (*)(void* root, void** name, bool recurse);

ObjectByNameFn g_objectByName = nullptr;
std::unique_ptr<FixedString> g_weaponNode;

// Starts `tag` on every managed NiNode under the WEAPON bone (the attached
// weapon's root and, one level down, its parts); geometry is skipped.
int PlayUnder(void* object, void* tag, int depth) {
    void* node = object ? VCall<void* (*)(void*)>(object, kVtAsNode)(object) : nullptr;
    if (!node || depth > 2) return 0;
    int n = 0;
    if (void* seq = sequence::Named(sequence::ManagerOf(node), tag)) {
        sequence::Play(seq);
        ++n;
    }
    void* kids = At<void*>(node, kNodeChildren);
    const std::uint16_t count = At<std::uint16_t>(node, kNodeChildCount);
    for (std::uint16_t i = 0; kids && i < count; ++i) {
        n += PlayUnder(At<void*>(kids, i * sizeof(void*)), tag, depth + 1);
    }
    return n;
}

}  // namespace

bool InstallParts() {
    const std::uintptr_t byName = Resolve("NiAVObject::GetObjectByName", ids::kObjectByName, nullptr);
    if (!sequence::Install() || !byName) return false;
    g_objectByName = reinterpret_cast<ObjectByNameFn>(byName);
    g_weaponNode.reset(new FixedString("WEAPON"));
    return true;
}

int PlayPartSequence(void* actor, void* tag) {
    if (!g_objectByName || !actor || !tag) return 0;
    void* third = VCall<void* (*)(void*)>(actor, kVtGet3D)(actor);
    void* first = VCall<void* (*)(void*, bool)>(actor, kVtGet3DFirstPerson)(actor, true);
    int n = 0;
    if (third) n += PlayUnder(g_objectByName(third, &g_weaponNode->ptr, true), tag, 0);
    if (first && first != third) n += PlayUnder(g_objectByName(first, &g_weaponNode->ptr, true), tag, 0);
    return n;
}

}  // namespace tesruntime
