// Morrowind's windows: Skyrim IMenus of our own, each showing its own SWF.
//
// Each registers under a NEW name with MenuManager::Register and loads its
// own Interface/<movie>.swf. No vanilla menu, movie or record is touched,
// which is what keeps Skyrim's own menus working.
//
// This layer knows the MOVIE: fields, properties, mouse events. What the
// fields say and what a click means belongs to the window's owner --
// conversation.cpp for dialogue, stats_sheet.cpp and levelup_menu.cpp for the
// character windows -- which registers itself through MenuInput.
// See: docs/commentary/morrowind_runtime.md#menu-registration

#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <string>

namespace tesruntime::mw {

// What the mouse did, in STAGE pixels, and when the movie came and went.
// Every pointer may be null. `release` is the left button coming up.
struct MenuInput {
    void (*hover)(double x, double y) = nullptr;
    void (*click)(double x, double y) = nullptr;
    void (*release)() = nullptr;
    void (*wheel)(double x, double y, double delta) = nullptr;
    void (*cancel)() = nullptr;
    void (*opened)() = nullptr;
    void (*closed)() = nullptr;
    void (*tick)() = nullptr;
};

struct EngineMenu;

// One window: its registered name, its movie, and what it has been told.
class CustomMenu {
public:
    // `name` is what it registers under; `movie` is passed to LoadMovie
    // WITHOUT an extension, which the callee formats through
    // "Interface/%s.swf".
    CustomMenu(const char* name, const char* movie);

    // Registers the menu. False when an engine entry point is missing, in
    // which case NOTHING is hooked and the game behaves as without it.
    bool Install();

    // Opens or closes it by posting a UIMessage, the way the engine opens its
    // own. Safe before Install, where both are no-ops.
    void Open();
    void Close();

    // Writes one of the movie's dynamic text fields by its VARIABLE path. Held
    // and replayed when the menu next opens, so text set before the open lands.
    // See: docs/commentary/morrowind_runtime.md#dynamic-text
    void SetText(const char* variable, const char* text);

    // Sets or reads a NUMBER property on the live movie -- a field's textColor
    // or scroll. Not replayed: the owner re-applies these from `opened`.
    void SetNumber(const char* path, double value);
    bool GetNumber(const char* path, double* out);

    // Calls a method on the live movie with numeric arguments, e.g. a field's
    // getCharIndexAtPoint. False when the movie is absent or the call failed.
    bool InvokeNumber(const char* path, const double* args, std::size_t count,
                      double* result);

    void SetInput(const MenuInput& input) { mInput = input; }

    // True once Register accepted it, and while it is between its open and
    // close messages.
    bool Installed() const { return mInstalled; }
    bool IsOpen() const { return mOpen; }

    // The name it registered under, for UI.OpenMenu from Papyrus or the console.
    const char* Name() const { return mName; }

    // The engine's calls into the menu, routed by the object it created.
    void* Create();
    void OnOpen(EngineMenu* menu);
    void OnClose();
    std::uint32_t OnScaleformEvent(EngineMenu* menu, char* data);
    std::uint32_t OnUserEvent(char* data);
    void OnFrame(EngineMenu* menu);

private:
    void* LiveView() const;
    void ApplyText(const char* variable, const char* text);
    bool MousePosition(double* x, double* y);

    const char* mName;
    const char* mMovie;
    bool mInstalled = false;
    bool mOpen = false;
    // Its movie failed to load; it will not open again this session.
    bool mFailed = false;
    MenuInput mInput;
    // The engine's object while the movie is live, and the one this session
    // created, reused across opens.
    EngineMenu* mLive = nullptr;
    EngineMenu* mKept = nullptr;
    // What the fields should say, kept so a menu created later still gets it.
    std::map<std::string, std::string> mPending;
    // Where the cursor last was, in stage pixels, for events that carry none.
    double mLastX = 0, mLastY = 0;
};

// The dialogue window, Interface/morrowind_dialogue.swf.
CustomMenu& DialogueMenu();

// The dialogue window's calls, which conversation*.cpp make throughout.
bool InstallMenu();
void OpenMenu();
void CloseMenu();
void SetMenuText(const char* variable, const char* text);
void SetMenuNumber(const char* path, double value);
bool GetMenuNumber(const char* path, double* out);
bool InvokeMenuNumber(const char* path, const double* args, std::size_t count,
                      double* result);
void SetMenuInput(const MenuInput& input);
bool MenuInstalled();
const char* MenuName();

// How many OPEN menus pause the game, ours included. 0 before any Install.
// See: docs/commentary/morrowind_runtime.md#the-tick-stops-while-the-game-is-paused
std::uint32_t PausingMenuCount();

}  // namespace tesruntime::mw
