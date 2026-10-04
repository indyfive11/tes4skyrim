"""Behaviour graphs for animated OBJECTS (activators/doors), not actors.

Why this exists
---------------
Oblivion animates a door/secret wall entirely inside the NIF: a
`NiControllerManager` holds named `NiControllerSequence`s ('Forward',
'Backward', 'Open', ...) and the script says `playgroup forward 1`.

Skyrim does NOT drive in-NIF sequences from script.  `ObjectReference` exposes
two different animation paths (verified in the SSE executable's Papyrus native
function table):

    PlayAnimation / PlayAnimationAndWait  -> behaviour-graph animation
    PlayGamebryoAnimation                 -> in-NIF NiControllerSequence

and `PlayAnimation` needs an *animation graph manager*, which only exists when
the NIF carries a `BSBehaviorGraphExtraData` (BGED) pointing at an hkx project.
Without one the call is accepted, returns immediately and does nothing — the
exe's own diagnostic string is "No reference selected or it has no animation
graph manager."  That is why CharacterGen's secret wall never physically
opened: the quest ran, the switch fired, PlayAnimation("Forward") logged no
Papyrus error, and the wall stayed shut.

The bridge between the two worlds is `BGSGamebryoSequenceGenerator` — a
Bethesda behaviour-graph node whose only job is to play a NIF sequence by name
(the exe describes its parameter literally as "Gamebryo Sequence name").

Vanilla template
----------------
`NocturnalsSecretDoor01` (Clutter\\BlackPool\\BlackPoolSecretDoor) is the
reference: its NIF has sequences AnimIdle01 / AnimPlay01 / AnimIdle02 and its
graph contains `GamebryoSequenceGenerator00/01/02` wrapped in a
`hkbStateMachine`, reached through a BGED that points at a *project* file
(`hkbProjectData` -> `Characters\\Character01.hkx` -> `Behaviors\\
Behavior00.hkx`).  So one animated object needs a small four-file tree:

    <model>.hkx                     project      (this is what BGED names)
    Characters\\Character01.hkx      character
    CharacterAssets\\Skeleton.hkx    1-bone skeleton
    Behaviors\\Behavior00.hkx        the state machine + Gamebryo generators

Each NIF sequence becomes one state; the state's name is also the event that
selects it, so `PlayAnimation("Forward")` sends `Forward` and the state machine
transitions to the generator bound to the 'Forward' NiControllerSequence.
"""

import collections
import os
import shutil
import struct
import time

from asset_convert.havok.behavior_nodes import BINDING_TMPL, VARIABLE_INFO_TMPL
from asset_convert.havok.hkx_xml import HkxPackfile, compile_hkx, convert_hkx_to_amd64

# The one-bone reference pose as vanilla SingleBoneSkeleton.hkx stores it:
# three 16-byte hkVector4 slots — translation, rotation, scale.  The rotation
# lanes are (1,0,0,0): Havok's BINARY quaternion is w-first, which is not the
# xyzw order the packfile XML uses.
_POSE_TRANS = (0.0, 0.0, 0.0, 0.0)
_POSE_QUAT = (1.0, 0.0, 0.0, 0.0)
_POSE_SCALE = (1.0, 1.0, 1.0, 1.0)
_POSE_BYTES = struct.pack('<12f', *(_POSE_TRANS + _POSE_QUAT + _POSE_SCALE))
# What hkxcmd actually emits for the identity pose: same, but the rotation slot
# is all zeros (no valid rotation -> nothing renders).
_POSE_BROKEN = struct.pack('<12f', *(_POSE_TRANS + (0.0, 0.0, 0.0, 0.0)
                                     + _POSE_SCALE))


def fix_identity_quat(hkx_path: str) -> bool:
    """Rewrite the skeleton's zero reference-pose quaternion to identity.

    hkxcmd compiles `(0 0 0)(0 0 0 1)(1 1 1)` — the exact text every shipped
    creature skeleton uses — into a reference pose whose ROTATION SLOT IS ALL
    ZEROS.  A zero quaternion is not a rotation, so the engine had no valid
    bind pose for the single bone and the whole object rendered nothing while
    the graph itself loaded fine (prisonSecretWall01, 2026-07-26).

    Patch the compiled WIN32 packfile in place, before the AMD64 step.  Matches
    on the full 48-byte pose block so it cannot hit unrelated data, and is a
    no-op if a future hkxcmd ever writes the quaternion correctly.
    """
    with open(hkx_path, 'rb') as f:
        data = f.read()
    if _POSE_BYTES in data:
        return False                      # already correct
    idx = data.find(_POSE_BROKEN)
    if idx < 0:
        return False                      # unrecognised layout; leave alone
    if data.find(_POSE_BROKEN, idx + 1) >= 0:
        return False                      # ambiguous; refuse to guess
    with open(hkx_path, 'wb') as f:
        f.write(data[:idx] + _POSE_BYTES + data[idx + len(_POSE_BYTES):])
    return True

