# SPDX-License-Identifier: GPL-2.0-or-later
"""Small, Blender-independent GLB reader and color-only writer.

Original unknown chunks are retained. Bake updates reuse compatible color
storage or replace a dedicated color stream, while shared/interleaved/sparse
accessors are left intact when rewriting them would be unsafe.
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
    def _encode_values(values, component, kind):
        fmt = '<' + COMPONENTS[component][0] * WIDTHS[kind]
        return b''.join(struct.pack(fmt, *row) for row in values)

    @staticmethod
    def append_accessor(document, binary, values, component, kind, normalized=False):
        while len(binary) % 4:
            binary.append(0)
        start = len(binary)
        binary.extend(GLB._encode_values(values, component, kind))
        views = document.setdefault('bufferViews', [])
        views.append({'buffer': 0, 'byteOffset': start, 'byteLength': len(binary) - start})
        accessor = {'bufferView': len(views) - 1, 'componentType': component,
                    'count': len(values), 'type': kind}
        if normalized:
            accessor['normalized'] = True
        accessors = document.setdefault('accessors', [])
        accessors.append(accessor)
        return len(accessors) - 1

    @staticmethod
    def _accessor_references(document):
        """Count standard glTF references to each accessor."""
        references = {}

        def add(index):
            if isinstance(index, int):
                references[index] = references.get(index, 0) + 1

        for mesh in document.get('meshes', []):
            for primitive in mesh.get('primitives', []):
                for index in primitive.get('attributes', {}).values():
                    add(index)
                add(primitive.get('indices'))
                for target in primitive.get('targets', []):
                    for index in target.values():
                        add(index)
        for animation in document.get('animations', []):
            for sampler in animation.get('samplers', []):
                add(sampler.get('input'))
                add(sampler.get('output'))
        for skin in document.get('skins', []):
            add(skin.get('inverseBindMatrices'))
        return references

    @staticmethod
    def _buffer_view_references(document):
        """Count standard glTF references to each buffer view."""
        references = {}

        def add(index):
            if isinstance(index, int):
                references[index] = references.get(index, 0) + 1

        for accessor in document.get('accessors', []):
            add(accessor.get('bufferView'))
            sparse = accessor.get('sparse')
            if sparse:
                add(sparse.get('indices', {}).get('bufferView'))
                add(sparse.get('values', {}).get('bufferView'))
        for image in document.get('images', []):
            add(image.get('bufferView'))
        return references

    @staticmethod
    def _write_existing_accessor(document, binary, accessor_index, payload, count,
                                  component, kind, normalized, accessor_references):
        """Rewrite a uniquely referenced, non-sparse accessor in place."""
        accessor = document['accessors'][accessor_index]
        if accessor_references.get(accessor_index) != 1:
            return False
        if (accessor.get('type') != kind or accessor.get('componentType') != component or
                bool(accessor.get('normalized')) != normalized or accessor.get('count') != count or
                'sparse' in accessor):
            return False
        view_index = accessor.get('bufferView')
        if not isinstance(view_index, int) or view_index >= len(document.get('bufferViews', [])):
            return False
        view = document['bufferViews'][view_index]
        if view.get('buffer', 0) != 0:
            return False
        element_size = COMPONENTS[component][1] * WIDTHS[kind]
        stride = view.get('byteStride', element_size)
        if stride < element_size:
            return False
        view_start = view.get('byteOffset', 0)
        accessor_offset = accessor.get('byteOffset', 0)
        start = view_start + accessor_offset
        end = start + (count - 1) * stride + element_size if count else start
        if (view_start < 0 or accessor_offset < 0 or start < view_start or
                end > view_start + view.get('byteLength', 0) or end > len(binary)):
            return False
        if len(payload) != count * element_size:
            return False
        for index in range(count):
            begin = start + index * stride
            binary[begin:begin + element_size] = payload[index * element_size:(index + 1) * element_size]
        accessor.pop('min', None)
        accessor.pop('max', None)
        return True

    @staticmethod
    def _replace_dedicated_accessor(document, binary, accessor_index, payload, count,
                                    component, kind, normalized, accessor_references,
                                    buffer_view_references):
        """Replace a uniquely referenced standalone color stream and remove its old bytes."""
        accessor = document['accessors'][accessor_index]
        if accessor_references.get(accessor_index) != 1 or 'sparse' in accessor:
            return False
        view_index = accessor.get('bufferView')
        if not isinstance(view_index, int) or view_index >= len(document.get('bufferViews', [])):
            return False
        if buffer_view_references.get(view_index) != 1:
            return False
        view = document['bufferViews'][view_index]
        if view.get('buffer', 0) != 0 or view.get('byteStride') is not None:
            return False
        if accessor.get('byteOffset', 0) != 0:
            return False
        old_type = accessor.get('type')
        old_component = accessor.get('componentType')
        old_count = accessor.get('count')
        if old_type not in ('VEC3', 'VEC4') or old_component != component or old_count != count:
            return False
        old_size = COMPONENTS[component][1] * WIDTHS[old_type] * count
        old_start = view.get('byteOffset', 0)
        old_end = old_start + old_size
        if (view.get('byteLength') != old_size or old_start < 0 or
                old_end > len(binary) or old_start % 4):
            return False

        # Remove padding after the standalone view as well, keeping every later
        # bufferView 4-byte aligned after the binary data is compacted.
        remove_end = (old_end + 3) & ~3
        if remove_end > len(binary):
            return False
        for index, other in enumerate(document['bufferViews']):
            if index == view_index:
                continue
            start = other.get('byteOffset', 0)
            end = start + other.get('byteLength', 0)
            if start < remove_end and end > old_start:
                return False

        while len(binary) % 4:
            binary.append(0)
        appended_start = len(binary)
        binary.extend(payload)
        del binary[old_start:remove_end]
        removed = remove_end - old_start
        new_start = appended_start - removed
        for index, other in enumerate(document['bufferViews']):
            start = other.get('byteOffset', 0)
            if index == view_index:
                other['byteOffset'] = new_start
                other['byteLength'] = len(payload)
                other.pop('byteStride', None)
            elif start >= remove_end:
                other['byteOffset'] = start - removed

        accessor.pop('byteOffset', None)
        accessor['componentType'] = component
        accessor['count'] = count
        accessor['type'] = kind
        if normalized:
            accessor['normalized'] = True
        else:
            accessor.pop('normalized', None)
        # Bounds from VEC3 colors are not valid for the replacement VEC4 data.
        accessor.pop('min', None)
        accessor.pop('max', None)
        return True

    @staticmethod
    def _compact_orphaned_color_data(document, binary):
        """Drop standalone color streams orphaned by an earlier bake update."""
        accessors = document.get('accessors', [])
        references = GLB._accessor_references(document)

        def color_key(accessor):
            component = accessor.get('componentType')
            normalized = bool(accessor.get('normalized'))
            if (accessor.get('type') not in ('VEC3', 'VEC4') or
                    component not in (5121, 5123, 5126) or
                    (component != 5126 and not normalized) or
                    (component == 5126 and normalized)):
                return None
            return component, accessor.get('count'), normalized

        active_keys = set()
        for mesh in document.get('meshes', []):
            for primitive in mesh.get('primitives', []):
                index = primitive.get('attributes', {}).get('COLOR_0')
                if isinstance(index, int) and index < len(accessors):
                    key = color_key(accessors[index])
                    if key:
                        active_keys.add(key)
        if not active_keys:
            return

        dead = {index for index, accessor in enumerate(accessors)
                if references.get(index, 0) == 0 and color_key(accessor) in active_keys}
        if not dead:
            return

        def view_indices(accessor):
            result = []
            if isinstance(accessor.get('bufferView'), int):
                result.append(accessor['bufferView'])
            sparse = accessor.get('sparse')
            if sparse:
                for key in ('indices', 'values'):
                    index = sparse.get(key, {}).get('bufferView')
                    if isinstance(index, int):
                        result.append(index)
            return result

        # Views still used by live accessors or embedded images cannot be removed.
        live_view_references = {}

        def add_view_reference(index):
            if isinstance(index, int):
                live_view_references[index] = live_view_references.get(index, 0) + 1

        for index, accessor in enumerate(accessors):
            if index in dead:
                continue
            for view_index in view_indices(accessor):
                add_view_reference(view_index)
        for image in document.get('images', []):
            add_view_reference(image.get('bufferView'))

        candidate_views = set()
        for index in dead:
            for view_index in view_indices(accessors[index]):
                if live_view_references.get(view_index, 0) == 0:
                    candidate_views.add(view_index)

        views = document.get('bufferViews', [])
        removable_views = set()
        removals = []
        for view_index in sorted(candidate_views):
            if view_index >= len(views):
                continue
            view = views[view_index]
            start = view.get('byteOffset', 0)
            end = start + view.get('byteLength', 0)
            remove_end = (end + 3) & ~3
            if (view.get('buffer', 0) != 0 or start < 0 or start % 4 or
                    end > len(binary) or remove_end > len(binary)):
                continue
            overlaps = False
            for other_index, other in enumerate(views):
                if other_index == view_index:
                    continue
                other_start = other.get('byteOffset', 0)
                other_end = other_start + other.get('byteLength', 0)
                if other_start < remove_end and other_end > start:
                    overlaps = True
                    break
            if overlaps:
                continue
            removable_views.add(view_index)
            removals.append((start, remove_end))

        if removals:
            for start, end in sorted(removals, reverse=True):
                del binary[start:end]

            def shifted(offset):
                return offset - sum(end - start for start, end in removals if end <= offset)

            for index, view in enumerate(views):
                if index not in removable_views:
                    view['byteOffset'] = shifted(view.get('byteOffset', 0))

        # These accessors have no standard glTF users. Their views can remain
        # when shared with live data, but the accessor records themselves are
        # safe to discard.
        removable_accessors = set(dead)
        accessor_map = {}
        kept_accessors = []
        for index, accessor in enumerate(accessors):
            if index in removable_accessors:
                continue
            accessor_map[index] = len(kept_accessors)
            kept_accessors.append(accessor)

        def remap_accessor(index):
            return accessor_map.get(index, index)

        for mesh in document.get('meshes', []):
            for primitive in mesh.get('primitives', []):
                primitive['attributes'] = {name: remap_accessor(index)
                                           for name, index in primitive.get('attributes', {}).items()}
                if isinstance(primitive.get('indices'), int):
                    primitive['indices'] = remap_accessor(primitive['indices'])
                for target in primitive.get('targets', []):
                    for name, index in list(target.items()):
                        target[name] = remap_accessor(index)
        for animation in document.get('animations', []):
            for sampler in animation.get('samplers', []):
                if isinstance(sampler.get('input'), int):
                    sampler['input'] = remap_accessor(sampler['input'])
                if isinstance(sampler.get('output'), int):
                    sampler['output'] = remap_accessor(sampler['output'])
        for skin in document.get('skins', []):
            if isinstance(skin.get('inverseBindMatrices'), int):
                skin['inverseBindMatrices'] = remap_accessor(skin['inverseBindMatrices'])
        document['accessors'] = kept_accessors

        view_map = {}
        kept_views = []
        for index, view in enumerate(views):
            if index in removable_views:
                continue
            view_map[index] = len(kept_views)
            kept_views.append(view)

        def remap_view(index):
            return view_map.get(index, index)

        for accessor in kept_accessors:
            if isinstance(accessor.get('bufferView'), int):
                accessor['bufferView'] = remap_view(accessor['bufferView'])
            sparse = accessor.get('sparse')
            if sparse:
                for key in ('indices', 'values'):
                    if isinstance(sparse.get(key, {}).get('bufferView'), int):
                        sparse[key]['bufferView'] = remap_view(sparse[key]['bufferView'])
        for image in document.get('images', []):
            if isinstance(image.get('bufferView'), int):
                image['bufferView'] = remap_view(image['bufferView'])
        document['bufferViews'] = kept_views

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
        accessor_references = self._accessor_references(document)
        buffer_view_references = self._buffer_view_references(document)
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
            normalized = component != 5126
            payload = self._encode_values(rgba, component, 'VEC4')
            old_index = attributes.get('COLOR_0')
            if (old_index is not None and
                    self._write_existing_accessor(document, binary, old_index, payload, count,
                                                   component, 'VEC4', normalized, accessor_references)):
                continue
            if (old_index is not None and
                    self._replace_dedicated_accessor(document, binary, old_index, payload, count,
                                                      component, 'VEC4', normalized,
                                                      accessor_references, buffer_view_references)):
                continue
            idx = self.append_accessor(document, binary, rgba, component, 'VEC4', normalized)
            document['meshes'][mi]['primitives'][pi]['attributes']['COLOR_0'] = idx
        if any(not isinstance(i, int) or i < 0 or i >= total for i in alpha_by_id):
            raise ValueError('AO contains unknown source vertex IDs')
        self._compact_orphaned_color_data(document, binary)
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
