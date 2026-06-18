"""blend_importer.main — CLI entry for the .blend builder.

Parses CLI args, reads the JSON model, calls the builder modules in
order, and saves the .blend.

Supports two invocation styles:

  1. ``python -m blend_importer <args>`` (requires ``bpy`` pip-installed,
     or run inside Blender's bundled Python).
  2. ``blender --background --python scripts/blend_importer/main.py -- <args>``
     (the original ``--`` separator is stripped exactly as in the
     original ``blend_importer.py``).

The module manipulates ``sys.path`` at import time so that the
``blend_importer`` package can be located under both invocations.
"""

import os
import sys
import json
import argparse
import logging

# ---------------------------------------------------------------------------
# sys.path bootstrap — allow `blender --python .../main.py` to find the
# blend_importer package (whose parent is the `scripts/` directory).
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.dirname(_HERE)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

# ---------------------------------------------------------------------------
# bpy import — works both as `blender --python` and as a pip package.
# This is the only module in the package that guards the bpy import;
# other modules assume the caller (here) has already ensured availability.
# ---------------------------------------------------------------------------
try:
    import bpy
    import mathutils  # noqa: F401  (imported for parity with the original)
except ImportError:
    sys.exit("[blend_importer] ERROR: 'bpy' not found. "
             "Install with: pip install bpy "
             "(or run via: blender --background --python ...)")

# Use absolute imports so the module works both when imported as
# `blend_importer.main` (via `python -m blend_importer`) and when run
# directly as `__main__` (via `blender --python .../main.py`).
from blend_importer import config
from blend_importer.scene import reset_scene, configure_render_fps
from blend_importer.armature import build_armature
from blend_importer.mesh import build_mesh_object
from blend_importer.animation import build_animations
from blend_importer.bone_axis import detect_bone_axis, apply_axis_permutation

_log = logging.getLogger("blend_importer")


# ---------------------------------------------------------------------------
# CLI parsing
# ---------------------------------------------------------------------------

def _build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="blend_importer",
        description="Build a .blend from an X2Blend JSON model.",
    )
    parser.add_argument("model_json",
                        help="Path to the input model.json produced by x2blend.exe")
    parser.add_argument("output_blend",
                        help="Path to the output .blend file")
    parser.add_argument(
        "--root-scale", type=float, default=config.DEFAULT_ROOT_SCALE,
        help=("Scale applied to every root object (default: "
              f"{config.DEFAULT_ROOT_SCALE}; the original hardcoded 0.01 "
              "for Higurashi Daybreak)."),
    )
    parser.add_argument(
        "--bone-tail-length", type=float,
        default=config.DEFAULT_BONE_TAIL_LENGTH,
        help=("Fallback bone tail length when the hierarchy can't determine "
              "one (single root bone, degenerate skeletons).  By default, "
              "tail lengths are computed from the bone hierarchy: a bone "
              "with children gets 80%% of the distance to its nearest child; "
              "a leaf bone gets 50%% of its parent's length.  This flag is "
              "the last-resort fallback (default: "
              f"{config.DEFAULT_BONE_TAIL_LENGTH})."),
    )
    parser.add_argument(
        "--max-influences", type=int,
        default=config.DEFAULT_MAX_INFLUENCES,
        help=("Max bone influences per vertex (default: "
              f"{config.DEFAULT_MAX_INFLUENCES}; informational — actual "
              "capping happens C++-side in MeshExtractor)."),
    )
    parser.add_argument(
        "--decimate", type=float, default=None,
        help=("Enable F-curve decimation. The value is interpreted as a "
              "remove-ratio in [0, 1] when --decimate-mode=ratio, or as an "
              "absolute error tolerance when --decimate-mode=error. "
              "Default: no decimation."),
    )
    parser.add_argument(
        "--decimate-mode", choices=["ratio", "error"],
        default=config.DECIMATE_MODE.lower(),
        help=("'ratio' uses bpy.ops.graph.decimate (requires a valid "
              "graph editor context — fragile in headless mode); "
              "'error' uses a manual Ramer-Douglas-Peucker pass with an "
              f"absolute error bound (headless-safe). Default: "
              f"{config.DECIMATE_MODE.lower()!r}."),
    )
    parser.add_argument(
        "--no-decimate", action="store_true",
        help="Explicitly disable decimation (overrides --decimate).",
    )
    parser.add_argument(
        "--visual-tails", action="store_true",
        help=("Use child-directed bone tails (point towards the nearest "
              "child bone) for visually-correct bone orientation.  WARNING: "
              "this BREAKS SKINNING — the armature modifier's inverse-bind "
              "no longer matches the DirectX inverseBindMatrix, so vertices "
              "will deform incorrectly.  Animation F-curves are unaffected. "
              "Use this mode only for hierarchy debugging.  Default: off "
              "(skinning-accurate mode, tails use the bind matrix's local Y "
              "axis which may point oddly but preserves skinning)."),
    )
    parser.add_argument(
        "--bone-axis", choices=["auto", "x", "y", "z"],
        default="auto",
        help=("Which local axis the .X file uses as the bone direction. "
              "'auto' detects it from the hierarchy (recommended — 3ds Max "
              "Biped uses X, Maya uses Y, some rigs use Z).  When the "
              "detected axis is not Y, a consistent axis permutation is "
              "applied to ALL data (matrices, vertices, normals, winding) "
              "so that both skinning AND visually-correct bone tails are "
              "achieved.  'y' skips the permutation (use if the .X file "
              "already uses Y, e.g. Maya-authored assets).  Default: auto."),
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARN", "ERROR"],
        help="Logging verbosity (default: INFO).  Use DEBUG to see bone "
             "axis-alignment diagnostics.",
    )
    return parser


