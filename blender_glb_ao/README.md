# BAR GLB AO Baker

Standalone Blender **5.2+** add-on for vertex ambient occlusion in **`COLOR_0` alpha**, and black RGBA ground AO plates. No xNormal, S3O Kit, or Skeletor installation is required. Tested with Blender 5.2.1 LTS.

## Install

Install `bar_glb_ao_v0.1.2.zip` using **Edit → Preferences → Add-ons → menu → Install from Disk**, then enable **BAR GLB AO Baker**. Open the 3D View sidebar (`N`) and select **GLB AO**.

When updating an existing installation, restart Blender after installing the ZIP so all add-on modules reload, then rebake models to apply the new ray minimum and shared-position AO.

To build the ZIP from the workspace root:

```powershell
python OBJ2S3O/package_glb_ao.py
```

From the OBJ2S3O repository root, use `python package_glb_ao.py`. The ZIP is
written to `dist/bar_glb_ao_v0.1.2.zip` within that repository.

## Blender MCP workflow skill

The reusable [blender-glb-ao skill](skills/blender-glb-ao/SKILL.md) instructs an
agent with Blender MCP already available to identify the model root in `Unit`,
bake vertex AO or ground plates, verify the result, and save/export when
requested. The self-contained `SKILL.md` is included in the add-on ZIP and
can be read directly by an agent using the available Blender MCP tools.

Example workflow request: “Use the blender-glb-ao skill to bake vertex AO for
the model in the open scene's Unit collection and show the AO preview.”

## Existing GLB workflow

1. Set **File Axes** to **Recoil / Blender Z Up** for models exported with `+Y Up` disabled. Use **Standard glTF Y Up** for ordinary glTF files.
2. Click **Load GLB for AO**. The add-on imports a temporary instrumented copy with stable vertex IDs and creates an AO Root. The original file is unchanged.
3. Choose **Building**, **Mobile**, or **Flying**, and set ray distance, samples, minimum brightness, gain and bias.
4. Optionally enable **Average Walk Cycle** and select the cycle range.
5. Click **Bake Vertex AO**, then **AO Only** or **Shaded AO** to inspect the result. **Restore Materials** returns the original materials.
6. Click **Update Source GLB** to write the baked alpha values back into the original file.

The update appends color data and redirects `COLOR_0` accessors. Original geometry, UVs, normals, tangents, RGB, hierarchy, skins, animation clips, extras and unknown GLB chunks are retained. Integer color precision is preserved. Missing colors become white RGB with baked AO alpha; RGB-only colors become RGBA. Unused vertices and unbaked primitives retain their previous values. UV seams and split vertices are mapped by source IDs, not by nearest position.

The first update creates `<model>.glb.ao-backup`, which is never overwritten. The destination is replaced atomically after successful validation. A source-file hash rejects external changes since import. Source mesh edits invalidate in-place mapping: reload the original, or use **Export New GLB** for an edited model. Saving the `.blend` retains source IDs, bake settings, and backup/source references.

## Blender authoring workflow

For a model already in Blender, select its top object and click **Use Active Hierarchy**, or choose **Model Root** directly. Only visible, render-enabled mesh descendants are baked; hidden geometry and unrelated scene objects do not occlude them. The root can itself be a mesh.

**Color Attribute** chooses the RGBA data to export as `COLOR_0`. Existing RGB is preserved. If this attribute is missing, the active color attribute is copied, or white RGB is initialized if none exists.

After baking, use **Export New GLB**. This is Blender's normal authoring export with `+Y Up` disabled, explicit color export, custom properties, and tangents. It regenerates the model file and is distinct from the preserving source-update operation. Imported Recoil `tex1`, `tex2`, `s3ocompat` and size metadata are supplied when absent from the Blender Scene. Internal AO mapping and preview properties are excluded. Add-on exports do not include hidden mesh objects.

Set Scene custom property `s3ocompat=true` for S3O-derived Blender models; retain the existing value when loading an existing GLB. The preserving update does not change axis conventions.

