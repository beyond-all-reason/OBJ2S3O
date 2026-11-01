#!/usr/bin/env python3
"""Dump S3O metadata and pieces by parsing the binary format directly."""

import struct
import sys


HEADER_FORMAT = "<12si5f4i"
PIECE_FORMAT = "<10i3f"
VERTEX_FORMAT = "<3f3f2f"
INDEX_FORMAT = "<i"
CHILD_OFFSET_FORMAT = "<i"

HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
PIECE_SIZE = struct.calcsize(PIECE_FORMAT)
VERTEX_SIZE = struct.calcsize(VERTEX_FORMAT)
INDEX_SIZE = struct.calcsize(INDEX_FORMAT)
CHILD_OFFSET_SIZE = struct.calcsize(CHILD_OFFSET_FORMAT)


def format_vector(vector):
    return "({:.6g}, {:.6g}, {:.6g})".format(*vector)


def read_string(data, offset):
    if offset == 0:
        return ""
    if offset < 0 or offset >= len(data):
        raise ValueError("string offset {} is outside the file".format(offset))

    end = data.find(b"\x00", offset)
    if end == -1:
        raise ValueError("unterminated string at offset {}".format(offset))
    return data[offset:end].decode("utf-8", errors="replace")


def check_range(data, offset, size, description):
    if offset < 0 or offset > len(data) - size:
        raise ValueError(
            "{} range [{}, {}) is outside the file".format(
                description, offset, offset + size
            )
        )


def check_piece_data(data, vertex_count, vertex_offset, index_count, index_offset):
    if vertex_count < 0 or index_count < 0:
        raise ValueError("piece contains a negative vertex or index count")

    check_range(data, vertex_offset, vertex_count * VERTEX_SIZE, "vertex data")
    check_range(data, index_offset, index_count * INDEX_SIZE, "index data")

    for index in range(vertex_count):
        start = vertex_offset + index * VERTEX_SIZE
        struct.unpack(VERTEX_FORMAT, data[start : start + VERTEX_SIZE])
    for index in range(index_count):
        start = index_offset + index * INDEX_SIZE
        struct.unpack(INDEX_FORMAT, data[start : start + INDEX_SIZE])


def print_piece(data, offset, depth, seen_offsets):
    if offset in seen_offsets:
        raise ValueError("piece offset {} is referenced more than once".format(offset))
    seen_offsets.add(offset)

    check_range(data, offset, PIECE_SIZE, "piece header")
    fields = struct.unpack(PIECE_FORMAT, data[offset : offset + PIECE_SIZE])
    (
        name_offset,
        child_count,
        children_offset,
        vertex_count,
        vertex_offset,
        vertex_type,
        primitive_type,
        index_count,
        index_offset,
        collision_data_offset,
        x_offset,
        y_offset,
        z_offset,
    ) = fields

    if child_count < 0:
        raise ValueError("piece contains a negative child count")
    if primitive_type not in (0, 1, 2):
        raise ValueError("piece contains an unknown primitive type {}".format(primitive_type))

    check_piece_data(data, vertex_count, vertex_offset, index_count, index_offset)
    name = read_string(data, name_offset) or "<unnamed>"
    indent = "  " * depth
    offset_text = format_vector((x_offset, y_offset, z_offset))
    print("{}{} vertices={} offset={}".format(indent, name, vertex_count, offset_text))

    if child_count == 0:
        return
    check_range(data, children_offset, child_count * CHILD_OFFSET_SIZE, "child offset data")
    for index in range(child_count):
        start = children_offset + index * CHILD_OFFSET_SIZE
        child_offset, = struct.unpack(
            CHILD_OFFSET_FORMAT, data[start : start + CHILD_OFFSET_SIZE]
        )
        print_piece(data, child_offset, depth + 1, seen_offsets)


def dump_s3o(filename, data):
    check_range(data, 0, HEADER_SIZE, "S3O header")
    (
        magic,
        version,
        radius,
        height,
        midpoint_x,
        midpoint_y,
        midpoint_z,
        root_offset,
        collision_data_offset,
        texture_1_offset,
        texture_2_offset,
    ) = struct.unpack(HEADER_FORMAT, data[:HEADER_SIZE])

    if magic != b"Spring unit\x00":
        raise ValueError("invalid S3O magic")
    if version != 0:
        raise ValueError("unsupported S3O version {}".format(version))
    if collision_data_offset != 0:
        raise ValueError("collision data is not supported")

    texture_1 = read_string(data, texture_1_offset) or "<none>"
    texture_2 = read_string(data, texture_2_offset) or "<none>"

    print("Model: {}".format(filename))
    print("Radius: {:.6g}".format(radius))
    print("Midpoint: {}".format(format_vector((midpoint_x, midpoint_y, midpoint_z))))
    print("Height: {:.6g}".format(height))
    print("Textures:")
    print("  1: {}".format(texture_1))
    print("  2: {}".format(texture_2))
    print("Piece hierarchy (local offsets):")
    print_piece(data, root_offset, 0, set())


def main():
    if len(sys.argv) != 2:
        print("usage: s3o_info_struct.py INPUT", file=sys.stderr)
        return 2

    filename = sys.argv[1]
    try:
        with open(filename, "rb") as input_file:
            data = input_file.read()
        dump_s3o(filename, data)
    except OSError as error:
        print("error: cannot read '{}': {}".format(filename, error), file=sys.stderr)
        return 1
    except (ValueError, struct.error) as error:
        print("error: '{}' is not a valid S3O file: {}".format(filename, error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
