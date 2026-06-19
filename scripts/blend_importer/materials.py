"""blend_importer.materials — Principled BSDF material builder with texture loading.

Ports ``build_materials`` from the original ``blend_importer.py`` (lines
98-140), with three improvements:

1. The D3DX specular-power max (``128``) is read from
   ``config.D3DX_SPECULAR_POWER_MAX`` instead of being a literal.

2. **Texture path resolution.**  The .X file stores texture filenames as
   relative paths (often just the filename).  Textures are resolved
   against the .X source file's directory first, then cwd.

3. **Robust node lookup by type, not name.**  The original code did
   ``mat.node_tree.nodes.get("Principled BSDF")`` which fails when the
   Blender UI language is not English (the node label gets translated).
   This module finds the BSDF by ``node.type == 'BSDF_PRINCIPLED'``,
   which works regardless of language.  Input names are also checked
   for existence before assignment to handle Blender 3.x vs 4.x
   differences (e.g. ``"Emission"`` vs ``"Emission Color"``).
"""

import os
import logging

import bpy

from . import config

_log = logging.getLogger(__name__)


def resolve_texture_path(tex_file, source_x_path=None):
    """Resolve a texture filename to an absolute path on disk.

    Resolution order:
      1. Absolute path that exists.
      2. Relative to the .X source file's directory.
      3. Relative to the current working directory.
      4. None if not found.
    """
    if not tex_file:
        return None

    if os.path.isabs(tex_file):
        if os.path.isfile(tex_file):
            return os.path.abspath(tex_file)
        return None

    if source_x_path:
        x_dir = os.path.dirname(os.path.abspath(source_x_path))
        candidate = os.path.join(x_dir, tex_file)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

    if os.path.isfile(tex_file):
        return os.path.abspath(tex_file)

    return None


def _find_node_by_type(node_tree, node_type):
    """Find the first node of a given type in a node tree.

    More robust than ``nodes.get("Principled BSDF")`` because it works
    regardless of the Blender UI language (node labels get translated,
    but ``node.type`` is always the English enum value).
    """
    for node in node_tree.nodes:
        if node.type == node_type:
            return node
    return None


def _set_input_safe(bsdf, input_names, value):
    """Set a BSDF input by trying multiple possible names.

    Different Blender versions use different input names:
      - Blender 3.x: "Emission", "Specular"
      - Blender 4.x: "Emission Color", "Specular IOR Level"

    This tries each name in order and sets the first that exists.
    """
    for name in input_names:
        if name in bsdf.inputs:
            bsdf.inputs[name].default_value = value
            return True
    return False


