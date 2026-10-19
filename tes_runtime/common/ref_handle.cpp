#include "ref_handle.h"

#include <windows.h>

#include "addresses.h"
#include "engine_ids.h"

namespace tesruntime {

namespace {

using LookupByHandleFn = void* (*)(std::uint32_t* handle, void** out);

LookupByHandleFn Lookup() {
    static const auto fn = reinterpret_cast<LookupByHandleFn>(
        Resolve("LookupReferenceByHandle", ids::kLookupByHandle, nullptr));
    return fn;
}

}  // namespace

void* RefFromHandle(std::uint32_t handle) {
    void* ref = nullptr;
    if (handle && Lookup()) Lookup()(&handle, &ref);
    return ref;
}

void ReleaseRef(void* ref) {
    // BSHandleRefObject: refcount in the low 10 bits of +0x28; zero deletes
    // through virtual slot 1 (measured at 0x62c3e2..0x62c3f8).
    auto* count = &At<volatile long>(ref, kRefCount);
    const long before = InterlockedExchangeAdd(count, -1);
    if (((before - 1) & 0x3ff) == 0) {
        VCall<void (*)(void*, int)>(ref, 1)(ref, 1);
    }
}

}  // namespace tesruntime
