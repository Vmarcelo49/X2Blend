# DirectX .X Format — Research Notes

> Research conducted to diagnose why the `--no-bake` (TRS-remap) path
> produces disfigured models, and to inform the correct fix.

## 1. Format history

- **Introduced**: DirectX 2.0 (1996), text encoding only.
- **Binary encoding**: Added in DirectX 3.0 (1996). Same template-driven
  structure as text, just binary-tokenized.
- **Peak usage**: DirectX 9 era (~2002–2010). D3DX9 provided
  `D3DXLoadMeshHierarchyFromX` which was the standard loader.
- **Decline**: DirectX 10+ dropped D3DX. The .X format is effectively
  deprecated; modern engines use glTF/FBX.

## 2. 2007-era authoring tools

The Higurashi Daybreak models (Twilight Frontier, August 2006) were
almost certainly authored in **3ds Max** with the **Panda DirectX
Exporter** — by far the most common .X authoring pipeline in Japanese
game development at the time. Other tools in use circa 2007:

| Tool | Host app | Notes |
|---|---|---|
| **Panda DirectX Exporter** | 3ds Max | Most popular; still maintained. Supports skinning, animations, embedded textures. |
| **Microsoft MakeMDL** | 3ds Max / XSI | Older, from the FS2004/FSX SDK. |
| **kwxport** | 3ds Max | Alternative exporter, open-source. |
| **Blender DirectX Exporter** | Blender 2.49–2.6x | Community Python script (by "micheus" and others). |
| **Maya .X Exporter** | Maya | Third-party plugin only; Maya has no built-in .X support (per Autodesk). |
| **XSI .X Exporter** | Softimage XSI | Via a plugin; XSI was popular in Japanese studios. |

**Key implication**: Panda (and most exporters) writes the frame hierarchy
with intermediate "helper" frames (FrameTransformMatrix nodes that aren't
bones). These helpers carry the TRS animation keys in .X files. When
Blender imports the skeleton, it collapses the hierarchy to actual bones
only — the helper frames' transforms get baked into the bone rest pose.
This parent-chain mismatch is the #1 reason TRS-remap fails (see §4).

## 3. Binary .X specification

From the Paul Bourke / cgdev spec:

### Header (16 bytes, compulsory)
```
Offset  Size  Field
0       4     Magic: "xof " (0x78 0x6F 0x66 0x20)
4       2     Major version (e.g. 03)
6       2     Minor version (e.g. 02)
8       4     Format type: "txt " | "bin " | "tzip" | "bzip"
12      4     Float size: "0032" (32-bit) | "0064" (64-bit)
```

### Binary tokens
After the header, the binary stream uses token records. Each record
starts with a 4-byte token type ID:

| Token | Meaning |
|---|---|
| `0x0` | NAME (string: DWORD length + bytes) |
| `0x1` | DATA (string: DWORD length + bytes) |
| `0x2` | INT (DWORD value) |
| `0x3` | FLOAT (float value) |
| `0x4` | STRING (DWORD length + bytes) |
| `0x5` | TEMPLATE reference |
| `0x6` | WORD |
| `0x7` | DWORD |
| `0x8` | GUID (16 bytes: 4+2+2+8) |
| `0x9` | Open brace `{` |
| `0xA` | Close brace `}` |
| `0xB` | Open bracket `[` |
| `0xC` | Close bracket `]` |
| `0xD` | Integer array |
| `0xE` | Float array |
| `0xF` | Object reference |

The binary format is a direct serialization of the text format's parse
tree — same templates, same data, just tokenized. A binary .X file can
be converted to text by expanding the tokens (and vice versa).

### The current X2Blend code only handles TEXT format
The `readAndPreprocessXFile` function checks for the `xof 0303txt` /
`xof 0302txt` header and only injects templates into text files. Binary
.x files (header `xof 0303bin`) are passed through without
preprocessing — which works because Wine's `d3dxof` parser handles
binary natively, but the template-injection workaround for missing
mesh-extension templates only applies to text format.

## 4. The AnimationKey template and why TRS-remap fails

### AnimationKey template
```
template AnimationKey {
  <10DD46A8-775B-11CF-8F52-0040333594A3>
  DWORD keyType;    // 0=rotation, 1=scale, 2=translation, 3=matrix, 4=quaternion
  DWORD nKeys;
  array TimedFloatKeys keys[nKeys];
}

template TimedFloatKeys {
  DWORD time;
  DWORD nValues;
  array float values[nValues];
}
```

