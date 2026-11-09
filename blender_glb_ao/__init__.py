# SPDX-License-Identifier: GPL-2.0-or-later
bl_info = {
    'name': 'BAR GLB AO Baker',
    'author': 'Beyond All Reason; based on ChrisFloofyKitsune and Beherith AO tooling',
    'version': (0, 1, 2),
    'blender': (5, 2, 0),
    'location': 'View3D > Sidebar > GLB AO',
    'description': 'Bake vertex-color alpha AO and ground plates for GLB models',
    'category': 'Import-Export',
}

import json
from pathlib import Path
import tempfile

import bpy
from bpy.props import (BoolProperty, EnumProperty, FloatProperty, IntProperty,
                       PointerProperty, StringProperty)
from bpy_extras.io_utils import ImportHelper, ExportHelper

from .glb import GLB, ID_ATTRIBUTE
from . import baking


class GLBAOSettings(bpy.types.PropertyGroup):
    root: PointerProperty(name='Model Root', type=bpy.types.Object)
    color_attribute: StringProperty(name='Color Attribute', default='COLOR_0')
    source_axes: EnumProperty(name='File Axes', items=[
        ('RECOIL', 'Recoil / Blender Z Up', 'For Recoil files exported with +Y Up disabled'),
        ('GLTF', 'Standard glTF Y Up', 'For ordinary glTF files')], default='RECOIL')
    unit_type: EnumProperty(name='Unit Type', items=[
        ('BUILDING', 'Building', 'Ground blocker twice the model width and depth'),
        ('MOBILE', 'Mobile', 'Model-size blocker lowered by half the maximum horizontal dimension'),
        ('FLYING', 'Flying', 'No ground blocker')], default='MOBILE')
    distance: FloatProperty(name='Ray Distance', default=128, min=0.001, soft_max=512,
                            description='AO radius in model world units')
    samples: IntProperty(name='Ray Samples', default=1024, min=1024, soft_max=8192, max=1048576,
                         description='Minimum AO rays per vertex corner or plate texel, per pose; '
                                     'at least 1024, rounded up to multiples of 128')
    minimum: FloatProperty(name='Minimum Brightness', default=0, min=0, max=1)
    gain: FloatProperty(name='Gain', default=1, min=0, soft_max=2)
    bias: FloatProperty(name='Bias', default=0.05, min=-1, max=1)
    ground_z: FloatProperty(name='Ground Height', default=0,
                            description='Ground level in model-root coordinates')
    walk: BoolProperty(name='Average Walk Cycle', default=False)
    animation_object: PointerProperty(name='Animation Object', type=bpy.types.Object,
                                      description='Armature or object owning the selected Action')
    action: PointerProperty(name='Action Override', type=bpy.types.Action,
                           description='Optional. Empty uses the current Actions and NLA timeline')
    frame_start: IntProperty(name='First Frame', default=1)
    frame_end: IntProperty(name='Closing Frame (Excluded)', default=31)
    sampling: EnumProperty(name='Sampling', items=[
        ('FRAMES', 'Every Frame', 'Time average over every integer frame, excluding the closing pose'),
        ('KEYS', 'Keyframes Only', 'Equal-weight average of Action keys in the cycle')], default='FRAMES')
    footprint_x: IntProperty(name='Footprint X', default=0, min=0,
                             description='16-unit gameplay squares; 0 estimates from model bounds')
    footprint_y: IntProperty(name='Footprint Z', default=0, min=0,
                             description='16-unit gameplay squares; 0 estimates from model bounds')
    resolution: EnumProperty(name='Resolution', items=[('64', '64 × 64', ''), ('128', '128 × 128', '')], default='64')


class SafeOperator:
    def execute(self, context):
        try:
            return self.run(context)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            import traceback
            traceback.print_exc()
            return {'CANCELLED'}


