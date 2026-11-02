# SPDX-License-Identifier: GPL-2.0-or-later
"""Blender-only bake operations; source objects never become bake targets.

Cycles vertex/plate workflow adapted from ChrisFloofyKitsune's S3O Kit.
Plate alpha balancing follows Beherith's OBJ2S3O baker.
"""
from contextlib import contextmanager
import hashlib
import json
import math
import struct

import bpy
import numpy as np
from mathutils import Matrix, Vector

from .glb import ID_ATTRIBUTE

COLOR = 'COLOR_0'
SCRATCH = '_BAR_AO_SCRATCH'


def objects_for_model(context, settings):
    root = settings.root
    if root is None:
        raise ValueError('Choose a model root, or load a GLB first')
    objects = [root, *root.children_recursive]
    targets = [o for o in objects if o.type == 'MESH' and o.visible_get(view_layer=context.view_layer)
               and not o.hide_render and len(o.data.polygons)]
    if not targets:
        raise ValueError('The model has no visible, render-enabled mesh faces')
    return targets


def model_frame(root):
    # Keep the root's orientation/origin while measuring extents in world
    # units, so root scaling cannot silently change exported decal sizes.
    return Matrix.LocRotScale(root.matrix_world.translation,
                              root.matrix_world.to_quaternion(), Vector((1, 1, 1)))


def mesh_signature(mesh):
    digest = hashlib.sha256()
    for vertex in mesh.vertices:
        digest.update(struct.pack('<3f', *vertex.co))
    for loop in mesh.loops:
        digest.update(struct.pack('<I', loop.vertex_index))
    for polygon in mesh.polygons:
        digest.update(struct.pack('<II', polygon.loop_start, polygon.loop_total))
    attribute = mesh.attributes.get(ID_ATTRIBUTE)
    if attribute:
        for item in attribute.data:
            digest.update(struct.pack('<i', int(item.value)))
    return digest.hexdigest()


def validate_source_meshes(targets, root):
    if not root.get('bar_ao_source'):
        return
    for obj in targets:
        if ID_ATTRIBUTE not in obj.data.attributes or mesh_signature(obj.data) != obj.data.get('bar_ao_signature'):
            raise ValueError(f'{obj.name}: source mesh was edited. Reload the GLB for in-place updates, or export as a new GLB')


def s3o_compatible(settings, scene):
    if settings.root and settings.root.get('bar_ao_extras'):
        return bool(json.loads(settings.root['bar_ao_extras']).get('s3ocompat', False))
    return bool(scene.get('s3ocompat', False))


def color_layer(mesh, name):
    layer = mesh.color_attributes.get(name)
    if layer is None:
        previous = mesh.color_attributes.active_color
        old = None
        domain = 'CORNER'
        if previous:
            domain = previous.domain
            old = np.empty(len(previous.data) * 4, dtype=np.float32)
            previous.data.foreach_get('color', old)
        layer = mesh.color_attributes.new(name=name, type='FLOAT_COLOR', domain=domain)
        if old is None:
            old = np.ones(len(layer.data) * 4, dtype=np.float32)
        layer.data.foreach_set('color', old)
    mesh.color_attributes.active_color = layer
    mesh.color_attributes.render_color_index = list(mesh.color_attributes).index(layer)
    return layer


def to_points(mesh, corner_values):
    indices = np.array([loop.vertex_index for loop in mesh.loops], dtype=np.int32)
    sums = np.bincount(indices, weights=corner_values, minlength=len(mesh.vertices))
    counts = np.bincount(indices, minlength=len(mesh.vertices))
    return np.divide(sums, counts, out=np.ones_like(sums), where=counts != 0)


def frames_for_bake(context, settings, targets):
    if not settings.walk:
        return [float(context.scene.frame_current) + context.scene.frame_subframe]
    start, end = settings.frame_start, settings.frame_end
    if end <= start:
        raise ValueError('Walk end must be after start (end is the duplicate closing pose and is excluded)')
    if settings.sampling == 'FRAMES':
        return list(range(start, end))
    frames = set()
    objects = set(targets)
    objects.update(o for target in targets for o in [target.parent] if o)
    objects.update(m.object for o in targets for m in o.modifiers if m.type == 'ARMATURE' and m.object)
    objects.update([settings.root, *settings.root.children_recursive])
    for obj in objects:
        animation = obj.animation_data
        if not animation or not animation.action:
            continue
        action = animation.action
        if action.is_action_legacy:
            curves = action.fcurves
        else:
            from bpy_extras import anim_utils
            slot = animation.action_slot
            bag = anim_utils.action_get_channelbag_for_slot(action, slot) if slot else None
            curves = bag.fcurves if bag else []
        for curve in curves:
            frames.update(float(k.co.x) for k in curve.keyframe_points if start <= k.co.x < end)
    if not frames:
        raise ValueError('No Action keys in the selected walk range; use Every Frame for NLA animation')
    return sorted(frames)


