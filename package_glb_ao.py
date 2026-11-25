"""Build the independently installable legacy add-on ZIP; no Blender needed."""
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parent
package = root / 'blender_glb_ao'
destination = root / 'dist' / 'bar_glb_ao_v0.3.0.zip'
destination.parent.mkdir(exist_ok=True)
with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
    for name in ('__init__.py', 'glb.py', 'baking.py', 'tracks.py', 'track_ui.py', 'wreck_ui.py', 'README.md', 'LICENSE'):
        path = package / name
        archive.write(path, f'blender_glb_ao/{name}')
    # Canonical wreck workflow belongs to BAR-Dev-MCP. Include a portable snapshot
    # when building in the meta-workspace; the panel prefers the configured checkout.
    skill = root.parent / 'BAR-Dev-MCP' / 'skills' / 'bar-generate-wrecks'
    if skill.is_dir():
        for path in sorted(skill.rglob('*')):
            if path.is_file() and path.suffix in ('.py', '.md', '.yaml') and '__pycache__' not in path.parts:
                archive.write(path, 'blender_glb_ao/skills/bar-generate-wrecks/' + path.relative_to(skill).as_posix())
    else:
        print('Wreck skill not bundled: configure Meta-workspace to a BAR-Dev-MCP checkout to use the panel')
print(destination)
