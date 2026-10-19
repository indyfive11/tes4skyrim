"""Splice the Morrowind NPC animation clips into the SSE humanoid master graph.

`0_master` gains one root state holding a state machine with a looping clip
per converted group, the shape vanilla's `Cart_State` has: each clip is
entered from anywhere by its own global wildcard event and the state leaves
on the engine's `IdleStop` or on `TES3AnimStop`. The clips are then
registered in the DefaultMale/DefaultFemale character files and animation
cache projects.
See: docs/commentary/asset_convert_animation.md#morrowind-npc-animation-files
"""

import os
import shutil
from types import SimpleNamespace

from asset_convert.havok.behavior_nodes import F_GLOBAL
from asset_convert.havok.gun_graph_falloutnv import GunGraphBuilder
from asset_convert.havok.humanoid_graph import (THIRD_PERSON, HumanoidGraph,
                                                load_vanilla, param_text,
                                                register_clips)
from asset_convert.havok.npc_anim_morrowind import anim_layout

#: The graph every humanoid's root machine lives in, and that machine's name.
MASTER = 'behaviors\\0_master.hkx'
ROOT_MACHINE = 'Master_Behavior'

#: The root state the clips live in, and the machine inside it.
STATE_NAME = 'TES3Anim_State'
MACHINE_NAME = 'TES3Anim'

#: What leaves the state: the engine's own idle stop, and MorrowindRuntime's.
ENGINE_STOP = 'IdleStop'
STOP_EVENT = 'TES3AnimStop'


def patch_master(graph: HumanoidGraph, clips: list, out_path: str) -> list:
    """Add the clip machine as a new root state and compile; the clip generators created."""
    for name in [STOP_EVENT] + [c['event'] for c in clips]:
        graph.add_event(name)
    entries = SimpleNamespace(entries={c['stem']: c for c in clips})
    builder = GunGraphBuilder(graph.events, graph.variables, graph.next_id(),
                              entries)
    states = [builder.state_c(i, c['stem'],
                              builder.clip(c['stem'], c['stem'], True).ref)
              for i, c in enumerate(clips)]
    wild = builder.trans_c([(c['event'], i, None, 0, F_GLOBAL)
                            for i, c in enumerate(clips)])
    machine = builder.state_machine(MACHINE_NAME, states, wildcard_ref=wild)
    graph.splice(builder.render(machine))
    root = graph.find(ROOT_MACHINE, 'hkbStateMachine')
    state_id = max(int(param_text(graph.obj(r), 'stateId'))
                   for r in graph.ref_list(root, 'states')) + 1
    graph.add_state(root, state_id, STATE_NAME, machine.ref)
    for event in (ENGINE_STOP, STOP_EVENT):
        graph.add_transition(graph.state_of(root, state_id), event, 0)
    graph.write(out_path)
    return builder.generators


def build_npc_graphs(manifest: dict, out_meshes_dir: str, work_dir: str,
                     log=print) -> dict:
    """Patch the humanoid master graph for the manifest's clips; the fragment appends.

    Returns {} when the manifest has no clips.
    """
    clips = manifest.get('clips') or []
    if not clips:
        return {}
    char_out = os.path.join(out_meshes_dir, 'actors', 'character')
    graph = load_vanilla(MASTER, work_dir, '0_master')
    if graph is None:
        raise FileNotFoundError(f'vanilla {MASTER} is not available')
    generators = patch_master(
        graph, clips, os.path.join(char_out, *MASTER.split('\\')))
    animdata = register_clips({c['stem']: c for c in clips}, generators,
                              char_out, work_dir)
    log(f'  NPC animation graph: {len(generators)} clips in {MASTER}, '
        f'registered in {len(animdata)} projects')
    return {'animdata_append': animdata}


def remove_npc_graphs(out_meshes_dir: str, log=print) -> None:
    """Delete the clips and humanoid graph patch an earlier build shipped.

    Only for a plugin without guns: the gun patch ships the same humanoid files.
    The runtime table stays: the import stage writes it (npc_anim_table).
    """
    char_out = os.path.join(out_meshes_dir, 'actors', 'character')
    patched = [os.path.join(char_out, *rel.split('\\'))
               for rel in [MASTER] + [rel for rel, _project in THIRD_PERSON]]
    stale = [p for p in patched if os.path.isfile(p)]
    for path in stale:
        os.remove(path)
    clips = anim_layout(out_meshes_dir)['dir']
    if os.path.isdir(clips):
        shutil.rmtree(clips)
        stale.append(clips)
    if stale:
        log(f'  Removed {len(stale)} stale NPC animation outputs (conversion is off)')
