"""Pose-hold states: a finished transition must HOLD its end pose across loads.

Skyrim saves a reference's graph state and re-enters it on load with the
generator restarted at t=0, so a graph whose only states are transitions
replays the last motion on every load.  These tests pin the three halves of
the fix: the graph (`End` -> hold state), the NIF (a one-frame `<Seq>Hold`
sequence) and the rule that the two never disagree on disk.
See: docs/commentary/asset_convert_animation.md#end-hold-states
"""

import collections
import hashlib
import io
import os
import re
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

import convert

if not hasattr(time, 'clock'):
    time.clock = time.perf_counter

from asset_convert.havok import hkx_animobject, hkx_xml
from asset_convert.havok.hkx_animobject import (HOLD_EVENT, SOUND_EVENT,
                                                behavior_xml,
                                                generate_animobject_project,
                                                graph_generators,
                                                stage_animobject_project)
from asset_convert.nif import nif_batch, nif_converter, pose_hold, sse_nif
from asset_convert.sources import bsa_pack
from asset_convert.nif.nif_passes import (add_animobject_bged,
                                          collect_sequence_names)
from pyffi.formats.nif import NifFormat
from tests.conftest import NIF_STUB, require_case_twins
from tools.validate import gamebryo_seq_check

EXPORT_MESHES = Path('export/Oblivion.esm/meshes')

#: Forward/Backward wall; Forward carries a `sound:` key and keyed bools.
_WALL = 'dungeons/chargen/prisonsecretwall01.nif'

#: One Forward sequence whose 21 keyed NiVisController blocks end hidden.
_TRIPWIRE = 'dungeons/caves/triggers/ctrigtripwire01.nif'

#: Forward drives a NiPathInterpolator, which no constant can stand in for.
_PATH_DRIVEN = 'effects/se11clonefx.nif'

#: One sequence, a LOOP `Forward`: no hold, and it never stops playing.
_LOOPING = 'architecture/anvil/lorgenhand01.nif'

#: `SpecialIdle` LOOP start state beside one held `Forward`.
_LOOP_AND_SHOT = 'oblivion/sigil/sigilfireboom01.nif'

#: CLAMP `SpecialIdle` start state with no hold, a held `Equip`, a LOOP `Forward`.
_BARE_START = 'effects/seflamesofagnonmani.nif'

#: Forward is CLAMP with an `end` key, but plays at frequency 0.
_FROZEN = 'oblivion/architecture/citadel/interior/switch/scampswitch01.nif'

#: Which states of the wall's graph are wrapped as playing when both sequences are held.
_BOTH_WRAPPED = {'Forward': 'ModifierGenerator00', 'Backward': 'ModifierGenerator01',
                 'Rest': None, 'ForwardHold': None, 'BackwardHold': None}

#: sha256 of the pre-hold XML for graph 'wall': wildcard array deleted, its ref nulled, later ids shifted down.
_NO_HOLD_XML = {
    ('Forward',):
        '176faf1a768957dcac78b59c539ae1bc4776a3f5f9cb4d7d5a04ed833099e59c',
    ('Forward', 'Backward'):
        '3061cbd33cea505b1cc56c27e80fce133f57cefd263346eca7beba7c93537b15',
    ('Forward', 'Backward', 'Unequip'):
        '0062d4e90f9cd70c14ec5f7d298631cb3b08403cdd7d3dfe8342152511294f3f',
    ('SpecialIdle',):
        '343f8ba1927b086f8b50b1f1c3fa0eb27f2f1e1cca02fb2bbb220fefd8a4c84e',
    ('SpecialIdle', 'Forward', 'Backward'):
        '7f0ec1e22ea0fd0dabe1f8c1958ad0cba15586b83afed143e9a5d6632d7d6e24',
    ('AutoLoop', 'Forward', 'Backward'):
        '2b3c6647f3455d90e3661c7431ebc0e101dd9789436273d04ed00a3f305a2c6c',
}

_BOTH = {'Forward': 'ForwardHold', 'Backward': 'BackwardHold'}

#: sha256 of the lever's `behavior00.hkx` tested in game: a second press mid-swing was ignored, no error line.
_LEVER_GRAPH = 'dae50412dbc6d9ccb632fede5e01a413cf72efe1863df7e9f7c76b1aecf254e9'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _param(obj, name):
    """Text of the `name` hkparam of a packfile object ('' when empty)."""
    return next((p.text or '').strip() for p in obj.findall('hkparam')
                if p.get('name') == name)


def _objects(xml):
    """{ref: element} of every top-level object of a behaviour XML."""
    return {o.get('name'): o for o in ET.fromstring(xml).iter('hkobject')
            if o.get('class')}


def _unwrap(objs, ref):
    """(wrapper name or None, Gamebryo generator element) behind a state's generator ref.

    A wrapper over a wrapper is refused: one state, at most one wrapper.
    """
    node = objs[ref]
    if node.get('class') != 'hkbModifierGenerator':
        return None, node
    inner = objs[_param(node, 'generator')]
    assert inner.get('class') == 'BGSGamebryoSequenceGenerator', inner.get('class')
    return _param(node, 'name'), inner


def _decode(xml):
    """A behaviour XML as {'events', 'start', 'wildcard', 'states', 'wrappers'}.

    `states` maps a state name to its id, Gamebryo generator name, sequence and
    rows, each row an (event name, target state name) pair.  `wrappers` maps
    every state name to the modifier generator over it, or None when bare.
    """
    objs = _objects(xml)
    by_class = {o.get('class'): o for o in objs.values()}
    strings = by_class['hkbBehaviorGraphStringData']
    names = next(p for p in strings.findall('hkparam')
                 if p.get('name') == 'eventNames')
    events = [e.text for e in names.findall('hkcstring')]
    machine = by_class['hkbStateMachine']
    raw, wrappers = {}, {}
    for ref in _param(machine, 'states').split():
        wrappers[_param(objs[ref], 'name')], gen = _unwrap(
            objs, _param(objs[ref], 'generator'))
        rows_ref = _param(objs[ref], 'transitions')
        rows = [] if rows_ref == 'null' else [
            (int(_param(row, 'eventId')), int(_param(row, 'toStateId')))
            for row in objs[rows_ref].iter('hkobject') if row.get('class') is None
            and row.findall("hkparam[@name='eventId']")]
        raw[_param(objs[ref], 'name')] = (
            int(_param(objs[ref], 'stateId')), _param(gen, 'name'),
            _param(gen, 'pSequence'), rows)
    by_id = {state[0]: name for name, state in raw.items()}
    states = {name: (sid, gen, seq, [(events[e], by_id[t]) for e, t in rows])
              for name, (sid, gen, seq, rows) in raw.items()}
    return {'events': events, 'start': int(_param(machine, 'startStateId')),
            'wildcard': _param(machine, 'wildcardTransitions'),
            'states': states, 'wrappers': wrappers}


def _fields(obj):
    """{param name: text} of a packfile object or of one inline struct."""
    return {p.get('name'): (p.text or '').strip() for p in obj.findall('hkparam')}


def _structs(obj, name):
    """The inline structs of array param `name`, each as its fields."""
    param = next(p for p in obj.findall('hkparam') if p.get('name') == name)
    return [_fields(o) for o in param.findall('hkobject')]


def _playing(xml):
    """Everything that writes or declares bAnimPlaying in a behaviour XML.

    Keys: `variables` (names), `types`, `words` (initial values), `modifiers`
    and `bindings` (field dicts, one per object), `bound` (classes carrying a
    binding set) and `wrappers` ({name: fields}); refs are left as written.
    """
    objs = _objects(xml)
    of = lambda klass: [o for o in objs.values() if o.get('class') == klass]
    strings, = of('hkbBehaviorGraphStringData')
    data, = of('hkbBehaviorGraphData')
    values = next(o for o in of('hkbVariableValueSet')
                  if o.get('name') == _param(data, 'variableInitialValues'))
    names = next(p for p in strings.findall('hkparam')
                 if p.get('name') == 'variableNames')
    return {
        'variables': [e.text for e in names.findall('hkcstring')],
        'types': [_fields(o).get('type') for o in next(
            p for p in data.findall('hkparam')
            if p.get('name') == 'variableInfos').findall('hkobject')],
        'words': [w['value'] for w in _structs(values, 'wordVariableValues')],
        'modifiers': [dict(_fields(o), ref=o.get('name'))
                      for o in of('BSIsActiveModifier')],
        'bindings': [dict(_fields(o), ref=o.get('name'),
                          rows=_structs(o, 'bindings'))
                     for o in of('hkbVariableBindingSet')],
        'bound': sorted(o.get('class') for o in objs.values()
                        if _fields(o).get('variableBindingSet', 'null') != 'null'),
        'wrappers': {_param(o, 'name'): _fields(o)
                     for o in of('hkbModifierGenerator')},
        'classes': sorted({o.get('class') for o in objs.values()}),
    }


def _chunks(xml):
    """(text before the objects, [object texts], text after) of a behaviour XML."""
    head, _, rest = xml.partition('\t<hkobject name=')
    body, _, tail = rest.rpartition('\n\n\t</hksection>')
    parts = ('\t<hkobject name=' + body).split('\n\n\t<hkobject name=')
    return head, [parts[0]] + ['\t<hkobject name=' + c for c in parts[1:]], \
        '\n\n\t</hksection>' + tail


def _chunk(chunks, klass, name):
    """Index of the object of class `klass` whose `name` param is `name`."""
    found = [i for i, c in enumerate(chunks)
             if f'class="{klass}"' in c.split('\n')[0]
             and f'<hkparam name="name">{name}</hkparam>' in c]
    assert len(found) == 1, (klass, name, len(found))
    return found[0]


def _ref(chunks, klass, name):
    """Packfile ref (`#0123`) of the object of class `klass` named `name`."""
    return re.match(r'\t<hkobject name="(#\d+)"',
                    chunks[_chunk(chunks, klass, name)]).group(1)


def _point(chunks, klass, name, param, target):
    """Make `param` of the named object a reference to `target`."""
    i = _chunk(chunks, klass, name)
    new, count = re.subn(r'(<hkparam name="%s">)#\d+(</hkparam>)' % param,
                         r'\g<1>%s\g<2>' % target, chunks[i])
    assert count == 1, (name, param, count)
    chunks[i] = new


_STATE, _GEN, _WRAP = ('hkbStateMachineStateInfo', 'BGSGamebryoSequenceGenerator',
                       'hkbModifierGenerator')

#: kind -> [(class, object name, param, name of the object to point it at, or a literal ref)].
_REWIRED = {
    'wrapper dropped': [
        (_STATE, 'Forward', 'generator', 'GamebryoSequenceGenerator00')],
    'inner generators swapped': [
        (_WRAP, 'ModifierGenerator00', 'generator', 'GamebryoSequenceGenerator01'),
        (_WRAP, 'ModifierGenerator01', 'generator', 'GamebryoSequenceGenerator00')],
    'wrapper on the hold': [
        (_STATE, 'Forward', 'generator', 'GamebryoSequenceGenerator00'),
        (_WRAP, 'ModifierGenerator00', 'generator', 'GamebryoSequenceGeneratorHold00'),
        (_STATE, 'ForwardHold', 'generator', 'ModifierGenerator00')],
    'missing modifier': [
        (_WRAP, 'ModifierGenerator00', 'modifier', '#9999'),
        (_WRAP, 'ModifierGenerator01', 'modifier', '#9999')],
    'two modifiers': [(_WRAP, 'ModifierGenerator01', 'modifier', '#9002')],
    'Rest wrapped under another name': [
        (_STATE, 'Rest', 'generator', '#9003')],
}


def _rest_wrapper(chunks):
    """A copy of wrapper 00 as #9003, named `ModifierGeneratorRest`, over Rest's generator."""
    i = _chunk(chunks, _WRAP, 'ModifierGenerator00')
    copy = chunks[i].replace(_ref(chunks, _WRAP, 'ModifierGenerator00'), '#9003')
    copy = copy.replace('>ModifierGenerator00<', '>ModifierGeneratorRest<')
    return [re.sub(r'(<hkparam name="generator">)#\d+', r'\g<1>%s' % _ref(
        chunks, _GEN, 'GamebryoSequenceGeneratorRest'), copy)]


def _second_modifier(chunks):
    """Copies of the binding set and the modifier, as #9001 and #9002."""
    bind = next(c for c in chunks if 'class="hkbVariableBindingSet"' in c)
    active = next(c for c in chunks if 'class="BSIsActiveModifier"' in c)
    old_bind = re.match(r'\t<hkobject name="(#\d+)"', bind).group(1)
    old_active = re.match(r'\t<hkobject name="(#\d+)"', active).group(1)
    return [bind.replace(old_bind, '#9001'),
            active.replace(old_active, '#9002').replace(old_bind, '#9001')]


def _wrong_playing_xml(kind):
    """The wall's graph (both sequences held) with its bAnimPlaying wiring broken.

    Each kind compiles and keeps every generator playing the right sequence;
    only who writes the variable, or for which state, is wrong.
    """
    head, chunks, tail = _chunks(behavior_xml('wall', ['Forward', 'Backward'],
                                              _BOTH))
    if kind == 'member typo':
        chunks = [c.replace('>bIsActive0<', '>bIsActve0<') for c in chunks]
    if kind == 'two modifiers':
        chunks += _second_modifier(chunks)
    if kind == 'Rest wrapped under another name':
        chunks += _rest_wrapper(chunks)
    for klass, name, param, target in _REWIRED.get(kind, ()):
        if not target.startswith('#'):
            target = _ref(chunks, _WRAP if target.startswith('Modifier') else _GEN,
                          target)
        _point(chunks, klass, name, param, target)
    return head + '\n\n'.join(chunks) + tail


#: The wrong wirings `_wrong_playing_xml` builds, by what the checks must notice.
_WRONG_PLAYING = ('member typo', 'wrapper dropped', 'inner generators swapped',
                  'wrapper on the hold', 'missing modifier', 'two modifiers',
                  'Rest wrapped under another name')


def _legacy_xml(xml):
    """`xml` as the emitter wrote it before bAnimPlaying existed: no variable."""
    for name in ('variableNames', 'wordVariableValues', 'variableInfos'):
        xml, count = re.subn(
            r'<hkparam name="%s" numelements="1">.*?</hkparam>(?=\n\t\t<hkparam|\n\t</hkobject>)'
            % name, '<hkparam name="%s" numelements="0"></hkparam>' % name, xml,
            flags=re.S)
        assert count == 1, (name, count)
    return xml


def _fill(group, kind, keys):
    """Give a pyffi key group `keys` of (time, value) with interpolation `kind`."""
    group.interpolation = kind
    group.num_keys = len(keys)
    group.keys.update_size()
    for key, (when, value) in zip(group.keys, keys):
        key.arg = kind
        key.time = when
        if isinstance(value, tuple):
            key.value.x, key.value.y, key.value.z = value
        else:
            key.value = value


def _euler_transform():
    """A keyed transform: QUADRATIC Z rotation to 1.5 and a move ending early."""
    interp = NifFormat.NiTransformInterpolator()
    interp.data = NifFormat.NiTransformData()
    interp.scale = 1.0
    interp.data.rotation_type = pose_hold.XYZ_ROTATION_KEY
    interp.data.num_rotation_keys = 1
    _fill(interp.data.xyz_rotations[2], 2, [(0.0, 0.0), (2.0, 1.5)])
    _fill(interp.data.translations, 1, [(0.0, (0.0, 0.0, 0.0)),
                                        (1.5, (1.0, 2.0, 3.0))])
    return interp


