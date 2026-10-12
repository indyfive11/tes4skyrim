#include "perks_button.h"

#include <windows.h>
#include <Xinput.h>

#include <cmath>
#include <string>

#include "addresses.h"
#include "ids.h"
#include "log.h"
#include "menu.h"
#include "menu_widgets.h"
#include "stats_layout.h"

namespace tesruntime::mw {

namespace {

namespace sl = stats_layout;

constexpr const char* kOpenLabel = "CHARACTER";
constexpr const char* kBackLabel = "BACK TO PERKS";
constexpr unsigned kHoverColor = 0xffffff;
constexpr double kTwips = 20;

// The controller's way in: Y, which Skyrim's controlmap.txt binds only in the
// item menus, read through XInput as the game reads its own gamepad. The key
// cap shows it while a controller is connected.
// See: docs/commentary/morrowind_runtime.md#controller
constexpr WORD kPadButton = XINPUT_GAMEPAD_Y;
constexpr const char* kPadLabel = "Y";
constexpr const char* kXInputDlls[] = {"xinput1_4.dll", "xinput1_3.dll", "xinput9_1_0.dll"};

using XInputGetStateFn = DWORD(WINAPI*)(DWORD, XINPUT_STATE*);
using CanProcessFn = bool (*)(void* handler, void* event);

CanProcessFn g_perksCanProcess = nullptr;

Rect g_at{0, 0, sl::kButtonW, sl::kButtonH};
bool g_shown = false;
bool g_hover = false;
bool g_back = false;
bool g_clicked = false;
bool g_placeLogged = false;
std::string g_keyLabel;
bool g_padConnected = false;
bool g_padHeld = false;

CustomMenu& Menu() {
    static CustomMenu menu("MorrowindPerksButton", "morrowind_perks_button", true);
    return menu;
}

void PushLook() {
    Menu().SetText("_root.Button.Key.text", g_padConnected ? kPadLabel : g_keyLabel.c_str());
    Menu().SetText("_root.Button.Label.text", g_back ? kBackLabel : kOpenLabel);
    Menu().SetNumber("_root.Button.Label.textColor", g_hover ? kHoverColor : sl::kButtonGray);
    Menu().SetNumber("_root.Button.Glow._visible", g_hover ? 1 : 0);
}

// The screen's left and bottom edges in stage pixels. Show-all scaling fits
// one side of the stage exactly, which tells whether the engine reported
// pixels or twips.
bool VisibleStage(double* left, double* bottom) {
    float rect[4] = {};
    if (!Menu().VisibleFrame(rect)) return false;
    for (double unit : {1.0, kTwips}) {
        const double w = (rect[2] - rect[0]) / unit;
        const double h = (rect[3] - rect[1]) / unit;
        if (std::abs(w - sl::kStageW) < 1 || std::abs(h - sl::kStageH) < 1) {
            *left = rect[0] / unit;
            *bottom = rect[3] / unit;
            return true;
        }
    }
    Log("perks button: visible frame (%.1f, %.1f, %.1f, %.1f) fits no stage side -- "
        "using the stage's corner", rect[0], rect[1], rect[2], rect[3]);
    return false;
}

// Into the screen's bottom-left corner, centered in the perks menu's bar.
void Place() {
    double left = 0, bottom = sl::kStageH;
    const bool visible = VisibleStage(&left, &bottom);
    g_at.x = static_cast<int>(std::lround(left)) + sl::kButtonMargin;
    g_at.y = static_cast<int>(std::lround(bottom)) - (sl::kPerksBarH + sl::kButtonH) / 2;
    Menu().SetNumber("_root.Button._x", g_at.x);
    Menu().SetNumber("_root.Button._y", g_at.y);
    if (g_placeLogged) return;
    g_placeLogged = true;
    Log("perks button: at stage (%d, %d)%s", g_at.x, g_at.y, visible ? "" : ", frame unknown");
}

void OnHover(double x, double y) {
    const bool over = g_at.Contains(x, y);
    if (over == g_hover) return;
    g_hover = over;
    PushLook();
}

void OnClick(double x, double y) {
    if (g_at.Contains(x, y)) g_clicked = true;
}

void OnOpened() {
    g_hover = false;
    Place();
    PushLook();
}

// The hotkey's own character, or its code when it has none.
std::string KeyLabel(int hotkey) {
    const UINT ch = MapVirtualKeyA(static_cast<UINT>(hotkey), MAPVK_VK_TO_CHAR) & 0x7fff;
    return ch ? std::string(1, static_cast<char>(ch)) : std::to_string(hotkey);
}

// XInputGetState from the first XInput the system has; null without one.
XInputGetStateFn XInputCall() {
    static const XInputGetStateFn get = [] {
        for (const char* dll : kXInputDlls) {
            if (HMODULE module = LoadLibraryA(dll)) {
                return reinterpret_cast<XInputGetStateFn>(GetProcAddress(module, "XInputGetState"));
            }
        }
        return XInputGetStateFn{};
    }();
    return get;
}

// Every controller slot: whether any is connected, and whether any holds the
// button. A press taken on the first read after the button shows (`fresh`)
// is ignored, so a held Y does not open the sheet as the perks menu opens.
void PollPad(bool fresh) {
    const XInputGetStateFn get = XInputCall();
    bool connected = false, held = false;
    for (DWORD pad = 0; get && pad < XUSER_MAX_COUNT; ++pad) {
        XINPUT_STATE state{};
        if (get(pad, &state) != ERROR_SUCCESS) continue;
        connected = true;
        held = held || (state.Gamepad.wButtons & kPadButton) != 0;
    }
    if (held && !g_padHeld && !fresh) g_clicked = true;
    g_padHeld = held;
    if (connected == g_padConnected) return;
    g_padConnected = connected;
    PushLook();
}

// Whether the cursor is on the button, from the movie's own mouse position.
bool UnderCursor() {
    double x = 0, y = 0;
    return Menu().GetNumber("_root._xmouse", &x) && Menu().GetNumber("_root._ymouse", &y) &&
           g_at.Contains(x, y);
}

// The perks menu's own input gate: it refuses the mouse's buttons while the
// cursor is on our button, so a click there opens the sheet and nothing else.
bool PerksCanProcess(void* handler, void* event) {
    if (g_shown && event && At<std::uint32_t>(event, ids::kOffInputDevice) == ids::kDeviceMouse &&
        At<std::uint32_t>(event, ids::kOffInputType) == ids::kInputButton && UnderCursor()) {
        return false;
    }
    return g_perksCanProcess(handler, event);
}

bool GatePerksInput() {
    const char* what = "StatsMenu MenuEventHandler::CanProcess";
    g_perksCanProcess = reinterpret_cast<CanProcessFn>(SwapVtableSlot(
        what, Resolve(what, ids::kPerksHandlerVtable, nullptr), ids::kCanProcessSlot,
        Resolve(what, ids::kPerksCanProcess, nullptr), reinterpret_cast<void*>(&PerksCanProcess)));
    return g_perksCanProcess != nullptr;
}

}  // namespace

bool InstallPerksButton(int hotkey) {
    if (EngineMenusQueryable() && !GatePerksInput()) {
        Log("perks button: the perks menu's input gate did not swap -- a click on the "
            "button also reaches the perks");
    }
    g_keyLabel = KeyLabel(hotkey);
    MenuInput input;
    input.hover = OnHover;
    input.click = OnClick;
    input.opened = OnOpened;
    Menu().SetInput(input);
    return Menu().Install();
}

bool PerksMenuOpen() { return EngineMenuOpen(ids::kPerksMenu); }

bool TickPerksButton(bool sheetOpen) {
    const bool show = Menu().Installed() && PerksMenuOpen();
    const bool fresh = show && !g_shown;
    if (show != g_shown) {
        g_shown = show;
        g_clicked = false;
        if (show) {
            Menu().Open();
        } else {
            Menu().Close();
        }
    }
    if (show) PollPad(fresh);
    if (sheetOpen != g_back) {
        g_back = sheetOpen;
        PushLook();
    }
    const bool clicked = g_clicked;
    g_clicked = false;
    return clicked && show;
}

}  // namespace tesruntime::mw
