// Native check of the locator and the detour. It maps a game executable by
// RVA, runs the same locate.cpp the plugin runs, plants faults that must each
// be refused for the right reason, and executes the stub against a mock.
//
//   g++ -std=c++17 -O1 -g -fsanitize=address,undefined -I..
//       locate_test.cpp ../locate.cpp ../detour.cpp -o locate_test
//   ./locate_test <SkyrimSE.exe> [--site 0x..] [--refuse <check>]
//                 [--fingerprint 0x..] [--faults]
//
// --site / --refuse / --fingerprint state what the file must give; --faults
// needs a file the locator accepts. The stub test always runs (x86-64 Linux).

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <string>
#include <vector>

#if defined(__linux__) && defined(__x86_64__)
#include <sys/mman.h>
#define HAVE_EXEC_TEST 1
#endif

#include "detour.h"
#include "locate.h"

using namespace interior_bounds;

namespace {

int g_failed = 0;
int g_ran = 0;

void Line(const char* text) { std::printf("    %s\n", text); }

void Expect(bool ok, const char* what, const char* detail = "") {
    ++g_ran;
    if (ok) return;
    ++g_failed;
    std::printf("  FAIL: %s %s\n", what, detail);
}

std::uint32_t Get32(const std::vector<std::uint8_t>& v, std::uint64_t at) {
    std::uint32_t x;
    std::memcpy(&x, v.data() + at, sizeof(x));
    return x;
}

bool ReadFile(const std::string& path, std::vector<std::uint8_t>& out) {
    FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) return false;
    std::fseek(f, 0, SEEK_END);
    const long size = std::ftell(f);
    std::fseek(f, 0, SEEK_SET);
    out.resize(size > 0 ? static_cast<std::size_t>(size) : 0);
    const std::size_t got = out.empty() ? 0 : std::fread(out.data(), 1, out.size(), f);
    std::fclose(f);
    return got == out.size();
}

// Lays a PE file out by RVA, as the loader would. The buffer is sized from
// the section table, capped; anything the file does not hold stays zero.
bool MapFile(const std::vector<std::uint8_t>& file, std::vector<std::uint8_t>& image) {
    Headers h;
    if (!ParseHeaders(file.data(), file.size(), h)) return false;
    std::uint64_t size = 0x1000;
    for (int i = 0; i < h.n_sections; ++i) {
        const std::uint64_t end =
            static_cast<std::uint64_t>(h.sections[i].rva) + h.sections[i].vsize;
        if (end > size) size = end;
    }
    if (size > (1ull << 30)) return false;
    image.assign(size, 0);
    std::memcpy(image.data(), file.data(),
                file.size() < 0x1000 ? file.size() : 0x1000);
    for (int i = 0; i < h.n_sections; ++i) {
        const SectionInfo& s = h.sections[i];
        if (s.raw_ptr >= file.size()) continue;
        std::uint64_t n = s.raw_size < s.vsize ? s.raw_size : s.vsize;
        if (n > file.size() - s.raw_ptr) n = file.size() - s.raw_ptr;
        std::memcpy(image.data() + s.rva, file.data() + s.raw_ptr, n);
    }
    return true;
}

Found Run(const std::vector<std::uint8_t>& image) {
    Image im;
    im.base = image.data();
    im.size = image.size();
    return Locate(im);
}

// ---- faults -------------------------------------------------------------

// Edits to the mapped image that undo themselves.
class Edit {
public:
    explicit Edit(std::vector<std::uint8_t>& image) : image_(image) {}
    ~Edit() {
        for (auto it = saved_.rbegin(); it != saved_.rend(); ++it) {
            image_[it->first] = it->second;
        }
    }
    void Byte(std::uint64_t at, std::uint8_t v) {
        saved_.emplace_back(at, image_[at]);
        image_[at] = v;
    }
    void Bytes(std::uint64_t at, std::initializer_list<std::uint8_t> v) {
        for (std::uint8_t b : v) Byte(at++, b);
    }
    void U32(std::uint64_t at, std::uint32_t v) {
        for (int k = 0; k < 4; ++k) Byte(at + k, static_cast<std::uint8_t>(v >> (8 * k)));
    }
    void Add32(std::uint64_t at, std::int32_t d) {
        U32(at, Get32(image_, at) + static_cast<std::uint32_t>(d));
    }
    // A 4-byte field at `at` that resolves to `target` when the instruction
    // ends `tail` bytes after it.
    void Rel(std::uint64_t at, std::uint64_t target, int tail = 0) {
        U32(at, static_cast<std::uint32_t>(target - (at + 4 + tail)));
    }
    void Copy(std::uint64_t to, std::uint64_t from, int n) {
        for (int k = 0; k < n; ++k) Byte(to + k, image_[from + k]);
    }

private:
    std::vector<std::uint8_t>& image_;
    std::vector<std::pair<std::uint64_t, std::uint8_t>> saved_;
};

struct Layout {
    std::uint64_t K, L, F, R, J;       // limit, load, function, site, exit
    std::uint64_t X;                   // spare code to plant instructions in
    std::uint64_t code_rva, code_end;
    std::uint64_t entry;               // our function-table entry
    std::uint64_t unwind;              // its unwind record
    std::uint64_t info;                // its handler data
    std::uint64_t state_map;
    std::uint64_t pe, opt;             // PE signature, optional header
    std::uint64_t sections;            // section table
    std::uint64_t table;               // function table
    std::uint64_t writable = 0;        // a writable data section
    std::uint64_t discardable = 0;     // a discardable data section
    std::uint64_t rdata = 0, rdata_end = 0;   // the section holding the limit
    int           rdata_index = -1;
    int           bind = -1;           // an execute-only, non-code section
    int           code = -1;
    std::uint64_t first_fn;            // the first function in the table
    std::uint64_t gap = 0;             // code between two table entries
    std::uint64_t fragment = 0;        // a chained entry's code
    std::uint64_t fragment_parent = 0; // where that entry names its parent
    std::uint64_t twin_entry = 0;      // another function of the same length
    std::uint64_t twin = 0;
    std::uint64_t zeros = 0;           // 160 zero bytes in read-only data
};