def _quat_transform():
    """A keyed transform whose rotation is two quaternion keys."""
    interp = NifFormat.NiTransformInterpolator()
    interp.data = NifFormat.NiTransformData()
    interp.scale = 1.0
    interp.data.rotation_type = pose_hold.KEY_LINEAR
    interp.data.num_rotation_keys = 2
    interp.data.quaternion_keys.update_size()
    for key, (when, w) in zip(interp.data.quaternion_keys,
                              ((0.0, 1.0), (2.0, 0.5))):
        key.arg = pose_hold.KEY_LINEAR
        key.time = when
        key.value.w, key.value.x = w, 0.5
    return interp


def _keyed(interp_class, data_class, kind, keys):
    """A float, point3 or bool interpolator carrying `keys`."""
    interp = getattr(NifFormat, interp_class)()
    interp.data = getattr(NifFormat, data_class)()
    _fill(interp.data.data, kind, keys)
    return interp


def _interpolators():
    """One of each supported form, in a fixed order, plus a constant and a null."""
    constant = NifFormat.NiBoolInterpolator()
    constant.bool_value = True
    return [
        _euler_transform(), _quat_transform(),
        _keyed('NiFloatInterpolator', 'NiFloatData', 2, [(0.0, 0.0), (2.0, 0.75)]),
        _keyed('NiPoint3Interpolator', 'NiPosData', 1,
               [(0.0, (0.0, 0.0, 0.0)), (2.0, (0.25, 0.5, 0.75))]),
        _keyed('NiBoolInterpolator', 'NiBoolData', 5, [(0.0, 1), (2.0, 0)]),
        constant, None]


_SOUNDED = ((0.0, b'start'), (0.0, b'SoundPlay.TES4_Open_SNDR'), (2.0, b'end'))


def _add_sequence(manager, name, interps, *, cycle=2, stop=2.0, frequency=1.0,
                  keys=_SOUNDED):
    """Register a sequence on `manager`; one controlled block per interpolator."""
    seq = NifFormat.NiControllerSequence()
    seq.name = name.encode()
    seq.cycle_type, seq.frequency, seq.weight = cycle, frequency, 1.0
    seq.start_time, seq.stop_time, seq.manager = 0.0, stop, manager
    seq.text_keys = NifFormat.NiTextKeyExtraData()
    seq.text_keys.num_text_keys = len(keys)
    seq.text_keys.text_keys.update_size()
    for key, (when, value) in zip(seq.text_keys.text_keys, keys):
        key.time, key.value = when, value
    seq.num_controlled_blocks = len(interps)
    seq.controlled_blocks.update_size()
    for i, (block, interp) in enumerate(zip(seq.controlled_blocks, interps)):
        block.interpolator = interp
        block.controller = NifFormat.NiTransformController()
        block.node_name = b'Node%d' % i
        block.controller_type = b'NiTransformController'
    manager.num_controller_sequences += 1
    manager.controller_sequences.update_size()
    manager.controller_sequences[manager.num_controller_sequences - 1] = seq
    return seq


def _mesh():
    """(data, manager) of an empty Skyrim-version NIF with a controller manager."""
    data = NifFormat.Data(version=0x14020007, user_version=12, user_version_2=83)
    data.header.endian_type = 1
    root = NifFormat.NiNode()
    root.name = b'Root'
    manager = NifFormat.NiControllerManager()
    root.controller = manager
    manager.target = root
    data.roots = [root]
    return data, manager


def _sequences(data):
    """{name: sequence block} of every sequence in a NIF."""
    return {bytes(b.name).decode('latin-1'): b for root in data.roots
            for b in root.tree() if isinstance(b, NifFormat.NiControllerSequence)}


def _text_keys(seq):
    """A sequence's text keys as [(time, value)]."""
    return [(k.time, bytes(k.value)) for k in seq.text_keys.text_keys]


def _channels(interp):
    """Every keyed channel of an interpolator as [(key type, [(time, value)])]."""
    data = getattr(interp, 'data', None)
    if data is None:
        return []
    if not isinstance(interp, NifFormat.NiTransformInterpolator):
        groups = [data.data]
    elif int(data.rotation_type) == pose_hold.XYZ_ROTATION_KEY:
        groups = list(data.xyz_rotations) + [data.translations, data.scales]
    else:
        groups = [data.translations, data.scales]
    out = [(int(g.interpolation), [(k.time, _plain(k.value)) for k in g.keys])
           for g in groups if g.num_keys]
    if (isinstance(interp, NifFormat.NiTransformInterpolator)
            and int(data.rotation_type) != pose_hold.XYZ_ROTATION_KEY
            and data.num_rotation_keys):
        out.append((int(data.rotation_type),
                    [(k.time, _plain(k.value)) for k in data.quaternion_keys]))
    return out


def _plain(value):
    """A key value as a float or a tuple of its axes."""
    axes = tuple(getattr(value, a) for a in 'wxyz' if hasattr(value, a))
    return axes or float(value)


def _reread(data):
    """`data` after a pyffi write and a fresh read."""
    buf = io.BytesIO()
    data.write(buf)
    fresh = NifFormat.Data()
    fresh.read(io.BytesIO(buf.getvalue()))
    return fresh


def _sample(rel):
    """An exported sample mesh, matched ignoring case; skips when not exported."""
    path = EXPORT_MESHES
    for part in rel.split('/'):
        found = [e for e in (os.listdir(path) if path.is_dir() else ())
                 if e.lower() == part]
        if not found:
            pytest.skip(f'{rel} not exported')
        path = path / found[0]
    return path


def _pool(hkx_path):
    """The NUL-separated strings of a compiled hkx."""
    return [p.decode('latin-1') for p in Path(hkx_path).read_bytes().split(b'\x00')
            if p]


def _tree_bytes(root):
    """{relative path: bytes} of every file under `root`."""
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted(Path(root).rglob('*')) if p.is_file()}


def _no_holds(_data, _seq_names):
    """A hold plan that plans nothing, standing in for `plan_pose_holds`."""
    return [], []


def _boom(*_args, **_kwargs):
    """A stand-in that fails the way a broken hkxcmd or a full disk does."""
    raise OSError('boom')


def _interrupt(*_args, **_kwargs):
    """A stand-in for Ctrl-C landing inside the call."""
    raise KeyboardInterrupt


def _fail_os(monkeypatch, name, when, error=OSError):
    """Make `os.<name>(src, dst)` raise `error` whenever `when(src, dst)` holds."""
    real = getattr(os, name)

    def call(src, dst, *args, **kwargs):
        """The real call, unless this pair of paths is the one to fail."""
        if when(str(src), str(dst)):
            raise error('injected')
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, name, call)


def _has_bged(nif_path):
    """Whether the NIF's root carries a BSBehaviorGraphExtraData."""
    data = sse_nif.read_nif(str(nif_path))
    return any(isinstance(e, NifFormat.BSBehaviorGraphExtraData)
               for e in data.roots[0].extra_data_list)


def _crash_pair_on_disk(dst, tree):
    """Whether `tree` is in place and names a sequence the NIF `dst` lacks."""
    graph = tree / 'Behaviors' / 'Behavior00.hkx'
    if not graph.exists():
        return False
    named = set(hkx_animobject.compiled_generators(str(graph)).values()) - {''}
    return not named <= set(_sequences(sse_nif.read_nif(str(dst))))


def _compile_graph(xml, hkx_path):
    """Compile behaviour XML into the AMD64 packfile `hkx_path`."""
    xml_path = Path(str(hkx_path) + '.xml')
    xml_path.write_text(xml, newline='\n')
    hkx_xml.compile_hkx(str(xml_path), str(hkx_path))
    xml_path.unlink()
    hkx_xml.convert_hkx_to_amd64(str(hkx_path))


#: How a compiled graph can be wrong while every name is still in its string pool.
_WRONG_GRAPHS = {
    'hold plays nothing': ('>ForwardHold</hkparam>', '></hkparam>'),
    'hold plays a sequence no NIF has': ('>ForwardHold</hkparam>',
                                         '>ForwardHoldX</hkparam>'),
    'sequence plays nothing': ('"pSequence">Forward</hkparam>',
                               '"pSequence"></hkparam>'),
    'Rest plays a sequence': ('"pSequence"></hkparam>',
                              '"pSequence">Forward</hkparam>'),
}


def _wrong_graph_xml(kind):
    """The wall's hold graph XML with one generator's pSequence corrupted."""
    xml = behavior_xml('wall', ['Forward', 'Backward'], _BOTH)
    if kind == 'holds swapped':
        return (xml.replace('"pSequence">ForwardHold<', '"pSequence">@<')
                .replace('"pSequence">BackwardHold<', '"pSequence">ForwardHold<')
                .replace('"pSequence">@<', '"pSequence">BackwardHold<'))
    old, new = _WRONG_GRAPHS[kind]
    old = old if old.startswith('"') else '"pSequence"' + old
    new = new if new.startswith('"') else '"pSequence"' + new
    assert xml.count(old) == 1
    return xml.replace(old, new)


def _project(root, sequences, *, stem='wall', holds=True, graph_loops=None):
    """A NIF built in memory and its compiled tree under `root`: (root, NIF, tree).

    `sequences` is [(name, cycle type, has controlled blocks)].  The NIF gets
    the holds the real planner gives it (none with `holds=False`) and a BGED;
    the graph is built from that plan and from the NIF's own LOOP set, unless
    `graph_loops` says otherwise.  Needs no game data.
    """
    data, manager = _mesh()
    for name, cycle, filled in sequences:
        _add_sequence(manager, name, _interpolators() if filled else [],
                      cycle=cycle)
    names = collect_sequence_names(data)
    planned = pose_hold.plan_pose_holds(data, names)[0] if holds else []
    loops = pose_hold.loop_sequences(data, names)
    dst = Path(root) / 'meshes' / 'tes4' / 'dungeons' / 'chargen' / f'{stem}.nif'
    bged = generate_animobject_project(
        str(Path(root) / 'meshes'), f'tes4/dungeons/chargen/{stem}.nif', names,
        pose_hold.hold_names(planned),
        loops=loops if graph_loops is None else graph_loops)
    pose_hold.apply_pose_holds(planned)
    add_animobject_bged(data, bged)
    with open(dst, 'wb') as stream:
        data.write(stream)
    return Path(root), dst, dst.parent / f'{stem}_behavior'


#: Two CLAMP one-shots: the planner holds both.  And a third shape used by the wrong-state test.
_PAIR = [('Forward', 2, True), ('Backward', 2, True)]
_START_SHOT_LOOP = [('SpecialIdle', 2, True), ('Equip', 2, True),
                    ('Forward', 0, True)]


@pytest.fixture(scope='module')
def pair(tmp_path_factory):
    """A synthetic Forward/Backward mesh WITH holds: (root, NIF, tree)."""
    return _project(tmp_path_factory.mktemp('pair_holds'), _PAIR)


@pytest.fixture(scope='module')
def holdless_pair(tmp_path_factory):
    """The same mesh and graph with no holds: (root, NIF, tree)."""
    return _project(tmp_path_factory.mktemp('pair_plain'), _PAIR, holds=False)


def _legacy_held_xml(name='wall'):
    """The held Forward/Backward graph as built before bAnimPlaying: holds, no variable, no wrapper."""
    head, chunks, tail = _chunks(behavior_xml(name, ['Forward', 'Backward'], _BOTH))
    for state, generator in (('Forward', 'GamebryoSequenceGenerator00'),
                             ('Backward', 'GamebryoSequenceGenerator01')):
        _point(chunks, _STATE, state, 'generator', _ref(chunks, _GEN, generator))
    return _legacy_xml(head + '\n\n'.join(chunks) + tail)


def _endless_xml(xml):
    """`xml` with the `End` event taken out of its event table (rows untouched)."""
    assert xml.count('\n\t\t\t<hkcstring>End</hkcstring>') == 1
    xml = xml.replace('\n\t\t\t<hkcstring>End</hkcstring>', '')
    return xml.replace('<hkparam name="eventNames" numelements="4">',
                       '<hkparam name="eventNames" numelements="3">')


def _wall_dst(root):
    """Where the wall sample converts to inside the meshes tree `root`."""
    return Path(root) / 'meshes' / 'tes4' / 'dungeons' / 'chargen' / 'wall.nif'


def _build_wall(root, patch=None):
    """Convert the wall sample into `root`; (result, NIF path, tree path)."""
    dst = _wall_dst(root)
    dst.parent.mkdir(parents=True, exist_ok=True)
    with pytest.MonkeyPatch.context() as mp:
        for name, stand_in in (patch or {}).items():
            mp.setattr(nif_converter, name, stand_in)
        result = nif_converter.convert_nif(str(_sample(_WALL)), str(dst))
    return result, dst, dst.parent / 'wall_behavior'


@pytest.fixture(scope='module')
def wall(tmp_path_factory):
    """The wall sample converted WITH holds: (meshes root, NIF, tree)."""
    root = tmp_path_factory.mktemp('wall_holds')
    result, dst, tree = _build_wall(root)
    assert not result.get('animobject_error'), result.get('animobject_error')
    return root, dst, tree


@pytest.fixture(scope='module')
def holdless_wall(tmp_path_factory):
    """The same pair as built before holds existed: (meshes root, NIF, tree)."""
    root = tmp_path_factory.mktemp('wall_plain')
    result, dst, tree = _build_wall(root, {'plan_pose_holds': _no_holds})
    assert not result.get('animobject_error'), result.get('animobject_error')
    return root, dst, tree


def _copy_pair(src_root, tmp_path):
    """A private copy of a built wall: (meshes root, NIF, tree)."""
    root = tmp_path / 'copy'
    shutil.copytree(src_root, root)
    dst = _wall_dst(root)
    return root, dst, dst.parent / 'wall_behavior'


# ---------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------


