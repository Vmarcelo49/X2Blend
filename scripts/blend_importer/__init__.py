"""blend_importer — Blender headless (.blend) builder package.

A pure-Python (with `bpy`/`mathutils` at runtime) reimplementation of the
original 590-line ``scripts/blend_importer.py`` monolith, split into focused
modules:

  - ``config``         — named constants (no more magic numbers)
  - ``math_utils``     — matrix / roll helpers (bpy-free, unit-testable)
  - ``bone_axis``      — bone axis detection + permutation (bpy-free, unit-testable)
  - ``scene``          — scene reset + render FPS
  - ``materials``      — Principled BSDF material builder
  - ``armature``       — bones from inverse-bind matrices
  - ``mesh``           — mesh objects with skinning / parenting
  - ``animation``      — baked + TRS-remap F-curve keying, decimation
  - ``main``           — CLI entry (``python -m blend_importer ...``)

The package is intended to run inside Blender's bundled Python (4.x ships
CPython 3.11+).  ``math_utils.py`` and ``bone_axis.py`` intentionally avoid
importing ``bpy``/``mathutils`` at module top, so their helpers can be
unit-tested without a Blender install.

The ``main`` entry point is imported lazily (only when accessed) so that
the pure-Python modules (``math_utils``, ``bone_axis``) can be imported
without ``bpy`` being installed.
"""


def __getattr__(name):
    """Lazy import to avoid requiring bpy when only math_utils/bone_axis
    are needed (e.g. during unit tests)."""
    if name == "main":
        from .main import main
        return main
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["main"]