def set_frame(scene, frame):
    integer = math.floor(frame)
    scene.frame_set(integer, subframe=frame - integer)


@contextmanager
def action_override(settings, targets):
    """An explicit Action targets the chosen armature/object; otherwise use timeline/NLA."""
    if not settings.action or not settings.walk:
        yield
        return
    obj = settings.animation_object
    if settings.action and obj is None:
        raise ValueError('Choose the animation object that owns the selected Action')
    created = obj.animation_data is None
    animation = obj.animation_data_create()
    previous = (animation.action, animation.action_slot, animation.use_nla)
    try:
        animation.action = settings.action
        slots = [slot for slot in settings.action.slots if slot.target_id_type == obj.id_type]
        if slots and animation.action_slot not in slots:
            animation.action_slot = slots[0]
        animation.use_nla = False
        yield
    finally:
        animation.action = previous[0]
        if previous[1] and previous[0]:
            animation.action_slot = previous[1]
        animation.use_nla = previous[2]
        if created:
            obj.animation_data_clear()


@contextmanager
def bake_scene(context, settings):
    original_scene = context.window.scene
    scene = bpy.data.scenes.new('_BAR_AO_BAKE')
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    scene.render.bake.margin = 0
    scene.world = bpy.data.worlds.new('_BAR_AO_WORLD')
    material = bpy.data.materials.new('_BAR_AO_MATERIAL')
    material.use_nodes = True
    nodes = material.node_tree.nodes
    nodes.clear()
    output = nodes.new('ShaderNodeOutputMaterial')
    emission = nodes.new('ShaderNodeEmission')
    ao = nodes.new('ShaderNodeAmbientOcclusion')
    ao.samples = settings.samples
    ao.inputs['Distance'].default_value = settings.distance
    material.node_tree.links.new(ao.outputs['Color'], emission.inputs['Color'])
    material.node_tree.links.new(emission.outputs[0], output.inputs['Surface'])
    try:
        yield scene, material, original_scene
    finally:
        context.window.scene = original_scene
        world = scene.world
        for obj in list(scene.objects):
            mesh = obj.data if obj.type == 'MESH' else None
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        bpy.data.scenes.remove(scene)
        bpy.data.materials.remove(material)
        bpy.data.worlds.remove(world)


def clear_copies(scene):
    for obj in list(scene.objects):
        mesh = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def evaluated_copies(context, scene, material, targets, root, explode=True):
    depsgraph = context.evaluated_depsgraph_get()
    copies = {}
    groups = {}
    for original in targets:
        evaluated = original.evaluated_get(depsgraph)
        mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=depsgraph)
        copy = bpy.data.objects.new('_BAR_AO_' + original.name, mesh)
        scene.collection.objects.link(copy)
        copies[original] = copy
        copy.matrix_world = evaluated.matrix_world.copy()
        # A deformation may change positions, but not vertex/corner identities.
        if (len(mesh.vertices) != len(original.data.vertices) or
                [l.vertex_index for l in mesh.loops] != [l.vertex_index for l in original.data.loops]):
            raise ValueError(f'{original.name}: apply topology-changing modifiers before baking')
        mesh.materials.clear()
        mesh.materials.append(material)
        for polygon in mesh.polygons:
            polygon.material_index = 0
        layer = mesh.color_attributes.get(SCRATCH)
        if layer:
            mesh.color_attributes.remove(layer)
        layer = mesh.color_attributes.new(name=SCRATCH, type='FLOAT_COLOR', domain='CORNER')
        mesh.color_attributes.active_color = layer
        mesh.color_attributes.render_color_index = list(mesh.color_attributes).index(layer)
        owner = original
        chain = []
        while owner:
            chain.append(owner)
            if owner == root:
                break
            owner = owner.parent
        # The outermost marked ancestor carries its complete subtree.
        marked = next((o for o in reversed(chain) if bool(o.get('ao_explode', False))), None)
        if marked and explode:
            groups.setdefault(marked, []).append(copy)
    bounds = model_bounds(copies.values(), model_frame(root))
    size = max(bounds[1].x - bounds[0].x, bounds[1].y - bounds[0].y, bounds[1].z - bounds[0].z)
    separation = max(100.0, 2 * size + 3 * context.scene.glb_ao.distance)
    direction = root.matrix_world.to_3x3() @ Vector((0, 0, 1))
    direction.normalize()
    for index, members in enumerate(groups.values(), 1):
        for copy in members:
            copy.matrix_world.translation += direction * (separation * index)
    return copies, bounds


