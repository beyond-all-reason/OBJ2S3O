# SPDX-License-Identifier: GPL-2.0-or-later
"""Vehicle-track panel and Blender adapter. Source meshes are read-only."""
import hashlib
import json
from pathlib import Path

import bpy
import numpy as np
from bpy.props import EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy_extras.io_utils import ExportHelper
from mathutils import Matrix, Vector

from . import baking, tracks


class TrackSettings(bpy.types.PropertyGroup):
    source_normal: StringProperty(name='Source Normal Map', subtype='FILE_PATH',
        description='Optional verified external normal map recorded in the recipe; geometry-only tracks do not sample it')
    contact_height: FloatProperty(name='Contact Height', default=1, min=0.001,
        description='Include upward-projected surfaces strictly below Ground Height + this many world units')
    forward_axis: EnumProperty(name='Forward', items=[
        ('AUTO', 'From Model Convention', '+Y for s3ocompat, -Y otherwise; verify using an asymmetric model'),
        ('POS_Y', '+Y', 'Model-root +Y is forward and +X is right'),
        ('NEG_Y', '-Y', 'Model-root -Y is forward and -X is right')], default='AUTO')
    padding_percent: FloatProperty(name='Padding Each Side (%)', default=10, min=0, max=100,
        description='Expand image Y by this percent of the contact span on EACH side; output height is 256 pixels')
    brightness: FloatProperty(name='Soil Brightness', default=0.75, min=0, max=1)
    opacity: FloatProperty(name='Opacity', default=0.75, min=0, max=1)
    breakup: FloatProperty(name='Soil Breakup', default=0.15, min=0, max=1,
        description='Seeded variation only; does not invent tread lugs or transfer material textures')
    seed: IntProperty(name='Soil Seed', default=0, min=0)
    seam_blend: FloatProperty(name='Seam Blend', default=0.08, min=0.01, max=0.25, subtype='FACTOR',
        description='Fraction of each longitudinal edge blended to join the full contact projection')
    lifetime: FloatProperty(name='Track Lifetime Strength', default=6, min=0.01,
        description='Proposed UnitDef trackstrength; affects lifetime, not encoded darkness')
    manifest: StringProperty(name='Last Track Manifest', subtype='FILE_PATH')
    summary: StringProperty(name='Track Result')


def model_triangles(context):
    settings = context.scene.glb_ao
    if context.mode != 'OBJECT':
        raise ValueError('Generate tracks in Object Mode to preserve pending mesh edits')
    targets = baking.objects_for_model(context, settings)
    sign = 1 if baking.s3o_compatible(settings, context.scene) else -1
    forward = context.scene.glb_tracks.forward_axis
    if forward != 'AUTO':
        sign = 1 if forward == 'POS_Y' else -1
    inverse = baking.model_frame(settings.root).inverted()
    basis = Matrix.Diagonal((sign, sign, 1, 1)) @ inverse
    depsgraph = context.evaluated_depsgraph_get()
    triangles = []
    for obj in targets:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            matrix = basis @ evaluated.matrix_world
            points = [matrix @ vertex.co for vertex in mesh.vertices]
            triangles.extend([[tuple(points[i]) for i in tri.vertices] for tri in mesh.loop_triangles])
        finally:
            evaluated.to_mesh_clear()
    return np.asarray(triangles, dtype=np.float64), targets, basis, sign


