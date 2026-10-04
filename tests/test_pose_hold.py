"""Pose-hold states: a finished transition must HOLD its end pose across loads.

Skyrim saves a reference's graph state and re-enters it on load with the
generator restarted at t=0, so a graph whose only states are transitions
replays the last motion on every load.  These tests pin the three halves of
the fix: the graph (`End` -> hold state), the NIF (a one-frame `<Seq>Hold`
sequence) and the rule that the two never disagree on disk.
See: docs/commentary/asset_convert_animation.md#end-hold-states
"""

import hashlib
import io
import os
import re
import shutil
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

if not hasattr(time, 'clock'):
    time.clock = time.perf_counter

from asset_convert.havok import hkx_animobject, hkx_xml
from asset_convert.havok.hkx_animobject import (HOLD_EVENT, SOUND_EVENT,
                                                behavior_xml,
                                                generate_animobject_project,
                                                graph_generators,
                                                stage_animobject_project)
from asset_convert.nif import nif_batch, nif_converter, pose_hold, sse_nif
from asset_convert.nif.nif_passes import collect_sequence_names
from pyffi.formats.nif import NifFormat
from tests.conftest import require_case_twins
from tools.validate import gamebryo_seq_check

EXPORT_MESHES = Path('export/Oblivion.esm/meshes')

#: Forward/Backward wall; Forward carries a `sound:` key and keyed bools.
_WALL = 'dungeons/chargen/prisonsecretwall01.nif'

#: One Forward sequence whose 21 keyed NiVisController blocks end hidden.
_TRIPWIRE = 'dungeons/caves/triggers/ctrigtripwire01.nif'

#: Forward drives a NiPathInterpolator, which no constant can stand in for.
_PATH_DRIVEN = 'effects/se11clonefx.nif'

#: Forward is CLAMP with an `end` key, but plays at frequency 0.
_FROZEN = 'oblivion/architecture/citadel/interior/switch/scampswitch01.nif'

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _param(obj, name):
    """Text of the `name` hkparam of a packfile object ('' when empty)."""
    return next((p.text or '').strip() for p in obj.findall('hkparam')
                if p.get('name') == name)