def model_bounds(objects, root_matrix):
    inverse = root_matrix.inverted_safe()
    points = [inverse @ obj.matrix_world @ Vector(corner) for obj in objects for corner in obj.bound_box]
    if not points:
        raise ValueError('Cannot compute bounds of an empty model')
    return (Vector(tuple(min(p[i] for p in points) for i in range(3))),
            Vector(tuple(max(p[i] for p in points) for i in range(3))))


def add_plane(scene, material, root_matrix, width, depth, z, name='_BAR_AO_PLATE'):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(-width / 2, -depth / 2, z), (width / 2, -depth / 2, z),
                      (width / 2, depth / 2, z), (-width / 2, depth / 2, z)], [], [(0, 1, 2, 3)])
    uv = mesh.uv_layers.new(name='UVMap')
    for item, coords in zip(uv.data, [(0, 0), (1, 0), (1, 1), (0, 1)]):
        item.uv = coords
    mesh.materials.append(material)
    obj = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(obj)
    obj.matrix_world = root_matrix
    return obj


def add_blocker(scene, material, root, bounds, settings):
    if settings.unit_type == 'FLYING':
        return
    low, high = bounds
    width, depth = max(high.x - low.x, 0.01), max(high.y - low.y, 0.01)
    if settings.unit_type == 'BUILDING':
        width = max(width, 2 * max(abs(low.x), abs(high.x))) * 2
        depth = max(depth, 2 * max(abs(low.y), abs(high.y))) * 2
        z = settings.ground_z
    else:
        z = settings.ground_z - 0.5 * max(width, depth)
    add_plane(scene, material, model_frame(root), width, depth, z, '_BAR_AO_BLOCKER')


def bake_vertex(context, settings):
    if context.mode != 'OBJECT':
        raise ValueError('Switch to Object Mode before baking')
    targets = objects_for_model(context, settings)
    validate_source_meshes(targets, settings.root)
    original_scene = context.scene
    original_frame = original_scene.frame_current + original_scene.frame_subframe
    pending = {}
    wm = context.window_manager
    with action_override(settings, targets):
        frames = frames_for_bake(context, settings, targets)
        wm.progress_begin(0, len(frames) * len(targets))
        try:
            with bake_scene(context, settings) as (scene, material, source_scene):
                progress = 0
                for frame in frames:
                    context.window.scene = source_scene
                    set_frame(source_scene, frame)
                    copies, bounds = evaluated_copies(context, scene, material, targets, settings.root)
                    add_blocker(scene, material, settings.root, bounds, settings)
                    context.window.scene = scene
                    for original, copy in copies.items():
                        bpy.ops.object.select_all(action='DESELECT')
                        copy.select_set(True)
                        context.view_layer.objects.active = copy
                        result = bpy.ops.object.bake(type='EMIT', target='VERTEX_COLORS')
                        if 'FINISHED' not in result:
                            raise RuntimeError('Vertex bake cancelled')
                        rgba = np.empty(len(copy.data.loops) * 4, dtype=np.float32)
                        copy.data.color_attributes[SCRATCH].data.foreach_get('color', rgba)
                        values = rgba.reshape((-1, 4))[:, :3].mean(axis=1)
                        pending[original] = pending.get(original, 0) + values
                        progress += 1
                        wm.progress_update(progress)
                    clear_copies(scene)
        finally:
            context.window.scene = original_scene
            set_frame(original_scene, original_frame)
            wm.progress_end()
    # Commit only after every pose baked successfully; RGB is never replaced.
    shared = {}
    for obj, sums in pending.items():
        mesh = obj.data
        if mesh in shared:
            shared[mesh].append(sums / len(frames))
        else:
            shared[mesh] = [sums / len(frames)]
    for mesh, samples in shared.items():
        raw = np.mean(samples, axis=0)
        layer = color_layer(mesh, settings.color_attribute)
        if layer.domain == 'POINT':
            raw = to_points(mesh, raw)
        alpha = np.clip(raw * settings.gain + settings.bias, settings.minimum, 1)
        rgba = np.empty(len(layer.data) * 4, dtype=np.float32)
        layer.data.foreach_get('color', rgba)
        rgba.reshape((-1, 4))[:, 3] = alpha
        layer.data.foreach_set('color', rgba)
        mesh['bar_ao_baked'] = True
        mesh.update()
    return len(frames), len(targets)


