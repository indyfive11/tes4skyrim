// The plugin's log: one time-stamped line per call, written straight to the
// file so it can be read while the game runs and survives a hang.

#pragma once

namespace interior_bounds {

// False when the log file cannot be created; the plugin then only looks.
bool LogOpen();
void Log(const char* fmt, ...);
void LogLine(const char* text);

}  // namespace interior_bounds
