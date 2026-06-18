"""blend_importer.animation — F-curve keying (baked + TRS-remap) + decimation.

This is the most behaviour-rich module in the package.  It ports
``build_animations`` from the original ``blend_importer.py`` (lines 365-522)
with three substantive additions:

1. **Bake FPS comes from the JSON ``meta.bake_fps``** (plumbed in by the
   caller) instead of the dead ``detect_fps()`` heuristic.  ``detect_fps``
   is removed entirely.

2. **Per-channel TRS-remap fallback.**  When a channel has no
   ``baked_keys`` (the C++ side ran with ``--no-bake``), its
   ``translation_keys`` / ``rotation_keys`` / ``scale_keys`` are written
   directly to the F-curves.  The C++ side already converted these to a
   rest-relative form, so no further math is needed on the Python side —
   just a quaternion component reorder, because the C++ ``XQuaternion`` is
   ``{x, y, z, w}`` and Blender's ``rotation_quaternion`` F-curve indices
   are 0=w, 1=x, 2=y, 3=z.

3. **Post-build F-curve decimation.**  Two modes are supported via
   ``decimate_mode``: ``"ratio"`` (uses ``bpy.ops.graph.decimate`` — needs
   a graph editor context, fragile in headless mode) and ``"error"``
   (manual Ramer-Douglas-Peucker with an absolute error bound — works
   headless).  Default is no decimation.

A fourth, smaller addition is the **Python-side static-channel skip**
(defense in depth alongside the C++-side static-bone optimization in
``animation_baker.cpp``): if a channel's baked matrices are all identical
within ``config.STATIC_CHANNEL_TOLERANCE`` (1e-7), only the first frame's
key is written.
"""

import math
import logging

import bpy
import mathutils

from . import config
from .math_utils import mat4_to_mathutils, pose_local_matrix

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_animations(anim_list, arm_obj, bone_name_map, nodes_data,
                     bake_fps=None, decimate=None, decimate_mode=None):
    """
    Create NLA-ready Actions on the armature for each XAnimation.

    Parameters
    ----------
    anim_list : list of dict
        The JSON ``animations`` array.
    arm_obj : bpy.types.Object
        The armature object the actions will be attached to.
    bone_name_map : dict
        ``{node_index: blender_bone_name}`` (from ``build_armature``).
    nodes_data : list of dict
        The JSON ``nodes`` array (used to map channel target_node → index).
    bake_fps : float, optional
        Sample rate used by the C++ baker.  When None, falls back to
        ``config.FALLBACK_BAKE_FPS`` (60.0).  Should come from
        ``model["meta"]["bake_fps"]``.
    decimate : float or None
        If not None, run post-build F-curve decimation.  Interpretation
        depends on ``decimate_mode``: a ratio in [0, 1] for ``"ratio"``
        mode (remove fraction of keyframes) or an absolute error
        tolerance for ``"error"`` mode (RDP threshold on the value axis).
    decimate_mode : str or None
        ``"ratio"`` (default; uses ``bpy.ops.graph.decimate`` — requires
        a valid context) or ``"error"`` (manual RDP — headless-safe).
    """
    if not arm_obj or not anim_list:
        return

    if bake_fps is None:
        bake_fps = config.FALLBACK_BAKE_FPS
    if decimate_mode is None:
        decimate_mode = config.DECIMATE_MODE

    name_to_idx = {nd["name"]: i for i, nd in enumerate(nodes_data)}

    for anim_data in anim_list:
        anim_name = anim_data.get("name") or "Action"
        action = bpy.data.actions.new(name=anim_name)

        fcurves_container = _setup_action_container(action, arm_obj)

        _build_action_channels(anim_data, arm_obj, bone_name_map,
                               name_to_idx, bake_fps, fcurves_container)

        # Decimate BEFORE the NLA push so the action is still the active
        # action (RATIO mode's bpy.ops.graph.decimate needs that).
        if decimate is not None:
            _decimate_action(action, fcurves_container, decimate,
                             decimate_mode, anim_name)

        _push_to_nla(arm_obj, anim_name, action)


# ---------------------------------------------------------------------------
# Action container setup (legacy + slotted API compat)
# ---------------------------------------------------------------------------

