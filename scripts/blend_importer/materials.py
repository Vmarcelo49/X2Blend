"""blend_importer.materials — Principled BSDF material builder with texture loading.

Ports ``build_materials`` from the original ``blend_importer.py`` (lines
98-140), with two improvements:

1. The D3DX specular-power max (``128``) is read from
   ``config.D3DX_SPECULAR_POWER_MAX`` instead of being a literal.

2. **Texture path resolution.**  The .X file stores texture filenames as
   relative paths (often just the filename, e.g. ``服.bmp`` — Shift-JIS
   decoded to UTF-8 by the C++ side).  The original importer did
   ``os.path.isfile(tex_file)`` which searched only the *current working
   directory* — so textures in the .X file's own folder were never found
   unless the user happened to run Blender from that folder.

   The fix: resolve relative texture paths against the .X source file's
   directory first (from ``meta.source_file``), then fall back to the
   current working directory.  This matches how D3DX itself resolves
   textures at load time.
"""

import os
import logging

import bpy

from . import config

_log = logging.getLogger(__name__)


def resolve_texture_path(tex_file, source_x_path=None):
    """Resolve a texture filename to an absolute path on disk.

    The .X file typically stores texture filenames as bare names (e.g.
    ``服.bmp``) or relative paths.  D3DX resolves these against the .X
    file's own directory at load time.  This function replicates that
    behavior so textures placed alongside the .X file are found
    automatically.

    Resolution order:
      1. If ``tex_file`` is an absolute path that exists, use it.
      2. If ``source_x_path`` is provided, try
         ``os.path.dirname(source_x_path) / tex_file``.
      3. Try ``tex_file`` relative to the current working directory
         (preserves the old fallback behavior for backward compat).
      4. Return None if nothing is found.

    Parameters
    ----------
    tex_file : str
        The texture filename/path from the JSON material.  May be a bare
        filename, a relative path, or an absolute path.
    source_x_path : str, optional
        The filesystem path of the source .X file (from
        ``model["meta"]["source_file"]``).  Used to resolve relative
        texture paths.

    Returns
    -------
    str or None
        The resolved absolute path if the file exists, else None.
    """
    if not tex_file:
        return None

    # 1. Absolute path — use directly if it exists.
    if os.path.isabs(tex_file):
        if os.path.isfile(tex_file):
            return os.path.abspath(tex_file)
        return None

    # 2. Try relative to the .X source file's directory.
    if source_x_path:
        x_dir = os.path.dirname(os.path.abspath(source_x_path))
        candidate = os.path.join(x_dir, tex_file)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)

    # 3. Fallback: try relative to the current working directory.
    if os.path.isfile(tex_file):
        return os.path.abspath(tex_file)

    return None


def build_materials(mat_defs, source_x_path=None):
    """Build Blender materials from the JSON mesh's ``materials`` list.

    Parameters
    ----------
    mat_defs : list of dict
        Each dict has keys ``name``, ``diffuse`` ([r,g,b]), ``alpha``,
        ``specular`` ([r,g,b]), ``specular_power`` (0..128), ``emissive``
        ([r,g,b]), and ``texture`` (filesystem path or "").
    source_x_path : str, optional
        The filesystem path of the source .X file (from
        ``model["meta"]["source_file"]``).  Used to resolve relative
        texture paths — textures placed alongside the .X file are found
        automatically.

    Returns
    -------
    list of bpy.types.Material
        One material per input dict, with a Principled BSDF configured to
        approximate the D3DX MatD3D, plus an optional ImageTexture node
        wired into Base Color.
    """
    mats = []
    found_count = 0
    missing_count = 0

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

            # Texture — resolve path against the .X source directory first.
            tex_file = md.get("texture", "")
            if tex_file:
                tex_node = mat.node_tree.nodes.new("ShaderNodeTexImage")
                resolved = resolve_texture_path(tex_file, source_x_path)

                if resolved:
                    try:
                        tex_node.image = bpy.data.images.load(resolved)
                        found_count += 1
                        _log.info("  Material '%s': loaded texture '%s' from %s",
                                  name, tex_file, resolved)
                    except RuntimeError as e:
                        # Image load can fail for corrupt / unsupported
                        # files; preserve the placeholder slot and warn.
                        _log.warning("  Material '%s': could not load texture '%s' "
                                     "(resolved to '%s'): %s",
                                     name, tex_file, resolved, e)
                        tex_node.label = tex_file
                        missing_count += 1
                else:
                    # Create a placeholder so the slot exists, and log where
                    # we looked so the user can fix the missing file.
                    x_dir = (os.path.dirname(os.path.abspath(source_x_path))
                             if source_x_path else os.getcwd())
                    _log.warning(
                        "  Material '%s': texture '%s' not found. "
                        "Looked in: '%s' (X source dir) and '%s' (cwd). "
                        "Creating placeholder node.",
                        name, tex_file, x_dir, os.getcwd())
                    tex_node.label = tex_file
                    missing_count += 1

                mat.node_tree.links.new(
                    tex_node.outputs["Color"],
                    bsdf.inputs["Base Color"]
                )
        mats.append(mat)

    if found_count or missing_count:
        _log.info("Materials: %d textures loaded, %d missing",
                  found_count, missing_count)
    return mats
