"""Preservation checks runnable without Blender: python -m unittest discover -s OBJ2S3O/tests."""
import copy
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'blender_glb_ao' / 'glb.py'
SPEC = importlib.util.spec_from_file_location('bar_ao_glb', MODULE)
MODULE_OBJECT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE_OBJECT)
GLB = MODULE_OBJECT.GLB


def fixture(color_type='VEC4', component=5123, sparse=False):
    # Interleaved color and unrelated bytes; original data must never change.
    binary = bytearray(struct.pack('<9f', 0, 0, 0, 1, 0, 0, 0, 1, 0))
    document = {
        'asset': {'version': '2.0'}, 'buffers': [{'byteLength': 36}],
        'bufferViews': [{'buffer': 0, 'byteLength': 36}],
        'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 3, 'type': 'VEC3'}],
        'meshes': [{'primitives': [{'attributes': {'POSITION': 0}}]}],
        'nodes': [{'mesh': 0}], 'scenes': [{'nodes': [0], 'extras': {'s3ocompat': True}}],
        'animations': [{'name': 'Preserve arbitrary animation metadata'}],
        'extensions': {'VENDOR_test': {'value': 42}},
    }
    if color_type:
        count = 4 if color_type == 'VEC4' else 3
        maximum = {5121: 255, 5123: 65535, 5126: 1}[component]
        rows = [tuple(([0, maximum, maximum // 2, maximum] if component != 5126 else [0.125, 0.75, 0.5, 1])[:count])] * 3
        index = GLB.append_accessor(document, binary, rows, component, color_type, component != 5126)
        document['meshes'][0]['primitives'][0]['attributes']['COLOR_0'] = index
        if sparse:
            a = document['accessors'][index]
            view = document['bufferViews'][a['bufferView']]
            patch_offset = len(binary)
            binary.extend(b'\x01\0\0\0')
            document['bufferViews'].append({'buffer': 0, 'byteOffset': patch_offset, 'byteLength': 1})
            a['sparse'] = {'count': 1, 'indices': {'bufferView': len(document['bufferViews']) - 1, 'componentType': 5121},
                           'values': {'bufferView': a['bufferView'], 'byteOffset': 0}}
    # Construct with a harmless unknown chunk to check chunk preservation.
    encoded = json.dumps(document).encode()
    encoded += b' ' * (-len(encoded) % 4)
    binary += b'\0' * (-len(binary) % 4)
    document['buffers'][0]['byteLength'] = len(binary)
    chunks = [(0x4E4F534A, encoded), (0x004E4942, bytes(binary)), (0x54534554, b'keep')]
    body = b''.join(struct.pack('<II', len(data), kind) + data for kind, data in chunks)
    return struct.pack('<4sII', b'glTF', 2, 12 + len(body)) + body


class ColorWriterTests(unittest.TestCase):
    def test_rgb_and_everything_else_preserved(self):
        for component in (5121, 5123, 5126):
            for kind in ('VEC3', 'VEC4'):
                with self.subTest(component=component, kind=kind):
                    original = GLB(fixture(kind, component))
                    before = original.accessor(1)
                    output = GLB(original.patched({0: 0.25, 2: 0.75}))
                    colors = output.accessor(output.document['meshes'][0]['primitives'][0]['attributes']['COLOR_0'])
                    self.assertEqual([c[:3] for c in colors], [c[:3] for c in before])
                    self.assertAlmostEqual(colors[0][3], 0.25, delta=1 / 255)
                    self.assertEqual(colors[1][3], 1)
                    self.assertTrue(output.binary.startswith(original.binary))
                    self.assertEqual(original.chunks[-1], output.chunks[-1])
                    for key in ('nodes', 'scenes', 'animations', 'extensions'):
                        self.assertEqual(original.document[key], output.document[key])

    def test_no_color_and_sparse_colors(self):
        for kind, sparse in ((None, False), ('VEC4', True)):
            original = GLB(fixture(kind, sparse=sparse))
            output = GLB(original.patched({1: 0.0}))
            a = output.document['meshes'][0]['primitives'][0]['attributes']['COLOR_0']
            self.assertEqual(output.accessor(a)[1][3], 0)
            if kind is None:
                self.assertEqual(output.accessor(a)[1][:3], (1, 1, 1))

    def test_interleaved_accessor_and_bounds(self):
        original = GLB(fixture('VEC4', 5121))
        a = original.document['accessors'][1]
        view = original.document['bufferViews'][a['bufferView']]
        values = original.accessor(1)
        blob = b''.join(bytes(round(v * 255) for v in row) + b'JUNK' for row in values)
        original.binary = original.binary[:view['byteOffset']] + blob
        view['byteStride'] = 8
        view['byteLength'] = 24
        self.assertEqual(original.accessor(1), values)
        view['byteLength'] = 19
        with self.assertRaises(ValueError):
            original.accessor(1)

    def test_ids_and_axis_adapter_only_in_temporary_copy(self):
        original = GLB(fixture())
        output = GLB(original.instrumented())
        a = output.document['meshes'][0]['primitives'][0]['attributes']['_BAR_AO_ID']
        self.assertEqual(output.accessor(a), [(0,), (1,), (2,)])
        self.assertEqual(len(original.document['nodes']), 1)
        self.assertEqual(len(output.document['nodes']), 2)
        self.assertNotIn('_BAR_AO_ID', original.document['meshes'][0]['primitives'][0]['attributes'])

    def test_atomic_save_backup_and_stale_input(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'model.glb'
            raw = fixture()
            path.write_bytes(raw)
            source = GLB.read(path)
            source.save_alpha(path, {0: 0.2})
            self.assertEqual(Path(str(path) + '.ao-backup').read_bytes(), raw)
            with self.assertRaises(ValueError):
                source.save_alpha(path, {0: 0.3})
            GLB.read(path).save_alpha(path, {0: 0.3})
            self.assertEqual(Path(str(path) + '.ao-backup').read_bytes(), raw)

    def test_invalid_ao_rejected(self):
        original = GLB(fixture())
        for values in ({}, {0: float('nan')}, {99: 0.2}):
            with self.assertRaises(ValueError):
                original.patched(values)

    def test_real_sample_rgb_preserved(self):
        path = MODULE.parents[1] / 'armanac.glb'
        if not path.exists():
            self.skipTest('Local reference GLB unavailable')
        original = GLB.read(path)
        values = {base + i: (i % 11) / 10 for _, _, base, count in original.primitives() for i in range(count)}
        output = GLB(original.patched(values))
        for mi, pi, base, count in original.primitives():
            old = original.document['meshes'][mi]['primitives'][pi]['attributes']['COLOR_0']
            new = output.document['meshes'][mi]['primitives'][pi]['attributes']['COLOR_0']
            self.assertEqual([c[:3] for c in original.accessor(old)], [c[:3] for c in output.accessor(new)])


if __name__ == '__main__':
    unittest.main()
