#!/usr/bin/env python3
"""Write S3O or GLB metadata and pieces as JSON using direct binary parsing."""

import json
import math
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

GLB_HEADER_FORMAT = "<4sII"
GLB_CHUNK_HEADER_FORMAT = "<I4s"
GLB_HEADER_SIZE = struct.calcsize(GLB_HEADER_FORMAT)
GLB_CHUNK_HEADER_SIZE = struct.calcsize(GLB_CHUNK_HEADER_FORMAT)

GLB_COMPONENT_TYPES = {
    5120: ("b", 1.0 / 128.0),
    5121: ("B", 1.0 / 255.0),
    5122: ("h", 1.0 / 32768.0),
    5123: ("H", 1.0 / 65535.0),
    5125: ("I", 1.0 / 4294967295.0),
    5126: ("f", 1.0),
}

GLB_TYPE_COMPONENT_COUNTS = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT3": 9,
    "MAT4": 16,
}


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


def read_glb(data):
    """Read the JSON and binary chunks from a GLB 2.0 file."""
    check_range(data, 0, GLB_HEADER_SIZE, "GLB header")
    magic, version, total_length = struct.unpack(
        GLB_HEADER_FORMAT, data[:GLB_HEADER_SIZE]
    )
    if magic != b"glTF":
        raise ValueError("invalid GLB magic")
    if version != 2:
        raise ValueError("unsupported GLB version {}".format(version))
    if total_length < GLB_HEADER_SIZE or total_length > len(data):
        raise ValueError("GLB declared length is outside the file")

    json_chunk = None
    binary_chunk = None
    offset = GLB_HEADER_SIZE
    while offset < total_length:
        check_range(data, offset, GLB_CHUNK_HEADER_SIZE, "GLB chunk header")
        chunk_length, chunk_type = struct.unpack(
            GLB_CHUNK_HEADER_FORMAT,
            data[offset : offset + GLB_CHUNK_HEADER_SIZE],
        )
        offset += GLB_CHUNK_HEADER_SIZE
        if offset > total_length - chunk_length:
            raise ValueError("GLB chunk extends beyond the declared file length")
        chunk = data[offset : offset + chunk_length]
        offset += chunk_length

        if chunk_type == b"JSON":
            if json_chunk is not None:
                raise ValueError("GLB contains more than one JSON chunk")
            json_chunk = chunk
        elif chunk_type == b"BIN\x00":
            if binary_chunk is not None:
                raise ValueError("GLB contains more than one binary chunk")
            binary_chunk = chunk

    if json_chunk is None:
        raise ValueError("GLB has no JSON chunk")
    try:
        gltf = json.loads(json_chunk.rstrip(b" \t\r\n\x00").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("GLB JSON chunk is invalid: {}".format(error))
    if not isinstance(gltf, dict):
        raise ValueError("GLB JSON root is not an object")
    return gltf, binary_chunk


def glb_axis_matrix(s3ocompat):
    if s3ocompat:
        # glTF (x, y, z) -> Recoil/S3O (-x, z, y)
        return [[-1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, 1.0, 0.0]]
    # glTF (x, y, z) -> Recoil/S3O (x, z, -y)
    return [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]]


def glb_mat3_identity():
    return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def glb_mat3_mul(a, b):
    return [
        [
            a[row][0] * b[0][column]
            + a[row][1] * b[1][column]
            + a[row][2] * b[2][column]
            for column in range(3)
        ]
        for row in range(3)
    ]


def glb_mat3_mul_vec(matrix, vector):
    return tuple(
        matrix[row][0] * vector[0]
        + matrix[row][1] * vector[1]
        + matrix[row][2] * vector[2]
        for row in range(3)
    )


def glb_vec_add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def glb_vec_sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def glb_vec_length(vector):
    return math.sqrt(sum(component * component for component in vector))


def glb_quaternion_matrix(quaternion):
    x, y, z, w = quaternion
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length < 0.000001:
        return glb_mat3_identity()
    x, y, z, w = (value / length for value in (x, y, z, w))
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ]


