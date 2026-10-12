#include "scaleform_log.h"

#include <windows.h>

#include <atomic>
#include <cstdarg>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

#include "addresses.h"
#include "ids.h"
#include "log.h"
#include "paths.h"

namespace tesruntime::mw {

namespace {

using SetStateFn = void (*)(void* bag, std::uint32_t type, void* state);
using GetStateFn = void* (*)(void* bag, std::uint32_t type);
using FileExistsFn = bool (*)(const char* path);

// A GFxLog as the engine reads it: GFxState (vtable, ref count, state type),
// then the GFxLogBase vtable at +0x18. LogError (0xfb7440) is handed that
// +0x18 pointer, steps back to the object and calls slot 1 of its vtable,
// LogMessageVarg(type, fmt, args).
struct ScaleformLogger {
    void**        vtable;
    long          refCount;
    std::uint32_t pad0c;
    std::uint32_t type;
    std::uint32_t pad14;
    void**        baseVtable;
};

static_assert(offsetof(ScaleformLogger, refCount) == 0x8, "ref count");
static_assert(offsetof(ScaleformLogger, type) == 0x10, "state type");
static_assert(offsetof(ScaleformLogger, baseVtable) == 0x18, "GFxLogBase");

// Where GFxRefCount keeps its count, for the reference GetStateAddRef adds.
constexpr std::size_t kOffRefCount = 0x8;

// The longest message kept; Scaleform's own are one line.
constexpr std::size_t kMessageMax = 512;

std::atomic<const char*> g_movie{nullptr};
bool g_installed = false;

// Never freed: the bag holds a reference and ours never goes away, so the
// count cannot reach zero and neither destructor is ever reached.
void* __fastcall LoggerDtor(void* self, std::uint32_t) { return self; }
bool __fastcall NotVerbose(void*) { return false; }

void __fastcall LogMessage(ScaleformLogger*, std::uint32_t type,
                           const char* fmt, va_list args) {
    const char* movie = g_movie.load();
    if (!movie || !fmt) return;
    char text[kMessageMax];
    std::vsnprintf(text, sizeof(text), fmt, args);
    std::size_t n = std::strlen(text);
    while (n && (text[n - 1] == '\n' || text[n - 1] == '\r')) text[--n] = '\0';
    if (n) Log("scaleform: [%s] %02x %s", movie, type, text);
}

void* g_loggerVtable[2] = {reinterpret_cast<void*>(&LoggerDtor),
                           reinterpret_cast<void*>(&LogMessage)};
void* g_baseVtable[2] = {reinterpret_cast<void*>(&LoggerDtor),
                         reinterpret_cast<void*>(&NotVerbose)};

ScaleformLogger g_logger = {g_loggerVtable, 1, 0, ids::kGfxStateLog, 0,
                            g_baseVtable};

bool OnDisk(const std::string& relative) {
    const std::string plugins = PluginsDir();
    const std::size_t skse = plugins.rfind("SKSE\\Plugins\\");
    if (skse == std::string::npos) return false;
    const std::string path = plugins.substr(0, skse) + relative;
    return GetFileAttributesA(path.c_str()) != INVALID_FILE_ATTRIBUTES;
}

}  // namespace

void InstallScaleformLog(void* loader) {
    if (g_installed || !loader) return;
    g_installed = true;
    void* bag = *reinterpret_cast<void**>(static_cast<char*>(loader) +
                                          ids::kOffLoaderStateBag);
    if (!bag) {
        Log("scaleform: loader has no state bag -- load errors stay silent");
        return;
    }
    void* existing = VCall<GetStateFn>(bag, ids::kStateBagGetStateSlot)(
        bag, ids::kGfxStateLog);
    if (existing) {
        InterlockedDecrement(reinterpret_cast<volatile long*>(
            static_cast<char*>(existing) + kOffRefCount));
        Log("scaleform: a log is already installed (skse64.ini "
            "bEnableGFXLog) -- load errors go to skse64.log");
        return;
    }
    VCall<SetStateFn>(bag, ids::kStateBagSetStateSlot)(bag, ids::kGfxStateLog,
                                                      &g_logger);
    Log("scaleform: log installed for our movies' load errors");
}

LoadMovieScope::LoadMovieScope(const char* movie) { g_movie.store(movie); }

LoadMovieScope::~LoadMovieScope() { g_movie.store(nullptr); }

void LogMovieLookup(const char* movie) {
    static auto exists = reinterpret_cast<FileExistsFn>(
        Resolve("MovieFileExists", ids::kMovieFileExists, nullptr));
    const std::string swf = std::string("Interface/") + movie + ".swf";
    const std::string gfx = std::string("Interface/Exported/") + movie + ".gfx";
    const std::string disk = std::string("Interface\\") + movie + ".swf";
    Log("menu: lookup for '%s': engine sees %s=%s, %s=%s; on disk %s", movie,
        swf.c_str(), exists ? (exists(swf.c_str()) ? "yes" : "NO") : "?",
        gfx.c_str(), exists ? (exists(gfx.c_str()) ? "yes" : "NO") : "?",
        OnDisk(disk) ? "yes" : "NO");
}

}  // namespace tesruntime::mw
