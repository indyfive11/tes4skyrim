"""Oblivion's flowing-water effects take Skyrim's waterfall shading.

See: docs/commentary/asset_convert_shader.md#flowing-water-effects
"""
import os
import struct
import time

if not hasattr(time, 'clock'):
    time.clock = time.perf_counter

import numpy as np
import pytest
from pyffi.formats.nif import NifFormat

from asset_convert.nif import flipbook, water_fx
from asset_convert.texture import water_spray

#: Oblivion's additive blend (SRC_ALPHA, ONE) on its water sheets and spray.
ADDITIVE = 0x100D


def _effect(texture=b'Textures\\tes4\\landscape\\LandscapeWater.dds'):
    """A converted effect shader as the FX pass leaves it: self-lit, soft 100."""
    shader = NifFormat.BSEffectShaderProperty()
    shader.shader_flags_1.slsf_1_own_emit = 1
    shader.shader_flags_1.slsf_1_z_buffer_test = 1
    shader.source_texture = texture
    shader.texture_clamp_mode = 3
    shader.soft_falloff_depth = 100.0
    return shader


def _alpha(flags=ADDITIVE, threshold=0):
    """A NiAlphaProperty."""
    prop = NifFormat.NiAlphaProperty()
    prop.flags = flags
    prop.threshold = threshold
    return prop


class TestWhichEffectIsWater:
    """The file name says water; particles are spray, a foam sheet is foam."""

    @pytest.mark.parametrize('texture, on_particles, kind', [
        (b'Textures\\tes4\\landscape\\LandscapeWater.dds', False, 'fluid'),
        (b'textures\\tes4\\architecture\\palace\\interior\\palacewater02.dds', False, 'fluid'),
        (b'Textures\\tes4\\landscape\\LandscapeWaterFoam.dds', False, 'foam'),
        (b'textures\\tes4\\architecture\\city\\mania\\maniwaterfoam01.dds', False, 'foam'),
        (b'Textures\\tes4\\landscape\\LandscapeWaterParticles01.dds', True, 'spray'),
        (b'textures\\tes4\\effects\\watersplash.dds', True, 'spray'),
    ])
    def test_the_nine_oblivion_water_textures(self, texture, on_particles, kind):
        """Sheets of water or foam, and the spray on particle systems."""
        assert water_fx.water_kind(texture, on_particles) == kind

    @pytest.mark.parametrize('texture', [
        b'textures\\tes4\\effects\\fxfirelarge.dds',
        b'textures\\tes4\\water\\mistplane01.dds',
        b'textures\\tes4\\effects\\ndfxsparkles.dds',
        b'',
    ])
    def test_other_effects_are_not_water(self, texture):
        """Fire, magic and mist keep their additive look; a folder name alone is no mark."""
        assert water_fx.water_kind(texture, False) is None
        assert water_fx.water_kind(texture, True) is None

    def test_the_spray_texture_is_renamed_beside_the_original(self):
        """The rebuilt texture sits beside the source, `_skya` added."""
        assert water_fx.spray_texture(b'Textures\\tes4\\Effects\\WaterSplash.dds') == \
            'textures\\tes4\\effects\\watersplash_skya.dds'


class TestTheLook:
    """Each kind takes the values of Skyrim's own waterfall meshes."""

    def test_a_fluid_sheet(self):
        """Fluid texture and palette, scene light 191, falloff, soft 10, normal blend tested at 128."""
        shader, alpha = _effect(), _alpha()
        water_fx.apply_water_look(shader, alpha, 'fluid')
        f1, f2 = shader.shader_flags_1, shader.shader_flags_2
        assert bytes(shader.source_texture) == b'textures\\effects\\FXfluidTile01.dds'
        assert bytes(shader.greyscale_texture).endswith(b'GradWhiteWaterMedSoft.dds')
        assert f1.slsf_1_own_emit == 0 and f1.slsf_1_use_falloff == 1 and f1.slsf_1_external_emittance == 1
        assert f1.slsf_1_greyscale_to_palette_color == 1 and f1.slsf_1_soft_effect == 1
        assert f2.slsf_2_effect_lighting == 1 and f2.slsf_2_double_sided == 1
        assert int(shader.texture_clamp_mode) == 3 | (191 << 8)
        assert shader.soft_falloff_depth == pytest.approx(10.0)
        assert (int(alpha.flags), int(alpha.threshold)) == (0x10ED, 128)

    def test_a_foam_sheet_has_no_falloff(self):
        """Foam: the white-water texture, no falloff, soft 5."""
        shader, alpha = _effect(), _alpha()
        water_fx.apply_water_look(shader, alpha, 'foam')
        assert bytes(shader.source_texture) == b'textures\\effects\\FXWhiteWater01.dds'
        assert shader.shader_flags_1.slsf_1_use_falloff == 0
        assert shader.soft_falloff_depth == pytest.approx(5.0)

    def test_spray_keeps_its_own_texture_and_is_untested(self):
        """Spray: the given texture, no palette, alpha test threshold 0, soft 35."""
        shader, alpha = _effect(), _alpha()
        water_fx.apply_water_look(shader, alpha, 'spray', b'textures\\x_skya.dds')
        assert bytes(shader.source_texture) == b'textures\\x_skya.dds'
        assert bytes(shader.greyscale_texture) == b''
        assert shader.shader_flags_1.slsf_1_greyscale_to_palette_color == 0
        assert (int(alpha.flags), int(alpha.threshold)) == (0x10ED, 0)
        assert shader.soft_falloff_depth == pytest.approx(35.0)

    def test_the_clamp_mode_byte_is_kept(self):
        """Only the lighting-influence byte changes."""
        shader = _effect()
        shader.texture_clamp_mode = 0x0000FF00 | 1
        water_fx.apply_water_look(shader, _alpha(), 'fluid')
        assert int(shader.texture_clamp_mode) == 1 | (191 << 8)


