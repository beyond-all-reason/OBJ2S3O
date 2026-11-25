# SPDX-License-Identifier: GPL-2.0-or-later
"""Deterministic contact projection and Recoil track encoding; no Blender API.

Triangles use (right, forward, up) physical coordinates. Image columns run
along forward/travel, rows across the strip. The strict contact ceiling is
ground + contact_height, including any geometry that penetrates the ground.
"""
import hashlib
import json
from pathlib import Path
import re
import struct
import zlib

import numpy as np


def clipped_contact(triangles, ceiling):
    triangles = np.asarray(triangles, dtype=np.float64)
    if triangles.ndim != 3 or triangles.shape[1:] != (3, 3):
        raise ValueError('Expected triangles with shape (N, 3, 3)')
    if not np.isfinite(triangles).all() or not np.isfinite(ceiling):
        raise ValueError('Contact geometry and ground height must be finite')
    result = []
    for triangle in triangles:
        polygon = []
        for a, b in zip(triangle, np.roll(triangle, -1, axis=0)):
            inside_a, inside_b = a[2] < ceiling, b[2] < ceiling
            if inside_a:
                polygon.append(a)
            if inside_a != inside_b:
                polygon.append(a + (b - a) * ((ceiling - a[2]) / (b[2] - a[2])))
        for i in range(1, len(polygon) - 1):
            tri = np.array([polygon[0], polygon[i], polygon[i + 1]])
            a, b = tri[1, :2] - tri[0, :2], tri[2, :2] - tri[0, :2]
            if abs(a[0] * b[1] - a[1] * b[0]) > 1e-12:
                result.append(tri)
    if not result:
        raise ValueError('No projected contact geometry below ground + contact height')
    return np.array(result)


def project_contact(triangles, ground=0.0, contact_height=1.0, padding=0.1,
                    resolution=256, supersampling=2, resolution_x=None):
    """Rasterize upward rays: retain the lowest eligible surface at each sample.

    Clip triangles at the contact ceiling before fitting bounds. Pad image Y
    (across the strip) by a fraction of its contact span on EACH side. Keep the
    entire longitudinal contact interval as one repeat, with square physical
    texels when resolution_x is automatic.
    """
    values = (ground, contact_height, padding)
    if not np.isfinite(values).all() or contact_height <= 0 or padding < 0:
        raise ValueError('Ground must be finite; contact height positive; padding nonnegative')
    if int(resolution) != resolution or not 8 <= resolution <= 1024:
        raise ValueError('Resolution must be an integer in 8..1024')
    if supersampling not in (1, 2, 4):
        raise ValueError('Supersampling must be 1, 2 or 4')
    ceiling = ground + contact_height
    triangles = clipped_contact(triangles, ceiling)
    low, high = triangles.min(axis=(0, 1)), triangles.max(axis=(0, 1))
    gutter = (high[0] - low[0]) * padding
    lateral_low, lateral_high = low[0] - gutter, high[0] + gutter
    width, length = lateral_high - lateral_low, high[1] - low[1]
    if width <= 1e-9 or length <= 1e-9:
        raise ValueError('Contact projection must have nonzero width and length')
    columns = max(8, round(resolution * length / width)) if resolution_x is None else resolution_x
    if int(columns) != columns or not 8 <= columns <= 4096:
        raise ValueError('Image X resolution must be an integer in 8..4096; choose an explicit width for extreme aspect ratios')
    rows = int(resolution)
    columns = int(columns)
    row_count, col_count = rows * supersampling, columns * supersampling
    lateral = lateral_low + (np.arange(row_count) + 0.5) * width / row_count
    forward = low[1] + (np.arange(col_count) + 0.5) * length / col_count
    heights = np.full((row_count, col_count), np.inf)
    for triangle in triangles:
        minimum, maximum = triangle[:, :2].min(axis=0), triangle[:, :2].max(axis=0)
        row0, row1 = np.searchsorted(lateral, minimum[0]), np.searchsorted(lateral, maximum[0], side='right')
        col0, col1 = np.searchsorted(forward, minimum[1]), np.searchsorted(forward, maximum[1], side='right')
        if row0 >= row1 or col0 >= col1:
            continue
        x, y = lateral[row0:row1, None], forward[None, col0:col1]
        a, b, c = triangle
        denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        w0 = ((b[1] - c[1]) * (x - c[0]) + (c[0] - b[0]) * (y - c[1])) / denominator
        w1 = ((c[1] - a[1]) * (x - c[0]) + (a[0] - c[0]) * (y - c[1])) / denominator
        w2 = 1 - w0 - w1
        z = w0 * a[2] + w1 * b[2] + w2 * c[2]
        valid = (w0 >= -1e-9) & (w1 >= -1e-9) & (w2 >= -1e-9) & (z < ceiling)
        region = heights[row0:row1, col0:col1]
        np.minimum(region, np.where(valid, z, np.inf), out=region)
    hits = np.isfinite(heights)
    if not hits.any():
        raise ValueError('No contact samples at this resolution; increase resolution')
    shape = (rows, supersampling, columns, supersampling)
    coverage = hits.reshape(shape).mean(axis=(1, 3))
    sums = np.where(hits, heights, 0).reshape(shape).sum(axis=(1, 3))
    counts = hits.reshape(shape).sum(axis=(1, 3))
    mean_height = np.divide(sums, counts, out=np.full_like(sums, ceiling), where=counts > 0)
    return {'coverage': coverage, 'height': mean_height,
            'bounds': {'contact_min': low.tolist(), 'contact_max': high.tolist(),
                       'right': [float(lateral_low), float(lateral_high)], 'forward': [float(low[1]), float(high[1])]},
            'width': float(width), 'length': float(length),
            'lateral_center': float((low[0] + high[0]) / 2),
            'gutter_each_side': float(gutter),
            'offset': float((low[1] + high[1]) / 2),
            'sample_height_range': [float(heights[hits].min()), float(heights[hits].max())]}


