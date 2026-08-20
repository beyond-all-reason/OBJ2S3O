#!/usr/bin/env python
# converts a GLTF model (.glb or .gltf) to the Spring/Recoil S3O model format
#
# requires pygltflib (pip install pygltflib)
#
# the GLTF model is expected to be exported from Blender with the Z axis
# pointing up, and with the exporter's "+Y up" option unchecked (see the
# blender-gltf-import guide in the RecoilEngine docs). The axis transform
# applied below matches the engine's GLTF model loader (GLTFParser.cpp):
#   s3ocompat=false: (x, y, z) -> (x, z, -y)
#   s3ocompat=true:  (x, y, z) -> (-x, z, y)
#
# s3o pieces only support a translation offset relative to the parent piece
# (there is no rotation or scale in the binary format), so each gltf node's
# rotation and scale, plus the accumulated rotations of its ancestors, are
# baked into the piece's vertex data. Runtime piece animations (turrets,
# weapons) still work, because the engine rotates s3o pieces around their
# own origin.
#
# gltf features without an s3o equivalent are ignored (a warning is printed
# once per file for each of them): tangents, vertex colors, second UV sets,
# materials, textures/images, animations, lights, cameras. Skinned meshes
# are not supported, and the whole node subtree of a skinned node is skipped
# with a warning.

import argparse
import base64
import math
import os
import struct
import sys

from pygltflib import GLTF2

from s3o import S3O, S3OPiece, face_normal, recursively_optimize_pieces


# glTF componentType -> (struct format char, normalized divisor)
_COMPONENT_TYPES = {
	5120: ('b', 1.0 / 128.0),           # BYTE
	5121: ('B', 1.0 / 255.0),           # UNSIGNED_BYTE
	5122: ('h', 1.0 / 32768.0),         # SHORT
	5123: ('H', 1.0 / 65535.0),         # UNSIGNED_SHORT
	5125: ('I', 1.0 / 4294967295.0),    # UNSIGNED_INT
	5126: ('f', 1.0),                   # FLOAT
}

_TYPE_COMPONENT_COUNTS = {
	'SCALAR': 1, 'VEC2': 2, 'VEC3': 3, 'VEC4': 4, 'MAT3': 9, 'MAT4': 16
}


## math helpers (3x3 rows, column vectors)

def axis_matrix(s3ocompat):
	# glTF (Blender Z-up, right handed) -> S3O model space (right handed, Y up)
	# same transforms the engine applies in GLTFParser.cpp
	if s3ocompat:
		# (x, y, z) -> (-x, z, y)
		return [[-1, 0, 0], [0, 0, 1], [0, 1, 0]]
	# (x, y, z) -> (x, z, -y)
	return [[1, 0, 0], [0, 0, 1], [0, -1, 0]]


def mat3_identity():
	return [[1, 0, 0], [0, 1, 0], [0, 0, 1]]


def mat3_mul(a, b):
	return [
		[a[0][0] * b[0][0] + a[0][1] * b[1][0] + a[0][2] * b[2][0],
		 a[0][0] * b[0][1] + a[0][1] * b[1][1] + a[0][2] * b[2][1],
		 a[0][0] * b[0][2] + a[0][1] * b[1][2] + a[0][2] * b[2][2]],
		[a[1][0] * b[0][0] + a[1][1] * b[1][0] + a[1][2] * b[2][0],
		 a[1][0] * b[0][1] + a[1][1] * b[1][1] + a[1][2] * b[2][1],
		 a[1][0] * b[0][2] + a[1][1] * b[1][2] + a[1][2] * b[2][2]],
		[a[2][0] * b[0][0] + a[2][1] * b[1][0] + a[2][2] * b[2][0],
		 a[2][0] * b[0][1] + a[2][1] * b[1][1] + a[2][2] * b[2][1],
		 a[2][0] * b[0][2] + a[2][1] * b[1][2] + a[2][2] * b[2][2]],
	]


