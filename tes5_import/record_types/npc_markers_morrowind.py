"""Morrowind NPC animation files played through vanilla furniture and idles.

An NPC that names its own animation file (`MorrowindAnim`) sits, lies, kneels
or works through that file's idles in place. Most have a vanilla counterpart.
A seat: each placement of such an NPC gets the furniture as a new reference at
its authored spot, linked from the actor (XLKR), and the NPC's wander package
becomes SitTarget on that linked reference. Any other pose: the package
becomes DoNothing, and MorrowindRuntime holds each placement on its authored
spot and plays the vanilla idle (the npc_anim_table rows, write_idle_table);
the engine otherwise moves an actor standing off navmesh onto it.
See: docs/commentary/asset_convert_animation.md#vanilla-markers-for-npc-animation-files
"""

import math
import os
import struct

from asset_convert.havok.npc_anim_table import placement_pose, remove_table, write_table

from ..base.text_reader import get_formid, get_int
from ..packages.converter import MARKER_IDLE, MARKER_KEY, MARKER_SIT
from .common import pack_subrecord
from .npc_morrowind import is_morrowind_npc

#: derive_formid site of a marker per placement, keyed by its ACHR.
_REF_SITE = 'MW_NPC_MARKER'

#: TES3 PKDT.Type of a wander package, the only kind whose idles the files drive.
_WANDER = 5

#: A placement's flag bit that a persistent actor's marker copies.
_PERSISTENT = 0x400

#: Vanilla furniture markers (SitTarget), Skyrim.esm FormIDs.
_CHAIR, _LEDGE, _CROSS, _LAY = 0x0006411B, 0x000C4827, 0x000C4EFD, 0x000C482E
_PRAY, _PICKAXE, _HAMMER = 0x000F11E9, 0x000E2BC7, 0x000C4328

#: Vanilla idle events (IDLE ENAM) and seconds between plays: 0 loops, a one-shot keeps its marker's timer.
_SWEEP, _BOOK, _DRINK = ('idleLooseSweepingStart', 0), ('IdleBook_Read', 0), ('idleBarDrinkingStart', 0)
_DANCE, _COUGH, _PICKUP = ('IdleCiceroDance1', 0), ('IdleUncontrollableCough', 10), ('IdlePickup_Ground', 10)

#: Animation file -> (furniture, yaw degrees, local x, y, z); offsets put the pelvises together.
_SEATS = {
    'arktwend/ani/sitzgespräch.nif': (_CHAIR, 3, 2.5, -2.4, 8.8),
    'arktwend/ani/sitzdrohen.nif': (_CHAIR, 4, 2.5, -2.2, 10.1),
    'arktwend/ani/anim_sit.nif': (_CHAIR, 4, 2.5, -2.2, 10.1),
    'arktwend/ani/sitzenbein.nif': (_CHAIR, 78, -22.8, -29.5, 6.3),
    'am/am_eater.nif': (_CHAIR, 3, 2.3, -2.4, 14.1),
    'am/am_luteplaying.nif': (_CHAIR, 4, 2.3, 1.2, 14.5),
    'am/am_fishman.nif': (_LEDGE, 7, -3.3, 0.7, 4.0),
    'am/am_beggar.nif': (_CROSS, -12, -6.8, 3.4, -0.6),
    'am/am_dreamera.nif': (_CROSS, -26, -6.9, 1.7, 1.5),
    'arktwend/ani/schlafen.nif': (_LAY, 14, 34.7, -6.8, 4.2),
    'arktwend/ani/schlafenm.nif': (_LAY, 38, -20.4, 12.9, 5.2),
    'arktwend/ani/liegenm_br.nif': (_LAY, 3, 4.4, 12.7, 0.1),
    'am/ii/prayerdf.nif': (_PRAY, 9, -0.8, -5.9, 8.4),
    'am/ii/miner.nif': (_PICKAXE, 0, -5.9, 12.8, 5.1),
    'am/am_smith.nif': (_HAMMER, 0, 0.2, 0.4, 8.8),
}

#: Animation file -> the vanilla idles that stand in for it.
_IDLES = {
    'am/am_sweeping.nif': _SWEEP,
    'am/am_reader1.nif': _BOOK,
    'arktwend/ani/bar.nif': _DRINK,
    'arktwend/ani/bar2.nif': _DRINK,
    'arktwend/ani/trinken0x2.nif': _DRINK,
    'am/am_cough.nif': _COUGH,
    'am/ii/farmer.nif': _PICKUP,
    'am/ii/farmer2.nif': _PICKUP,
    **{f'{path}.nif': _DANCE for path in (
        'anim_dancinggirl', 'arktwend/ani/tanzdynamic', 'arktwend/ani/tanzsexlo',
        'arktwend/ani/tanzsexfnb', 'arktwend/ani/xtanzsexfnb', 'arktwend/ani/rocknroll',
        'arktwend/ani/rocknrolla', 'arktwend/ani/aliroman',
        'arktwend/ani/aliroman2', 'arktwend/ani/sexgehen0x2')},
}


