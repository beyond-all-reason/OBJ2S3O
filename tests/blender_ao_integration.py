"""Run with Blender 5.2 --background --factory-startup --python <this file>."""
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

import bpy
import numpy as np
from mathutils import Matrix, Quaternion, Vector

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import blender_glb_ao as addon
from blender_glb_ao import baking
from blender_glb_ao.glb import GLB


def assert_clean(scene):
    assert bpy.context.scene == scene
    assert not any(s.name.startswith('_BAR_AO_BAKE') for s in bpy.data.scenes)
    assert not any(m.name.startswith('_BAR_AO_MATERIAL') for m in bpy.data.materials)


def source_bounds(source):
    document = source.document
    points = []

    def walk(index, parent):
        node = document['nodes'][index]
        if 'matrix' in node:
            local = Matrix([node['matrix'][i:i + 4] for i in range(0, 16, 4)]).transposed()
        else:
            x, y, z, w = node.get('rotation', [0, 0, 0, 1])
            local = Matrix.LocRotScale(Vector(node.get('translation', [0, 0, 0])),
                                      Quaternion((w, x, y, z)), Vector(node.get('scale', [1, 1, 1])))
        world = parent @ local
        if 'mesh' in node:
            for p in document['meshes'][node['mesh']]['primitives']:
                points.extend(world @ Vector(p) for p in source.accessor(p['attributes']['POSITION']))
        for child in node.get('children', []):
            walk(child, world)
    for index in document['scenes'][document.get('scene', 0)]['nodes']:
        walk(index, Matrix.Identity(4))
    return np.array([[min(p[i] for p in points) for i in range(3)],
                     [max(p[i] for p in points) for i in range(3)]])


addon.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
settings = scene.glb_ao
settings.samples = 8
settings.distance = 64
settings.bias = 0
settings.unit_type = 'FLYING'