def mat3_mul_vec(m, v):
	return (
		m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
		m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
		m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
	)


def vec_add(a, b):
	return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def vec_sub(a, b):
	return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vec_length(v):
	return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


def vec_normalize(v):
	l = vec_length(v)
	if l < 0.000001:
		return (0.0, 0.0, 0.0)
	return (v[0] / l, v[1] / l, v[2] / l)


def quat_to_matrix(q):
	x, y, z, w = q
	l = math.sqrt(x * x + y * y + z * z + w * w)
	if l < 0.000001:
		return mat3_identity()
	x, y, z, w = x / l, y / l, z / l, w / l
	return [
		[1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
		[2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
		[2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
	]


## gltf reading

def get_node_transform(node):
	# returns (translation, rotation_matrix, uniform_scale)
	name = node.name or 'unnamed node'
	if node.matrix:
		m = node.matrix
		c0 = (m[0], m[1], m[2])
		c1 = (m[4], m[5], m[6])
		c2 = (m[8], m[9], m[10])
		t = (m[12], m[13], m[14])
		s0, s1, s2 = vec_length(c0), vec_length(c1), vec_length(c2)
		s = (s0 + s1 + s2) / 3
		if s < 0.000001:
			print ('[ERROR]', 'node', name, 'has a zero scale matrix, cannot convert it')
			sys.exit(1)
		if abs(s0 - s1) > 0.001 * s or abs(s1 - s2) > 0.001 * s:
			print ('[ERROR]', 'node', name, 'has a non-uniform scale, which s3o cannot represent')
			sys.exit(1)
		rot = [
			[c0[0] / s0, c1[0] / s1, c2[0] / s2],
			[c0[1] / s0, c1[1] / s1, c2[1] / s2],
			[c0[2] / s0, c1[2] / s1, c2[2] / s2],
		]
		return t, rot, s
	t = tuple(node.translation) if node.translation else (0, 0, 0)
	quat = (0, 0, 0, 1) if node.rotation is None else tuple(node.rotation)
	scale = (1, 1, 1) if node.scale is None else tuple(node.scale)
	sx, sy, sz = scale
	s = sx
	if s < 0.000001:
		print ('[ERROR]', 'node', name, 'has a zero scale, cannot convert it')
		sys.exit(1)
	if abs(sx - sy) > 0.001 * s or abs(sy - sz) > 0.001 * s:
		print ('[ERROR]', 'node', name, 'has a non-uniform scale, which s3o cannot represent')
		sys.exit(1)
	return t, quat_to_matrix(quat), s


def get_buffer_blob(gltf, filepath, buffer_index):
	blob = gltf.binary_blob()
	if blob:
		return blob
	# .gltf with external buffers
	buf = gltf.buffers[buffer_index]
	uri = buf.uri
	if uri is None:
		print ('[ERROR]', 'gltf buffer %i has no uri and the file has no embedded bin chunk' % buffer_index)
		sys.exit(1)
	if uri.startswith('data:'):
		_, _, data = uri.partition(',')
		return base64.b64decode(data)
	path = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(filepath)), uri))
	if not os.path.isfile(path):
		print ('[ERROR]', 'buffer file not found:', path)
		sys.exit(1)
	return open(path, 'rb').read()


