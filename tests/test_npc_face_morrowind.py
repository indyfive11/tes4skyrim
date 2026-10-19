"""Morrowind NPC faces: head measurement, the hair/beard table, sliders and the export lines."""

from types import SimpleNamespace

import numpy as np
import pytest

from asset_convert.character import morrowind_head
from tes4_export.record_types import morrowind_actors
from tes5_import.actors import npc_face_morrowind as faces

NORD = 0x00013746
ARGONIAN = 0x00013740


def _head(width=10.0, nose=3.0):
    """A synthetic head: a box of `width` with a nose that reaches `nose` forward."""
    rng = np.random.default_rng(0)
    pts = rng.uniform([-width / 2, -5, 0], [width / 2, 5, 20], (400, 3))
    pts = np.vstack([pts, [[0, -5 - nose, 9], [0, -5 - nose / 2, 10]]])
    return pts


def test_head_features_track_nose_and_width():
    """A longer nose raises nose_forward; a wider head raises jaw_width."""
    base = morrowind_head.head_features(_head())
    longer = morrowind_head.head_features(_head(nose=6.0))
    wider = morrowind_head.head_features(_head(width=14.0))
    i_nose = morrowind_head.FEATURES.index('nose_forward')
    i_jaw = morrowind_head.FEATURES.index('jaw_width')
    assert len(base) == len(morrowind_head.FEATURES)
    assert longer[i_nose] > base[i_nose]
    assert wider[i_jaw] > base[i_jaw]


def test_head_features_reject_tiny_meshes():
    """Too few vertices to measure gives None."""
    assert morrowind_head.head_features(np.zeros((5, 3))) is None


def _rec(edid='npc_a', head='b_n_nord_m_head_01', hair='b_n_nord_m_hair01', female=False):
    """A minimal exported Morrowind NPC record."""
    return {'EditorID': edid, 'MorrowindHead': head, 'MorrowindHair': hair,
            'MorrowindRace': 'Nord', 'ACBS.Flags': str(int(female))}


def test_every_table_pick_is_valid_for_some_race():
    """Each shipped hair/beard pick names a vanilla head part with a race list."""
    data = faces._data()
    picks = {v for table in ('hair', 'beard') for v in data[table].values() if v}
    assert picks and all(data['head_part_races'].get(p) for p in picks)


def test_hair_from_table_pool_and_bald():
    """A table hit is used as is, an unknown part picks from the race pool, '' stays bald."""
    data = faces._data()
    part, pick = next((k, v) for k, v in data['hair'].items()
                      if v and faces._allowed(v, NORD))
    assert faces._hair_fid(part, NORD, False) == int(pick, 16)
    pool = data['hair_pools'][f'{NORD:08X}|M']
    assert f'{faces._hair_fid("no_such_hair", NORD, False):08X}' in pool
    bald = next(k for k, v in data['hair'].items() if v == '')
    assert faces._hair_fid(bald, NORD, False) == 0


def test_beard_skipped_for_a_race_it_does_not_fit():
    """A human beard pick is not written on an Argonian."""
    data = faces._data()
    head = next(k for k, v in data['beard'].items() if v and faces._allowed(v, NORD)
                and not faces._allowed(v, ARGONIAN))
    rec = _rec(head=head, hair='')
    assert len(faces.head_part_subs(rec, NORD, False)) > 0
    assert faces.head_part_subs(rec, ARGONIAN, False) == b''


@pytest.fixture
def measured():
    """PARTS/GROUPS holding two measured heads in one group, restored afterwards."""
    saved = dict(faces.PARTS), dict(faces.GROUPS)
    faces.PARTS.clear()
    faces.GROUPS.clear()
    faces.PARTS['wide'] = (morrowind_head.head_features(_head(width=14.0)), (200, 150, 120))
    faces.PARTS['narrow'] = (morrowind_head.head_features(_head(width=8.0)), None)
    faces._fill_groups({('nord', False): {'wide': faces.PARTS['wide'][0],
                                          'narrow': faces.PARTS['narrow'][0]}})
    yield
    faces.PARTS.clear()
    faces.PARTS.update(saved[0])
    faces.GROUPS.clear()
    faces.GROUPS.update(saved[1])


def test_morphs_follow_the_head_and_vary_per_npc(measured):
    """A wider head gets a wider jaw; two NPCs on one head differ slightly; all stay in range."""
    wide = faces.face_morphs(_rec(head='wide'), NORD, False)
    narrow = faces.face_morphs(_rec(head='narrow'), NORD, False)
    other = faces.face_morphs(_rec(edid='npc_b', head='wide'), NORD, False)
    assert len(wide) == 19 and all(-1.0 <= v <= 1.0 for v in wide)
    assert wide[3] > narrow[3]
    assert wide != other
    assert max(abs(a - b) for a, b in zip(wide, other)) < 0.5
    assert faces.face_morphs(_rec(head='wide'), NORD, False) == wide


def test_skin_and_weight(measured):
    """Skin comes from the head texture; weight sits inside 0-100."""
    assert faces.skin_color(_rec(head='wide')) == (200, 150, 120)
    assert 0.0 <= faces.weight(_rec(), NORD, False) <= 100.0
    assert faces.face_morphs(_rec(), 0xDEADBEEF, False) is None


def test_archive_cache_write_is_atomic(tmp_path, monkeypatch):
    """An extracted archive entry lands whole under its name, with no temp file left."""
    from asset_convert.sources import morrowind_assets
    monkeypatch.setattr(morrowind_assets, 'read_entry', lambda *entry: b'payload')
    target = tmp_path / 'textures' / 'tx_head.dds'
    assert morrowind_assets._cache_entry(target, ('a.bsa', 0, 7)) == target
    assert target.read_bytes() == b'payload'
    assert [p.name for p in target.parent.iterdir()] == ['tx_head.dds']


def test_export_writes_head_and_hair_parts():
    """export_NPC_'s head-part lines name each BODY id and its mesh."""
    def sub(sig, text):
        """A TES3 string subrecord."""
        return SimpleNamespace(type=sig, data=text.encode() + b'\0')
    rec = SimpleNamespace(subrecords=[sub('BNAM', 'B_N_Nord_M_Head_01'), sub('KNAM', 'b_n_nord_m_hair01')])
    ctx = SimpleNamespace(body_models={'b_n_nord_m_head_01': 'b\\B_N_Nord_M_Head_01.nif'})
    lines = []
    morrowind_actors._emit_head_parts(lines, rec, ctx)
    assert lines[0] == 'MorrowindHead=B_N_Nord_M_Head_01'
    assert lines[1].startswith('MorrowindHeadModel=')
    assert lines[2] == 'MorrowindHair=b_n_nord_m_hair01'
    assert len(lines) == 3
