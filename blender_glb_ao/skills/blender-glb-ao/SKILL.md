---
name: blender-glb-ao
description: Bake vertex-color alpha AO or ground-plane AO PNGs for the open BAR unit model through an available Blender MCP and the BAR GLB AO Baker add-on. Use for vertex AO, walk-cycle AO averaging, ground AO plates, previews, and saving the resulting GLB when requested.
---

# Blender GLB AO

Use the **Blender MCP tools already available in context** to work in the connected, currently open Blender session. The baker targets Blender **5.2+**; use BAR GLB AO Baker **0.1.2 or later**. Its properties are on `bpy.context.scene.glb_ao`, and its operators are under `bpy.ops.glb_ao`.

Prefer MCP inspection and screenshot tools for those tasks. Use the MCP Python execution tool for the add-on operations below; it can be named `execute_blender_code` or have an equivalent name in the current context. Follow its schema and result-return convention. If a required baker property or operator is unavailable, report that missing capability. Do not substitute another baker or open a separate Blender process.

## Inspect and identify the model

Inspect the active scene, file path/dirty state, mode, bake settings, mesh color attributes, modifiers, visibility, and animation before making changes. The model root is the **single top-level object inside the scene's `Unit` collection**. The collection can directly contain all the pieces as well as that root; it need not contain only one object.

Run this through the MCP Python tool to resolve the root and bake targets:

```python
import bpy

scene = bpy.context.scene
unit = scene.collection.children.get('Unit')
if unit is None:
    raise ValueError('No top-level Unit collection in the active scene')
members = set(unit.all_objects)
roots = [obj for obj in members if obj.parent not in members]
if len(roots) != 1:
    raise ValueError('Expected one model root in Unit: ' + repr([o.name for o in roots]))
root = roots[0]
hierarchy = [root, *root.children_recursive]
if any(obj not in members for obj in hierarchy):
    raise ValueError('The model hierarchy extends outside Unit')
targets = [obj for obj in hierarchy
           if obj.type == 'MESH'
           and obj.visible_get(view_layer=bpy.context.view_layer)
           and not obj.hide_render and len(obj.data.polygons)]
if not targets:
    raise ValueError('Unit has no visible, render-enabled mesh faces')
```

Use a root explicitly supplied by the user instead when applicable. If discovery is ambiguous, report the candidates and clarify rather than selecting an unrelated active object. Do not hide, move, rename or import replacement pieces to make discovery succeed.

Work in Object Mode. If switching from Edit Mode is needed, preserve pending edits and the user's mode for restoration. Check shared mesh data: baking a mesh also updates its instances. Isolate target mesh data first if it is also used outside the requested model. Topology-changing modifiers must be resolved before baking; do not silently apply or remove them. Deforming modifiers and armatures are supported.

## Choose settings

Set `scene.glb_ao.root = root`. Respect the user's requested values and the scene's existing settings. The established defaults are:

| Property | Meaning / default |
|---|---|
| `color_attribute` | `COLOR_0`; vertex AO is brightness in alpha, preserving RGB |
| `samples` | At least **1024** for both vertex and plate AO |
| `distance` | AO radius; default **128** world units |
| `gain`, `bias`, `minimum` | Default **1**, **0.05**, **0**; apply once after averaging |
| `unit_type` | `MOBILE`, `BUILDING`, or `FLYING`, according to the unit |
| `ground_z` | Ground height relative to the root; default **0** |

Do not bypass `samples` by setting the AO shader node alone: it caps at 128 rays per evaluation. The add-on supplies enough Cycles samples to meet the requested budget, disables early stopping, and rounds up to a multiple of 128.

For a mobile unit, the temporary blocker is one model size wide and half the maximum horizontal size below ground. Buildings use a twice-size blocker at ground level; flying units use none. Existing Boolean object property `ao_explode` marks subtrees to separate during **vertex** baking. Respect the marks; change them only as requested or clearly required by the chosen bake setup.

For a requested walk average, inspect Actions, NLA and the cycle range. Set `walk=True`, `frame_start`, and `frame_end`; the end is the **duplicate closing pose and is excluded**. Prefer `sampling='FRAMES'` for a time average. `sampling='KEYS'` equally weights Action keys. Leave `action` empty to use the current timeline/NLA; an explicit Action override also needs its owning `animation_object`. Do not invent a walk cycle when none is available: use the current pose and report it, or clarify if averaging was specifically required.

## Vertex AO

Capture enough source state to verify geometry, transforms, UVs, normals and RGB remain intact. Prefer a single MCP execution containing snapshot, bake and verification, because local variables may not persist between MCP calls. Add a Blender undo step before a live bake when available.