class TestHoldGraph:
    """`behavior_xml(..., holds)`: End moves a finished sequence onto its hold."""

    def test_end_is_declared_last_and_only_with_holds(self):
        """Sequence event ids and SoundPlay keep their places; End follows."""
        assert _decode(behavior_xml('wall', ['Forward', 'Backward'], _BOTH))[
            'events'] == ['Forward', 'Backward', SOUND_EVENT, HOLD_EVENT]
        for holds in (None, {}, {'Unequip': 'UnequipHold'}):
            assert _decode(behavior_xml('wall', ['Forward', 'Backward'], holds))[
                'events'] == ['Forward', 'Backward', SOUND_EVENT]

    def test_hold_states_follow_rest_so_no_id_moves(self):
        """Forward 0, Backward 1, Rest 2 as before; the holds take 3 and 4."""
        graph = _decode(behavior_xml('wall', ['Forward', 'Backward'], _BOTH))
        assert {name: state[0] for name, state in graph['states'].items()} == {
            'Forward': 0, 'Backward': 1, 'Rest': 2,
            'ForwardHold': 3, 'BackwardHold': 4}
        assert graph['start'] == 2 and graph['wrappers'] == _BOTH_WRAPPED
        plain = _decode(behavior_xml('wall', ['Forward', 'Backward']))
        assert {name: state[0] for name, state in plain['states'].items()} == {
            'Forward': 0, 'Backward': 1, 'Rest': 2}
        assert plain['wrappers'] == {'Forward': None, 'Backward': None,
                                     'Rest': None}

    def test_held_sequences_gain_one_end_row(self):
        """Each held state reaches its OWN hold on End; Rest has no End row."""
        graph = _decode(behavior_xml('wall', ['Forward', 'Backward'], _BOTH))
        states = graph['states']
        assert graph['wrappers'] == _BOTH_WRAPPED
        assert states['Forward'][3] == [('Backward', 'Backward'),
                                        (HOLD_EVENT, 'ForwardHold')]
        assert states['Backward'][3] == [('Forward', 'Forward'),
                                         (HOLD_EVENT, 'BackwardHold')]
        assert states['Rest'][3] == [('Forward', 'Forward'),
                                     ('Backward', 'Backward')]

    def test_multi_sequence_hold_ignores_its_own_event(self):
        """A hold's rows are its sequence's rows minus End: no same-name row.

        The teleport pads re-send `Backward` every 0.1 s; a same-name row on
        the hold would replay the clip each time it finished.
        """
        graph = _decode(behavior_xml('wall', ['Forward', 'Backward'], _BOTH))
        states = graph['states']
        assert graph['wrappers'] == _BOTH_WRAPPED
        assert states['ForwardHold'][3] == [('Backward', 'Backward')]
        assert states['BackwardHold'][3] == [('Forward', 'Forward')]

    def test_single_sequence_hold_can_replay(self):
        """The lone sequence's self-row is inherited, so the hold is no dead end."""
        graph = _decode(behavior_xml('wall', ['Unequip'],
                                     {'Unequip': 'UnequipHold'}))
        states = graph['states']
        assert graph['wrappers'] == {'Unequip': 'ModifierGenerator00',
                                     'Rest': None, 'UnequipHold': None}
        assert states['Unequip'][3] == [('Unequip', 'Unequip'),
                                        (HOLD_EVENT, 'UnequipHold')]
        assert states['UnequipHold'][3] == [('Unequip', 'Unequip')]
        assert states['UnequipHold'][0] == 2

    def test_only_planned_sequences_are_held(self):
        """A sequence with no hold keeps today's rows; unknown names are ignored."""
        graph = _decode(behavior_xml(
            'wall', ['Forward', 'Backward'],
            {'Backward': 'BackwardHold', 'Left': 'LeftHold'}))
        assert sorted(graph['states']) == ['Backward', 'BackwardHold',
                                           'Forward', 'Rest']
        assert graph['states']['Forward'][3] == [('Backward', 'Backward')]
        assert graph['states']['BackwardHold'][:3] == (
            4, 'GamebryoSequenceGeneratorHold01', 'BackwardHold')
        assert graph['wrappers'] == {'Forward': None, 'Rest': None,
                                     'Backward': 'ModifierGenerator01',
                                     'BackwardHold': None}

    def test_load_state_keeps_the_start_and_gets_no_end_row(self):
        """SpecialIdle still starts the machine; only Forward is held."""
        graph = _decode(behavior_xml('obj', ['SpecialIdle', 'Forward'],
                                     {'Forward': 'ForwardHold'}))
        assert graph['start'] == 0
        assert graph['states']['SpecialIdle'][3] == [('Forward', 'Forward')]
        assert graph['states']['ForwardHold'][0] == 4
        assert graph['wrappers'] == {'SpecialIdle': None, 'Rest': None,
                                     'Forward': 'ModifierGenerator01',
                                     'ForwardHold': None}

    def test_a_hold_id_does_not_depend_on_which_others_are_held(self):
        """One id slot per sequence, used or not: Rest + 1 + sequence index.

        A save stores the state it sits in; an id that moved when a neighbour
        became eligible would point that save at a different state.
        """
        seqs = ['Forward', 'Unequip', 'Backward']
        alone = _decode(behavior_xml('wall', seqs, {'Unequip': 'UnequipHold'}))
        assert {n: st[0] for n, st in alone['states'].items()} == {
            'Forward': 0, 'Unequip': 1, 'Backward': 2, 'Rest': 3,
            'UnequipHold': 5}
        assert alone['states']['Unequip'][3][-1] == (HOLD_EVENT, 'UnequipHold')
        assert alone['wrappers'] == {'Forward': None, 'Backward': None,
                                     'Unequip': 'ModifierGenerator01',
                                     'Rest': None, 'UnequipHold': None}
        every = _decode(behavior_xml('wall', seqs, {
            'Forward': 'ForwardHold', 'Unequip': 'UnequipHold',
            'Backward': 'BackwardHold'}))
        assert {n: st[0] for n, st in every['states'].items()
                if n.endswith('Hold')} == {
            'ForwardHold': 4, 'UnequipHold': 5, 'BackwardHold': 6}

    def test_hold_states_follow_rest_in_the_state_array(self):
        """Array order, not only ids: sequences, Rest, then the holds."""
        graph = _decode(behavior_xml('wall', ['Forward', 'Backward'], _BOTH))
        assert list(graph['states']) == ['Forward', 'Backward', 'Rest',
                                         'ForwardHold', 'BackwardHold']
        mixed = _decode(behavior_xml('wall', ['Forward', 'Unequip', 'Backward'],
                                     {'Backward': 'BackwardHold'}))
        assert list(mixed['states']) == ['Forward', 'Unequip', 'Backward',
                                         'Rest', 'BackwardHold']
        assert [w for w in mixed['wrappers'].values() if w] == [
            'ModifierGenerator02']

    @pytest.mark.parametrize('holds', [None, _BOTH])
    def test_every_event_has_an_event_info(self, holds):
        """`eventInfos` is indexed by event id; a short array is read past."""
        xml = behavior_xml('wall', ['Forward', 'Backward'], holds)
        infos = int(re.search(r'name="eventInfos" numelements="(\d+)"',
                              xml).group(1))
        assert infos == len(_decode(xml)['events']) == (4 if holds else 3)

    @pytest.mark.parametrize('seqs, shot', [
        (['SpecialIdle', 'Forward'], 'Forward'),
        (['Forward', 'AutoLoop', 'AutoPlay'], 'Forward'),
    ])
    def test_a_lone_one_shot_beside_load_sequences_can_replay(self, seqs, shot):
        """Load sequences are not "another sequence": the one-shot keeps a self-row.

        The hold inherits it, so a finished one-shot plays again on a re-send;
        a load state gains no self-row.
        """
        graph = _decode(behavior_xml('obj', seqs, {shot: shot + 'Hold'}))
        states = graph['states']
        assert [w for w in graph['wrappers'].values() if w] == [
            f'ModifierGenerator{seqs.index(shot):02d}']
        others = [(s, s) for s in seqs if s != shot]
        assert states[shot][3] == others + [(shot, shot),
                                            (HOLD_EVENT, shot + 'Hold')]
        assert states[shot + 'Hold'][3] == others + [(shot, shot)]
        for load in (s for s in seqs if s != shot):
            assert (load, load) not in states[load][3]

    def test_two_one_shots_beside_a_load_sequence_get_no_self_row(self):
        """With another one-shot to reach, a repeated event stays a no-op."""
        graph = _decode(behavior_xml(
            'obj', ['SpecialIdle', 'Forward', 'Backward'], _BOTH))
        states = graph['states']
        assert graph['wrappers'] == {
            'SpecialIdle': None, 'Forward': 'ModifierGenerator01',
            'Backward': 'ModifierGenerator02', 'Rest': None,
            'ForwardHold': None, 'BackwardHold': None}
        assert states['ForwardHold'][3] == [('SpecialIdle', 'SpecialIdle'),
                                            ('Backward', 'Backward')]
        assert states['SpecialIdle'][3] == [('Forward', 'Forward'),
                                            ('Backward', 'Backward')]

    def test_each_hold_generator_names_its_hold(self):
        """Rest alone plays nothing; a hold generator plays the hold sequence."""
        xml = behavior_xml('wall', ['Forward', 'Backward'], _BOTH)
        states = _decode(xml)['states']
        assert xml.count('<hkparam name="pSequence"></hkparam>') == 1
        assert states['Rest'][1:3] == ('GamebryoSequenceGeneratorRest', '')
        assert states['ForwardHold'][1:3] == (
            'GamebryoSequenceGeneratorHold00', 'ForwardHold')
        assert {name: state[1:3] for name, state in states.items()} == {
            'Forward': ('GamebryoSequenceGenerator00', 'Forward'),
            'Backward': ('GamebryoSequenceGenerator01', 'Backward'),
            'Rest': ('GamebryoSequenceGeneratorRest', ''),
            'ForwardHold': ('GamebryoSequenceGeneratorHold00', 'ForwardHold'),
            'BackwardHold': ('GamebryoSequenceGeneratorHold01', 'BackwardHold')}
        assert graph_generators(['Forward', 'Backward'], _BOTH) == {
            gen: seq for _id, gen, seq, _rows in states.values()}

    @pytest.mark.parametrize('holds', [None, _BOTH])
    @pytest.mark.parametrize('seqs', [['Forward'], ['Forward', 'Backward'],
                                      ['Forward', 'Backward', 'Unequip']])
    def test_wildcard_transitions_are_null(self, seqs, holds):
        """Vanilla's Gamebryo machines ship null; every array belongs to a state."""
        xml = behavior_xml('wall', seqs, holds)
        graph = _decode(xml)
        assert graph['wildcard'] == 'null'
        assert xml.count('class="hkbStateMachineTransitionInfoArray"') == \
            len(graph['states'])
        assert sum(w is not None for w in graph['wrappers'].values()) == (
            len(seqs) if holds and len(seqs) < 3 else 2 if holds else 0)

    @pytest.mark.parametrize('seqs', sorted(_NO_HOLD_XML))
    def test_a_graph_with_nothing_wrapped_is_the_old_graph_plus_the_variable(
            self, seqs):
        """Take the variable's three entries out and the pre-hold XML is back.

        The digests were made by the emitter that predates holds and the
        variable, so nothing else may have moved.
        """
        xml = behavior_xml('wall', list(seqs))
        assert xml == behavior_xml('wall', list(seqs), {}, ())
        assert xml != _legacy_xml(xml)
        assert hashlib.sha256(_legacy_xml(xml).encode()).hexdigest() == \
            _NO_HOLD_XML[seqs]


# ---------------------------------------------------------------------------
# bAnimPlaying: the graph bool converted IsAnimPlaying reads
# ---------------------------------------------------------------------------


#: (sequences, holds, loops) -> {state: (wrapper or None, generator, sequence)}, written out by hand.
_SHAPES = {
    'two held': (
        ['Forward', 'Backward'], _BOTH, (), {
            'Forward': ('ModifierGenerator00', 'GamebryoSequenceGenerator00', 'Forward'),
            'Backward': ('ModifierGenerator01', 'GamebryoSequenceGenerator01', 'Backward'),
            'Rest': (None, 'GamebryoSequenceGeneratorRest', ''),
            'ForwardHold': (None, 'GamebryoSequenceGeneratorHold00', 'ForwardHold'),
            'BackwardHold': (None, 'GamebryoSequenceGeneratorHold01', 'BackwardHold')}),
    'single held': (
        ['Unequip'], {'Unequip': 'UnequipHold'}, (), {
            'Unequip': ('ModifierGenerator00', 'GamebryoSequenceGenerator00', 'Unequip'),
            'Rest': (None, 'GamebryoSequenceGeneratorRest', ''),
            'UnequipHold': (None, 'GamebryoSequenceGeneratorHold00', 'UnequipHold')}),
    'LOOP start state beside a held one-shot': (
        ['SpecialIdle', 'Forward'], {'Forward': 'ForwardHold'}, ('SpecialIdle',), {
            'SpecialIdle': ('ModifierGenerator00', 'GamebryoSequenceGenerator00', 'SpecialIdle'),
            'Forward': ('ModifierGenerator01', 'GamebryoSequenceGenerator01', 'Forward'),
            'Rest': (None, 'GamebryoSequenceGeneratorRest', ''),
            'ForwardHold': (None, 'GamebryoSequenceGeneratorHold01', 'ForwardHold')}),
    'hold-less CLAMP start state beside wrapped states': (
        ['SpecialIdle', 'Equip', 'Forward', 'Backward'],
        {'Equip': 'EquipHold', 'Backward': 'BackwardHold'}, ('Forward',), {
            'SpecialIdle': (None, 'GamebryoSequenceGenerator00', 'SpecialIdle'),
            'Equip': ('ModifierGenerator01', 'GamebryoSequenceGenerator01', 'Equip'),
            'Forward': ('ModifierGenerator02', 'GamebryoSequenceGenerator02', 'Forward'),
            'Backward': ('ModifierGenerator03', 'GamebryoSequenceGenerator03', 'Backward'),
            'Rest': (None, 'GamebryoSequenceGeneratorRest', ''),
            'EquipHold': (None, 'GamebryoSequenceGeneratorHold01', 'EquipHold'),
            'BackwardHold': (None, 'GamebryoSequenceGeneratorHold03', 'BackwardHold')}),
    'nothing wrapped': (
        ['Forward'], None, (), {
            'Forward': (None, 'GamebryoSequenceGenerator00', 'Forward'),
            'Rest': (None, 'GamebryoSequenceGeneratorRest', '')}),
    'hold planning failed, one LOOP': (
        ['Forward', 'Backward'], None, ('Backward',), {
            'Forward': (None, 'GamebryoSequenceGenerator00', 'Forward'),
            'Backward': ('ModifierGenerator01', 'GamebryoSequenceGenerator01', 'Backward'),
            'Rest': (None, 'GamebryoSequenceGeneratorRest', '')}),
    'loops matched by exact name, unknown names ignored': (
        ['Forward', 'fastforward'], None, ('fastforward', 'forward', 'Death'), {
            'Forward': (None, 'GamebryoSequenceGenerator00', 'Forward'),
            'fastforward': ('ModifierGenerator01', 'GamebryoSequenceGenerator01', 'fastforward'),
            'Rest': (None, 'GamebryoSequenceGeneratorRest', '')}),
}

#: What `graph_node_names` must list for each shape: wrapper right before its generator, in file order.
_SHAPE_NODES = {
    'two held': 'M00 G00 M01 G01 GRest GHold00 GHold01',
    'single held': 'M00 G00 GRest GHold00',
    'LOOP start state beside a held one-shot': 'M00 G00 M01 G01 GRest GHold01',
    'hold-less CLAMP start state beside wrapped states':
        'G00 M01 G01 M02 G02 M03 G03 GRest GHold01 GHold03',
    'nothing wrapped': 'G00 GRest',
    'hold planning failed, one LOOP': 'G00 M01 G01 GRest',
    'loops matched by exact name, unknown names ignored': 'G00 M01 G01 GRest',
}


def _node_names(short):
    """`M01 G01 GRest` spelled out as the names the string pool holds."""
    return [('ModifierGenerator' if n[0] == 'M' else 'GamebryoSequenceGenerator')
            + n[1:] for n in short.split()]