#: Flags of every per-state transition row, exactly as vanilla's Gamebryo machines write them.
_TRANSITION_FLAGS = 'FLAG_DISABLE_CONDITION'

# hkbStateMachineTimeInterval, "no interval restriction" — vanilla writes this
# same all -1 / 0.0 struct for every transition in the template.
_INTERVAL = (
    '\t\t<hkobject>\n'
    '\t\t\t<hkparam name="enterEventId">-1</hkparam>\n'
    '\t\t\t<hkparam name="exitEventId">-1</hkparam>\n'
    '\t\t\t<hkparam name="enterTime">0.000000</hkparam>\n'
    '\t\t\t<hkparam name="exitTime">0.000000</hkparam>\n'
    '\t\t</hkobject>')

_BLEND_DURATION = 0.0   # objects snap between sequences; no cross-fade

# Vanilla's name for a self-playing ambient sequence.  The state machine starts
# on it instead of the do-nothing Rest state (see behavior_xml).
_AUTOPLAY_SEQUENCE = 'AutoPlay'
_AUTOLOOP_SEQUENCE = 'AutoLoop'   # where the real ambient motion lives

#: BGED of vanilla's shared self-playing graph (all 63 AutoPlay meshes); relative to meshes\, no SoundPlay event.
VANILLA_AUTOPLAY_BGED = 'GenericBehaviors\\Autoplay.hkx'

#: Sequences an object plays from load, most preferred first; SpecialIdle is Oblivion's rest group when no Idle exists.
LOAD_SEQUENCES = (_AUTOLOOP_SEQUENCE, _AUTOPLAY_SEQUENCE, 'SpecialIdle')


def _start_state_id(sequences: list, rest_id: int) -> int:
    """Start state: the first `LOAD_SEQUENCES` name present, else `rest_id`."""
    return next((sequences.index(s) for s in LOAD_SEQUENCES if s in sequences),
                rest_id)

# Vanilla's fixed dummy bone name for single-bone animated objects
# (clutter\beehive\characterassets\SingleBoneSkeleton.hkx uses exactly this).
# The rig is a placeholder — the real motion lives in the NIF's
# NiControllerSequences — so the bone must NOT be named after anything in the
# NIF.  Naming it after the model made the engine bind the graph's identity
# bind pose onto the object and place it far from its authored position.
DUMMY_BONE = 'x_SingleBone'

#: Graph event a `SoundPlay.<SNDR>` NIF text key raises; every vanilla sounded object graph declares it.
SOUND_EVENT = 'SoundPlay'

#: Graph event a NIF `end` text key raises; it moves a finished sequence onto its hold state.
HOLD_EVENT = 'End'

#: Graph bool that reads 1 while a wrapped state is active: the name every converted `IsAnimPlaying` reads.
PLAYING_VARIABLE = 'bAnimPlaying'

#: Member of the one BSIsActiveModifier that writes PLAYING_VARIABLE, and the name prefix of a wrapper.
_ACTIVE_MEMBER = 'bIsActive0'
_WRAPPER_PREFIX = 'ModifierGenerator'

#: A compiled project awaiting its NIF: the BGED value, where it was built, and where it belongs.
StagedProject = collections.namedtuple('StagedProject', 'bged stage_dir final_dir')

#: Name prefix of every Gamebryo generator, and how far past it in the string pool its pSequence sits.
_GENERATOR_PREFIX = 'GamebryoSequenceGenerator'
_PSEQUENCE_WINDOW = 7

#: How often the staged tree's move into place is tried before it is copied instead, and the pause between.
_SWAP_TRIES = 3
_SWAP_PAUSE = 0.1

#: Folder suffixes of the final tree, one being built and one being replaced: equal length, for MAX_PATH.
_TREE_SUFFIX = '_behavior'
_STAGE_SUFFIX = '_hkxstage'
_ASIDE_SUFFIX = '_hkxaside'