def _sheet(shader, alpha):
    """A converted shape holding `shader` and `alpha`."""
    ts = NifFormat.NiTriShape()
    ts.bs_properties[0] = shader
    ts.bs_properties[1] = alpha
    return ts


class TestASheetOwnsItsBlend:
    """Oblivion shares one NiAlphaProperty across shapes: a water sheet gets a copy."""

    def test_the_shared_property_is_left_as_it_was(self):
        """The sheet points at its own copy; the shared one keeps the additive blend."""
        shared = _alpha()
        ts = _sheet(_effect(), shared)
        assert water_fx.water_sheet(ts, {})
        assert ts.bs_properties[1] is not shared and int(ts.bs_properties[1].flags) == 0x10ED
        assert int(shared.flags) == ADDITIVE

    def test_a_sheet_without_a_blend_gets_one(self):
        """The waterfall blend needs a NiAlphaProperty even where the source had none."""
        ts = _sheet(_effect(), None)
        assert water_fx.water_sheet(ts, {})
        assert int(ts.bs_properties[1].flags) == 0x10ED

    def test_a_non_water_effect_and_a_lit_shape_are_left_alone(self):
        """Nothing changes for a fire sheet or a lighting shader."""
        fire = _sheet(_effect(b'textures\\tes4\\effects\\fxfire.dds'), _alpha())
        lit = _sheet(NifFormat.BSLightingShaderProperty(), _alpha())
        stats = {}
        assert not water_fx.water_sheet(fire, stats) and not water_fx.water_sheet(lit, stats)
        assert int(fire.bs_properties[1].flags) == ADDITIVE and 'water_sheets' not in stats

    def test_two_sheets_sharing_a_blend_both_stay_effects(self):
        """Through the geometry pass: the second sheet still reads the source's additive blend.

        Changing the shared property in place would turn the second sheet into a
        lit shape, since the FX pass keys on that blend.
        """
        from asset_convert.nif.nif_converter import process_geometry
        shared = _alpha()
        shapes = []
        for _ in range(2):
            shape = NifFormat.NiTriShape()
            shape.data = NifFormat.NiTriShapeData()
            tex = NifFormat.NiTexturingProperty()
            tex.has_base_texture = True
            tex.base_texture.source = NifFormat.NiSourceTexture()
            tex.base_texture.source.file_name = b'textures\\landscape\\landscapewater.dds'
            shape.num_properties = 2
            shape.properties.update_size()
            shape.properties[0], shape.properties[1] = tex, shared
            shapes.append(process_geometry(shape, fix_textures=True, stats={}))
        kinds = [type(s.bs_properties[0]).__name__ for s in shapes]
        assert kinds == ['BSEffectShaderProperty', 'BSEffectShaderProperty']
        assert all(int(s.bs_properties[1].flags) == 0x10ED for s in shapes)
        assert shapes[0].bs_properties[1] is not shapes[1].bs_properties[1]
        assert int(shared.flags) == ADDITIVE


class TestSprayIsPlanned:
    """A water particle system takes the spray look and a job for its texture."""

    def test_the_job_names_the_source_and_the_new_texture(self):
        """The job maps the rebuilt name to the extracted source."""
        shader, alpha, stats = _effect(b'Textures\\tes4\\effects\\WaterSplash.dds'), _alpha(), {}
        assert water_fx.plan_water_spray(shader, alpha, 'textures\\effects\\watersplash.dds',
                                         lambda rel: '/src/' + rel, stats)
        job = stats['_water_spray_textures']['textures\\tes4\\effects\\watersplash_skya.dds']
        assert job['src'] == '/src/textures\\effects\\watersplash.dds'
        assert bytes(shader.source_texture) == b'textures\\tes4\\effects\\watersplash_skya.dds'
        assert stats['water_sprays'] == 1

    def test_a_spray_whose_source_is_missing_is_left_as_it_was(self):
        """Without a source the texture cannot be rebuilt, so the shader is not changed."""
        shader, alpha, stats = _effect(b'textures\\tes4\\effects\\watersplash.dds'), _alpha(), {}
        assert not water_fx.plan_water_spray(shader, alpha, 'x', lambda rel: None, stats)
        assert shader.shader_flags_1.slsf_1_own_emit == 1 and int(alpha.flags) == ADDITIVE
        assert '_water_spray_textures' not in stats

    def test_a_non_water_particle_system_is_left_alone(self):
        """Smoke or fire particles are not spray."""
        shader, stats = _effect(b'textures\\tes4\\effects\\smoke01.dds'), {}
        assert not water_fx.plan_water_spray(shader, _alpha(), 'x', lambda rel: '/x', stats)
        assert stats == {}