def load_glb(context, filepath, recoil_z_up=True):
    source = GLB.read(filepath)
    if any(image.get('uri') and not image['uri'].startswith('data:')
           for image in source.document.get('images', [])):
        raise ValueError('Embed external images in the GLB before importing it for AO')
    before = set(bpy.data.objects)
    old_selection = list(context.selected_objects)
    old_active = context.view_layer.objects.active
    old_scale = context.scene.unit_settings.scale_length
    root = None
    try:
        with tempfile.TemporaryDirectory(prefix='bar_glb_ao_') as folder:
            path = Path(folder) / Path(filepath).name
            path.write_bytes(source.instrumented(recoil_z_up))
            context.scene.unit_settings.scale_length = 1
            result = bpy.ops.import_scene.gltf(filepath=str(path), merge_vertices=False,
                                               import_shading='NORMALS', bone_heuristic='BLENDER',
                                               disable_bone_shape=True, import_scene_extras=False)
            if 'FINISHED' not in result:
                raise RuntimeError('GLB import cancelled')
        created = set(bpy.data.objects) - before
        if not any(o.type == 'MESH' for o in created):
            raise ValueError('GLB contains no importable triangle meshes')
        root = bpy.data.objects.new(Path(filepath).stem + ' AO Root', None)
        context.collection.objects.link(root)
        for obj in created:
            if obj.parent is None:
                world = obj.matrix_world.copy()
                obj.parent = root
                obj.matrix_world = world
            if obj.type == 'MESH':
                if ID_ATTRIBUTE not in obj.data.attributes:
                    raise ValueError(f'{obj.name}: Blender did not retain source vertex IDs')
                obj.data['bar_ao_signature'] = baking.mesh_signature(obj.data)
                # Blender imports glTF COLOR_0 under the Blender name "Color".
                layer = obj.data.color_attributes.get('Color')
                if layer and 'COLOR_0' not in obj.data.color_attributes:
                    layer.name = 'COLOR_0'
                    for material in obj.data.materials:
                        if material and material.use_nodes:
                            for node in material.node_tree.nodes:
                                if node.type == 'VERTEX_COLOR' and node.layer_name == 'Color':
                                    node.layer_name = 'COLOR_0'
        root['bar_ao_source'] = str(Path(filepath).resolve())
        root['bar_ao_digest'] = source.digest
        scene_index = source.document.get('scene', 0)
        extras = source.document.get('scenes', [{}])[scene_index].get('extras', {})
        root['bar_ao_extras'] = json.dumps(extras)
        root['bar_ao_axes'] = 'RECOIL' if recoil_z_up else 'GLTF'
        bpy.ops.object.select_all(action='DESELECT')
        root.select_set(True)
        context.view_layer.objects.active = root
        context.scene.glb_ao.root = root
        context.scene.glb_ao.color_attribute = 'COLOR_0'
        return root
    except Exception:
        for obj in set(bpy.data.objects) - before:
            mesh = obj.data if obj.type == 'MESH' else None
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        for obj in old_selection:
            obj.select_set(True)
        context.view_layer.objects.active = old_active
        raise
    finally:
        context.scene.unit_settings.scale_length = old_scale


class GLBAO_OT_load(SafeOperator, bpy.types.Operator, ImportHelper):
    bl_idname = 'glb_ao.load'
    bl_label = 'Load GLB for AO'
    bl_description = ('Import a GLB using File Axes and make it the model root for AO baking. '
                      'Retain source vertex IDs so baked alpha can be written back with Update Source GLB. '
                      'The source file is changed only when you save the baked result')
    bl_options = {'REGISTER', 'UNDO'}
    filename_ext = '.glb'
    filter_glob: StringProperty(default='*.glb', options={'HIDDEN'})

    def run(self, context):
        load_glb(context, self.filepath, context.scene.glb_ao.source_axes == 'RECOIL')
        return {'FINISHED'}


class GLBAO_OT_use_active(SafeOperator, bpy.types.Operator):
    bl_idname = 'glb_ao.use_active'
    bl_label = 'Use Active Hierarchy'
    bl_description = ('Set the active object\'s topmost parent as Model Root. '
                      'AO operations include visible, render-enabled meshes in that hierarchy; '
                      'use Export New GLB to save a model authored in Blender')
    bl_options = {'REGISTER', 'UNDO'}

    def run(self, context):
        root = context.active_object
        if root is None:
            raise ValueError('Select a model object first')
        while root.parent:
            root = root.parent
        context.scene.glb_ao.root = root
        return {'FINISHED'}


