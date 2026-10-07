// SKSE plugin: an interior reference outside Skyrim's +/-30000 range stays
// where the plugin's author put it.
//
// When Skyrim builds the physics world of an INTERIOR cell, a reference whose
// X or Y lies beyond 30000 units is put back on its start position, or else
// moved onto the cell's default spot. Oblivion has no such rule, so a
// converted interior that is larger than that range, or sits far from its
// origin, arrives with its contents stacked on one point.
//
// The rule is one block in one function. This plugin finds that block from
// what the build itself contains (locate.cpp) and, when its ini says so,
// makes the block do nothing. It names exactly one site or changes nothing.
//
// See: README.md

#define _CRT_SECURE_NO_WARNINGS

#include <windows.h>

#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "detour.h"
#include "locate.h"
#include "log.h"

#ifndef INTERIOR_BOUNDS_LOOK_ONLY
#include "apply.h"
#endif

extern "C" IMAGE_DOS_HEADER __ImageBase;

namespace interior_bounds {

namespace {

#ifdef INTERIOR_BOUNDS_LOOK_ONLY
constexpr bool kCanWrite = false;
#else
constexpr bool kCanWrite = true;
#endif

constexpr std::uint32_t kPluginVersion = 1;

HANDLE g_log = INVALID_HANDLE_VALUE;

// True when [at, at + len) is committed and readable. Every range the
// search reads is asked about first; a guess is never dereferenced.
bool Readable(const std::uint8_t* at, std::uint64_t len) {
    constexpr DWORD kReadable =
        PAGE_READONLY | PAGE_READWRITE | PAGE_WRITECOPY | PAGE_EXECUTE_READ
        | PAGE_EXECUTE_READWRITE | PAGE_EXECUTE_WRITECOPY;
    const std::uint8_t* end = at + len;
    while (at < end) {
        MEMORY_BASIC_INFORMATION mbi;
        if (!VirtualQuery(at, &mbi, sizeof(mbi))) return false;
        if (mbi.State != MEM_COMMIT) return false;
        if (mbi.Protect & (PAGE_GUARD | PAGE_NOACCESS)) return false;
        if (!(mbi.Protect & kReadable)) return false;
        at = static_cast<const std::uint8_t*>(mbi.BaseAddress) + mbi.RegionSize;
    }
    return true;
}

// The mode is read once, here; nothing re-reads it while the game runs.
Mode ReadMode() {
    char path[MAX_PATH]{};
    const DWORD n = GetModuleFileNameA(reinterpret_cast<HMODULE>(&__ImageBase),
                                       path, MAX_PATH);
    // The ini sits beside the DLL and shares its name.
    char* name = std::strrchr(path, '\\');
    name = name ? name + 1 : path;
    char* dot = std::strrchr(name, '.');
    if (!n || n >= MAX_PATH || !dot || (dot - path) + 5 > MAX_PATH) {
        Log("ini: the plugin's own path could not be read; iMode = 0");
        return Mode::kLook;
    }
    std::strcpy(dot, ".ini");
    const bool exists = GetFileAttributesA(path) != INVALID_FILE_ATTRIBUTES;
    const int value = GetPrivateProfileIntA("General", "iMode", 0, path);
    Log("ini: %s (%s), iMode = %d", name, exists ? "found" : "not found", value);
    if (value < 0 || value > 2) {
        Log("iMode must be 0, 1 or 2; using 0");
        return Mode::kLook;
    }
    if (value && !kCanWrite) {
        Log("this is the look-only build; it has no write path, so iMode = 0");
        return Mode::kLook;
    }
    return static_cast<Mode>(value);
}

void Run(std::uint32_t runtime) {
    // Once: a second pass would reopen the log and search a patched image.
    static bool ran = false;
    if (ran) return;
    ran = true;
    const bool logging = LogOpen();
    Log("InteriorBounds %u, %s", kPluginVersion,
        kCanWrite ? "full build" : "look-only build");
    if (runtime) {
        Log("runtime %u.%u.%u.%u", (runtime >> 24) & 0xFF,
            (runtime >> 16) & 0xFF, (runtime >> 4) & 0xFFF, runtime & 0xF);
    }
    // Without a log nobody could tell what was changed, so nothing is.
    const Mode mode = logging ? ReadMode() : Mode::kLook;

    auto* base = reinterpret_cast<std::uint8_t*>(GetModuleHandleA(nullptr));
    Headers headers;
    if (!base || !Readable(base, 0x1000)
        || !ParseHeaders(base, 0x1000, headers) || !headers.size_of_image) {
        Log("REFUSE: the game's module headers could not be read");
        return;
    }
    Image image;
    image.base = base;
    image.size = headers.size_of_image;
    image.readable = Readable;
    const Found found = Locate(image);
    Report(found, LogLine);
    if (!found.ok() || mode == Mode::kLook) {
        Log("nothing written");
        return;
    }
#ifndef INTERIOR_BOUNDS_LOOK_ONLY
    Apply(mode, base, image.size, found);
#endif
}

}  // namespace

bool LogOpen() {
    // Beside SKSE's own logs, under Documents/My Games.
    const char* home = std::getenv("USERPROFILE");
    if (!home) return false;
    char path[MAX_PATH]{};
    const int n = std::snprintf(
        path, sizeof(path), "%s\\Documents\\My Games\\Skyrim Special Edition"
        "\\SKSE\\InteriorBounds.log", home);
    if (n < 0 || n >= static_cast<int>(sizeof(path))) return false;
    // FILE_SHARE_READ: the log can be read while the game runs.
    const HANDLE file = CreateFileA(path, GENERIC_WRITE, FILE_SHARE_READ,
                                    nullptr, CREATE_ALWAYS,
                                    FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return false;
    g_log = file;
    return true;
}

// One WriteFile per line, so the two threads that log cannot interleave
// inside a line and a hang loses nothing already written.
void LogLine(const char* text) {
    if (g_log == INVALID_HANDLE_VALUE) return;
    SYSTEMTIME st;
    GetLocalTime(&st);
    char line[600];
    int n = std::snprintf(line, sizeof(line) - 2, "[%02d:%02d:%02d.%03d] %s",
                          st.wHour, st.wMinute, st.wSecond, st.wMilliseconds,
                          text);
    if (n < 0) return;
    if (n > static_cast<int>(sizeof(line)) - 3) n = sizeof(line) - 3;
    line[n++] = '\r';
    line[n++] = '\n';
    DWORD written = 0;
    WriteFile(g_log, line, static_cast<DWORD>(n), &written, nullptr);
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

// ---- SKSE interface ---------------------------------------------------
//
// Declared inline rather than via the SKSE SDK so this builds standalone.
// Only the layout of the version struct matters to SKSE.

struct SKSEPluginVersionData {
    uint32_t dataVersion;
    uint32_t pluginVersion;
    char     name[256];
    char     author[256];
    char     supportEmail[252];
    uint32_t versionIndependenceEx;
    uint32_t versionIndependence;
    uint32_t compatibleVersions[16];
    uint32_t seVersionRequired;
};

// Layout mirrors tes_runtime/common/skse_abi.h.
struct PluginInfo {
    enum { kInfoVersion = 1 };
    uint32_t    infoVersion;
    const char* name;
    uint32_t    version;
};

struct SKSEInterface {
    uint32_t skseVersion;
    uint32_t runtimeVersion;
    uint32_t editorVersion;
    uint32_t isEditor;
    void*    (*QueryInterface)(uint32_t id);
    uint32_t (*GetPluginHandle)(void);
    uint32_t (*GetReleaseIndex)(void);
    const PluginInfo* (*GetPluginInfo)(const char* name);
};

// Both flags are true here: it touches no game struct, and it finds its
// target from what the image contains, never from a hardcoded address. That
// is what lets compatibleVersions stay empty; on a build it does not
// recognise it refuses instead of patching.
constexpr uint32_t kVersionIndependentEx_NoStructUse = 1 << 0;
constexpr uint32_t kVersionIndependent_Signatures = 1 << 1;

extern "C" __declspec(dllexport) SKSEPluginVersionData SKSEPlugin_Version = {
    1,                                  // dataVersion (kVersion)
    1,                                  // plugin version
    "InteriorBounds",
    "TESConversion",
    "",
    kVersionIndependentEx_NoStructUse,
    kVersionIndependent_Signatures,
    {0},                                // compatibleVersions: any
    0,                                  // seVersionRequired: any
};

namespace {
uint32_t g_runtime_version = 0;
}

extern "C" __declspec(dllexport) bool SKSEPlugin_Query(const SKSEInterface* skse,
                                                       PluginInfo* info) {
    if (info) {
        info->infoVersion = PluginInfo::kInfoVersion;
        info->name = "InteriorBounds";
        info->version = 1;
    }
    if (skse) g_runtime_version = skse->runtimeVersion;
    return true;
}

extern "C" __declspec(dllexport) bool SKSEPlugin_Load(const SKSEInterface* skse) {
    if (skse) g_runtime_version = skse->runtimeVersion;
    interior_bounds::Run(g_runtime_version);
    return true;                        // never block game start
}
