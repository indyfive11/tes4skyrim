"""convert_meshes: a --mesh-subdirs run converts the named NIFs and nothing else."""
import pytest

from asset_convert import asset_pipeline

POST_PASSES = ('_profile_hair_and_grass', '_split_magic_art',
               '_copy_and_fix_textures')


@pytest.fixture
def calls(tmp_path, monkeypatch):
    """Stub every step of convert_meshes; return the list of steps that ran."""
    ran = []
    (tmp_path / 'export' / 'Test.esm' / 'meshes').mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(asset_pipeline, '_activate_namespace', lambda _d: 'tes4')
    monkeypatch.setattr(asset_pipeline, 'assemble_armor', lambda *_a: 0)
    monkeypatch.setattr(asset_pipeline, '_persist_mesh_manifests',
                        lambda *_a: None)
    monkeypatch.setattr(asset_pipeline, '_convert_mesh_tree',
                        lambda *_a: ran.append('batch') or {})
    for name in POST_PASSES:
        monkeypatch.setattr(asset_pipeline, name,
                            lambda *_a, _n=name: ran.append(_n))
    monkeypatch.setattr(asset_pipeline.landscape_normals, 'ensure_ltex_normals',
                        lambda *_a: ran.append('ltex_normals') or (0, 0))
    return ran


def test_filtered_run_converts_only_the_meshes(calls):
    """A --mesh-subdirs run stops after the NIF batch: no whole-tree pass runs."""
    asset_pipeline.convert_meshes('Test.esm', mesh_subdirs=['dungeons/a.nif'])
    assert calls == ['batch']


def _member(exp, plugin, sig, model):
    """Write one exported mod member whose one `sig` record names `model`."""
    rec_dir = asset_pipeline.record_dir(exp, plugin)
    rec_dir.mkdir(parents=True)
    (rec_dir / '_HEADER.txt').write_text('Master[0]=Oblivion.esm\n', encoding='utf-8')
    (rec_dir / f'{sig}.txt').write_text(
        f'---RECORD_BEGIN---\nFormID=01000A01\nModel.MODL={model}\n---RECORD_END---\n',
        encoding='utf-8')
    return rec_dir


def test_a_mods_shared_mesh_tree_is_planned_from_every_member(tmp_path):
    """Whichever member runs Meshes, the plan holds every member's door and fixture."""
    import json
    from asset_convert.character.wearable_plan import norm_model_path
    exp = tmp_path / 'export'
    exp.mkdir()
    names = ['A.esm', 'B.esp', 'Unexported.esp']
    (exp / 'sources.json').write_text(json.dumps({'version': 2, 'sources': {
        n: {'kind': 'archive', 'plugin': n, 'group_id': 'g', 'group_label': 'Pack',
            'group_plugins': names} for n in names}}), encoding='utf-8')
    _member(exp, 'A.esm', 'DOOR', 'a/door.nif')
    _member(exp, 'B.esp', 'STAT', 'b/rock.nif')

    members = asset_pipeline._exported_members(exp, 'B.esp')
    assert members == ['A.esm', 'B.esp']
    plan = asset_pipeline._mesh_plan([asset_pipeline.record_dir(exp, m) for m in members])
    door, rock = norm_model_path('a/door.nif'), norm_model_path('b/rock.nif')
    assert plan[asset_pipeline.door_plan.DOOR_KEY] == {door}
    assert plan[asset_pipeline.fixture_plan.FIXTURE_KEY] == {door, rock}


def test_unfiltered_run_runs_every_pass(calls):
    """A full run still runs the hair/grass, magic art, texture and LTEX passes."""
    asset_pipeline.convert_meshes('Test.esm')
    assert calls == ['batch', *POST_PASSES, 'ltex_normals']