class GLBAO_OT_explode(SafeOperator, bpy.types.Operator):
    bl_idname = 'glb_ao.mark_explode'
    bl_label = 'Set Explode on Selected'
    bl_description = ('Set or clear the ao_explode custom property on selected objects. '
                      'Marked objects and their descendants are temporarily separated during vertex AO baking')
    bl_options = {'REGISTER', 'UNDO'}
    enabled: BoolProperty(default=True)

    @classmethod
    def description(cls, context, properties):
        if properties.enabled:
            return ('Set ao_explode=true on selected objects. During vertex AO baking, each marked '
                    'subtree is moved at least 100 units away on temporary copies to prevent other '
                    'pieces occluding it. Original transforms and plate baking are unaffected')
        return ('Set ao_explode=false on selected objects so they remain in place and occlude '
                'the rest of the model during vertex AO baking. Descendants with their own '
                'ao_explode property remain independently marked')

    def run(self, context):
        for obj in context.selected_objects:
            obj['ao_explode'] = self.enabled
        return {'FINISHED'}


class GLBAO_OT_bake(SafeOperator, bpy.types.Operator):
    bl_idname = 'glb_ao.bake_vertex'
    bl_label = 'Bake Vertex AO'
    bl_description = ('Bake AO into Color Attribute alpha on the model\'s visible meshes, preserving RGB. '
                      'Use at least 1024 rays, the selected unit blocker and explode settings; '
                      'average the walk cycle when enabled and unify AO at positions within 0.001 mesh-local units. '
                      'Update Source GLB or Export New GLB saves the result to disk')
    bl_options = {'REGISTER', 'UNDO'}

    def run(self, context):
        settings = context.scene.glb_ao
        baking.restore_preview(baking.objects_for_model(context, settings))
        frames, meshes = baking.bake_vertex(context, settings)
        self.report({'INFO'}, f'Baked {meshes} meshes over {frames} pose(s). AO is in color alpha')
        return {'FINISHED'}


class GLBAO_OT_save(SafeOperator, bpy.types.Operator):
    bl_idname = 'glb_ao.update_source'
    bl_label = 'Update Source GLB'
    bl_description = ('Write baked AO alpha back to the GLB loaded through this add-on, preserving RGB, '
                      'geometry, UVs, hierarchy and animation. Keep the first original as .glb.ao-backup '
                      'and reject externally changed files or edited source meshes')

    def run(self, context):
        settings = context.scene.glb_ao
        root = settings.root
        if root is None or not root.get('bar_ao_source'):
            raise ValueError('Load a GLB through this add-on to update it in place')
        source = GLB.read(root['bar_ao_source'])
        if source.digest != root['bar_ao_digest']:
            raise ValueError('Source GLB changed since import; reload it before saving')
        values = baking.source_alpha(context, settings)
        root['bar_ao_digest'] = source.save_alpha(root['bar_ao_source'], values)
        self.report({'INFO'}, 'Updated source GLB colors; original backup retained')
        return {'FINISHED'}


class GLBAO_OT_plate(SafeOperator, bpy.types.Operator, ExportHelper):
    bl_idname = 'glb_ao.bake_plate'
    bl_label = 'Bake AO Plate PNG'
    bl_description = ('Bake the current model pose onto a ground plate using at least 1024 rays per texel. '
                      'Save a black RGBA PNG with occlusion in alpha, transparent edges and footprint-based '
                      'size, plus a JSON file with decal dimensions. Walk averaging and exploded positions '
                      'are not used for plates')
    filename_ext = '.png'
    filter_glob: StringProperty(default='*.png', options={'HIDDEN'})

    def invoke(self, context, event):
        root = context.scene.glb_ao.root
        if root:
            source = root.get('bar_ao_source')
            name = Path(source).stem if source else root.name
            self.filepath = str(Path(source).with_name(name + '_aoplane.png')) if source else name + '_aoplane.png'
        return ExportHelper.invoke(self, context, event)

    def run(self, context):
        settings = context.scene.glb_ao
        width, depth = baking.bake_plate(context, settings, self.filepath)
        metadata = {
            'image': Path(self.filepath).name,
            'world_width': width, 'world_depth': depth,
            'center': [0, 0],
            'buildinggrounddecalsizex': width / 16,
            'buildinggrounddecalsizey': depth / 16,
            'buildinggrounddecalalpha': 1,
            'usebuildinggrounddecal': True,
            's3ocompat': baking.s3o_compatible(settings, context.scene),
            'uv_axes': 'engine +X, engine -Z',
            'note': 'Add the PNG to the game AO atlas and set buildinggrounddecaltype to its atlas key. Do not additionally flip the source PNG.',
        }
        Path(self.filepath).with_suffix('.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf8')
        self.report({'INFO'}, f'Saved {width:g} × {depth:g} unit plate and decal-size JSON')
        return {'FINISHED'}