def _setup_action_container(action, arm_obj):
    """
    Bind ``action`` to ``arm_obj``'s animation data and return the
    container its F-curves should be written to.

    Preserves the original blend_importer.py logic (lines 384-407):
    ``hasattr(action, "fcurves")`` is always True on ``bpy.types.Action``,
    so the legacy branch is taken in practice.  The slotted-API branch is
    retained verbatim for parity with the original and as a forward-compat
    hook should a future Blender version default new actions to slotted
    mode.
    """
    if not arm_obj.animation_data:
        arm_obj.animation_data_create()
    arm_obj.animation_data.action = action

    if hasattr(action, "fcurves"):
        # Legacy API — action.fcurves is the writable collection.
        return action

    # Slotted API (Blender >= 4.4) — dead branch under current Blender
    # because hasattr() above is always True.  Kept for parity.
    if not action.slots:
        slot = action.slots.new(id_type='OBJECT', name=arm_obj.name)
    else:
        slot = action.slots[0]
    arm_obj.animation_data.action_slot = slot
    if not action.layers:
        layer = action.layers.new(name="Base Layer")
    else:
        layer = action.layers[0]
    if not layer.strips:
        strip = layer.strips.new(type='KEYFRAME')
    else:
        strip = layer.strips[0]
    return strip.channelbag(slot, ensure=True)


def _create_fcurve(container, data_path, index, group_name):
    """
    Create an F-curve, falling back to no ``action_group`` kwarg if the
    Blender version rejects it (some slotted-API code paths don't accept
    action_group on fcurves.new).
    """
    try:
        return container.fcurves.new(
            data_path=data_path,
            index=index,
            action_group=group_name
        )
    except TypeError:
        return container.fcurves.new(
            data_path=data_path,
            index=index
        )


# ---------------------------------------------------------------------------
# Per-channel keyframe writing (baked + TRS-remap)
# ---------------------------------------------------------------------------

def _build_action_channels(anim_data, arm_obj, bone_name_map, name_to_idx,
                           bake_fps, fcurves_container):
    """
    Write keyframes for every channel of one animation.

    Per-channel decision:
      - If ``baked_keys`` is non-empty, use the baked path (world-matrix →
        pose-local → decompose → F-curve).  Defense-in-depth static-channel
        skip: if every baked matrix is identical to the first within
        ``STATIC_CHANNEL_TOLERANCE``, only the first frame's key is
        written.
      - Else if any of ``translation_keys`` / ``rotation_keys`` /
        ``scale_keys`` is non-empty, use the TRS-remap path (write keys
        directly, reordering rotation components from C++ (x,y,z,w) to
        Blender (w,x,y,z)).

    The baked path uses a shared ``frames_map`` so that a bone's
    pose-local matrix can reference its parent's world matrix at the same
    frame.  If a parent isn't in the map (e.g. mixed baked/TRS action),
    the no-parent formula ``M_rest^-1 @ M_world`` is used as a fallback.
    """
    # --- First pass: collect baked frames and detect static channels ---
    frames_map = {}            # frame_num -> {bone_name: mathutils.Matrix}
    channel_static = {}        # bone_name -> bool
    baked_bone_names = set()

    for ch in anim_data.get("channels", []):
        bone_name = _resolve_bone_name(ch, name_to_idx, bone_name_map)
        if not bone_name:
            continue

        baked_keys = ch.get("baked_keys", [])
        if not baked_keys:
            continue

        baked_bone_names.add(bone_name)

        # Defense-in-depth static-channel skip.
        channel_static[bone_name] = _is_static_channel(baked_keys)

        for kf in baked_keys:
            f_num = int(round(kf["t"] * bake_fps))
            frames_map.setdefault(f_num, {})[bone_name] = mat4_to_mathutils(kf["m"])

    # --- Cache rest matrices and parent relations for baked bones ---
    rest_mats = {}
    rest_parents = {}
    parent_names = {}
    for bname in baked_bone_names:
        bone = arm_obj.data.bones.get(bname)
        if bone:
            rest_mats[bname] = bone.matrix_local
            parent_names[bname] = bone.parent.name if bone.parent else None
            if bone.parent:
                rest_parents[bname] = bone.parent.matrix_local

    # F-curve cache so each bone gets exactly one F-curve set.
    bone_fcurves = {}

    def get_or_create_fcurves(bname):
        if bname not in bone_fcurves:
            data_path_prefix = f'pose.bones["{bname}"]'
            bone_fcurves[bname] = {
                "location": [
                    _create_fcurve(fcurves_container,
                                   f"{data_path_prefix}.location", i, bname)
                    for i in range(3)
                ],
                "rotation_quaternion": [
                    _create_fcurve(fcurves_container,
                                   f"{data_path_prefix}.rotation_quaternion", i, bname)
                    for i in range(4)
                ],
                "scale": [
                    _create_fcurve(fcurves_container,
                                   f"{data_path_prefix}.scale", i, bname)
                    for i in range(3)
                ],
            }
        return bone_fcurves[bname]

    first_frame_num = min(frames_map.keys()) if frames_map else None

    # --- Second pass: per-channel keyframe writing ---
    baked_count = 0
    trs_count = 0
    for ch in anim_data.get("channels", []):
        bone_name = _resolve_bone_name(ch, name_to_idx, bone_name_map)
        if not bone_name:
            continue

        baked_keys = ch.get("baked_keys", [])
        if baked_keys:
            baked_count += 1
            _write_baked_channel(
                bone_name, baked_keys, frames_map, channel_static,
                first_frame_num, rest_mats, rest_parents, parent_names,
                get_or_create_fcurves
            )
        else:
            t_keys = ch.get("translation_keys", [])
            r_keys = ch.get("rotation_keys", [])
            s_keys = ch.get("scale_keys", [])
            if not t_keys and not r_keys and not s_keys:
                continue  # nothing to animate
            trs_count += 1
            _write_trs_channel(bone_name, t_keys, r_keys, s_keys,
                               bake_fps, get_or_create_fcurves)

    _log.debug("Action '%s': %d baked channels, %d TRS-remap channels.",
               anim_data.get("name") or "Action", baked_count, trs_count)