def bake_track(context, filepath):
    triangles, targets, basis, sign = model_triangles(context)
    settings, ao = context.scene.glb_tracks, context.scene.glb_ao
    projection = tracks.project_contact(triangles, ao.ground_z, settings.contact_height,
        settings.padding_percent / 100, 256)
    encoded = tracks.encode_track(projection, settings.brightness, settings.opacity,
        settings.breakup, settings.seed, settings.seam_blend)
    source = ao.root.get('bar_ao_source', '')
    extras = json.loads(ao.root.get('bar_ao_extras', '{}'))
    material_inputs = {'references': {key: extras[key] for key in ('tex1', 'tex2', 's3ocompat') if key in extras},
                       'textures': {}, 'normal_candidates': [], 'normal_used_for_track': False}
    # Read the GLB metadata retained by load_glb, then query the source game's
    # unittextures tree. Filename matches are candidates, not material identity.
    game = next((parent for parent in Path(source).parents if (parent / 'unittextures').is_dir()), None) if source else None
    if game:
        files = [file for file in (game / 'unittextures').rglob('*') if file.is_file()]
        for key in ('tex1', 'tex2'):
            name = Path(extras.get(key, '')).name.casefold()
            material_inputs['textures'][key] = [str(file.resolve()) for file in files if file.name.casefold() == name]
        stem = Path(extras.get('tex1', '')).stem
        for suffix in ('_Color', '_Albedo'):
            if stem.casefold().endswith(suffix.casefold()):
                stem = stem[:-len(suffix)]
                break
        if stem:
            material_inputs['normal_candidates'] = [str(file.resolve()) for file in files
                if file.stem.casefold() == (stem + '_Normal').casefold()]
    if settings.source_normal:
        normal = Path(bpy.path.abspath(settings.source_normal)).resolve()
        if not normal.is_file():
            raise ValueError('Source Normal Map does not exist')
        material_inputs['selected_normal'] = {'path': str(normal),
            'sha256': hashlib.sha256(normal.read_bytes()).hexdigest(),
            'packing': 'not decoded in geometry-only mode; verify effective material before transfer'}
    metadata = {'sources': {'model': source, 'sha256': hashlib.sha256(Path(source).read_bytes()).hexdigest()
                            if source and Path(source).is_file() else None,
                            'blender': bpy.app.version_string, 'root': ao.root.name,
                            'import_sha256': ao.root.get('bar_ao_digest'),
                            'materials': material_inputs,
                            'pieces': [obj.name for obj in targets],
                            'evaluated_triangles_sha256': hashlib.sha256(triangles.tobytes()).hexdigest(),
                            'frame': context.scene.frame_current + context.scene.frame_subframe},
                'coordinates': {'units': 'engine world units', 's3ocompat': baking.s3o_compatible(ao, context.scene),
                                'forward_root_axis': '+Y' if sign == 1 else '-Y',
                                'world_to_track': [list(row) for row in basis],
                                'image_columns': 'increasing forward', 'image_rows': 'increasing right (top to bottom)',
                                'runtime_mapping': 'source-verified; rendered BAR check pending'},
                'settings': {key: getattr(settings, key) for key in
                    ('contact_height', 'padding_percent', 'brightness', 'opacity', 'breakup', 'seed', 'seam_blend', 'lifetime')},
                'appearance': {'mode': 'geometry contact with seeded soil breakup; no material transfer',
                               'encoding': 'RGB BMP: R brightness, G opacity, B zero'}}
    metadata['settings'].update(ground=ao.ground_z, resolution_y=256, resolution_x=encoded.shape[1], supersampling=2,
        repeat_policy='whole contact projection', contact_policy='lowest upward hit strictly below ground + contact_height')
    report, manifest = tracks.save_bundle(bpy.path.abspath(filepath), projection, encoded, metadata)
    settings.manifest = str(manifest)
    settings.summary = f"{encoded.shape[1]} × 256; width {projection['width']:.3f}; stretch {projection['length']/projection['width']:.4f}"
    return report


class TrackOperator:
    def execute(self, context):
        try:
            return self.run(context)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            import traceback
            traceback.print_exc()
            return {'CANCELLED'}


