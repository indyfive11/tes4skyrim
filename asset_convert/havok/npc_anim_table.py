"""The table MorrowindRuntime plays NPC animations from, `<plugin>.anims.txt`.

`clip.<slug>.<group>=event|seconds`, `actor.<npc id>=slug|chances|distance`
and `place.<placement FormID>=plugin|npc id|x,y,z,rx,ry,rz`. The import stage
writes it for NPCs whose animation file became a vanilla idle; the creature
stage's NPC animation conversion, when on, writes it for the converted clips.
See: docs/commentary/morrowind_runtime.md#npc-animation-files
"""

import math
import os

#: Where MorrowindRuntime reads a plugin's animation table, beside its sidecar folder.
RUNTIME_DIR = os.path.join('SKSE', 'Plugins', 'MorrowindRuntime')
RUNTIME_SUFFIX = '.anims.txt'


def table_path(plugin_out: str, plugin_name: str) -> str:
    """The table's path for one plugin under its output folder."""
    stem = os.path.splitext(os.path.basename(plugin_name))[0]
    return os.path.join(plugin_out, RUNTIME_DIR, stem + RUNTIME_SUFFIX)


def placement_pose(rec: dict) -> str:
    """A placement's authored `x,y,z,rx,ry,rz`, the angles in degrees."""
    at = [float(rec.get(key) or 0) for key in ('PosX', 'PosY', 'PosZ')]
    turn = [math.degrees(float(rec.get(key) or 0)) for key in ('RotX', 'RotY', 'RotZ')]
    return ','.join(f'{value:.3f}' for value in at + turn)


def write_table(plugin_out: str, plugin_name: str, clips: list, actors: list,
                places: list) -> str:
    """Write the table; its path.

    `clips` holds (slug, group, event, seconds), `actors` (npc id, slug,
    `chances|distance`) and `places` (placement FormID, npc id, pose).
    """
    lines = [f'clip.{slug}.{group}={event}|{seconds:.3f}'
             for slug, group, event, seconds in clips]
    lines += [f'actor.{npc}={slug}|{wander}' for npc, slug, wander in sorted(actors)]
    lines += [f'place.{form_id}={plugin_name}|{npc}|{pose}'
              for form_id, npc, pose in sorted(places)]
    path = table_path(plugin_out, plugin_name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(lines) + '\n')
    return path


def remove_table(plugin_out: str, plugin_name: str) -> None:
    """Delete the table an earlier build left for a plugin that now binds no animation."""
    path = table_path(plugin_out, plugin_name)
    if os.path.isfile(path):
        os.remove(path)
