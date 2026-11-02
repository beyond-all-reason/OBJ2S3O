#!/usr/bin/env python3
"""Write S3O metadata and the piece tree as JSON."""

import argparse
import json
import struct
from pathlib import Path

import s3o


RECOIL_INFO = (
    "RecoilEngine/Spring S3O uses a left-handed coordinate system: "
    "+X is right (-X left), +Y is up (-Y down), and +Z is forward (-Z backward). "
    "Piece offsets are translations relative to each piece's parent. "
    "Local-piece rotations use the RecoilEngine CQuaternion convention in radians "
    "with Y-X-Z Euler order (yaw, pitch, roll): positive X rotates up toward forward, "
    "positive Y rotates forward toward right, positive Z rotates right toward up; "
    "negative X rotates up toward backward, negative Y forward toward left, and "
    "negative Z right toward down. "
    "Piece dimensions are the local axis-aligned extents of that piece's own vertices, "
    "excluding children; X is width, Y height, and Z length; pieces with no vertices "
    "omit dimensions because their dimensions are trivially zero. "
    "Sources: RecoilEngine/rts/System/float3.h, "
    "RecoilEngine/rts/System/Matrix44f.cpp, and "
    "RecoilEngine/rts/Rendering/Models/3DModelPiece.cpp."
)


def decode_name(value):
    """Convert an S3O byte-string field into readable terminal text."""
    if isinstance(value, bytes):
        value = value.split(b"\x00", 1)[0]
        return value.decode("utf-8", errors="replace")
    return str(value)


def piece_to_dict(piece):
    name = decode_name(piece.name) or "<unnamed>"
    if piece.vertices:
        mins = list(piece.vertices[0][0])
        maxs = list(piece.vertices[0][0])
        for vertex in piece.vertices[1:]:
            for axis in range(3):
                mins[axis] = min(mins[axis], vertex[0][axis])
                maxs[axis] = max(maxs[axis], vertex[0][axis])
        dimensions = [maxs[axis] - mins[axis] for axis in range(3)]
    else:
        dimensions = [0.0, 0.0, 0.0]

    result = {
        "name": name,
        "vertices": len(piece.vertices),
        "offset": list(piece.parent_offset),
    }
    if piece.vertices:
        result["dimensions"] = dimensions
    if piece.children:
        result["children"] = [piece_to_dict(child) for child in piece.children]
    return result


def model_to_dict(filename, model):
    return {
        "info": RECOIL_INFO,
        "model": str(filename),
        "radius": model.collision_radius,
        "midpoint": list(model.midpoint),
        "height": model.height,
        "textures": [decode_name(texture) or None for texture in model.texture_paths],
        "pieces": [piece_to_dict(model.root_piece)],
    }


def compact_vector_fields(json_text):
    lines = json_text.splitlines()
    compacted = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped in ('"offset": [', '"dimensions": ['):
            indent = line[: len(line) - len(line.lstrip())]
            values = [
                "{:.2f}".format(float(lines[index + offset].strip().rstrip(",")))
                for offset in range(1, 4)
            ]
            closing = lines[index + 4].strip()
            comma = "," if closing.endswith(",") else ""
            compacted.append(
                "{}{}[{}]{}".format(indent, stripped[:-1], ", ".join(values), comma)
            )
            index += 5
        else:
            compacted.append(line)
            index += 1
    return "\n".join(compacted)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Write S3O metadata, texture names, and the piece hierarchy as JSON."
    )
    parser.add_argument("input", type=Path, help="S3O file to inspect")
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        model = s3o.S3O(args.input.read_bytes())
    except OSError as error:
        raise SystemExit("error: cannot read '{}': {}".format(args.input, error))
    except AssertionError:
        raise SystemExit(
            "error: '{}' is not a valid S3O file (bad magic, version, or header)".format(
                args.input
            )
        )
    except (IndexError, KeyError, ValueError, struct.error) as error:
        raise SystemExit("error: '{}' is not a valid S3O file: {}".format(args.input, error))

    document = json.dumps(model_to_dict(args.input, model), indent=2, allow_nan=False)
    print(compact_vector_fields(document))


if __name__ == "__main__":
    main()
