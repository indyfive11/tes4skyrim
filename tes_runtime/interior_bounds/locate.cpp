#include "locate.h"

#include <cstdio>
#include <cstring>

namespace interior_bounds {

namespace {

// Section flags (IMAGE_SCN_*).
constexpr std::uint32_t kCntCode = 0x00000020;
constexpr std::uint32_t kCntData = 0x00000040;
constexpr std::uint32_t kDiscardable = 0x02000000;
constexpr std::uint32_t kExecute = 0x20000000;
constexpr std::uint32_t kRead = 0x40000000;
constexpr std::uint32_t kWrite = 0x80000000;

// 30000.0f, the limit an interior reference's X and Y are held to.
constexpr std::uint32_t kLimitBits = 0x46EA6000;

// The function that applies the limit, as the build's own tables describe
// it: its length, its unwind record and the three offsets of its state map.
constexpr std::uint32_t kFunctionLength = 0x363;
constexpr std::uint8_t  kPrologue = 49;
constexpr std::uint8_t  kUnwindCodes[30] = {
    0x31, 0x88, 0x06, 0x00, 0x2c, 0x78, 0x07, 0x00, 0x28, 0x68,
    0x08, 0x00, 0x24, 0x74, 0x17, 0x00, 0x20, 0x64, 0x16, 0x00,
    0x1c, 0x34, 0x14, 0x00, 0x10, 0x01, 0x12, 0x00, 0x09, 0xe0};
constexpr std::uint32_t kStateOffsets[3] = {0x120, 0x30f, 0x335};
constexpr std::int32_t  kStates[3] = {0, 1, -1};

// Offsets inside that function. F = its start, L = the load of the limit,
// R = the site (the block that moves a reference), J = the exit.
constexpr std::uint32_t kLoadAt = 0x0f3;                 // L - F
constexpr std::uint32_t kSiteAt = 0x277;                 // R - F
constexpr std::uint32_t kExitAt = 0x30f;                 // J - F
constexpr std::uint32_t kBranchAt[4] = {0x35, 0xdb, 0xed, 0xf6};   // from L
constexpr std::uint8_t  kBranchOp[4] = {0x84, 0x83, 0x83, 0x85};
constexpr std::uint32_t kFirstCallAt = 0x55;             // from L

constexpr std::uint8_t kLoadForm[] = {0xf3, 0x0f, 0x10, 0x3d};
constexpr std::uint8_t kAbsoluteValue[] = {             // at L - 9
    0xf3, 0x41, 0x0f, 0x10, 0x40, 0x54, 0x0f, 0x54, 0xc6};
constexpr std::uint8_t kComparePairs[] = {              // at L + 8
    0x0f, 0x2f, 0xc7, 0x77, 0x17, 0xf3, 0x41, 0x0f, 0x10, 0x40,
    0x58, 0x0f, 0x54, 0xc6, 0x0f, 0x2f, 0xc7, 0x77, 0x09};
constexpr std::uint8_t kFlagTest[] = {0x40, 0x84, 0xff, 0x0f, 0x84};  // L+0x1b
constexpr std::uint8_t kExitBytes[] = {0x48, 0x8b, 0x0e, 0x48, 0x85, 0xc9};

// Instructions after the site that carry an address outside the function.
// Their four address bytes differ from build to build, so the short-branch
// sweep steps over them; the bytes in front are checked instead.
struct TailField {
    std::uint32_t at;          // of the opcode bytes, from R
    std::uint8_t  op[4];
    int           op_len;
    std::uint32_t field;       // of the four address bytes, from R
};
constexpr TailField kTailFields[] = {
    {0x25, {0xf3, 0x0f, 0x58, 0x0d}, 4, 0x29},
    {0x33, {0xf3, 0x0f, 0x58, 0x15}, 4, 0x37},
    {0x52, {0xe8}, 1, 0x53},
    {0x64, {0xe8}, 1, 0x65},
    {0x69, {0x80, 0x3d}, 2, 0x6b},
};
constexpr std::uint32_t kTailEnd = 0x70;                // from R

// A short branch at p reaches p + 2 - 128 .. p + 2 + 127.
constexpr std::uint32_t kShortBack = 128;
constexpr std::uint32_t kShortAhead = 132;

std::uint16_t U16(const std::uint8_t* p) {
    std::uint16_t v;
    std::memcpy(&v, p, sizeof(v));
    return v;
}

std::uint32_t U32(const std::uint8_t* p) {
    std::uint32_t v;
    std::memcpy(&v, p, sizeof(v));
    return v;
}

std::int32_t I32(const std::uint8_t* p) {
    std::int32_t v;
    std::memcpy(&v, p, sizeof(v));
    return v;
}

bool Within(std::uint64_t rva, std::uint64_t len, std::uint64_t begin,
            std::uint64_t size) {
    return rva >= begin && len <= size && rva - begin <= size - len;
}

struct Scan {
    Image              image;
    Headers            headers;
    const SectionInfo* code = nullptr;
    const SectionInfo* data[kMaxSections] = {};
    int                n_data = 0;

