"""A Morrowind shape without an alpha property renders opaque, whatever its material alpha."""

import asset_convert.nif.pyffi_monkey_patch as pyffi_monkey_patch
from asset_convert.nif.nif_converter_morrowind import opaque_unblended_shapes
from pyffi.formats.nif import NifFormat


def _shape(root, alpha, blended):
    """Attach a converted shape whose lighting shader has `alpha`, blended or not."""
    shape = NifFormat.NiTriShape()
    shader = NifFormat.BSLightingShaderProperty()
    shader.alpha = alpha
    shape.bs_properties[0] = shader
    if blended:
        shape.bs_properties[1] = NifFormat.NiAlphaProperty()
    root.add_child(shape)
    return shader


def test_unblended_alpha_raised_blended_kept():
    """Alpha 0 with no alpha property becomes 1; an authored blend keeps its alpha."""
    assert pyffi_monkey_patch
    data = NifFormat.Data()
    root = NifFormat.BSFadeNode()
    data.roots = [root]
    wall = _shape(root, 0.0, blended=False)
    glass = _shape(root, 0.5, blended=True)
    assert opaque_unblended_shapes(data) == 1
    assert wall.alpha == 1.0
    assert glass.alpha == 0.5