// Finds, in the image itself, everything the fault cases need to aim at.
bool Describe(const std::vector<std::uint8_t>& image, const Found& f, Layout& y) {
    Headers h;
    if (!ParseHeaders(image.data(), 0x1000, h)) return false;
    y.K = f.constant; y.L = f.load; y.F = f.fn_begin; y.R = f.site; y.J = f.exit;
    y.pe = Get32(image, 0x3c);
    y.opt = y.pe + 24;
    std::uint16_t opt_size;
    std::memcpy(&opt_size, image.data() + y.pe + 20, 2);
    y.sections = y.opt + opt_size;
    for (int i = 0; i < h.n_sections; ++i) {
        const SectionInfo& s = h.sections[i];
        if (s.flags == 0x60000000) y.bind = i;
        if (s.flags == 0x60000020) y.code = i;
        if (s.flags == 0xC0000040 && !y.writable && s.vsize > 0x1000) y.writable = s.rva;
        if (s.flags == 0x42000040) y.discardable = s.rva;
        if (s.flags == 0x40000040 && y.K >= s.rva && y.K < s.rva + s.vsize) {
            y.rdata = s.rva;
            y.rdata_end = y.rdata + s.vsize;
            y.rdata_index = i;
        }
    }
    y.code_rva = f.code_rva;
    y.code_end = y.code_rva + f.code_size;
    y.X = f.code_rva + 0x40;
    y.table = h.table_rva;
    y.entry = 0;
    std::uint32_t prev_end = 0;
    for (std::uint32_t i = 0; i < h.table_size / 12; ++i) {
        const std::uint64_t e = h.table_rva + 12ull * i;
        const std::uint32_t begin = Get32(image, e), end = Get32(image, e + 4);
        const std::uint32_t unwind = Get32(image, e + 8);
        const bool far = begin > f.fn_end + 0x1000;
        if (begin == f.fn_begin) y.entry = e;
        if (!y.gap && i && far && begin - prev_end >= 24) y.gap = prev_end + 8;
        if (!y.twin && end - begin == f.fn_end - f.fn_begin && begin != f.fn_begin) {
            y.twin_entry = e;
            y.twin = begin;
        }
        if (!y.fragment && far && !(unwind & 3) && end - begin >= 16
            && ((image[unwind] >> 3) & 4)) {
            y.fragment = begin;
            y.fragment_parent = unwind + 4 + ((image[unwind + 2] + 1u) & ~1u) * 2;
        }
        prev_end = end;
    }
    if (!y.entry || !y.writable || !y.discardable || !y.rdata || y.code < 0
        || y.bind < 0 || !y.gap || !y.twin || !y.fragment) {
        return false;
    }
    y.first_fn = Get32(image, h.table_rva);
    y.unwind = Get32(image, y.entry + 8);
    y.info = Get32(image, y.unwind + 4 + 32 + 4);
    y.state_map = Get32(image, y.info + 24);
    int run = 0;
    for (std::uint64_t a = (y.rdata + 3) & ~3ull; a + 4 <= y.rdata_end; a += 4) {
        run = Get32(image, a) == 0 ? run + 1 : 0;
        if (run == 44) {
            y.zeros = a - 4 * 43 + 8;
            break;
        }
    }
    return y.zeros != 0;
}

struct Fault {
    const char* name;
    Check       expect;
    std::function<void(Edit&, const Layout&)> plant;
};