def source_alpha(context, settings):
    targets = objects_for_model(context, settings)
    validate_source_meshes(targets, settings.root)
    buckets = {}
    for obj in targets:
        mesh = obj.data
        ids = mesh.attributes.get(ID_ATTRIBUTE)
        layer = mesh.color_attributes.get(settings.color_attribute)
        if not ids or not layer or not mesh.get('bar_ao_baked'):
            raise ValueError(f'{obj.name}: missing source vertex IDs or AO color attribute; bake first')
        rgba = np.empty(len(layer.data) * 4, dtype=np.float32)
        layer.data.foreach_get('color', rgba)
        alpha = rgba.reshape((-1, 4))[:, 3]
        if layer.domain == 'CORNER':
            alpha = to_points(mesh, alpha)
        used = {loop.vertex_index for loop in mesh.loops}
        for i in used:
            buckets.setdefault(int(ids.data[i].value), []).append(float(alpha[i]))
    return {i: sum(values) / len(values) for i, values in buckets.items()}


def plate_dimensions(settings, bounds):
    low, high = bounds
    # Footprint squares are 16 world units. Inferred extents include offsets
    # from the origin, because the widget cannot offset the plate center.
    fx = settings.footprint_x or max(1, math.ceil(2 * max(abs(low.x), abs(high.x)) / 16))
    fy = settings.footprint_y or max(1, math.ceil(2 * max(abs(low.y), abs(high.y)) / 16))
    side = 2 * max(fx, fy) * 16
    return side, side


def plate_pixels(visibility, width, depth):
    """OBJ2S3O-style balancing, with zero alpha on every boundary pixel."""
    resolution = visibility.shape[0]
    ao = visibility.astype(np.float64) * 255
    modifier = 255 - min(ao[0, 0], ao[0, -1], ao[-1, 0], ao[-1, -1]) - 3
    ao = np.minimum(ao + modifier, 255)
    ao = 255 - np.maximum(ao - (255 - ao) / 8, 32)
    y, x = np.indices(ao.shape)
    edge_x = np.minimum(x, resolution - 1 - x)
    edge_y = np.minimum(y, resolution - 1 - y)
    # Fade the outer eight world units; finite even for very small plates.
    fade = np.minimum(np.clip(edge_x / max(1, resolution * 8 / width), 0, 1),
                      np.clip(edge_y / max(1, resolution * 8 / depth), 0, 1))
    rgba = np.zeros((*ao.shape, 4), dtype=np.float32)
    rgba[:, :, 3] = ao * fade / 255
    return rgba


