"""Build the independently installable legacy add-on ZIP; no Blender needed."""
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parent
package = root / 'blender_glb_ao'
destination = root / 'dist' / 'bar_glb_ao_v0.1.2.zip'
destination.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
    for name in ('__init__.py', 'glb.py', 'baking.py', 'README.md', 'LICENSE',
                 'skills/blender-glb-ao/SKILL.md'):
        path = package / name
        archive.write(path, f'blender_glb_ao/{name}')
print(destination)