std::vector<Fault> Faults() {
    const std::uint32_t kLimit = 0x46EA6000;
    std::vector<Fault> v = {
        {"limit there twice", Check::kConstant,
         [=](Edit& e, const Layout& y) { e.U32(y.K + 4, kLimit); }},
        {"limit gone", Check::kConstant,
         [](Edit& e, const Layout& y) { e.U32(y.K, 0); }},
        {"limit only unaligned", Check::kConstant,
         [=](Edit& e, const Layout& y) { e.U32(y.K, 0); e.U32(y.K + 1, kLimit); }},
        {"limit only in a writable section", Check::kConstant,
         [=](Edit& e, const Layout& y) { e.U32(y.K, 0); e.U32(y.writable + 0x100, kLimit); }},
        {"second load", Check::kLoad,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0xf3, 0x0f, 0x10, 0x05}); e.Rel(y.X + 4, y.K); }},
        {"second load, REX form", Check::kLoad,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0xf3, 0x44, 0x0f, 0x10, 0x05}); e.Rel(y.X + 5, y.K); }},
        {"no load at all", Check::kLoad,
         [](Edit& e, const Layout& y) { e.Add32(y.L + 4, 4); }},
        {"comiss against the limit in memory", Check::kCompare,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0x0f, 0x2f, 0x05}); e.Rel(y.X + 3, y.K); }},
        {"ucomiss against the limit in memory", Check::kCompare,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0x0f, 0x2e, 0x05}); e.Rel(y.X + 3, y.K); }},
        {"cmpss against the limit in memory", Check::kCompare,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0xf3, 0x0f, 0xc2, 0x05}); e.Rel(y.X + 4, y.K, 1);
             e.Byte(y.X + 8, 0x01); }},
        {"reference of an unknown kind (minss)", Check::kOtherReference,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0xf3, 0x0f, 0x5d, 0x05}); e.Rel(y.X + 4, y.K); }},
        {"reference of an unknown kind (VEX load)", Check::kOtherReference,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0xc5, 0xfa, 0x10, 0x05}); e.Rel(y.X + 4, y.K); }},
        {"the one load sits in another function", Check::kFunctionLength,
         [](Edit& e, const Layout& y) {
             e.Add32(y.L + 4, 4);
             e.Bytes(y.first_fn, {0xf3, 0x0f, 0x10, 0x05}); e.Rel(y.first_fn + 4, y.K); }},
        {"table entry one byte shorter", Check::kFingerprint,
         [](Edit& e, const Layout& y) { e.Add32(y.entry + 4, -1); }},
        {"table entry empty", Check::kFunctionTable,
         [](Edit& e, const Layout& y) { e.U32(y.entry + 4, static_cast<std::uint32_t>(y.F)); }},
        {"table entry overlaps the one before", Check::kFunctionTable,
         [](Edit& e, const Layout& y) { e.Add32(y.entry - 12 + 4, 0x40); }},
        {"table size not a multiple of 12", Check::kFunctionTable,
         [](Edit& e, const Layout& y) { e.Add32(y.opt + 112 + 28, -4); }},
        {"table directory RVA near 4 GiB", Check::kFunctionTable,
         [](Edit& e, const Layout& y) { e.U32(y.opt + 112 + 24, 0xfffffff0); }},
        {"table directory size near 4 GiB", Check::kFunctionTable,
         [](Edit& e, const Layout& y) { e.U32(y.opt + 112 + 28, 0xfffffffc); }},
        {"table directory empty", Check::kFunctionTable,
         [](Edit& e, const Layout& y) { e.U32(y.opt + 112 + 28, 0); }},
        {"table directory outside read-only data", Check::kFunctionTable,
         [](Edit& e, const Layout& y) { e.U32(y.opt + 112 + 24, 0x10); }},
        {"our entry marked as a fragment", Check::kFingerprint,
         [](Edit& e, const Layout& y) { e.Byte(y.unwind, 0x01 | (4 << 3)); }},
        {"unwind record differs", Check::kFingerprint,
         [](Edit& e, const Layout& y) { e.Byte(y.unwind + 4 + 9, 0x69); }},
        {"prologue length differs", Check::kFingerprint,
         [](Edit& e, const Layout& y) { e.Byte(y.unwind + 1, 48); }},
        {"state map differs", Check::kFingerprint,
         [](Edit& e, const Layout& y) { e.Add32(y.state_map + 8, 1); }},
        {"load into another register", Check::kLoadForm,
         [](Edit& e, const Layout& y) { e.Byte(y.L + 3, 0x35); }},
        {"absolute-value bytes differ", Check::kAbsoluteValue,
         [](Edit& e, const Layout& y) { e.Byte(y.L - 9 + 5, 0x58); }},
        {"compare pairs differ", Check::kComparePairs,
         [](Edit& e, const Layout& y) { e.Byte(y.L + 8 + 4, 0x18); }},
        {"flag test differs", Check::kFlagTest,
         [](Edit& e, const Layout& y) { e.Byte(y.L + 0x1b + 4, 0x85); }},
        {"one branch retargeted", Check::kBranches,
         [](Edit& e, const Layout& y) { e.Add32(y.L + 0xdb + 2, 1); }},
        {"first branch retargeted", Check::kBranches,
         [](Edit& e, const Layout& y) { e.Add32(y.L + 0x35 + 2, -3); }},
        {"all four branches moved together", Check::kBranches,
         [](Edit& e, const Layout& y) {
             for (std::uint64_t at : {0x35, 0xdb, 0xed, 0xf6}) e.Add32(y.L + at + 2, 4); }},
        {"a branch's condition changed", Check::kBranches,
         [](Edit& e, const Layout& y) { e.Byte(y.L + 0xf6 + 1, 0x84); }},
        {"jump before the site retargeted", Check::kJumpBefore,
         [](Edit& e, const Layout& y) { e.Add32(y.R - 4, 1); }},
        {"no jump before the site", Check::kJumpBefore,
         [](Edit& e, const Layout& y) { e.Byte(y.R - 5, 0x90); }},
        {"site already patched", Check::kSite,
         [](Edit& e, const Layout& y) { e.Bytes(y.R, {0xe9, 0x93, 0, 0, 0}); }},
        {"exit bytes differ", Check::kExit,
         [](Edit& e, const Layout& y) { e.Byte(y.J + 2, 0x0f); }},
        {"the two calls disagree", Check::kCalls,
         [](Edit& e, const Layout& y) { e.Add32(y.L + 0x55 + 1, 0x10); }},
        {"no nop before the exit", Check::kCalls,
         [](Edit& e, const Layout& y) { e.Byte(y.J - 1, 0xcc); }},
        {"second call is not a call", Check::kCalls,
         [](Edit& e, const Layout& y) { e.Byte(y.J - 6, 0xe9); }},
        {"an instruction after the site differs", Check::kTailFields,
         [](Edit& e, const Layout& y) { e.Byte(y.R + 0x52, 0xe9); }},
        {"the compare after the site differs", Check::kTailFields,
         [](Edit& e, const Layout& y) { e.Byte(y.R + 0x6f, 0x01); }},
        {"near jump into the site's tail", Check::kTailBranch32,
         [](Edit& e, const Layout& y) { e.Byte(y.X, 0xe9); e.Rel(y.X + 1, y.R + 4); }},
        {"near conditional jump into the tail", Check::kTailBranch32,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0x0f, 0x84}); e.Rel(y.X + 2, y.R + 1); }},
        {"call into the last byte of the tail", Check::kTailBranch32,
         [](Edit& e, const Layout& y) { e.Byte(y.X, 0xe8); e.Rel(y.X + 1, y.R + 6); }},
        {"a fifth branch to the site", Check::kSiteEntries,
         [](Edit& e, const Layout& y) { e.Byte(y.X, 0xe9); e.Rel(y.X + 1, y.R); }},
        {"short jump into the tail, from after the site", Check::kTailBranch8,
         [](Edit& e, const Layout& y) { e.Bytes(y.R + 0x3c, {0xeb, 0xc6}); }},
        {"short conditional jump into the tail, from before", Check::kTailBranch8,
         [](Edit& e, const Layout& y) { e.Bytes(y.R - 0x40, {0x75, 0x40}); }},
        {"two code sections", Check::kCodeSection,
         [](Edit& e, const Layout& y) {
             e.U32(y.sections + 40ull * y.bind + 36, 0x60000020); }},
        {"code section writable", Check::kCodeSection,
         [](Edit& e, const Layout& y) {
             e.U32(y.sections + 40ull * y.code + 36, 0xE0000020); }},
        {"header: bad e_lfanew", Check::kHeaders,
         [](Edit& e, const Layout&) { e.U32(0x3c, 0xfffffff0); }},
        {"header: no sections", Check::kHeaders,
         [](Edit& e, const Layout& y) { e.Bytes(y.opt - 20 + 2, {0, 0}); }},
        {"header: 65535 sections", Check::kHeaders,
         [](Edit& e, const Layout& y) { e.Bytes(y.opt - 20 + 2, {0xff, 0xff}); }},
        {"header: not x64", Check::kHeaders,
         [](Edit& e, const Layout& y) { e.Bytes(y.opt - 20, {0x4c, 0x01}); }},
        // Not faults: the same bytes without a branch in front, or aimed at
        // the site's first byte from a short jump, change nothing.
        {"a value that only looks like a jump into the tail", Check::kOk,
         [](Edit& e, const Layout& y) { e.Byte(y.X, 0x90); e.Rel(y.X + 1, y.R + 4); }},
        {"short jump to the site's first byte", Check::kOk,
         [](Edit& e, const Layout& y) { e.Bytes(y.R + 0x3c, {0xeb, 0xc2}); }},
        {"address bytes after the site that look like a short jump", Check::kOk,
         [](Edit& e, const Layout& y) { e.Bytes(y.R + 0x29, {0xeb, 0xd9}); }},
    };
    const std::vector<Fault> more = {
        // The load, and which function holds it.
        {"the one load sits elsewhere in the same function", Check::kLoadOffset,
         [](Edit& e, const Layout& y) { e.Add32(y.L + 4, 4); e.Rel(y.F + 0x6c + 4, y.K); }},
        {"the one load sits between two functions", Check::kFunction,
         [](Edit& e, const Layout& y) {
             e.Add32(y.L + 4, 4);
             e.Bytes(y.gap, {0xf3, 0x0f, 0x10, 0x05}); e.Rel(y.gap + 4, y.K); }},
        {"the one load sits in a chained fragment", Check::kFunction,
         [](Edit& e, const Layout& y) {
             e.Add32(y.L + 4, 4);
             e.Bytes(y.fragment, {0xf3, 0x0f, 0x10, 0x05}); e.Rel(y.fragment + 4, y.K); }},
        {"the tables name another function of that length", Check::kFingerprintMismatch,
         [](Edit& e, const Layout& y) {
             e.U32(y.twin_entry + 8, static_cast<std::uint32_t>(y.unwind));
             e.U32(y.state_map, static_cast<std::uint32_t>(y.twin + 0x120));
             e.U32(y.state_map + 8, static_cast<std::uint32_t>(y.twin + 0x30f));
             e.U32(y.state_map + 16, static_cast<std::uint32_t>(y.twin + 0x335)); }},
        {"a fragment elsewhere names our function as its parent", Check::kFunctionFragment,
         [](Edit& e, const Layout& y) {
             e.U32(y.fragment_parent, static_cast<std::uint32_t>(y.F)); }},
        {"two functions carry the known shape", Check::kFingerprint,
         [](Edit& e, const Layout& y) {
             const std::uint64_t unwind = y.zeros, info = y.zeros + 48, map = y.zeros + 96;
             e.Copy(unwind, y.unwind, 44); e.U32(unwind + 40, static_cast<std::uint32_t>(info));
             e.Copy(info, y.info, 40); e.U32(info + 24, static_cast<std::uint32_t>(map));
             e.U32(map, static_cast<std::uint32_t>(y.twin + 0x120));
             e.U32(map + 8, static_cast<std::uint32_t>(y.twin + 0x30f)); e.U32(map + 12, 1);
             e.U32(map + 16, static_cast<std::uint32_t>(y.twin + 0x335)); e.U32(map + 20, 0xffffffff);
             e.U32(y.twin_entry + 8, static_cast<std::uint32_t>(unwind)); }},
        // Every field of the fingerprint.
        {"a state value differs (first entry)", Check::kFingerprint,
         [](Edit& e, const Layout& y) { e.Add32(y.state_map + 4, 1); }},
        {"a state value differs (last entry)", Check::kFingerprint,
         [](Edit& e, const Layout& y) { e.Add32(y.state_map + 20, 1); }},
        {"state map has four entries", Check::kFingerprint,
         [](Edit& e, const Layout& y) { e.U32(y.info + 20, 4); }},
        {"handler data of another kind", Check::kFingerprint,
         [](Edit& e, const Layout& y) { e.U32(y.info, 0x19930519); }},
        {"unwind code count differs", Check::kFingerprint,
         [](Edit& e, const Layout& y) { e.Byte(y.unwind + 2, 14); }},
        {"frame register set", Check::kFingerprint,
         [](Edit& e, const Layout& y) { e.Byte(y.unwind + 3, 0x05); }},
        {"no handler flag", Check::kFingerprint,
         [](Edit& e, const Layout& y) { e.Byte(y.unwind, 0x01); }},
        {"unwind record of version 2", Check::kFingerprint,
         [](Edit& e, const Layout& y) { e.Byte(y.unwind, 0x02 | (3 << 3)); }},
        {"fragment and handler flags together", Check::kFingerprint,
         [](Edit& e, const Layout& y) { e.Byte(y.unwind, 0x01 | (5 << 3)); }},
        {"handler data at the very end of its section", Check::kFingerprint,
         [](Edit& e, const Layout& y) {
             e.U32(y.unwind + 40, static_cast<std::uint32_t>(y.rdata_end - 8)); }},
        {"state map at the very end of its section", Check::kFingerprint,
         [](Edit& e, const Layout& y) {
             e.U32(y.info + 24, static_cast<std::uint32_t>(y.rdata_end - 8)); }},
        {"our unwind address is odd", Check::kFingerprint,
         [](Edit& e, const Layout& y) { e.Add32(y.entry + 8, 1); }},
        {"our unwind address is near 4 GiB", Check::kFingerprint,
         [](Edit& e, const Layout& y) { e.U32(y.entry + 8, 0xfffffffc); }},
        // More kinds of reference to the limit.
        {"limit only in a discardable section", Check::kConstant,
         [=](Edit& e, const Layout& y) { e.U32(y.K, 0); e.U32(y.discardable + 0x100, kLimit); }},
        {"movups from the limit (no F3)", Check::kOtherReference,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0x90, 0x0f, 0x10, 0x05}); e.Rel(y.X + 4, y.K); }},
        {"mulps by the limit (no F3)", Check::kOtherReference,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0x90, 0x0f, 0x59, 0x05}); e.Rel(y.X + 4, y.K); }},
        {"REX byte but no F3 in front of the load", Check::kOtherReference,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0x90, 0x44, 0x0f, 0x10, 0x05}); e.Rel(y.X + 5, y.K); }},
        {"a field that reaches the limit behind a non-RIP operand", Check::kOtherReference,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0xf3, 0x0f, 0x10, 0x80}); e.Rel(y.X + 4, y.K); }},
        {"lea of the limit", Check::kOtherReference,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0x48, 0x8d, 0x05}); e.Rel(y.X + 3, y.K); }},
        {"VEX cmpss against the limit in memory", Check::kCompare,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0xc5, 0xfa, 0xc2, 0x05}); e.Rel(y.X + 4, y.K, 1);
             e.Byte(y.X + 8, 0x01); }},
        {"integer compare of the limit with a 4-byte operand", Check::kOtherReference,
         [=](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0x81, 0x3d}); e.Rel(y.X + 2, y.K, 4); e.U32(y.X + 6, kLimit); }},
        {"a second load of the float stored just before the limit", Check::kOk,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0xf3, 0x0f, 0x10, 0x05}); e.Rel(y.X + 4, y.K - 4); }},
        {"a value that only looks like a reference with a 1-byte operand", Check::kOk,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0x90, 0x90, 0x90}); e.Rel(y.X + 3, y.K, 1); }},
        {"shufps with the limit and a 1-byte operand", Check::kOtherReference,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.X, {0x0f, 0xc6, 0x05}); e.Rel(y.X + 3, y.K, 1); e.Byte(y.X + 7, 0x00); }},
        // The instructions after the site, byte by byte.
        {"first call is not a call", Check::kCalls,
         [](Edit& e, const Layout& y) { e.Byte(y.L + 0x55, 0xe9); }},
        {"first addss after the site: byte 0", Check::kTailFields,
         [](Edit& e, const Layout& y) { e.Byte(y.R + 0x25, 0xf2); }},
        {"first addss after the site: byte 1", Check::kTailFields,
         [](Edit& e, const Layout& y) { e.Byte(y.R + 0x26, 0x0e); }},
        {"first addss after the site: byte 2", Check::kTailFields,
         [](Edit& e, const Layout& y) { e.Byte(y.R + 0x27, 0x59); }},
        {"first addss after the site: byte 3", Check::kTailFields,
         [](Edit& e, const Layout& y) { e.Byte(y.R + 0x28, 0x05); }},
        {"second addss after the site: byte 0", Check::kTailFields,
         [](Edit& e, const Layout& y) { e.Byte(y.R + 0x33, 0xf2); }},
        {"second addss after the site: byte 3", Check::kTailFields,
         [](Edit& e, const Layout& y) { e.Byte(y.R + 0x36, 0x05); }},
        {"second call after the site is not a call", Check::kTailFields,
         [](Edit& e, const Layout& y) { e.Byte(y.R + 0x64, 0xe9); }},
        {"the compare after the site: byte 0", Check::kTailFields,
         [](Edit& e, const Layout& y) { e.Byte(y.R + 0x69, 0x81); }},
        {"the compare after the site: byte 1", Check::kTailFields,
         [](Edit& e, const Layout& y) { e.Byte(y.R + 0x6a, 0x05); }},
        // The edges of both sweeps.
        {"short jump from 128 bytes before, to the tail's first byte", Check::kTailBranch8,
         [](Edit& e, const Layout& y) { e.Bytes(y.R - 128, {0x74, 0x7f}); }},
        {"short jump from 128 bytes before, to the site itself", Check::kOk,
         [](Edit& e, const Layout& y) { e.Bytes(y.R - 128, {0x74, 0x7e}); }},
        {"short jump from 129 bytes before can only reach the site", Check::kOk,
         [](Edit& e, const Layout& y) { e.Bytes(y.R - 129, {0x74, 0x7f}); }},
        {"short jump from 132 bytes after, to the tail's last byte", Check::kTailBranch8,
         [](Edit& e, const Layout& y) { e.Bytes(y.R + 132, {0xeb, 0x80}); }},
        {"short jump from 133 bytes after lands past the tail", Check::kOk,
         [](Edit& e, const Layout& y) { e.Bytes(y.R + 133, {0xeb, 0x80}); }},
        {"loopne into the tail", Check::kTailBranch8,
         [](Edit& e, const Layout& y) { e.Bytes(y.R + 0x3c, {0xe0, 0xc6}); }},
        {"loope into the tail", Check::kTailBranch8,
         [](Edit& e, const Layout& y) { e.Bytes(y.R + 0x3c, {0xe1, 0xc6}); }},
        {"loop into the tail", Check::kTailBranch8,
         [](Edit& e, const Layout& y) { e.Bytes(y.R + 0x3c, {0xe2, 0xc6}); }},
        {"jrcxz into the tail", Check::kTailBranch8,
         [](Edit& e, const Layout& y) { e.Bytes(y.R + 0x3c, {0xe3, 0xc6}); }},
        {"short jump to the tail's first byte", Check::kTailBranch8,
         [](Edit& e, const Layout& y) { e.Bytes(y.R + 0x3c, {0xeb, 0xc3}); }},
        {"short jump to the tail's last byte", Check::kTailBranch8,
         [](Edit& e, const Layout& y) { e.Bytes(y.R + 0x3c, {0xeb, 0xc8}); }},
        {"short jump to the byte after the tail", Check::kOk,
         [](Edit& e, const Layout& y) { e.Bytes(y.R + 0x3c, {0xeb, 0xc9}); }},
        {"near jump into the tail from the code section's last bytes", Check::kTailBranch32,
         [](Edit& e, const Layout& y) {
             e.Byte(y.code_end - 5, 0xe9); e.Rel(y.code_end - 4, y.R + 3); }},
        {"call into the tail from the code section's first byte", Check::kTailBranch32,
         [](Edit& e, const Layout& y) { e.Byte(y.code_rva, 0xe8); e.Rel(y.code_rva + 1, y.R + 2); }},
        {"conditional jump into the tail from the section's first bytes", Check::kTailBranch32,
         [](Edit& e, const Layout& y) {
             e.Bytes(y.code_rva, {0x0f, 0x85}); e.Rel(y.code_rva + 2, y.R + 5); }},
        // Headers.
        {"header: PE signature wrong", Check::kHeaders,
         [](Edit& e, const Layout& y) { e.Byte(y.pe, 'X'); }},
        {"header: 32-bit optional header", Check::kHeaders,
         [](Edit& e, const Layout& y) { e.Bytes(y.opt, {0x0b, 0x01}); }},
        {"header: only three data directories", Check::kHeaders,
         [](Edit& e, const Layout& y) { e.U32(y.opt + 108, 3); }},
        {"header: code section claims 2 GiB", Check::kHeaders,
         [](Edit& e, const Layout& y) { e.U32(y.sections + 40ull * y.code + 8, 0x7fffffff); }},
        {"header: code section near 4 GiB", Check::kHeaders,
         [](Edit& e, const Layout& y) { e.U32(y.sections + 40ull * y.code + 12, 0xfffff000); }},
        {"header: the limit's section has size 0", Check::kHeaders,
         [](Edit& e, const Layout& y) { e.U32(y.sections + 40ull * y.rdata_index + 8, 0); }},
    };
    v.insert(v.end(), more.begin(), more.end());
    for (int k = 0; k < kSiteWindow; ++k) {
        v.push_back({"a site byte differs", Check::kSite,
                     [k](Edit& e, const Layout& y) { e.Byte(y.R + k, 0xcc); }});
    }
    return v;
}


