# X2Blend

Opens DirectX `.X` models in Blender: mesh, UVs, materials, armature, weights and every
animation set. Built for *Higurashi Daybreak*, whose fighters ship as `.X` with 124 to 137
animation sets.

The animation is not baked. Each key in the file becomes one key on an F-curve, at the same
time, and the curves evaluate to the pose the game computes between keys.

It is pure Python: no Wine, no D3DX and nothing to compile.

## Install

Build the extension, then install it from Blender (`Edit > Preferences > Get Extensions >
Install from Disk`). Blender 5.0 or newer.

```bash
blender --command extension build --source-dir x2blend --output-dir build
```

Then use `File > Import > DirectX / Daybreak (.x, .dat)`. Pick a character file,
`data/<character>/NN.dat`, to get the fighter with its weapon. Pick a `.X` to get that model alone.

## Command line

```bash
./cli.py info data/keiichi/01.dat
```

```bash
./cli.py convert data/keiichi/01.dat out.blend --scale 0.01
```

`info` needs no Blender. `convert` runs a headless Blender, found on `PATH` or through
`BLENDER`.

## What you get

- **Armature:** one bone per frame that does something. Biped's unnamed, scaled helper boxes
  are left out. The rest pose is the T-pose from the `SkinWeights` offsets, and bone Y runs
  along the bone.
- **Meshes:** the file's faces, UVs and normals (as custom normals). Vertices that only exist
  to carry a second UV or normal are merged, and vertices with different weights never are.
- **Materials:** the texture on a Principled BSDF, packed in the `.blend`. Materials ask for
  `.bmp`, and the matching `.cnv` beside the model is decoded directly. The game's diffuse,
  emissive, power and specular stay as `x_*` custom properties, because the game shades with
  its own toon effect, which is not reproduced.
- **Weapon:** a character file names one `.X` and one weapon id, and `data/item/NNN.dat` names the
  weapon's models. Each becomes a mesh parented to the armature and weighted to the hand that holds
  it, so it follows every action: item model 0 on `Bip01_Prop1`, model 1 on `Bip01_Prop2`, as the game
  starts a round. Objects are named `weapon0 40`, with the index in `x_item_model`. Models from index
  2 up exist but start hidden, since no hand holds them by default.
- **Actions:** one per animation set, kept with a fake user and named by the game's index:
  `000 Anim-124` is the set the game calls 0, the last in the file. Pick one in the Action
  Editor. A set that names only some bones leaves the others alone, as the game does.
- **Time:** frame = tick / 80, with the scene at 60 fps. Shipped keys all fall on multiples of
  80 ticks at 4800 per second, so they sit on whole frames and play in real time. One game
  update (0.016 s) is 0.96 of a frame.
- **Units:** the file's own, with the scale on the armature object.

## Why no bake is needed

The math is at the top of [`x2blend/build.py`](x2blend/build.py). In short, the F-curve value is
`constant * key * constant`, and with rigid constants each channel stays linear in the key's
components. The game lerps those components and normalizes the quaternion; a `LINEAR` F-curve
and Blender's own normalization do exactly that. Nothing is sampled, so nothing is lost.

The shipped files themselves were exported at 60 Hz, so the curves have a key every 1/60 s.
Faithful here does not mean sparse.

## Layout

| Path | What |
|---|---|
| `x2blend/xfile.py` | Reads `.X` txt, bin, tzip and bzip. No Blender dependency |
| `x2blend/cnv.py` | Reads the game's `.cnv` images |
| `x2blend/character.py` | Reads `NN.dat` and `item/NNN.dat`, as far as they name models |
| `x2blend/build.py` | Builds the Blender data |
| `x2blend/ui.py` | The import menu |
| `cli.py` | The command line |
| `tests/` | See below |

## Tests

```bash
./tests/run.sh
```

`tests/test_xfile.py` needs neither Blender nor the game. The rest needs a
[daybreak-decomp](https://github.com/Vmarcelo49/daybreak-remake) checkout with the game installed,
found beside this repository or through `DAYBREAK_DECOMP`. Without it, `run.sh` says `NOT RUN` and
exits with 77, which is not a pass. Game files are never copied here.

The reference is `tests/oracle/x_oracle.cpp`, which dumps a model through that project's
`xmodel.h`, a separate parser. After a save and reload, `tests/test_import.py` checks:

- the reader against the oracle: frames, corners, offsets, sets, key times and key values;
- every curve has the file's key count, and `frame * 80` equals the file's tick exactly;
- Blender's evaluated pose against the file's keys, at every key and halfway between keys;
- skinned vertices against the game's palette blend, at sampled times;
- normals, UVs, face materials, weights and packed images.

`tests/test_weapon.py` imports `keiichi/00.dat`, `01.dat` and `02.dat`, and compares the posed
weapon vertices against the game's placement (hand bone world, weapon frame world, vertex), at
sampled sets and times.

`tests/render_compare.py` renders from the same camera as a `daybreak --out` capture and
reports the silhouette overlap. `tests/smoke_corpus.py` imports every shipped model and lists
the warnings, without measuring accuracy.

## Limits

- Moves can change what a hand holds. That is logic in the executable (`FUN_00434a70`), with no data
  in these files, so a swap during an action is not imported. Use the hidden models by hand.
- The animation sets inside a few weapon models (`00`, `09`, `13`, `24`, `52`) are not imported.
- Only model 0 of three Keiichi weapons is measured. The other character files are only imported.
- Only `keiichi/00.X` passes the full accuracy checks. On `akane/00.X`, `satoko/01.X` and
  `rena_D/00.X` the reader, keys and poses pass, but the mesh and deformation checks do not
  run cleanly yet: they assume one mesh per named frame and no dropped faces.
- Some fighters give one bone different offsets in different meshes (a warning says so). The
  first offset wins, so the other mesh may deform slightly wrong: 0.11 units was measured on
  `satoko/01.X`, cause not confirmed.
- Faces with a repeated vertex or a bad index are dropped, with a warning.
- A bone keeps only the rigid part of a scaled rest matrix, with a warning.
- Matrix keys with shear lose it, as they do in the game's own decomposition.