#: Animation file -> the prop its vanilla stand-in already carries, as the prop armor's part mesh.
_CARRIED = {
    'am/am_sweeping.nif': 'am/am_broomshield.nif',
    'am/am_reader1.nif': 'am/am_book.nif',
    'am/am_smith.nif': 'am/am_smithhammer.nif',
    'am/ii/miner.nif': 'am/ii/minerpick.nif',
}


def _slashed(path) -> str:
    """A TES3 mesh path lowercase with forward slashes."""
    return (path or '').replace(chr(92) * 2, '/').replace(chr(92), '/').strip().lower()


def _anim_path(rec: dict) -> str:
    """An NPC's own animation file, lowercase with forward slashes."""
    return _slashed(rec.get('MorrowindAnim'))


def _drop_carried_props(by_type: dict, marked: dict) -> int:
    """Take from each marked NPC the prop armor its vanilla stand-in already
    carries (_CARRIED), renumbering its inventory; how many items dropped."""
    meshes = {a['FormID']: _slashed(a.get('MorrowindPart[0].Male')) for a in by_type.get('ARMO', [])}
    dropped = 0
    for npc in by_type.get('NPC_', []):
        prop = _CARRIED.get(_anim_path(npc))
        if prop is None or npc['FormID'] not in marked:
            continue
        count = get_int(npc, 'ItemCount', 0)
        items = [{key[len(f'Item[{i}]'):]: npc.pop(key) for key in list(npc)
                  if key.startswith(f'Item[{i}].')} for i in range(count)]
        kept = [item for item in items if meshes.get(item.get('.FormID')) != prop]
        for i, item in enumerate(kept):
            npc.update({f'Item[{i}]{field}': value for field, value in item.items()})
        npc['ItemCount'] = str(len(kept))
        dropped += count - len(kept)
    return dropped


def _plan(npc: dict, placed: set):
    """(MARKER_SIT, seat row) or (MARKER_IDLE, idle) for an NPC the plugin places; else None."""
    path = _anim_path(npc)
    if npc['FormID'] not in placed:
        return None
    if path in _SEATS:
        return MARKER_SIT, _SEATS[path]
    if path in _IDLES:
        return MARKER_IDLE, _IDLES[path]
    return None


def _wander_of(npc: dict, packs: dict):
    """The NPC's first wander package record, or None."""
    for index in range(get_int(npc, 'AIPackageCount', 0)):
        pack = packs.get(npc.get(f'AIPackage[{index}]', ''))
        if pack is not None and get_int(pack, 'PKDT.Type', -1) == _WANDER:
            return pack
    return None


def _shared_key(plan):
    """What every NPC on one package must agree on: seated, or the very same idle."""
    return plan if plan is None or plan[0] == MARKER_IDLE else MARKER_SIT


def _marked_npcs(by_type: dict, placed: set) -> dict:
    """{NPC FormID: plan} for NPCs whose wander package now seats or holds them.

    A package is flagged when every NPC running it has the same plan and is
    placed by this plugin (one spawned by a script has no spot to keep): all
    seated, or all playing the same idle. Any other keeps its Sandbox.
    """
    packs = {p.get('FormID'): p for p in by_type.get('PACK', [])}
    users = {}
    for npc in by_type.get('NPC_', []):
        pack = _wander_of(npc, packs)
        if pack is not None:
            users.setdefault(pack['FormID'], []).append((npc['FormID'], _plan(npc, placed)))
    out = {}
    for pack_id, members in users.items():
        keys = {_shared_key(plan) for _npc, plan in members}
        if len(keys) == 1 and None not in keys:
            packs[pack_id][MARKER_KEY] = members[0][1][0]
            out.update(members)
    return out


def _turn(x: float, y: float, yaw: float) -> tuple:
    """A local xy offset in the world, for a reference turned clockwise by `yaw`."""
    return (x * math.cos(yaw) + y * math.sin(yaw), -x * math.sin(yaw) + y * math.cos(yaw))