class GLBAO_OT_preview(SafeOperator, bpy.types.Operator):
    bl_idname = 'glb_ao.preview'
    bl_label = 'Preview AO'
    bl_description = ('Inspect baked color alpha with temporary AO preview materials, '
                      'or restore the original model materials')
    mode: EnumProperty(items=[
        ('AO', 'AO Only', 'Show baked color alpha as grayscale using temporary materials'),
        ('SHADED', 'Shaded AO', 'Multiply baked alpha into copied Principled material base colors'),
        ('RESTORE', 'Restore Materials', 'Restore material assignments saved before AO preview')])

    @classmethod
    def description(cls, context, properties):
        if properties.mode == 'AO':
            return ('Show the model\'s baked color alpha as grayscale: black is occluded, white is unoccluded. '
                    'Use temporary emission materials and switch the viewport to Material Preview; '
                    'Restore Materials returns the original assignments')
        if properties.mode == 'SHADED':
            return ('Preview baked AO by multiplying color alpha into copied Principled material base colors. '
                    'Use grayscale AO for materials without a Principled shader and switch to Material '
                    'Preview. Restore Materials returns the original assignments')
        return ('Restore the model\'s material assignments from before AO preview and remove unused '
                'temporary preview materials. Baked vertex alpha is retained')

    def run(self, context):
        settings = context.scene.glb_ao
        if self.mode == 'RESTORE':
            baking.restore_preview(baking.objects_for_model(context, settings))
        else:
            baking.preview(context, settings, self.mode == 'SHADED')
        return {'FINISHED'}


class GLBAO_OT_export(SafeOperator, bpy.types.Operator, ExportHelper):
    bl_idname = 'glb_ao.export_new'
    bl_label = 'Export New GLB'
    bl_description = ('Export the model hierarchy through Blender\'s GLB exporter with +Y Up disabled, '
                      'custom properties, tangents and the selected color attribute as COLOR_0. '
                      'Restore preview materials first and exclude AO helper data. This regenerates '
                      'the file; use Update Source GLB to preserve an originally loaded file')
    filename_ext = '.glb'
    filter_glob: StringProperty(default='*.glb', options={'HIDDEN'})

    def run(self, context):
        settings = context.scene.glb_ao
        root = settings.root
        if root is None:
            raise ValueError('Choose a model root first')
        if root.get('bar_ao_source') and Path(self.filepath).resolve() == Path(root['bar_ao_source']).resolve():
            raise ValueError('Use Update Source GLB for the original file, or choose a different export name')
        targets = baking.objects_for_model(context, settings)
        baking.restore_preview(targets)
        for obj in targets:
            baking.color_layer(obj.data, settings.color_attribute)
        previous_selection = list(context.selected_objects)
        previous_active = context.view_layer.objects.active
        objects = [root, *root.children_recursive]
        # Include armatures referenced by meshes, even if outside the hierarchy.
        objects = list(dict.fromkeys(objects + [m.object for o in targets for m in o.modifiers
                                               if m.type == 'ARMATURE' and m.object]))
        hidden_properties = []
        scene_properties = {}
        try:
            for owner in [context.scene, *objects, *(o.data for o in targets)]:
                for name in list(owner.keys()):
                    if name.startswith('bar_ao_'):
                        hidden_properties.append((owner, name, owner[name]))
            source_extras = json.loads(root.get('bar_ao_extras', '{}'))
            for name in ('tex1', 'tex2', 's3ocompat', 'midpos', 'mins', 'maxs', 'radius', 'height', 'fliptextures', 'invertteamcolor'):
                if name in source_extras and name not in context.scene:
                    scene_properties[name] = None
                    context.scene[name] = source_extras[name]
            for owner, name, value in hidden_properties:
                del owner[name]
            bpy.ops.object.select_all(action='DESELECT')
            for obj in objects:
                if obj.visible_get(view_layer=context.view_layer) and not obj.hide_render:
                    obj.select_set(True)
            result = bpy.ops.export_scene.gltf(
                filepath=self.filepath, export_format='GLB', use_selection=True,
                export_yup=False, export_extras=True, export_tangents=True,
                export_vertex_color='NAME', export_vertex_color_name=settings.color_attribute,
                export_active_vertex_color_when_no_material=True, export_all_vertex_colors=False,
                export_attributes=False, export_apply=False)
            if 'FINISHED' not in result:
                raise RuntimeError('GLB export cancelled')
            # Blender 5.2 exports registered Scene property groups as system
            # extras even when absent from scene.keys(). Strip our UI state
            # from this NEW authoring export, without touching geometry.
            output = GLB.read(self.filepath)
            for scene in output.document.get('scenes', []):
                scene.get('extras', {}).pop('glb_ao', None)
            Path(self.filepath).write_bytes(output.encode(output.document, output.binary))
        finally:
            for owner, name, value in hidden_properties:
                owner[name] = value
            for name in scene_properties:
                del context.scene[name]
            bpy.ops.object.select_all(action='DESELECT')
            for obj in previous_selection:
                obj.select_set(True)
            context.view_layer.objects.active = previous_active
        self.report({'INFO'}, 'Exported GLB with +Y Up disabled and AO color alpha')
        return {'FINISHED'}