## Vertex AO settings

Vertex alpha represents **visibility/brightness**: `0` is occluded and `1` is unoccluded. Bake output is linear data. Adjustment is applied once after temporal averaging:

```text
alpha = clamp(mean(raw_visibility) * gain + bias, minimum_brightness, 1)
```

No automatic per-mesh contrast normalization is applied. Face-corner color data is retained for native models. Shared mesh instances also share/average the resulting AO.

### Position identity and seam continuity

Corner samples are averaged per vertex, then vertices within **0.001 mesh-local Blender units** of one another receive shared AO, including duplicate indices at UV seams, hard normals and material boundaries. Identity uses Euclidean distance on source/rest coordinates, independent of normals and UVs. Neighboring spatial cells are checked, so near-identical positions on opposite sides of a cell boundary still match.

Groups are transitive: A close to B and B close to C share one value even if A and C are farther apart. For example, vertices at X = 0, 0.00075 and 0.0015 belong to one group when Y/Z match. A diagonal offset of (0.0008, 0.0008, 0) exceeds the 0.001 distance tolerance and does not directly match.

The shared value is copied to every corresponding corner, so triangle interpolation is continuous across those seams. Position averaging happens after walk/instance averaging and before gain, bias and clamping. Vertex positions, UVs, normals, topology and RGB are preserved. Loose vertices do not contribute AO samples to the average. Distinct meshes/pieces remain independent because their local origins and animation may differ. The tolerance is measured before object transforms, so object scaling does not change which local positions match.

### Ray budget

**Ray Distance** and model dimensions use world coordinate units. **Ray Samples** sets the minimum number of hemisphere rays per vertex corner or plate texel, per pose. The default and minimum are **1024**. The same setting appears in the Vertex AO and Ground AO Plate sections and applies to both operations. Blender caps an AO shader evaluation at 128 rays, so the baker accumulates at least eight Cycles samples, each with 128 AO rays. Higher requests round up to the next multiple of 128 (for example, 1025 requests 1152 rays). Adaptive sampling and time limits are disabled to retain the full budget, including for settings saved by older add-on versions. Baking uses a separate CPU Cycles scene, independent of the current render engine, lights, world and source materials.

### Walk averaging

**Every Frame** samples every integer frame from **First Frame** up to, but excluding, **Closing Frame**. The closing frame should repeat the first pose. Exclude stance/transition frames from this range. This is a time average, including the poses between keys.

**Keyframes Only** equally weights the union of Action key times inside that range. Use **Every Frame** for NLA strips or procedural/constraint animation. Native Blender 5 slotted Actions, armature deformation and imported GLB animations use Blender's evaluated poses. Deforming modifiers are supported; topology-changing modifiers must be applied before baking.

Leave **Action Override** empty to use the current timeline, active Actions and NLA. To temporarily use another Action, choose its owning **Animation Object** (armature or animated object), then the Action. For an Action with multiple compatible slots, the current matching slot is preferred; otherwise the first compatible slot is used. Other objects retain their timeline animation. Original Action, slot, NLA state and frame are restored after the operation.

### Ground blockers and exploding pieces

- **Building:** a blocker at **Ground Height**, twice the model's horizontal dimensions, expanded as necessary to cover an offset model.
- **Mobile:** a model-size blocker lowered below **Ground Height** by half the maximum horizontal dimension.
- **Flying:** no blocker.

The blocker is bake geometry and is never exported. It is separate from a saved AO plate.

Select pieces and use **Mark Explode** to set Boolean object custom property **`ao_explode`**. **Clear Explode** sets it false. A marked object carries its complete descendant subtree; nested marks do not move descendants twice. Bake copies are separated by at least 100 world units and enough distance to exceed the model extents and AO radius. Original transforms are not changed. The property applies to objects, not individual bones inside a skinned mesh.

## AO plate output

Click **Bake AO Plate PNG** to bake the current pose without exploded pieces or a vertex-bake ground blocker. Walk averaging applies to vertex AO only.