const std::uint8_t* g_base = nullptr;
std::uint64_t g_refuse_at = 0;

// Readable everywhere except the one range that starts at `g_refuse_at`.
bool ReadableBut(const std::uint8_t* at, std::uint64_t) {
    return static_cast<std::uint64_t>(at - g_base) != g_refuse_at;
}

void RunFaults(std::vector<std::uint8_t>& image, const Found& clean) {
    Layout y{};
    if (!Describe(image, clean, y)) {
        Expect(false, "could not describe the image for the fault cases");
        return;
    }
    int planted = 0;
    for (const Fault& fault : Faults()) {
        Found got;
        {
            Edit e(image);
            fault.plant(e, y);
            got = Run(image);
        }
        const bool ok = got.refused == fault.expect;
        std::printf("  %-4s %-62s -> %s\n", ok ? "ok" : "FAIL", fault.name,
                    CheckName(got.refused));
        Expect(ok, fault.name, CheckName(fault.expect));
        ++planted;
    }
    std::printf("  (%d cases planted)\n", planted);
    // Truncated images, as exact-size copies so an over-read is caught by
    // the sanitizer: each must refuse, not fault.
    for (std::uint64_t size : {0x40ull, 0x188ull, 0x400ull, 0x1000ull, 0x1004ull,
                               static_cast<unsigned long long>(y.code_end - 1),
                               static_cast<unsigned long long>(y.K + 2),
                               static_cast<unsigned long long>(y.table + 6),
                               static_cast<unsigned long long>(image.size() / 2)}) {
        const std::vector<std::uint8_t> cut(image.begin(), image.begin() + size);
        const Found got = Run(cut);
        std::printf("  %-4s image of exactly 0x%llx bytes -> %s\n",
                    got.ok() ? "FAIL" : "ok",
                    static_cast<unsigned long long>(size), CheckName(got.refused));
        Expect(!got.ok(), "truncated image accepted");
    }
    Image none;
    Expect(!Locate(none).ok(), "an empty image is refused");
    // Each range the search reads is asked about first, and a "no" refuses
    // with that range's offset.
    Headers h;
    ParseHeaders(image.data(), 0x1000, h);
    std::vector<std::uint64_t> ranges = {0};
    for (int i = 0; i < h.n_sections; ++i) {
        const std::uint32_t flags = h.sections[i].flags;
        if (flags == 0x60000020 || flags == 0x40000040) ranges.push_back(h.sections[i].rva);
    }
    g_base = image.data();
    for (std::uint64_t rva : ranges) {
        Image im;
        im.base = image.data();
        im.size = image.size();
        im.readable = ReadableBut;
        g_refuse_at = rva;
        const Found got = Locate(im);
        const bool ok = got.refused == Check::kUnreadable && got.at == rva;
        std::printf("  %-4s range at +0x%llx unreadable -> %s at +0x%x\n", ok ? "ok" : "FAIL",
                    static_cast<unsigned long long>(rva), CheckName(got.refused), got.at);
        Expect(ok, "unreadable range");
    }
    // After every fault the untouched image must still give the same answer.
    const Found again = Run(image);
    Expect(again.ok() && again.site == clean.site, "pristine image after the faults");
}

