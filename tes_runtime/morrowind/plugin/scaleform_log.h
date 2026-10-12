// What Scaleform says while one of our movies loads.
//
// GFxLoader::LoadMovie returns only true or false; the reason a movie is
// refused goes to Scaleform's log, which the release game leaves unset. This
// installs one (the same object skse64's bEnableGFXLog installs) and writes
// what it hears to our log, but only while a LoadMovieScope is alive, so no
// other menu's traces reach it.
#pragma once

namespace tesruntime::mw {

// Installs the log on the GFx loader singleton `loader`, once. Leaves one that
// is already there (SKSE's own) alone, and says so.
void InstallScaleformLog(void* loader);

// Scaleform's messages are ours for the lifetime of this object.
class LoadMovieScope {
public:
    explicit LoadMovieScope(const char* movie);
    ~LoadMovieScope();
};

// After a failed load: whether the engine's own lookup, and the disk, can see
// Interface/<movie>.swf and the .gfx fallback it tries next.
void LogMovieLookup(const char* movie);

}  // namespace tesruntime::mw
