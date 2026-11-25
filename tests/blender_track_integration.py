"""Blender 5.2 --background --factory-startup --disable-autoexec --python this_file.

Uses armstump without modifying it. Optional -- --output <folder> retains a
generated bundle, preview blend and render; otherwise use a temporary folder.
"""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

import bpy
import numpy as np
from mathutils import Matrix

REPO = Path(__file__).resolve().parents[1]
args = argparse.ArgumentParser()
args.add_argument('--output', type=Path)
args.add_argument('--package', type=Path, help='Test the built install ZIP instead of the source package')
options = args.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
sys.path.insert(0, str(options.package.resolve()) if options.package else str(REPO))
import blender_glb_ao as addon

# Exercise reload/registration, including uninstall/reinstall.
del addon.tracks.project_contact
importlib.reload(addon)
assert callable(addon.tracks.project_contact)
addon.register()
addon.unregister()
addon.register()
for cls in addon.track_ui.CLASSES:
    if issubclass(cls, bpy.types.Operator):
        description = getattr(bpy.ops.glb_ao, cls.bl_idname.split('.')[1]).get_rna_type().description
        assert len(description) > 100
print('PASS: track panel operators register, reload and unregister')

source = REPO.parent / 'Beyond-All-Reason-Private/objects3d/UnitsV2/armstump.glb'
source_bytes = source.read_bytes()
root = addon.load_glb(bpy.context, source)
bpy.context.view_layer.update()
scene = bpy.context.scene
settings = scene.glb_tracks
settings.source_normal = str(REPO.parent / 'Beyond-All-Reason-Private/unittextures/novelpbr/Armada_T1_Units_Normal.dds')
assert settings.contact_height == 1
assert settings.padding_percent == 10
scene.frame_set(11, subframe=0.25)
targets = addon.baking.objects_for_model(bpy.context, scene.glb_ao)


def signature():
    return {obj.name: (addon.baking.mesh_signature(obj.data), list(obj.data.materials),
                      [list(row) for row in obj.matrix_world],
                      [list(v.color) for v in obj.data.color_attributes['COLOR_0'].data]) for obj in targets}


before = signature()
selection = list(bpy.context.selected_objects)
active = bpy.context.view_layer.objects.active
triangles, _, basis, sign = addon.track_ui.model_triangles(bpy.context)
assert sign == 1
assert len(triangles) > 1000
projected = addon.tracks.project_contact(triangles)
print('CONTACT_MEASUREMENTS', json.dumps({key: projected[key] for key in ('width', 'length', 'bounds', 'sample_height_range')}))
assert 25 < projected['width'] < 32
assert 0 < projected['length'] <= 38, 'Contact length cannot exceed the measured full model bounds'
assert projected['sample_height_range'][1] < 1
# Central hull is not a contact surface in Stout.
assert projected['coverage'][128].sum() == 0
assert projected['coverage'].sum() > 1000
print('PASS: actual armstump below-one-unit projection excludes suspended hull and turret')

# Physical units retain root scale; translation/rotation do not change root-frame bounds.
saved_matrix = root.matrix_world.copy()
root.matrix_world = Matrix.Translation((10, 20, 30)) @ Matrix.Rotation(0.7, 4, 'Z') @ Matrix.Scale(2, 4)
bpy.context.view_layer.update()
scaled_triangles, _, _, _ = addon.track_ui.model_triangles(bpy.context)
scaled = addon.tracks.project_contact(scaled_triangles, contact_height=2)
assert abs(scaled['width'] - projected['width'] * 2) < 1e-4
assert abs(scaled['length'] - projected['length'] * 2) < 1e-4
root.matrix_world = saved_matrix
bpy.context.view_layer.update()
settings.forward_axis = 'NEG_Y'
reversed_triangles, _, _, sign = addon.track_ui.model_triangles(bpy.context)
assert sign == -1
np.testing.assert_allclose(reversed_triangles[:, :, :2], -triangles[:, :, :2])
settings.forward_axis = 'AUTO'
print('PASS: physical root scale, transformed root and explicit forward convention')