def skeleton_xml(root_bone: str) -> str:
    """One-bone skeleton — an animated object has no rig of its own.

    The transforms live in the NIF sequences; Havok only needs a skeleton to
    exist so the character/behaviour pair is well-formed.
    """
    # Emission order matches vanilla SingleBoneSkeleton.hkx: resource
    # container, skeleton, animation container, root.
    pf = HkxPackfile(first_id=8)
    res = pf.add('hkMemoryResourceContainer')
    skel = pf.add('hkaSkeleton')
    anim = pf.add('hkaAnimationContainer')
    top = pf.add('hkRootLevelContainer')

    skel.param('name', root_bone)
    skel.param_array('parentIndices', [-1])
    skel.param_structs('bones', [[('name', root_bone), ('lockTranslation', 'false')]])
    # referencePose is emitted ONCE, here, before referenceFloats (same order as
    # hkx_skeleton.py).  Emitting an empty one first and appending the real one
    # later makes hkxcmd keep the EMPTY array: the skeleton then has 1 bone and
    # 0 poses, and binding a sequence indexes past the end -> null deref ->
    # CTD at `movdqu xmm2,[rax]` with rax=0 on cell load (prisonCellGate01,
    # 2026-07-26).  One entry per bone is mandatory.
    #
    # Text form is the same 3+4+3 `(t)(q xyzw)(s)` every shipped creature
    # skeleton uses (hkx_skeleton.build_skeleton_xml).  NOTE: hkxcmd writes the
    # quaternion lanes as ZERO for this identity value; a zero quaternion has no
    # valid rotation and the object renders nothing.  `fix_identity_quat` below
    # patches the compiled bytes to vanilla's (1,0,0,0).
    skel.param_raw('referencePose',
                   '(0.000000 0.000000 0.000000)'
                   '(0.000000 0.000000 0.000000 1.000000)'
                   '(1.000000 1.000000 1.000000)', numelements=1)
    skel.param_array('referenceFloats', [])
    skel.param_raw('floatSlots', '', numelements=0)
    skel.param_raw('localFrames', '', numelements=0)

    # Order per vanilla SingleBoneSkeleton.hkx: skeletons FIRST, and there is
    # no `attachmentNames` member on this class.
    anim.param_raw('skeletons', skel.ref, numelements=1)
    anim.param_array('animations', [])
    anim.param_array('bindings', [])
    anim.param_array('attachments', [])
    anim.param_array('skins', [])

    # Vanilla's resource container name is EMPTY (the namedVariant is what is
    # called "Resource Data").
    res.param('name', '')
    res.param_array('resourceHandles', [])
    res.param_array('children', [])

    top.param_structs('namedVariants', [
        [('name', 'Merged Animation Container'),
         ('className', 'hkaAnimationContainer'), ('variant', anim.ref)],
        [('name', 'Resource Data'),
         ('className', 'hkMemoryResourceContainer'), ('variant', res.ref)],
    ])
    return pf.render(top)


def _project_xml(character_file: str) -> str:
    """The file BGED points at: just a pointer to the character file."""
    pf = HkxPackfile(first_id=9)
    sd = pf.add('hkbProjectStringData')
    pd = pf.add('hkbProjectData')
    top = pf.add('hkRootLevelContainer')

    sd.param_array('animationFilenames', [])
    sd.param_array('behaviorFilenames', [])
    sd.param_strings('characterFilenames', [character_file])
    sd.param_array('eventNames', [])
    sd.param('animationPath', '')
    sd.param('behaviorPath', '')
    sd.param('characterPath', '')
    sd.param('fullPathToSource', '')

    pd.param('worldUpWS', '(0.000000 0.000000 1.000000 0.000000)')
    pd.param('stringData', sd.ref)
    pd.param('defaultEventMode', 'EVENT_MODE_IGNORE_FROM_GENERATOR')

    top.param_structs('namedVariants', [
        [('name', 'hkbProjectData'), ('className', 'hkbProjectData'),
         ('variant', pd.ref)]])
    return pf.render(top)


def _character_xml(name: str, behavior_file: str, skeleton_file: str) -> str:
    pf = HkxPackfile(first_id=27)
    mirror = pf.add('hkbMirroredSkeletonInfo')
    strings = pf.add('hkbCharacterStringData')
    values = pf.add('hkbVariableValueSet')
    cdata = pf.add('hkbCharacterData')
    top = pf.add('hkRootLevelContainer')

    mirror.param('mirrorAxis', '(1.000000 0.000000 0.000000 0.000000)')
    # Vanilla single-bone objects ship an EMPTY bonePairMap — there is no pair
    # to mirror on a 1-bone rig.
    mirror.param_array('bonePairMap', [])

    strings.param_array('deformableSkinNames', [])
    strings.param_array('rigidSkinNames', [])
    strings.param_array('animationNames', [])
    strings.param_array('animationFilenames', [])
    strings.param_array('characterPropertyNames', [])
    strings.param_array('retargetingSkeletonMapperFilenames', [])
    strings.param_array('lodNames', [])
    strings.param_array('mirroredSyncPointSubstringsA', [])
    strings.param_array('mirroredSyncPointSubstringsB', [])
    strings.param('name', name)
    strings.param('rigName', skeleton_file)
    strings.param('ragdollName', '')
    strings.param('behaviorFilename', behavior_file)

    values.param_array('wordVariableValues', [])
    values.param_array('quadVariableValues', [])
    values.param_array('variantVariableValues', [])

    # Field set/ORDER/values are those of vanilla `clutter\beehive\characters\
    # Character00.hkx` — the single-bone animated-object character.  This class
    # has NO `variableInitialValues` and NO `aiControlDriverInfo`: the value set
    # hangs off `characterPropertyValues`, the IK infos are NULL POINTERS (not
    # arrays), stringData/mirroredSkeletonInfo come AFTER them, and `scale` is
    # mandatory.  Getting this wrong made hkxcmd silently drop the
    # hkbVariableValueSet (visible as a missing class in the packfile's
    # __classnames__ table), and the object rendered nothing in-game.
    cdata.param_structs('characterControllerInfo', [
        [('capsuleHeight', '1.700000'), ('capsuleRadius', '0.400000'),
         ('collisionFilterInfo', 1), ('characterControllerCinfo', 'null')]])
    cdata.param('modelUpMS', '(0.000000 0.000000 1.000000 0.000000)')
    cdata.param('modelForwardMS', '(1.000000 0.000000 0.000000 0.000000)')
    cdata.param('modelRightMS', '(-0.000000 -1.000000 -0.000000 0.000000)')
    cdata.param_array('characterPropertyInfos', [])
    cdata.param_array('numBonesPerLod', [])
    cdata.param('characterPropertyValues', values.ref)
    cdata.param('footIkDriverInfo', 'null')
    cdata.param('handIkDriverInfo', 'null')
    cdata.param('stringData', strings.ref)
    cdata.param('mirroredSkeletonInfo', mirror.ref)
    cdata.param('scale', '1.000000')

    top.param_structs('namedVariants', [
        [('name', 'hkbCharacterData'), ('className', 'hkbCharacterData'),
         ('variant', cdata.ref)]])
    return pf.render(top)


