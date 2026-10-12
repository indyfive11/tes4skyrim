#include "menu_widgets.h"

#include <algorithm>
#include <cstdio>

#include "menu_layout.h"

namespace tesruntime::mw {

namespace {

const layout::Palette* g_colors = &layout::kMorrowindColors;

int TrackLength(const Rect& bar) {
    return bar.h - layout::kScrollTrackTop - layout::kScrollTrackBottom -
           layout::kThumbH;
}

// Where the thumb's top edge sits with the view `fraction` of the way down.
double ThumbTop(const Rect& bar, double fraction) {
    return bar.y + layout::kScrollTrackTop +
           std::clamp(fraction, 0.0, 1.0) * TrackLength(bar);
}

}  // namespace

const layout::Palette& Colors() { return *g_colors; }

void PickColors(CustomMenu& menu) {
    double x = 0;
    g_colors = menu.GetNumber(layout::kStyleMarker, &x) ? &layout::kSkyrimColors
                                                        : &layout::kMorrowindColors;
}

void PushScrollbar(CustomMenu& menu, const Rect& bar, const std::string& barPath,
                   const std::string& thumbPath, bool visible, double fraction) {
    menu.SetNumber((barPath + "._visible").c_str(), visible ? 1 : 0);
    menu.SetNumber((thumbPath + "._visible").c_str(), visible ? 1 : 0);
    if (!visible) return;
    menu.SetNumber((thumbPath + "._y").c_str(), ThumbTop(bar, fraction));
}

int ScrollClick(const Rect& bar, double y, double fraction, int page) {
    if (y < bar.y + layout::kScrollEnd) return -1;
    if (y >= bar.y + bar.h - layout::kScrollEnd) return 1;
    const double thumbTop = ThumbTop(bar, fraction);
    if (y < thumbTop) return -page;
    if (y >= thumbTop + layout::kThumbH) return page;
    return 0;
}

bool ThumbDrag::Begin(const Rect& on, double x, double y, double fraction) {
    const double top = ThumbTop(on, fraction);
    if (!on.Contains(x, y) || y < top || y >= top + layout::kThumbH) return false;
    bar = &on;
    grab = y - top;
    return true;
}

double ThumbDrag::Fraction(double y) const {
    const int track = bar ? TrackLength(*bar) : 0;
    if (track <= 0) return 0;
    return std::clamp((y - grab - bar->y - layout::kScrollTrackTop) / track, 0.0, 1.0);
}

double CharWidth(char c) {
    const int index = static_cast<unsigned char>(c) - layout::kFirstCode;
    constexpr int count = sizeof(layout::kAdvance) / sizeof(layout::kAdvance[0]);
    const int units = (index >= 0 && index < count)
                          ? layout::kAdvance[index]
                          : layout::kAdvance['?' - layout::kFirstCode];
    return units * static_cast<double>(layout::kFontPx) / layout::kFontEm;
}

double TextWidth(const std::string& text) {
    double width = 0;
    for (char c : text) width += CharWidth(c);
    return width;
}

std::string HexColor(unsigned rgb) {
    char buf[16];
    std::snprintf(buf, sizeof(buf), "#%06X", rgb);
    return buf;
}

std::string HtmlText(const std::string& text) {
    std::string html;
    for (char c : text) {
        if (c == '\r') continue;
        if (c == '\n') {
            html += "<br>";
        } else if (c == '&') {
            html += "&amp;";
        } else if (c == '<') {
            html += "&lt;";
        } else if (c == '>') {
            html += "&gt;";
        } else {
            html += c;
        }
    }
    return html;
}

std::string HtmlColored(const std::string& text, unsigned rgb) {
    return "<font color=\"" + HexColor(rgb) + "\">" + HtmlText(text) + "</font>";
}

}  // namespace tesruntime::mw