def glb_node_transform(node):
    """Return (translation, rotation matrix, uniform scale) for a glTF node."""
    name = node.get("name") or "unnamed node"
    matrix = node.get("matrix")
    if matrix is not None:
        if len(matrix) != 16:
            raise ValueError("node '{}' has an invalid matrix".format(name))
        columns = [
            (matrix[0], matrix[1], matrix[2]),
            (matrix[4], matrix[5], matrix[6]),
            (matrix[8], matrix[9], matrix[10]),
        ]
        scales = [glb_vec_length(column) for column in columns]
        scale = sum(scales) / 3.0
        if min(scales) < 0.000001:
            raise ValueError("node '{}' has a zero scale matrix".format(name))
        if (
            abs(scales[0] - scales[1]) > 0.001 * scale
            or abs(scales[1] - scales[2]) > 0.001 * scale
        ):
            raise ValueError("node '{}' has a non-uniform scale".format(name))
        rotation = [
            [columns[column][row] / scales[column] for column in range(3)]
            for row in range(3)
        ]
        return (
            (matrix[12], matrix[13], matrix[14]),
            rotation,
            scale,
        )

    translation = tuple(node.get("translation", (0.0, 0.0, 0.0)))
    quaternion = tuple(node.get("rotation", (0.0, 0.0, 0.0, 1.0)))
    scale_values = tuple(node.get("scale", (1.0, 1.0, 1.0)))
    if len(translation) != 3 or len(quaternion) != 4 or len(scale_values) != 3:
        raise ValueError("node '{}' has an invalid transform".format(name))
    sx, sy, sz = scale_values
    if sx < 0.000001:
        raise ValueError("node '{}' has a zero or negative scale".format(name))
    if abs(sx - sy) > 0.001 * sx or abs(sy - sz) > 0.001 * sx:
        raise ValueError("node '{}' has a non-uniform scale".format(name))
    return translation, glb_quaternion_matrix(quaternion), sx


def glb_read_accessor(gltf, binary_chunk, accessor_index):
    accessors = gltf.get("accessors") or []
    if not isinstance(accessor_index, int) or not 0 <= accessor_index < len(accessors):
        raise ValueError("GLB accessor index {} is invalid".format(accessor_index))
    accessor = accessors[accessor_index]
    buffer_view_index = accessor.get("bufferView")
    if buffer_view_index is None:
        raise ValueError("sparse or missing-bufferView accessors are not supported")
    buffer_views = gltf.get("bufferViews") or []
    if not 0 <= buffer_view_index < len(buffer_views):
        raise ValueError("GLB bufferView index {} is invalid".format(buffer_view_index))
    buffer_view = buffer_views[buffer_view_index]

    component_type = accessor.get("componentType")
    if component_type not in GLB_COMPONENT_TYPES:
        raise ValueError("unsupported GLB accessor component type {}".format(component_type))
    accessor_type = accessor.get("type")
    if accessor_type not in GLB_TYPE_COMPONENT_COUNTS:
        raise ValueError("unsupported GLB accessor type {}".format(accessor_type))
    format_character, normalized_divisor = GLB_COMPONENT_TYPES[component_type]
    if component_type == 5126 and accessor.get("normalized", False):
        raise ValueError("normalized float GLB accessors are invalid")

    component_count = GLB_TYPE_COMPONENT_COUNTS[accessor_type]
    item_format = "<{}{}".format(component_count, format_character)
    item_size = struct.calcsize(item_format)
    stride = buffer_view.get("byteStride") or item_size
    if stride < item_size:
        raise ValueError("GLB bufferView stride is smaller than its item size")

    buffer_index = buffer_view.get("buffer", 0)
    buffers = gltf.get("buffers") or []
    if not 0 <= buffer_index < len(buffers):
        raise ValueError("GLB buffer index {} is invalid".format(buffer_index))
    buffer = buffers[buffer_index]
    if buffer.get("uri") is not None:
        raise ValueError("external GLTF buffers are not supported by the standalone parser")
    if buffer_index != 0 or binary_chunk is None:
        raise ValueError("GLB has no embedded binary data for buffer {}".format(buffer_index))

    count = accessor.get("count", 0)
    if not isinstance(count, int) or count < 0:
        raise ValueError("GLB accessor has an invalid count")
    offset = (buffer_view.get("byteOffset") or 0) + (accessor.get("byteOffset") or 0)
    required_size = 0 if count == 0 else (count - 1) * stride + item_size
    check_range(binary_chunk, offset, required_size, "GLB accessor data")

    values = []
    for index in range(count):
        value = struct.unpack_from(item_format, binary_chunk, offset + index * stride)
        if accessor.get("normalized", False):
            value = tuple(component * normalized_divisor for component in value)
        values.append(value)
    return values


