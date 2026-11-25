"""Exercise plate sizes with Blender: --background --factory-startup --python this file."""
import json
from pathlib import Path
import sys
import tempfile

import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import blender_glb_ao as addon

addon.register()
scene = bpy.context.scene
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=8, location=(0, 0, 5))
settings = scene.glb_ao
settings.root = bpy.context.object
assert settings.resolution == 'AUTO'
items = settings.bl_rna.properties['resolution'].enum_items
assert items['64'].value == 0 and items['128'].value == 1
bounds = (Vector((-64, -64, 0)), Vector((64, 64, 10)))
for fx, fz, expected in ((2, 2, 128), (7, 8, 128), (8, 7, 128),
                         (8, 8, 256), (9, 12, 256), (0, 8, 256),
                         (8, 0, 256), (0, 7, 128)):
    settings.footprint_x, settings.footprint_y = fx, fz
    assert addon.baking.plate_resolution(settings, bounds) == expected

with tempfile.TemporaryDirectory(prefix='bar_ao_resolution_') as temp:
    for choice, fx, fz, expected in (('AUTO', 2, 2, 128), ('AUTO', 8, 8, 256),
                                      ('64', 8, 8, 64), ('256', 2, 2, 256)):
        settings.resolution = choice
        settings.footprint_x, settings.footprint_y = fx, fz
        path = Path(temp) / f'{choice}_{fx}_{fz}.png'
        assert bpy.ops.glb_ao.bake_plate(filepath=str(path)) == {'FINISHED'}
        image = bpy.data.images.load(str(path), check_existing=False)
        assert tuple(image.size) == (expected, expected)
        pixels = list(image.pixels[:])
        alpha = pixels[3::4]
        assert max(alpha) > 0
        assert all(alpha[y * expected + x] == 0 for y in range(expected) for x in range(expected)
                   if x in (0, expected - 1) or y in (0, expected - 1))
        bpy.data.images.remove(image)
        metadata = json.loads(path.with_suffix('.json').read_text())
        assert metadata['world_width'] == 2 * max(fx, fz) * 16
        assert metadata['buildinggrounddecalsizex'] == 2 * max(fx, fz)
        assert not any(s.name.startswith('_BAR_AO_BAKE') for s in bpy.data.scenes)
        print(f'PASS: {choice} {fx}x{fz} footprint -> {expected}x{expected} PNG')

addon.unregister()
print('ALL PLATE RESOLUTION CHECKS PASSED')