def _resolve_bone_name(channel, name_to_idx, bone_name_map):
    """Map a channel's ``target_node`` to its Blender bone name."""
    target = channel.get("target_node", "")
    ni = name_to_idx.get(target, -1)
    return bone_name_map.get(ni)


def _is_static_channel(baked_keys):
    """
    Return True if every baked matrix is identical to the first within
    ``STATIC_CHANNEL_TOLERANCE`` (1e-7 across all 16 floats).  Matches the
    C++ static-bone optimization tolerance (animation_baker.cpp
    ``k_staticTol``).
    """
    if len(baked_keys) < 2:
        return False  # 0 or 1 keys — nothing to skip
    first = mat4_to_mathutils(baked_keys[0]["m"])
    for kf in baked_keys[1:]:
        other = mat4_to_mathutils(kf["m"])
        if not _matrices_equal(first, other, config.STATIC_CHANNEL_TOLERANCE):
            return False
    return True


def _matrices_equal(a, b, tol):
    """Per-element matrix equality within ``tol``."""
    for r in range(4):
        for c in range(4):
            if abs(a[r][c] - b[r][c]) > tol:
                return False
    return True


def _write_baked_channel(bone_name, baked_keys, frames_map, channel_static,
                         first_frame_num, rest_mats, rest_parents,
                         parent_names, get_or_create_fcurves):
    """Baked path: world-matrix → pose-local → decompose → F-curve."""
    fcs = get_or_create_fcurves(bone_name)
    is_static = channel_static.get(bone_name, False)
    M_rest = rest_mats.get(bone_name)
    if not M_rest:
        return
    parent_name = parent_names.get(bone_name)
    M_rest_parent = rest_parents.get(bone_name)

    for f_num in sorted(frames_map.keys()):
        # Static-channel skip: write only the first frame's key.
        if is_static and f_num != first_frame_num:
            continue
        if bone_name not in frames_map[f_num]:
            continue

        M_world = frames_map[f_num][bone_name]
        # Parent lookup: if the parent's world matrix is in frames_map at
        # this frame, use the parent formula; otherwise fall back to the
        # no-parent formula (matches the original blend_importer.py
        # behavior for root bones).
        if parent_name and parent_name in frames_map.get(f_num, {}):
            M_world_parent = frames_map[f_num][parent_name]
            M_pose_local = pose_local_matrix(
                M_world, M_rest, M_world_parent, M_rest_parent
            )
        else:
            M_pose_local = pose_local_matrix(M_world, M_rest)

        loc, rot, scale = M_pose_local.decompose()

        for i in range(3):
            fcs["location"][i].keyframe_points.insert(f_num, loc[i])
        for i in range(4):
            fcs["rotation_quaternion"][i].keyframe_points.insert(f_num, rot[i])
        for i in range(3):
            fcs["scale"][i].keyframe_points.insert(f_num, scale[i])


