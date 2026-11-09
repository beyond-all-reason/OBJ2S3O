# OBJ2S3O
Tools for Spring/Recoil model conversion, inspection, and ambient occlusion (AO)
baking. Includes the standalone **BAR GLB AO Baker** add-on for Blender 5.2+.

## GLB ambient occlusion in Blender

Use [BAR GLB AO Baker](blender_glb_ao/README.md) to bake vertex AO into
**`COLOR_0` alpha** and generate RGBA ground AO plates. It supports existing GLBs
and models authored in Blender, including walk-cycle averaging and skinned
animation. Blender provides the dependencies; xNormal, S3O Kit, and Skeletor
are not required for this add-on.

From this repository's root, build the installable ZIP:

```powershell
python package_glb_ao.py
```

Install `dist/bar_glb_ao_v0.1.2.zip` through Blender's **Edit → Preferences →
Add-ons → menu → Install from Disk**, enable **BAR GLB AO Baker**, and open
the **GLB AO** tab in the 3D View sidebar (`N`). Restart Blender after updating
an existing installation, then rebake to apply the current settings.

1. Choose **File Axes** and **Load GLB for AO**, or select an existing Blender
   model and click **Use Active Hierarchy**. Recoil GLBs exported with **+Y Up**
   disabled use **Recoil / Blender Z Up**.
2. Choose the unit type, ray settings, and optional walk range. Use **Mark
   Explode** on pieces that should be separated while baking vertex AO.
3. Click **Bake Vertex AO** and inspect the result with **AO Only** or
   **Shaded AO**. **Restore Materials** returns the original materials.
4. Use **Update Source GLB** to update the loaded file's color alpha while
   preserving its geometry, RGB, UVs, hierarchy, and animations. It retains
   the first original as `<model>.glb.ao-backup`. Use **Export New GLB** for
   models authored or edited in Blender.
5. For a ground plate, choose a footprint and 64×64 or 128×128 resolution,
   then click **Bake AO Plate PNG**. This saves a black RGBA PNG with darkness
   in alpha and a companion JSON file with the decal dimensions.

Key conventions:

- **Ray Samples** defaults to **1024** and cannot be set lower. The setting
  applies to both vertex and plate AO; larger values round up to multiples
  of 128 rays.
- Vertices within **0.001 mesh-local Blender units** share averaged AO, even
  across UV seams, hard normals, and material boundaries. Grouping uses
  Euclidean distance on rest positions and is transitive within each mesh.
  Positions and mesh topology are preserved.
- Vertex alpha stores brightness (`0` occluded, `1` unoccluded). Plate alpha
  stores darkness. Gain, bias, and minimum brightness are applied after
  vertex AO averaging.
- Walk sampling excludes the closing frame, which should repeat the first
  pose. **Every Frame** includes intermediate poses; **Keyframes Only** is
  available for faster sampling.
- Footprint inputs use **16 world units per square**. Zero estimates from
  model bounds. The square plate width is twice the larger footprint
  dimension; a 3×3 footprint produces a 96×96 world-unit plate and decal
  size values of 6×6.

See the [add-on guide](blender_glb_ao/README.md) for Action selection, blockers,
the `ao_explode` property, axis conventions, supported input, and preservation
limits. Adding plates to BAR's atlas and consuming vertex alpha in a shader
are separate game-integration steps.

### Verify the GLB baker

From this repository's root:

```powershell
python -m unittest discover -s tests -v
& 'C:/Program Files/Blender Foundation/Blender 5.2/blender.exe' -b --factory-startup --python-exit-code 1 --python tests/blender_ao_integration.py
```

Adjust the Blender executable path for your installation. The integration
suite uses temporary copies of `armanac.glb`, preserving the reference file,
and checks real bakes, seams, animation, GLB updates, previews, and plate output.

## Inspect an S3O

Both inspection scripts emit JSON containing RecoilEngine coordinate/rotation
conventions, model metadata, texture names, and recursively nested pieces with
vertex counts, one-line local offsets, and one-line local dimensions. Offsets
and dimensions use two decimal places; the root `info` value is a single string
containing the coordinate, rotation, parent-relative offset, and dimension
conventions. Pieces with no vertices omit `dimensions`, and leaf pieces omit
the empty `children` list:

```
python s3o_info.py path\to\model.s3o
```

For a standalone parser that reads the binary records directly with
`struct.unpack` instead of importing `s3o.py`, use either `.s3o` or an
embedded-buffer `.glb` model:

```
python s3o_info_struct.py path\to\model.s3o
python s3o_info_struct.py path\to\model.glb
```

For GLB files, the parser also reports the scene name, `s3ocompat` setting,
scene metadata, and the hierarchy after applying the same axis and node
transform conventions as the RecoilEngine GLTF importer. It uses only Python's
standard library; external GLTF buffers are not supported.

# GUI usage:

