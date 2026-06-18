"""tests/python/test_materials.py — pytest tests for material node lookup.

Tests that ``_find_node_by_type`` correctly finds nodes by their ``type``
attribute rather than by name.  This is the fix for the bug where materials
had no textures when the Blender UI language was not English (the Principled
BSDF node label gets translated, breaking ``nodes.get("Principled BSDF")``).

Stubs out ``bpy`` so the module imports cleanly without Blender.
"""

import os
import sys
import types

import pytest

# Stub out bpy so blend_importer.materials imports cleanly.
if "bpy" not in sys.modules:
    bpy_stub = types.ModuleType("bpy")
    bpy_stub.data = types.SimpleNamespace(
        materials=types.SimpleNamespace(new=lambda **kw: None),
        images=types.SimpleNamespace(load=lambda p: None),
    )
    bpy_stub.types = types.SimpleNamespace(Material=object)
    sys.modules["bpy"] = bpy_stub

# Make scripts/ importable as the blend_importer package root.
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.normpath(os.path.join(_HERE, "..", "..", "scripts"))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from blend_importer.materials import _find_node_by_type, _set_input_safe


# ---------------------------------------------------------------------------
# Fake node tree for testing _find_node_by_type
# ---------------------------------------------------------------------------

class FakeNode:
    """A fake Blender node with a type and a name."""
    def __init__(self, node_type, name=""):
        self.type = node_type
        self.name = name
        self.inputs = {}


class FakeNodeTree:
    """A fake Blender node tree."""
    def __init__(self, nodes=None):
        self.nodes = nodes or []


# ---------------------------------------------------------------------------
# _find_node_by_type
# ---------------------------------------------------------------------------

def test_find_node_by_type_finds_principled_bsdf():
    """Should find a BSDF_PRINCIPLED node regardless of its name."""
    tree = FakeNodeTree([
        FakeNode("OUTPUT_MATERIAL", "Material Output"),
        FakeNode("BSDF_PRINCIPLED", "Principled BSDF"),
    ])
    result = _find_node_by_type(tree, 'BSDF_PRINCIPLED')
    assert result is not None
    assert result.type == 'BSDF_PRINCIPLED'


def test_find_node_by_type_works_with_translated_name():
    """Should find the BSDF even if its name is translated (e.g. Portuguese)."""
    tree = FakeNodeTree([
        FakeNode("OUTPUT_MATERIAL", "Saída de Material"),
        FakeNode("BSDF_PRINCIPLED", "BSDF Principado"),  # translated!
    ])
    result = _find_node_by_type(tree, 'BSDF_PRINCIPLED')
    assert result is not None
    assert result.type == 'BSDF_PRINCIPLED'
    # The name is the translated one, but we found it by type.
    assert result.name == "BSDF Principado"


def test_find_node_by_type_returns_none_if_not_found():
    """Should return None if no node of the type exists."""
    tree = FakeNodeTree([
        FakeNode("OUTPUT_MATERIAL", "Material Output"),
    ])
    result = _find_node_by_type(tree, 'BSDF_PRINCIPLED')
    assert result is None


def test_find_node_by_type_finds_output_material():
    """Should find an OUTPUT_MATERIAL node."""
    tree = FakeNodeTree([
        FakeNode("BSDF_PRINCIPLED", "Principled BSDF"),
        FakeNode("OUTPUT_MATERIAL", "Material Output"),
    ])
    result = _find_node_by_type(tree, 'OUTPUT_MATERIAL')
    assert result is not None
    assert result.type == 'OUTPUT_MATERIAL'


def test_find_node_by_type_empty_tree():
    """Should return None for an empty node tree."""
    tree = FakeNodeTree([])
    result = _find_node_by_type(tree, 'BSDF_PRINCIPLED')
    assert result is None


# ---------------------------------------------------------------------------
# _set_input_safe (handles Blender 3.x vs 4.x input name differences)
# ---------------------------------------------------------------------------

class FakeBSDF:
    """A fake BSDF node with a dict of inputs."""
    def __init__(self, input_names):
        self.inputs = {name: FakeInput() for name in input_names}


class FakeInput:
    """A fake BSDF input slot."""
    def __init__(self):
        self.default_value = None


def test_set_input_safe_blender4_emission_color():
    """Should set 'Emission Color' on Blender 4.x."""
    bsdf = FakeBSDF(["Base Color", "Emission Color", "Roughness"])
    result = _set_input_safe(bsdf, ["Emission Color", "Emission"], (1, 0, 0, 1))
    assert result is True
    assert bsdf.inputs["Emission Color"].default_value == (1, 0, 0, 1)


def test_set_input_safe_blender3_emission():
    """Should set 'Emission' on Blender 3.x (fallback name)."""
    bsdf = FakeBSDF(["Base Color", "Emission", "Roughness"])
    # "Emission Color" doesn't exist, but "Emission" does.
    result = _set_input_safe(bsdf, ["Emission Color", "Emission"], (1, 0, 0, 1))
    assert result is True
    assert bsdf.inputs["Emission"].default_value == (1, 0, 0, 1)


def test_set_input_safe_returns_false_if_none_exist():
    """Should return False if no input name matches."""
    bsdf = FakeBSDF(["Base Color", "Roughness"])
    result = _set_input_safe(bsdf, ["Emission Color", "Emission"], (1, 0, 0, 1))
    assert result is False


def test_set_input_safe_single_name():
    """Should work with a single name (not a fallback list)."""
    bsdf = FakeBSDF(["Roughness"])
    result = _set_input_safe(bsdf, ["Roughness"], 0.5)
    assert result is True
    assert bsdf.inputs["Roughness"].default_value == 0.5