// Hostile FILES: the mapper and the locator must cope with whatever is left.
void RunFileFaults(const std::vector<std::uint8_t>& file) {
    for (std::size_t size : {std::size_t{0}, std::size_t{0x3f}, std::size_t{0x100},
                             std::size_t{0x400}, std::size_t{0x1000}, file.size() / 2}) {
        if (size > file.size()) continue;
        std::vector<std::uint8_t> cut(file.begin(), file.begin() + size), image;
        const bool mapped = MapFile(cut, image);
        const Found got = mapped ? Run(image) : Found();
        std::printf("  %-4s file cut to 0x%zx bytes -> %s\n", got.ok() ? "FAIL" : "ok",
                    size, mapped ? CheckName(got.refused) : "not mapped");
        Expect(!got.ok(), "truncated file accepted");
    }
    // A section whose raw data lies past the end of the file.
    std::vector<std::uint8_t> bad(file), image;
    const std::uint64_t pe = Get32(bad, 0x3c);
    std::uint16_t opt_size;
    std::memcpy(&opt_size, bad.data() + pe + 20, 2);
    // More sections than the parser has room for, in a file big enough to
    // hold such a table: the count itself must be refused.
    std::vector<std::uint8_t> crowded(file);
    crowded[pe + 6] = 200;
    crowded[pe + 7] = 0;
    Headers none;
    Expect(!ParseHeaders(crowded.data(), crowded.size(), none),
           "a file with 200 sections is refused");
    // Files that end inside the section table.
    const std::size_t table = pe + 24 + opt_size;
    for (std::size_t size = table - 8; size <= table + 40 * 9 + 8; size += 20) {
        std::vector<std::uint8_t> cut(file.begin(), file.begin() + size), mapped;
        const Found got = MapFile(cut, mapped) ? Run(mapped) : Found();
        Expect(!got.ok(), "file cut inside the section table accepted");
    }
    const std::uint32_t far = 0x7ffffff0;
    std::memcpy(bad.data() + pe + 24 + opt_size + 20, &far, 4);
    const bool mapped = MapFile(bad, image);
    const Found got = mapped ? Run(image) : Found();
    std::printf("  %-4s code section's raw data past the end of the file -> %s\n",
                got.ok() ? "FAIL" : "ok", mapped ? CheckName(got.refused) : "not mapped");
    Expect(!got.ok(), "file with a stray section accepted");
}