def bake_plate(context, settings, filepath):
    if context.mode != 'OBJECT':
        raise ValueError('Switch to Object Mode before baking')
    targets = objects_for_model(context, settings)
    image = None
    resolution = int(settings.resolution)
    try:
        with bake_scene(context, settings) as (scene, material, original_scene):
            copies, bounds = evaluated_copies(context, scene, material, targets, settings.root, explode=False)
            width, depth = plate_dimensions(settings, bounds)
            plane = add_plane(scene, material, model_frame(settings.root), width, depth, settings.ground_z)
            image = bpy.data.images.new('_BAR_AO_PLATE_IMAGE', width=resolution,
                                        height=resolution, alpha=True, is_data=True)
            texture = material.node_tree.nodes.new('ShaderNodeTexImage')
            texture.image = image
            material.node_tree.nodes.active = texture
            context.window.scene = scene
            plane.select_set(True)
            context.view_layer.objects.active = plane
            result = bpy.ops.object.bake(type='EMIT', target='IMAGE_TEXTURES')
            if 'FINISHED' not in result:
                raise RuntimeError('Plate bake cancelled')
            pixels = np.empty(resolution ** 2 * 4, dtype=np.float32)
            image.pixels.foreach_get(pixels)
            visibility = pixels.reshape((resolution, resolution, 4))[:, :, :3].mean(axis=2)
            rgba = plate_pixels(visibility, width, depth)
            # Legacy plate UVs are engine (+X, -Z). In S3O-compatible source
            # space these are Blender (-X, -Y); default GLB uses (+X, +Y).
            if s3o_compatible(settings, original_scene):
                rgba = rgba[::-1, ::-1]
            image.pixels.foreach_set(rgba.ravel())
            image.file_format = 'PNG'
            image.filepath_raw = filepath
            # save_render would apply scene view transforms; save stores data.
            image.save()
        return width, depth
    finally:
        if image:
            bpy.data.images.remove(image)


def restore_preview(targets):
    empty_meshes = set()
    for obj in targets:
        stored = obj.get('bar_ao_preview_slots')
        if stored is None:
            continue
        record = json.loads(stored)
        previews = []
        for index, slot in enumerate(obj.material_slots):
            if slot.material and slot.material.get('bar_ao_preview'):
                previews.append(slot.material)
            if index < len(record):
                link, name = record[index]
                slot.link = link
                slot.material = bpy.data.materials.get(name) if name else None
            else:
                slot.material = None
                slot.link = 'DATA'
        if not record:
            empty_meshes.add(obj.data)
        del obj['bar_ao_preview_slots']
        for material in previews:
            if material.users == 0:
                bpy.data.materials.remove(material)
    for mesh in empty_meshes:
        mesh.materials.clear()
    # Shared meshes with initially empty slots can release another instance's
    # override when their temporary slot is removed.
    for material in list(bpy.data.materials):
        if material.get('bar_ao_preview') and material.users == 0:
            bpy.data.materials.remove(material)


def preview(context, settings, shaded=False):
    targets = objects_for_model(context, settings)
    restore_preview(targets)
    for obj in targets:
        if settings.color_attribute not in obj.data.color_attributes:
            raise ValueError(f'{obj.name}: bake AO before previewing')
    records = {obj: json.dumps([(s.link, s.material.name if s.material else '') for s in obj.material_slots])
               for obj in targets}
    for obj in targets:
        obj['bar_ao_preview_slots'] = records[obj]
        if not obj.material_slots:
            obj.data.materials.append(None)
        for slot in obj.material_slots:
            original = slot.material
            material = original.copy() if shaded and original and original.use_nodes else bpy.data.materials.new('_BAR_AO_PREVIEW')
            material['bar_ao_preview'] = True
            material.use_nodes = True
            nodes, links = material.node_tree.nodes, material.node_tree.links
            attribute = nodes.new('ShaderNodeVertexColor')
            attribute.layer_name = settings.color_attribute
            principled = [n for n in nodes if n.type == 'BSDF_PRINCIPLED']
            if shaded and principled:
                for shader in principled:
                    socket = shader.inputs['Base Color']
                    multiply = nodes.new('ShaderNodeMixRGB')
                    multiply.blend_type = 'MULTIPLY'
                    multiply.inputs[0].default_value = 1
                    multiply.inputs[1].default_value = socket.default_value
                    if socket.is_linked:
                        links.new(socket.links[0].from_socket, multiply.inputs[1])
                    links.new(attribute.outputs['Alpha'], multiply.inputs[2])
                    links.new(multiply.outputs[0], socket)
            else:
                output = next((n for n in nodes if n.type == 'OUTPUT_MATERIAL' and n.is_active_output), None)
                if output is None:
                    output = nodes.new('ShaderNodeOutputMaterial')
                emission = nodes.new('ShaderNodeEmission')
                links.new(attribute.outputs['Alpha'], emission.inputs['Color'])
                links.new(emission.outputs[0], output.inputs['Surface'])
            slot.link = 'OBJECT'
            slot.material = material
    if context.space_data and context.space_data.type == 'VIEW_3D':
        context.space_data.shading.type = 'MATERIAL'