class TRACK_OT_bake(TrackOperator, bpy.types.Operator, ExportHelper):
    bl_idname = 'glb_ao.bake_track'
    bl_label = 'Generate Vehicle Track BMP'
    bl_description = ('Project all visible model surfaces below Ground Height + Contact Height, '
                      'join the full contact repeat and save a Recoil RGB BMP, decoded and tiled PNGs, '
                      'raw contact mask, recipe and proposed UnitDef values. Does not update the GLB or game')
    filename_ext = '.bmp'
    filter_glob: StringProperty(default='*.bmp', options={'HIDDEN'})

    def invoke(self, context, event):
        if not self.filepath:
            root = context.scene.glb_ao.root
            source = root.get('bar_ao_source', '') if root else ''
            stem = Path(source).stem if source else 'vehicle'
            folder = Path(bpy.data.filepath).parent if bpy.data.filepath else Path(source).parent
            self.filepath = str(folder / 'track_output' / (stem.lower() + '_tracks.bmp'))
        return ExportHelper.invoke(self, context, event)

    def run(self, context):
        bake_track(context, self.filepath)
        self.report({'INFO'}, context.scene.glb_tracks.summary)
        return {'FINISHED'}


def _create_preview_scene(context):
    """Separate evaluated model copies above three decoded repeats, on neutral soil."""
    original = context.scene
    manifest = Path(bpy.path.abspath(original.glb_tracks.manifest))
    report = json.loads(manifest.read_text(encoding='utf-8'))
    triangles, targets, basis, _ = model_triangles(context)
    if hashlib.sha256(triangles.tobytes()).hexdigest() != report['sources']['evaluated_triangles_sha256']:
        raise ValueError('The evaluated model or forward convention changed; generate the track again')
    if original.glb_ao.ground_z != report['settings']['ground']:
        raise ValueError('Ground Height changed; generate the track again')
    scene = bpy.data.scenes.new(manifest.stem + ' Track Preview')
    scene.render.engine = 'CYCLES'
    scene.cycles.samples = 16
    scene.render.resolution_x, scene.render.resolution_y = 1000, 900
    scene.render.resolution_percentage = 100
    scene.view_settings.view_transform = 'Standard'
    scene.world = bpy.data.worlds.new('Track Preview World')
    scene.world.use_nodes = True
    scene.world.node_tree.nodes['Background'].inputs['Color'].default_value = (0.25, 0.25, 0.25, 1)
    created = []

    def material(name, color):
        mat = bpy.data.materials.new(name)
        mat.diffuse_color = color
        mat.use_nodes = True
        mat.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value = color
        return mat

    def plane(name, x0, x1, y0, y1, z, mat):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata([(x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)], [], [(0, 1, 2, 3)])
        uv = mesh.uv_layers.new()
        for item, value in zip(uv.data, ((0, 1), (0, 0), (1, 0), (1, 1))):
            item.uv = value
        obj = bpy.data.objects.new(name, mesh)
        scene.collection.objects.link(obj)
        mesh.materials.append(mat)
        created.append(obj)
        return obj

    try:
        body = material('Track Preview Model', (0.16, 0.21, 0.26, 1))
        depsgraph = context.evaluated_depsgraph_get()
        for obj in targets:
            evaluated = obj.evaluated_get(depsgraph)
            mesh = bpy.data.meshes.new_from_object(evaluated, depsgraph=depsgraph)
            copy = bpy.data.objects.new('Preview ' + obj.name, mesh)
            scene.collection.objects.link(copy)
            copy.matrix_world = basis @ evaluated.matrix_world
            mesh.materials.clear()
            mesh.materials.append(body)
            created.append(copy)
        width, length = report['tracks']['width'], report['tracks']['repeat_length']
        low, high = report['projection']['bounds']['forward']
        ground = report['settings']['ground']
        soil = material('Track Preview Soil', (0.35, 0.30, 0.22, 1))
        plane('Soil', -width * 2, width * 2, low - 2.5 * length, high + 0.5 * length, ground - 0.04, soil)
        mat = material('Decoded Track (three repeats)', (1, 1, 1, 1))
        nodes, links = mat.node_tree.nodes, mat.node_tree.links
        image_node = nodes.new('ShaderNodeTexImage')
        # A fresh datablock sees regenerated pixels without reloading an image
        # the artist may already have open with unsaved edits.
        image_node.image = bpy.data.images.load(report['outputs']['tiled']['path'], check_existing=False)
        image_node.extension = 'EXTEND'
        shader = nodes.get('Principled BSDF')
        links.new(image_node.outputs['Color'], shader.inputs['Base Color'])
        links.new(image_node.outputs['Alpha'], shader.inputs['Alpha'])
        right_min, right_max = report['projection']['bounds']['right']
        plane('Decoded Track Strip', right_min, right_max, low - 2 * length, high, ground - 0.02, mat)
        light_data = bpy.data.lights.new('Track Preview Sun', 'SUN')
        light_data.energy = 3
        light = bpy.data.objects.new('Track Preview Sun', light_data)
        scene.collection.objects.link(light)
        light.rotation_euler = (0.4, -0.5, -0.4)
        created.append(light)
        camera_data = bpy.data.cameras.new('Track Preview Camera')
        camera_data.type = 'ORTHO'
        camera_data.ortho_scale = max(3.5 * length, 3.0 * width)
        camera = bpy.data.objects.new('Track Preview Camera', camera_data)
        scene.collection.objects.link(camera)
        camera.location = (width * 1.7, length * 0.5, length * 2.6)
        target = Vector((0, -length * 0.75, ground))
        camera.rotation_euler = (target - camera.location).to_track_quat('-Z', 'Y').to_euler()
        scene.camera = camera
        created.append(camera)
        scene['bar_track_preview_manifest'] = str(manifest)
        scene.glb_tracks.manifest = str(manifest)
        scene.glb_tracks.summary = 'Decoded texture preview; rendered BAR validation pending'
        context.window.scene = scene
        return scene
    except Exception:
        context.window.scene = original
        for obj in created:
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.scenes.remove(scene)
        raise