def _transition_effect(pf):
    """The one blend every transition row shares.

    Field set, order and values are vanilla's "BlendingTransitionEffectGB";
    `flags` is the integer 0, not a FLAG_* name.
    """
    fx = pf.add('hkbBlendingTransitionEffect')
    fx.param('variableBindingSet', 'null')
    fx.param('userData', 0)
    fx.param('name', 'BlendingTransitionEffectGB')
    fx.param('selfTransitionMode',
             'SELF_TRANSITION_MODE_CONTINUE_IF_CYCLIC_BLEND_IF_ACYCLIC')
    fx.param('eventMode', 'EVENT_MODE_DEFAULT')
    fx.param('duration', f'{_BLEND_DURATION:.6f}')
    fx.param('toGeneratorStartTimeFraction', '0.000000')
    fx.param('flags', 0)
    fx.param('endMode', 'END_MODE_NONE')
    fx.param('blendCurve', 'BLEND_CURVE_SMOOTH')
    return fx


def _transition_array(pf, fx, rows):
    """Ref of one state's transition array for `rows` of (event id, state id).

    'null' for no rows, which makes the state a dead end.  The intervals are
    nested structs, so the body is rendered by hand.
    See: docs/commentary/asset_convert_animation.md#transitions-live-on-the-state
    """
    if not rows:
        return 'null'
    arr = pf.add('hkbStateMachineTransitionInfoArray')
    arr.param_raw('transitions', '\n'.join(
        '<hkobject>\n'
        f'\t<hkparam name="triggerInterval">\n{_INTERVAL}\n\t</hkparam>\n'
        f'\t<hkparam name="initiateInterval">\n{_INTERVAL}\n\t</hkparam>\n'
        f'\t<hkparam name="transition">{fx.ref}</hkparam>\n'
        '\t<hkparam name="condition">null</hkparam>\n'
        f'\t<hkparam name="eventId">{ev}</hkparam>\n'
        f'\t<hkparam name="toStateId">{to}</hkparam>\n'
        '\t<hkparam name="fromNestedStateId">0</hkparam>\n'
        '\t<hkparam name="toNestedStateId">0</hkparam>\n'
        '\t<hkparam name="priority">0</hkparam>\n'
        f'\t<hkparam name="flags">{_TRANSITION_FLAGS}</hkparam>\n'
        '</hkobject>'
        for ev, to in rows), numelements=len(rows))
    return arr.ref


def _sequence_rows(sequences, eid, state):
    """Rows out of sequence state `state`: every other sequence, then itself if lone.

    Lone means no other sequence at all, or a one-shot whose only neighbours
    are load sequences: its self-row is what lets a finished one-shot replay.
    See: docs/commentary/asset_convert_animation.md#transitions-live-on-the-state
    """
    others = [j for j in range(len(sequences)) if j != state]
    rows = [(eid[sequences[j]], j) for j in others]
    lone_shot = (sequences[state] not in LOAD_SEQUENCES
                 and all(sequences[j] in LOAD_SEQUENCES for j in others))
    if lone_shot or not rows:
        rows.append((eid[sequences[state]], state))
    return rows


def _playing_modifier(pf):
    """The graph's one BSIsActiveModifier: PLAYING_VARIABLE is 1 while a wrapped state is active.

    One binding, `bIsActive0` to variable 0, no inversion; no modifier list.
    See: docs/commentary/asset_convert_animation.md#playing-variable
    """
    bind = pf.add('hkbVariableBindingSet')
    bind.param_raw('bindings',
                   BINDING_TMPL.format(member=_ACTIVE_MEMBER, var_index=0),
                   numelements=1)
    bind.param('indexOfBindingToEnable', -1)
    active = pf.add('BSIsActiveModifier')
    active.param('variableBindingSet', bind.ref)
    active.param('userData', 2)
    active.param('name', 'IsActiveModifier')
    active.param('enable', True)
    for i in range(5):
        active.param(f'bIsActive{i}', False)
        active.param(f'bInvertActive{i}', False)
    return active


