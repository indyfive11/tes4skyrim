"""
Settings ▸ Morrowind source: the GUI half of the Morrowind master switch.

The export stage reads the chosen set from `conversion_config.json`, so the
radio group saves on every change and nothing else has to be plumbed. Choosing
Morroblivion also builds the compatibility patch when it is missing, because
that mode refuses every conversion without it, and asks whether to rebuild it
when it exists.

See: docs/commentary/tes4_export_morrowind.md#masters
"""

import sys
import tkinter as tk
from tkinter import filedialog

from core.gui import runner
from core.gui.config import REPO_ROOT
from core.gui.menubar_behavior import enable_tips
from tes4_export.export_morrowind import MORROWIND_SOURCE_KEY, SOURCE_MORROBLIVION, SOURCE_VANILLA, morroblivion_exports
from tes4_export.morrowind_patch import PATCH_NAME, PATCH_SOURCES, patch_exists, source_dir, source_paths

#: Source set -> its menu label, in menu order.
_LABELS = (
    (SOURCE_VANILLA, "Vanilla  (Morrowind + Tribunal + Bloodmoon)"),
    (SOURCE_MORROBLIVION, "Morroblivion + patch"),
)

#: Title of every dialog the patch build raises.
_TITLE = "Morroblivion compatibility patch"

#: Menu tip on the Morroblivion entry.
MORROBLIVION_TIP = (
    f"Convert Morrowind plugins against Morroblivion and {PATCH_NAME}. "
    "Builds the patch if it is missing (convert Morrowind_ob.esm first); "
    "asks whether to rebuild it if it exists")


def source_default(cfg: dict) -> str:
    """The configured source set; anything unrecognised reads as vanilla."""
    value = str(cfg.get(MORROWIND_SOURCE_KEY, "")).strip().lower()
    return value if value in dict(_LABELS) else SOURCE_VANILLA


def add_source_menu(app, settings_menu, menu_opts: dict, cfg: dict,
                    load_config, save_config, export_dir) -> tk.StringVar:
    """Add Settings ▸ Morrowind source as a radio cascade saved on change."""
    var = tk.StringVar(value=source_default(cfg))
    chosen = {"mode": var.get()}

    def _save():
        """Persist the chosen set and remember it as the current one."""
        chosen["mode"] = var.get()
        updated = load_config()
        updated[MORROWIND_SOURCE_KEY] = var.get()
        save_config(updated)

    def _pick_morroblivion():
        """Switch, building the patch first when it is missing or asked for."""
        was = chosen["mode"]
        if patch_exists(str(export_dir)) and not app.confirm(
                _TITLE, f"{PATCH_NAME} already exists.\n\nRebuild it?",
                yes="Rebuild", no="Keep"):
            _save()
            return
        if build_patch_dialog(app, str(export_dir)):
            _save()
        else:
            var.set(was)

    menu = tk.Menu(settings_menu, **menu_opts)
    enable_tips(menu)
    menu.add_radiobutton(label=dict(_LABELS)[SOURCE_VANILLA],
                         value=SOURCE_VANILLA, variable=var, command=_save)
    menu.add_radiobutton(label=dict(_LABELS)[SOURCE_MORROBLIVION],
                         value=SOURCE_MORROBLIVION, variable=var,
                         command=_pick_morroblivion)
    menu.entry_tips[menu.index("end")] = MORROBLIVION_TIP
    settings_menu.add_cascade(label="Morrowind source", menu=menu)
    return var


def _morrowind_data_dir(app, export_dir: str) -> str:
    """The Morrowind Data Files folder: the registered install, else asked for.

    Returns "" when the user cancels or picks a folder without the masters.
    """
    known = source_dir(export_dir)
    if known and not source_paths(known, PATCH_SOURCES)[1]:
        return known
    data_dir = filedialog.askdirectory(
        title="Select your Morrowind 'Data Files' folder")
    if not data_dir:
        return ""
    _, missing = source_paths(data_dir, PATCH_SOURCES)
    if missing:
        app.info(
            _TITLE,
            "That folder is not a Morrowind Data Files directory.\n\n"
            f"Looked in:\n{data_dir}\n\nMissing:\n  " + "\n  ".join(missing))
        return ""
    return data_dir


def build_patch_dialog(app, export_dir: str) -> bool:
    """Start building the patch as a run in the main log pane; False when it could not start.

    Morroblivion has to be converted first -- the patch holds what it does NOT
    supply, so without it there is no gap to measure.
    """
    if app.running.is_set():
        app.info(_TITLE, "Wait for the current run to finish, "
                         "then choose Morroblivion again.")
        return False
    if not morroblivion_exports(export_dir):
        app.info(
            _TITLE,
            "No converted Morroblivion plugin was found.\n\n"
            f"{PATCH_NAME} holds the objects Morroblivion does NOT convert, so "
            "convert Morrowind_ob.esm first, then choose Morroblivion again.")
        return False
    data_dir = _morrowind_data_dir(app, export_dir)
    if not data_dir:
        return False
    cmd = [sys.executable, "-u", str(REPO_ROOT / "convert.py"),
           "--build-morrowind-patch", data_dir,
           "--output-dir", str(app.out_root())]
    runner.start_command(app, f"Build {PATCH_NAME}", cmd, PATCH_NAME,
                         lambda _key: None)
    return True
