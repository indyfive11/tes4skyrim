// Windows-side check of the write path. It lays a game executable out in
// memory by RVA (it is never run), has apply.cpp patch that copy, and checks
// every byte and page protection left behind. Runs on Windows or under Wine.
//
//   cl /EHsc /std:c++17 /MT /I.. apply_test.cpp ..\locate.cpp ..\detour.cpp
//      ..\apply.cpp
//   apply_test <SkyrimSE.exe> <1|2>

#define _CRT_SECURE_NO_WARNINGS

#include <windows.h>

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

#include "apply.h"
#include "detour.h"
#include "locate.h"
#include "log.h"

namespace interior_bounds {

// The plugin's log, to stdout.
bool LogOpen() { return true; }

void LogLine(const char* text) {
    std::printf("    %s\n", text);
    std::fflush(stdout);
}

void Log(const char* fmt, ...) {
    char text[512];
    va_list args;
    va_start(args, fmt);
    std::vsnprintf(text, sizeof(text), fmt, args);
    va_end(args);
    LogLine(text);
}

}  // namespace interior_bounds

using namespace interior_bounds;

namespace {

int g_failed = 0;
int g_ran = 0;

void Expect(bool ok, const char* what) {
    ++g_ran;
    std::printf("  %-4s %s\n", ok ? "ok" : "FAIL", what);
    if (!ok) ++g_failed;
}

DWORD Protection(const void* at) {
    MEMORY_BASIC_INFORMATION mbi{};
    if (!VirtualQuery(at, &mbi, sizeof(mbi))) return 0;
    return mbi.State == MEM_COMMIT ? mbi.Protect : 0;
}

std::int64_t Rel(const std::uint8_t* field) {
    std::int32_t rel;
    std::memcpy(&rel, field, sizeof(rel));
    return rel;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 3) {
        std::fprintf(stderr, "usage: apply_test <exe> <1|2>\n");
        return 2;
    }
    const Mode mode = std::atoi(argv[2]) == 2 ? Mode::kPatch : Mode::kCount;
    FILE* f = std::fopen(argv[1], "rb");
    if (!f) {
        std::fprintf(stderr, "cannot read %s\n", argv[1]);
        return 2;
    }
    std::fseek(f, 0, SEEK_END);
    std::vector<std::uint8_t> file(static_cast<std::size_t>(std::ftell(f)));
    std::fseek(f, 0, SEEK_SET);
    const std::size_t got = std::fread(file.data(), 1, file.size(), f);
    std::fclose(f);
    Headers h;
    if (got != file.size() || !ParseHeaders(file.data(), file.size(), h)
        || !h.size_of_image) {
        std::fprintf(stderr, "not a 64-bit PE file\n");
        return 2;
    }

    // Lay the file out as the loader would; the code section ends
    // execute-read, like the game's.
    auto* base = static_cast<std::uint8_t*>(VirtualAlloc(
        nullptr, h.size_of_image, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE));
    if (!base) return 2;
    std::memcpy(base, file.data(), file.size() < 0x1000 ? file.size() : 0x1000);
    const SectionInfo* code = nullptr;
    for (int i = 0; i < h.n_sections; ++i) {
        const SectionInfo& s = h.sections[i];
        std::uint64_t n = s.raw_size < s.vsize ? s.raw_size : s.vsize;
        if (s.raw_ptr >= file.size() || s.rva + n > h.size_of_image) continue;
        if (n > file.size() - s.raw_ptr) n = file.size() - s.raw_ptr;
        std::memcpy(base + s.rva, file.data() + s.raw_ptr, n);
        if (s.flags == 0x60000020) code = &s;
    }
    DWORD old = 0;
    if (!code || !VirtualProtect(base + code->rva, code->vsize,
                                 PAGE_EXECUTE_READ, &old)) {
        return 2;
    }

    Image image;
    image.base = base;
    image.size = h.size_of_image;
    Found found = Locate(image);
    Report(found, LogLine);
    Expect(found.ok(), "the copy is accepted before the write");
    if (!found.ok()) return 1;

    std::uint8_t* site = base + found.site;
    Expect(Apply(mode, base, image.size, found), "Apply reports success");

    SYSTEM_INFO si;
    GetSystemInfo(&si);
    const std::int64_t page = si.dwPageSize;
    std::uint8_t* stub = site + 5 + Rel(site + 1);
    Expect(site[0] == 0xE9 && site[5] == 0x66 && site[6] == 0x90,
           "the site is a jump and a two-byte nop");
    Expect(std::memcmp(site + kSiteLen, kSiteBytes + kSiteLen,
                       kSiteWindow - kSiteLen) == 0,
           "the bytes after the seven are untouched");
    Expect(Protection(site) == PAGE_EXECUTE_READ,
           "the site's page is execute-read again");
    Expect(stub < base || stub >= base + image.size,
           "the stub is outside the image");
    Expect(Protection(stub) == PAGE_EXECUTE_READ, "the stub page is execute-read");
    Expect(stub[0] == 0xF0 && stub[1] == 0x48 && stub[2] == 0xFF && stub[3] == 0x05,
           "the stub starts with the counter increment");
    std::uint8_t* counter = stub + 8 + Rel(stub + 4);
    Expect(counter == stub + page, "the counter is at the start of the next page");
    Expect(Protection(counter) == PAGE_READWRITE, "the counter page is read-write");
    LONG64 count;
    std::memcpy(&count, counter, sizeof(count));
    Expect(count == 0, "the counter starts at zero");
    if (mode == Mode::kCount) {
        Expect(std::memcmp(stub + 8, kSiteBytes, kSiteLen) == 0,
               "mode 1: the stub runs the two original instructions");
        Expect(stub[15] == 0xE9 && stub + 20 + Rel(stub + 16) == site + kSiteLen,
               "mode 1: then jumps back to the byte after them");
    } else {
        Expect(stub[8] == 0xE9 && stub + 13 + Rel(stub + 9) == base + found.exit,
               "mode 2: the stub jumps to the function's exit");
    }

    // The counter thread logs a change within about a second.
    InterlockedAdd64(reinterpret_cast<volatile LONG64*>(counter), 3);
    std::printf("  (three visits simulated; the next line should report them)\n");
    Sleep(2500);

    // A second look at a patched image must refuse: it is not the known site.
    found = Locate(image);
    Expect(found.refused == Check::kSite, "a patched image is refused as 'site'");

    std::printf("%s: %d of %d expectations held\n", g_failed ? "FAILED" : "PASSED",
                g_ran - g_failed, g_ran);
    return g_failed ? 1 : 0;
}