def read_accessor(gltf, filepath, buffer_blobs, accessor, as_indices=False):
	ctype = accessor.componentType
	if ctype not in _COMPONENT_TYPES:
		print ('[ERROR]', 'unsupported accessor component type', ctype)
		sys.exit(1)
	if ctype == 5126 and accessor.normalized:
		print ('[ERROR]', 'normalized float accessor is invalid gltf')
		sys.exit(1)
	f, norm = _COMPONENT_TYPES[ctype]
	ncomp = _TYPE_COMPONENT_COUNTS[accessor.type]
	fmt = '<' + f * ncomp
	itemsize = struct.calcsize(fmt)
	bv = gltf.bufferViews[accessor.bufferView]
	stride = bv.byteStride if bv.byteStride else itemsize
	if bv.buffer not in buffer_blobs:
		buffer_blobs[bv.buffer] = get_buffer_blob(gltf, filepath, bv.buffer)
	blob = buffer_blobs[bv.buffer]
	offset = (bv.byteOffset or 0) + (accessor.byteOffset or 0)
	if offset + accessor.count * stride > len(blob):
		print ('[ERROR]', 'accessor reads outside of its buffer')
		sys.exit(1)
	values = []
	for i in range(accessor.count):
		v = struct.unpack_from(fmt, blob, offset + i * stride)
		if accessor.normalized:
			v = tuple(x * norm for x in v)
		if as_indices:
			v = (int(round(v[0])),)
		values.append(v)
	return values


def get_scene_extras(scene):
	# the engine's GLTF loader reads these from the top level of the scene
	# extras; also look one level under 'my_addon_props', where the old
	# Blender addon stored them
	extras = scene.extras or {}
	fallback = extras.get('my_addon_props') or {}
	merged = dict(fallback)
	merged.update(extras)
	return merged


## conversion

def compute_piece_face_normals(piece):
	# fill zero normals with the flat normal of the vertex's first triangle
	if len(piece.indices) < 3 or len(piece.indices) % 3 != 0:
		return
	for i in range(0, len(piece.indices), 3):
		i0, i1, i2 = piece.indices[i], piece.indices[i + 1], piece.indices[i + 2]
		n = face_normal(piece.vertices[i0], piece.vertices[i1], piece.vertices[i2])
		for vi in (i0, i1, i2):
			if vec_length(piece.vertices[vi][1]) < 0.01:
				piece.vertices[vi] = (piece.vertices[vi][0], n, piece.vertices[vi][2])