class TestPlayingVariable:
    """`bAnimPlaying` reads 1 exactly while a held or looping sequence state is active."""

    @pytest.mark.parametrize('shape', sorted(_SHAPES))
    def test_wrapped_states_per_shape(self, shape):
        """State by state: its wrapper or none, its own generator, its own sequence.

        Held or LOOP states are wrapped; Rest, the holds and a CLAMP state
        with no hold stay bare.  A wrapper carries its state's id.
        """
        seqs, holds, loops, table = _SHAPES[shape]
        xml = behavior_xml('obj', seqs, holds, loops)
        graph = _decode(xml)
        assert {name: (graph['wrappers'][name],) + state[1:3]
                for name, state in graph['states'].items()} == table
        assert sorted(_playing(xml)['wrappers']) == sorted(
            w for w, _gen, _seq in table.values() if w)
        assert hkx_animobject.graph_node_names(seqs, holds, loops) == \
            _node_names(_SHAPE_NODES[shape])

    @pytest.mark.parametrize('shape', sorted(_SHAPES))
    def test_the_variable_is_always_declared_once_bool_and_zero(self, shape):
        """Wrapped or not, the read finds a BOOL at 0 instead of logging an error."""
        seqs, holds, loops, _table = _SHAPES[shape]
        playing = _playing(behavior_xml('obj', seqs, holds, loops))
        assert playing['variables'] == [hkx_animobject.PLAYING_VARIABLE] == [
            'bAnimPlaying']
        assert playing['types'] == ['VARIABLE_TYPE_BOOL']
        assert playing['words'] == ['0']

    def test_one_shared_modifier_writes_the_variable(self):
        """The whole field set of the shape proven in game, read from the XML."""
        playing = _playing(behavior_xml('wall', ['Forward', 'Backward'], _BOTH))
        modifier, = playing['modifiers']
        binding, = playing['bindings']
        assert modifier == {
            'ref': modifier['ref'], 'variableBindingSet': binding['ref'],
            'userData': '2', 'name': 'IsActiveModifier', 'enable': 'true',
            **{f'bIsActive{i}': 'false' for i in range(5)},
            **{f'bInvertActive{i}': 'false' for i in range(5)}}
        assert binding['indexOfBindingToEnable'] == '-1'
        assert binding['rows'] == [{
            'memberPath': 'bIsActive0', 'variableIndex': '0', 'bitIndex': '-1',
            'bindingType': 'BINDING_TYPE_VARIABLE'}]
        assert playing['bound'] == ['BSIsActiveModifier']
        assert {name: (w['modifier'], w['variableBindingSet'], w['userData'])
                for name, w in playing['wrappers'].items()} == {
            'ModifierGenerator00': (modifier['ref'], 'null', '1'),
            'ModifierGenerator01': (modifier['ref'], 'null', '1')}
        assert 'hkbModifierList' not in playing['classes']

    @pytest.mark.parametrize('shape', sorted(_SHAPES))
    def test_every_wrapper_names_the_one_modifier(self, shape):
        """Whatever its state id: the shared modifier, no binding set of its own.

        The compiled check and the validator read names only, so a wrapper
        with a null modifier would pass both and leave its state unwritten.
        """
        seqs, holds, loops, table = _SHAPES[shape]
        playing = _playing(behavior_xml('obj', seqs, holds, loops))
        wrappers = sorted(w for w, _gen, _seq in table.values() if w)
        assert len(playing['modifiers']) == len(playing['bindings']) == int(
            bool(wrappers))
        assert {name: (w['modifier'], w['variableBindingSet'], w['userData'])
                for name, w in playing['wrappers'].items()} == {
            name: (playing['modifiers'][0]['ref'], 'null', '1')
            for name in wrappers}

    def test_nothing_wrapped_means_no_writer_at_all(self):
        """No modifier, no binding set, no wrapper: hkxcmd would drop an orphan anyway."""
        playing = _playing(behavior_xml('fx', ['Forward']))
        assert (playing['modifiers'], playing['bindings'], playing['wrappers'],
                playing['bound']) == ([], [], {}, [])
        assert not {'BSIsActiveModifier', 'hkbVariableBindingSet',
                    'hkbModifierGenerator'} & set(playing['classes'])

    def test_the_xml_does_not_depend_on_what_else_was_imported(self):
        """The three new classes carry their signatures from `hkx_xml` itself."""
        xml = behavior_xml('wall', ['Forward', 'Backward'], _BOTH)
        for klass, signature in (('hkbVariableBindingSet', '0x338ad4ff'),
                                 ('hkbModifierGenerator', '0x1f81fae6'),
                                 ('BSIsActiveModifier', '0xb0fde45a')):
            assert hkx_xml.SIGNATURES[klass] == signature
            assert f'class="{klass}" signature="{signature}"' in xml

    def test_the_compiled_lever_graph_is_the_one_proven_in_game(self, tmp_path):
        """sha256 of the file tested live on 2026-10-03: a held Forward/Backward lever.

        The digest was not made by this emitter's current code, and it is the
        only check that sees the scalar fields in the COMPILED file.
        """
        staged = stage_animobject_project(
            str(tmp_path), 'x/rfswitch01.nif', ['Forward', 'Backward'], _BOTH,
            loops=())
        graph = Path(staged.stage_dir) / 'Behaviors' / 'Behavior00.hkx'
        assert hashlib.sha256(graph.read_bytes()).hexdigest() == _LEVER_GRAPH

    @pytest.mark.parametrize('shape', sorted(_SHAPES))
    def test_every_shape_compiles_to_the_expected_nodes(self, tmp_path, shape):
        """The compiled pool lists wrappers and generators in the expected order."""
        seqs, holds, loops, _table = _SHAPES[shape]
        staged = stage_animobject_project(str(tmp_path), 'a/obj.nif', seqs, holds,
                                          loops=loops)
        pool = _pool(Path(staged.stage_dir) / 'Behaviors' / 'Behavior00.hkx')
        nodes = _node_names(_SHAPE_NODES[shape])
        assert [p for p in pool if 'Generator' in p and p[:3] in ('Gam', 'Mod')] == nodes
        assert pool.count('bAnimPlaying') == 1
        assert pool.count('bIsActive0') == pool.count('IsActiveModifier') == int(
            any(n.startswith('Modifier') for n in nodes))

    @pytest.mark.parametrize('kind', _WRONG_PLAYING)
    def test_a_compiled_graph_with_the_wrong_writer_is_rejected(
            self, tmp_path, monkeypatch, kind):
        """Each compiles and exits 0; the ordered node names or the binding count catch it."""
        monkeypatch.setattr(hkx_animobject, 'behavior_xml',
                            lambda *_a, **_k: _wrong_playing_xml(kind))
        with pytest.raises(RuntimeError, match='is wrong'):
            stage_animobject_project(str(tmp_path), 'tes4/a/wall.nif',
                                     ['Forward', 'Backward'], _BOTH, loops=())
        assert not [p for p in tmp_path.rglob('*') if p.is_file()]

    def test_a_compiled_graph_with_holds_and_no_end_event_is_rejected(
            self, tmp_path, monkeypatch):
        """Nothing could ever leave the sequence states for their holds."""
        real = hkx_animobject.behavior_xml
        monkeypatch.setattr(hkx_animobject, 'behavior_xml',
                            lambda *a: _endless_xml(real(*a)))
        with pytest.raises(RuntimeError, match='End not declared'):
            stage_animobject_project(str(tmp_path), 'tes4/a/wall.nif',
                                     ['Forward', 'Backward'], _BOTH, loops=())

    @pytest.mark.parametrize('stem', ['ModifierGenerator01', 'bIsActive0'])
    def test_a_model_named_like_a_graph_node_still_builds(self, tmp_path, stem):
        """The graph is named after the model; its own name is not a node."""
        generate_animobject_project(str(tmp_path), f'a/{stem}.nif',
                                    ['Forward', 'Backward'], _BOTH, loops=())
        pool = _pool(tmp_path / 'a' / f'{stem}_behavior' / 'Behaviors'
                     / 'Behavior00.hkx')
        assert pool.count(stem) == 3

    @pytest.mark.parametrize('stem, damage, message', [
        ('bAnimPlaying', _legacy_xml, 'bAnimPlaying not declared'),
        ('End', _endless_xml, 'End not declared'),
    ])
    def test_a_model_name_does_not_stand_in_for_the_variable_or_the_event(
            self, tmp_path, monkeypatch, stem, damage, message):
        """A model called `End` must not hide a graph that lost its End event."""
        real = hkx_animobject.behavior_xml
        monkeypatch.setattr(hkx_animobject, 'behavior_xml',
                            lambda *a: damage(real(*a)))
        with pytest.raises(RuntimeError, match=message):
            stage_animobject_project(str(tmp_path), f'a/{stem}.nif',
                                     ['Forward', 'Backward'], _BOTH, loops=())

    def test_loops_given_as_one_name_is_refused(self, tmp_path):
        """`set('Forward')` is seven letters: it would silently mean "no loops"."""
        with pytest.raises(TypeError, match='collection of sequence names'):
            stage_animobject_project(str(tmp_path), 'a/fx.nif', ['Forward'],
                                     loops='Forward')

    @pytest.mark.parametrize('emitted, expected, message', [
        (('Forward',), (), 'nodes are'),
        ((), ('Forward',), 'nodes are'),
    ])
    def test_a_compiled_graph_wrapped_differently_than_asked_is_rejected(
            self, tmp_path, monkeypatch, emitted, expected, message):
        """A wrapper where none is expected, and a LOOP state left bare."""
        real = hkx_animobject.behavior_xml
        monkeypatch.setattr(hkx_animobject, 'behavior_xml',
                            lambda name, seqs, holds, _loops: real(
                                name, seqs, holds, emitted))
        with pytest.raises(RuntimeError, match=message):
            stage_animobject_project(str(tmp_path), 'tes4/a/fx.nif', ['Forward'],
                                     loops=expected)

    def test_a_compiled_graph_without_the_variable_is_rejected(self, tmp_path,
                                                               monkeypatch):
        """The build never emits a legacy graph."""
        real = hkx_animobject.behavior_xml
        monkeypatch.setattr(hkx_animobject, 'behavior_xml',
                            lambda *a: _legacy_xml(real(*a)))
        with pytest.raises(RuntimeError, match='bAnimPlaying not declared'):
            stage_animobject_project(str(tmp_path), 'tes4/a/fx.nif', ['Forward'],
                                     loops=())

    def test_loops_has_no_default_on_the_layers_that_ship_a_tree(self, tmp_path):
        """Forgetting it would leave looping states bare with every check agreeing."""
        with pytest.raises(TypeError, match='loops'):
            stage_animobject_project(str(tmp_path), 'a/fx.nif', ['Forward'])
        with pytest.raises(TypeError, match='loops'):
            generate_animobject_project(str(tmp_path), 'a/fx.nif', ['Forward'])

    @pytest.mark.parametrize('cycle, loops', [(0, {'Forward'}), (1, set()),
                                              (2, set())])
    def test_only_cycle_type_loop_never_finishes(self, cycle, loops):
        """REVERSE finishes like CLAMP; wrapped as a loop it would read 1 for ever."""
        data, manager = _mesh()
        _add_sequence(manager, 'Forward', _interpolators(), cycle=cycle)
        assert pose_hold.loop_sequences(data, ['Forward']) == loops

    def test_loop_set_follows_the_sequence_the_graph_plays(self):
        """First of each name with controlled blocks; exact case; graph sequences only."""
        data, manager = _mesh()
        _add_sequence(manager, 'Forward', [], cycle=0)
        _add_sequence(manager, 'Forward', _interpolators(), cycle=2)
        _add_sequence(manager, 'Forward', _interpolators(), cycle=0)
        _add_sequence(manager, 'fastforward', _interpolators(), cycle=0)
        _add_sequence(manager, 'Death', _interpolators(), cycle=0)
        assert pose_hold.loop_sequences(
            data, ['Forward', 'fastforward', 'FastForward']) == {'fastforward'}


# ---------------------------------------------------------------------------
# The NIF hold builder
# ---------------------------------------------------------------------------


class TestPoseHoldBuilder:
    """`pose_hold`: a one-frame `<Seq>Hold` frozen at the source's last keys."""

    def _held(self):
        """(data, source sequence, hold sequence) of a planned and applied mesh."""
        data, manager = _mesh()
        source = _add_sequence(manager, 'Forward', _interpolators())
        planned, refused = pose_hold.plan_pose_holds(data, ['Forward'])
        assert not refused
        assert pose_hold.apply_pose_holds(planned) == {'Forward': 'ForwardHold'}
        return data, source, _sequences(data)['ForwardHold']

    def test_hold_is_one_clamped_frame(self):
        """0..1/30 s, CLAMP, same frequency and weight as its source."""
        _data, source, hold = self._held()
        assert (hold.start_time, hold.stop_time) == (0.0, pose_hold.HOLD_SECONDS)
        assert int(hold.cycle_type) == 2
        assert (hold.frequency, hold.weight) == (source.frequency, source.weight)

    def test_text_keys_are_a_fresh_start_end_pair(self):
        """A shared block would replay the source's sound key on every load."""
        _data, source, hold = self._held()
        assert _text_keys(hold) == [(0.0, b'start'),
                                    (pose_hold.HOLD_SECONDS, b'end')]
        assert hold.text_keys is not source.text_keys
        assert _text_keys(source) == list(_SOUNDED)

    def test_hold_covers_every_controlled_block(self):
        """Same blocks, order, node names and controller objects as the source."""
        _data, source, hold = self._held()
        assert hold.num_controlled_blocks == source.num_controlled_blocks == 7
        for mine, theirs in zip(hold.controlled_blocks, source.controlled_blocks):
            assert mine.controller is theirs.controller
            assert bytes(mine.node_name) == bytes(theirs.node_name)
            assert bytes(mine.controller_type) == bytes(theirs.controller_type)

    def test_keyed_channels_become_two_linear_keys_at_the_last_value(self):
        """Never QUADRATIC: pyffi writes those without tangents."""
        _data, source, hold = self._held()
        for mine, theirs in zip(list(hold.controlled_blocks)[:4],
                                list(source.controlled_blocks)[:4]):
            assert mine.interpolator is not theirs.interpolator
            assert type(mine.interpolator) is type(theirs.interpolator)
            want = [(pose_hold.KEY_LINEAR,
                     [(0.0, keys[-1][1]), (pose_hold.HOLD_SECONDS, keys[-1][1])])
                    for _kind, keys in _channels(theirs.interpolator)]
            assert want and _channels(mine.interpolator) == want

    def test_euler_rotation_stays_euler(self):
        """XYZ rotation keeps one key group per axis; quaternions stay quaternions."""
        _data, _source, hold = self._held()
        euler, quat = [b.interpolator.data for b in list(hold.controlled_blocks)[:2]]
        assert int(euler.rotation_type) == pose_hold.XYZ_ROTATION_KEY
        assert [g.num_keys for g in euler.xyz_rotations] == [0, 0, 2]
        assert int(quat.rotation_type) == pose_hold.KEY_LINEAR
        assert [(k.value.w, k.value.x) for k in quat.quaternion_keys] == [
            (0.5, 0.5), (0.5, 0.5)]

    def test_keyed_bool_becomes_a_dataless_constant(self):
        """The last key (hidden) is the constant; no key data is carried."""
        _data, source, hold = self._held()
        mine = hold.controlled_blocks[4].interpolator
        assert type(mine) is NifFormat.NiBoolInterpolator
        assert mine is not source.controlled_blocks[4].interpolator
        assert mine.data is None and not mine.bool_value

    def test_dataless_and_null_interpolators_are_shared(self):
        """Already constants: the hold points at the very same objects."""
        _data, source, hold = self._held()
        assert hold.controlled_blocks[5].interpolator is \
            source.controlled_blocks[5].interpolator
        assert hold.controlled_blocks[6].interpolator is None

    def test_source_sequence_is_left_as_authored(self):
        """Planning and applying never touch the transition's own keys."""
        data, manager = _mesh()
        source = _add_sequence(manager, 'Forward', _interpolators())
        before = [_channels(b.interpolator) for b in source.controlled_blocks]
        pose_hold.apply_pose_holds(pose_hold.plan_pose_holds(data, ['Forward'])[0])
        assert [_channels(b.interpolator)
                for b in source.controlled_blocks] == before
        assert (source.start_time, source.stop_time) == (0.0, 2.0)

    def test_planning_does_not_touch_the_nif(self):
        """The plan is computed first so a failed graph leaves the mesh hold-less."""
        data, manager = _mesh()
        _add_sequence(manager, 'Forward', _interpolators())
        planned, _refused = pose_hold.plan_pose_holds(data, ['Forward'])
        assert pose_hold.hold_names(planned) == {'Forward': 'ForwardHold'}
        assert sorted(_sequences(data)) == ['Forward']
        assert pose_hold.missing_holds(data, planned) == ['ForwardHold']
        pose_hold.apply_pose_holds(planned)
        assert pose_hold.missing_holds(data, planned) == []

    def test_holds_are_not_events(self):
        """`collect_sequence_names` must not list a hold: it is no PlayAnimation target."""
        data, _source, _hold = self._held()
        assert collect_sequence_names(data) == ['Forward']

    def test_round_trip_through_pyffi(self):
        """Written and re-read, the hold still has its keys, values and text keys."""
        data, _source, _hold = self._held()
        seqs = _sequences(_reread(data))
        assert sorted(seqs) == ['Forward', 'ForwardHold']
        hold, source = seqs['ForwardHold'], seqs['Forward']
        stop = hold.stop_time
        assert abs(stop - 1.0 / 30.0) < 1e-7
        assert _text_keys(hold) == [(0.0, b'start'), (stop, b'end')]
        assert _text_keys(source) == list(_SOUNDED)
        for mine, theirs in zip(list(hold.controlled_blocks)[:4],
                                list(source.controlled_blocks)[:4]):
            want = [(pose_hold.KEY_LINEAR, [(0.0, keys[-1][1]), (stop, keys[-1][1])])
                    for _kind, keys in _channels(theirs.interpolator)]
            assert want and _channels(mine.interpolator) == want
        assert hold.controlled_blocks[4].interpolator.data is None
        assert not hold.controlled_blocks[4].interpolator.bool_value

    @pytest.mark.parametrize('name, kwargs', [
        ('Forward', {'cycle': 0}),
        ('SpecialIdle', {}),
        ('AutoPlay', {}),
        ('AutoLoop', {'cycle': 0}),
        ('Open', {}),
        ('Forward', {'frequency': 0.0}),
        ('Forward', {'keys': ((0.0, b'start'), (1.0, b'end'))}),
        ('Forward', {'keys': ((0.0, b'start'), (2.0, b'end'), (2.0, b'Enum: X'))}),
        ('Forward', {'keys': ((0.0, b'start'),)}),
    ])
    def test_ineligible_sequences_get_no_hold(self, name, kwargs):
        """LOOP, load and native names, frequency 0, and `end` early, not last or absent."""
        data, manager = _mesh()
        _add_sequence(manager, name, _interpolators(), **kwargs)
        planned, refused = pose_hold.plan_pose_holds(data, [name])
        assert (planned, refused) == ([], [])

    def test_only_graph_sequences_and_free_names_are_planned(self):
        """Not in the graph's list, or `<Seq>Hold` already taken: no hold."""
        data, manager = _mesh()
        _add_sequence(manager, 'Forward', _interpolators())
        assert pose_hold.plan_pose_holds(data, ['Backward'])[0] == []
        _add_sequence(manager, 'ForwardHold', _interpolators())
        assert pose_hold.plan_pose_holds(data, ['Forward'])[0] == []

    def test_unsupported_interpolator_refuses_the_whole_sequence(self):
        """One block no constant can replace: no hold, the manager untouched."""
        data, manager = _mesh()
        _add_sequence(manager, 'Forward',
                      _interpolators() + [NifFormat.NiPathInterpolator()])
        _add_sequence(manager, 'Backward', _interpolators())
        planned, refused = pose_hold.plan_pose_holds(data, ['Forward', 'Backward'])
        assert refused == [('Forward', 'unsupported interpolator NiPathInterpolator')]
        assert pose_hold.hold_names(planned) == {'Backward': 'BackwardHold'}
        assert sorted(_sequences(data)) == ['Backward', 'Forward']

    @pytest.mark.parametrize('keys, reason', [
        ([(0.0, 0.0), (2.5, 1.0)], 'key past stop time'),
        ([(1.0, 0.0), (0.5, 1.0)], 'unsorted keys'),
    ])
    def test_keys_the_last_value_cannot_be_read_from_refuse(self, keys, reason):
        """A key after stop time or out of order has no trustworthy end value."""
        data, manager = _mesh()
        _add_sequence(manager, 'Forward', [
            _keyed('NiFloatInterpolator', 'NiFloatData', 1, keys)])
        assert pose_hold.plan_pose_holds(data, ['Forward']) == (
            [], [('Forward', reason)])