def create_preview_scene(context):
    original = context.scene
    kinds = ('objects', 'scenes', 'meshes', 'materials', 'worlds', 'cameras', 'lights', 'images')
    before = {kind: set(getattr(bpy.data, kind)) for kind in kinds}
    try:
        return _create_preview_scene(context)
    except Exception:
        context.window.scene = original
        for kind in kinds:
            collection = getattr(bpy.data, kind)
            for block in set(collection) - before[kind]:
                collection.remove(block)
        raise


class TRACK_OT_preview(TrackOperator, bpy.types.Operator):
    bl_idname = 'glb_ao.preview_track'
    bl_label = 'Create Track Preview Scene'
    bl_description = ('Create a separate scene with evaluated model copies above three decoded track repeats '
                      'on neutral soil. Source objects remain intact. Render its camera to inspect alignment; '
                      'this preview does not simulate Recoil track emission')

    def run(self, context):
        create_preview_scene(context)
        return {'FINISHED'}


class TRACK_PT_panel(bpy.types.Panel):
    bl_label = 'Vehicle Track'
    bl_idname = 'TRACK_PT_panel'
    bl_parent_id = 'GLBAO_PT_panel'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'GLB AO'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout, settings = self.layout, context.scene.glb_tracks
        layout.prop(context.scene.glb_ao, 'ground_z')
        for name in ('contact_height', 'forward_axis', 'padding_percent',
                     'brightness', 'opacity', 'breakup', 'seed', 'seam_blend', 'lifetime'):
            layout.prop(settings, name)
        layout.prop(settings, 'source_normal')
        layout.label(text='256 px high; width fits physical aspect')
        layout.label(text='One repeat = full contact projection')
        layout.label(text='Geometry + soil breakup; no material transfer')
        layout.operator('glb_ao.bake_track')
        if settings.summary:
            layout.label(text=settings.summary)
        if settings.manifest:
            layout.prop(settings, 'manifest')
            row = layout.row()
            row.enabled = context.scene.glb_ao.root is not None
            row.operator('glb_ao.preview_track')


CLASSES = (TrackSettings, TRACK_OT_bake, TRACK_OT_preview, TRACK_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.glb_tracks = PointerProperty(type=TrackSettings)


def unregister():
    del bpy.types.Scene.glb_tracks
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
