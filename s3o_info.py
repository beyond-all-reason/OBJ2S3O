#!/usr/bin/env python3
"""Print useful metadata and the piece tree from a Spring S3O model."""

import argparse
import struct
from pathlib import Path

import s3o


def decode_name(value):
    """Convert an S3O byte-string field into readable terminal text."""
    if isinstance(value, bytes):
        value = value.split(b"\x00", 1)[0]
        return value.decode("utf-8", errors="replace")
    return str(value)


def format_vector(vector):
    return "({:.6g}, {:.6g}, {:.6g})".format(*vector)


def print_piece_tree(piece, depth=0):
    indent = "  " * depth
    name = decode_name(piece.name) or "<unnamed>"
    print(
        "{}{} vertices={} offset={}".format(
            indent, name, len(piece.vertices), format_vector(piece.parent_offset)
        )
    )
    for child in piece.children:
        print_piece_tree(child, depth + 1)


def print_model_info(filename, model):
    print("Model: {}".format(filename))
    print("Radius: {:.6g}".format(model.collision_radius))
    print("Midpoint: {}".format(format_vector(model.midpoint)))
    print("Height: {:.6g}".format(model.height))
    print("Textures:")
    for index, texture in enumerate(model.texture_paths, start=1):
        texture_name = decode_name(texture) or "<none>"
        print("  {}: {}".format(index, texture_name))
    print("Piece hierarchy (local offsets):")
    print_piece_tree(model.root_piece)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Dump S3O metadata, texture names, and the indented piece hierarchy."
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

    print_model_info(args.input, model)


if __name__ == "__main__":
    main()
