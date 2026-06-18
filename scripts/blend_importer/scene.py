"""blend_importer.scene — Scene reset and render FPS helpers.

Provides ``reset_scene`` (ported verbatim from the original
``blend_importer.py`` lines 81-91) and a small ``configure_render_fps``
helper that consolidates the ``bpy.context.scene.render.fps = int(fps)``
assignment that the original main() did inline.
"""

import bpy


def reset_scene():
    """
    Wipe the current .blend scene clean.

    Selects and deletes all objects, then drops every orphan mesh, armature,
    material, and action data-block.  Mirrors the original implementation
    line-for-line so the importer's idempotency is preserved (running it
    twice on the same .blend yields the same result as running it once).
    """
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for block in list(bpy.data.meshes):
        bpy.data.meshes.remove(block)
    for block in list(bpy.data.armatures):
        bpy.data.armatures.remove(block)
    for block in list(bpy.data.materials):
        bpy.data.materials.remove(block)
    for block in list(bpy.data.actions):
        bpy.data.actions.remove(block)


def configure_render_fps(fps):
    """
    Set ``scene.render.fps`` to the nearest integer FPS.

    Blender's render.fps is an integer field; we round ``fps`` and store it.
    The original importer hardcoded ``int(fps)`` from ``detect_fps``; here
    we round so a bake_fps of 29.97 (NTSC video) doesn't truncate to 29.
    """
    bpy.context.scene.render.fps = int(round(fps))