# ---------------------------------------------------------------------------
# The converter: graph and NIF agree, and move together
# ---------------------------------------------------------------------------


class TestGraphAndNifMoveTogether:
    """A graph naming a hold its NIF lacks crashes the game when End fires."""

    def test_real_mesh_ships_holds_its_graph_names(self, wall):
        """Re-read from disk: both holds exist and the compiled graph names them."""
        _root, dst, tree = wall
        seqs = _sequences(sse_nif.read_nif(str(dst)))
        assert sorted(seqs) == ['Backward', 'BackwardHold', 'Forward',
                                'ForwardHold']
        graph = tree / 'Behaviors' / 'Behavior00.hkx'
        assert graph.read_bytes()[0x10] == 8, 'Behavior00.hkx is not AMD64'
        pool = _pool(graph)
        assert HOLD_EVENT in pool
        named = [pool[i + 1] for i, s in enumerate(pool)
                 if s.startswith('GamebryoSequenceGeneratorHold')]
        assert sorted(named) == ['BackwardHold', 'ForwardHold']
        assert set(named) <= set(seqs)

    def test_real_hold_drops_the_sound_key_and_freezes_the_end_pose(self, wall):
        """`Forward` keeps its sound key; `ForwardHold` is `[start, end]` only."""
        _root, dst, _tree = wall
        seqs = _sequences(sse_nif.read_nif(str(dst)))
        source, hold = seqs['Forward'], seqs['ForwardHold']
        assert any(v.startswith(b'SoundPlay.') for _t, v in _text_keys(source))
        assert _text_keys(hold) == [(0.0, b'start'), (hold.stop_time, b'end')]
        checked = 0
        for mine, theirs in zip(hold.controlled_blocks, source.controlled_blocks):
            for (kind, keys), (_k, src) in zip(_channels(mine.interpolator),
                                               _channels(theirs.interpolator)):
                assert kind == pose_hold.KEY_LINEAR
                assert [v for _t, v in keys] == [src[-1][1]] * 2
                checked += 1
        assert checked, 'the sample no longer has a keyed channel'

    @pytest.mark.parametrize('sample, nodes, patch', [
        (_WALL, 'M00 G00 M01 G01 GRest GHold00 GHold01', {}),
        (_TRIPWIRE, 'M00 G00 GRest GHold00', {}),
        (_LOOPING, 'M00 G00 GRest', {}),
        (_LOOP_AND_SHOT, 'M00 G00 M01 G01 GRest GHold01', {}),
        (_BARE_START, 'G00 M01 G01 M02 G02 GRest GHold01', {}),
        (_PATH_DRIVEN, 'G00 GRest', {}),
        (_LOOP_AND_SHOT, 'M00 G00 G01 GRest', {'plan_pose_holds': _boom}),
    ])
    def test_real_meshes_wrap_their_held_and_looping_states(
            self, tmp_path, capsys, sample, nodes, patch):
        """From the NIF through the compiled pool: held or LOOP, and nothing else.

        The last row is a hold-planning failure: the one-shot loses its hold
        and its wrapper, the LOOP start state keeps reading as playing.
        """
        dst = tmp_path / 'meshes' / 'tes4' / 'obj.nif'
        with pytest.MonkeyPatch.context() as mp:
            for name, stand_in in patch.items():
                mp.setattr(nif_converter, name, stand_in)
            result = nif_converter.convert_nif(str(_sample(sample)), str(dst))
        assert not result.get('animobject_error')
        pool = _pool(dst.parent / 'obj_behavior' / 'Behaviors' / 'Behavior00.hkx')
        assert [p for p in pool if p.startswith(
            ('GamebryoSequenceGenerator', 'ModifierGenerator'))] == _node_names(nodes)
        assert pool.count('bAnimPlaying') == 1
        assert pool.count('bIsActive0') == int('M' in nodes)
        assert _audit(tmp_path, capsys)[0] == 0

    def test_no_meshes_root_means_no_graph_and_no_hold(self, tmp_path):
        """Holds are added at the graph seam, so a graph-less mesh gets none."""
        dst = tmp_path / 'wall.nif'
        nif_converter.convert_nif(str(_sample(_WALL)), str(dst))
        assert sorted(_sequences(sse_nif.read_nif(str(dst)))) == [
            'Backward', 'Forward']
        assert not list(tmp_path.rglob('*.hkx'))

    def test_holds_do_not_reach_the_rest_pose_passes(self, tmp_path, monkeypatch):
        """Each swapped node rests as `Forward` STARTS, not as its hold ends.

        The tripwire's base wrapper starts visible and ends hidden; a hold
        present when rest visibility is computed would hide it from the start.
        """
        monkeypatch.setattr(
            nif_converter, 'stage_animobject_project',
            lambda *_a, **_k: hkx_animobject.StagedProject('a\\b.hkx', None, None))
        dst = tmp_path / 'meshes' / 'tes4' / 'wire.nif'
        nif_converter.convert_nif(str(_sample(_TRIPWIRE)), str(dst))
        data = sse_nif.read_nif(str(dst))
        seqs = _sequences(data)
        hidden = {bytes(b.name): bool(int(b.flags) & 1) for b in data.roots[0].tree()
                  if isinstance(b, NifFormat.NiAVObject)}
        ends_hidden = 0
        for hold, source in zip(seqs['ForwardHold'].controlled_blocks,
                                seqs['Forward'].controlled_blocks):
            if bytes(source.controller_type) != b'NiVisController':
                continue
            starts_visible = bool(source.interpolator.data.data.keys[0].value)
            assert hidden[bytes(source.node_name)] == (not starts_visible)
            ends_hidden += starts_visible and not hold.interpolator.bool_value
        assert ends_hidden, 'no node starts visible and ends hidden: sample changed'

    @pytest.mark.parametrize('sample', [_PATH_DRIVEN, _FROZEN])
    def test_real_refusals_convert_as_before(self, tmp_path, monkeypatch, sample):
        """A path-driven clip and a frequency-0 clip get no hold and no End."""
        seen = {}

        def stage(_root, _rel, sequences, holds=None, *, loops):
            """Record what the graph would be built from; compile nothing."""
            seen.update(sequences=sequences, holds=holds, loops=loops)
            return hkx_animobject.StagedProject('a\\b.hkx', None, None)

        monkeypatch.setattr(nif_converter, 'stage_animobject_project', stage)
        dst = tmp_path / 'meshes' / 'tes4' / 'fx.nif'
        nif_converter.convert_nif(str(_sample(sample)), str(dst))
        assert 'Forward' in seen['sequences'] and seen['holds'] == {}
        assert seen['loops'] == ({'AutoLoop'} if sample == _FROZEN else set())
        assert not [n for n in _sequences(sse_nif.read_nif(str(dst)))
                    if n.endswith('Hold')]

    def test_failed_serialize_keeps_the_previous_pair(self, wall, tmp_path,
                                                      monkeypatch):
        """A write error must not leave the NEW graph beside the OLD NIF.

        The rebuild is forced to plan no holds, so its graph differs from the
        one on disk; after the failed write every file is still the old one.
        """
        root, dst, tree = _copy_pair(wall[0], tmp_path)
        before = _tree_bytes(root)
        monkeypatch.setattr(nif_converter, 'plan_pose_holds', _no_holds)
        monkeypatch.setattr(NifFormat.Data, 'write', _boom)
        result = nif_converter.convert_nif(str(_sample(_WALL)), str(dst))
        assert result['error'] == 'WR'
        assert _tree_bytes(root) == before
        assert HOLD_EVENT in _pool(tree / 'Behaviors' / 'Behavior00.hkx')

    def test_old_tree_leaves_before_the_nif_and_new_tree_arrives_after(
            self, wall, tmp_path, monkeypatch):
        """Set aside, replace the NIF, install: never both a NIF and a stale tree."""
        _root, dst, tree = _copy_pair(wall[0], tmp_path)
        steps = []
        _fail_os(monkeypatch, 'rename',
                 lambda src, new: steps.append(Path(new).name) and False)
        _fail_os(monkeypatch, 'replace',
                 lambda src, new: steps.append(Path(new).name) and False)
        result = nif_converter.convert_nif(str(_sample(_WALL)), str(dst))
        assert not result.get('error') and not result.get('animobject_error')
        wanted = ['wall_hkxaside', 'wall.nif', 'wall_behavior']
        assert [s for s in steps if s in wanted] == wanted
        assert sorted(p.name for p in dst.parent.iterdir()) == [
            'wall.nif', 'wall_behavior']
        assert HOLD_EVENT in _pool(tree / 'Behaviors' / 'Behavior00.hkx')

    def test_the_temp_nif_is_no_longer_than_the_nif(self, tmp_path, monkeypatch):
        """Same folder, same length, another name: no path that fitted stops fitting."""
        seen = []
        _fail_os(monkeypatch, 'replace',
                 lambda src, new: seen.append((src, new)) and False)
        dst = tmp_path / 'wall.nif'
        nif_converter.convert_nif(str(_sample(_WALL)), str(dst))
        (src, new), = [pair for pair in seen if pair[1] == str(dst)]
        assert Path(src).parent == dst.parent and src != new
        assert len(src) == len(new)
        assert [p.name for p in tmp_path.iterdir()] == ['wall.nif']

    @pytest.mark.parametrize('point', ['aside', 'replace'])
    def test_shrinking_holds_and_a_failed_step_keep_the_old_pair(
            self, wall, tmp_path, monkeypatch, point):
        """The rebuild has NO holds, the tree on disk has them, and a step fails.

        Failing to set the old tree aside, or to replace the NIF, must leave
        the old NIF beside the old tree, byte for byte, and nothing else.
        """
        root, dst, tree = _copy_pair(wall[0], tmp_path)
        before = _tree_bytes(root)
        monkeypatch.setattr(nif_converter, 'plan_pose_holds', _no_holds)
        if point == 'aside':
            _fail_os(monkeypatch, 'rename', lambda s, d: d.endswith('_hkxaside'))
        else:
            _fail_os(monkeypatch, 'replace', lambda s, d: d.endswith('wall.nif'))
        with pytest.raises(OSError, match='injected'):
            nif_converter.convert_nif(str(_sample(_WALL)), str(dst))
        assert _tree_bytes(root) == before
        assert not _crash_pair_on_disk(dst, tree)

    @pytest.mark.parametrize('copy_works', [True, False])
    def test_shrinking_holds_and_a_failed_install_never_keep_the_old_tree(
            self, wall, tmp_path, monkeypatch, copy_works):
        """The staged tree cannot be moved in: copy it, or leave NO tree and say so.

        The old hold-bearing tree must not come back beside the hold-less NIF.
        """
        _root, dst, tree = _copy_pair(wall[0], tmp_path)
        monkeypatch.setattr(nif_converter, 'plan_pose_holds', _no_holds)
        _fail_os(monkeypatch, 'rename', lambda s, d: s.endswith('_hkxstage'))
        if not copy_works:
            monkeypatch.setattr(shutil, 'copytree', _boom)
        result = nif_converter.convert_nif(str(_sample(_WALL)), str(dst))
        assert not _crash_pair_on_disk(dst, tree)
        assert sorted(_sequences(sse_nif.read_nif(str(dst)))) == [
            'Backward', 'Forward']
        if copy_works:
            assert not result.get('error')
            assert HOLD_EVENT not in _pool(tree / 'Behaviors' / 'Behavior00.hkx')
            assert len(_tree_bytes(tree)) == 4
        else:
            assert result['error'] == 'TREE' and result['animobject_error']
        assert sorted(p.name for p in dst.parent.iterdir()) == (
            ['wall.nif', 'wall_behavior'] if copy_works else ['wall.nif'])

    def test_a_rename_that_fails_once_is_retried(self, wall, tmp_path,
                                                 monkeypatch):
        """A scanner holding a file for a moment must not cost the tree."""
        _root, dst, tree = _copy_pair(wall[0], tmp_path)
        tries = []
        _fail_os(monkeypatch, 'rename', lambda s, d: (
            s.endswith('_hkxstage') and not tries and not tries.append(1)))
        monkeypatch.setattr(shutil, 'copytree', _boom)
        result = nif_converter.convert_nif(str(_sample(_WALL)), str(dst))
        assert tries and not result.get('error')
        assert len(_tree_bytes(tree)) == 4
        assert sorted(p.name for p in dst.parent.iterdir()) == [
            'wall.nif', 'wall_behavior']

    def test_a_set_aside_folder_that_cannot_be_cleared_stops_the_write(
            self, wall, tmp_path, monkeypatch):
        """The old tree has nowhere to go, so the NIF is not replaced either."""
        root, dst, _tree = _copy_pair(wall[0], tmp_path)
        (dst.parent / 'wall_hkxaside').mkdir()
        before = _tree_bytes(root)
        real = shutil.rmtree
        monkeypatch.setattr(shutil, 'rmtree', lambda path, *a, **k: (
            None if str(path).endswith('_hkxaside') else real(path, *a, **k)))
        with pytest.raises(OSError, match='cannot be cleared'):
            nif_converter.convert_nif(str(_sample(_WALL)), str(dst))
        assert _tree_bytes(root) == before
        assert not (dst.parent / 'wall_hkxstage').exists()

    def test_an_interrupt_at_the_nif_replace_does_not_restore_the_old_tree(
            self, wall, tmp_path, monkeypatch):
        """Ctrl-C may land after the replace: putting the tree back would be the crash pair."""
        _root, dst, tree = _copy_pair(wall[0], tmp_path)
        monkeypatch.setattr(nif_converter, 'plan_pose_holds', _no_holds)
        _fail_os(monkeypatch, 'replace', lambda s, d: d.endswith('wall.nif'),
                 KeyboardInterrupt)
        with pytest.raises(KeyboardInterrupt):
            nif_converter.convert_nif(str(_sample(_WALL)), str(dst))
        assert not tree.exists() and not _crash_pair_on_disk(dst, tree)
        assert sorted(p.name for p in dst.parent.iterdir()) == [
            'wall.nif', 'wall_hkxaside']

    def test_the_crash_pair_check_can_see_a_crash_pair(self, wall, holdless_wall,
                                                       tmp_path):
        """Positive control for the tests above."""
        _root, dst, tree = _crash_pair(wall, holdless_wall, tmp_path)
        assert _crash_pair_on_disk(dst, tree)

    @pytest.mark.parametrize('where', ['harvest', 'apply', 'makedirs'])
    def test_nothing_staged_outlives_a_failure_before_the_write(
            self, wall, tmp_path, monkeypatch, where):
        """An exception or interrupt between staging and the write leaks no folder."""
        root, dst, tree = _copy_pair(wall[0], tmp_path)
        before = _tree_bytes(root)
        if where == 'harvest':
            monkeypatch.setattr(nif_converter, '_harvest_textures', _boom)
        elif where == 'apply':
            monkeypatch.setattr(nif_converter, 'apply_pose_holds', _interrupt)
        else:
            real = os.makedirs
            monkeypatch.setattr(os, 'makedirs', lambda path, *a, **k: (
                _boom() if str(path) == str(dst.parent) else real(path, *a, **k)))
        with pytest.raises((OSError, KeyboardInterrupt)):
            nif_converter.convert_nif(str(_sample(_WALL)), str(dst))
        assert _tree_bytes(root) == before
        assert not _crash_pair_on_disk(dst, tree)

    def test_a_staging_folder_that_cannot_be_cleared_fails_the_graph(
            self, tmp_path, monkeypatch):
        """A stale hkx there could pass for this build's output, so refuse."""
        stale = tmp_path / 'tes4' / 'a' / 'wall_hkxstage'
        stale.mkdir(parents=True)
        real = shutil.rmtree
        monkeypatch.setattr(shutil, 'rmtree', lambda path, *a, **k: (
            None if str(path).endswith('_hkxstage') else real(path, *a, **k)))
        with pytest.raises(RuntimeError, match='cannot be cleared'):
            stage_animobject_project(str(tmp_path), 'tes4/a/wall.nif', ['Forward'],
                                     loops=())
        assert not list(tmp_path.rglob('*.hkx'))

    def test_a_planning_failure_costs_the_holds_not_the_graph(
            self, tmp_path, monkeypatch):
        """Unexpected planner error: the pre-hold graph, a BGED, and a report."""

        def broken(_data, _seq_names):
            """A planner bug, not a refusal."""
            raise RuntimeError('plan broke')

        monkeypatch.setattr(nif_converter, 'plan_pose_holds', broken)
        result, dst, tree = _build_wall(tmp_path)
        assert result['hold_plan_error'] == 'plan broke'
        assert not result.get('animobject_error') and not result.get('error')
        assert _has_bged(dst)
        assert sorted(_sequences(sse_nif.read_nif(str(dst)))) == [
            'Backward', 'Forward']
        assert hkx_animobject.compiled_generators(
            str(tree / 'Behaviors' / 'Behavior00.hkx')) == graph_generators(
                ['Forward', 'Backward'])
        assert not [p for p in _pool(tree / 'Behaviors' / 'Behavior00.hkx')
                    if p.startswith(('ModifierGenerator', 'bIsActive'))]

    def test_holds_that_did_not_register_fail_the_graph(self, tmp_path,
                                                        monkeypatch):
        """A graph naming holds the NIF did not get must not be installed."""
        monkeypatch.setattr(nif_converter, 'apply_pose_holds', lambda planned: {})
        result, dst, tree = _build_wall(tmp_path)
        assert result['animobject_error'].startswith('holds not registered')
        assert not _has_bged(dst) and not tree.exists()
        assert sorted(p.name for p in dst.parent.iterdir()) == ['wall.nif']

    def test_failed_graph_ships_an_inert_mesh_and_no_stale_tree(
            self, wall, tmp_path, monkeypatch):
        """No hold, no BGED, the old tree gone, and the error in the result."""
        root, dst, tree = _copy_pair(wall[0], tmp_path)
        monkeypatch.setattr(nif_converter, 'stage_animobject_project', _boom)
        result = nif_converter.convert_nif(str(_sample(_WALL)), str(dst))
        assert result['animobject_error'] == 'boom' and result['converted']
        data = sse_nif.read_nif(str(dst))
        assert sorted(_sequences(data)) == ['Backward', 'Forward']
        assert not [e for e in data.roots[0].extra_data_list
                    if isinstance(e, NifFormat.BSBehaviorGraphExtraData)]
        assert not tree.exists()
        assert not list(root.rglob('*.hkx'))

    @pytest.mark.parametrize('tag, converted', [('GRAPH', 1), ('HOLDS', 1),
                                                ('TREE', 0)])
    def test_graph_failures_reach_the_batch_error_list(self, tmp_path, monkeypatch,
                                                       capsys, tag, converted):
        """Each failure has a reader: one error, listed under its own tag.

        GRAPH: no graph, no BGED.  HOLDS: graph without holds.  TREE: the NIF
        names a tree that could not be installed, so it is NOT counted converted.
        """
        src = tmp_path / 'src' / 'dungeons'
        src.mkdir(parents=True)
        shutil.copy(_sample(_WALL), src / 'wall.nif')
        monkeypatch.setattr(nif_batch, 'WORKER_COUNT', 1)
        if tag == 'GRAPH':
            monkeypatch.setattr(nif_converter, 'stage_animobject_project', _boom)
        elif tag == 'HOLDS':
            monkeypatch.setattr(nif_converter, 'plan_pose_holds', _boom)
        else:
            _fail_os(monkeypatch, 'rename', lambda s, d: s.endswith('_hkxstage'))
            monkeypatch.setattr(shutil, 'copytree', _boom)
        stats = nif_batch.batch_convert(
            str(tmp_path / 'src'), str(tmp_path / 'out' / 'meshes' / 'tes4'))
        assert (stats['converted'], stats['errors']) == (converted, 1)
        out = capsys.readouterr().out.replace(os.sep, '/')
        assert re.search(r'\[%s[^\]]*\] dungeons/wall.nif' % tag, out)
        assert 'TREE=BGED names a tree that could not be installed' in out

    def test_failed_compile_leaves_no_partial_tree(self, tmp_path, monkeypatch):
        """hkxcmd dying on the third file: nothing staged, nothing final."""
        real, calls = hkx_animobject.compile_hkx, []

        def flaky(xml_path, hkx_path):
            """Compile the first two files, then fail."""
            calls.append(hkx_path)
            if len(calls) == 3:
                raise RuntimeError('hkxcmd failed')
            real(xml_path, hkx_path)

        monkeypatch.setattr(hkx_animobject, 'compile_hkx', flaky)
        with pytest.raises(RuntimeError, match='hkxcmd failed'):
            generate_animobject_project(str(tmp_path), 'tes4/a/wall.nif',
                                        ['Forward'], {'Forward': 'ForwardHold'},
                                        loops=())
        assert len(calls) == 3
        assert not [p for p in tmp_path.rglob('*') if p.is_file()]
        assert [p.name for p in (tmp_path / 'tes4' / 'a').iterdir()] == []

    def test_a_compiled_graph_missing_its_holds_is_rejected(self, tmp_path,
                                                            monkeypatch):
        """hkxcmd exits 0 on a dangling reference, so the result is inspected."""
        real = hkx_animobject.behavior_xml
        monkeypatch.setattr(hkx_animobject, 'behavior_xml',
                            lambda name, seqs, *_a: real(name, seqs))
        with pytest.raises(RuntimeError, match='GamebryoSequenceGeneratorHold00'):
            stage_animobject_project(str(tmp_path), 'tes4/a/wall.nif',
                                     ['Forward'], {'Forward': 'ForwardHold'},
                                     loops=())
        assert not [p for p in tmp_path.rglob('*') if p.is_file()]

    @pytest.mark.parametrize('kind', sorted(_WRONG_GRAPHS) + ['holds swapped'])
    def test_a_compiled_generator_playing_the_wrong_thing_is_rejected(
            self, tmp_path, monkeypatch, kind):
        """Every name is still in the string pool; only the pairing is wrong."""
        monkeypatch.setattr(hkx_animobject, 'behavior_xml',
                            lambda *_a, **_k: _wrong_graph_xml(kind))
        with pytest.raises(RuntimeError, match='plays'):
            stage_animobject_project(str(tmp_path), 'tes4/a/wall.nif',
                                     ['Forward', 'Backward'], _BOTH, loops=())
        assert not [p for p in tmp_path.rglob('*') if p.is_file()]

    def test_a_hold_for_an_unknown_sequence_is_ignored_when_compiling(
            self, tmp_path):
        """As `behavior_xml` documents: no hold state, no End, and no failure."""
        generate_animobject_project(str(tmp_path), 'tes4/a/wall.nif',
                                    ['Forward'], {'Left': 'LeftHold'}, loops=())
        graph = tmp_path / 'tes4' / 'a' / 'wall_behavior' / 'Behaviors' / 'Behavior00.hkx'
        assert hkx_animobject.compiled_generators(str(graph)) == \
            graph_generators(['Forward'])
        assert HOLD_EVENT not in _pool(graph)


