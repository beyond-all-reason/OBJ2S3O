"""Contact geometry/encoding invariants, runnable without Blender."""
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np

SPEC = importlib.util.spec_from_file_location('bar_tracks', Path(__file__).resolve().parents[1] / 'blender_glb_ao/tracks.py')
tracks = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(tracks)


def patch(x0, x1, y0, y1, z):
    a, b, c, d = (x0, y0, z), (x1, y0, z), (x1, y1, z), (x0, y1, z)
    return [[a, b, c], [a, c, d]]


class TrackTests(unittest.TestCase):
    def test_all_low_geometry_included_and_high_geometry_rejected(self):
        triangles = patch(-4, -2, -3, 5, 0.2) + patch(2, 4, -3, 5, 0.8)
        triangles += patch(-8, 8, -10, 10, 1.1)
        projection = tracks.project_contact(triangles, resolution=64, supersampling=1, padding=0)
        self.assertEqual(projection['width'], 8)
        self.assertEqual(projection['length'], 8)
        self.assertEqual(projection['offset'], 1)
        np.testing.assert_array_equal(projection['coverage'][16:48], 0)
        np.testing.assert_array_equal(projection['coverage'][:16], 1)
        np.testing.assert_array_equal(projection['coverage'][48:], 1)

    def test_strict_threshold_and_ground_translation(self):
        for z in (1, 1.001):
            with self.assertRaisesRegex(ValueError, 'No projected contact'):
                tracks.project_contact(patch(-1, 1, -1, 1, z))
        below = tracks.project_contact(patch(-1, 1, -1, 1, -2), padding=0)
        self.assertEqual(below['sample_height_range'], [-2, -2])
        raised = tracks.project_contact(patch(-1, 1, -1, 1, 10.9), ground=10, padding=0)
        self.assertAlmostEqual(raised['sample_height_range'][0], 10.9)

    def test_clip_sloped_triangle_before_measuring(self):
        # Only the half reaching y=1 is below z=1; do not fit the whole y=2 triangle.
        result = tracks.project_contact([[[-1, 0, 0], [1, 0, 0], [0, 2, 2]]], padding=0, resolution=32)
        self.assertAlmostEqual(result['length'], 1)
        self.assertLess(result['sample_height_range'][1], 1)

    def test_lowest_upward_hit_wins(self):
        result = tracks.project_contact(patch(-1, 1, -1, 1, 0.8) + patch(-1, 1, -1, 1, 0.2), padding=0)
        np.testing.assert_allclose(result['height'], 0.2)

    def test_offcenter_contact_retains_ten_percent_gutter_on_each_side(self):
        result = tracks.project_contact(patch(2, 4, -2, 3, 0))
        np.testing.assert_allclose(result['bounds']['right'], [1.8, 4.2])
        self.assertAlmostEqual(result['width'], 2.4)
        self.assertEqual(result['lateral_center'], 3)
        self.assertEqual(result['length'], 5)
        self.assertEqual(result['coverage'].shape, (256, 533))

    def test_asymmetric_orientation_columns_are_travel(self):
        result = tracks.project_contact(patch(-4, -2, -3, 5, 0) + patch(2, 4, 1, 5, 0), padding=0, resolution=64)
        self.assertEqual(result['coverage'][56, 4], 0)
        self.assertEqual(result['coverage'][56, 60], 1)
        self.assertEqual(result['coverage'][4, 4], 1)

    def test_periodic_join_determinism_and_gap(self):
        result = tracks.project_contact(patch(-4, -2, -3, 5, 0) + patch(2, 4, 1, 5, 0), resolution=128, resolution_x=128)
        encoded = tracks.encode_track(result)
        np.testing.assert_array_equal(encoded, tracks.encode_track(result))
        self.assertFalse(np.array_equal(encoded, tracks.encode_track(result, seed=1)))
        np.testing.assert_array_equal(encoded[:, 0], encoded[:, -1])
        np.testing.assert_array_equal(encoded[50:78, :, 1], 0)
        np.testing.assert_array_equal(encoded[:, :, 2], 0)
        # Mip texels sample different neighborhoods and need not be identical.
        # On this asymmetric fixture the seam must not introduce a sharper
        # transition than the actual contact geometry inside the repeat.
        for factor in (1, 2, 4, 8):
            mip = encoded.astype(float).reshape(128 // factor, factor, 128 // factor, factor, 3).mean(axis=(1, 3))
            self.assertLessEqual(np.max(abs(mip[:, 0, 1] - mip[:, -1, 1])),
                                 np.max(abs(np.diff(mip[:, :, 1], axis=1))))

    def test_loader_decode_and_bmp_padding_preserve_data_bytes(self):
        rgb = np.array([[[255, 0, 0], [128, 180, 0], [50, 255, 0]],
                        [[0, 17, 0], [255, 255, 0], [7, 200, 0]]], dtype=np.uint8)
        decoded = tracks.decode_track(rgb)
        np.testing.assert_array_equal(decoded[0, 0], [90, 60, 30, 0])
        np.testing.assert_array_equal(decoded[0, 1], [45, 30, 15, 180])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'track.bmp'
            tracks.write_bmp(path, rgb)
            raw = path.read_bytes()
            self.assertEqual(struct.unpack_from('<iiHH', raw, 18), (3, 2, 1, 24))
            self.assertEqual(raw[54:63], rgb[-1, :, ::-1].tobytes())
            self.assertEqual(raw[66:75], rgb[0, :, ::-1].tobytes())

    def test_bundle_records_physical_settings_and_runtime_pending(self):
        projection = tracks.project_contact(patch(-4, 4, -2, 2, 0), padding=0)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'sample_tracks.bmp'
            report, manifest = tracks.save_bundle(path, projection, tracks.encode_track(projection), {'settings': {'lifetime': 6}})
            self.assertEqual(report['tracks']['unitdef']['trackstretch'], 0.5)
            self.assertEqual(report['stages']['runtime']['status'], 'pending')
            self.assertEqual(json.loads(manifest.read_text())['outputs']['bmp']['path'], str(path.resolve()))
            self.assertEqual(len(report['outputs']), 6)
            for output in report['outputs'].values():
                self.assertTrue(Path(output['path']).is_file())

    def test_invalid_geometry_and_settings_fail(self):
        for triangles in ([[[0, 0, 0]]], [[[0, 0, 0], [1, 0, 0], [2, 0, 0]]],
                          [[[0, 0, 0], [1, 0, 0], [0, 1, float('nan')]]]):
            with self.assertRaises(ValueError):
                tracks.project_contact(triangles)
        for args in ({'contact_height': 0}, {'padding': -1}, {'resolution': 3}, {'ground': float('inf')}):
            with self.assertRaises(ValueError):
                tracks.project_contact(patch(-1, 1, -1, 1, 0), **args)


if __name__ == '__main__':
    unittest.main()