def _write_trs_channel(bone_name, t_keys, r_keys, s_keys, bake_fps,
                       get_or_create_fcurves):
    """
    TRS-remap path: write C++-derived rest-relative TRS keys directly.

    The C++ side already converted the keys to a rest-relative form (see
    ``animation_baker.cpp::extractKeyframedKeys``); the Python side just
    converts times to frame numbers via ``int(round(t * bake_fps))`` and
    reorders rotation components.

    Rotation component reorder
    --------------------------
    The C++ ``XQuaternion`` is ``{x, y, z, w}`` and the JSON exporter
    (``json_exporter.cpp::writeAnimations``) emits it as
    ``jvec4(q.x, q.y, q.z, q.w)`` — i.e. ``[x, y, z, w]`` in the JSON.
    Blender's ``rotation_quaternion`` F-curve indices are 0=w, 1=x, 2=y,
    3=z, so we must reorder:

        F-curve index 0  ←  w  (JSON index 3)
        F-curve index 1  ←  x  (JSON index 0)
        F-curve index 2  ←  y  (JSON index 1)
        F-curve index 3  ←  z  (JSON index 2)
    """
    fcs = get_or_create_fcurves(bone_name)

    # Translation keys: [x, y, z] → location[0..2].
    for kf in t_keys:
        f_num = int(round(kf["t"] * bake_fps))
        v = kf["v"]
        for i in range(3):
            fcs["location"][i].keyframe_points.insert(f_num, v[i])

    # Rotation keys: [x, y, z, w] → rotation_quaternion[w, x, y, z].
    for kf in r_keys:
        f_num = int(round(kf["t"] * bake_fps))
        v = kf["v"]
        x, y, z, w = v[0], v[1], v[2], v[3]
        fcs["rotation_quaternion"][0].keyframe_points.insert(f_num, w)
        fcs["rotation_quaternion"][1].keyframe_points.insert(f_num, x)
        fcs["rotation_quaternion"][2].keyframe_points.insert(f_num, y)
        fcs["rotation_quaternion"][3].keyframe_points.insert(f_num, z)

    # Scale keys: [x, y, z] → scale[0..2].
    for kf in s_keys:
        f_num = int(round(kf["t"] * bake_fps))
        v = kf["v"]
        for i in range(3):
            fcs["scale"][i].keyframe_points.insert(f_num, v[i])


# ---------------------------------------------------------------------------
# Decimation
# ---------------------------------------------------------------------------

def _decimate_action(action, fcurves_container, value, mode, anim_name):
    """
    Apply decimation to all F-curves in ``fcurves_container``.

    ``mode`` is case-insensitive: ``"ratio"`` uses
    ``bpy.ops.graph.decimate`` (requires a graph editor context — fragile
    in headless mode); ``"error"`` uses manual Ramer-Douglas-Peucker with
    an absolute error bound on each F-curve's value axis (headless-safe).
    """
    fcurves = (
        fcurves_container.fcurves
        if hasattr(fcurves_container, "fcurves")
        else action.fcurves
    )

    mode_upper = (mode or "").upper()
    if mode_upper == "RATIO":
        _decimate_ratio(fcurves, value, anim_name)
    elif mode_upper == "ERROR":
        _decimate_error(fcurves, value, anim_name)
    else:
        _log.warning("[decimate] Unknown mode '%s' for action '%s'; "
                     "skipping. Use 'ratio' or 'error'.", mode, anim_name)