with tempfile.TemporaryDirectory(prefix='bar_ao_integration_') as folder:
    folder = Path(folder)
    source_path = folder / 'armanac.glb'
    shutil.copy2(REPO / 'armanac.glb', source_path)
    original = GLB.read(source_path)
    root = addon.load_glb(bpy.context, source_path)
    targets = baking.objects_for_model(bpy.context, settings)
    actual_bounds = np.array(baking.model_bounds(targets, root.matrix_world))
    assert np.allclose(actual_bounds, source_bounds(original), atol=0.0002), (actual_bounds, source_bounds(original))
    assert len(targets) == 4
    try:
        baking.source_alpha(bpy.context, settings)
    except ValueError:
        pass
    else:
        raise AssertionError('Saving without an AO bake must be rejected')
    print('PASS: stable vertex IDs and Recoil Z-up import orientation')

    before_rgb = {}
    for obj in targets:
        layer = obj.data.color_attributes['COLOR_0']
        colors = np.array([list(v.color) for v in layer.data])
        before_rgb[obj] = colors[:, :3].copy()
    assert bpy.ops.glb_ao.bake_vertex() == {'FINISHED'}
    assert_clean(scene)
    assert GLB.read(source_path).digest == original.digest
    for obj in targets:
        rgba = np.array([list(v.color) for v in obj.data.color_attributes['COLOR_0'].data])
        assert np.array_equal(rgba[:, :3], before_rgb[obj]), obj.name
        assert np.isfinite(rgba).all()
        assert np.all((rgba[:, 3] >= 0) & (rgba[:, 3] <= 1))
    values = baking.source_alpha(bpy.context, settings)
    print('AO_RANGE', min(values.values()), max(values.values()))
    assert min(values.values()) < 0.95, 'Expected occluded vertices on sample model'
    assert bpy.ops.glb_ao.update_source() == {'FINISHED'}
    patched = GLB.read(source_path)
    assert patched.binary.startswith(original.binary)
    assert patched.document['nodes'] == original.document['nodes']
    for mi, pi, base, count in original.primitives():
        old_idx = original.document['meshes'][mi]['primitives'][pi]['attributes']['COLOR_0']
        new_idx = patched.document['meshes'][mi]['primitives'][pi]['attributes']['COLOR_0']
        assert [v[:3] for v in original.accessor(old_idx)] == [v[:3] for v in patched.accessor(new_idx)]
    assert Path(str(source_path) + '.ao-backup').read_bytes() == (REPO / 'armanac.glb').read_bytes()
    print('PASS: Cycles alpha bake, RGB preservation, color-only source update and backup')

    slots = {o: [(s.link, s.material) for s in o.material_slots] for o in targets}
    for shaded in (False, True):
        baking.preview(bpy.context, settings, shaded)
        baking.restore_preview(targets)
        for obj in targets:
            assert [(s.link, s.material) for s in obj.material_slots] == slots[obj]
    print('PASS: AO and shaded previews restore original materials')

    settings.footprint_x = 3
    settings.footprint_y = 3
    plate_path = folder / 'armanac_aoplane.png'
    assert bpy.ops.glb_ao.bake_plate(filepath=str(plate_path)) == {'FINISHED'}
    assert_clean(scene)
    image = bpy.data.images.load(str(plate_path), check_existing=False)
    pixels = np.array(image.pixels[:]).reshape((64, 64, 4))
    assert np.all(pixels[:, :, :3] == 0)
    assert np.all(pixels[[0, -1], :, 3] == 0) and np.all(pixels[:, [0, -1], 3] == 0)
    assert pixels[:, :, 3].max() > 0.05
    bpy.data.images.remove(image)
    metadata = json.loads(plate_path.with_suffix('.json').read_text())
    assert metadata['world_width'] == 96 and metadata['buildinggrounddecalsizex'] == 6
    print('PASS: 64px RGBA plate, transparent edges, 16-unit decal sizing')

    new_path = folder / 'new.glb'
    assert bpy.ops.glb_ao.export_new(filepath=str(new_path)) == {'FINISHED'}
    exported = GLB.read(new_path)
    assert all('COLOR_0' in p['attributes'] for m in exported.document['meshes'] for p in m['primitives'])
    assert all('_BAR_AO_ID' not in p['attributes'] for m in exported.document['meshes'] for p in m['primitives'])
    assert not any('bar_ao_' in k for n in exported.document['nodes'] for k in n.get('extras', {}))
    assert all('glb_ao' not in s.get('extras', {}) for s in exported.document['scenes'])
    assert np.allclose(source_bounds(exported), source_bounds(original), atol=0.0002)
    print('PASS: authoring export retains AO alpha and Z-up geometry, excludes mapping attributes')

    # Native object animation exercises Blender 5 slotted Actions and temporal averaging.
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    root = bpy.data.objects.new('Animated fixture root', None)
    scene.collection.objects.link(root)
    settings.root = root
    bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 2))
    target = bpy.context.object
    target.name = 'Target'
    target.parent = root
    bpy.ops.mesh.primitive_cube_add(size=2, location=(1.1, 0, 2))
    moving = bpy.context.object
    moving.name = 'Moving occluder'
    moving.parent = root
    moving.keyframe_insert(data_path='location', frame=1)
    moving.location.x = 20
    moving.keyframe_insert(data_path='location', frame=2)
    settings.distance = 8
    settings.walk = False
    scene.frame_set(1)
    baking.bake_vertex(bpy.context, settings)
    a = np.array([v.color[3] for v in target.data.color_attributes['COLOR_0'].data])
    scene.frame_set(2)
    baking.bake_vertex(bpy.context, settings)
    b = np.array([v.color[3] for v in target.data.color_attributes['COLOR_0'].data])
    assert np.max(np.abs(a - b)) > 0.05, 'Moving occluder must affect sampled AO'
    settings.walk = True
    settings.frame_start, settings.frame_end = 1, 3
    settings.sampling = 'FRAMES'
    frame_count, _ = baking.bake_vertex(bpy.context, settings)
    average = np.array([v.color[3] for v in target.data.color_attributes['COLOR_0'].data])
    assert frame_count == 2
    assert np.allclose(average, (a + b) / 2, atol=0.002)
    assert scene.frame_current == 2
    settings.sampling = 'KEYS'
    assert baking.frames_for_bake(bpy.context, settings, [target, moving]) == [1, 2]
    settings.animation_object = moving
    settings.action = moving.animation_data.action
    old_action = moving.animation_data.action
    with baking.action_override(settings, [target, moving]):
        assert moving.animation_data.action == settings.action
    assert moving.animation_data.action == old_action
    print('PASS: evaluated animation, every-frame average, keyframe mode, Action restoration')

    settings.walk = False
    settings.unit_type = 'BUILDING'
    moving['ao_explode'] = True
    before = moving.matrix_world.copy()
    baking.bake_vertex(bpy.context, settings)
    assert moving.matrix_world == before
    assert_clean(scene)
    print('PASS: explode and building blocker leave source transforms intact')

    subdivision = target.modifiers.new('Topology rejection', 'SUBSURF')
    subdivision.levels = 1
    original_material = target.data.color_attributes['COLOR_0'].data[0].color[:]
    try:
        baking.bake_vertex(bpy.context, settings)
    except ValueError as error:
        assert 'topology' in str(error)
    else:
        raise AssertionError('Expected topology-changing modifier rejection')
    assert_clean(scene)
    assert tuple(target.data.color_attributes['COLOR_0'].data[0].color) == original_material
    print('PASS: failure cleanup and no partial AO commit')

    target.modifiers.remove(subdivision)
    # A shared mesh with no original material must survive repeated preview toggles.
    linked = target.copy()
    scene.collection.objects.link(linked)
    baking.preview(bpy.context, settings)
    baking.restore_preview([target, moving, linked])
    assert not any(m.get('bar_ao_preview') for m in bpy.data.materials), [(m.name, m.users) for m in bpy.data.materials if m.get('bar_ao_preview')]
    assert not target.material_slots and not linked.material_slots
    print('PASS: shared-mesh preview cleanup')

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    # Test both plate axis conventions with an off-center asymmetric occluder.
    root = bpy.data.objects.new('Plate axis fixture', None)
    scene.collection.objects.link(root)
    settings.root = root
    settings.unit_type = 'FLYING'
    settings.distance = 32
    settings.resolution = '128'
    bpy.ops.mesh.primitive_cube_add(size=8, location=(12, 8, 4.1))
    obj = bpy.context.object
    obj.parent = root
    centroids = []
    for compat in (False, True):
        scene['s3ocompat'] = compat
        path = folder / f'plate_axes_{compat}.png'
        baking.bake_plate(bpy.context, settings, str(path))
        image = bpy.data.images.load(str(path), check_existing=False)
        alpha = np.array(image.pixels[:]).reshape((128, 128, 4))[:, :, 3]
        y, x = np.indices(alpha.shape)
        centroids.append(((x * alpha).sum() / alpha.sum(), (y * alpha).sum() / alpha.sum()))
        bpy.data.images.remove(image)
    assert centroids[0][0] > 64 and centroids[0][1] > 64, centroids
    assert np.allclose(np.array(centroids[0]) + np.array(centroids[1]), [127, 127], atol=0.01), centroids
    del scene['s3ocompat']
    print('PASS: 128px asymmetric plate orientation in both s3ocompat modes')

    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    # Native armature deformation, followed by a preservation-mode GLB import.
    root = bpy.data.objects.new('Skinned fixture root', None)
    scene.collection.objects.link(root)
    settings.root = root
    bpy.ops.object.armature_add(location=(0, 0, 0))
    armature = bpy.context.object
    armature.name = 'AO test armature'
    armature.parent = root
    bpy.ops.mesh.primitive_cube_add(size=4, location=(0, 0, 3))
    skin = bpy.context.object
    skin.name = 'Skinned cube'
    skin.parent = armature
    group = skin.vertex_groups.new(name=armature.data.bones[0].name)
    group.add(list(range(len(skin.data.vertices))), 1, 'REPLACE')
    modifier = skin.modifiers.new('Deform', 'ARMATURE')
    modifier.object = armature
    bone = armature.pose.bones[0]
    bone.location = (0, 0, 0)
    bone.keyframe_insert(data_path='location', frame=1)
    bone.location = (0, 3, 0)
    bone.keyframe_insert(data_path='location', frame=2)
    settings.walk = True
    settings.animation_object = armature
    settings.action = armature.animation_data.action
    settings.frame_start, settings.frame_end = 1, 3
    settings.sampling = 'FRAMES'
    before = skin.data.vertices[0].co.copy()
    baking.bake_vertex(bpy.context, settings)
    assert skin.data.vertices[0].co == before
    skinned_path = folder / 'skinned.glb'
    assert bpy.ops.glb_ao.export_new(filepath=str(skinned_path)) == {'FINISHED'}
    skinned_source = GLB.read(skinned_path)
    assert skinned_source.document.get('skins')
    assert skinned_source.document.get('animations')
    root = addon.load_glb(bpy.context, skinned_path)
    settings.action = None
    settings.animation_object = None
    assert baking.bake_vertex(bpy.context, settings)[0] == 2
    assert bpy.ops.glb_ao.update_source() == {'FINISHED'}
    skinned_result = GLB.read(skinned_path)
    assert skinned_result.document['skins'] == skinned_source.document['skins']
    assert skinned_result.document['animations'] == skinned_source.document['animations']
    assert skinned_result.document['nodes'] == skinned_source.document['nodes']
    assert_clean(scene)
    print('PASS: native armature and imported GLB skinning/animation bake, preserved skins and clips')

    # Root scaling is already part of world-unit extents; it must not scale
    # the plate a second time or make metadata disagree with the image.
    root.scale = (2, 2, 2)
    scene.frame_set(scene.frame_current)
    targets = baking.objects_for_model(bpy.context, settings)
    with baking.bake_scene(bpy.context, settings) as (temporary, material, original_scene):
        copies, bounds = baking.evaluated_copies(bpy.context, temporary, material, targets, root)
        world_bounds = baking.model_bounds(copies.values(), baking.model_frame(root))
        assert np.allclose(bounds, world_bounds)
        plane = baking.add_plane(temporary, material, baking.model_frame(root), 96, 96, 0)
        plane_bounds = baking.model_bounds([plane], baking.model_frame(root))
        assert abs(plane_bounds[1].x - plane_bounds[0].x - 96) < 0.001
    assert_clean(scene)
    print('PASS: root scaling preserves physical plate dimensions')

addon.unregister()
print('ALL BLENDER AO INTEGRATION CHECKS PASSED')
