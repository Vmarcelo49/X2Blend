# XtoBlend

High-precision converter for DirectX 9 `.x` models to Blender `.blend` files, optimized for legacy game assets like those from *Higurashi Daybreak kai* (2008).

## Features

- **Complete Asset Conversion**: Frame hierarchies, meshes, materials, textures, armatures, skinning, and animations
- **Shift-JIS Support**: Properly decodes MS932-encoded texture paths in `.x` files
- **Accurate Skinning**: Preserves bone hierarchies, bind poses, and vertex weights with mathematical precision
- **60 FPS Animation Baking**: Samples animation data at 60 FPS for flawless keyframe matching in Blender
- **Unskinned Mesh Handling**: Correctly parents rigid attachments to bones as constraints
- **Material Pipeline**: Generates Blender BSDF materials with texture mapping
- **Interactive Viewer**: Includes `viewer.exe` for previewing models and animations via DirectX 9
- **Validation Tools**: Scripts to verify bone matrix accuracy against DirectX references

## Architecture

The pipeline runs in two stages due to Windows-only DirectX SDK dependencies:

```bash
DirectX .x Model
         ↓ (Wine)
     x2blend.exe  ← Stage 1: C++ Exporter
         ↓
    model.json    ← Intermediate format
         ↓
blend_importer.py ← Stage 2: Blender Python Importer
         ↓
      output.blend
```

**Stage 1** (`x2blend.exe`): Loads models in a headless Direct3D 9 device under Wine, samples animations at 60 FPS, and exports structured JSON.

**Stage 2** (`blend_importer.py`): Runs in Blender's Python environment (`bpy`) to reconstruct armatures, apply skinning, and keyframe animations from the JSON data.

## Requirements

- **Linux host** with:
  - `x86_64-w64-mingw32-g++` (MinGW cross-compiler)
  - `wine` (to run Windows binaries)
  - `blender` (system installation)
  - Optional: Python virtual environment with `bpy` (`pip install bpy`)

## Usage

### 1. Build

```bash
./build.sh
```

Produces `build/x2blend.exe` and `build/viewer.exe`.

### 2. Convert Model

```bash
./x2blend.sh input.x output.blend
```

Example:
```bash
./x2blend.sh assets/test_bone_anim.x output.blend
```

The script auto-detects available `bpy` or falls back to system Blender.

### 3. View Models

```bash
wine build/viewer.exe model.x
```

Controls:
- **Orbit**: Left mouse drag
- **Zoom**: Mouse wheel
- **Cycle Animations**: Spacebar or ←/→ arrows
- **Reset Camera**: `R`

### Advanced: Manual Stages

```bash
# Stage 1: .x → JSON
wine build/x2blend.exe model.x model.json

# Stage 2: JSON → .blend (using native bpy)
.blend_venv/bin/python scripts/blend_importer.py model.json output.blend

# Or using system Blender
blender --background --python scripts/blend_importer.py -- model.json output.blend
```

### Validation

Check animation accuracy:
```bash
blender --background --python scripts/verify_animation_poses.py -- model.json output.blend
```

Reports translation (< 10⁻⁴ units) and rotation (< 0.1°) error bounds.

## Notes

- Tested with Wine 9.0, Blender 5.1, and x86_64-w64-mingw32-g++ 14.2.0
- Generated `.blend` files include NLA tracks for easy animation switching
- Armature uses Stick display mode for bone visibility
- Root objects are scaled to 0.01 specifically for higurashi daybreak models
- Most development work performed with Gemini 3.5 Flash and Claude Sonnet 4.6