def glb_piece_geometry(gltf, binary_chunk, node, bake_matrix):
    mesh_index = node.get("mesh")
    if mesh_index is None:
        return 0, [0.0, 0.0, 0.0], None, None
    meshes = gltf.get("meshes") or []
    if not isinstance(mesh_index, int) or not 0 <= mesh_index < len(meshes):
        raise ValueError("GLB mesh index {} is invalid".format(mesh_index))

    mesh = meshes[mesh_index]
    vertex_count = 0
    mins = [None, None, None]
    maxs = [None, None, None]
    for primitive in mesh.get("primitives", []):
        if primitive.get("mode", 4) != 4:
            continue
        attributes = primitive.get("attributes") or {}
        position_accessor = attributes.get("POSITION")
        if position_accessor is None:
            continue
        positions = glb_read_accessor(gltf, binary_chunk, position_accessor)
        for position in positions:
            if len(position) != 3:
                raise ValueError("GLB POSITION accessor is not a VEC3")
            transformed = glb_mat3_mul_vec(bake_matrix, position)
            for axis in range(3):
                if mins[axis] is None:
                    mins[axis] = transformed[axis]
                    maxs[axis] = transformed[axis]
                else:
                    mins[axis] = min(mins[axis], transformed[axis])
                    maxs[axis] = max(maxs[axis], transformed[axis])
        vertex_count += len(positions)

    if vertex_count == 0:
        return 0, [0.0, 0.0, 0.0], None, None
    return (
        vertex_count,
        [maxs[axis] - mins[axis] for axis in range(3)],
        mins,
        maxs,
    )


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

    mins = [None, None, None]
    maxs = [None, None, None]
    for index in range(vertex_count):
        start = vertex_offset + index * VERTEX_SIZE
        vertex = struct.unpack(VERTEX_FORMAT, data[start : start + VERTEX_SIZE])
        for axis in range(3):
            mins[axis] = vertex[axis] if mins[axis] is None else min(mins[axis], vertex[axis])
            maxs[axis] = vertex[axis] if maxs[axis] is None else max(maxs[axis], vertex[axis])
    for index in range(index_count):
        start = index_offset + index * INDEX_SIZE
        struct.unpack(INDEX_FORMAT, data[start : start + INDEX_SIZE])

    if vertex_count == 0:
        return [0.0, 0.0, 0.0]
    return [maxs[axis] - mins[axis] for axis in range(3)]


def read_piece(data, offset, seen_offsets):
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

    dimensions = check_piece_data(data, vertex_count, vertex_offset, index_count, index_offset)
    name = read_string(data, name_offset) or "<unnamed>"
    children = []
    if child_count > 0:
        check_range(data, children_offset, child_count * CHILD_OFFSET_SIZE, "child offset data")
        for index in range(child_count):
            start = children_offset + index * CHILD_OFFSET_SIZE
            child_offset, = struct.unpack(
                CHILD_OFFSET_FORMAT, data[start : start + CHILD_OFFSET_SIZE]
            )
            children.append(read_piece(data, child_offset, seen_offsets))

    result = {
        "name": name,
        "vertices": vertex_count,
        "offset": [x_offset, y_offset, z_offset],
    }
    if vertex_count:
        result["dimensions"] = dimensions
    if children:
        result["children"] = children
    return result


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

    return {
        "info": RECOIL_INFO,
        "model": filename,
        "radius": radius,
        "midpoint": [midpoint_x, midpoint_y, midpoint_z],
        "height": height,
        "textures": [
            read_string(data, texture_1_offset) or None,
            read_string(data, texture_2_offset) or None,
        ],
        "pieces": [read_piece(data, root_offset, set())],
    }


