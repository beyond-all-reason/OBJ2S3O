# SPDX-License-Identifier: GPL-2.0-or-later
"""Panel adapter; canonical wreck implementation lives in BAR-Dev-MCP's skill."""
import importlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty


def helpers(workspace):
    local = Path(workspace)/'BAR-Dev-MCP/skills/bar-generate-wrecks/scripts'
    bundled = Path(__file__).parent/'skills/bar-generate-wrecks/scripts'
    folder = local if (local/'__init__.py').is_file() else bundled
    if not (folder/'__init__.py').is_file():
        raise ValueError('Wreck skill scripts not found; choose the complete meta-workspace')
    name = '_bar_wreck_helpers'
    # Reload on each operation so skill iteration does not require restarting Blender.
    for key in list(sys.modules):
        if key == name or key.startswith(name+'.'):
            del sys.modules[key]
    spec = importlib.util.spec_from_file_location(name, folder/'__init__.py', submodule_search_locations=[str(folder)])
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
    return importlib.import_module(name+'.wreck_blender'), folder


class WreckSettings(bpy.types.PropertyGroup):
    workspace: StringProperty(name='Meta-workspace', subtype='DIR_PATH')
    python_executable: StringProperty(name='Preflight Python', default='python',
        description='External Python with pcpp; Blender itself need not install compiler dependencies')
    unit: StringProperty(name='Unit ID', default='armstump')
    manifest_path: StringProperty(name='Inspection Manifest', subtype='FILE_PATH',
        description='Optional JSON from inspect_wreck.py; useful when preflight runs outside Blender')
    output_folder: StringProperty(name='Wreck Output Folder', subtype='DIR_PATH')
    seed: IntProperty(name='Seed Override', default=-1, min=-1,
        description='-1 selects a stable seed derived from the unit ID; nonnegative values override it')
    rotation_degrees: FloatProperty(name='Rotation Limit (degrees)', default=6, min=0, max=45)
    displacement_fraction: FloatProperty(name='Displacement Fraction', default=.01, min=0, max=.1,
        description='Maximum displacement-vector length as a fraction of the intact largest dimension')
    patch_coverage: FloatProperty(name='Team-color Damage Coverage', default=.5, min=0, max=1, subtype='FACTOR')
    patch_scale_fraction: FloatProperty(name='Patch Size Fraction', default=.18, min=.001, max=2)
    pose_mode: EnumProperty(name='Source Pose', items=[
        ('DEFAULT', 'Default Pose', 'No bot collapse posing; small non-base rotations are applied'),
        ('FINAL_POSE', 'Confirmed Death Final Pose', 'Use evaluated current pose; suppress random rotations')], default='DEFAULT')
    pose_evidence: StringProperty(name='Death Pose Evidence',
        description='For an authored death: source blend, Action/slot, final frame and corresponding Killed helper')
    bake_ao: BoolProperty(name='Bake AO Before Export', default=True)
    summary: StringProperty(name='Inspection Result')


class WreckOperator:
    def execute(self, context):
        try:
            return self.run(context)
        except Exception as error:
            self.report({'ERROR'}, str(error))
            import traceback
            traceback.print_exc()
            return {'CANCELLED'}


class WRECK_OT_inspect(WreckOperator, bpy.types.Operator):
    bl_idname = 'glb_ao.wreck_inspect'
    bl_label = 'Inspect Unit for Wreck'
    bl_description = ('Read the matching UnitsV2 BOS, source GLB, UnitDef and PBR textures. '
                      'Resolve the corpse-1 path without editing game files. Uses external Python with pcpp')

    def run(self, context):
        settings = context.scene.glb_wreck
        _, folder = helpers(settings.workspace)
        if settings.manifest_path:
            manifest = json.loads(Path(bpy.path.abspath(settings.manifest_path)).read_text(encoding='utf-8'))
            if manifest['unit'] != settings.unit:
                raise ValueError('Manifest unit differs from Unit ID')
        else:
            args = [settings.python_executable, str(folder/'inspect_wreck.py'), settings.unit,
                    '--workspace', bpy.path.abspath(settings.workspace)]
            if settings.seed >= 0:
                args.extend(['--seed', str(settings.seed)])
            completed = subprocess.run(args, capture_output=True, text=True, timeout=60, shell=False)
            if completed.returncode:
                raise ValueError(completed.stderr.strip() or completed.stdout.strip())
            manifest = json.loads(completed.stdout)
        context.scene['bar_wreck_inspection'] = json.dumps(manifest)
        settings.summary = f"{len(manifest['model']['pieces'])} nodes; {len(manifest['death']['removed'])} removed; base: {manifest['model']['base']}"
        self.report({'INFO'}, settings.summary)
        return {'FINISHED'}