def _image(h=64, w=64, alpha=3.0, background=222.0, blob=255.0):
    """A spray as Oblivion paints one: a bright blob on a flat background."""
    img = np.zeros((h, w, 4))
    img[..., :3] = background
    yy, xx = np.mgrid[0:h, 0:w]
    inside = (yy - h / 2) ** 2 + (xx - w / 2) ** 2 < (min(h, w) / 5) ** 2
    img[inside, :3] = blob
    img[..., 3] = alpha
    return img, inside


class TestSprayAlpha:
    """The alpha follows the shape over the background and fades out at the edge."""

    def test_the_blob_is_opaque_and_the_background_clear(self):
        """Brightness above the edge level becomes alpha; the flat background none."""
        img, inside = _image()
        out = water_spray.spray_rgba(img)
        assert out[inside, 3].min() > 200 and out[~inside, 3].max() <= 6.0

    def test_an_authored_alpha_still_counts_doubled(self):
        """A faint authored alpha over the background is kept, doubled."""
        img, _ = _image(alpha=40.0, blob=222.0)
        out = water_spray.spray_rgba(img)
        assert out[32, 32, 3] == pytest.approx(80.0)

    def test_an_opaque_alpha_is_ignored(self):
        """An alpha opaque everywhere (made for the additive blend) holds no shape."""
        img, inside = _image(alpha=255.0)
        out = water_spray.spray_rgba(img)
        assert out[~inside, 3].max() <= 6.0 and out[inside, 3].min() > 200

    @pytest.mark.parametrize('side', [32, 256])
    def test_the_edge_is_always_clear(self, side):
        """No box: the outer texels are transparent at any size."""
        img = np.full((side, side, 4), 255.0)
        img[..., 3] = 0.0
        out = water_spray.spray_rgba(img)
        edge = np.r_[out[0, :, 3], out[-1, :, 3], out[:, 0, 3], out[:, -1, 3]]
        assert edge.max() == 0.0

    def test_the_colour_moves_toward_white(self):
        """A grey spray keeps 40 % of its distance to white."""
        img, _ = _image(background=155.0, blob=155.0)
        out = water_spray.spray_rgba(img)
        assert out[0, 0, 0] == pytest.approx(255 - 100 * 0.4)


class TestTheSprayFile:
    """An uncompressed BGRA DDS with every mip level, written atomically."""

    def test_the_header_and_the_mip_chain(self):
        """32x16 holds six levels; the size is the header plus every level."""
        blob = water_spray.bgra_dds(np.zeros((16, 32, 4)))
        size, flags, h, w, pitch, _depth, mips = struct.unpack_from('<7I', blob, 4)
        assert (blob[:4], size, h, w, pitch, mips) == (b'DDS ', 124, 16, 32, 128, 6)
        assert flags & 0x20000 and struct.unpack_from('<I', blob, 80)[0] == 0x41
        assert len(blob) == 128 + 4 * sum(max(32 >> i, 1) * max(16 >> i, 1) for i in range(6))

    def test_a_source_texture_is_rebuilt(self, tmp_path):
        """From a decoded source DDS to the spray file, read back."""
        img, inside = _image()
        bgra = img[..., [2, 1, 0, 3]].astype(np.uint8).tobytes()
        src = str(tmp_path / 'src.dds')
        flipbook._write_bgra_dds(src, 64, 64, bgra)
        out = str(tmp_path / 'out' / 'spray_skya.dds')
        assert water_spray.build_spray_texture(src, out)
        w, h, back = flipbook.decode_dds(out)
        alpha = np.frombuffer(bytes(back), np.uint8).reshape(h, w, 4)[..., 3]
        assert (w, h) == (64, 64) and alpha[32, 32] > 200 and alpha[0, 0] == 0
        assert os.listdir(tmp_path / 'out') == ['spray_skya.dds']

    def test_an_unreadable_source_writes_nothing(self, tmp_path):
        """A missing source answers False and leaves no file."""
        out = tmp_path / 'out.dds'
        assert not water_spray.build_spray_texture(str(tmp_path / 'missing.dds'), str(out))
        assert not out.exists()