def _wrapped(pf, gen, state_id, modifier):
    """`gen` under an hkbModifierGenerator naming the shared `modifier`.

    The wrapper is named after its state's id, so the name says which state
    is wrapped whatever else in the graph is.
    """
    wrapper = pf.add('hkbModifierGenerator')
    wrapper.param('variableBindingSet', 'null')
    wrapper.param('userData', 1)
    wrapper.param('name', f'{_WRAPPER_PREFIX}{state_id:02d}')
    wrapper.param('modifier', modifier.ref)
    wrapper.param('generator', gen.ref)
    return wrapper


def _state(pf, fx, name, state_id, generator, sequence, rows, modifier=None):
    """One state whose generator plays NIF sequence `sequence`.

    The generator takes exactly pSequence, eBlendModeFunction and fPercent;
    an empty `sequence` plays nothing.  With `modifier` the generator sits
    under a modifier generator, so the state counts as playing.
    See: docs/commentary/asset_convert_animation.md#animated-object-behaviour-graphs
    """
    gen = pf.add('BGSGamebryoSequenceGenerator')
    gen.param('variableBindingSet', 'null')
    gen.param('userData', 0)
    gen.param('name', generator)
    gen.param('pSequence', sequence)
    gen.param('eBlendModeFunction', 'BMF_NONE')
    gen.param('fPercent', '1.000000')
    if modifier is not None:
        gen = _wrapped(pf, gen, state_id, modifier)

    st = pf.add('hkbStateMachineStateInfo')
    st.param('variableBindingSet', 'null')
    st.param_array('listeners', [])
    st.param('enterNotifyEvents', 'null')
    st.param('exitNotifyEvents', 'null')
    st.param('transitions', _transition_array(pf, fx, rows))
    st.param('generator', gen.ref)
    st.param('name', name)
    st.param('stateId', state_id)
    st.param('probability', '1.000000')
    st.param('enable', 'true')
    return st


def _states(pf, fx, sequences, holds, eid, loops):
    """Every state in array order: one per sequence, Rest, then one per hold.

    Rest plays nothing and reaches every sequence.  A held sequence gains an
    End row to its hold; the hold carries that state's rows minus End.  Every
    sequence owns one hold id slot, held or not.  A sequence state is wrapped
    as playing iff it is held or loops; Rest and the holds never are.
    See: docs/commentary/asset_convert_animation.md#end-hold-states
    """
    rest_id = len(sequences)
    held = [i for i, seq in enumerate(sequences) if seq in holds]
    hold_id = {i: rest_id + 1 + i for i in held}
    playing = playing_states(sequences, holds, loops)
    modifier = _playing_modifier(pf) if playing else None
    states = []
    for i, seq in enumerate(sequences):
        rows = _sequence_rows(sequences, eid, i)
        if i in hold_id:
            rows = rows + [(eid[HOLD_EVENT], hold_id[i])]
        states.append(_state(pf, fx, seq, i,
                             f'GamebryoSequenceGenerator{i:02d}', seq, rows,
                             modifier if i in playing else None))
    states.append(_state(pf, fx, 'Rest', rest_id,
                         'GamebryoSequenceGeneratorRest', '',
                         [(eid[seq], j) for j, seq in enumerate(sequences)]))
    for i in held:
        hold = holds[sequences[i]]
        states.append(_state(pf, fx, hold, hold_id[i],
                             f'GamebryoSequenceGeneratorHold{i:02d}', hold,
                             _sequence_rows(sequences, eid, i)))
    return states


def _state_machine(pf, graph_name, states, start_id):
    """The machine over `states`, starting on `start_id`, with no wildcards.

    `eventToSendWhenStateOrTransitionChanges` is an inline struct, not a
    pointer; looping is the sequence's own cycle type, never the machine's.
    See: docs/commentary/asset_convert_animation.md#transitions-live-on-the-state
    """
    sm = pf.add('hkbStateMachine')
    sm.param('variableBindingSet', 'null')
    sm.param('userData', 0)
    sm.param('name', f'{graph_name}SM')
    sm.param_raw('eventToSendWhenStateOrTransitionChanges', (
        '<hkobject>\n\t<hkparam name="id">-1</hkparam>\n'
        '\t<hkparam name="payload">null</hkparam>\n</hkobject>'))
    sm.param('startStateChooser', 'null')
    sm.param('startStateId', start_id)
    sm.param('returnToPreviousStateEventId', -1)
    sm.param('randomTransitionEventId', -1)
    sm.param('transitionToNextHigherStateEventId', -1)
    sm.param('transitionToNextLowerStateEventId', -1)
    sm.param('syncVariableIndex', -1)
    sm.param('wrapAroundStateId', 'false')
    sm.param('maxSimultaneousTransitions', 32)
    sm.param('startStateMode', 'START_STATE_MODE_DEFAULT')
    sm.param('selfTransitionMode', 'SELF_TRANSITION_MODE_NO_TRANSITION')
    sm.params.append(('states', ' '.join(s.ref for s in states),
                      f'array:{len(states)}'))
    sm.param('wildcardTransitions', 'null')
    return sm


