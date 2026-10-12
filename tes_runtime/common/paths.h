// Where a plugin's files live: its name, its data folder under
// Data\SKSE\Plugins, and the SKSE log folder.

#pragma once

#include <string>

namespace tesruntime {

// Names the plugin ("TESRuntime", "MorrowindRuntime", ...). The log file and
// the sidecar folder both take it, so it is set first thing in
// SKSEPlugin_Load, before OpenLog.
void SetPluginName(const char* name);
const std::string& PluginName();

// Data\SKSE\Plugins\<plugin name>\, with the trailing backslash: where the
// converter puts this plugin's sidecars. "" when the game path is unknown.
std::string SidecarDir();

// <game folder>\Data\SKSE\Plugins\, with the trailing backslash, "" when
// unknown. Built from the game exe, never this DLL's own path: a mod manager
// merges mods only under the game's Data folder.
// See: docs/commentary/morrowind_runtime.md#sidecar
std::string PluginsDir();

// The SKSE log folder with a trailing backslash, or "" when Documents is
// unknown; every file a plugin writes for a human goes here.
std::wstring LogDir();

}  // namespace tesruntime