class WRECK_OT_load(WreckOperator, bpy.types.Operator):
    bl_idname = 'glb_ao.wreck_load_source'
    bl_label = 'Load Intact Source in New Scene'
    bl_description = ('Load the inspected Armada GLB into a separate scene. The existing scene and '
                      'unsaved source data are retained; generation always starts from this intact hierarchy')

    def run(self, context):
        import blender_glb_ao as addon
        if 'bar_wreck_inspection' not in context.scene:
            raise ValueError('Inspect a unit first')
        manifest = json.loads(context.scene['bar_wreck_inspection'])
        old_scene = context.scene
        old_config = {prop.identifier: getattr(old_scene.glb_wreck, prop.identifier)
                      for prop in old_scene.glb_wreck.bl_rna.properties if not prop.is_readonly}
        scene = bpy.data.scenes.new(manifest['unit']+' intact source')
        context.window.scene = scene
        try:
            addon.load_glb(context, manifest['source']['authoring_glb'], recoil_z_up=True)
            scene['bar_wreck_inspection'] = json.dumps(manifest)
            for name, value in old_config.items():
                setattr(scene.glb_wreck, name, value)
        except Exception:
            context.window.scene = old_scene
            for obj in list(scene.objects):
                bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.scenes.remove(scene)
            raise
        return {'FINISHED'}


class WRECK_OT_generate(WreckOperator, bpy.types.Operator):
    bl_idname = 'glb_ao.wreck_generate'
    bl_label = 'Generate Wreck Working Scene'
    bl_description = ('Copy the intact hierarchy, remove corpse-1 debris, apply seeded geometry and '
                      'team-color damage, merge an export mesh and fit bounds. Retains editable damaged pieces')

    def run(self, context):
        import blender_glb_ao as addon
        settings = context.scene.glb_wreck
        module, _ = helpers(settings.workspace)
        if 'bar_wreck_inspection' not in context.scene:
            raise ValueError('Inspect the source unit first')
        manifest = json.loads(context.scene['bar_wreck_inspection'])
        if settings.unit != manifest['unit']:
            raise ValueError('Unit ID changed; inspect again')
        config = {name: getattr(settings, name) for name in ('rotation_degrees', 'displacement_fraction',
                  'patch_coverage', 'patch_scale_fraction', 'pose_mode', 'pose_evidence')}
        config['seed'] = settings.seed if settings.seed >= 0 else manifest['settings']['seed']
        config['ground_z'] = context.scene.glb_ao.ground_z
        previous = {prop.identifier: getattr(settings, prop.identifier)
                    for prop in settings.bl_rna.properties if not prop.is_readonly}
        scene, report = module.generate(context, manifest, context.scene.glb_ao.root, addon, config)
        for name, value in previous.items():
            setattr(scene.glb_wreck, name, value)
        scene.glb_wreck.summary = 'Generated; heap: '+report['heap']['selected']['object']
        self.report({'INFO'}, 'Generated a separate wreck scene; source retained')
        return {'FINISHED'}


class WRECK_OT_export(WreckOperator, bpy.types.Operator):
    bl_idname = 'glb_ao.wreck_export_bundle'
    bl_label = 'Bake AO and Save Wreck Bundle'
    bl_description = ('Bake the final static wreck pose and save a separate GLB, editable blend, '
                      'manifest and unapplied integration proposal. Does not patch UnitDefs or deploy assets')

    def run(self, context):
        settings = context.scene.glb_wreck
        if not settings.output_folder:
            raise ValueError('Choose a separate wreck output folder')
        module, _ = helpers(settings.workspace)
        report = module.export_bundle(context, bpy.path.abspath(settings.output_folder), settings.bake_ao)
        self.report({'INFO'}, 'Saved '+report['outputs']['glb'])
        return {'FINISHED'}


class WRECK_PT_panel(bpy.types.Panel):
    bl_label = 'Wreck Generation'
    bl_idname = 'WRECK_PT_panel'
    bl_parent_id = 'GLBAO_PT_panel'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'GLB AO'
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout, settings = self.layout, context.scene.glb_wreck
        for name in ('workspace', 'unit', 'python_executable', 'manifest_path'):
            layout.prop(settings, name)
        layout.operator('glb_ao.wreck_inspect')
        if settings.summary:
            layout.label(text=settings.summary)
        layout.operator('glb_ao.wreck_load_source')
        for name in ('seed', 'pose_mode'):
            layout.prop(settings, name)
        if settings.pose_mode == 'FINAL_POSE':
            layout.prop(settings, 'pose_evidence')
        for name in ('rotation_degrees', 'displacement_fraction', 'patch_coverage', 'patch_scale_fraction'):
            layout.prop(settings, name)
        layout.operator('glb_ao.wreck_generate')
        layout.prop(settings, 'output_folder')
        layout.prop(settings, 'bake_ao')
        layout.operator('glb_ao.wreck_export_bundle')
        layout.label(text='PBR dead state; vertex-alpha AO rendering deferred')


CLASSES = (WreckSettings, WRECK_OT_inspect, WRECK_OT_load, WRECK_OT_generate, WRECK_OT_export, WRECK_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.glb_wreck = PointerProperty(type=WreckSettings)


def unregister():
    del bpy.types.Scene.glb_wreck
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