def _strip_blender_separator(argv):
    """
    Strip the ``--`` separator that ``blender --python`` injects, plus
    the script-name prefix, so the result is suitable for argparse.

    When called as ``blender --background --python main.py -- arg1 arg2``,
    ``sys.argv`` looks like ``['blender', ..., '--', 'main.py', 'arg1', 'arg2']``
    (the exact prefix depends on Blender's invocation).  We slice from
    the first ``--`` onward.

    When called as ``python -m blend_importer arg1 arg2``, ``sys.argv``
    is ``['/path/to/__main__.py', 'arg1', 'arg2']`` — no ``--``, so we
    just drop the first element (the script path).
    """
    if "--" in argv:
        idx = argv.index("--")
        return argv[idx + 1:]
    return argv[1:]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def main():
    """CLI entry point. Parses args, builds the .blend, saves."""
    argv = _strip_blender_separator(sys.argv)
    args = _build_arg_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="[blend_importer] %(levelname)s %(name)s: %(message)s",
    )

    json_path = args.model_json
    blend_path = args.output_blend

    _log.info("Loading JSON: %s", json_path)
    with open(json_path, "r", encoding="utf-8") as f:
        model = json.load(f)

    # --- Meta block (new in the refactor; replaces detect_fps) ---
    meta = model.get("meta", {})
    bake_fps = float(meta.get("bake_fps", config.FALLBACK_BAKE_FPS))
    bake_mode = meta.get("bake_mode", "unknown")
    source_file = meta.get("source_file", "(unknown)")
    max_influences_meta = int(meta.get("max_influences",
                                       config.DEFAULT_MAX_INFLUENCES))
    x2blend_version = meta.get("x2blend_version", "(unknown)")

    _log.info("X2Blend version: %s", x2blend_version)
    _log.info("Source file: %s", source_file)
    _log.info("Bake mode: %s", bake_mode)
    _log.info("Bake FPS: %g", bake_fps)
    _log.info("Max influences (meta): %d", max_influences_meta)

    # Prefer the CLI --max-influences when it differs from the default;
    # otherwise honor whatever the meta block reports.  This is purely
    # informational on the Python side (the actual vertex-weight cap
    # happens in MeshExtractor on the C++ side).
    if args.max_influences == config.DEFAULT_MAX_INFLUENCES:
        max_influences = max_influences_meta
    else:
        max_influences = args.max_influences

    nodes_data = model.get("nodes", [])
    meshes_data = model.get("meshes", [])
    anims_data = model.get("animations", [])

    _log.info("Model: %d nodes, %d meshes, %d animations",
              len(nodes_data), len(meshes_data), len(anims_data))

    # --- 0. Bone axis detection + permutation ---
    # The .X file may use X (3ds Max Biped), Y (Maya), or Z as the bone
    # direction axis.  Blender requires Y.  If the detected axis is not Y,
    # apply a consistent axis permutation to ALL data so that both skinning
    # AND visually-correct bone tails are achieved.
    if args.bone_axis == "auto":
        bone_axis = detect_bone_axis(model)
    else:
        bone_axis = args.bone_axis
        _log.info("Bone axis (forced): %s", bone_axis)

    if bone_axis != "y":
        apply_axis_permutation(model, bone_axis)
        # Re-read the permuted data
        nodes_data = model.get("nodes", [])
        meshes_data = model.get("meshes", [])
        anims_data = model.get("animations", [])

    # --- 1. Reset scene ---
    _log.info("Resetting scene...")
    reset_scene()

    # --- 2. Armature (needed before meshes for parenting) ---
    has_bones = any(nd.get("is_bone") for nd in nodes_data)
    arm_obj = None
    bone_name_map = {}
    world_mats = []
    if has_bones:
        tail_mode = "visual (child-directed)" if args.visual_tails else "skinning-accurate"
        _log.info("Building armature (bone_tail_length=%g, tails=%s)...",
                  args.bone_tail_length, tail_mode)
        arm_obj, bone_name_map, world_mats = build_armature(
            nodes_data, meshes_data, args.bone_tail_length,
            visual_tails=args.visual_tails,
        )
    else:
        _log.info("No bones found; skipping armature build.")

    # --- 3. Meshes ---
    _log.info("Building %d mesh(es) (max_influences=%d)...",
              len(meshes_data), max_influences)
    for idx, mesh_data in enumerate(meshes_data):
        build_mesh_object(mesh_data, arm_obj, nodes_data, idx, max_influences,
                          source_x_path=source_file)

    # --- 4. Scale adjustment ---
    # Apply root scale to all root objects (objects with no parent).
    # The original hardcoded 0.01 for Higurashi Daybreak; the CLI now
    # exposes this via --root-scale (default 1.0 = no rescale).
    if abs(args.root_scale - 1.0) > 1e-9:
        _log.info("Applying root scale: %g", args.root_scale)
        for obj in bpy.context.scene.objects:
            if obj.parent is None:
                obj.scale = (args.root_scale, args.root_scale, args.root_scale)
    else:
        _log.debug("Root scale is 1.0; no rescaling applied.")

    # --- 5. Animations ---
    if anims_data:
        _log.info("Building %d animation(s) at %g FPS...",
                  len(anims_data), bake_fps)
        configure_render_fps(bake_fps)

        decimate_value = None if args.no_decimate else args.decimate
        build_animations(
            anims_data, arm_obj, bone_name_map, nodes_data,
            bake_fps=bake_fps,
            decimate=decimate_value,
            decimate_mode=args.decimate_mode,
        )
    else:
        _log.info("No animations to build.")

    # --- 6. Save ---
    _log.info("Saving blend file: %s", blend_path)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(blend_path))
    print(f"[blend_importer] Done: {blend_path}")


if __name__ == "__main__":
    main()
