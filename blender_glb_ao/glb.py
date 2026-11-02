# SPDX-License-Identifier: GPL-2.0-or-later
"""Small, Blender-independent GLB reader and color-only writer.

Original BIN bytes and unknown chunks are retained. New color accessors are
appended, so shared/interleaved/sparse accessors never get overwritten.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile

ID_ATTRIBUTE = '_BAR_AO_ID'
JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
COMPONENTS = {5120: ('b', 1), 5121: ('B', 1), 5122: ('h', 2),
              5123: ('H', 2), 5125: ('I', 4), 5126: ('f', 4)}
WIDTHS = {'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT4': 16}


class GLB:
    def __init__(self, raw):
        if len(raw) < 20 or struct.unpack_from('<4sII', raw) != (b'glTF', 2, len(raw)):
            raise ValueError('Expected a complete binary glTF 2.0 (.glb) file')
        self.digest = hashlib.sha256(raw).hexdigest()
        self.chunks = []
        offset = 12
        while offset < len(raw):
            if offset + 8 > len(raw):
                raise ValueError('Truncated GLB chunk header')
            size, kind = struct.unpack_from('<II', raw, offset)
            offset += 8
            if size % 4 or offset + size > len(raw):
                raise ValueError('Invalid GLB chunk length')
            self.chunks.append((kind, raw[offset:offset + size]))
            offset += size
        if self.chunks[0][0] != JSON_CHUNK:
            raise ValueError('GLB must start with a JSON chunk')
        self.document = json.loads(self.chunks[0][1])
        bins = [data for kind, data in self.chunks if kind == BIN_CHUNK]
        if len(bins) != 1:
            raise ValueError('Expected one embedded GLB binary buffer')
        buffers = self.document.get('buffers', [])
        if len(buffers) != 1 or 'uri' in buffers[0]:
            raise ValueError('Only self-contained GLBs with one embedded buffer are supported')
        if buffers[0]['byteLength'] > len(bins[0]):
            raise ValueError('GLB binary buffer is truncated')
        self.binary = bins[0]
        compressed = {'KHR_draco_mesh_compression', 'EXT_meshopt_compression'}
        if compressed.intersection(self.document.get('extensionsUsed', [])):
            raise ValueError('Decompress Draco/meshopt geometry before baking')

    @classmethod
    def read(cls, path):
        return cls(Path(path).read_bytes())

    def _view_values(self, view_index, offset, count, component, width):
        view = self.document['bufferViews'][view_index]
        if view.get('buffer', 0) != 0:
            raise ValueError('External buffers are unsupported')
        fmt, size = COMPONENTS[component]
        stride = view.get('byteStride', size * width)
        if stride < size * width:
            raise ValueError('Accessor stride is smaller than its element')
        start = view.get('byteOffset', 0) + offset
        end = start + (count - 1) * stride + size * width if count else start
        if offset < 0 or end > view.get('byteOffset', 0) + view['byteLength'] or end > len(self.binary):
            raise ValueError('Accessor reads outside its buffer view')
        return [struct.unpack_from('<' + fmt * width, self.binary, start + i * stride)
                for i in range(count)]

    def accessor(self, index):
        a = self.document['accessors'][index]
        component = a['componentType']
        width = WIDTHS[a['type']]
        if 'bufferView' in a:
            values = self._view_values(a['bufferView'], a.get('byteOffset', 0), a['count'], component, width)
        else:
            values = [(0,) * width for _ in range(a['count'])]
        sparse = a.get('sparse')
        if sparse:
            indices = self._view_values(sparse['indices']['bufferView'], sparse['indices'].get('byteOffset', 0),
                                        sparse['count'], sparse['indices']['componentType'], 1)
            patches = self._view_values(sparse['values']['bufferView'], sparse['values'].get('byteOffset', 0),
                                        sparse['count'], component, width)
            for (i,), value in zip(indices, patches):
                if i >= len(values):
                    raise ValueError('Sparse accessor index is out of range')
                values[i] = value
        if a.get('normalized'):
            if component == 5126:
                raise ValueError('Normalized FLOAT accessor is invalid')
            maximum = {5120: 127, 5121: 255, 5122: 32767, 5123: 65535, 5125: 4294967295}[component]
            values = [tuple(max(-1.0, v / maximum) for v in row) for row in values]
        return values

    def primitives(self):
        """Stable IDs are per primitive vertex, independent of Blender welding."""
        base = 0
        for mi, mesh in enumerate(self.document.get('meshes', [])):
            for pi, primitive in enumerate(mesh['primitives']):
                if primitive.get('mode', 4) != 4:
                    raise ValueError('AO currently supports triangle mesh primitives only')
                count = self.document['accessors'][primitive['attributes']['POSITION']]['count']
                yield mi, pi, base, count
                base += count

    @staticmethod
    def append_accessor(document, binary, values, component, kind, normalized=False):
        while len(binary) % 4:
            binary.append(0)
        start = len(binary)
        fmt = '<' + COMPONENTS[component][0] * WIDTHS[kind]
        for row in values:
            binary.extend(struct.pack(fmt, *row))
        views = document.setdefault('bufferViews', [])
        views.append({'buffer': 0, 'byteOffset': start, 'byteLength': len(binary) - start})
        accessor = {'bufferView': len(views) - 1, 'componentType': component,
                    'count': len(values), 'type': kind}
        if normalized:
            accessor['normalized'] = True
        accessors = document.setdefault('accessors', [])
        accessors.append(accessor)
        return len(accessors) - 1

    def encode(self, document, binary):
        document['buffers'][0]['byteLength'] = len(binary)
        encoded = json.dumps(document, ensure_ascii=False, separators=(',', ':')).encode('utf8')
        encoded += b' ' * (-len(encoded) % 4)
        binary = bytes(binary) + b'\0' * (-len(binary) % 4)
        chunks = [(JSON_CHUNK, encoded)]
        for kind, data in self.chunks[1:]:
            chunks.append((kind, binary if kind == BIN_CHUNK else data))
        body = b''.join(struct.pack('<II', len(data), kind) + data for kind, data in chunks)
        return struct.pack('<4sII', b'glTF', 2, 12 + len(body)) + body

    def instrumented(self, recoil_z_up=True):
        document = copy.deepcopy(self.document)
        binary = bytearray(self.binary)
        for mi, pi, base, count in self.primitives():
            if base + count > 2 ** 24:
                raise ValueError('Source exceeds the exact Blender import ID limit (16 million vertices)')
            primitive = document['meshes'][mi]['primitives'][pi]
            if ID_ATTRIBUTE in primitive['attributes']:
                raise ValueError('Source already contains the reserved AO vertex ID attribute')
            primitive['attributes'][ID_ATTRIBUTE] = self.append_accessor(
                document, binary, [(base + i,) for i in range(count)], 5126, 'SCALAR')
        if recoil_z_up:
            # Blender imports Y-up -> Z-up. This temporary ancestor cancels
            # that rotation in world space, including animation and skinning.
            for scene in document.get('scenes', []):
                root = {'name': 'Recoil Z-up import adapter', 'children': scene.get('nodes', []),
                        'rotation': [-math.sqrt(0.5), 0, 0, math.sqrt(0.5)]}
                scene['nodes'] = [len(document['nodes'])]
                document['nodes'].append(root)
        return self.encode(document, binary)

    def patched(self, alpha_by_id):
        if not alpha_by_id:
            raise ValueError('No AO vertex values to save')
        document = copy.deepcopy(self.document)
        binary = bytearray(self.binary)
        total = 0
        for mi, pi, base, count in self.primitives():
            total += count
            primitive = self.document['meshes'][mi]['primitives'][pi]
            updates = [i for i in range(count) if base + i in alpha_by_id]
            if not updates:
                continue
            attributes = primitive['attributes']
            if 'COLOR_0' in attributes:
                old = self.document['accessors'][attributes['COLOR_0']]
                if old['type'] not in ('VEC3', 'VEC4') or old['count'] != count:
                    raise ValueError('COLOR_0 must match the POSITION vertex count')
                colors = self.accessor(attributes['COLOR_0'])
                component = old['componentType']
                if component not in (5121, 5123, 5126) or (component != 5126 and not old.get('normalized')):
                    raise ValueError('Unsupported COLOR_0 representation')
            else:
                colors = [(1.0, 1.0, 1.0, 1.0)] * count
                component = 5123
            rgba = [list(c) if len(c) == 4 else [*c, 1.0] for c in colors]
            for i in updates:
                value = float(alpha_by_id[base + i])
                if not math.isfinite(value):
                    raise ValueError('AO values must be finite')
                rgba[i][3] = max(0.0, min(1.0, value))
            # Retain the original component format so RGB round-trips exactly.
            if component != 5126:
                maximum = 255 if component == 5121 else 65535
                rgba = [tuple(round(max(0, min(1, v)) * maximum) for v in c) for c in rgba]
            idx = self.append_accessor(document, binary, rgba, component, 'VEC4', component != 5126)
            document['meshes'][mi]['primitives'][pi]['attributes']['COLOR_0'] = idx
        if any(not isinstance(i, int) or i < 0 or i >= total for i in alpha_by_id):
            raise ValueError('AO contains unknown source vertex IDs')
        return self.encode(document, binary)

    def save_alpha(self, path, alpha_by_id):
        """Validate everything before backup/atomic replace; reject stale sources."""
        path = Path(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != self.digest:
            raise ValueError('Source GLB changed since import; reload it before saving AO')
        output = self.patched(alpha_by_id)
        GLB(output)  # Validate the container before touching the destination.
        backup = path.with_name(path.name + '.ao-backup')
        if not backup.exists():
            shutil.copy2(path, backup)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.stem + '_ao_', suffix='.tmp', delete=False) as f:
                temporary = f.name
                f.write(output)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)
        return GLB(output).digest