- `keyType=0` (rotation): each key has 4 floats = quaternion (x, y, z, w).
- `keyType=1` (scale): each key has 3 floats.
- `keyType=2` (translation): each key has 3 floats.
- `keyType=3` (matrix): each key has 16 floats (4×4 row-major).
- `keyType=4`: rarely used.

### Keys are LOCAL (parent-relative), not world
The D3DX documentation and the GameDev tutorial ("Working with the
DirectX .X File Format and Animation in DirectX 9.0") both confirm:
**the animation keys REPLACE the frame's `TransformationMatrix` at
evaluation time.** The frame's `TransformationMatrix` is local to its
parent. So:

```
frame.WorldMatrix(t) = frame.TransformationMatrix(t) × parent.WorldMatrix(t)
```

where `TransformationMatrix(t)` is composed from the S/R/T keys at time t.

### Why the original TRS-remap was wrong

The original C++ `extractKeyframedKeys` (now replaced by `extractKeyTimeBakedKeys`) did a **component-wise** rest-relative conversion:
- `tRel = R_rest⁻¹ ⊗ (t_key − t_rest)`
- `rRel = R_rest⁻¹ ⊗ r_key`
- `sRel = s_key ÷ s_rest`

This treats the key values as if they're in the same space as the rest
pose and can be independently converted to Blender's `chan_mat`. But
**Blender's `pose_bone.matrix_basis` (chan_mat) is NOT a simple
rest-relative TRS delta** — it's the local transform that, when composed
with the parent's posed matrix and the bone's `parent_mat`, produces the
correct armature-world matrix:

```
pose.matrix = parent.pose.matrix × bone.parent_mat × chan_mat
```

where `bone.parent_mat = parent.matrix_local⁻¹ × this.matrix_local`.

Solving for `chan_mat`:
```
chan_mat = bone.parent_mat⁻¹ × parent.pose.matrix⁻¹ × world.matrix
         = this.matrix_local⁻¹ × parent.matrix_local × parent.pose.matrix⁻¹ × world.matrix
```

This is the **baked-path formula** (`M_rest⁻¹ · M_rest_parent ·
M_world_parent⁻¹ · M_world`), and it requires the parent's ACTUAL posed
world matrix at each keyframe time — not just the key value.

The component-wise TRS-remap misses the `parent.matrix_local ×
parent.pose.matrix⁻¹` term entirely. When the parent has a non-identity
rest local transform (which is always the case for real skeletons), the
error accumulates down the chain, producing the "totally disfigured"
result the user observed.

### The correct no-bake approach: key-time baking

The fix is to evaluate the D3DX animation controller at each bone's
**original keyframe times** (not at fixed 60 FPS), walk the hierarchy to
get the correct world matrices, and apply the same formula as the baked
path. This:

1. **Preserves the original keyframe times** (the union of all bones'
   key times, not 60 FPS sampling).
2. **Is mathematically exact** (uses the same formula as the baked path).
3. **Is much sparser than 60 FPS baking** (typically 20–50 key times vs
   300+ for a 5-second animation).
4. **Produces `bakedKeys`** (world matrices), so the Python side needs
   no changes — it already handles `bakedKeys` with the correct formula.

The trade-off: this is not "true" keyframe preservation (each bone gets
keys at the union of all times, not just its own), but it's dramatically
sparser than 60 FPS and mathematically correct. A per-bone approach
(each bone keeps only its own key times) would require Python-side parent
transform interpolation — possible but complex, and deferred.

## 5. References

- Paul Bourke, "Direct-X File Format" —
  https://paulbourke.net/dataformats/directx/
- cgdev, "X File Format DirectX File Format Specification" —
  https://www.cgdev.net/axe/x-file.html
- Microsoft Learn, "X file format reference (DirectX 9)" —
  https://learn.microsoft.com/en-us/windows/win32/direct3d9/dx9-graphics-reference-x-file-format
- GameDev.net, "Working with the DirectX .X File Format and Animation in DirectX 9.0" —
  https://gamedev.net/tutorials/programming/general-and-gameplay-programming/working-with-the-directx-x-file-format-and-animation-in-directx-90-r2079/
- GameDev StackExchange, "How do I apply skeletal animation from a .x (Direct X) file?" —
  https://gamedev.stackexchange.com/questions/35451/
- Wikipedia, "Higurashi Daybreak" —
  https://en.wikipedia.org/wiki/Higurashi_Daybreak
- FSDeveloper Wiki, "Exporting from 3DSMax using Pandasoft plugin" —
  https://www.fsdeveloper.com/wiki/index.php/Exporting_from_3DSMax_using_Pandasoft_plugin