**Footprint X/Z** are gameplay footprint squares of 16 world units. Zero estimates the footprint from visible model bounds relative to the root origin. Estimates cannot recover gameplay footprint rules; enter explicit values when available. The square plate's full width is `2 * max(footprint_x, footprint_z) * 16`. Resolutions are 64×64 and 128×128.

For example, a 3×3 footprint produces a 96×96 world-unit plate. The companion JSON reports `buildinggrounddecalsizex=6` and `buildinggrounddecalsizey=6`, matching the widget's 16-world-unit scale per decal-size value.

Output consists of:

- `<model>_aoplane.png`: black RGB, occlusion in alpha, faded to zero on all four edges.
- `<model>_aoplane.json`: physical dimensions and corresponding `buildinggrounddecalsizex/y` values.

Plate alpha represents **darkness**, the inverse of vertex AO alpha. Balancing follows the old OBJ2S3O convention, including a maximum opacity of 223/255 before edge fading. PNG UVs follow engine `+X, -Z`; `s3ocompat=true` is handled with a 180-degree image rotation. Do not additionally flip the source PNG. The plate is centered on the model root and uses **Ground Height**.

The game widget uses an atlas. Add the image to that atlas and configure the corresponding UnitDef decal parameters separately. This add-on does not modify BAR widgets, shaders, atlases, UnitDefs or engine code. Vertex-alpha AO requires a renderer that interprets that channel as AO; ordinary glTF material viewers do not give alpha this meaning.

## Supported input and limits

- Self-contained GLB 2.0 files with one embedded binary buffer and triangle mesh primitives.
- Interleaved and sparse color accessors, RGB/RGBA colors, and normalized 8/16-bit or float color values.
- Maximum 16 million source primitive vertices, to keep temporary float vertex IDs exact in Blender's importer.
- Draco/meshopt geometry must be decompressed first. External image files are not imported by the preserving workflow; embed images in the GLB first.
- Mesh edits and topology-changing modifiers are rejected for preserving updates. Select a separate filename for an authoring export.
- Preview material copies support Principled base-color shading; they are inspection aids, not a reproduction of BAR's lighting model. Restore previews before editing source materials.

## Verification

From the workspace root:

```powershell
python -m unittest discover -s OBJ2S3O/tests -v
& 'C:/Program Files/Blender Foundation/Blender 5.2/blender.exe' -b --factory-startup --python-exit-code 1 --python OBJ2S3O/tests/blender_ao_integration.py
```

If OBJ2S3O is checked out on its own, run `python -m unittest discover -s tests -v` and use `--python tests/blender_ao_integration.py` from its root instead. Adjust the Blender executable path for your installation.

The Blender suite uses temporary copies of `armanac.glb`; it does not update the reference model. It checks real Cycles bakes, the 1024-ray minimum, operator tooltips, 0.001-distance grouping and boundary cases, seam continuity across GLB material primitives, vertex IDs, axes, RGB preservation, source backups, animation averaging, skinning, material restoration, plate orientation and dimensions, authoring export, and failure cleanup.

## Version history

- **0.1.2:** use a 0.001 Euclidean position tolerance, including neighboring spatial cells and transitive groups; preserve shared AO across GLB material primitives.
- **0.1.1:** enforce a 1024-ray minimum for vertex and plate baking, add complete button tooltips, and average AO at matching vertex positions across normal/UV seams.
- **0.1.0:** initial standalone Blender 5.2+ add-on with vertex-alpha AO, animation averaging, previews, preserving GLB updates, and RGBA ground plates.

## Credits and license

GPL-2.0-or-later; see `LICENSE`. Cycles bake workflow adapted from **ChrisFloofyKitsune's S3O Kit** (`s3o-blender-tools/ambient_occlusion.py`). Plate color balancing and conventions originate in **Beherith's OBJ2S3O**. Skeletor's Blender Action compatibility informed animation handling. The preserving GLB writer and vertex-ID bridge are new implementations.