def dump_glb(filename, data):
    gltf, binary_chunk = read_glb(data)
    scenes = gltf.get("scenes") or []
    if not scenes:
        raise ValueError("GLB contains no scenes")
    scene_index = gltf.get("scene", 0)
    if scene_index is None:
        scene_index = 0
    if not isinstance(scene_index, int) or not 0 <= scene_index < len(scenes):
        raise ValueError("GLB scene index {} is invalid".format(scene_index))
    scene = scenes[scene_index]
    if not isinstance(scene, dict):
        raise ValueError("GLB scene is not an object")

    raw_extras = scene.get("extras") or {}
    if not isinstance(raw_extras, dict):
        raise ValueError("GLB scene extras are not an object")
    fallback_extras = raw_extras.get("my_addon_props") or {}
    if not isinstance(fallback_extras, dict):
        fallback_extras = {}
    extras = dict(fallback_extras)
    extras.update(raw_extras)

    s3ocompat = bool(extras.get("s3ocompat", False))
    axis = glb_axis_matrix(s3ocompat)
    nodes = gltf.get("nodes") or []
    root_name = scene.get("name") or "Scene"
    root_piece = {
        "name": root_name,
        "vertices": 0,
        "offset": [0.0, 0.0, 0.0],
        "children": [],
    }
    visited = set()
    bbox_mins = [None, None, None]
    bbox_maxs = [None, None, None]

    def build_piece(node_index, parent_wpos, parent_wlin, parent_piece):
        if not isinstance(node_index, int) or not 0 <= node_index < len(nodes):
            raise ValueError("GLB node index {} is invalid".format(node_index))
        if node_index in visited:
            return
        visited.add(node_index)
        node = nodes[node_index]
        if not isinstance(node, dict):
            raise ValueError("GLB node {} is not an object".format(node_index))
        if node.get("skin") is not None:
            return

        translation, rotation, scale = glb_node_transform(node)
        local_linear = [[rotation[row][column] * scale for column in range(3)] for row in range(3)]
        world_linear = glb_mat3_mul(parent_wlin, local_linear)
        world_position = glb_vec_add(
            parent_wpos,
            glb_mat3_mul_vec(parent_wlin, translation),
        )
        bake_matrix = glb_mat3_mul(axis, world_linear)
        offset = glb_mat3_mul_vec(axis, glb_vec_sub(world_position, parent_wpos))
        vertex_count, dimensions, mins, maxs = glb_piece_geometry(
            gltf, binary_chunk, node, bake_matrix
        )
        piece = {
            "name": node.get("name") or "node_{}".format(node_index),
            "vertices": vertex_count,
            "offset": list(offset),
        }
        if vertex_count:
            piece["dimensions"] = dimensions
        piece["children"] = []
        parent_piece["children"].append(piece)

        if mins is not None:
            for axis_index in range(3):
                if bbox_mins[axis_index] is None:
                    bbox_mins[axis_index] = mins[axis_index]
                    bbox_maxs[axis_index] = maxs[axis_index]
                else:
                    bbox_mins[axis_index] = min(bbox_mins[axis_index], mins[axis_index])
                    bbox_maxs[axis_index] = max(bbox_maxs[axis_index], maxs[axis_index])

        for child_index in node.get("children", []) or []:
            build_piece(child_index, world_position, world_linear, piece)

        if not piece["children"]:
            del piece["children"]

    for node_index in scene.get("nodes", []) or []:
        build_piece(node_index, (0.0, 0.0, 0.0), glb_mat3_identity(), root_piece)

    have_geometry = bbox_mins[0] is not None
    if extras.get("radius") is not None:
        radius = float(extras["radius"])
    elif have_geometry:
        radius = max(bbox_maxs[0] - bbox_mins[0], bbox_maxs[2] - bbox_mins[2]) / 2.0
    else:
        radius = None

    if extras.get("height") is not None:
        height = float(extras["height"])
    elif have_geometry:
        height = bbox_maxs[1]
    else:
        height = None

    if extras.get("midpos"):
        midpoint = [float(value) for value in extras["midpos"]]
        if len(midpoint) != 3:
            raise ValueError("GLB scene midpos is not a 3-component vector")
    elif have_geometry:
        midpoint = [
            (bbox_mins[axis_index] + bbox_maxs[axis_index]) / 2.0
            for axis_index in range(3)
        ]
    else:
        midpoint = None

    textures = []
    for key in ("tex1", "tex2"):
        value = extras.get(key)
        textures.append(str(value) if value else None)

    return {
        "info": RECOIL_INFO,
        "model": filename,
        "format": "glb",
        "scene": root_name,
        "s3ocompat": s3ocompat,
        "metadata": extras,
        "radius": radius,
        "midpoint": midpoint,
        "height": height,
        "textures": textures,
        "pieces": [root_piece],
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


def main():
    if len(sys.argv) != 2:
        print("usage: s3o_info_struct.py INPUT", file=sys.stderr)
        return 2

    filename = sys.argv[1]
    try:
        with open(filename, "rb") as input_file:
            data = input_file.read()
        if filename.lower().endswith(".glb"):
            document = dump_glb(filename, data)
        else:
            document = dump_s3o(filename, data)
    except OSError as error:
        print("error: cannot read '{}': {}".format(filename, error), file=sys.stderr)
        return 1
    except (IndexError, KeyError, TypeError, ValueError, struct.error) as error:
        print(
            "error: '{}' is not a valid S3O or GLB file: {}".format(filename, error),
            file=sys.stderr,
        )
        return 1
    print(compact_vector_fields(json.dumps(document, indent=2, allow_nan=False)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
