#include "store.h"

#include <windows.h>

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <fstream>
#include <list>
#include <sstream>

#include "chargen_tables.h"
#include "components/esm3/infoorder.hpp"
#include "log.h"
#include "paths.h"
#include "scope.h"
#include "script_tables.h"

namespace tesruntime::mw {

namespace {

constexpr const char* kSigTopic = "MWDI";
constexpr const char* kSigInfo = "MWIN";
constexpr const char* kFileTopics = "DIAL.txt";
constexpr const char* kFileInfos = "INFO.txt";

constexpr const char* kBegin = "---RECORD_BEGIN---";
constexpr const char* kEnd = "---RECORD_END---";

std::unordered_map<std::string, Topic> g_topics;

std::string Lower(std::string text) {
    std::transform(text.begin(), text.end(), text.begin(),
                   [](unsigned char c) { return static_cast<char>(::tolower(c)); });
    return text;
}

DialType ParseDialType(const std::string& name) {
    if (name == "Topic") return DialType::Topic;
    if (name == "Voice") return DialType::Voice;
    if (name == "Greeting") return DialType::Greeting;
    if (name == "Persuasion") return DialType::Persuasion;
    if (name == "Journal") return DialType::Journal;
    return DialType::Unknown;
}

using Record = std::unordered_map<std::string, std::string>;

const std::string& Get(const Record& rec, const char* key) {
    static const std::string kEmpty;
    const auto it = rec.find(key);
    return it == rec.end() ? kEmpty : it->second;
}

int GetInt(const Record& rec, const char* key, int fallback) {
    const auto it = rec.find(key);
    if (it == rec.end() || it->second.empty()) return fallback;
    return std::atoi(it->second.c_str());
}

void AddConditions(const Record& rec, Info& info) {
    const int count = GetInt(rec, "ConditionCount", 0);
    info.conditions.reserve(static_cast<std::size_t>(count));
    for (int i = 0; i < count; ++i) {
        const std::string prefix = "Condition[" + std::to_string(i) + "].";
        const std::string& fn = Get(rec, (prefix + "Function").c_str());
        if (fn.empty()) continue;
        Condition cond;
        cond.function = fn[0];
        const std::string& varType = Get(rec, (prefix + "VarType").c_str());
        const std::string& cmp = Get(rec, (prefix + "Comparison").c_str());
        cond.varType = varType.empty() ? 0 : varType[0];
        cond.comparison = cmp.empty() ? 0 : cmp[0];
        cond.variable = Unescape(Get(rec, (prefix + "Variable").c_str()));
        const std::string& fnIndex = Get(rec, (prefix + "FunctionIndex").c_str());
        if (!fnIndex.empty()) cond.index = std::atoi(fnIndex.c_str());
        const std::string& valueType = Get(rec, (prefix + "ValueType").c_str());
        const std::string& value = Get(rec, (prefix + "Value").c_str());
        cond.isFloat = valueType == "Float";
        if (cond.isFloat) {
            cond.valueFloat = static_cast<float>(std::atof(value.c_str()));
        } else {
            cond.valueInt = std::atoi(value.c_str());
        }
        info.conditions.push_back(std::move(cond));
    }
}

Info MakeInfo(const Record& rec) {
    Info info;
    info.id = Unescape(Get(rec, "EditorID"));
    info.topic = Unescape(Get(rec, "Topic"));
    info.prev = Unescape(Get(rec, "Prev"));
    info.deleted = GetInt(rec, "Deleted", 0) != 0;
    info.type = ParseDialType(Get(rec, "InfoType"));
    info.disposition = GetInt(rec, "Disposition", 0);
    info.journalIndex = GetInt(rec, "JournalIndex", 0);
    info.rank = GetInt(rec, "Rank", -1);
    info.gender = GetInt(rec, "Gender", -1);
    info.pcRank = GetInt(rec, "PCRank", -1);
    info.factionLess = GetInt(rec, "FactionLess", 0) != 0;
    info.actor = Unescape(Get(rec, "Actor"));
    info.race = Unescape(Get(rec, "Race"));
    info.clazz = Unescape(Get(rec, "Class"));
    info.faction = Unescape(Get(rec, "Faction"));
    info.cell = Unescape(Get(rec, "Cell"));
    info.pcFaction = Unescape(Get(rec, "PCFaction"));
    info.voice = Unescape(Get(rec, "Voice"));
    info.response = Unescape(Get(rec, "Response"));
    info.resultScript = Unescape(Get(rec, "ResultScript"));
    info.questStatus = Get(rec, "QuestStatus");
    AddConditions(rec, info);
    return info;
}

// One response as OpenMW's InfoOrder holds it: the two ids it orders by.
struct Ordered {
    ESM::RefId mId;
    ESM::RefId mPrev;
    const Info* info = nullptr;
};

// Per view, the layers it sees; view 0 sees every layer. And per layer, its
// view: layers that see the same sidecars share one merged order.
std::vector<std::vector<bool>> g_views;
std::vector<int> g_viewOf;

void BuildViews() {
    const std::size_t count = LayerCount();
    g_views.assign(1, std::vector<bool>(count, true));
    g_viewOf.assign(count, 0);
    for (std::size_t layer = 0; layer < count; ++layer) {
        std::vector<bool> sees(count);
        for (std::size_t other = 0; other < count; ++other) {
            sees[other] = LayersRelated(static_cast<int>(layer),
                                        static_cast<int>(other));
        }
        const auto found = std::find(g_views.begin(), g_views.end(), sees);
        g_viewOf[layer] = static_cast<int>(found - g_views.begin());
        if (found == g_views.end()) g_views.push_back(std::move(sees));
    }
}

// One view's order: Dialogue::readInfo per response, then setUp.
std::vector<const Info*> MergeView(const std::vector<Ordered>& all,
                                   const std::vector<bool>& sees) {
    ESM::InfoOrder<Ordered> order;
    for (const Ordered& entry : all) {
        const int layer = entry.info->layer;
        const bool seen = layer < 0 || static_cast<std::size_t>(layer) >= sees.size() ||
                          sees[static_cast<std::size_t>(layer)];
        if (seen) order.insertInfo(Ordered(entry), entry.info->deleted);
    }
    order.removeDeleted();
    std::list<Ordered> merged;
    order.extractOrderedInfo(merged);
    std::vector<const Info*> out;
    out.reserve(merged.size());
    for (const Ordered& entry : merged) out.push_back(entry.info);
    return out;
}

// A sidecar that predates `Prev` holds the whole merged list, ordered by
// Ordinal: chaining each response to the one before it rebuilds that list.
// DEPRECATED: remove once no sidecar without `Prev` remains.
void ChainLegacy(std::vector<std::pair<int, Info>>& legacy,
                 std::vector<Info>& infos) {
    std::stable_sort(legacy.begin(), legacy.end(),
                     [](const auto& a, const auto& b) { return a.first < b.first; });
    std::string prev;
    for (auto& entry : legacy) {
        entry.second.prev = prev;
        prev = entry.second.id;
        infos.push_back(std::move(entry.second));
    }
}

// A DIAL staged by several sidecars is ONE topic whose responses each keep
// their own layer; the first sidecar's type stands.
void AddTopic(int layer, const Record& rec, StoreStats& stats) {
    const std::string id = Unescape(Get(rec, "EditorID"));
    if (id.empty()) return;
    const std::string key = Lower(id);
    Topic& topic = g_topics[key];
    if (topic.layers.empty()) {
        topic.id = id;
        topic.type = ParseDialType(Get(rec, "DialType"));
    }
    if (std::find(topic.layers.begin(), topic.layers.end(), layer) ==
        topic.layers.end()) {
        topic.layers.push_back(layer);
    }
    NoteId(layer, key);
    ++stats.topics;
}

std::size_t LoadOne(int layer, const std::string& dir, const char* name,
                    StoreStats& stats) {
    const std::string text = ReadFile(dir + name);
    if (text.empty()) return 0;
    const auto records = ParseExport(text);
    std::unordered_map<Topic*, std::vector<std::pair<int, Info>>> legacy;
    for (const Record& rec : records) {
        const std::string sig = Get(rec, "Signature");
        if (sig == kSigTopic) {
            AddTopic(layer, rec, stats);
        } else if (sig == kSigInfo) {
            Info info = MakeInfo(rec);
            info.layer = layer;
            const auto it = g_topics.find(Lower(info.topic));
            if (it == g_topics.end()) continue;
            if (!info.resultScript.empty()) ++stats.scripts;
            if (rec.count("Prev")) {
                it->second.infos.push_back(std::move(info));
            } else {
                legacy[&it->second].emplace_back(GetInt(rec, "Ordinal", 0),
                                                 std::move(info));
            }
            ++stats.infos;
        }
    }
    for (auto& [topic, infos] : legacy) ChainLegacy(infos, topic->infos);
    return records.size();
}

// `<Data>\SKSE\Plugins\MorrowindRuntime\` -> `<Data>\`, where the plugins
// whose headers name each layer's masters sit.
std::string DataDirOf(std::string root) {
    for (int up = 0; up < 3; ++up) {
        while (!root.empty() && (root.back() == '\\' || root.back() == '/')) {
            root.pop_back();
        }
        const std::size_t slash = root.find_last_of("\\/");
        root = slash == std::string::npos ? std::string() : root.substr(0, slash);
    }
    return root.empty() ? root : root + "\\";
}

}  // namespace

std::string Unescape(const std::string& text) {
    std::string out;
    out.reserve(text.size());
    for (std::size_t i = 0; i < text.size(); ++i) {
        if (text[i] != '\\' || i + 1 >= text.size()) {
            out.push_back(text[i]);
            continue;
        }
        switch (text[++i]) {
            case 'n': out.push_back('\n'); break;
            case 'r': out.push_back('\r'); break;
            case 't': out.push_back('\t'); break;
            case '\\': out.push_back('\\'); break;
            default: out.push_back('\\'); out.push_back(text[i]); break;
        }
    }
    return out;
}

std::vector<std::unordered_map<std::string, std::string>> ParseExport(
    const std::string& text) {
    std::vector<Record> out;
    std::istringstream in(text);
    std::string line;
    Record cur;
    bool open = false;
    while (std::getline(in, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line == kBegin) {
            cur.clear();
            open = true;
            continue;
        }
        if (line == kEnd) {
            if (open) out.push_back(cur);
            open = false;
            continue;
        }
        if (!open) continue;
        const std::size_t eq = line.find('=');
        if (eq == std::string::npos) continue;
        // First wins: a repeated key is the header echo of a body line.
        cur.emplace(line.substr(0, eq), line.substr(eq + 1));
    }
    return out;
}

std::string ReadFile(const std::string& path) {
    std::ifstream in(path, std::ios::binary);
    if (!in) return "";
    std::ostringstream ss;
    ss << in.rdbuf();
    return ss.str();
}

namespace {

// The staged tables whose rows name a BASE record as `...Plugin.esm|FormID...`
// -- a quest, a base object, an item, a faction, a GLOB. A base
// record resolves whenever its plugin is loaded; a placed reference does not
// while its cell is unloaded, which is why SCPT_instances is not here.
constexpr const char* kOwnFormTables[] = {
    "quests_formid.txt", "bases_formid.txt", "items_formid.txt",
    "factions_formid.txt", "GLOB.txt", "attributes_formid.txt", "chargen.txt",
    "stats.txt"};

// `Plugin.esm|FormID` out of one row's value, when the file is `plugin`'s own.
// The file is whatever follows the last ',' before the first '|' (GLOB rows
// carry `type,value,` ahead of it).
bool OwnFormIn(const std::string& value, const std::string& plugin,
               OwnForm* out) {
    const std::size_t bar = value.find('|');
    if (bar == std::string::npos) return false;
    const std::size_t comma = value.rfind(',', bar);
    const std::size_t start = comma == std::string::npos ? 0 : comma + 1;
    const std::string file = value.substr(start, bar - start);
    const std::size_t dot = file.rfind('.');
    if (dot == std::string::npos || Lower(file.substr(0, dot)) != Lower(plugin)) {
        return false;
    }
    const std::uint32_t formId = static_cast<std::uint32_t>(
        std::strtoul(value.c_str() + bar + 1, nullptr, 16));
    if (!(formId & 0x00FFFFFF)) return false;
    out->file = file;
    out->formId = formId;
    return true;
}

}  // namespace

bool FindOwnForm(const std::string& dir, const std::string& plugin,
                 OwnForm* out) {
    for (const char* table : kOwnFormTables) {
        std::ifstream in(dir + table, std::ios::binary);
        std::string line;
        while (std::getline(in, line)) {
            const std::size_t eq = line.find('=');
            if (eq != std::string::npos &&
                OwnFormIn(line.substr(eq + 1), plugin, out)) {
                return true;
            }
        }
    }
    return false;
}

namespace {
SidecarLoadedFn g_sidecarLoaded = nullptr;
}  // namespace

void SetSidecarLoadedCheck(SidecarLoadedFn check) { g_sidecarLoaded = check; }

std::vector<std::string> SidecarPlugins(const std::string& root) {
    std::vector<std::string> plugins;
    WIN32_FIND_DATAA find;
    HANDLE handle = FindFirstFileA((root + "*").c_str(), &find);
    if (handle == INVALID_HANDLE_VALUE) return plugins;
    do {
        if (!(find.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY)) continue;
        const std::string name = find.cFileName;
        if (name == "." || name == "..") continue;
        if (g_sidecarLoaded && !g_sidecarLoaded(root, name)) continue;
        plugins.push_back(name);
    } while (FindNextFileA(handle, &find));
    FindClose(handle);
    return plugins;
}

StoreStats LoadStore() { return LoadStoreFrom(SidecarDir()); }

StoreStats LoadStoreFrom(const std::string& rootIn) {
    g_topics.clear();
    ClearLayers();
    StoreStats stats;
    if (rootIn.empty()) {
        Log("store: no sidecar root -- GetModuleFileNameA gave nothing");
        return stats;
    }
    std::string root = rootIn;
    if (root.back() != '\\' && root.back() != '/') root.push_back('\\');
    const std::vector<std::string> plugins = SidecarPlugins(root);
    Log("store: root '%s' -> %zu plugin folder(s)", root.c_str(),
        plugins.size());
    for (const std::string& plugin : plugins) AddLayer(plugin);
    FinishLayers(DataDirOf(root));

    // DIAL first for every plugin: an INFO is dropped unless its topic exists.
    for (const std::string& plugin : plugins) {
        const std::string dir = root + plugin + "\\";
        const bool ok = LoadOne(LayerIndex(plugin), dir, kFileTopics, stats);
        Log("store:   %s/%s %s", plugin.c_str(), kFileTopics,
            ok ? "loaded" : "MISSING or empty");
        if (ok) ++stats.files;
    }
    ClearScriptTables();
    ClearChargenTables();
    LoadSharedChargen(root);
    for (const std::string& plugin : plugins) {
        LoadScriptTables(root + plugin + "\\");
        LoadChargenTable(root + plugin + "\\", LayerIndex(plugin));
        LoadStatsTable(root + plugin + "\\", LayerIndex(plugin));
    }
    // Load order, masters first, as OpenMW reads its content files.
    std::vector<std::string> byDepth = plugins;
    std::stable_sort(byDepth.begin(), byDepth.end(),
                     [](const std::string& a, const std::string& b) {
                         return LayerDepth(LayerIndex(a)) <
                                LayerDepth(LayerIndex(b));
                     });
    for (const std::string& plugin : byDepth) {
        LoadOne(LayerIndex(plugin), root + plugin + "\\", kFileInfos, stats);
    }
    BuildViews();
    for (auto& entry : g_topics) OrderTopic(entry.second);
    Log("store: %zu actor(s), %zu journal quest(s), %zu global(s), %zu "
        "script(s) with locals, %zu scripted object(s)%s", ActorCount(),
        QuestCount(), GlobalCount(), ScriptCount(), ActorScriptCount(),
        GlobalCount() == 0 ? " -- this sidecar predates the script tables; "
                               "restage it or result scripts that name a "
                               "global will not compile" : "");
    return stats;
}

const std::unordered_map<std::string, Topic>& Topics() { return g_topics; }

bool TopicVisible(const Topic& topic) {
    for (const int layer : topic.layers) {
        if (LayerVisible(layer)) return true;
    }
    return false;
}

void OrderTopic(Topic& topic) {
    if (g_views.empty()) BuildViews();
    std::vector<Ordered> all;
    all.reserve(topic.infos.size());
    for (const Info& info : topic.infos) {
        all.push_back({ESM::RefId::stringRefId(info.id),
                       ESM::RefId::stringRefId(info.prev), &info});
    }
    topic.views.clear();
    for (const std::vector<bool>& sees : g_views) {
        topic.views.push_back(MergeView(all, sees));
    }
}

const std::vector<const Info*>& ViewInfos(const Topic& topic) {
    static const std::vector<const Info*> kNone;
    const int layer = CurrentLayer();
    const std::size_t view =
        layer >= 0 && static_cast<std::size_t>(layer) < g_viewOf.size()
            ? static_cast<std::size_t>(g_viewOf[static_cast<std::size_t>(layer)])
            : 0;
    return view < topic.views.size() ? topic.views[view] : kNone;
}

const Topic* FindTopic(const std::string& id) {
    const auto it = g_topics.find(Lower(id));
    return it == g_topics.end() || !TopicVisible(it->second) ? nullptr
                                                             : &it->second;
}

}  // namespace tesruntime::mw
