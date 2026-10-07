// The bytes of the detour: a stub that counts a visit to the site, and the
// jump to it that replaces the site's first seven bytes.
//
// Portable (it only encodes); the plugin places the bytes, the test harness
// executes them against a mock of the site.

#pragma once

#include <cstdint>

namespace interior_bounds {

enum class Mode : int {
    kLook = 0,    // find the site, write nothing
    kCount = 1,   // count visits, behaviour unchanged
    kPatch = 2,   // count visits and leave the reference where it is
};

struct Detour {
    std::uint8_t stub[24];
    int          stub_len = 0;
    std::uint8_t site[7];
};

// All four are addresses in one address space. `original` is the site's
// first seven bytes, which mode 1 runs from the stub. False when the mode is
// not 1 or 2, or a distance does not fit a 32-bit relative jump.
bool BuildDetour(Mode mode, std::uint64_t site, std::uint64_t exit,
                 std::uint64_t stub, std::uint64_t counter,
                 const std::uint8_t* original, Detour& out);

}  // namespace interior_bounds