def convert_glb(glbfile, outputfilename, s3ocompat='auto', tex1=None, tex2=None,
                radius=None, height=None, midpos=None, optimize=True):
	gltf = GLTF2.load(glbfile)
	if not gltf.scenes:
		print ('[ERROR]', 'no scenes in', glbfile)
		return False
	scene = gltf.scenes[gltf.scene if gltf.scene is not None else 0]

	extras = get_scene_extras(scene)
	if s3ocompat == 'auto':
		s3ocompat_flag = bool(extras.get('s3ocompat', False))
	else:
		s3ocompat_flag = s3ocompat == 'true'
	axis = axis_matrix(s3ocompat_flag)
	print ('[INFO]', 's3ocompat =', s3ocompat_flag)

	# empty s3o shell, its synthetic root piece is replaced below
	model = S3O('', isobj=True)
	root_piece = S3OPiece('', (0, 0, 0), name=(scene.name or 'Scene').encode('utf-8'))
	root_piece.parent = None
	root_piece.parent_offset = (0.0, 0.0, 0.0)
	root_piece.primitive_type = 'triangles'
	model.root_piece = root_piece

	warnings = set()
	buffer_blobs = {}
	visited = [False] * len(gltf.nodes)
	pieces_needing_normals = []


	def read(accessor, as_indices=False):
		return read_accessor(gltf, glbfile, buffer_blobs, accessor, as_indices)


	def build_piece(node_idx, parent_piece, parent_wpos, parent_wlin):
		node = gltf.nodes[node_idx]
		name = node.name or ('node_%d' % node_idx)
		if visited[node_idx]:
			print ('[WARN]', 'node', name, 'is referenced more than once, skipping it (s3o pieces form a tree)')
			return
		visited[node_idx] = True

		tn, rn, sn = get_node_transform(node)
		wlin = mat3_mul(parent_wlin, [
			[rn[0][0] * sn, rn[0][1] * sn, rn[0][2] * sn],
			[rn[1][0] * sn, rn[1][1] * sn, rn[1][2] * sn],
			[rn[2][0] * sn, rn[2][1] * sn, rn[2][2] * sn],
		])
		wpos = vec_add(parent_wpos, mat3_mul_vec(parent_wlin, tn))

		if node.skin is not None:
			print ('[WARN]', 'node', name, 'is skinned, skinning is not supported by s3o, skipping this node and its children')
			return

		# rotation/scale of the whole ancestor chain, applied to glTF space
		bake_matrix = mat3_mul(axis, wlin)

		piece = S3OPiece('', (0, 0, 0), name=name.encode('utf-8'))
		piece.parent = parent_piece
		offset = mat3_mul_vec(axis, vec_sub(wpos, parent_wpos))
		piece.parent_offset = (offset[0], offset[1], offset[2])
		piece.primitive_type = 'triangles'

		if node.mesh is not None:
			mesh = gltf.meshes[node.mesh]
			vertices = []
			indices = []
			piece_has_normals = True
			for prim in mesh.primitives:
				if prim.mode != 4:
					print ('[WARN]', 'node', name, 'has a non-triangle primitive (mode %s), skipping it' % prim.mode)
					continue
				attrs = prim.attributes
				if attrs.POSITION is None:
					print ('[WARN]', 'node', name, 'has a primitive without POSITION, skipping it')
					continue
				positions = read(gltf.accessors[attrs.POSITION])
				normals = None
				if attrs.NORMAL is not None:
					normals = read(gltf.accessors[attrs.NORMAL])
				else:
					piece_has_normals = False
					if 'missing normals' not in warnings:
						warnings.add('missing normals')
						print ('[WARN]', 'primitive without NORMAL attribute found, its normals will be computed per-face')
				uvs = None
				if attrs.TEXCOORD_0 is not None:
					uvs = read(gltf.accessors[attrs.TEXCOORD_0])
				if attrs.TANGENT is not None and 'tangents' not in warnings:
					warnings.add('tangents')
					print ('[WARN]', 'TANGENT attributes are not supported by s3o and will be ignored')
				if attrs.COLOR_0 is not None and 'vertex colors' not in warnings:
					warnings.add('vertex colors')
					print ('[WARN]', 'vertex colors (COLOR_0) are not supported by s3o and will be ignored')
				if attrs.TEXCOORD_1 is not None and 'second UV set' not in warnings:
					warnings.add('second UV set')
					print ('[WARN]', 's3o only supports one UV set, TEXCOORD_1 will be ignored')
				vbase = len(vertices)
				for i, p in enumerate(positions):
					pos = mat3_mul_vec(bake_matrix, p)
					if normals is not None:
						nor = vec_normalize(mat3_mul_vec(bake_matrix, normals[i]))
					else:
						nor = (0.0, 0.0, 0.0)
					if uvs is not None:
						# glTF (OpenGL) has the V axis inverted compared to s3o/DDS
						# textures, flip it back
						uv = (uvs[i][0], 1.0 - uvs[i][1])
					else:
						uv = (0.0, 0.0)
					vertices.append((pos, nor, uv))
				if prim.indices is not None:
					indc = read(gltf.accessors[prim.indices], as_indices=True)
					indices.extend(vbase + i0[0] for i0 in indc)
				else:
					indices.extend(range(vbase, vbase + len(positions)))
			piece.vertices = vertices
			piece.indices = indices
			if vertices and not piece_has_normals:
				pieces_needing_normals.append(piece)

		parent_piece.children.append(piece)

		for child_idx in node.children:
			build_piece(child_idx, piece, wpos, wlin)

	for root_idx in scene.nodes:
		build_piece(root_idx, root_piece, (0.0, 0.0, 0.0), mat3_identity())

	for piece in pieces_needing_normals:
		compute_piece_face_normals(piece)

	# header parameters
	if tex1 is not None:
		model.texture_paths[0] = str(tex1).encode('utf-8')
	elif extras.get('tex1'):
		model.texture_paths[0] = str(extras['tex1']).encode('utf-8')
	if tex2 is not None:
		model.texture_paths[1] = str(tex2).encode('utf-8')
	elif extras.get('tex2'):
		model.texture_paths[1] = str(extras['tex2']).encode('utf-8')

	# bounding box in s3o model space (used for fallbacks and sanity info)
	minx, miny, minz, maxx, maxy, maxz = root_piece.recursively_get_bounding_box(
		1e18, 1e18, 1e18, -1e18, -1e18, -1e18)
	have_geometry = maxx > -1e17

	if radius is not None:
		model.collision_radius = float(radius)
	elif extras.get('radius') is not None:
		model.collision_radius = float(extras['radius'])
	elif have_geometry:
		model.collision_radius = (max(maxx - minx, maxz - minz)) / 2

	if height is not None:
		model.height = float(height)
	elif extras.get('height') is not None:
		model.height = float(extras['height'])
	elif have_geometry:
		model.height = maxy

	if midpos is not None:
		model.midpoint = tuple(midpos)
	elif extras.get('midpos'):
		model.midpoint = tuple(float(v) for v in extras['midpos'])
	elif have_geometry:
		model.midpoint = ((minx + maxx) / 2, (miny + maxy) / 2, (minz + maxz) / 2)

	print ('[INFO]', 'S3O header: radius=%.2f height=%.2f midpoint=(%.2f, %.2f, %.2f)' % (
		model.collision_radius, model.height,
		model.midpoint[0], model.midpoint[1], model.midpoint[2]))
	print ('[INFO]', 'textures: %s %s' % (model.texture_paths[0], model.texture_paths[1]))
	print ('[INFO]', 'model bbox x[%.2f, %.2f] y[%.2f, %.2f] z[%.2f, %.2f]' % (
		minx, maxx, miny, maxy, minz, maxz))

	if optimize:
		recursively_optimize_pieces(model.root_piece)

	with open(outputfilename, 'wb') as f:
		f.write(model.serialize())
	print ('[INFO]', 'Successfully converted', glbfile, 'to', outputfilename)
	return True