// ---- the stub, executed -------------------------------------------------

#ifdef HAVE_EXEC_TEST
// A stand-in for the engine's function around the site: it sets the flags
// and xmm1 before the site, and records what the code after it saw.
void RunStub() {
    const std::size_t kPage = 0x1000;
    void* mem = mmap(nullptr, 3 * kPage, PROT_READ | PROT_WRITE | PROT_EXEC,
                     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED) {
        Expect(false, "mmap for the stub test");
        return;
    }
    auto* fn = static_cast<std::uint8_t*>(mem);
    std::uint8_t* stub = fn + kPage;
    std::uint8_t* counter = fn + 2 * kPage;
    const std::uint8_t before[] = {
        0x53, 0x41, 0x56,                    // push rbx; push r14
        0x49, 0x89, 0xfe,                    // mov r14, rdi
        0x66, 0x0f, 0x76, 0xc9,              // pcmpeqd xmm1, xmm1
        0x31, 0xc0, 0x83, 0xf8, 0x01};       // xor eax, eax; cmp eax, 1
    const std::uint8_t after[] = {
        0x0f, 0x95, 0xc0,                    // setne al (from the site's test)
        0x41, 0x88, 0x46, 0x29,              // mov [r14+29h], al
        0x66, 0x41, 0x0f, 0xd6, 0x4e, 0x20,  // movq [r14+20h], xmm1
        0x41, 0xc6, 0x46, 0x28, 0x01};       // mov byte ptr [r14+28h], 1
    const std::uint8_t leave[] = {
        0xb8, 0x55, 0x00, 0x00, 0x00,        // mov eax, 55h
        0x41, 0x5e, 0x5b, 0xc3};             // pop r14; pop rbx; ret
    const std::size_t site = sizeof(before);
    const std::size_t exit_at = site + kSiteWindow + sizeof(after);

    std::uint8_t cell[0x80] = {};
    cell[0x40] = 1;
    std::uint8_t closure[0x40] = {};
    const std::uint8_t* cell_ptr = cell;
    std::memcpy(closure + 0x18, &cell_ptr, sizeof(cell_ptr));

    for (Mode mode : {Mode::kLook, Mode::kCount, Mode::kPatch}) {
        std::memset(fn, 0xcc, 3 * kPage);
        std::memcpy(fn, before, sizeof(before));
        std::memcpy(fn + site, kSiteBytes, kSiteWindow);
        std::memcpy(fn + site + kSiteWindow, after, sizeof(after));
        std::memcpy(fn + exit_at, leave, sizeof(leave));
        std::memset(counter, 0, 8);
        if (mode != Mode::kLook) {
            Detour d;
            const bool built = BuildDetour(
                mode, reinterpret_cast<std::uint64_t>(fn + site),
                reinterpret_cast<std::uint64_t>(fn + exit_at),
                reinterpret_cast<std::uint64_t>(stub),
                reinterpret_cast<std::uint64_t>(counter), fn + site, d);
            Expect(built, "BuildDetour");
            Expect(d.stub_len == (mode == Mode::kCount ? 20 : 13), "stub length");
            std::memcpy(stub, d.stub, d.stub_len);
            std::memcpy(fn + site, d.site, sizeof(d.site));
        }
        std::memset(closure + 0x20, 0xee, 0x10);
        const int kCalls = 3;
        std::uint64_t ret = 0;
        auto call = reinterpret_cast<std::uint64_t (*)(void*)>(fn);
        for (int k = 0; k < kCalls; ++k) ret = call(closure);
        std::uint64_t count, xmm;
        std::memcpy(&count, counter, 8);
        std::memcpy(&xmm, closure + 0x20, 8);
        const bool moved = closure[0x28] == 1;
        std::printf("  mode %d: returned 0x%llx, counter %llu, moved %d, xmm1 %s, "
                    "test seen %d\n", static_cast<int>(mode),
                    static_cast<unsigned long long>(ret),
                    static_cast<unsigned long long>(count), moved ? 1 : 0,
                    xmm == 0 ? "zero" : "not zero", closure[0x29]);
        Expect(ret == 0x55, "the function returns through its exit");
        Expect(count == (mode == Mode::kLook ? 0u : kCalls), "counter");
        if (mode == Mode::kPatch) {
            Expect(!moved && closure[0x28] == 0xee, "mode 2 skips the block");
        } else {
            Expect(moved && xmm == 0 && closure[0x29] == 1,
                   "modes 0 and 1 run the block unchanged");
        }
    }
    // Out of reach: a counter more than 2 GB from the stub must be refused.
    Detour d;
    Expect(!BuildDetour(Mode::kCount, 0x1000, 0x1100, 0x2000, 0x2000 + (3ull << 30),
                        kSiteBytes, d), "far counter refused");
    Expect(!BuildDetour(Mode::kPatch, 0x1000, 0x1100, 0x1000 + (3ull << 30), 0x2000,
                        kSiteBytes, d), "far stub refused");
    Expect(!BuildDetour(Mode::kLook, 0x1000, 0x1100, 0x2000, 0x3000, kSiteBytes, d),
           "mode 0 builds nothing");
    munmap(mem, 3 * kPage);
}
#endif

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: locate_test <exe> [--site 0x..] "
                     "[--refuse <check>] [--fingerprint 0x..] [--faults]\n");
        return 2;
    }
    const std::string path = argv[1];
    std::uint32_t want_site = 0, want_fingerprint = 0;
    const char* want_refuse = nullptr;
    bool faults = false;
    for (int i = 2; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--faults") faults = true;
        else if (a == "--site" && i + 1 < argc) want_site = std::strtoul(argv[++i], nullptr, 16);
        else if (a == "--fingerprint" && i + 1 < argc) want_fingerprint = std::strtoul(argv[++i], nullptr, 16);
        else if (a == "--refuse" && i + 1 < argc) want_refuse = argv[++i];
        else { std::fprintf(stderr, "unknown argument %s\n", a.c_str()); return 2; }
    }

    std::vector<std::uint8_t> file, image;
    if (!ReadFile(path, file)) {
        std::fprintf(stderr, "cannot read %s\n", path.c_str());
        return 2;
    }
    if (!MapFile(file, image)) {
        std::fprintf(stderr, "not a 64-bit PE file: %s\n", path.c_str());
        return 2;
    }
    std::printf("%s (%zu bytes, mapped 0x%zx)\n", path.c_str(), file.size(), image.size());
    const Found f = Run(image);
    Report(f, Line);

    if (want_site) {
        Expect(f.ok() && f.site == want_site, "--site");
    }
    if (want_refuse) {
        Expect(!f.ok() && std::strcmp(CheckName(f.refused), want_refuse) == 0, "--refuse");
    }
    if (want_fingerprint) {
        Expect(f.n_fingerprint == 1 && f.fingerprint_fn == want_fingerprint, "--fingerprint");
    }
    if (faults) {
        if (!f.ok()) {
            Expect(false, "--faults needs a file the locator accepts");
        } else {
            std::printf("planted faults:\n");
            RunFaults(image, f);
            RunFileFaults(file);
        }
    }
#ifdef HAVE_EXEC_TEST
    std::printf("the stub, executed against a mock of the site:\n");
    RunStub();
#endif
    std::printf("%s: %d of %d expectations held\n", g_failed ? "FAILED" : "PASSED",
                g_ran - g_failed, g_ran);
    return g_failed ? 1 : 0;
}