class GLBAO_PT_panel(bpy.types.Panel):
    bl_label = 'GLB Ambient Occlusion'
    bl_idname = 'GLBAO_PT_panel'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'GLB AO'

    def draw(self, context):
        layout = self.layout
        settings = context.scene.glb_ao
        layout.prop(settings, 'source_axes')
        layout.operator('glb_ao.load', icon='IMPORT')
        layout.prop(settings, 'root')
        layout.operator('glb_ao.use_active')
        layout.prop(settings, 'color_attribute')
        box = layout.box()
        box.label(text='Vertex AO')
        for name in ('unit_type', 'ground_z', 'distance', 'samples', 'minimum', 'gain', 'bias'):
            box.prop(settings, name)
        row = box.row(align=True)
        row.operator('glb_ao.mark_explode', text='Mark Explode').enabled = True
        row.operator('glb_ao.mark_explode', text='Clear Explode').enabled = False
        box.label(text='Object custom property: ao_explode')
        box.prop(settings, 'walk')
        if settings.walk:
            for name in ('animation_object', 'action', 'frame_start', 'frame_end', 'sampling'):
                box.prop(settings, name)
        box.operator('glb_ao.bake_vertex')
        row = box.row(align=True)
        row.operator('glb_ao.preview', text='AO Only').mode = 'AO'
        row.operator('glb_ao.preview', text='Shaded AO').mode = 'SHADED'
        box.operator('glb_ao.preview', text='Restore Materials').mode = 'RESTORE'
        layout.operator('glb_ao.update_source', icon='FILE_TICK')
        layout.operator('glb_ao.export_new', icon='EXPORT')
        box = layout.box()
        box.label(text='Ground AO Plate')
        for name in ('footprint_x', 'footprint_y', 'resolution'):
            box.prop(settings, name)
        box.prop(settings, 'samples')
        box.operator('glb_ao.bake_plate')


CLASSES = (GLBAOSettings, GLBAO_OT_load, GLBAO_OT_use_active, GLBAO_OT_explode,
           GLBAO_OT_bake, GLBAO_OT_save, GLBAO_OT_plate, GLBAO_OT_preview,
           GLBAO_OT_export, GLBAO_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.glb_ao = PointerProperty(type=GLBAOSettings)


def unregister():
    for obj in list(bpy.data.objects):
        if obj.type == 'MESH' and obj.get('bar_ao_preview_slots') is not None:
            baking.restore_preview([obj])
    del bpy.types.Scene.glb_ao
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
