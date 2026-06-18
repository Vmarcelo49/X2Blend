"""blend_importer.materials — Principled BSDF material builder.

Ports ``build_materials`` from the original ``blend_importer.py`` (lines
98-140).  The only intentional change is that the D3DX specular-power max
(``128``) is now read from ``config.D3DX_SPECULAR_POWER_MAX`` instead of
being a literal in the roughness formula.
"""

import os
import logging

import bpy

from . import config

_log = logging.getLogger(__name__)


def build_materials(mat_defs):
    """
    Build Blender materials from the JSON mesh's ``materials`` list.

    Parameters
    ----------
    mat_defs : list of dict
        Each dict has keys ``name``, ``diffuse`` ([r,g,b]), ``alpha``,
        ``specular`` ([r,g,b]), ``specular_power`` (0..128), ``emissive``
        ([r,g,b]), and ``texture`` (filesystem path or "").

    Returns
    -------
    list of bpy.types.Material
        One material per input dict, with a Principled BSDF configured to
        approximate the D3DX MatD3D, plus an optional ImageTexture node
        wired into Base Color.
    """
    mats = []
    for md in mat_defs:
        name = md.get("name") or "Material"
        mat = bpy.data.materials.new(name=name)
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        if bsdf:
            d = md["diffuse"]
            alpha = md.get("alpha", 1.0)
            bsdf.inputs["Base Color"].default_value = (d[0], d[1], d[2], alpha)
            if "Alpha" in bsdf.inputs:
                bsdf.inputs["Alpha"].default_value = alpha
            if alpha < 0.999:
                mat.blend_method = 'BLEND'
                if hasattr(mat, "use_screen_refraction"):
                    mat.use_screen_refraction = True
            spec = md.get("specular", [0, 0, 0])
            _ = spec  # currently unused beyond plumbed-through presence
            spec_power = md.get("specular_power", 0.0)
            # Map specular_power (0..D3DX_SPECULAR_POWER_MAX) to roughness
            # (1..0).  Higher specular power -> sharper highlight -> lower
            # roughness.
            roughness = 1.0 - min(spec_power / config.D3DX_SPECULAR_POWER_MAX, 1.0)
            bsdf.inputs["Roughness"].default_value = roughness
            em = md.get("emissive", [0, 0, 0])
            bsdf.inputs["Emission Color"].default_value = (em[0], em[1], em[2], 1.0)
            # Texture
            tex_file = md.get("texture", "")
            if tex_file:
                tex_node = mat.node_tree.nodes.new("ShaderNodeTexImage")
                if os.path.isfile(tex_file):
                    try:
                        tex_node.image = bpy.data.images.load(tex_file)
                    except RuntimeError as e:
                        # Image load can fail for corrupt / unsupported
                        # files; preserve the placeholder slot and warn.
                        _log.warning("Could not load texture '%s': %s", tex_file, e)
                        tex_node.label = tex_file
                else:
                    # Create a placeholder so the slot exists.
                    _log.debug("Texture path '%s' not found on disk; "
                               "creating placeholder node.", tex_file)
                    tex_node.label = tex_file
                mat.node_tree.links.new(
                    tex_node.outputs["Color"],
                    bsdf.inputs["Base Color"]
                )
        mats.append(mat)
    return mats
