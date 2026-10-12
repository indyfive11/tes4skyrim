#include "perks_skills.h"

#include <algorithm>
#include <cctype>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>

#include "actor_stats.h"
#include "addresses.h"
#include "ids.h"
#include "menu.h"

namespace tesruntime::mw {

namespace {

// Each skill's label in the vanilla movie, `NAME <font color='...'>LEVEL</font>`
// as its InitAnimatedSkillText builds it. The name carries no color of its
// own, so the first color in the label read back is the name's.
constexpr const char* kLabel =
    "_root.StatsMenuBaseInstance.AnimatingSkillTextInstance.SkillText%u.LabelInstance.htmlText";

// The red vanilla gives a lowered skill's level (0x97b0b0 on 1.6.1170).
constexpr const char* kCappedColor = "#FF0000";
constexpr std::size_t kColorLength = 7;

// Ticks between passes: a quarter second at the sheet's 33 ms tick.
constexpr int kEvery = 8;
constexpr std::uint32_t kMaxSkills = 18;

int g_until = 0;
// Each name's own color, kept while it shows red so it can be given back.
std::string g_own[kMaxSkills];

// Where the first `color="#` value starts in `html`, or npos.
std::size_t FirstColor(const std::string& html) {
    std::string lower(html);
    std::transform(lower.begin(), lower.end(), lower.begin(),
                   [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
    for (const char* attr : {"color=\"#", "color='#"}) {
        const std::size_t at = lower.find(attr);
        if (at != std::string::npos) return at + std::char_traits<char>::length(attr) - 1;
    }
    return std::string::npos;
}

// Red while `capped`, its own color again once it is not. A label the game
// has just rebuilt reads its own color, so it is painted again.
void Paint(void* view, std::uint32_t n, bool capped) {
    char path[128];
    std::snprintf(path, sizeof(path), kLabel, n);
    std::string html;
    if (!MovieGetText(view, path, &html)) return;
    const std::size_t at = FirstColor(html);
    if (at == std::string::npos || at + kColorLength > html.size()) return;
    const std::string now = html.substr(at, kColorLength);
    const bool red = _stricmp(now.c_str(), kCappedColor) == 0;
    if (capped == red || (!capped && g_own[n].empty())) return;
    if (capped) g_own[n] = now;
    html.replace(at, kColorLength, capped ? kCappedColor : g_own[n]);
    MovieSetText(view, path, html.c_str());
}

}  // namespace

void TickPerksSkills() {
    if (IsVr() || --g_until > 0) return;
    g_until = kEvery;
    void* view = nullptr;
    void* menu = EngineMenuObject(ids::kPerksMenu, &view);
    if (!menu || !view) return;
    const auto* skills = At<const std::uint32_t*>(menu, ids::kOffPerksSkills);
    const std::uint32_t count =
        std::min(At<std::uint32_t>(menu, ids::kOffPerksSkillCount), kMaxSkills);
    for (std::uint32_t n = 0; skills && n < count; ++n) {
        Paint(view, n, SkillCapped(static_cast<int>(skills[n])));
    }
}

}  // namespace tesruntime::mw
