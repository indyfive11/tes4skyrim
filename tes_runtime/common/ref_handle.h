// Turning an engine reference handle into its reference, and letting go of it.

#pragma once

#include <cstddef>
#include <cstdint>

namespace tesruntime {

// BSHandleRefObject's reference count: the low 10 bits of this word.
constexpr std::size_t kRefCount = 0x28;

// The reference `handle` names, held -- ReleaseRef it when done -- or null.
void* RefFromHandle(std::uint32_t handle);

// Releases a reference RefFromHandle handed out.
void ReleaseRef(void* ref);

}  // namespace tesruntime
