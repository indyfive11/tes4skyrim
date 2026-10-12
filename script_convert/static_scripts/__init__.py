"""The Papyrus scripts a masterless converted plugin ships as written here.

See: docs/commentary/script_convert.md#static-scripts-ownership
"""

import os

#: This folder, which holds the .psc files.
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))


def static_script_files() -> list:
    """The .psc files shipped here, by name."""
    return sorted(name for name in os.listdir(STATIC_DIR) if name.endswith('.psc'))