# ---------------------------------------------------------------------------
# The validator
# ---------------------------------------------------------------------------


def _audit(root, capsys, *extra):
    """(exit code, printed summary line) of the validator on `root`."""
    code = gamebryo_seq_check.main([str(root), '--quiet', *extra])
    lines = capsys.readouterr().out.splitlines()
    return code, next(l for l in lines if l.startswith('behavior projects'))


def _audit_text(root, capsys, *extra):
    """(exit code, everything the validator printed) on `root`."""
    code = gamebryo_seq_check.main([str(root), '--quiet', *extra])
    return code, capsys.readouterr().out


def _crash_pair(wall, holdless_wall, tmp_path):
    """A hold-bearing tree beside a hold-less NIF: (meshes root, NIF, tree)."""
    root, dst, tree = _copy_pair(wall[0], tmp_path)
    shutil.copy(holdless_wall[1], dst)
    return root, dst, tree


class TestGamebryoSeqCheck:
    """The validator must SEE the crash pair in every on-disk layout."""

    def test_clean_pair_passes_with_counts(self, pair, capsys):
        """One project, one End graph, two holds, no violation: exit 0."""
        assert _audit(pair[0], capsys) == (0, (
            'behavior projects checked: 1   graphs declaring End: 1   '
            'hold sequences: 2   declaring bAnimPlaying: 1   wrapped states: 2   '
            'legacy graphs: 0   violations: 0'))

    def test_hold_less_graph_counts_no_end(self, holdless_pair, capsys):
        """The pre-hold shape is still clean, and reports zero End graphs."""
        assert _audit(holdless_pair[0], capsys) == (0, (
            'behavior projects checked: 1   graphs declaring End: 0   '
            'hold sequences: 0   declaring bAnimPlaying: 1   wrapped states: 0   '
            'legacy graphs: 0   violations: 0'))

    def test_crash_pair_is_flagged(self, pair, holdless_pair, tmp_path, capsys):
        """A graph naming holds the NIF lacks is a violation."""
        root, _dst, _tree = _crash_pair(pair, holdless_pair, tmp_path)
        code, summary = _audit(root, capsys)
        assert code == 1 and summary.endswith('violations: 1')

    def test_uppercase_nif_extension_is_paired(self, pair, holdless_pair,
                                               tmp_path, capsys):
        """`wall.NIF` was skipped: the crash pair read "checked: 0", exit 0."""
        root, dst, _tree = _crash_pair(pair, holdless_pair, tmp_path)
        dst.rename(dst.with_suffix('.NIF'))
        code, summary = _audit(root, capsys)
        assert code == 1
        assert summary.startswith('behavior projects checked: 1 ')
        assert summary.endswith('violations: 1')

    def test_lowercase_tree_members_are_paired(self, pair, holdless_pair,
                                               tmp_path, capsys):
        """A BSA extract or loose deploy lowercases `behaviors/behavior00.hkx`."""
        root, _dst, tree = _crash_pair(pair, holdless_pair, tmp_path)
        (tree / 'Behaviors' / 'Behavior00.hkx').rename(
            tree / 'Behaviors' / 'behavior00.hkx')
        (tree / 'Behaviors').rename(tree / 'behaviors')
        code, summary = _audit(root, capsys)
        assert code == 1
        assert summary.startswith('behavior projects checked: 1 ')
        assert summary.endswith('violations: 1')

    def test_renamed_clean_pair_still_passes(self, pair, tmp_path, capsys):
        """Case-insensitive pairing finds the pair and finds it clean."""
        root, dst, tree = _copy_pair(pair[0], tmp_path)
        dst.rename(dst.with_suffix('.NIF'))
        (tree / 'Behaviors' / 'Behavior00.hkx').rename(
            tree / 'Behaviors' / 'behavior00.hkx')
        (tree / 'Behaviors').rename(tree / 'behaviors')
        assert _audit(root, capsys) == (0, (
            'behavior projects checked: 1   graphs declaring End: 1   '
            'hold sequences: 2   declaring bAnimPlaying: 1   wrapped states: 2   '
            'legacy graphs: 0   violations: 0'))

    def test_unreadable_nif_is_a_violation(self, pair, tmp_path, capsys):
        """It used to count as checked-and-clean."""
        root, dst, _tree = _copy_pair(pair[0], tmp_path)
        dst.write_bytes(b'not a nif')
        code, out = _audit_text(root, capsys)
        assert code == 1 and f'NIF cannot be read: {dst}: not a NIF' in out

    @pytest.mark.parametrize('missing', [
        'wall.nif', 'wall_behavior/Behaviors/Behavior00.hkx',
        'wall_behavior/Characters/Character01.hkx',
        'wall_behavior/CharacterAssets/Skeleton.hkx', 'wall_behavior/wall.hkx'])
    def test_unpaired_or_incomplete_tree_is_a_violation(self, pair, tmp_path,
                                                        capsys, missing):
        """A tree with no NIF, or missing any of its four files, is not clean."""
        root, dst, _tree = _copy_pair(pair[0], tmp_path)
        (dst.parent / missing).unlink()
        code, summary = _audit(root, capsys)
        assert code == 1
        assert summary.startswith('behavior projects checked: 1 ')
        assert not summary.endswith('violations: 0')

    @pytest.mark.parametrize('damage', ['empty file', 'no Rest generator'])
    def test_a_graph_nothing_was_read_from_is_a_violation(self, pair, tmp_path,
                                                          capsys, damage):
        """Zero generators, or none named Rest, used to report "ok"."""
        root, _dst, tree = _copy_pair(pair[0], tmp_path)
        graph = tree / 'Behaviors' / 'Behavior00.hkx'
        graph.write_bytes(b'' if damage == 'empty file' else
                          graph.read_bytes().replace(b'GeneratorRest', b'GeneratorXest'))
        code, out = _audit_text(root, capsys)
        assert code == 1 and 'no Rest generator read' in out

    @pytest.mark.parametrize('leftover', ['wall_hkxstage', 'wall_hkxaside',
                                          'other.ni~'])
    def test_leftovers_of_an_interrupted_build_are_violations(
            self, pair, tmp_path, capsys, leftover):
        """They get packed like any file, and no tree walk would notice them."""
        root, dst, _tree = _copy_pair(pair[0], tmp_path)
        if leftover.endswith('~'):
            (dst.parent / leftover).write_bytes(b'half')
        else:
            (dst.parent / leftover).mkdir()
        code, out = _audit_text(root, capsys)
        assert code == 1 and 'violations: 1' in out and 'leftover' in out

    def test_a_bged_naming_a_missing_project_is_a_violation(
            self, pair, tmp_path, capsys, monkeypatch):
        """A NIF that names no tree on disk is not drawn; a healthy neighbour hid it."""
        root, dst, _tree = _copy_pair(pair[0], tmp_path)
        assert _audit(root, capsys)[0] == 0
        monkeypatch.setattr(
            nif_converter, 'stage_animobject_project',
            lambda *_a, **_k: hkx_animobject.StagedProject(
                'tes4\\dungeons\\chargen\\ghost_behavior\\ghost.hkx', None, None))
        nif_converter.convert_nif(str(_sample(_WALL)),
                                  str(dst.parent / 'ghost.nif'))
        code, out = _audit_text(root, capsys)
        assert code == 1 and 'violations: 1' in out
        assert 'ghost.hkx' in out and 'does not exist' in out
        assert 'NIFs read for a BGED: 2   naming a generated project: 2' in out

    def test_matching_counts_never_excuse_a_violation(self, pair, holdless_pair,
                                                      tmp_path, capsys):
        """The crash pair has the right counts; the exit code must still be 1."""
        root, _dst, _tree = _crash_pair(pair, holdless_pair, tmp_path)
        code, summary = _audit(root, capsys, '--expect-checked', '1',
                               '--expect-end-graphs', '1', '--expect-holds', '2')
        assert code == 1 and 'hold sequences: 2 ' in summary
        assert summary.endswith('violations: 1')

    @pytest.mark.parametrize('kind, message', [
        ('wrapper dropped', 'wrapped states [1], expected [0, 1]'),
        ('wrapper on the hold',
         'ModifierGenerator00 does not wrap GamebryoSequenceGenerator00'),
        ('two modifiers', '2 bIsActive0 binding(s) for 2 wrapped state(s)'),
        ('member typo', '0 bIsActive0 binding(s) for 2 wrapped state(s)'),
    ])
    def test_a_graph_writing_the_variable_in_the_wrong_states_is_a_violation(
            self, pair, tmp_path, capsys, kind, message):
        """Expected set: held (this graph's hold generators) or LOOP (the NIF)."""
        root, _dst, tree = _copy_pair(pair[0], tmp_path)
        _compile_graph(_wrong_playing_xml(kind),
                       tree / 'Behaviors' / 'Behavior00.hkx')
        code, out = _audit_text(root, capsys)
        assert code == 1 and message in out

    def test_a_loop_the_emitter_was_not_told_about_is_a_violation(
            self, tmp_path, monkeypatch, capsys):
        """Only this check reads the LOOP set from the NIF, in its own code.

        With the emitter's LOOP input emptied the graph is self-consistent and
        the compiled check agrees with it; the looping state reads 0 for ever.
        """
        dst = tmp_path / 'meshes' / 'tes4' / 'hand.nif'
        nif_converter.convert_nif(str(_sample(_LOOPING)), str(dst))
        assert _audit(tmp_path, capsys) == (0, (
            'behavior projects checked: 1   graphs declaring End: 0   '
            'hold sequences: 0   declaring bAnimPlaying: 1   wrapped states: 1   '
            'legacy graphs: 0   violations: 0'))
        monkeypatch.setattr(nif_converter, 'loop_sequences', lambda *_a: set())
        result = nif_converter.convert_nif(str(_sample(_LOOPING)), str(dst))
        assert not result.get('animobject_error')
        code, out = _audit_text(tmp_path, capsys)
        assert code == 1 and 'wrapped states [], expected [0] (held or LOOP)' in out

    def test_the_right_count_on_the_wrong_states_is_a_violation(self, tmp_path,
                                                                 capsys):
        """A set keyed by state id, not a count: two wrapped, but not the two.

        The graph wraps the hold-less CLAMP start state and leaves the LOOP bare.
        """
        root, _dst, _tree = _project(tmp_path, _START_SHOT_LOOP,
                                     graph_loops=('SpecialIdle',))
        code, out = _audit_text(root, capsys)
        assert code == 1
        assert 'wrapped states [0, 1], expected [1, 2] (held or LOOP)' in out

    @pytest.mark.parametrize('sequences', [
        [('Forward', 0, False), ('Forward', 2, True), ('Backward', 2, True)],
        [('Forward', 2, True), ('Backward', 2, True), ('Forward', 0, True)],
    ])
    def test_the_validator_reads_the_sequence_the_graph_plays(
            self, tmp_path, capsys, sequences):
        """First of each name with controlled blocks: an empty LOOP of the same
        name before it, or a filled one after it, does not make `Forward` loop.

        Built without holds, so nothing but a LOOP could earn a wrapper.
        """
        root, _dst, _tree = _project(tmp_path, sequences, holds=False)
        assert _audit(root, capsys) == (0, (
            'behavior projects checked: 1   graphs declaring End: 0   '
            'hold sequences: 0   declaring bAnimPlaying: 1   wrapped states: 0   '
            'legacy graphs: 0   violations: 0'))

    def test_a_legacy_graph_with_holds_is_valid_and_refusable(self, pair, tmp_path,
                                                              capsys):
        """The shape of nearly every tree built before the variable: held, no wrapper."""
        root, _dst, tree = _copy_pair(pair[0], tmp_path)
        _compile_graph(_legacy_held_xml(), tree / 'Behaviors' / 'Behavior00.hkx')
        assert _audit(root, capsys) == (0, (
            'behavior projects checked: 1   graphs declaring End: 1   '
            'hold sequences: 2   declaring bAnimPlaying: 0   wrapped states: 0   '
            'legacy graphs: 1   violations: 0'))
        assert gamebryo_seq_check.build_gate(str(root))['violations'] == 0
        assert _audit(root, capsys, '--require-playing-variable')[0] == 1

    @pytest.mark.parametrize('legacy', [False, True])
    @pytest.mark.parametrize('gate', [False, True])
    def test_a_hold_generator_without_a_state_id_gets_a_verdict(
            self, pair, tmp_path, capsys, legacy, gate):
        """`…HoldXX` is free text to the engine: no traceback, whatever the mode.

        A legacy graph ignores it, as before; a graph that declares the
        variable cannot have its wrapped set judged, so it is a problem there.
        """
        root, _dst, tree = _copy_pair(pair[0], tmp_path)
        graph = tree / 'Behaviors' / 'Behavior00.hkx'
        if legacy:
            _compile_graph(_legacy_held_xml(), graph)
        blob = graph.read_bytes()
        assert blob.count(b'GamebryoSequenceGeneratorHold00') == 1
        graph.write_bytes(blob.replace(b'GamebryoSequenceGeneratorHold00',
                                       b'GamebryoSequenceGeneratorHoldXX'))
        code, out = _audit_text(root, capsys, *(['--build-gate'] * gate))
        assert code == (0 if legacy else 1)
        assert ('GamebryoSequenceGeneratorHoldXX carries no state id' in out) is (
            not legacy)

    @pytest.mark.parametrize('gate', [False, True])
    def test_a_graph_cut_inside_a_hold_name_gets_a_verdict(self, pair, tmp_path,
                                                           capsys, gate):
        """A damaged file is a violation line, never an exception."""
        root, _dst, tree = _copy_pair(pair[0], tmp_path)
        graph = tree / 'Behaviors' / 'Behavior00.hkx'
        blob = graph.read_bytes()
        cut = blob.index(b'GamebryoSequenceGeneratorHold00') + len(
            b'GamebryoSequenceGeneratorHold')
        graph.write_bytes(blob[:cut])
        code, out = _audit_text(root, capsys, *(['--build-gate'] * gate))
        assert code == 1 and 'violations: 1' in out

    def test_a_wrapper_under_an_unknown_name_is_a_violation(self, pair, tmp_path,
                                                            capsys):
        """Wrappers are found by name; `ModifierGeneratorRest` must not vanish."""
        root, _dst, tree = _copy_pair(pair[0], tmp_path)
        _compile_graph(_wrong_playing_xml('Rest wrapped under another name'),
                       tree / 'Behaviors' / 'Behavior00.hkx')
        code, out = _audit_text(root, capsys)
        assert code == 1
        assert 'ModifierGeneratorRest is not a wrapper name' in out

    @pytest.mark.parametrize('stem', ['ModifierGenerator01', 'bIsActive0'])
    def test_a_model_named_like_a_graph_node_passes(self, tmp_path, capsys, stem):
        """What the emitter builds the gate must accept, lowercased folder included."""
        root, _dst, tree = _project(tmp_path, _PAIR, stem=stem)
        tree.rename(tree.with_name(tree.name.lower()))
        code, out = _audit_text(root, capsys)
        assert code == 0 and 'wrapped states: 2   legacy graphs: 0' in out
        assert gamebryo_seq_check.build_gate(str(root))['violations'] == 0

    def test_a_model_named_like_the_variable_does_not_declare_it(self, tmp_path,
                                                                 capsys):
        """A legacy graph of a model called `bAnimPlaying` is still legacy."""
        root, _dst, tree = _project(tmp_path, _PAIR, stem='bAnimPlaying',
                                    holds=False)
        _compile_graph(
            _legacy_xml(behavior_xml('bAnimPlaying', ['Forward', 'Backward'])),
            tree / 'Behaviors' / 'Behavior00.hkx')
        code, out = _audit_text(root, capsys)
        assert code == 0
        assert 'declaring bAnimPlaying: 0   wrapped states: 0   legacy graphs: 1' in out

    def test_a_model_named_end_does_not_declare_the_event(self, tmp_path, capsys):
        """A hold-less graph of a model called `End` declares no End."""
        root, _dst, _tree = _project(tmp_path, _PAIR, stem='End', holds=False)
        code, out = _audit_text(root, capsys)
        assert code == 0 and 'graphs declaring End: 0 ' in out

    def test_wrappers_without_the_variable_are_not_counted_as_legacy(
            self, pair, tmp_path, capsys):
        """A violation is not a legacy graph."""
        root, _dst, tree = _copy_pair(pair[0], tmp_path)
        _compile_graph(
            _legacy_xml(behavior_xml('wall', ['Forward', 'Backward'], _BOTH)),
            tree / 'Behaviors' / 'Behavior00.hkx')
        code, out = _audit_text(root, capsys)
        assert code == 1
        assert 'declaring bAnimPlaying: 0   wrapped states: 0   legacy graphs: 0' in out

    def test_a_reverse_sequence_is_not_a_loop_to_the_validator(self, tmp_path,
                                                               capsys):
        """Cycle type 1 finishes; a graph that wraps it as a loop is wrong."""
        dst = tmp_path / 'meshes' / 'tes4' / 'hand.nif'
        nif_converter.convert_nif(str(_sample(_LOOPING)), str(dst))
        data = NifFormat.Data()
        with open(dst, 'rb') as stream:
            data.read(stream)
        _sequences(data)['Forward'].cycle_type = 1
        with open(dst, 'wb') as stream:
            data.write(stream)
        code, out = _audit_text(tmp_path, capsys)
        assert code == 1 and 'wrapped states [0], expected [] (held or LOOP)' in out

    def test_a_legacy_graph_is_valid_counted_and_refusable(self, holdless_pair,
                                                           tmp_path, capsys):
        """No variable: a tree built before bAnimPlaying existed, not a violation.

        `--require-playing-variable` makes it one, for the acceptance run of a
        full rebuild; the build gate prints the count.
        """
        root, _dst, tree = _copy_pair(holdless_pair[0], tmp_path)
        _compile_graph(_legacy_xml(behavior_xml('wall', ['Forward', 'Backward'])),
                       tree / 'Behaviors' / 'Behavior00.hkx')
        assert _audit(root, capsys) == (0, (
            'behavior projects checked: 1   graphs declaring End: 0   '
            'hold sequences: 0   declaring bAnimPlaying: 0   wrapped states: 0   '
            'legacy graphs: 1   violations: 0'))
        code, out = _audit_text(root, capsys, '--require-playing-variable')
        assert code == 1 and 'legacy graph: bAnimPlaying is not declared' in out
        totals = gamebryo_seq_check.build_gate(str(root))
        assert (totals['legacy'], totals['violations']) == (1, 0)
        assert 'legacy graphs: 1 ' in capsys.readouterr().out

    def test_wrappers_without_the_variable_are_a_violation(self, pair, tmp_path,
                                                           capsys):
        """Not a legacy graph: something writes a variable that is not there."""
        root, _dst, tree = _copy_pair(pair[0], tmp_path)
        _compile_graph(
            _legacy_xml(behavior_xml('wall', ['Forward', 'Backward'], _BOTH)),
            tree / 'Behaviors' / 'Behavior00.hkx')
        code, out = _audit_text(root, capsys)
        assert code == 1 and 'wrapped states but no bAnimPlaying variable' in out

    def test_the_new_counts_can_be_expected(self, pair, capsys):
        """`--expect-anim-var`, `--expect-wrapped` and `--expect-legacy` for hand runs."""
        good = ['--expect-anim-var', '1', '--expect-wrapped', '2',
                '--expect-legacy', '0']
        assert _audit(pair[0], capsys, *good)[0] == 0
        for flag in ('--expect-anim-var', '--expect-wrapped', '--expect-legacy'):
            assert _audit(pair[0], capsys, flag, '7')[0] == 1

    def test_hold_generators_without_an_end_event_are_a_violation(
            self, pair, tmp_path, capsys):
        """Hold states nothing can reach: the pool has holds but no `End`."""
        root, _dst, tree = _copy_pair(pair[0], tmp_path)
        graph = tree / 'Behaviors' / 'Behavior00.hkx'
        blob = graph.read_bytes()
        assert blob.count(b'\x00End\x00') == 1
        graph.write_bytes(blob.replace(b'\x00End\x00', b'\x00Enx\x00'))
        code, out = _audit_text(root, capsys)
        assert code == 1 and 'no End event to reach them' in out

    @pytest.mark.parametrize('kind, message', [
        ('Rest plays a sequence', 'plays on load'),
        ('sequence plays nothing', 'EMPTY pSequence'),
        ('hold plays nothing', 'EMPTY pSequence'),
    ])
    def test_generator_rules_fire_on_a_compiled_graph(self, pair, tmp_path,
                                                      capsys, kind, message):
        """Rest must play nothing; every other generator must play something."""
        root, _dst, tree = _copy_pair(pair[0], tmp_path)
        _compile_graph(_wrong_graph_xml(kind),
                       tree / 'Behaviors' / 'Behavior00.hkx')
        code, out = _audit_text(root, capsys)
        assert code == 1 and message in out

    def test_an_empty_text_key_beside_a_graph_is_a_violation(self, pair,
                                                             tmp_path, capsys):
        """The generator strchr()s every key value; an empty one is a NULL string."""
        root, dst, _tree = _copy_pair(pair[0], tmp_path)
        data = NifFormat.Data()
        with open(dst, 'rb') as stream:
            data.read(stream)
        _sequences(data)['Forward'].text_keys.text_keys[1].value = b''
        with open(dst, 'wb') as stream:
            data.write(stream)
        code, out = _audit_text(root, capsys)
        assert code == 1 and 'EMPTY text key value' in out

    def test_two_nifs_differing_by_case_are_ambiguous(self, pair, tmp_path,
                                                      capsys):
        """The engine sees one name; which file it loads is not knowable here."""
        require_case_twins(tmp_path)
        root, dst, _tree = _copy_pair(pair[0], tmp_path)
        shutil.copy(dst, dst.with_suffix('.NIF'))
        code, summary = _audit(root, capsys)
        assert code == 1 and summary.endswith('violations: 1')

    def test_nothing_checked_is_a_failure(self, tmp_path, capsys):
        """A run that inspected no project proves nothing."""
        code, summary = _audit(tmp_path, capsys)
        assert code == 1
        assert summary.startswith('behavior projects checked: 0 ')

    def test_expected_counts_are_enforced(self, pair, capsys):
        """Matching `--expect-*` counts pass; any mismatch fails a clean tree."""
        good = ['--expect-checked', '1', '--expect-end-graphs', '1',
                '--expect-holds', '2']
        assert _audit(pair[0], capsys, *good)[0] == 0
        for flag in ('--expect-checked', '--expect-end-graphs', '--expect-holds'):
            assert _audit(pair[0], capsys, flag, '7')[0] == 1


