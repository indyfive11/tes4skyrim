"""Oblivion's flowing-water effects take Skyrim's waterfall shading.

Oblivion draws its water sheets and spray additively, self-lit, over low-alpha
textures; converted as they are, they nearly vanish under Skyrim's lighting.
A water-textured effect takes the setup of Skyrim's own waterfalls instead.
See: docs/commentary/asset_convert_shader.md#flowing-water-effects
"""

from asset_convert.nif.tex_paths import path_bytes, path_text

from asset_convert.nif.pyffi_monkey_patch import apply_patches
apply_patches()
from pyffi.formats.nif import NifFormat

#: Texture-name mark of a water effect. See: docs/commentary/asset_convert_shader.md#flowing-water-effects
WATER_MARK = 'water'

#: Texture-name mark of a foam sheet.
FOAM_MARK = 'foam'

#: Name a spray texture takes once its alpha is rebuilt from its shape.
SPRAY_SUFFIX = '_skya.dds'

#: Skyrim's white-water palette, mapped onto a sheet's brightness and alpha.
_GRADIENT = b'textures\\effects\\gradients\\GradWhiteWaterMedSoft.dds'

#: Blend SRC_ALPHA / INV_SRC_ALPHA with the alpha test, as on Skyrim's waterfalls.
_WATER_ALPHA_FLAGS = 0x10ED

#: The scene light a water effect takes (vanilla fxwaterfallbodytall: 191).
_LIGHTING_INFLUENCE = 191

#: texture_clamp_mode's lighting-influence byte.
_INFLUENCE_SHIFT = 8

#: Each kind's setup, from vanilla fxwaterfallbodytall.nif / fxwaterfallbodyslope.nif.
_LOOKS = {
    'fluid': {
        'texture': b'textures\\effects\\FXfluidTile01.dds', 'greyscale': _GRADIENT,
        'flags1': ('vertex_alpha', 'greyscale_to_palette_color', 'greyscale_to_palette_alpha',
                   'use_falloff', 'external_emittance', 'soft_effect', 'z_buffer_test'),
        'flags2': ('double_sided', 'vertex_colors', 'effect_lighting'),
        'falloff': (0.42, 0.17, 1.0, 0.0), 'emissive': (1.0, 1.0, 1.0, 0.8),
        'multiple': 0.9, 'soft': 10.0, 'threshold': 128,
    },
    'foam': {
        'texture': b'textures\\effects\\FXWhiteWater01.dds', 'greyscale': _GRADIENT,
        'flags1': ('vertex_alpha', 'greyscale_to_palette_color', 'greyscale_to_palette_alpha',
                   'external_emittance', 'soft_effect', 'z_buffer_test'),
        'flags2': ('double_sided', 'vertex_colors', 'effect_lighting'),
        'falloff': (0.42, 0.09, 1.0, 0.0), 'emissive': (1.0, 1.0, 1.0, 1.0),
        'multiple': 1.0, 'soft': 5.0, 'threshold': 128,
    },
    'spray': {
        'texture': None, 'greyscale': b'',
        'flags1': ('external_emittance', 'soft_effect', 'z_buffer_test'),
        'flags2': ('vertex_colors', 'effect_lighting'),
        'falloff': (1.0, 1.0, 0.0, 0.0), 'emissive': (0.96, 0.96, 0.96, 1.0),
        'multiple': 0.8, 'soft': 35.0, 'threshold': 0,
    },
}


def _text(path) -> str:
    """A texture path as lowercase text with backslashes."""
    return path_text(path).replace('/', '\\').lower()


def water_kind(texture, on_particles: bool):
    """'spray', 'foam', 'fluid', or None for an effect texture that names no water.

    See: docs/commentary/asset_convert_shader.md#flowing-water-effects
    """
    name = _text(texture).rsplit('\\', 1)[-1]
    if WATER_MARK not in name:
        return None
    if on_particles:
        return 'spray'
    return 'foam' if FOAM_MARK in name else 'fluid'


def spray_texture(texture) -> str:
    """The rebuilt-alpha name of a spray texture."""
    text = _text(texture)
    return (text[:-4] if text.endswith('.dds') else text) + SPRAY_SUFFIX


def _set_flags(flags, prefix: str, names) -> None:
    """Clear every flag of a shader flag field, then set `names`."""
    for field in flags._names:
        setattr(flags, field, 0)
    for name in names:
        setattr(flags, prefix + name, 1)


def apply_water_look(shader, alpha_prop, kind: str, texture=None) -> None:
    """Give a water effect shader and its own NiAlphaProperty `kind`'s setup.

    `texture` replaces the look's own (a spray's rebuilt texture). UV transform
    and clamp mode are kept; the scroll controllers stay attached.
    See: docs/commentary/asset_convert_shader.md#flowing-water-effects
    """
    look = _LOOKS[kind]
    _set_flags(shader.shader_flags_1, 'slsf_1_', look['flags1'])
    _set_flags(shader.shader_flags_2, 'slsf_2_', look['flags2'])
    shader.source_texture = texture if texture is not None else look['texture']
    shader.greyscale_texture = look['greyscale']
    clamp = int(shader.texture_clamp_mode) & 0xFF
    shader.texture_clamp_mode = clamp | (_LIGHTING_INFLUENCE << _INFLUENCE_SHIFT)
    (shader.falloff_start_angle, shader.falloff_stop_angle,
     shader.falloff_start_opacity, shader.falloff_stop_opacity) = look['falloff']
    color = shader.emissive_color
    color.r, color.g, color.b, color.a = look['emissive']
    shader.emissive_multiple = look['multiple']
    shader.soft_falloff_depth = look['soft']
    alpha_prop.flags = _WATER_ALPHA_FLAGS
    alpha_prop.threshold = look['threshold']


def own_alpha(alpha_prop):
    """A copy of `alpha_prop` for one shape alone (Oblivion shares them)."""
    own = NifFormat.NiAlphaProperty()
    if alpha_prop is not None:
        own.flags = alpha_prop.flags
        own.threshold = alpha_prop.threshold
    return own


def _count(stats, key: str) -> None:
    """Add one to a stats counter."""
    if stats is not None:
        stats[key] = stats.get(key, 0) + 1


def water_sheet(ts, stats) -> bool:
    """Give a converted water sheet the fluid or foam look; True when it took one.

    See: docs/commentary/asset_convert_shader.md#flowing-water-effects
    """
    shader = ts.bs_properties[0]
    if not isinstance(shader, NifFormat.BSEffectShaderProperty):
        return False
    kind = water_kind(shader.source_texture, False)
    if kind is None:
        return False
    alpha_prop = own_alpha(ts.bs_properties[1])
    ts.bs_properties[1] = alpha_prop
    apply_water_look(shader, alpha_prop, kind)
    _count(stats, 'water_sheets')
    return True


def plan_water_spray(shader, alpha_prop, source_rel, resolve, stats) -> bool:
    """Give a particle system's water spray the spray look and plan its texture.

    `resolve` maps the source texture to its extracted file; when it finds
    none the spray is left as it was, since its texture cannot be rebuilt.
    See: docs/commentary/asset_convert_shader.md#flowing-water-effects
    """
    if stats is None or water_kind(shader.source_texture, True) != 'spray':
        return False
    src = resolve(source_rel)
    if src is None:
        return False
    out_rel = spray_texture(shader.source_texture)
    stats.setdefault('_water_spray_textures', {})[out_rel] = {'out_rel': out_rel, 'src': src}
    apply_water_look(shader, alpha_prop, 'spray', path_bytes(out_rel))
    _count(stats, 'water_sprays')
    return True
