#include "detour.h"

#include <cstring>

#include "locate.h"

namespace interior_bounds {

namespace {

bool Rel32(std::uint64_t next, std::uint64_t to, std::uint8_t* out) {
    const std::int64_t d = static_cast<std::int64_t>(to - next);
    if (d > INT32_MAX || d < INT32_MIN) return false;
    const std::int32_t rel = static_cast<std::int32_t>(d);
    std::memcpy(out, &rel, sizeof(rel));
    return true;
}

}  // namespace

bool BuildDetour(Mode mode, std::uint64_t site, std::uint64_t exit,
                 std::uint64_t stub, std::uint64_t counter,
                 const std::uint8_t* original, Detour& out) {
    if (mode != Mode::kCount && mode != Mode::kPatch) return false;
    std::uint8_t* s = out.stub;
    // lock inc qword ptr [rip+d]. It writes flags; the test that follows the
    // site (and the one at the exit) sets them again before anything reads.
    s[0] = 0xF0; s[1] = 0x48; s[2] = 0xFF; s[3] = 0x05;
    if (!Rel32(stub + 8, counter, s + 4)) return false;
    int n = 8;
    std::uint64_t back = exit;
    if (mode == Mode::kCount) {
        std::memcpy(s + n, original, kSiteLen);
        n += kSiteLen;
        back = site + kSiteLen;
    }
    s[n] = 0xE9;
    if (!Rel32(stub + n + 5, back, s + n + 1)) return false;
    out.stub_len = n + 5;
    // jmp stub, then a two-byte nop over the rest of the second instruction.
    out.site[0] = 0xE9;
    if (!Rel32(site + 5, stub, out.site + 1)) return false;
    out.site[5] = 0x66;
    out.site[6] = 0x90;
    return true;
}

}  // namespace interior_bounds