def periodic_edges(field, fraction):
    """Join the longitudinal collars at a shared value with a smoothstep blend.

    The raw projection stays separate. This deliberately adjusts the two ends
    of the repeat; it does not apply an AO plate's longitudinal fade to zero.
    """
    if not np.isfinite(fraction) or not 0 < fraction <= 0.25:
        raise ValueError('Seam blend must be in (0, 0.25]')
    result = np.array(field, dtype=np.float64, copy=True)
    count = max(2, min(result.shape[1] // 2, round(result.shape[1] * fraction)))
    shared = (result[:, :1] + result[:, -1:]) / 2
    t = np.linspace(0, 1, count)[None, :]
    blend = t * t * (3 - 2 * t)
    result[:, :count] = shared * (1 - blend) + result[:, :count] * blend
    result[:, -count:] = shared * (1 - blend[:, ::-1]) + result[:, -count:] * blend[:, ::-1]
    return result


def encode_track(projection, brightness=0.75, opacity=0.75, breakup=0.15, seed=0,
                 seam_blend=0.08):
    if not np.isfinite((brightness, opacity, breakup)).all() or not all(
            0 <= value <= 1 for value in (brightness, opacity, breakup)):
        raise ValueError('Brightness, opacity and breakup must be in 0..1')
    coverage = projection['coverage']
    noise = np.random.default_rng(seed).random(coverage.shape)
    for _ in range(3):
        noise = (noise * 4 + np.roll(noise, 1, axis=0) + np.roll(noise, -1, axis=0)
                 + np.roll(noise, 1, axis=1) + np.roll(noise, -1, axis=1)) / 8
    red = periodic_edges(brightness * (1 - breakup * noise * 0.3), seam_blend)
    green = periodic_edges(coverage * opacity * (1 - breakup * noise), seam_blend)
    encoded = np.zeros((*coverage.shape, 3), dtype=np.uint8)
    encoded[:, :, 0] = np.rint(np.clip(red, 0, 1) * 255).astype(np.uint8)
    encoded[:, :, 1] = np.rint(np.clip(green, 0, 1) * 255).astype(np.uint8)
    return encoded


def decode_track(encoded):
    data = np.asarray(encoded, dtype=np.uint16)
    result = np.empty((*data.shape[:2], 4), dtype=np.uint8)
    for channel, factor in enumerate((90, 60, 30)):
        result[:, :, channel] = data[:, :, 0] * factor // 255
    result[:, :, 3] = data[:, :, 1]
    return result


def write_bmp(path, rgb):
    """Uncompressed 24-bit BMP with exact data bytes; bypass color management."""
    rgb = np.asarray(rgb, dtype=np.uint8)
    height, width, channels = rgb.shape
    if channels != 3:
        raise ValueError('Track BMP must have exactly three channels')
    stride = (width * 3 + 3) & ~3
    rows = b''.join(row[:, ::-1].tobytes() + b'\0' * (stride - width * 3) for row in rgb[::-1])
    header = struct.pack('<2sIHHI', b'BM', 54 + len(rows), 0, 0, 54)
    info = struct.pack('<IiiHHIIiiII', 40, width, height, 1, 24, 0, len(rows), 2835, 2835, 0, 0)
    Path(path).write_bytes(header + info + rows)


def write_png(path, pixels):
    """Write preview bytes directly, with no display transform or image dependency."""
    pixels = np.asarray(pixels, dtype=np.uint8)
    height, width, channels = pixels.shape
    if channels not in (3, 4):
        raise ValueError('PNG preview needs RGB or RGBA')
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    scanlines = b''.join(b'\0' + row.tobytes() for row in pixels)
    Path(path).write_bytes(b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6 if channels == 4 else 2, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(scanlines)) + chunk(b'IEND', b''))


def save_bundle(filepath, projection, encoded, metadata):
    path = Path(filepath).resolve()
    if path.suffix.lower() != '.bmp' or not re.fullmatch(r'[a-z0-9_]+', path.stem):
        raise ValueError('Choose a .bmp filename with a lowercase letter/digit/underscore basename')
    path.parent.mkdir(parents=True, exist_ok=True)
    decoded = decode_track(encoded)
    outputs = {'bmp': path, 'decoded': path.with_name(path.stem + '_decoded.png'),
               'tiled': path.with_name(path.stem + '_tiled.png'),
               'tiled_preview': path.with_name(path.stem + '_tiled_preview.png'),
               'contact': path.with_name(path.stem + '_contact.png'),
               'unitdef': path.with_suffix('.unitdef.lua')}
    write_bmp(outputs['bmp'], encoded)
    write_png(outputs['decoded'], decoded)
    write_png(outputs['tiled'], np.tile(decoded, (1, 3, 1)))
    tiled = np.tile(decoded, (1, 3, 1)).astype(float)
    alpha = tiled[:, :, 3:4] / 255
    matte = tiled[:, :, :3] * alpha + np.array([177, 161, 132]) * (1 - alpha)
    write_png(outputs['tiled_preview'], np.rint(matte).astype(np.uint8))
    mask = np.empty((*projection['coverage'].shape, 4), dtype=np.uint8)
    mask[:, :, :3] = 255
    mask[:, :, 3] = np.rint(projection['coverage'] * 255).astype(np.uint8)
    write_png(outputs['contact'], mask)
    fragment = {'leavetracks': True, 'tracktype': path.stem, 'trackwidth': projection['width'],
                'trackoffset': projection['offset'], 'trackstretch': projection['length'] / projection['width'],
                'trackstrength': metadata['settings']['lifetime']}
    lua = ['-- Proposed UnitDef values; copy the BMP into bitmaps/tracks/ first.',
           '-- trackstrength controls lifetime. Runtime orientation/offset still need validation.', 'return {']
    for key, value in fragment.items():
        literal = json.dumps(value) if isinstance(value, (str, bool)) else f'{value:.9g}'
        lua.append(f'    {key} = {literal},')
    outputs['unitdef'].write_text('\n'.join([*lua, '}', '']), encoding='utf-8')
    report = {'schema': 'bar.ground-decals', 'version': 1, 'mode': 'tracks', **metadata,
              'projection': {key: projection[key] for key in ('bounds', 'sample_height_range')},
              'tracks': {'width': projection['width'], 'repeat_length': projection['length'],
                         'offset': projection['offset'], 'unitdef': fragment},
              'outputs': {key: {'path': str(file), 'sha256': hashlib.sha256(file.read_bytes()).hexdigest()}
                          for key, file in outputs.items()},
              'stages': {'projection': {'status': 'passed', 'scope': 'mathematical contact projection'},
                         'encoding': {'status': 'passed', 'scope': 'generated RGB data'},
                         'blender_preview': {'status': 'pending'},
                         'runtime': {'status': 'pending', 'reason': 'Blender-only authoring; no game trial'}}}
    report['projection'].update(image_size=[encoded.shape[1], encoded.shape[0]],
        gutter_each_side=projection['gutter_each_side'], lateral_center=projection['lateral_center'])
    if abs(projection['lateral_center']) > 0.001:
        report['tracks']['integration_note'] = ('The contact projection is laterally off-center. UnitDefs have no lateral track offset; '
            'recenter/extend the image for game integration and recompute width/stretch. The artist output retains the requested padding.')
    manifest = path.with_suffix('.json')
    manifest.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return report, manifest