```python
settings = scene.glb_ao
settings.root = root
settings.samples = max(1024, settings.samples)
settings.color_attribute = 'COLOR_0'  # Use a different attribute if requested.
status = bpy.ops.glb_ao.bake_vertex()
if 'FINISHED' not in status:
    raise RuntimeError('Vertex AO bake did not finish: ' + repr(status))
```

The operator writes AO to the selected color attribute's alpha. It averages temporal and shared-instance samples, then shares AO between rest-position vertices within **0.001 mesh-local units**, independent of UVs and normals. These Euclidean groups are transitive and scoped to each mesh; separate animated pieces are independent. Geometry is not welded. Do not replace this with coordinate rounding, UV packing, RGB painting, or manual per-corner writes.

After verifying the bake, use `bpy.ops.glb_ao.preview(mode='AO')` for grayscale inspection or `mode='SHADED'` for a material preview. For MCP calls without a 3D View context, set `area.spaces.active.shading.type = 'MATERIAL'` on the intended window's `VIEW_3D` area and request redraw. `mode='RESTORE'` restores materials while retaining the baked alpha. Respect a request to keep the original material view.

## Ground-plane AO

Plate baking uses the **current pose**. It ignores walk averaging, exploded positions, and the temporary vertex-bake blocker. Set a useful pose first when one is specified.

- `footprint_x` / `footprint_y`: gameplay X/Z footprint squares, **16 world units each**. Zero estimates from bounds around the root. Prefer known gameplay values; geometry only gives an estimate.
- `resolution`: the **string** `'64'` or `'128'`. Respect an existing choice or requested size; use 128 for a large/high-detail plate when appropriate.
- Full square plate width: `2 * max(footprint_x, footprint_y) * 16` after resolving automatic values. A 3×3 footprint produces 96×96 world units and decal-size values of 6×6.

Resolve an output path before executing. Use the user's destination; otherwise derive `<blend-stem>_aoplane.png` beside the saved `.blend`, or a suitable workspace artifact path if the blend is unsaved. A plate request includes writing its PNG and companion JSON. Avoid an unrelated existing file; resolve an ambiguous overwrite before proceeding.

```python
settings = scene.glb_ao
settings.root = root
settings.samples = max(1024, settings.samples)
# Set footprint/resolution from the inspected scene and requested output.
status = bpy.ops.glb_ao.bake_plate(filepath=output_path)
if 'FINISHED' not in status:
    raise RuntimeError('Ground AO plate bake did not finish: ' + repr(status))
```

Pass a concrete absolute `output_path`; call the operator in execution mode rather than invoking a file dialog. The output is black RGBA with **darkness** in alpha and transparent edges. The `.json` contains dimensions and decal-size values. The baker handles the `s3ocompat` orientation; do not add another image flip. Atlas registration, UnitDefs, shaders and engine changes are separate tasks.

## Verify, persist and report

- Inspect both MCP and operator results. An MCP response can succeed while its nested Blender result reports an error. On cancellation/error, report it instead of claiming a bake completed. On timeout, inspect whether the original operation is still running or finished before considering a retry; do not submit duplicate bakes or saves blindly.
- Vertex AO: report root, mesh count, pose/frame range, ray budget, and alpha min/mean/max. Confirm finite values in `[0,1]`, RGB preservation, and equal AO within 0.001-position groups. A uniformly bright result can be valid for unoccluded geometry. Confirm geometry, transforms, UVs and normals were preserved and temporary bake scenes were removed.
- Plate AO: confirm the PNG exists with the selected resolution, black RGB, alpha in range and zero alpha on all edges; confirm the companion JSON dimensions. Use an MCP screenshot or image preview when useful to inspect the result.
- Save the `.blend` or export/update a GLB when requested by the user or the enclosing workflow. A vertex bake alone updates the open session; it does not imply saving unrelated pending edits. When saving a blend, restore temporary preview materials first unless the user explicitly wants the preview saved.
- For a GLB loaded through this add-on with intact source IDs and `bar_ao_source`, use `bpy.ops.glb_ao.update_source()` for a requested preserving update. It checks the source hash and retains `.glb.ao-backup`. A native `.blend` model without those IDs needs `bpy.ops.glb_ao.export_new(filepath=...)` for a requested export; do not manufacture a source association or overwrite the original by ordinary re-export.
- Finish with the actual outcome, any preview shown, output paths, and whether changes are **in memory or saved to disk**. State any unmet part of the request.