def _graph_data(pf, events):
    """The graph's event table and its one variable, BOOL PLAYING_VARIABLE at 0.

    The word min/max arrays must sit between eventInfos and
    variableInitialValues, or hkxcmd fails the compile silently.
    """
    strings = pf.add('hkbBehaviorGraphStringData')
    strings.param_strings('eventNames', events)
    strings.param_array('attributeNames', [])
    strings.param_strings('variableNames', [PLAYING_VARIABLE])
    strings.param_array('characterPropertyNames', [])

    values = pf.add('hkbVariableValueSet')
    values.param_structs('wordVariableValues', [[('value', 0)]])
    values.param_array('quadVariableValues', [])
    values.param_array('variantVariableValues', [])

    gdata = pf.add('hkbBehaviorGraphData')
    gdata.param_array('attributeDefaults', [])
    gdata.param_raw('variableInfos', VARIABLE_INFO_TMPL.format(vtype='BOOL'),
                    numelements=1)
    gdata.param_structs('characterPropertyInfos', [])
    gdata.param_structs('eventInfos', [[('flags', 0)] for _ in events])
    gdata.param_array('wordMinVariableValues', [])
    gdata.param_array('wordMaxVariableValues', [])
    gdata.param('variableInitialValues', values.ref)
    gdata.param('stringData', strings.ref)
    return gdata


def playing_states(sequences, holds, loops) -> set:
    """Ids of the sequence states that read as playing: held, or a LOOP."""
    return {i for i, seq in enumerate(sequences)
            if seq in (holds or {}) or seq in loops}


def behavior_xml(graph_name: str, sequences: list, holds: dict = None,
                 loops=()) -> str:
    """State machine with one BGSGamebryoSequenceGenerator per NIF sequence.

    `sequences` are the NiControllerSequence names from the converted NIF.
    Each becomes a same-named event, so PlayAnimation("<seq>") selects it;
    SOUND_EVENT follows them so the NIF's sound keys reach the engine.
    `holds` maps a sequence to its one-frame hold sequence: HOLD_EVENT is then
    declared last and moves the finished sequence onto the hold's state.
    `loops` names the LOOP sequences.
    See: docs/commentary/asset_convert_animation.md#end-hold-states
    """
    holds = {seq: hold for seq, hold in (holds or {}).items()
             if seq in sequences}
    events = list(sequences) + [SOUND_EVENT] + ([HOLD_EVENT] if holds else [])
    eid = {n: i for i, n in enumerate(events)}

    pf = HkxPackfile(first_id=100)
    fx = _transition_effect(pf)
    states = _states(pf, fx, sequences, holds, eid,
                     set(loops) & set(sequences))
    sm = _state_machine(pf, graph_name, states,
                        _start_state_id(sequences, len(sequences)))
    gdata = _graph_data(pf, events)

    graph = pf.add('hkbBehaviorGraph')
    graph.param('variableBindingSet', 'null')
    graph.param('userData', 0)
    graph.param('name', graph_name)
    graph.param('variableMode', 'VARIABLE_MODE_DISCARD_WHEN_INACTIVE')
    graph.param('rootGenerator', sm.ref)
    graph.param('data', gdata.ref)

    top = pf.add('hkRootLevelContainer')
    top.param_structs('namedVariants', [
        [('name', graph_name), ('className', 'hkbBehaviorGraph'),
         ('variant', graph.ref)]])
    return pf.render(top)


def graph_generators(sequences: list, holds: dict = None) -> dict:
    """{generator name: NIF sequence it plays} for the graph `behavior_xml` emits.

    Rest maps to the empty name.  This is what a compiled graph must contain.
    """
    held = {seq: hold for seq, hold in (holds or {}).items()
            if seq in sequences}
    out = {f'GamebryoSequenceGenerator{i:02d}': seq
           for i, seq in enumerate(sequences)}
    out['GamebryoSequenceGeneratorRest'] = ''
    out.update({f'GamebryoSequenceGeneratorHold{sequences.index(seq):02d}': hold
                for seq, hold in held.items()})
    return out


def compiled_generators(hkx_path) -> dict:
    """{generator name: the sequence it plays} read from a compiled graph.

    The string pool stores a generator's pSequence right after its name; an
    empty pSequence leaves nothing before the next generator, and reads ''.
    """
    with open(hkx_path, 'rb') as f:
        pool = [p.decode('latin-1') for p in f.read().split(b'\x00')]
    found = {}
    for i, name in enumerate(pool):
        if not name.startswith(_GENERATOR_PREFIX):
            continue
        after = [p for p in pool[i + 1:i + 1 + _PSEQUENCE_WINDOW] if p]
        plays = after[0] if after else ''
        found[name] = '' if plays.startswith(_GENERATOR_PREFIX) else plays
    return found


