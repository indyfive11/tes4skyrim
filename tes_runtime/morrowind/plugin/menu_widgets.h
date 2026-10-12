// What every Morrowind window shares: hit rectangles, MW_VScroll's thumb
// placed along its track and a click on it turned into steps, and the
// embedded face's glyph widths. The art and the face are the same in every
// movie, so the numbers are menu_layout.h's.

#pragma once

#include <string>

#include "menu.h"
#include "menu_layout.h"

namespace tesruntime::mw {

// The text colors the plugin draws with: Skyrim's palette when the last menu
// opened carries the movie's SkyrimStyle marker, else Morrowind's.
// See: docs/commentary/morrowind_runtime.md#menu-styles
const layout::Palette& Colors();

// Reads `menu`'s movie for the marker. Each menu calls it as it opens.
void PickColors(CustomMenu& menu);

// A rectangle in stage pixels.
struct Rect {
    int x, y, w, h;
    bool Contains(double px, double py) const {
        return px >= x && py >= y && px < x + w && py < y + h;
    }
};

// Shows `menu`'s scrollbar sprite `barPath` with its `thumbPath` at
// `fraction` of `bar`'s track, or hides both.
void PushScrollbar(CustomMenu& menu, const Rect& bar, const std::string& barPath,
                   const std::string& thumbPath, bool visible, double fraction);

// A click at `y` on a scrollbar whose thumb sits at `fraction`: the arrows
// step, the track pages relative to the thumb. The signed number of steps.
int ScrollClick(const Rect& bar, double y, double fraction, int page);

// A thumb held by the mouse: pressed on it, it follows the cursor along the
// track until the button comes up.
struct ThumbDrag {
    const Rect* bar = nullptr;
    double grab = 0;

    // A press at (`x`, `y`): grabs `on`'s thumb, sitting at `fraction`, when
    // the press is on it. True when it did.
    bool Begin(const Rect& on, double x, double y, double fraction);
    // The fraction the cursor at `y` puts the thumb at.
    double Fraction(double y) const;
    bool Active() const { return bar != nullptr; }
    void End() { bar = nullptr; }
};

// A glyph's width on screen at the body size, as the movie draws it, and a
// whole line's.
double CharWidth(char c);
double TextWidth(const std::string& text);

// An HTML field's markup: `rgb` as "#RRGGBB"; text escaped, with each newline
// a <br> (which the field counts as ONE character); and that text in a color.
std::string HexColor(unsigned rgb);
std::string HtmlText(const std::string& text);
std::string HtmlColored(const std::string& text, unsigned rgb);

}  // namespace tesruntime::mw