if __name__ == '__main__':
	parser = argparse.ArgumentParser(
		description='Convert a GLTF model (.glb or .gltf) to S3O format')
	parser.add_argument('-i', '--input', type=str, required=True,
		help='input .glb or .gltf model file')
	parser.add_argument('-o', '--output', type=str,
		help='output .s3o file (default: <input base name>.s3o)')
	parser.add_argument('--s3ocompat', choices=['auto', 'true', 'false'], default='auto',
		help='which axis transform to apply. "auto" (default) uses the value of the '
		'"s3ocompat" scene property in the gltf file, if present. "true" gives '
		'(-x, z, y) relative to the file, "false" gives (x, z, -y)')
	parser.add_argument('--tex1', type=str, help='override texture 1 (relative to UnitTextures)')
	parser.add_argument('--tex2', type=str, help='override texture 2 (relative to UnitTextures)')
	parser.add_argument('--radius', type=float, help='override collision radius')
	parser.add_argument('--height', type=float, help='override model height')
	parser.add_argument('--midpos', nargs=3, type=float,
		help='override midpoint position x y z')
	parser.add_argument('--no-optimize', dest='optimize', action='store_false',
		help='do not run the s3o vertex deduplication/vertex cache optimization')
	parser.set_defaults(optimize=True)
	args = parser.parse_args()

	if not os.path.isfile(args.input):
		print ('[ERROR]', 'input file not found:', args.input)
		sys.exit(1)

	output = args.output
	if output is None:
		base, ext = os.path.splitext(args.input)
		output = base + '.s3o'

	convert_glb(args.input, output, args.s3ocompat, args.tex1, args.tex2,
	            args.radius, args.height, args.midpos, args.optimize)