def _marker_ref(achr: dict, row: tuple, raw: int, base: int) -> dict:
    """A marker reference at the placement, turned and shifted so the poses meet."""
    yaw = float(achr.get('RotZ') or 0)
    dx, dy = _turn(row[2], row[3], yaw)
    persistent = get_int(achr, 'RecordFlags') & _PERSISTENT
    return {'Signature': 'REFR', 'FormID': '%08X' % raw, 'NAME': '00000000',
            'NAME.Final': '%08X' % base, 'RecordFlags': str(persistent),
            'ParentCELL': achr.get('ParentCELL', ''),
            'PosX': str(float(achr.get('PosX') or 0) + dx),
            'PosY': str(float(achr.get('PosY') or 0) + dy),
            'PosZ': str(float(achr.get('PosZ') or 0) + row[4]),
            'RotX': '0', 'RotY': '0', 'RotZ': str(yaw + math.radians(row[1]))}


#: The marker REFRs planned this run, held back from the navmesh pass (flush_npc_markers).
_PENDING: list = []

#: This run's held placements: (placement FormID, NPC EditorID, pose, (event, seconds) or None for no idle).
_HELD: list = []


def _packageless(by_type: dict) -> set:
    """FormIDs of Morrowind NPCs with no AI package; they stay where placed."""
    return {npc['FormID'] for npc in by_type.get('NPC_', [])
            if is_morrowind_npc(npc) and get_int(npc, 'AIPackageCount', 0) == 0}


def place_npc_markers(by_type: dict, writer, own_index: int) -> int:
    """Plan a marker for every own placement of a seated NPC; how many.

    Flags wander packages (MARKER_KEY), drops props the stand-ins carry, and
    sets each seated placement's `XLKR.Reference` before the package and NPC
    converters run; the REFRs wait for flush_npc_markers, so no navmesh input
    changes. Idle placements, and those of NPCs with no package, wait for
    write_idle_table.
    """
    _PENDING.clear()
    _HELD.clear()
    own = [a for a in by_type.get('ACHR', []) if int(a['FormID'], 16) >> 24 == own_index]
    marked = _marked_npcs(by_type, {a.get('NAME') for a in own})
    _drop_carried_props(by_type, marked)
    names ={npc['FormID']: npc.get('EditorID') or npc['FormID'] for npc in by_type.get('NPC_', [])}
    still = _packageless(by_type)
    for achr in own:
        kind, row = marked.get(achr.get('NAME')) or (None, None)
        if kind is None and achr.get('NAME') in still:
            _HELD.append((achr['FormID'], names[achr['NAME']], placement_pose(achr), None))
        elif kind == MARKER_IDLE:
            _HELD.append((achr['FormID'], names[achr['NAME']], placement_pose(achr), row))
        elif kind == MARKER_SIT:
            fid = writer.derive_formid(_REF_SITE, achr['FormID'])
            raw = (own_index << 24) | (fid & 0xFFFFFF)
            _PENDING.append(_marker_ref(achr, row, raw, row[0]))
            achr['XLKR.Reference'] = '%08X' % raw
    return len(_PENDING)


def write_idle_table(output_path: str) -> int:
    """Write MorrowindRuntime's table of held placements beside the ESM (or
    delete a stale one); how many placements. A placement with no idle binds
    no clip, which the runtime reads as held only."""
    plugin_out, plugin = os.path.dirname(output_path), os.path.basename(output_path)
    held = list(_HELD)
    _HELD.clear()
    if not held:
        remove_table(plugin_out, plugin)
        return 0
    idles = {idle for _f, _n, _p, idle in held if idle is not None}
    write_table(plugin_out, plugin,
                [(event.lower(), 'idle', event, seconds) for event, seconds in sorted(idles)],
                {(npc, idle[0].lower() if idle else '', '|0') for _f, npc, _p, idle in held},
                [(form_id, npc, pose) for form_id, npc, pose, _i in held])
    return len(held)


def flush_npc_markers(by_type: dict) -> int:
    """Append the planned marker REFRs for the cell builders; how many."""
    by_type.setdefault('REFR', []).extend(_PENDING)
    count = len(_PENDING)
    _PENDING.clear()
    return count


def linked_ref_subrecord(rec: dict) -> bytes:
    """A placement's XLKR to its marker (no keyword), or b''."""
    ref = get_formid(rec, 'XLKR.Reference')
    return pack_subrecord('XLKR', struct.pack('<II', 0, ref)) if ref else b''


def final_base(rec: dict):
    """A generated reference's base FormID, already in the output's space, or None."""
    value = rec.get('NAME.Final')
    return int(value, 16) if value else None