def _decimate_ratio(fcurves, ratio, anim_name):
    """
    Use ``bpy.ops.graph.decimate`` with the given remove-ratio (0..1).

    .. note::

       This operator requires a valid graph editor context.  In
       ``blender --background`` mode the graph editor area usually does
       not exist, so the call typically raises ``RuntimeError``.  We log
       a warning and continue (no decimation happens).  Use
       ``--decimate-mode error`` for headless-safe decimation.
    """
    if ratio <= 0.0:
        return
    if ratio > 1.0:
        ratio = 1.0

    # Select all F-curves — the operator acts on the selected set only.
    for fc in fcurves:
        fc.select = True

    try:
        bpy.ops.graph.decimate(mode='RATIO', ratio=ratio)
        _log.info("[decimate] RATIO decimation applied to action '%s' "
                  "(ratio=%g).", anim_name, ratio)
    except (RuntimeError, AttributeError, TypeError) as e:
        _log.warning("[decimate] bpy.ops.graph.decimate failed for action "
                     "'%s' (%s). This is expected in headless mode. Use "
                     "--decimate-mode error for headless-safe decimation.",
                     anim_name, e)
    finally:
        for fc in fcurves:
            fc.select = False


def _decimate_error(fcurves, epsilon, anim_name):
    """
    Manual Ramer-Douglas-Peucker decimation with absolute error bound.

    For each F-curve, runs RDP on its ``(frame, value)`` keyframe sequence
    with the given ``epsilon`` (in the F-curve's value space).  Keyframe
    points that RDP does not keep are removed via
    ``keyframe_points.remove(kfp, fast=True)``.

    Works headless (no operator / context required).
    """
    if epsilon <= 0.0:
        return

    total_before = 0
    total_after = 0
    for fc in fcurves:
        kfps = list(fc.keyframe_points)
        total_before += len(kfps)
        if len(kfps) < 3:
            total_after += len(kfps)
            continue

        # Snapshot (frame, value) for each keyframe_point.
        points = [(kfp.co.x, kfp.co.y) for kfp in kfps]
        kept = _rdp(points, epsilon)
        kept_set = set(kept)

        # Remove keyframe_points whose (frame, value) is not in the
        # kept set.  Removing while iterating is safe because we
        # snapshot the list first.
        for kfp in kfps:
            if (kfp.co.x, kfp.co.y) not in kept_set:
                fc.keyframe_points.remove(kfp, fast=True)
        fc.keyframe_points.update()
        total_after += len(kept)

    _log.info("[decimate] action '%s': %d -> %d keyframes (epsilon=%g)",
              anim_name, total_before, total_after, epsilon)


def _rdp(points, epsilon):
    """
    Ramer-Douglas-Peucker on a list of ``(x, y)`` tuples.  Returns the
    reduced list of points (preserving first and last).
    """
    if len(points) < 3:
        return list(points)

    start, end = points[0], points[-1]
    max_dist = 0.0
    max_idx = 0
    for i in range(1, len(points) - 1):
        d = _perp_distance(points[i], start, end)
        if d > max_dist:
            max_dist = d
            max_idx = i

    if max_dist > epsilon:
        left = _rdp(points[:max_idx + 1], epsilon)
        right = _rdp(points[max_idx:], epsilon)
        # Drop the duplicated junction point.
        return left[:-1] + right
    else:
        return [start, end]


def _perp_distance(p, a, b):
    """
    Perpendicular distance from point ``p`` to the line through ``a`` and
    ``b`` (2D).  If ``a == b``, returns the Euclidean distance to ``a``.
    """
    if a[0] == b[0] and a[1] == b[1]:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    # 2D cross-product magnitude / base length = perpendicular height.
    area = abs((b[0] - a[0]) * (a[1] - p[1]) - (a[0] - p[0]) * (b[1] - a[1]))
    base_len = math.hypot(b[0] - a[0], b[1] - a[1])
    return area / base_len


# ---------------------------------------------------------------------------
# NLA packaging
# ---------------------------------------------------------------------------

def _push_to_nla(arm_obj, anim_name, action):
    """
    Push the action onto a new NLA track so multiple animations can
    co-exist on the same armature.  Mirrors the original
    blend_importer.py lines 517-522.
    """
    if not arm_obj.animation_data:
        return
    track = arm_obj.animation_data.nla_tracks.new()
    track.name = anim_name
    track.strips.new(anim_name, 1, action)
    arm_obj.animation_data.action = None
