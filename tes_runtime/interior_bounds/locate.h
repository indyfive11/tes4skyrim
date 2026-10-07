// Finds the one place where Skyrim moves an out-of-range interior reference.
//
// Portable: no Windows API, no struct overlays. It reads a PE image laid out
// by RVA -- the game's own module in memory, or a file the test harness
// mapped -- and either names exactly one site or refuses with the name of the
// check that failed. It never guesses, and it reports no code bytes.
//
// See: README.md ("For developers")

#pragma once

#include <cstdint>

namespace interior_bounds {

// A PE image laid out by RVA. `readable`, when set, is asked before a range
// is read; a range it turns down is a refusal, not a fault.
struct Image {
    const std::uint8_t* base = nullptr;
    std::uint64_t       size = 0;
    bool (*readable)(const std::uint8_t* at, std::uint64_t len) = nullptr;
};

struct SectionInfo {
    std::uint32_t rva = 0;
    std::uint32_t vsize = 0;
    std::uint32_t raw_ptr = 0;
    std::uint32_t raw_size = 0;
    std::uint32_t flags = 0;
};

constexpr int kMaxSections = 96;

struct Headers {
    int           n_sections = 0;
    SectionInfo   sections[kMaxSections];
    std::uint32_t table_rva = 0;      // exception directory (function table)
    std::uint32_t table_size = 0;
    std::uint32_t size_of_image = 0;
};

// Parses the headers found in the first `n` bytes at `p` (a file's first
// bytes and a loaded module's first page hold the same thing).
bool ParseHeaders(const std::uint8_t* p, std::uint64_t n, Headers& out);

enum class Check : int {
    kOk = 0,
    kHeaders,            // not a 64-bit PE image, or headers out of bounds
    kUnreadable,         // a range the search must read is not readable
    kCodeSection,        // not exactly one code section
    kDataSections,       // no read-only data section
    kConstant,           // the float 30000.0 is not there exactly once
    kFunctionTable,      // function table missing, misplaced or unsorted
    kFingerprint,        // not exactly one function with the known shape
    kLoad,               // not exactly one load of the constant
    kCompare,            // something compares against the constant in memory
    kOtherReference,     // a reference to the constant of an unknown kind
    kFunction,           // the load is in no function, or in a fragment
    kFunctionLength,     // that function has the wrong length
    kFingerprintMismatch,// the load is not in the fingerprinted function
    kFunctionFragment,   // another table entry continues that function
    kLoadOffset,         // the load is not where it should be in the function
    kLoadForm,
    kAbsoluteValue,      // the bytes that take |x| before the load
    kComparePairs,       // the two compare-and-branch pairs after the load
    kFlagTest,           // the test that accepts an in-range reference
    kBranches,           // the four branches to the site disagree
    kJumpBefore,         // no jump to the exit right before the site
    kSite,               // the site's own eleven bytes
    kExit,               // the exit's first six bytes
    kCalls,              // the two set-position calls disagree
    kTailFields,         // an instruction after the site is not the known one
    kSiteEntries,        // not exactly four branches reach the site
    kTailBranch32,       // a near branch lands inside the site's tail
    kTailBranch8,        // a short branch lands inside the site's tail
    kCount_
};

const char* CheckName(Check c);

// What the search found, as far as it got. Every address is an RVA.
struct Found {
    Check         refused = Check::kHeaders;
    std::uint32_t at = 0;             // what the refusal is about
    int           mismatch = -1;      // first differing byte of a window
    int           differing = 0;      // how many bytes of that window differ
    int           window = 0;         // that window's length
    int           passed = 0;         // self-checks passed before stopping

    int           n_code = 0, n_data = 0;
    std::uint32_t code_rva = 0, code_size = 0;
    int           n_constant = 0;
    std::uint32_t constant = 0;
    std::uint32_t n_functions = 0;
    int           n_fingerprint = 0;
    std::uint32_t fingerprint_fn = 0;
    int           n_load = 0, n_mulss = 0, n_compare = 0, n_other = 0;
    std::uint32_t load = 0;
    std::uint32_t fn_begin = 0, fn_end = 0;
    int           n_site_entries = 0;
    std::uint32_t site = 0, exit = 0, move_fn = 0;

    bool ok() const { return refused == Check::kOk; }
};

// Self-checks a full pass runs (the count the report prints).
extern const int kSelfChecks;

// The site's bytes on a build this plugin knows: two whole instructions (the
// seven a detour replaces) and the test that follows them.
constexpr int kSiteLen = 7;
constexpr int kSiteWindow = 11;
extern const std::uint8_t kSiteBytes[kSiteWindow];

Found Locate(const Image& image);

// Writes the report one line at a time: what was found, then `ONE SITE` or
// `REFUSE`. Offsets and counts only.
void Report(const Found& f, void (*line)(const char* text));

}  // namespace interior_bounds