def _decode(xml):
    """A behaviour XML as {'events', 'start', 'wildcard', 'states'}.

    `states` maps a state name to its id, generator name, sequence and rows,
    each row an (event name, target state name) pair.
    """
    objs = {o.get('name'): o for o in ET.fromstring(xml).iter('hkobject')
            if o.get('class')}
    by_class = {o.get('class'): o for o in objs.values()}
    strings = by_class['hkbBehaviorGraphStringData']
    names = next(p for p in strings.findall('hkparam')
                 if p.get('name') == 'eventNames')
    events = [e.text for e in names.findall('hkcstring')]
    machine = by_class['hkbStateMachine']
    raw = {}
    for ref in _param(machine, 'states').split():
        gen = objs[_param(objs[ref], 'generator')]
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
            'states': states}


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
        assert graph['start'] == 2
        plain = _decode(behavior_xml('wall', ['Forward', 'Backward']))
        assert {name: state[0] for name, state in plain['states'].items()} == {
            'Forward': 0, 'Backward': 1, 'Rest': 2}

    def test_held_sequences_gain_one_end_row(self):
        """Each held state reaches its OWN hold on End; Rest has no End row."""
        states = _decode(behavior_xml('wall', ['Forward', 'Backward'],
                                      _BOTH))['states']
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
        states = _decode(behavior_xml('wall', ['Forward', 'Backward'],
                                      _BOTH))['states']
        assert states['ForwardHold'][3] == [('Backward', 'Backward')]
        assert states['BackwardHold'][3] == [('Forward', 'Forward')]

    def test_single_sequence_hold_can_replay(self):
        """The lone sequence's self-row is inherited, so the hold is no dead end."""
        states = _decode(behavior_xml('wall', ['Unequip'],
                                      {'Unequip': 'UnequipHold'}))['states']
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

    def test_load_state_keeps_the_start_and_gets_no_end_row(self):
        """SpecialIdle still starts the machine; only Forward is held."""
        graph = _decode(behavior_xml('obj', ['SpecialIdle', 'Forward'],
                                     {'Forward': 'ForwardHold'}))
        assert graph['start'] == 0
        assert graph['states']['SpecialIdle'][3] == [('Forward', 'Forward')]
        assert graph['states']['ForwardHold'][0] == 4

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
        states = _decode(behavior_xml('obj', seqs, {shot: shot + 'Hold'}))['states']
        others = [(s, s) for s in seqs if s != shot]
        assert states[shot][3] == others + [(shot, shot),
                                            (HOLD_EVENT, shot + 'Hold')]
        assert states[shot + 'Hold'][3] == others + [(shot, shot)]
        for load in (s for s in seqs if s != shot):
            assert (load, load) not in states[load][3]

    def test_two_one_shots_beside_a_load_sequence_get_no_self_row(self):
        """With another one-shot to reach, a repeated event stays a no-op."""
        states = _decode(behavior_xml(
            'obj', ['SpecialIdle', 'Forward', 'Backward'], _BOTH))['states']
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
        assert {gen: seq for _id, gen, seq, _rows in states.values()} == \
            graph_generators(['Forward', 'Backward'], _BOTH)

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

    @pytest.mark.parametrize('seqs', sorted(_NO_HOLD_XML))
    def test_no_holds_is_the_old_graph_minus_the_wildcard_array(self, seqs):
        """Without holds: the pre-hold emitter's XML, wildcard array removed."""
        xml = behavior_xml('wall', list(seqs))
        assert xml == behavior_xml('wall', list(seqs), {})
        assert hashlib.sha256(xml.encode()).hexdigest() == _NO_HOLD_XML[seqs]


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

        def stage(_root, _rel, sequences, holds=None):
            """Record what the graph would be built from; compile nothing."""
            seen.update(sequences=sequences, holds=holds)
            return hkx_animobject.StagedProject('a\\b.hkx', None, None)

        monkeypatch.setattr(nif_converter, 'stage_animobject_project', stage)
        dst = tmp_path / 'meshes' / 'tes4' / 'fx.nif'
        nif_converter.convert_nif(str(_sample(sample)), str(dst))
        assert 'Forward' in seen['sequences'] and seen['holds'] == {}
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
            stage_animobject_project(str(tmp_path), 'tes4/a/wall.nif', ['Forward'])
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
                                        ['Forward'], {'Forward': 'ForwardHold'})
        assert len(calls) == 3
        assert not [p for p in tmp_path.rglob('*') if p.is_file()]
        assert [p.name for p in (tmp_path / 'tes4' / 'a').iterdir()] == []

    def test_a_compiled_graph_missing_its_holds_is_rejected(self, tmp_path,
                                                            monkeypatch):
        """hkxcmd exits 0 on a dangling reference, so the result is inspected."""
        real = hkx_animobject.behavior_xml
        monkeypatch.setattr(hkx_animobject, 'behavior_xml',
                            lambda name, seqs, _holds=None: real(name, seqs))
        with pytest.raises(RuntimeError, match='GamebryoSequenceGeneratorHold00'):
            stage_animobject_project(str(tmp_path), 'tes4/a/wall.nif',
                                     ['Forward'], {'Forward': 'ForwardHold'})
        assert not [p for p in tmp_path.rglob('*') if p.is_file()]

    @pytest.mark.parametrize('kind', sorted(_WRONG_GRAPHS) + ['holds swapped'])
    def test_a_compiled_generator_playing_the_wrong_thing_is_rejected(
            self, tmp_path, monkeypatch, kind):
        """Every name is still in the string pool; only the pairing is wrong."""
        monkeypatch.setattr(hkx_animobject, 'behavior_xml',
                            lambda *_a, **_k: _wrong_graph_xml(kind))
        with pytest.raises(RuntimeError, match='plays'):
            stage_animobject_project(str(tmp_path), 'tes4/a/wall.nif',
                                     ['Forward', 'Backward'], _BOTH)
        assert not [p for p in tmp_path.rglob('*') if p.is_file()]

    def test_a_hold_for_an_unknown_sequence_is_ignored_when_compiling(
            self, tmp_path):
        """As `behavior_xml` documents: no hold state, no End, and no failure."""
        generate_animobject_project(str(tmp_path), 'tes4/a/wall.nif',
                                    ['Forward'], {'Left': 'LeftHold'})
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

    def test_clean_pair_passes_with_counts(self, wall, capsys):
        """One project, one End graph, two holds, no violation: exit 0."""
        assert _audit(wall[0], capsys) == (0, (
            'behavior projects checked: 1   graphs declaring End: 1   '
            'hold sequences: 2   violations: 0'))

    def test_hold_less_graph_counts_no_end(self, holdless_wall, capsys):
        """The pre-hold shape is still clean, and reports zero End graphs."""
        assert _audit(holdless_wall[0], capsys) == (0, (
            'behavior projects checked: 1   graphs declaring End: 0   '
            'hold sequences: 0   violations: 0'))

    def test_crash_pair_is_flagged(self, wall, holdless_wall, tmp_path, capsys):
        """A graph naming holds the NIF lacks is a violation."""
        root, _dst, _tree = _crash_pair(wall, holdless_wall, tmp_path)
        code, summary = _audit(root, capsys)
        assert code == 1 and summary.endswith('violations: 1')

    def test_uppercase_nif_extension_is_paired(self, wall, holdless_wall,
                                               tmp_path, capsys):
        """`wall.NIF` was skipped: the crash pair read "checked: 0", exit 0."""
        root, dst, _tree = _crash_pair(wall, holdless_wall, tmp_path)
        dst.rename(dst.with_suffix('.NIF'))
        code, summary = _audit(root, capsys)
        assert code == 1
        assert summary.startswith('behavior projects checked: 1 ')
        assert summary.endswith('violations: 1')

    def test_lowercase_tree_members_are_paired(self, wall, holdless_wall,
                                               tmp_path, capsys):
        """A BSA extract or loose deploy lowercases `behaviors/behavior00.hkx`."""
        root, _dst, tree = _crash_pair(wall, holdless_wall, tmp_path)
        (tree / 'Behaviors' / 'Behavior00.hkx').rename(
            tree / 'Behaviors' / 'behavior00.hkx')
        (tree / 'Behaviors').rename(tree / 'behaviors')
        code, summary = _audit(root, capsys)
        assert code == 1
        assert summary.startswith('behavior projects checked: 1 ')
        assert summary.endswith('violations: 1')

    def test_renamed_clean_pair_still_passes(self, wall, tmp_path, capsys):
        """Case-insensitive pairing finds the pair and finds it clean."""
        root, dst, tree = _copy_pair(wall[0], tmp_path)
        dst.rename(dst.with_suffix('.NIF'))
        (tree / 'Behaviors' / 'Behavior00.hkx').rename(
            tree / 'Behaviors' / 'behavior00.hkx')
        (tree / 'Behaviors').rename(tree / 'behaviors')
        assert _audit(root, capsys) == (0, (
            'behavior projects checked: 1   graphs declaring End: 1   '
            'hold sequences: 2   violations: 0'))

    def test_unreadable_nif_is_a_violation(self, wall, tmp_path, capsys):
        """It used to count as checked-and-clean."""
        root, dst, _tree = _copy_pair(wall[0], tmp_path)
        dst.write_bytes(b'not a nif')
        code, summary = _audit(root, capsys)
        assert code == 1 and summary.endswith('violations: 1')

    @pytest.mark.parametrize('missing', [
        'wall.nif', 'wall_behavior/Behaviors/Behavior00.hkx',
        'wall_behavior/Characters/Character01.hkx',
        'wall_behavior/CharacterAssets/Skeleton.hkx', 'wall_behavior/wall.hkx'])
    def test_unpaired_or_incomplete_tree_is_a_violation(self, wall, tmp_path,
                                                        capsys, missing):
        """A tree with no NIF, or missing any of its four files, is not clean."""
        root, dst, _tree = _copy_pair(wall[0], tmp_path)
        (dst.parent / missing).unlink()
        code, summary = _audit(root, capsys)
        assert code == 1
        assert summary.startswith('behavior projects checked: 1 ')
        assert not summary.endswith('violations: 0')

    @pytest.mark.parametrize('damage', ['empty file', 'no Rest generator'])
    def test_a_graph_nothing_was_read_from_is_a_violation(self, wall, tmp_path,
                                                          capsys, damage):
        """Zero generators, or none named Rest, used to report "ok"."""
        root, _dst, tree = _copy_pair(wall[0], tmp_path)
        graph = tree / 'Behaviors' / 'Behavior00.hkx'
        graph.write_bytes(b'' if damage == 'empty file' else
                          graph.read_bytes().replace(b'GeneratorRest', b'GeneratorXest'))
        code, out = _audit_text(root, capsys)
        assert code == 1 and 'no Rest generator read' in out

    @pytest.mark.parametrize('leftover', ['wall_hkxstage', 'wall_hkxaside',
                                          'other.ni~'])
    def test_leftovers_of_an_interrupted_build_are_violations(
            self, wall, tmp_path, capsys, leftover):
        """They get packed like any file, and no tree walk would notice them."""
        root, dst, _tree = _copy_pair(wall[0], tmp_path)
        if leftover.endswith('~'):
            (dst.parent / leftover).write_bytes(b'half')
        else:
            (dst.parent / leftover).mkdir()
        code, out = _audit_text(root, capsys)
        assert code == 1 and 'violations: 1' in out and 'leftover' in out

    def test_a_bged_naming_a_missing_project_is_a_violation(
            self, wall, tmp_path, capsys, monkeypatch):
        """A NIF that names no tree on disk is not drawn; a healthy neighbour hid it."""
        root, dst, _tree = _copy_pair(wall[0], tmp_path)
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

    def test_matching_counts_never_excuse_a_violation(self, wall, holdless_wall,
                                                      tmp_path, capsys):
        """The crash pair has the right counts; the exit code must still be 1."""
        root, _dst, _tree = _crash_pair(wall, holdless_wall, tmp_path)
        code, summary = _audit(root, capsys, '--expect-checked', '1',
                               '--expect-end-graphs', '1', '--expect-holds', '2')
        assert code == 1 and summary.endswith('hold sequences: 2   violations: 1')

    def test_hold_generators_without_an_end_event_are_a_violation(
            self, wall, tmp_path, capsys):
        """Hold states nothing can reach: the pool has holds but no `End`."""
        root, _dst, tree = _copy_pair(wall[0], tmp_path)
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
    def test_generator_rules_fire_on_a_compiled_graph(self, wall, tmp_path,
                                                      capsys, kind, message):
        """Rest must play nothing; every other generator must play something."""
        root, _dst, tree = _copy_pair(wall[0], tmp_path)
        _compile_graph(_wrong_graph_xml(kind),
                       tree / 'Behaviors' / 'Behavior00.hkx')
        code, out = _audit_text(root, capsys)
        assert code == 1 and message in out

    def test_an_empty_text_key_beside_a_graph_is_a_violation(self, wall,
                                                             tmp_path, capsys):
        """The generator strchr()s every key value; an empty one is a NULL string."""
        root, dst, _tree = _copy_pair(wall[0], tmp_path)
        data = NifFormat.Data()
        with open(dst, 'rb') as stream:
            data.read(stream)
        _sequences(data)['Forward'].text_keys.text_keys[1].value = b''
        with open(dst, 'wb') as stream:
            data.write(stream)
        code, out = _audit_text(root, capsys)
        assert code == 1 and 'EMPTY text key value' in out

    def test_two_nifs_differing_by_case_are_ambiguous(self, wall, tmp_path,
                                                      capsys):
        """The engine sees one name; which file it loads is not knowable here."""
        require_case_twins(tmp_path)
        root, dst, _tree = _copy_pair(wall[0], tmp_path)
        shutil.copy(dst, dst.with_suffix('.NIF'))
        code, summary = _audit(root, capsys)
        assert code == 1 and summary.endswith('violations: 1')

    def test_nothing_checked_is_a_failure(self, tmp_path, capsys):
        """A run that inspected no project proves nothing."""
        code, summary = _audit(tmp_path, capsys)
        assert code == 1
        assert summary.startswith('behavior projects checked: 0 ')

    def test_expected_counts_are_enforced(self, wall, capsys):
        """Matching `--expect-*` counts pass; any mismatch fails a clean tree."""
        good = ['--expect-checked', '1', '--expect-end-graphs', '1',
                '--expect-holds', '2']
        assert _audit(wall[0], capsys, *good)[0] == 0
        for flag in ('--expect-checked', '--expect-end-graphs', '--expect-holds'):
            assert _audit(wall[0], capsys, flag, '7')[0] == 1