    // `len` bytes at `rva` inside the code section, else null.
    const std::uint8_t* Code(std::uint64_t rva, std::uint64_t len) const {
        if (!code || !Within(rva, len, code->rva, code->vsize)) return nullptr;
        return image.base + rva;
    }

    // `len` bytes at `rva` inside one read-only data section, else null.
    const std::uint8_t* Data(std::uint64_t rva, std::uint64_t len) const {
        for (int i = 0; i < n_data; ++i) {
            if (Within(rva, len, data[i]->rva, data[i]->vsize)) {
                return image.base + rva;
            }
        }
        return nullptr;
    }
};

bool Refuse(Found& f, Check check, std::uint32_t at) {
    f.refused = check;
    f.at = at;
    return false;
}

// Compares a window with what the known builds hold there. A mismatch is
// reported as a position and a count, never as bytes.
bool Same(Found& f, Check check, std::uint32_t at, const std::uint8_t* got,
          const std::uint8_t* want, int n) {
    int first = -1, differing = 0;
    for (int i = 0; i < n; ++i) {
        if (got[i] == want[i]) continue;
        if (first < 0) first = i;
        ++differing;
    }
    if (!differing) return true;
    f.mismatch = first;
    f.differing = differing;
    f.window = n;
    return Refuse(f, check, at);
}

bool IsCode(std::uint32_t flags) {
    return (flags & kCntCode) && (flags & kExecute) && !(flags & kWrite);
}

bool IsReadOnlyData(std::uint32_t flags) {
    return (flags & kCntData) && (flags & kRead)
           && !(flags & (kWrite | kExecute | kDiscardable));
}

bool Open(const Image& image, Scan& s, Found& f) {
    s.image = image;
    if (!image.base) return Refuse(f, Check::kHeaders, 0);
    const std::uint64_t head = image.size < 0x1000 ? image.size : 0x1000;
    if (image.readable && !image.readable(image.base, head)) {
        return Refuse(f, Check::kUnreadable, 0);
    }
    if (!ParseHeaders(image.base, head, s.headers)) {
        return Refuse(f, Check::kHeaders, 0);
    }
    for (int i = 0; i < s.headers.n_sections; ++i) {
        const SectionInfo& x = s.headers.sections[i];
        const bool code = IsCode(x.flags);
        if (!code && !IsReadOnlyData(x.flags)) continue;
        if (!x.vsize || !Within(x.rva, x.vsize, 0, image.size)) {
            return Refuse(f, Check::kHeaders, x.rva);
        }
        if (code) {
            s.code = &x;
            ++f.n_code;
        } else {
            s.data[s.n_data++] = &x;
        }
    }
    f.n_data = s.n_data;
    if (f.n_code != 1) return Refuse(f, Check::kCodeSection, 0);
    if (!s.n_data) return Refuse(f, Check::kDataSections, 0);
    f.code_rva = s.code->rva;
    f.code_size = s.code->vsize;
    if (!image.readable) return true;
    if (!image.readable(image.base + s.code->rva, s.code->vsize)) {
        return Refuse(f, Check::kUnreadable, s.code->rva);
    }
    for (int i = 0; i < s.n_data; ++i) {
        if (!image.readable(image.base + s.data[i]->rva, s.data[i]->vsize)) {
            return Refuse(f, Check::kUnreadable, s.data[i]->rva);
        }
    }
    return true;
}

// The limit is a lone 4-byte float; a compiler aligns it to four bytes.
bool FindConstant(const Scan& s, Found& f) {
    for (int i = 0; i < s.n_data; ++i) {
        const SectionInfo& d = *s.data[i];
        const std::uint8_t* p = s.image.base + d.rva;
        for (std::uint64_t off = (4 - d.rva % 4) % 4; off + 4 <= d.vsize;
             off += 4) {
            if (U32(p + off) != kLimitBits) continue;
            if (!f.n_constant) f.constant = d.rva + static_cast<std::uint32_t>(off);
            ++f.n_constant;
        }
    }
    if (f.n_constant != 1) return Refuse(f, Check::kConstant, f.constant);
    return true;
}

// The function table (12 bytes an entry: begin, end, unwind record) must be
// sorted and free of overlaps, or a lookup in it means nothing.
bool CheckTable(const Scan& s, Found& f, const std::uint8_t*& table) {
    const Headers& h = s.headers;
    if (!h.table_rva || !h.table_size || h.table_size % 12) {
        return Refuse(f, Check::kFunctionTable, h.table_rva);
    }
    table = s.Data(h.table_rva, h.table_size);
    if (!table) return Refuse(f, Check::kFunctionTable, h.table_rva);
    f.n_functions = h.table_size / 12;
    std::uint32_t prev_end = 0;
    for (std::uint32_t i = 0; i < f.n_functions; ++i) {
        const std::uint32_t begin = U32(table + 12 * i);
        const std::uint32_t end = U32(table + 12 * i + 4);
        if (begin >= end || begin < prev_end) {
            return Refuse(f, Check::kFunctionTable, begin);
        }
        prev_end = end;
    }
    return true;
}

bool IsChained(const Scan& s, std::uint32_t unwind, bool& chained) {
    if (unwind & 3) return false;
    const std::uint8_t* h = s.Data(unwind, 4);
    if (!h) return false;
    chained = ((h[0] >> 3) & 4) != 0;
    return true;
}

// True when the tables alone say this entry is the function: unwind record
// and state map as known. Reads no code.
bool HasKnownShape(const Scan& s, std::uint32_t begin, std::uint32_t unwind) {
    if (unwind & 3) return false;
    // Header, 15 codes padded to 16 slots, handler, handler data.
    const std::uint8_t* h = s.Data(unwind, 4 + 32 + 8);
    if (!h) return false;
    const unsigned flags = h[0] >> 3;
    if ((h[0] & 7) != 1 || (flags & 4) || !(flags & 3)) return false;
    if (h[1] != kPrologue || h[2] != sizeof(kUnwindCodes) / 2 || h[3] != 0) {
        return false;
    }
    if (std::memcmp(h + 4, kUnwindCodes, sizeof(kUnwindCodes)) != 0) return false;
    const std::uint8_t* info = s.Data(U32(h + 4 + 32 + 4), 40);
    if (!info) return false;
    const std::uint32_t magic = U32(info);
    if (magic < 0x19930520 || magic > 0x19930522) return false;
    if (U32(info + 20) != 3) return false;
    const std::uint8_t* map = s.Data(U32(info + 24), 3 * 8);
    if (!map) return false;
    for (int k = 0; k < 3; ++k) {
        if (U32(map + 8 * k) != begin + kStateOffsets[k]) return false;
        if (I32(map + 8 * k + 4) != kStates[k]) return false;
    }
    return true;
}

bool Fingerprint(const Scan& s, Found& f, const std::uint8_t* table) {
    for (std::uint32_t i = 0; i < f.n_functions; ++i) {
        const std::uint32_t begin = U32(table + 12 * i);
        if (U32(table + 12 * i + 4) - begin != kFunctionLength) continue;
        if (!HasKnownShape(s, begin, U32(table + 12 * i + 8))) continue;
        if (!f.n_fingerprint) f.fingerprint_fn = begin;
        ++f.n_fingerprint;
    }
    if (f.n_fingerprint != 1) {
        return Refuse(f, Check::kFingerprint, f.fingerprint_fn);
    }
    return true;
}

enum class Ref { kLoad, kMulss, kCompare, kOther };

// Classifies the instruction whose RIP-relative address field starts at
// c[i] (i >= 3). `start` gets the instruction's first byte for a load.
Ref Classify(const std::uint8_t* c, std::uint64_t i, std::uint64_t& start) {
    if ((c[i - 1] & 0xC7) != 0x05 || c[i - 3] != 0x0F) return Ref::kOther;
    const std::uint8_t op = c[i - 2];
    // The F3 prefix comes before an optional REX byte.
    std::uint8_t prefix = i >= 4 ? c[i - 4] : 0;
    start = i - 4;
    if ((prefix & 0xF0) == 0x40 && i >= 5) {
        prefix = c[i - 5];
        start = i - 5;
    }
    if (op == 0x10 && prefix == 0xF3) return Ref::kLoad;
    if (op == 0x59 && prefix == 0xF3) return Ref::kMulss;
    if (op == 0x2F || op == 0x2E || op == 0xC2) return Ref::kCompare;
    return Ref::kOther;
}

// Every reference to the limit from code, by kind. Exactly one may be a
// load; a compare against memory or an unknown kind could be a second copy
// of the rule, so either refuses.
bool FindReferences(const Scan& s, Found& f) {
    const std::uint8_t* c = s.image.base + s.code->rva;
    const std::uint64_t n = s.code->vsize;
    const std::int64_t want = f.constant;
    std::uint32_t compare_at = 0, other_at = 0;
    for (std::uint64_t i = 3; i + 4 <= n; ++i) {
        const std::int64_t next = static_cast<std::int64_t>(s.code->rva + i + 4);
        const std::int64_t target = next + I32(c + i);
        const std::uint32_t here = s.code->rva + static_cast<std::uint32_t>(i);
        if (target + 1 == want || target + 4 == want) {
            // The instruction may end in an operand after the address field,
            // which moves what the field is relative to. One byte: cmpss and
            // cmpps (opcode C2) compare, anything else is unknown. Four
            // bytes: only the integer forms that take one count -- without
            // that test every load of the float stored just before the
            // limit would look like a reference to the limit.
            if ((c[i - 1] & 0xC7) != 0x05) continue;
            const std::uint8_t op = c[i - 2];
            if (target + 1 == want && op == 0xC2) {
                if (!f.n_compare) compare_at = here;
                ++f.n_compare;
            } else if (target + 1 == want || op == 0x81 || op == 0xC7
                       || op == 0xF7 || op == 0x69) {
                if (!f.n_other) other_at = here;
                ++f.n_other;
            }
            continue;
        }
        if (target != want) continue;
        std::uint64_t start = 0;
        switch (Classify(c, i, start)) {
        case Ref::kLoad:
            if (!f.n_load) f.load = s.code->rva + static_cast<std::uint32_t>(start);
            ++f.n_load;
            break;
        case Ref::kMulss:
            ++f.n_mulss;
            break;
        case Ref::kCompare:
            if (!f.n_compare) compare_at = here;
            ++f.n_compare;
            break;
        case Ref::kOther:
            if (!f.n_other) other_at = here;
            ++f.n_other;
            break;
        }
    }
    if (f.n_load != 1) return Refuse(f, Check::kLoad, f.load);
    if (f.n_compare) return Refuse(f, Check::kCompare, compare_at);
    if (f.n_other) return Refuse(f, Check::kOtherReference, other_at);
    return true;
}

// The table entry that holds the load must be a whole function of the known
// length, the one the fingerprint named, with no fragment elsewhere.
bool EnclosingFunction(const Scan& s, Found& f, const std::uint8_t* table) {
    std::uint32_t lo = 0, hi = f.n_functions;
    while (lo < hi) {
        const std::uint32_t mid = lo + (hi - lo) / 2;
        if (U32(table + 12 * mid) <= f.load) lo = mid + 1; else hi = mid;
    }
    if (!lo) return Refuse(f, Check::kFunction, f.load);
    const std::uint8_t* entry = table + 12 * (lo - 1);
    const std::uint32_t begin = U32(entry), end = U32(entry + 4);
    if (f.load >= end) return Refuse(f, Check::kFunction, f.load);
    bool chained = false;
    if (!IsChained(s, U32(entry + 8), chained) || chained) {
        return Refuse(f, Check::kFunction, begin);
    }
    f.fn_begin = begin;
    f.fn_end = end;
    if (end - begin != kFunctionLength || !s.Code(begin, kFunctionLength)) {
        return Refuse(f, Check::kFunctionLength, begin);
    }
    if (begin != f.fingerprint_fn) {
        return Refuse(f, Check::kFingerprintMismatch, begin);
    }
    for (std::uint32_t i = 0; i < f.n_functions; ++i) {
        const std::uint32_t unwind = U32(table + 12 * i + 8);
        if (!IsChained(s, unwind, chained) || !chained) continue;
        const std::uint8_t* h = s.Data(unwind, 4);
        const std::uint32_t tail = unwind + 4 + ((h[2] + 1u) & ~1u) * 2;
        const std::uint8_t* parent = s.Data(tail, 12);
        if (parent && U32(parent) == begin) {
            return Refuse(f, Check::kFunctionFragment, U32(table + 12 * i));
        }
    }
    return true;
}

bool IsNearBranch(const std::uint8_t* c, std::uint64_t i) {
    if (c[i - 1] == 0xE8 || c[i - 1] == 0xE9) return true;
    return i >= 2 && c[i - 2] == 0x0F && (c[i - 1] & 0xF0) == 0x80;
}

bool IsShortBranch(std::uint8_t op) {
    return op == 0xEB || (op & 0xF0) == 0x70 || (op >= 0xE0 && op <= 0xE3);
}

// Nothing may land inside the bytes a detour overwrites, other than at
// their start: exactly the four known branches reach the site, and no near
// branch anywhere in the code section reaches the six bytes after it.
bool SweepNear(const Scan& s, Found& f) {
    const std::uint8_t* c = s.image.base + s.code->rva;
    const std::uint64_t n = s.code->vsize;
    const std::int64_t site = f.site;
    for (std::uint64_t i = 1; i + 4 <= n; ++i) {
        const std::int64_t target =
            static_cast<std::int64_t>(s.code->rva + i + 4) + I32(c + i);
        if (target < site || target > site + kSiteLen - 1) continue;
        if (!IsNearBranch(c, i)) continue;
        if (target == site) {
            ++f.n_site_entries;
            continue;
        }
        return Refuse(f, Check::kTailBranch32,
                      s.code->rva + static_cast<std::uint32_t>(i) - 1);
    }
    if (f.n_site_entries != 4) return Refuse(f, Check::kSiteEntries, f.site);
    f.passed += 2;
    return true;
}

bool InTailField(std::uint32_t from_site) {
    for (const TailField& t : kTailFields) {
        if (from_site >= t.field && from_site < t.field + 4) return true;
    }
    return false;
}

bool SweepShort(Found& f, const std::uint8_t* fn) {
    const std::uint32_t site = f.site - f.fn_begin;
    const std::uint32_t first = site > kShortBack ? site - kShortBack : 0;
    std::uint32_t last = site + kShortAhead;
    if (last > kFunctionLength - 2) last = kFunctionLength - 2;
    for (std::uint32_t p = first; p <= last; ++p) {
        // A byte inside an address field is not an opcode. The byte in
        // front of each field is one the tail-field check has pinned, and
        // none of those is a branch.
        if (p >= site && InTailField(p - site)) continue;
        if (!IsShortBranch(fn[p])) continue;
        const std::int64_t target =
            static_cast<std::int64_t>(p) + 2 + static_cast<std::int8_t>(fn[p + 1]);
        if (target > site && target <= site + kSiteLen - 1) {
            return Refuse(f, Check::kTailBranch8, f.fn_begin + p);
        }
    }
    ++f.passed;
    return true;
}

// The four branches that send an out-of-range reference to the site must
// agree on where it is; the jump right before it gives the exit.
bool SiteAndExit(Found& f, const std::uint8_t* fn) {
    const std::uint32_t load = f.load - f.fn_begin;
    std::int64_t site = -1;
    for (int k = 0; k < 4; ++k) {
        const std::uint32_t at = load + kBranchAt[k];
        const std::int64_t target = static_cast<std::int64_t>(at) + 6 + I32(fn + at + 2);
        if (fn[at] != 0x0F || fn[at + 1] != kBranchOp[k]
            || (site >= 0 && target != site)) {
            return Refuse(f, Check::kBranches, f.fn_begin + at);
        }
        site = target;
    }
    if (site != kSiteAt) return Refuse(f, Check::kBranches, f.fn_begin + load);
    f.site = f.fn_begin + kSiteAt;
    ++f.passed;
    const std::int64_t exit = static_cast<std::int64_t>(kSiteAt) + I32(fn + kSiteAt - 4);
    if (fn[kSiteAt - 5] != 0xE9 || exit != kExitAt) {
        return Refuse(f, Check::kJumpBefore, f.site - 5);
    }
    f.exit = f.fn_begin + kExitAt;
    ++f.passed;
    return true;
}

// Both ways out of the block end in the same call (the one that moves the
// reference): after the start-position try, and right before the exit.
bool SameCalls(Found& f, const std::uint8_t* fn) {
    const std::uint32_t first = f.load - f.fn_begin + kFirstCallAt;
    const std::uint32_t second = kExitAt - 6;
    if (fn[first] != 0xE8 || fn[second] != 0xE8 || fn[kExitAt - 1] != 0x90) {
        return Refuse(f, Check::kCalls, f.fn_begin + second);
    }
    const std::int64_t a = static_cast<std::int64_t>(f.fn_begin) + first + 5 + I32(fn + first + 1);
    const std::int64_t b = static_cast<std::int64_t>(f.fn_begin) + second + 5 + I32(fn + second + 1);
    if (a != b) return Refuse(f, Check::kCalls, f.fn_begin + first);
    f.move_fn = static_cast<std::uint32_t>(a);
    ++f.passed;
    return true;
}

bool TailFields(Found& f, const std::uint8_t* fn) {
    for (const TailField& t : kTailFields) {
        if (!Same(f, Check::kTailFields, f.site + t.at, fn + kSiteAt + t.at,
                  t.op, t.op_len)) {
            return false;
        }
    }
    // The compare's one-byte operand, after its address field.
    if (fn[kSiteAt + kTailEnd - 1] != 0x00) {
        return Refuse(f, Check::kTailFields, f.site + kTailEnd - 1);
    }
    ++f.passed;
    return true;
}

bool SelfChecks(const Scan& s, Found& f) {
    const std::uint8_t* fn = s.Code(f.fn_begin, kFunctionLength);
    if (f.load != f.fn_begin + kLoadAt) {
        return Refuse(f, Check::kLoadOffset, f.load);
    }
    ++f.passed;
    const std::uint8_t* load = fn + kLoadAt;
    if (!Same(f, Check::kLoadForm, f.load, load, kLoadForm, sizeof(kLoadForm))) {
        return false;
    }
    ++f.passed;
    if (!Same(f, Check::kAbsoluteValue, f.load - 9, load - 9, kAbsoluteValue,
              sizeof(kAbsoluteValue))) {
        return false;
    }
    ++f.passed;
    if (!Same(f, Check::kComparePairs, f.load + 8, load + 8, kComparePairs,
              sizeof(kComparePairs))) {
        return false;
    }
    ++f.passed;
    if (!Same(f, Check::kFlagTest, f.load + 0x1b, load + 0x1b, kFlagTest,
              sizeof(kFlagTest))) {
        return false;
    }
    ++f.passed;
    if (!SiteAndExit(f, fn)) return false;
    if (!Same(f, Check::kSite, f.site, fn + kSiteAt, kSiteBytes, kSiteWindow)) {
        return false;
    }
    ++f.passed;
    if (!Same(f, Check::kExit, f.exit, fn + kExitAt, kExitBytes,
              sizeof(kExitBytes))) {
        return false;
    }
    ++f.passed;
    if (!SameCalls(f, fn) || !TailFields(f, fn)) return false;
    return SweepNear(s, f) && SweepShort(f, fn);
}

}  // namespace

const int kSelfChecks = 14;

const std::uint8_t kSiteBytes[kSiteWindow] = {
    0x49, 0x8b, 0x5e, 0x18,        // mov rbx, [r14+18h]
    0x0f, 0x57, 0xc9,              // xorps xmm1, xmm1
    0xf6, 0x43, 0x40, 0x01};       // test byte ptr [rbx+40h], 1

bool ParseHeaders(const std::uint8_t* p, std::uint64_t n, Headers& out) {
    out = Headers();
    if (!p || n < 0x40 || p[0] != 'M' || p[1] != 'Z') return false;
    const std::uint64_t pe = U32(p + 0x3c);
    if (pe > n || n - pe < 24) return false;
    if (U32(p + pe) != 0x00004550) return false;            // "PE\0\0"
    const std::uint64_t coff = pe + 4;
    if (U16(p + coff) != 0x8664) return false;              // x64
    const unsigned count = U16(p + coff + 2);
    const std::uint64_t opt_size = U16(p + coff + 16);
    const std::uint64_t opt = coff + 20;
    if (!count || count > kMaxSections) return false;
    // Through data directory 3, the exception directory.
    if (opt_size < 112 + 4 * 8 || n - opt < opt_size) return false;
    if (U16(p + opt) != 0x20b || U32(p + opt + 108) < 4) return false;
    out.size_of_image = U32(p + opt + 56);
    out.table_rva = U32(p + opt + 112 + 3 * 8);
    out.table_size = U32(p + opt + 112 + 3 * 8 + 4);
    const std::uint64_t sections = opt + opt_size;
    if (n - sections < static_cast<std::uint64_t>(count) * 40) return false;
    for (unsigned i = 0; i < count; ++i) {
        const std::uint8_t* s = p + sections + i * 40;
        SectionInfo& x = out.sections[i];
        x.vsize = U32(s + 8);
        x.rva = U32(s + 12);
        x.raw_size = U32(s + 16);
        x.raw_ptr = U32(s + 20);
        x.flags = U32(s + 36);
    }
    out.n_sections = static_cast<int>(count);
    return true;
}

Found Locate(const Image& image) {
    Found f;
    Scan s;
    const std::uint8_t* table = nullptr;
    if (!Open(image, s, f) || !FindConstant(s, f) || !CheckTable(s, f, table)
        || !Fingerprint(s, f, table) || !FindReferences(s, f)
        || !EnclosingFunction(s, f, table) || !SelfChecks(s, f)) {
        return f;
    }
    f.refused = Check::kOk;
    return f;
}

const char* CheckName(Check c) {
    static const char* const kNames[] = {
        "ok", "headers", "unreadable", "code-section", "data-sections",
        "constant", "function-table", "fingerprint", "load", "compare",
        "other-reference", "function", "function-length",
        "fingerprint-mismatch", "function-fragment", "load-offset",
        "load-form", "absolute-value", "compare-pairs", "flag-test",
        "branches", "jump-before", "site", "exit", "calls", "tail-fields",
        "site-entries", "tail-branch-near", "tail-branch-short"};
    static_assert(sizeof(kNames) / sizeof(kNames[0])
                  == static_cast<int>(Check::kCount_), "one name per check");
    const int i = static_cast<int>(c);
    if (i < 0 || i >= static_cast<int>(Check::kCount_)) return "?";
    return kNames[i];
}

void Report(const Found& f, void (*line)(const char* text)) {
    char b[200];
    const Check r = f.refused;
    if (r != Check::kHeaders && !(r == Check::kUnreadable && !f.at)) {
        std::snprintf(b, sizeof(b), "sections: %d code (+0x%x, 0x%x bytes), "
                      "%d read-only data", f.n_code, f.code_rva, f.code_size,
                      f.n_data);
        line(b);
    }
    if (f.n_constant) {
        std::snprintf(b, sizeof(b), "the limit 30000.0: found %d time(s), "
                      "first at +0x%x", f.n_constant, f.constant);
        line(b);
    } else if (r == Check::kConstant) {
        line("the limit 30000.0: not found");
    }
    if (f.n_functions) {
        std::snprintf(b, sizeof(b), "function table: %u entries, function of "
                      "the known shape (tables only): %d, first at +0x%x",
                      f.n_functions, f.n_fingerprint, f.fingerprint_fn);
        line(b);
    }
    if (f.n_fingerprint == 1 && r != Check::kFingerprint) {
        std::snprintf(b, sizeof(b), "references to the limit: %d load, "
                      "%d mulss, %d compare, %d other", f.n_load, f.n_mulss,
                      f.n_compare, f.n_other);
        line(b);
    }
    if (f.fn_begin) {
        std::snprintf(b, sizeof(b), "the load is at +0x%x, in function "
                      "+0x%x..+0x%x", f.load, f.fn_begin, f.fn_end);
        line(b);
        std::snprintf(b, sizeof(b), "self-checks passed: %d of %d", f.passed,
                      kSelfChecks);
        line(b);
    }
    if (f.ok()) {
        std::snprintf(b, sizeof(b), "ONE SITE at +0x%x (function +0x%x, exit "
                      "+0x%x, mover +0x%x)", f.site, f.fn_begin, f.exit,
                      f.move_fn);
    } else if (f.mismatch >= 0) {
        std::snprintf(b, sizeof(b), "REFUSE: %s at +0x%x (byte %d of %d is the "
                      "first that differs, %d differ)", CheckName(r), f.at,
                      f.mismatch, f.window, f.differing);
    } else if (f.at) {
        std::snprintf(b, sizeof(b), "REFUSE: %s at +0x%x", CheckName(r), f.at);
    } else {
        std::snprintf(b, sizeof(b), "REFUSE: %s", CheckName(r));
    }
    line(b);
}

}  // namespace interior_bounds