def build_materials(mat_defs, source_x_path=None, emissive_strength=0.0):
    """Build Blender materials from the JSON mesh's ``materials`` list.

    Each material gets:
      - A Principled BSDF configured to approximate the D3DX MatD3D
        (diffuse color, alpha, roughness from specular power, emission).
      - An Image Texture node (if a texture filename is present) wired
        into the BSDF's Base Color input.
      - A Material Output node with the BSDF connected to Surface
        (ensures the material renders even if the default tree is
        incomplete).

    Parameters
    ----------
    mat_defs : list of dict
        Each dict has keys ``name``, ``diffuse`` ([r,g,b]), ``alpha``,
        ``specular`` ([r,g,b]), ``specular_power`` (0..128), ``emissive``
        ([r,g,b]), and ``texture`` (filesystem path or "").
    source_x_path : str, optional
        The filesystem path of the source .X file.  Used to resolve
        relative texture paths.

    Returns
    -------
    list of bpy.types.Material
    """
    mats = []
    found_count = 0
    missing_count = 0

    for md in mat_defs:
        name = md.get("name") or "Material"
        mat = bpy.data.materials.new(name=name)
        mat.use_nodes = True

        # Find the Principled BSDF by TYPE (not name) for language-robustness.
        bsdf = _find_node_by_type(mat.node_tree, 'BSDF_PRINCIPLED')

        if not bsdf:
            # If there's no Principled BSDF (e.g. the default node tree
            # was stripped), create one.
            bsdf = mat.node_tree.nodes.new("ShaderNodeBsdfPrincipled")
            _log.debug("  Material '%s': created new Principled BSDF node", name)

        # Ensure a Material Output exists and the BSDF is connected to it.
        # This guarantees the material renders even in stripped node trees.
        output = _find_node_by_type(mat.node_tree, 'OUTPUT_MATERIAL')
        if not output:
            output = mat.node_tree.nodes.new("ShaderNodeOutputMaterial")
        # Connect BSDF -> Output Surface (re-link to be safe).
        try:
            mat.node_tree.links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])
        except KeyError:
            pass  # shouldn't happen, but don't crash

        # --- Configure BSDF inputs (with version-safe name fallbacks) ---
        d = md.get("diffuse", [0.8, 0.8, 0.8])
        alpha = md.get("alpha", 1.0)

        # Base Color (same name in all Blender versions).
        if "Base Color" in bsdf.inputs:
            bsdf.inputs["Base Color"].default_value = (d[0], d[1], d[2], alpha)

        # Alpha.
        _set_input_safe(bsdf, ["Alpha"], alpha)
        if alpha < 0.999:
            mat.blend_method = 'BLEND'
            if hasattr(mat, "use_screen_refraction"):
                mat.use_screen_refraction = True

        # Roughness from specular power.
        spec_power = md.get("specular_power", 0.0)
        roughness = 1.0 - min(spec_power / config.D3DX_SPECULAR_POWER_MAX, 1.0)
        _set_input_safe(bsdf, ["Roughness"], roughness)

        # Emission (name changed between Blender 3.x and 4.x).
        # Old anime games (DirectX 9) commonly use high Emissive values
        # as 'baked lighting' to make characters visible without dynamic
        # light sources.  In Blender Material Preview, this adds to the
        # HDRI lighting, making the model appear ~2x brighter than intended.
        # By default, Emission Strength = 0 (color preserved but not applied).
        # Use --emissive-strength N to restore the original game look.
        em = md.get("emissive", [0, 0, 0])
        _set_input_safe(bsdf, ["Emission Color", "Emission"],
                        (em[0], em[1], em[2], 1.0))
        _set_input_safe(bsdf, ["Emission Strength"], emissive_strength)

        # --- Texture (Image Texture node wired into Base Color) ---
        tex_file = md.get("texture", "")
        if tex_file:
            tex_node = mat.node_tree.nodes.new("ShaderNodeTexImage")
            tex_node.label = tex_file  # always set label for identification
            resolved = resolve_texture_path(tex_file, source_x_path)

            if resolved:
                try:
                    img = bpy.data.images.load(resolved)
                    tex_node.image = img
                    # Ensure the image uses sRGB colorspace (default for
                    # most image types, but explicit is safer).
                    try:
                        img.colorspace_settings.name = 'sRGB'
                    except (AttributeError, TypeError):
                        pass  # older Blender or unusual image type
                    # PACK the image into the .blend file so it travels
                    # with the file.  Without this, the .blend only stores
                    # a filepath reference, and when the .blend is opened on
                    # another machine (or the textures are moved), the images
                    # are "missing" — the material shows up empty in the
                    # Properties panel even though the node tree has the
                    # Image Texture node.
                    try:
                        img.pack()
                    except (RuntimeError, AttributeError) as e:
                        _log.warning("  Material '%s': could not pack image '%s': %s",
                                     name, tex_file, e)
                    found_count += 1
                    _log.info("  Material '%s': loaded+packed texture '%s' from %s",
                              name, tex_file, resolved)
                except RuntimeError as e:
                    _log.warning("  Material '%s': could not load texture '%s' "
                                 "(resolved to '%s'): %s",
                                 name, tex_file, resolved, e)
                    missing_count += 1
            else:
                x_dir = (os.path.dirname(os.path.abspath(source_x_path))
                         if source_x_path else os.getcwd())
                _log.warning(
                    "  Material '%s': texture '%s' not found. "
                    "Looked in: '%s' (X source dir) and '%s' (cwd). "
                    "Creating placeholder node.",
                    name, tex_file, x_dir, os.getcwd())
                missing_count += 1

            # ALWAYS connect the Image Texture to Base Color, even if the
            # image didn't load (the node exists as a placeholder so the
            # user can manually assign a texture later).
            try:
                mat.node_tree.links.new(
                    tex_node.outputs["Color"],
                    bsdf.inputs["Base Color"]
                )
            except KeyError:
                _log.warning("  Material '%s': could not link texture to BSDF",
                             name)

        mats.append(mat)

    if found_count or missing_count:
        _log.info("Materials: %d textures loaded, %d missing",
                  found_count, missing_count)
    return mats