# ---------------------------------------------------------------------------
# The validator as the build's own gate
# ---------------------------------------------------------------------------


def _plugin_output(src_root, tmp_path):
    """`src_root`'s meshes as the built tree of Oblivion.esm: (output dir, NIF folder)."""
    plugin = tmp_path / 'output' / 'Oblivion.esm'
    shutil.copytree(Path(src_root) / 'meshes', plugin / 'meshes')
    return tmp_path / 'output', _wall_dst(plugin).parent


def _pack(monkeypatch, tmp_path, output):
    """(results, {archive: staged files}) of a BSA pack with BSArch stubbed out."""
    staged = {}

    def run(_exe, stage_root, bsa_path, _compress, results):
        """Record what would be packed instead of packing it."""
        staged[bsa_path.name] = sorted(
            p.relative_to(stage_root).as_posix()
            for p in Path(stage_root).rglob('*') if p.is_file())
        results['packed'].append(str(bsa_path))
        return True

    monkeypatch.setattr(bsa_pack, '_run_bsarch', run)
    exe = tmp_path / 'BSArch.exe'
    exe.write_bytes(b'')
    return bsa_pack.pack_bsas('Oblivion.esm', output_dir=str(output),
                              bsarch_path=str(exe)), staged


class TestBuildGate:
    """The build runs the validator itself: violations == 0, or nothing ships."""

    def test_clean_tree_passes(self, pair, capsys):
        """One project, named by its NIF, nothing wrong."""
        totals = gamebryo_seq_check.build_gate(str(pair[0]))
        assert (totals['checked'], totals['violations']) == (1, 0)
        assert 'violations: 0' in capsys.readouterr().out

    def test_crash_pair_fails_and_names_the_path(self, pair, holdless_pair,
                                                 tmp_path, capsys):
        """A graph naming holds its NIF lacks: one violation, the mesh printed."""
        root, dst, _tree = _crash_pair(pair, holdless_pair, tmp_path)
        assert gamebryo_seq_check.build_gate(str(root))['violations'] == 1
        out = capsys.readouterr().out
        assert f'BAD {dst.with_suffix("")}' in out and "'ForwardHold'" in out

    def test_a_recased_project_is_still_named(self, tmp_path):
        """The BGED keeps its author's case; a loose deploy lowercases the files.

        Matching the name case-sensitively would call the tree unnamed and
        skip it, crash pair or not.
        """
        dst = tmp_path / 'meshes' / 'tes4' / 'Chargen' / 'Wall.NIF'
        nif_converter.convert_nif(str(_sample(_WALL)), str(dst))
        (dst.parent / 'Wall_behavior').rename(dst.parent / 'wall_behavior')
        dst.rename(dst.parent / 'wall.nif')
        totals = gamebryo_seq_check.build_gate(str(tmp_path))
        assert (totals['checked'], totals['unnamed'], totals['violations']) == (1, 0, 0)

    @pytest.mark.parametrize('size', [0, 9, 300])
    def test_a_nif_whose_header_cannot_be_read_fails(self, pair, tmp_path,
                                                     capsys, size):
        """Empty, not a NIF, or cut off inside its header: refused, never skipped.

        Such a NIF names no tree, so the tree beside it reads as unnamed; the
        NIF itself must be the violation or a real pair is passed unread.
        """
        root, dst, _tree = _copy_pair(pair[0], tmp_path)
        dst.write_bytes(dst.read_bytes()[:size])
        totals = gamebryo_seq_check.build_gate(str(root))
        assert totals['violations'] == 1
        assert gamebryo_seq_check.main([str(root), '--build-gate']) == 1
        assert f'NIF cannot be read: {dst}' in capsys.readouterr().out

    def test_a_nif_cut_off_after_its_header_fails_without_a_traceback(
            self, pair, tmp_path, capsys, caplog):
        """pyffi logs a traceback for the broken block; the gate prints one line."""
        root, dst, _tree = _copy_pair(pair[0], tmp_path)
        dst.write_bytes(dst.read_bytes()[:-200])
        assert gamebryo_seq_check.build_gate(str(root))['violations'] == 1
        assert f'NIF cannot be read: {dst}' in capsys.readouterr().out
        assert not [r for r in caplog.records if r.exc_info]

    def test_a_nif_of_another_version_is_read_whole_and_passes(self, tmp_path):
        """Only a 20.2.0.7 header has the string table; any other NIF is searched."""
        (tmp_path / 'old.nif').write_bytes(NIF_STUB)
        totals = gamebryo_seq_check.build_gate(str(tmp_path))
        assert (totals['nifs'], totals['violations']) == (1, 0)

    @pytest.mark.parametrize('kind', ['tree with no NIF', 'empty tree'])
    def test_trees_with_nothing_to_read_still_pass(self, pair, tmp_path, kind):
        """No NIF at all is not an unreadable NIF."""
        root, dst, _tree = _copy_pair(pair[0], tmp_path)
        if kind == 'empty tree':
            shutil.rmtree(root / 'meshes')
            (root / 'meshes').mkdir()
        else:
            dst.unlink()
        totals = gamebryo_seq_check.build_gate(str(root))
        assert (totals['checked'], totals['violations']) == (0, 0)

    def test_leftover_staging_folder_fails(self, pair, tmp_path, capsys):
        """An interrupted mesh run must not be packed."""
        root, dst, _tree = _copy_pair(pair[0], tmp_path)
        (dst.parent / 'wall_hkxstage').mkdir()
        assert gamebryo_seq_check.build_gate(str(root))['violations'] == 1
        assert 'wall_hkxstage' in capsys.readouterr().out

    def test_tree_with_no_animated_object_passes(self, tmp_path, capsys):
        """A hand run fails "nothing checked"; the build must not."""
        nif_converter.convert_nif(str(_sample(_WALL)),
                                  str(tmp_path / 'plain' / 'wall.nif'))
        (tmp_path / 'textures').mkdir()
        totals = gamebryo_seq_check.build_gate(str(tmp_path))
        assert (totals['checked'], totals['nifs'], totals['violations']) == (0, 1, 0)
        assert gamebryo_seq_check.main([str(tmp_path), '--build-gate']) == 0
        assert _audit(tmp_path, capsys)[0] == 1

    @pytest.mark.parametrize('kind', ['foreign folder', 'tree of a removed mesh'])
    def test_a_behavior_folder_no_nif_names_is_skipped_not_failed(
            self, pair, tmp_path, capsys, kind):
        """Only a project a NIF names is ours to judge; the hand run still flags it."""
        root, dst, _tree = _copy_pair(pair[0], tmp_path)
        if kind == 'foreign folder':
            (dst.parent / 'mt_behavior').mkdir()
            (dst.parent / 'mt_behavior' / 'mt_behavior.hkx').write_bytes(b'x')
        else:
            shutil.copytree(dst.parent / 'wall_behavior', dst.parent / 'old_behavior')
        totals = gamebryo_seq_check.build_gate(str(root))
        assert (totals['checked'], totals['unnamed'], totals['violations']) == (1, 1, 0)
        assert 'skip ' in capsys.readouterr().out
        assert _audit(root, capsys)[0] == 1

    def test_a_missing_project_fails_even_with_no_tree_at_all(self, pair, tmp_path,
                                                             capsys):
        """Zero projects is a pass only when no NIF names one."""
        root, _dst, tree = _copy_pair(pair[0], tmp_path)
        shutil.rmtree(tree)
        totals = gamebryo_seq_check.build_gate(str(root))
        assert (totals['checked'], totals['violations']) == (0, 1)
        assert 'does not exist' in capsys.readouterr().out

    def test_the_pack_refuses_a_violating_tree(self, pair, holdless_pair,
                                               tmp_path, monkeypatch, capsys):
        """Nothing is handed to BSArch, and the error says why."""
        root, _dst, _tree = _crash_pair(pair, holdless_pair, tmp_path)
        output, _folder = _plugin_output(root, tmp_path)
        results, staged = _pack(monkeypatch, tmp_path, output)
        assert staged == {} and results['packed'] == []
        assert len(results['errors']) == 1
        assert 'animated-object violation' in results['errors'][0]
        assert 'BAD ' in capsys.readouterr().out

    def test_the_pack_takes_a_clean_tree(self, pair, tmp_path, monkeypatch):
        """Positive control: the same pack, without the crash pair, packs the pair."""
        output, _folder = _plugin_output(pair[0], tmp_path)
        results, staged = _pack(monkeypatch, tmp_path, output)
        assert results['errors'] == []
        assert 'meshes/tes4/dungeons/chargen/wall.nif' in staged['Oblivion.bsa']
        assert len(staged['Oblivion.bsa']) == 5

    @pytest.mark.parametrize('broken', [False, True])
    def test_the_mesh_step_ends_on_the_gate(self, pair, tmp_path, monkeypatch,
                                            broken):
        """`phase_assets` fails when the tree it leaves has a violation.

        The conversion itself is stubbed; the tree on disk is what is judged,
        which is also what a `--mesh-subdirs` partial run leaves.
        """
        output, folder = _plugin_output(pair[0], tmp_path)
        if broken:
            (folder / 'wall_hkxaside').mkdir()
        monkeypatch.setattr(convert.os, 'environ', dict(os.environ))
        monkeypatch.setattr('asset_convert.asset_pipeline.convert_meshes',
                            lambda **_k: {})
        monkeypatch.setattr('asset_convert.ui.book_inam.generate_book_inams',
                            lambda **_k: collections.Counter())
        assert convert.phase_assets('Oblivion.esm', {},
                                    output_dir=str(output)) is not broken

    def test_the_gate_honours_the_acceptance_flag(self, pair, tmp_path, capsys):
        """`--build-gate --require-playing-variable` refuses a legacy graph."""
        root, _dst, tree = _copy_pair(pair[0], tmp_path)
        _compile_graph(_legacy_held_xml(), tree / 'Behaviors' / 'Behavior00.hkx')
        assert gamebryo_seq_check.main([str(root), '--build-gate']) == 0
        assert gamebryo_seq_check.main(
            [str(root), '--build-gate', '--require-playing-variable']) == 1
        assert 'legacy graph: bAnimPlaying is not declared' in capsys.readouterr().out
        assert gamebryo_seq_check.build_gate(
            str(root), require_variable=True)['violations'] == 1

    def test_the_gate_refuses_an_expected_count_instead_of_ignoring_it(
            self, pair, capsys):
        """The gate judges violations only; a count it would not check is an error."""
        with pytest.raises(SystemExit) as stopped:
            gamebryo_seq_check.main([str(pair[0]), '--build-gate',
                                     '--expect-legacy', '0'])
        assert stopped.value.code == 2
        assert '--build-gate judges violations only' in capsys.readouterr().err

    @pytest.mark.parametrize('mixed', [True, False])
    def test_a_partly_rebuilt_tree_names_its_legacy_graphs(self, tmp_path, capsys,
                                                           mixed):
        """Listed only when the tree ALSO holds a graph that declares the variable."""
        old = _project(tmp_path / 'old', _PAIR, stem='old')
        _compile_graph(_legacy_held_xml('old'),
                       old[2] / 'Behaviors' / 'Behavior00.hkx')
        if mixed:
            _project(tmp_path / 'new', _PAIR, stem='new')
        totals = gamebryo_seq_check.build_gate(str(tmp_path))
        assert (totals['legacy'], totals['anim_var'], totals['violations']) == (
            1, int(mixed), 0)
        listed = [line for line in capsys.readouterr().out.splitlines()
                  if line.startswith('  legacy (no bAnimPlaying')]
        assert listed == ([f'  legacy (no bAnimPlaying, IsAnimPlaying reads 0 '
                           f'there) {old[1].with_suffix("")}'] if mixed else [])

    def test_legacy_graphs_beside_a_broken_project_are_not_called_a_mix(
            self, tmp_path, capsys):
        """A project that failed early is no proof that anything was rebuilt."""
        old = _project(tmp_path / 'old', _PAIR, stem='old')
        _compile_graph(_legacy_held_xml('old'),
                       old[2] / 'Behaviors' / 'Behavior00.hkx')
        broken = _project(tmp_path / 'bad', _PAIR, stem='bad')
        (broken[2] / 'Behaviors' / 'Behavior00.hkx').write_bytes(b'')
        totals = gamebryo_seq_check.build_gate(str(tmp_path))
        assert (totals['legacy'], totals['anim_var'], totals['violations']) == (
            1, 0, 1)
        assert '  legacy (no bAnimPlaying' not in capsys.readouterr().out
