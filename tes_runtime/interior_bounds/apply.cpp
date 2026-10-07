#include "apply.h"

#include <windows.h>

#include <cstring>

#include "log.h"

namespace interior_bounds {

namespace {

// Visits to the site, on a read-write page of its own next to the stub.
volatile LONG64* g_counter = nullptr;
Mode             g_mode = Mode::kLook;

// Logs the counter when it has moved. The count is per world build, not per
// reference: a reference left in place is out of range again the next time
// its cell's physics world is built.
DWORD WINAPI CounterThread(LPVOID) {
    LONG64 last = 0;
    for (;;) {
        Sleep(1000);
        const LONG64 now = *g_counter;
        if (now == last) continue;
        Log("%s: %lld in total (+%lld)",
            g_mode == Mode::kCount ? "moves seen" : "moves suppressed",
            static_cast<long long>(now), static_cast<long long>(now - last));
        last = now;
    }
}

// Two fresh pages a rel32 from `site` can reach. The search starts outside
// the image (nothing inside it is free) and walks away from it on both sides,
// one allocation boundary at a time.
std::uint8_t* AllocNear(std::uintptr_t site, std::uintptr_t image_begin,
                        std::uintptr_t image_end, SIZE_T bytes) {
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    const std::uintptr_t gran = si.dwAllocationGranularity;
    // Stay clear of the rel32 edges: the stub must also reach the exit and
    // its own counter page.
    constexpr std::uintptr_t kReach = 0x70000000;
    const std::uintptr_t below = image_begin & ~(gran - 1);
    const std::uintptr_t above = (image_end + gran - 1) & ~(gran - 1);
    for (std::uintptr_t k = 0;; ++k) {
        std::uintptr_t at[2] = {0, 0};
        if ((k + 2) * gran <= below && site - (below - (k + 1) * gran) < kReach) {
            at[0] = below - (k + 1) * gran;
        }
        if (above + k * gran - site < kReach) at[1] = above + k * gran;
        if (!at[0] && !at[1]) return nullptr;
        for (std::uintptr_t want : at) {
            if (!want) continue;
            void* got = VirtualAlloc(reinterpret_cast<void*>(want), bytes,
                                     MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
            if (!got) continue;
            if (reinterpret_cast<std::uintptr_t>(got) == want) {
                return static_cast<std::uint8_t*>(got);
            }
            VirtualFree(got, 0, MEM_RELEASE);
        }
    }
}

bool Give(std::uint8_t* mem, const char* why) {
    if (mem) VirtualFree(mem, 0, MEM_RELEASE);
    Log("REFUSE: %s; nothing written", why);
    return false;
}

// Replaces the site's first seven bytes, then reads them back. `touched`
// says whether the site was written at all; on a mismatch the original
// bytes are put back and the failure is logged.
bool WriteSite(std::uint8_t* site, const std::uint8_t* bytes, bool& touched) {
    DWORD old = 0, ignored = 0;
    touched = false;
    if (!VirtualProtect(site, kSiteLen, PAGE_EXECUTE_READWRITE, &old)) {
        Log("REFUSE: the site cannot be made writable (error %lu); "
            "nothing written", GetLastError());
        return false;
    }
    touched = true;
    std::memcpy(site, bytes, kSiteLen);
    const bool same = std::memcmp(site, bytes, kSiteLen) == 0;
    if (!same) {
        std::memcpy(site, kSiteBytes, kSiteLen);
        Log(std::memcmp(site, kSiteBytes, kSiteLen) == 0
                ? "FAILED: the site did not take the write; original put back"
                : "FAILED: the site did not take the write and does not hold "
                  "the original either");
    }
    if (!VirtualProtect(site, kSiteLen, old, &ignored)) {
        Log("the site's page protection could not be restored (error %lu)",
            GetLastError());
    }
    FlushInstructionCache(GetCurrentProcess(), site, kSiteLen);
    return same;
}

}  // namespace

bool Apply(Mode mode, std::uint8_t* base, std::uint64_t image_size,
           const Found& found) {
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    const SIZE_T page = si.dwPageSize;
    std::uint8_t* site = base + found.site;
    const auto site_addr = reinterpret_cast<std::uintptr_t>(site);
    if ((site_addr & (page - 1)) > page - kSiteLen) {
        return Give(nullptr, "the site straddles a page boundary");
    }
    const auto begin = reinterpret_cast<std::uintptr_t>(base);
    std::uint8_t* mem = AllocNear(site_addr, begin, begin + image_size, 2 * page);
    if (!mem) return Give(nullptr, "no free memory within reach of the site");
    std::uint8_t* counter = mem + page;

    Detour d;
    if (!BuildDetour(mode, site_addr,
                     reinterpret_cast<std::uintptr_t>(base + found.exit),
                     reinterpret_cast<std::uintptr_t>(mem),
                     reinterpret_cast<std::uintptr_t>(counter), kSiteBytes, d)) {
        return Give(mem, "the stub would be out of reach");
    }
    // The stub first: written, made execute-read, read back. Its counter
    // page stays read-write.
    std::memcpy(mem, d.stub, d.stub_len);
    DWORD old = 0;
    if (!VirtualProtect(mem, page, PAGE_EXECUTE_READ, &old)) {
        return Give(mem, "the stub page cannot be made executable");
    }
    FlushInstructionCache(GetCurrentProcess(), mem, d.stub_len);
    if (std::memcmp(mem, d.stub, d.stub_len) != 0) {
        return Give(mem, "the stub did not read back");
    }
    // The site last, and only if it still holds what the search saw.
    if (std::memcmp(site, kSiteBytes, kSiteWindow) != 0) {
        return Give(mem, "the site changed since it was checked");
    }
    bool touched = false;
    if (!WriteSite(site, d.site, touched)) {
        // A site that was written keeps its stub: a jump left behind must
        // still land on something.
        if (!touched) VirtualFree(mem, 0, MEM_RELEASE);
        return false;
    }
    g_counter = reinterpret_cast<volatile LONG64*>(counter);
    g_mode = mode;
    Log("mode %d (%s): the site now jumps to a stub at %p", static_cast<int>(mode),
        mode == Mode::kCount ? "count; references are still moved"
                             : "keep; references are no longer moved",
        static_cast<void*>(mem));
    const HANDLE thread = CreateThread(nullptr, 0, CounterThread, nullptr, 0, nullptr);
    if (thread) {
        CloseHandle(thread);
    } else {
        Log("the counter thread did not start; counts will not be logged");
    }
    return true;
}

}  // namespace interior_bounds