Mash buttons:
![ui](https://user-images.githubusercontent.com/109391/222241312-54abe98e-2f45-4f7a-ab4a-b2d768cf6cd0.png)

The "Convert GLB to S3O" button converts a Blender-exported `.glb`/`.gltf`
directly to S3O (requires `pip install pygltflib`). The `s3ocompat mode`
dropdown selects `auto` (read from the glb scene properties, default),
`true` or `false`.


# Command line Usage
```
obj2s3o.exe --help
usage: obj2s3o.exe [-h] [-i INPUT] [-o OUTPUT] [--s3otoobj] [--wings3d]
                   [--objtos3o]
                   [--transformuv TRANSFORMUV TRANSFORMUV TRANSFORMUV TRANSFORMUV]
                   [--swaptex SWAPTEX SWAPTEX] [--optimize] [--printao]
                   [--clearao] [--piecelist PIECELIST [PIECELIST ...]]
                   [--zerolevelao ZEROLEVELAO] [--bakeaoplate]
                   [--aoplatesizex AOPLATESIZEX] [--aoplatesizez AOPLATESIZEZ]
                   [--aoplateresolution AOPLATERESOLUTION]
                   [--xnormalpath XNORMALPATH] [--bakevertexao] [--isbuilding]
                   [--isflying] [--minclamp MINCLAMP] [--bias BIAS]
                   [--gain GAIN] [--merge] [--scale SCALE] [--recenter]
                   [--adds3o ADDS3O] [--splits3o]

optional arguments:
  -h, --help            show this help message and exit
  -i INPUT, --input INPUT
                        The file to work on (s3o or obj)
  -o OUTPUT, --output OUTPUT
                        The name of the output file. If not specified,
                        modification operations are done in-place
  --s3otoobj            Convert a file from s3o to obj
  --wings3d             Optimize smoothing groups for Wings3D OBJ output
  --objtos3o            Convert a file from obj to s3o
  --transformuv TRANSFORMUV TRANSFORMUV TRANSFORMUV TRANSFORMUV
                        Transform UV space
  --swaptex SWAPTEX SWAPTEX
                        Specify the two textures for s3o
  --optimize            Convert a file from obj to s3o
  --printao             print the AO information for an s3o
  --clearao             print the AO information for an s3o
  --piecelist PIECELIST [PIECELIST ...]
                        Piece list to clear for --clearao, and piece list to
                        explode for --bakevertexao
  --zerolevelao ZEROLEVELAO
                        Specify zero level for AO
  --bakeaoplate         Bake an AO plate for the model
  --aoplatesizex AOPLATESIZEX
                        AO plate size X in footprint units
  --aoplatesizez AOPLATESIZEZ
                        AO plate size Z in footprint units
  --aoplateresolution AOPLATERESOLUTION
                        AO plate resolution in pixels
  --xnormalpath XNORMALPATH
                        Path to xnormal.exe
  --bakevertexao        Bake vertex AO for the model
  --isbuilding          Vertex AO. Enable this when baking AO for buildings.
                        This puts a larger than normal groundplate underneath
                        the unit, to make sure the building is only lit from
                        the top hemisphere
  --isflying            Vertex AO. Use for aircraft, this remove the
                        groundplate from under the unit, so it can get lit
                        from all directions
  --minclamp MINCLAMP   Vertex AO. The darkest possible level AO shading will
                        go to. 0 means even the darkes is allowed, 255 means
                        that everything will be full white. 128 is good if you
                        dont want peices to go too dark.
  --bias BIAS           Vertex AO. Add this much to every vertex AO value,
                        positive values brighten, negative values darken. Sane
                        range [-255;255] .
  --gain GAIN           Vertex AO.Multiply calculated AO terms with this
                        value. A value of 2.0 would double the brightness of
                        each value, 0.5 would half it. AO_out = min(255,
                        max(clamp, AO_in * bias + gain)).
  --merge               merge all pieces in an s3o
  --scale SCALE         merge all pieces in an s3o
  --recenter            recalculate center, midpoint, height
  --adds3o ADDS3O       Take all the pieces of this file, and add it to the
                        root of input
   --splits3o            take the piecelist, and split that out into a new s3o
   ```

# GLB to S3O (glb2s3o.py)

Converts a Blender-exported `.glb` (glTF 2.0 binary) directly to Spring S3O,
without an OBJ detour. The axis conversion, piece tree layout and scene extra
parsing mirror the engine's own GLTF import, so the result matches what the
Recoil engine produces for the same file.

Requires `pygltflib`:

```
pip install pygltflib
```

```
python glb2s3o.py -i model.glb [-o model.s3o] [options]

  -o OUTPUT, --output OUTPUT        Output .s3o file (default: input name with .s3o)
  --s3ocompat {auto,true,false}     s3ocompat mode (default auto: read from the
                                    glb scene extras, else false)
  --tex1 T                          First texture name (default: from scene extras)
  --tex2 T                          Second texture name (default: from scene extras)
  --radius R                        Model radius (default: from extras, else bbox)
  --height H                        Model height (default: from extras, else bbox)
  --midpos X Y Z                    Midpoint/collision offset (default: from extras,
                                    else bbox center)
  --no-optimize                     Skip vertex deduplication
```

Blender export requirements (see the engine's
[GLTF import guide](https://recoilengine.org/docs/guides/getting-started/blender-gltf-import/)):
- The model must be positioned Z-up in Blender
- Use the Khronos glTF 2.0 exporter in `.glb` format, with `Custom properties`
  included and `+Y up` **unchecked**
- Include `UVs`, `Normals`, `Tangents` (tangents are dropped when converting)
- Scene custom properties (`tex1`, `tex2`, `midpos`, `height`, `radius`,
  `s3ocompat`, ...) become the S3O header values; missing values fall back to
  the bounding box
- Each node becomes an S3O piece, so node names are preserved (the game's lua
  references pieces, e.g. firepoints, by name)
- Node rotation/scale is baked into the piece's vertices (S3O pieces only
  support a translation offset); non-uniform node scale is rejected

Not supported (ignored with a warning if present): materials/images/animations,
skinned meshes, vertex colors, tangents, non-triangle primitives.