def graph_node_names(sequences: list, holds: dict, loops) -> list:
    """Generator and wrapper names of the graph `behavior_xml` emits, in file order.

    A wrapped state's wrapper comes right before the generator it wraps; then
    Rest; then the hold generators.
    """
    held = {seq: hold for seq, hold in (holds or {}).items()
            if seq in sequences}
    playing = playing_states(sequences, held, set(loops) & set(sequences))
    names = []
    for i, seq in enumerate(sequences):
        names += [f'{_WRAPPER_PREFIX}{i:02d}'] * (i in playing)
        names.append(f'{_GENERATOR_PREFIX}{i:02d}')
    names.append(f'{_GENERATOR_PREFIX}Rest')
    return names + [f'{_GENERATOR_PREFIX}Hold{i:02d}'
                    for i, seq in enumerate(sequences) if seq in held]


def _verify_compiled_graph(hkx_path, graph_name, sequences, holds, loops):
    """Raise unless the compiled graph has the nodes it should, in order.

    hkxcmd exits 0 on a dangling reference and writes a smaller graph, so a
    successful compile is not evidence of structure.  Checked, all as STRINGS
    of the pool minus the graph's own name: what each generator plays, the
    ordered generator and wrapper names, the variable, one `bIsActive0` iff a
    state is wrapped, the End event.  Rows and object fields are not read.
    See: docs/commentary/asset_convert_animation.md#graph-and-nif-move-together
    """
    want = graph_generators(sequences, holds)
    got = compiled_generators(hkx_path)
    wrong = sorted(f'{gen} plays {got.get(gen)!r}, expected {seq!r}'
                   for gen, seq in want.items() if got.get(gen) != seq)
    with open(hkx_path, 'rb') as f:
        pool = [p.decode('latin-1') for p in f.read().split(b'\x00')]
    for own in (graph_name, graph_name, f'{graph_name}SM'):
        if own in pool:
            pool.remove(own)
    nodes = [p for p in pool if p.startswith((_GENERATOR_PREFIX, _WRAPPER_PREFIX))]
    expected = graph_node_names(sequences, holds, loops)
    if nodes != expected:
        wrong.append(f'nodes are {nodes}, expected {expected}')
    writers = int(any(n.startswith(_WRAPPER_PREFIX) for n in expected))
    if pool.count(_ACTIVE_MEMBER) != writers:
        wrong.append(f'{pool.count(_ACTIVE_MEMBER)} {_ACTIVE_MEMBER} binding(s), '
                     f'expected {writers}')
    events = [PLAYING_VARIABLE] + [HOLD_EVENT] * (len(want) > len(sequences) + 1)
    wrong += [f'{name} not declared' for name in events if name not in pool]
    if wrong:
        raise RuntimeError(f'compiled graph {hkx_path} is wrong: {wrong}')


def _compile_tree(proj_dir, stem, sequences, holds, loops):
    """Compile the four hkx files of one project into `proj_dir`.

    The skeleton bone is always DUMMY_BONE; its pose is repaired while the
    file is still WIN32, and the AMD64 step is last for every file.
    See: docs/commentary/asset_convert_animation.md#animated-object-behaviour-graphs
    """
    char_rel = os.path.join('Characters', 'Character01.hkx')
    behv_rel = os.path.join('Behaviors', 'Behavior00.hkx')
    skel_rel = os.path.join('CharacterAssets', 'Skeleton.hkx')
    targets = [
        (os.path.join(proj_dir, skel_rel), skeleton_xml(DUMMY_BONE), True),
        (os.path.join(proj_dir, behv_rel),
         behavior_xml(stem, sequences, holds, loops), False),
        (os.path.join(proj_dir, char_rel),
         _character_xml(stem, behv_rel, skel_rel), False),
        (os.path.join(proj_dir, stem + '.hkx'), _project_xml(char_rel), False),
    ]
    for path, xml, is_skeleton in targets:
        xml_path = path + '.xml'
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(xml_path, 'w', encoding='ascii', errors='replace',
                  newline='\n') as f:
            f.write(xml)
        compile_hkx(xml_path, path)
        os.remove(xml_path)
        if is_skeleton:
            fix_identity_quat(path)
        convert_hkx_to_amd64(path)
    _verify_compiled_graph(os.path.join(proj_dir, behv_rel), stem, sequences,
                           holds, loops)


def stage_animobject_project(out_root: str, model_rel: str, sequences: list,
                             holds: dict = None, *, loops) -> StagedProject:
    """Compile one animated object's hkx tree into a staging folder.

    out_root is the output meshes root and model_rel the NIF path under it.
    Nothing reaches the final `<stem>_behavior` folder until
    `commit_animobject_project`.  Ambient-only meshes name vanilla's shared
    graph and stage nothing; no sequences means no graph (empty BGED).
    `loops`, the LOOP sequences, has no default: forgetting it would ship
    looping states that never read as playing.
    See: docs/commentary/asset_convert_animation.md#graph-and-nif-move-together
    """
    if isinstance(loops, str):
        raise TypeError('loops is a collection of sequence names, not one name')
    if not sequences:
        return StagedProject('', None, None)
    if all(seq in (_AUTOPLAY_SEQUENCE, _AUTOLOOP_SEQUENCE) for seq in sequences):
        return StagedProject(VANILLA_AUTOPLAY_BGED, None, None)

    rel_dir, stem, final_dir = _project_dir(out_root, model_rel)
    stage_dir = final_dir[:-len(_TREE_SUFFIX)] + _STAGE_SUFFIX
    shutil.rmtree(stage_dir, ignore_errors=True)
    if os.path.lexists(stage_dir):
        raise RuntimeError(f'staging folder cannot be cleared: {stage_dir}')
    try:
        _compile_tree(stage_dir, stem, sequences, holds, loops)
    except BaseException:
        shutil.rmtree(stage_dir, ignore_errors=True)
        raise
    bged = '/'.join([rel_dir, stem + _TREE_SUFFIX, stem + '.hkx'])
    return StagedProject(bged.replace('/', '\\'), stage_dir, final_dir)


