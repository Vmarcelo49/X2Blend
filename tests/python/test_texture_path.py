"""tests/python/test_texture_path.py — pytest tests for texture path resolution.

Tests the ``resolve_texture_path`` function in ``blend_importer.materials``
without requiring Blender.  Stubs out ``bpy`` so the module imports cleanly.
"""

import os
import sys
import tempfile
import types

import pytest

# Stub out bpy and mathutils so blend_importer.materials imports cleanly.
# We only need resolve_texture_path, which doesn't use bpy, but the module
# does `import bpy` at the top level.
if "bpy" not in sys.modules:
    bpy_stub = types.ModuleType("bpy")
    bpy_stub.data = types.SimpleNamespace(materials=types.SimpleNamespace(new=lambda **kw: None))
    bpy_stub.types = types.SimpleNamespace(Material=object)
    sys.modules["bpy"] = bpy_stub

# Make scripts/ importable as the blend_importer package root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.normpath(os.path.join(_HERE, "..", "..", "scripts"))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from blend_importer.materials import resolve_texture_path


# ---------------------------------------------------------------------------
# resolve_texture_path
# ---------------------------------------------------------------------------

def test_empty_returns_none():
    """An empty texture path should return None."""
    assert resolve_texture_path("") is None
    assert resolve_texture_path(None) is None


def test_absolute_path_existing():
    """An absolute path that exists should be returned (normalized)."""
    with tempfile.NamedTemporaryFile(suffix=".bmp", delete=False) as f:
        f.write(b"BM")  # minimal BMP header
        path = f.name
    try:
        result = resolve_texture_path(path)
        assert result is not None
        assert os.path.abspath(path) == result
    finally:
        os.unlink(path)


def test_absolute_path_missing_returns_none():
    """An absolute path that doesn't exist should return None."""
    result = resolve_texture_path("/nonexistent/path/texture.bmp")
    assert result is None


def test_relative_path_resolved_against_source_x_dir():
    """A relative texture filename should be found in the .X source dir."""
    with tempfile.TemporaryDirectory() as x_dir:
        # Create the .X file and the texture in the same dir.
        x_path = os.path.join(x_dir, "model.x")
        with open(x_path, "w") as f:
            f.write("xof 0303txt 0032")
        tex_path = os.path.join(x_dir, "texture.bmp")
        with open(tex_path, "wb") as f:
            f.write(b"BM")

        # tex_file is just the filename; source_x_path is the full .X path.
        result = resolve_texture_path("texture.bmp", source_x_path=x_path)
        assert result is not None
        assert os.path.abspath(tex_path) == result


def test_relative_path_falls_back_to_cwd():
    """If not in the .X dir, a relative path should fall back to cwd."""
    with tempfile.TemporaryDirectory() as x_dir:
        x_path = os.path.join(x_dir, "model.x")
        with open(x_path, "w") as f:
            f.write("xof 0303txt 0032")
        # Create the texture in cwd (not in x_dir)
        with tempfile.NamedTemporaryFile(suffix=".bmp", delete=False, dir=".") as f:
            cwd_tex_path = f.name
        try:
            tex_name = os.path.basename(cwd_tex_path)
            result = resolve_texture_path(tex_name, source_x_path=x_path)
            assert result is not None
            assert os.path.abspath(cwd_tex_path) == result
        finally:
            os.unlink(cwd_tex_path)


def test_relative_path_missing_returns_none():
    """A relative path that doesn't exist anywhere should return None."""
    with tempfile.TemporaryDirectory() as x_dir:
        x_path = os.path.join(x_dir, "model.x")
        with open(x_path, "w") as f:
            f.write("xof 0303txt 0032")

        result = resolve_texture_path("nonexistent.bmp", source_x_path=x_path)
        assert result is None


def test_no_source_x_path_falls_back_to_cwd():
    """Without source_x_path, relative paths resolve against cwd."""
    with tempfile.NamedTemporaryFile(suffix=".bmp", delete=False, dir=".") as f:
        cwd_tex_path = f.name
    try:
        tex_name = os.path.basename(cwd_tex_path)
        result = resolve_texture_path(tex_name, source_x_path=None)
        assert result is not None
        assert os.path.abspath(cwd_tex_path) == result
    finally:
        os.unlink(cwd_tex_path)


def test_shift_jis_named_texture_found():
    """A texture with a Japanese (Shift-JIS decoded) name should be found
    if it exists in the .X source directory.  This is the real-world case
    for Higurashi Daybreak (e.g. スカート.bmp)."""
    with tempfile.TemporaryDirectory() as x_dir:
        x_path = os.path.join(x_dir, "00.X")
        with open(x_path, "wb") as f:
            f.write(b"xof 0303bin 0032")
        # Create a texture with a Japanese name (the kind the .X stores)
        tex_name = "スカート.bmp"
        tex_path = os.path.join(x_dir, tex_name)
        with open(tex_path, "wb") as f:
            f.write(b"BM")

        result = resolve_texture_path(tex_name, source_x_path=x_path)
        assert result is not None
        assert os.path.abspath(tex_path) == result


def test_subdirectory_relative_path():
    """A relative path with a subdirectory (e.g. 'textures/foo.bmp') should
    resolve against the .X source directory."""
    with tempfile.TemporaryDirectory() as x_dir:
        x_path = os.path.join(x_dir, "model.x")
        with open(x_path, "w") as f:
            f.write("xof 0303txt 0032")
        # Create textures subdir + file
        tex_subdir = os.path.join(x_dir, "textures")
        os.makedirs(tex_subdir)
        tex_path = os.path.join(tex_subdir, "foo.bmp")
        with open(tex_path, "wb") as f:
            f.write(b"BM")

        result = resolve_texture_path("textures/foo.bmp", source_x_path=x_path)
        assert result is not None
        assert os.path.abspath(tex_path) == result
