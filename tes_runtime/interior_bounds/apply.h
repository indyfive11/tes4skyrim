// The write path: places the stub near the site and redirects the site to
// it. Not compiled into a look-only build, which therefore cannot write.

#pragma once

#include <cstdint>

#include "detour.h"
#include "locate.h"

namespace interior_bounds {

// `base` is the game's module, `image_size` its size, `found` a search that
// ended in one site. Logs what it did; false when nothing was changed.
bool Apply(Mode mode, std::uint8_t* base, std::uint64_t image_size,
           const Found& found);

}  // namespace interior_bounds