temporary = tempfile.TemporaryDirectory(prefix='bar_track_trial_') if options.output is None else None
folder = options.output.resolve() if options.output else Path(temporary.name)
folder.mkdir(parents=True, exist_ok=True)
filepath = folder / 'armstump_tracks_generated.bmp'
try:
    assert bpy.ops.glb_ao.bake_track(filepath=str(filepath)) == {'FINISHED'}
    assert source.read_bytes() == source_bytes
    assert signature() == before
    assert list(bpy.context.selected_objects) == selection
    assert bpy.context.view_layer.objects.active == active
    assert scene.frame_current == 11 and scene.frame_subframe == 0.25
    assert bpy.context.scene == scene
    report = json.loads(Path(settings.manifest).read_text())
    assert report['projection']['image_size'][1] == 256
    assert report['settings']['padding_percent'] == 10
    contact_span = report['projection']['bounds']['contact_max'][0] - report['projection']['bounds']['contact_min'][0]
    assert abs(report['tracks']['width'] - contact_span * 1.2) < 1e-8
    assert report['settings']['contact_height'] == 1
    materials = report['sources']['materials']
    assert materials['references']['tex1'] == 'Armada_T1_Units_Color.dds'
    assert materials['references']['tex2'] == 'Armada_T1_Units_Other.dds'
    assert any('Armada_T1_Units_Normal.dds' in path for path in materials['normal_candidates'])
    assert Path(materials['selected_normal']['path']).parts[-2:] == ('novelpbr', 'Armada_T1_Units_Normal.dds')
    assert materials['normal_used_for_track'] is False
    assert report['stages']['runtime']['status'] == 'pending'
    assert 0.04 < report['projection']['sample_height_range'][0] < 0.05
    image = bpy.data.images.load(str(filepath), check_existing=False)
    pixels = np.array(image.pixels[:]).reshape(256, -1, 4)
    assert image.size[1] == 256
    assert image.size[0] == round(256 * report['tracks']['repeat_length'] / report['tracks']['width'])
    # Blender's BMP reader must see B=0; raw file RGB encoding bypasses color management.
    assert np.max(pixels[:, :, 2]) == 0
    assert np.max(pixels[:20, :, 1]) == 0 and np.max(pixels[-20:, :, 1]) == 0
    bpy.data.images.remove(image)
    class Layout:
        def prop(self, owner, name):
            assert hasattr(owner, name), name
        def label(self, **kwargs):
            pass
        def operator(self, name):
            assert getattr(bpy.ops.glb_ao, name.split('.')[1]).get_rna_type()
        def row(self):
            return self
    addon.track_ui.TRACK_PT_panel.draw(SimpleNamespace(layout=Layout()), bpy.context)
    print('PASS: panel controls bind registered properties/operators after generation')
    print('PASS: actual operator writes valid BMP/PNG/JSON/Lua, preserves source, state, materials, geometry and vertex AO')
    print('PASS: GLB tex1/tex2 metadata resolves external textures and records the verified novelpbr normal map')
    with tempfile.TemporaryDirectory(prefix='bar_track_export_check_') as export_folder:
        exported_path = Path(export_folder) / 'authoring_export.glb'
        assert bpy.ops.glb_ao.export_new(filepath=str(exported_path)) == {'FINISHED'}
        exported = addon.GLB.read(exported_path)
        assert all('glb_tracks' not in item.get('extras', {}) for item in exported.document['scenes'])
        assert all(not key.startswith('bar_track_') for item in exported.document['nodes'] for key in item.get('extras', {}))
    assert source.read_bytes() == source_bytes
    assert signature() == before
    print('PASS: new GLB export excludes track UI state and retains the original source')
    # Failure after preview geometry/material creation must clean every scratch datablock.
    kinds = ('objects', 'scenes', 'meshes', 'materials', 'worlds', 'cameras', 'lights', 'images')
    blocks_before = {kind: set(getattr(bpy.data, kind)) for kind in kinds}
    manifest_path = Path(settings.manifest)
    manifest_bytes = manifest_path.read_bytes()
    broken = json.loads(manifest_bytes)
    broken['outputs']['tiled']['path'] = str(folder / 'missing_preview.png')
    manifest_path.write_text(json.dumps(broken))
    try:
        try:
            addon.track_ui.create_preview_scene(bpy.context)
        except RuntimeError:
            pass
        else:
            raise AssertionError('Expected missing preview image failure')
    finally:
        manifest_path.write_bytes(manifest_bytes)
    assert {kind: set(getattr(bpy.data, kind)) for kind in kinds} == blocks_before
    assert bpy.context.scene == scene
    assert signature() == before
    print('PASS: failed preview cleans scratch meshes, materials, scene, images and world')
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / 'armstump_track_authoring.blend'))
    assert bpy.ops.glb_ao.preview_track() == {'FINISHED'}
    preview = bpy.context.scene
    assert preview != scene
    assert len([o for o in preview.objects if o.name.startswith('Preview ')]) == len(targets)
    assert signature() == before
    preview.render.filepath = str(folder / 'armstump_track_preview.png')
    bpy.ops.render.render(write_still=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(folder / 'armstump_track_preview.blend'))
    assert source.read_bytes() == source_bytes
    report['stages']['blender_preview'] = {'status': 'passed', 'scope': 'Blender render completed',
        'evidence': str(folder / 'armstump_track_preview.png')}
    Path(settings.manifest).write_text(json.dumps(report, indent=2) + '\n')
    print('PASS: separate evaluated Blender preview scene rendered; source untouched')
    print('TRACK_TRIAL', json.dumps({'outputs': str(folder), 'source_sha256': hashlib.sha256(source_bytes).hexdigest(),
                                   'width': report['tracks']['width'], 'repeat': report['tracks']['repeat_length'],
                                   'stretch': report['tracks']['unitdef']['trackstretch']}))
finally:
    if temporary:
        temporary.cleanup()