def _project_dir(out_root, model_rel):
    """(folder of the model under out_root as posix, model stem, final tree folder)."""
    rel_dir = os.path.dirname(model_rel).replace('\\', '/')
    stem = os.path.splitext(os.path.basename(model_rel))[0]
    return rel_dir, stem, os.path.join(out_root, *rel_dir.split('/'),
                                       stem + _TREE_SUFFIX)


def stale_animobject_project(out_root: str, model_rel: str) -> StagedProject:
    """What a failed build commits: no BGED, and the old tree is removed."""
    return StagedProject('', None, _project_dir(out_root, model_rel)[2])


def _aside_dir(staged):
    """Where the tree a staged project replaces waits while the NIF is written."""
    return staged.final_dir[:-len(_TREE_SUFFIX)] + _ASIDE_SUFFIX


def set_aside_animobject_project(staged: StagedProject) -> None:
    """Move the tree a STAGED project replaces out of the way, before its NIF.

    The old graph may name holds the new NIF lacks, so it must not sit beside
    that NIF for a moment.  Raises OSError with nothing changed; a project
    with nothing staged leaves its tree alone.
    See: docs/commentary/asset_convert_animation.md#graph-and-nif-move-together
    """
    if staged is None or staged.stage_dir is None:
        return
    aside = _aside_dir(staged)
    shutil.rmtree(aside, ignore_errors=True)
    if os.path.lexists(aside):
        raise OSError(f'old set-aside tree cannot be cleared: {aside}')
    if os.path.isdir(staged.final_dir):
        os.rename(staged.final_dir, aside)


def restore_animobject_project(staged: StagedProject) -> None:
    """Put a set-aside tree back; ONLY for a NIF replace that raised OSError."""
    if staged is None or staged.stage_dir is None:
        return
    aside = _aside_dir(staged)
    if os.path.isdir(aside) and not os.path.lexists(staged.final_dir):
        os.rename(aside, staged.final_dir)


def _install_staged_tree(staged):
    """Move the staged tree to its final folder; copy it if the move keeps failing.

    Raises OSError with no final folder left when even the copy fails: a
    partial tree would be worse than none.
    """
    for attempt in range(_SWAP_TRIES):
        try:
            os.rename(staged.stage_dir, staged.final_dir)
            return
        except OSError:
            time.sleep(_SWAP_PAUSE * attempt)
    try:
        shutil.copytree(staged.stage_dir, staged.final_dir)
    except OSError:
        shutil.rmtree(staged.final_dir, ignore_errors=True)
        raise


def commit_animobject_project(staged: StagedProject) -> None:
    """Install a staged tree as a whole, once its NIF is in place.

    The tree it replaces was set aside before the NIF and is deleted here.
    A project with nothing staged removes the tree it names, if it names one.
    Raises OSError, leaving NO tree, when the staged one cannot be installed.
    """
    if staged is None or staged.final_dir is None:
        return
    if staged.stage_dir is None:
        shutil.rmtree(staged.final_dir, ignore_errors=True)
        return
    if os.path.lexists(staged.final_dir):
        set_aside_animobject_project(staged)
    try:
        _install_staged_tree(staged)
    finally:
        shutil.rmtree(staged.stage_dir, ignore_errors=True)
        shutil.rmtree(_aside_dir(staged), ignore_errors=True)


def discard_animobject_project(staged: StagedProject) -> None:
    """Drop a staged tree that will not ship; the final folder is untouched."""
    if staged is not None and staged.stage_dir is not None:
        shutil.rmtree(staged.stage_dir, ignore_errors=True)


def generate_animobject_project(out_root: str, model_rel: str, sequences: list,
                                holds: dict = None, *, loops) -> str:
    """Write the 4-file hkx tree for one animated object; return its BGED.

    The BGED is the project hkx path relative to meshes\\, backslashed, or ''
    when there is nothing to animate.  A caller that also writes the NIF
    stages and commits around that write instead.
    See: docs/commentary/asset_convert_animation.md#graph-and-nif-move-together
    """
    staged = stage_animobject_project(out_root, model_rel, sequences, holds,
                                      loops=loops)
    commit_animobject_project(staged)
    return staged.bged
