#!/usr/bin/env python

import sys
import struct
import math
import operator
import random
import vertex_cache
import os
import sys



_S3OHeader_struct = struct.Struct("< 12s i 5f 4i")
_S3OPiece_struct = struct.Struct("< 10i 3f")
_S3OVertex_struct = struct.Struct("< 3f 3f 2f")
_S3OChildOffset_struct = struct.Struct("< i")
_S3OIndex_struct = struct.Struct("< i")


def vectorlength(v):
	length = 0
	for p in v:
		length += p * p
	return math.sqrt(length)


def vectorcross(a, b):
	c = (a[1] * b[2] - a[2] * b[1],
			a[2] * b[0] - a[0] * b[2],
			a[0] * b[1] - a[1] * b[0])

	return c


def vectoradd(a, b):
	return a[0] + b[0], a[1] + b[1], a[2] + b[2]


def vectorminus(a, b):
	return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

def vectormult(a,b):
	return (a[0] * b[0], a[1] * b[1], a[2] * b[2])

def vectorscalarmult(a,b):
	return(a[0] * b, a[1] * b, a[2] * b)

def vectormix(a,b,f):
	return vectoradd(vectorscalarmult(a,f),vectorscalarmult(b,1.0-f))

def normalize(a):
	l = vectorlength(a)
	if l < 0.000001:
		print ('[WARN]', 'Normal vector is nearly 0 long, substituting 1 as length',)
		l = 1.0
	return a[0] / l, a[1] / l, a[2] / l
	
def vectorangle(a,b):
	a = normalize(a)
	b = normalize(b)
	dot = vectormult(a,b)
	cosphi = dot[0] + dot[1] + dot[2]
	angle = math.acos(max(-0.99999,min(0.99999,cosphi)))*90/math.pi
	#print dot,cosphi, angle
	return angle

def face_normal(v1, v2, v3):
	newnormal = vectorcross(
		vectorminus(v2[0],v1[0]),
		vectorminus(v3[0], v1[0])
	)
	if vectorlength(newnormal) < 0.001:
		return (0,1,0)
	else:
		return normalize(newnormal)


def _get_null_terminated_string(data, offset):
	if offset == 0:
		return b""
	else:
		return data[offset:data.index(b'\x00', offset)]

def get_vertex_ao_value_01(u_channel): #return the shadedness of a vertex, in range [0-1]
	return (u_channel * 16384.0) % 1


def recursively_optimize_pieces(piece):
	if type(piece.indices) == type ([]) and len(piece.indices)>4:
		optimize_piece(piece)
		fix_zero_normals_piece(piece)
	for child in piece.children:
		recursively_optimize_pieces(child)

def chunks(l, n):
	""" Yield successive n-sized chunks from l.
	"""
	for i in range(0, len(l), n):
		yield tuple(l[i:i + n])


def optimize_piece(piece):
	remap = {}
	new_indices = []
	print ('[INFO]','Optimizing:',piece.name)
	for index in piece.indices:
		vertex = piece.vertices[index]
		if vertex not in remap:
			remap[vertex] = len(remap)
		new_indices.append(remap[vertex])

	new_vertices = [(index, vertex) for vertex, index in remap.items()]
	new_vertices.sort()
	new_vertices = [vertex for index, vertex in new_vertices]

	if piece.primitive_type == "triangles" and len(new_indices) > 0:
		tris = list(chunks(new_indices, 3))
		acmr = vertex_cache.average_transform_to_vertex_ratio(tris)

		tmp = vertex_cache.get_cache_optimized_triangles(tris)
		acmr_new = vertex_cache.average_transform_to_vertex_ratio(tmp)
		if acmr_new < acmr:
			new_indices = []
			for tri in tmp:
				new_indices.extend(tri)

	vertex_map = []
	remapped_indices = []
	for index in new_indices:
		try:
			new_index = vertex_map.index(index)
		except ValueError:
			new_index = len(vertex_map)
			vertex_map.append(index)

		remapped_indices.append(new_index)

	new_vertices = [new_vertices[index] for index in vertex_map]
	new_indices = remapped_indices

	piece.indices = new_indices
	piece.vertices = new_vertices


##if there are zero vertices, the emit direction is 0,0,1, the emit position is the origin of the piece
##if there is 1 vertex, the emit dir is the vector from the origin to the the position of the first vertex the emit position is the origin of the piece
## if there is more than one, then the emit vector is the vector pointing from v[0] to v[1], and the emit position is v[0]
def fix_zero_normals_piece(piece):
	badnormals = 0
	fixednormals = 0
	nonunitnormals = 0
	if len(piece.indices) > 0:

		for v_i in range(len(piece.vertices)):
			vertex = piece.vertices[v_i]
			# print (vertex[1])
			normallength = vectorlength(vertex[1])
			if normallength < 0.01:  # nearly 0 normal
				badnormals += 1
				if v_i not in piece.indices:
					# this is some sort of degenerate vertex, just replace it's normal with [0,1,0]
					piece.vertices[v_i] = (vertex[0], (0.0, 1.0, 0.0), vertex[2])
					fixednormals += 1
				else:
					for f_i in range(0, len(piece.indices), 3):
						if v_i in piece.indices[f_i:min(len(piece.indices), f_i + 3)]:
							newnormal = vectorcross(vectorminus(piece.vertices[piece.indices[f_i + 1]][0],
																piece.vertices[piece.indices[f_i]][0]),
													vectorminus(piece.vertices[piece.indices[f_i + 2]][0],
																piece.vertices[piece.indices[f_i]][0]))
							if vectorlength(newnormal) < 0.001:
								piece.vertices[v_i] = (vertex[0], (0.0, 1.0, 0.0), vertex[2])
							else:
								piece.vertices[v_i] = (vertex[0], normalize(newnormal), vertex[2])
							fixednormals += 1
							break
			elif normallength < 0.9 or normallength > 1.1:
				nonunitnormals += 1
				piece.vertices[v_i] = (vertex[0], normalize(vertex[1]), vertex[2])
	if badnormals > 0:
		print ('[WARN]', 'Bad normals:', badnormals, 'Fixed:', fixednormals)
		if badnormals != fixednormals:
			print('[WARN]', 'NOT ALL ZERO NORMALS fixed!!!!!')  # this isnt possible with above code anyway :/
	if nonunitnormals > 0:
		print ('[WARN]', nonunitnormals, 'fixed to unit length')


def recalculate_normals(piece, smoothangle, recursive = False):
	#build a list of vertices, each with their list of faces:
	if len(piece.indices) > 4 and piece.primitive_type == 'triangles':
		#explode vertices uniquely
		new_vertices = []
		new_indices = []
		for i,vi in enumerate(piece.indices):
			new_vertices.append(piece.vertices[vi])
			new_indices.append(i)
		piece.vertices = new_vertices
		piece.indices = new_indices
		
		matchingvertices = [] # a list of vertex indices mapping other identical pos vertices
		facespervertex = []
		for i,v1 in enumerate(piece.vertices):
			facespervertex.append([])
			for j, v2 in enumerate(piece.vertices):
				if vectorlength(vectorminus(v1[0], v2[0])) < 0.05:
					facespervertex[i].append(j)
					
		for i,v1 in enumerate(piece.vertices):
			if len(facespervertex[i]) > 0 :
				faceindex = int(math.floor(i/3) *3)
				mynormal = face_normal(
							piece.vertices[faceindex + 0],
							piece.vertices[faceindex + 1],
							piece.vertices[faceindex + 2]
							)
						
				
				mixednorm = (0,0,0)
				for facevertex in facespervertex[i]:
					#get face:
					faceindex = int(math.floor(facevertex/3) *3)
					faceindices = piece.indices[faceindex:faceindex+3]
					mixednorm = vectoradd(mixednorm, 
						face_normal(
							piece.vertices[faceindex + 0],
							piece.vertices[faceindex + 1],
							piece.vertices[faceindex + 2]
							)
						)
				mixednorm = normalize(mixednorm)
				#print(i, len(facespervertex[i]), mixednorm, mynormal)
				if vectorangle(mynormal, mixednorm) <= smoothangle:
					piece.vertices[i] = (piece.vertices[i][0], mixednorm, piece.vertices[i][2])
				else:
					piece.vertices[i] = (piece.vertices[i][0], mynormal, piece.vertices[i][2])
	if recursive:
		for child in piece.children:
			recalculate_normals(child, smoothangle, recursive)
	
# for child in piece.children:
# fix_zero_normals_piece(child)


class S3O(object):
	def S3OtoOBJ(self, filename, optimize_for_wings3d=True):
		print ("[INFO] Wings3d optimization:", optimize_for_wings3d)
		objfile = open(filename, 'w')
		objfile.write('# Spring Unit export, Created by Beherith mysterme@gmail.com with the help of Muon \n')
		objfile.write(
			'# arguments of an object \'o\' piecename:\n# Mxyz = midpoint of an s3o\n# r = unit radius\n# h = height\n#\
			 t1 t2 = textures 1 and 2\n# Oxyz = piece offset\n# p = parent\n')
		header = 'mx=%.2f,my=%.2f,mz=%.2f,r=%.2f,h=%.2f,t1=%s,t2=%s' % (
			self.midpoint[0],
			self.midpoint[1],
			self.midpoint[2],
			self.collision_radius,
			self.height,
			self.texture_paths[0].replace(b'\0', b'').decode(),
			self.texture_paths[1].replace(b'\0', b'').decode()
		)
		obj_vertindex = 0
		obj_normal_uv_index = 0  # obj indexes vertices from 1

		self.recurseS3OtoOBJ(self.root_piece, objfile, header, obj_vertindex, obj_normal_uv_index, 0, (0, 0, 0),
							 optimize_for_wings3d)

	def closest_vertex(self, vtable, q, tolerance):  # returns the index of the closest vertex pos
		v = vtable[q][0]
		for i in range(len(vtable)):
			v2 = vtable[i][0]
			if abs(v2[0] - v[0]) < tolerance and abs(v2[1] - v[1]) < tolerance and abs(v2[2] - v[2]) < tolerance:
				# if i!=q:
				# print i,'matches',q
				return i
		print ('[WARN] No matching vertex for', v, ' not even self!')
		return q

	def in_smoothing_group(self, piece, face_a, face_b, tolerance,
						   step):  # returns wether the two primitives shared a smoothed edge
		shared = 0
		for va in range(face_a, face_a + step):
			for vb in range(face_b, face_b + step):
				v = piece.vertices[piece.indices[va]]
				v2 = piece.vertices[piece.indices[vb]]
				if abs(v2[0][0] - v[0][0]) < tolerance and abs(v2[0][1] - v[0][1]) < tolerance and abs(
								v2[0][2] - v[0][2]) < tolerance:
					if abs(v2[1][0] - v[1][0]) < tolerance and abs(v2[1][1] - v[1][1]) < tolerance and abs(
									v2[1][2] - v[1][2]) < tolerance:
						shared += 1
		if shared >= 3:
			print ('[WARN]', shared, 'shared and normal matching vertices faces', face_a, face_b, piece.name)
		return shared == 2

	def recurseS3OtoOBJ(self, piece, objfile, extraargs, vi, nti, groups, offset,
						optimize_for_wings3d=True):  # vi is our current vertex index counter, nti is the normal/texcoord index counter
		# If we dont use shared vertices in a OBJ file in wings, it wont be able to merge vertices, so we need a mapping to remove redundant vertices, normals and texture indices are separate
		parent = ''
		oldnti = nti

		if piece.parent != None:
			parent = piece.parent.name
			print ('[INFO] parentname=', piece.parent.name)
		# objfile.write('o %s,ox=%.2f,oy=%.2f,oz=%.2f,p=%s,%s\n'%(
		# piece.name,
		# piece.parent_offset[0],
		# piece.parent_offset[1],
		# piece.parent_offset[2],
		# parent,
		# extraargs))

		vdata_obj = []  # vertex, normal and UV in the piece
		fdata_obj = []  # holds the faces in the piece
		hash = {}
		vcount = 0
		step = 3  # todo: fix for not just triangles
		if piece.primitive_type == 'triangles':
			step = 3
		elif piece.primitive_type == 'quads':
			step = 4
		print ('[INFO]', piece.name, 'has', piece.primitive_type, step)
		if len(piece.indices) >= step and piece.primitive_type != "triangle strips":
			objfile.write('o %s,ox=%.2f,oy=%.2f,oz=%.2f,p=%s,%s\n' % (
				piece.name.decode(),
				piece.parent_offset[0],
				piece.parent_offset[1],
				piece.parent_offset[2],
				'' if parent == '' else parent.decode() ,
				extraargs))
			print ('[INFO]','Piece', piece.name, 'has more than 3 vert indices')
			for k in range(0, len(piece.indices), step):  # iterate over faces
				facestr = 'f'
				for i in range(step):
					v = piece.vertices[piece.indices[k + i]]
					#sanity check normals:
					for j in range(3):
						if v[1][j] < 1000000 and v[1][j] > -1000000:
							pass #any comparison of NaN is always false
						else:
							v=(v[0],(0.0,0.0,0.0),v[2])
							print ('[WARN]','NAN normal encountered in piece',piece.name,'replacing with 0')
					if float('nan') in v[1]:
						print ('[WARN]','NAN normal encountered in piece',piece.name)
					if optimize_for_wings3d:
						closest = self.closest_vertex(piece.vertices, piece.indices[k + i], 0.002)
						if closest not in hash:
							# print 'closest',closest,'not in hash',hash
							vcount += 1
							hash[closest] = vcount
							vdata_obj.append('v %f %f %f\n' % (
								v[0][0] + offset[0] + piece.parent_offset[0], v[0][1] + offset[1] + piece.parent_offset[1],
								v[0][2] + offset[2] + piece.parent_offset[2]))
						vdata_obj.append('vn %f %f %f\n' % (v[1][0], v[1][1], v[1][2]))
						vdata_obj.append('vt %.9f %.9f\n' % (v[2][0], v[2][1]))
						nti += 1
						facestr += ' %i/%i/%i' % (vi + hash[closest], nti, nti)


					else:
						closest = piece.indices[k + i]

						if closest not in hash:
							# print 'closest',closest,'not in hash',hash
							vcount += 1
							hash[closest] = vcount
							vdata_obj.append('v %f %f %f\n' % (
								v[0][0] + offset[0] + piece.parent_offset[0], v[0][1] + offset[1] + piece.parent_offset[1],
								v[0][2] + offset[2] + piece.parent_offset[2]))
						vdata_obj.append('vn %f %f %f\n' % (v[1][0], v[1][1], v[1][2]))
						vdata_obj.append('vt %.9f %.9f\n' % (v[2][0], v[2][1]))
						nti += 1
						# if 1==1: #closest>=piece.indices[k+i]: #no matching vert

						facestr += ' %i/%i/%i' % (vi + hash[closest], nti, nti)

				fdata_obj.append(facestr + '\n')
			for l in vdata_obj:
				objfile.write(l)
			# now its time to smooth this bitch!
			# how wings3d processes obj meshes:
			# if no normals are specified, it merges edges correctly, but all edges are soft
			# if normals are specified, but there are no smoothing groups,
			# it will treat each smoothed group as a separate mesh in an object
			# if normals AND smoothing groups are specified, it works as it should

			faces = {}
			if optimize_for_wings3d:
				for face1 in range(0, len(piece.indices), step):
					# for f2 in range(f1+step,len(piece.indices),step):
					for face2 in range(0, len(piece.indices), step):
						if face1 != face2 and self.in_smoothing_group(piece, face1, face2, 0.001, step):
							f1 = face1 / step
							f2 = face2 / step
							if f1 in faces and f2 in faces:
								if faces[f2] != faces[f1]:
									#print '[INFO]', 'Conflicting smoothing groups!', f1, f2, faces[f1], faces[
									#	f2], 'resolving with merge!'
									greater = max(faces[f2], faces[f1])
									lesser = min(faces[f2], faces[f1])
									for faceindex in faces.keys():
										if faces[faceindex] == greater:
											faces[faceindex] = lesser
										elif faces[faceindex] > greater:
											faces[faceindex] -= 1
									groups -= 1
								# else:
								# print 'already in same group, yay!',f1,f2,faces[f1],faces[f2]
							elif f1 in faces:
								faces[f2] = faces[f1]
							elif f2 in faces:
								faces[f1] = faces[f2]
							else:
								groups += 1
								faces[f1] = groups
								faces[f2] = groups
							# if a face shares any two optimized position vertices and has equal normals on that,
							# it is in one smoothing group.
							# does it work for any 1
			groupids = set(faces.values())
			print ('[INFO]', 'Sets of smoothing groups in piece', piece.name, 'are', groupids, groups)

			nonsmooth_faces = False
			for l in range(len(fdata_obj)):
				if l not in faces:
					nonsmooth_faces = True
			if nonsmooth_faces:
				objfile.write('s off\n')
			for l in range(len(fdata_obj)):
				if l not in faces:
					objfile.write(fdata_obj[l])
			for k in groupids:
				objfile.write('s ' + str(k) + '\n')
				for l in range(len(fdata_obj)):
					if l in faces and faces[l] == k:
						objfile.write(fdata_obj[l])
			print ('[INFO]', 'Optimized vertex count=', vcount, 'unoptimized count=', nti - oldnti)
		elif piece.primitive_type == "triangle strips":
			print ('[WARN]', piece.name, 'has a triangle strip type, this is unsupported by this application, skipping piece!')
		else:
			if not optimize_for_wings3d:
				print ('[WARN]', 'Skipping empty emit piece', piece.name, 'because wings3d optimization is off!')
			else:
				print ('[INFO]', 'Empty piece', piece.name, 'writing placeholder face with primitive type',\
					piece.primitive_type, '#vertices=', len(piece.vertices), '#indices=', len(piece.indices))
				objfile.write('o %s,ox=%.2f,oy=%.2f,oz=%.2f,p=%s,%s,e=%i\n' % (
					piece.name.decode(),
					piece.parent_offset[0],
					piece.parent_offset[1],
					piece.parent_offset[2],
					'' if parent == '' else parent.decode(),
					'' if extraargs=='' else extraargs.decode(),
					len(piece.vertices)))
				if len(piece.vertices) == 0:
					objfile.write('v %f %f %f\n' % (
						offset[0] + piece.parent_offset[0], offset[1] + piece.parent_offset[1],
						offset[2] + piece.parent_offset[2]))
					objfile.write('v %f %f %f\n' % (
						offset[0] + piece.parent_offset[0], offset[1] + piece.parent_offset[1],
						4 + offset[2] + piece.parent_offset[2]))
					objfile.write('v %f %f %f\n' % (
						offset[0] + piece.parent_offset[0], 2 + offset[1] + piece.parent_offset[1],
						offset[2] + piece.parent_offset[2]))
					# objfile.write('v 0 0 0\n')
					# objfile.write('v 0 0 1\n')
					objfile.write('f %i/1/1 %i/2/2 %i/3/3\n' % (vi + 1, vi + 2, vi + 3))
					vcount += 3
				elif len(piece.vertices) == 1:
					print ('[INFO]', 'Emit vertices:', piece.vertices, 'offset:  %f %f %f\n' % (
						offset[0] + piece.parent_offset[0], offset[1] + piece.parent_offset[1],
						offset[2] + piece.parent_offset[2]))
					v = piece.vertices[0]
					objfile.write('v %f %f %f\n' % (
						offset[0] + piece.parent_offset[0], offset[1] + piece.parent_offset[1],
						offset[2] + piece.parent_offset[2]))
					objfile.write('v %f %f %f\n' % (
						v[0][0] + offset[0] + piece.parent_offset[0], v[0][1] + offset[1] + piece.parent_offset[1],
						v[0][2] + offset[2] + piece.parent_offset[2]))
					objfile.write('v %f %f %f\n' % (
						offset[0] + piece.parent_offset[0], 2 + offset[1] + piece.parent_offset[1],
						offset[2] + piece.parent_offset[2]))
					# objfile.write('v 0 0 0\n')
					# objfile.write('v 0 0 1\n')
					objfile.write('f %i/1/1 %i/2/2 %i/3/3\n' % (vi + 1, vi + 2, vi + 3))
					vcount += 3
				elif len(piece.vertices) == 2:
					print ('[INFO]', 'Emit vertices:', piece.vertices, 'offset:  %f %f %f\n' % (
						offset[0] + piece.parent_offset[0], offset[1] + piece.parent_offset[1],
						offset[2] + piece.parent_offset[2]))
					v = piece.vertices[0]
					objfile.write('v %f %f %f\n' % (
						v[0][0] + offset[0] + piece.parent_offset[0], v[0][1] + offset[1] + piece.parent_offset[1],
						v[0][2] + offset[2] + piece.parent_offset[2]))
					v = piece.vertices[1]
					objfile.write('v %f %f %f\n' % (
						v[0][0] + offset[0] + piece.parent_offset[0], v[0][1] + offset[1] + piece.parent_offset[1],
						v[0][2] + offset[2] + piece.parent_offset[2]))
					v = piece.vertices[0]
					objfile.write('v %f %f %f\n' % (
						v[0][0] + offset[0] + piece.parent_offset[0], 2 + v[0][1] + offset[1] + piece.parent_offset[1],
						v[0][2] + offset[2] + piece.parent_offset[2]))

					# objfile.write('v 0 0 0\n')
					# objfile.write('v 0 0 1\n')
					objfile.write('f %i/1/1 %i/2/2 %i/3/3\n' % (vi + 1, vi + 2, vi + 3))
					vcount += 3
				else:
					print ('[ERROR]', 'Piece', piece.name, ': failed to write as it looks invalid')
				# print 'empty piece',piece.name,'writing placeholder face with primitive type',piece.primitive_type
		vi = vi + vcount
		for child in piece.children:
			vi, nti = self.recurseS3OtoOBJ(child, objfile, '', vi, nti, groups, (
			offset[0] + piece.parent_offset[0], offset[1] + piece.parent_offset[1], offset[2] + piece.parent_offset[2]),
									optimize_for_wings3d)
		return vi, nti

	def __init__(self, data, isobj=False):
		if not isobj:
			header = _S3OHeader_struct.unpack_from(data, 0)

			magic, version, radius, height, mid_x, mid_y, mid_z, \
				root_piece_offset, collision_data_offset, tex1_offset, \
				tex2_offset = header

			assert (magic == b'Spring unit\x00')
			assert (version == 0)
			assert (collision_data_offset == 0)

			self.collision_radius = radius
			self.height = height
			self.midpoint = (mid_x, mid_y, mid_z)

			self.texture_paths = (_get_null_terminated_string(data, tex1_offset),
								  _get_null_terminated_string(data, tex2_offset))
			self.root_piece = S3OPiece(data, root_piece_offset)
		else:
			objfile = data
			self.collision_radius = 0
			self.height = 0
			self.midpoint = [0, 0, 0]
			self.texture_paths = [b"".join([b'Arm_color.dds' , b'\x00']), b"".join([b'Arm_other.dds' , b'\x00'])]
			self.root_piece = S3OPiece('', (0, 0, 0))
			self.root_piece.parent = None
			self.root_piece.name = b"".join([b'empty_root_piece' , b'\x00'])
			self.root_piece.primitive_type = 'triangles'  # triangles
			self.root_piece.parent_offset = (0, 0, 0)
			self.root_piece.vertices = []
			self.root_piece.indices = []
			self.root_piece.children = []
			i = 0
			verts = []
			normals = []
			uvs = []
			warn = 0
			piecedict = {}
			calcheight = 0
			while i < len(objfile):
				# print '.',
				if objfile[i][0] == 'o':
					piece = S3OPiece('', (0, 0, 0))
					piece.parent = self.root_piece
					piece.parent_offset = (0, 0, 0)
					piece.name = ''
					params = objfile[i].partition(' ')[2].strip().split(',')
					emittype = 10000000
					piece.parent_offset = (0, 0, 0)
					for p in params:
						if '=' in p:
							try:
								kv = p.partition('=')
								if kv[0] == 't1':
									self.texture_paths[0] = b"".join([bytes(kv[2],'utf-8') , b'\x00'])
								if kv[0] == 't2':
									self.texture_paths[1] = b"".join([bytes(kv[2],'utf-8') , b'\x00'])
								if kv[0] == 'h':
									self.height = float(kv[2])
								if kv[0] == 'r':
									self.collision_radius = float(kv[2])
								if kv[0] == 'mx':
									self.midpoint[0] = float(kv[2])
								if kv[0] == 'my':
									self.midpoint[1] = float(kv[2])
								if kv[0] == 'mz':
									self.midpoint[2] = float(kv[2])
								if kv[0] == 'ox':
									piece.parent_offset = (float(kv[2]), piece.parent_offset[1], piece.parent_offset[2])
								if kv[0] == 'oy':
									piece.parent_offset = (piece.parent_offset[0], float(kv[2]), piece.parent_offset[2])
								if kv[0] == 'oz':
									piece.parent_offset = (piece.parent_offset[0], piece.parent_offset[1], float(kv[2]))
								if kv[0] == 'e':
									emittype = int(kv[2])
								if kv[0] == 'p':
									piece.parent = b"".join([bytes(kv[2],'utf-8') , b'\x00'])
								# print '[INFO]', kv
							except ValueError:
								print ('[ERROR]', 'Failed to parse parameter', p, 'in', objfile[i])
					piece.name = b"".join([bytes(objfile[i].partition(' ')[2].strip().partition(',')[0][0:20],'utf-8') , b'\x00'])
					# why was I limiting piece names to 10 characters in length?
					print ('[INFO]', 'Piece name =', piece.name)
					piece.primitive_type = 'triangles'  # tris

					piece.children = []
					piece.indices = []
					piece.vertices = []

					i += 1
					while (i < len(objfile) and objfile[i][0] != 'o'):
						part = objfile[i].partition(' ')
						if part[0] == 'v':  # and len(verts)<emittype:
							v = list(map(float, part[2].split(' ')))
							verts.append((v[0], v[1], v[2]))
						elif part[0] == 'vn':  # and len(verts)<emittype:
							vn = list(map(float, part[2].split(' ')))
							lensqr = vn[0] ** 2 + vn[1] ** 2 + vn[2] ** 2
							if lensqr > 0.0002 and math.fabs(lensqr - 1.0) > 0.001:
								sqr = math.sqrt(lensqr)
								vn[0] /= sqr
								vn[1] /= sqr
								vn[2] /= sqr
							normals.append((vn[0], vn[1], vn[2]))
						elif part[0] == 'vt':  # and len(verts)<emittype:
							vt = list(map(float, part[2].split(' ')))
							uvs.append((vt[0], vt[1]))
						elif part[0] == 'f' and emittype == 10000000:
							# only add faces if its not an emit type primitive( meaning it should have no geometry)
							face = part[2].split(' ')
							if len(face) > 3:
								warn = 1
							for triangle in range(len(face) - 2):
								for face_index in range(triangle, triangle + 3):
									faceindexold = face_index
									if face_index == triangle:
										# trick when tesselating, It uses the first vert of the
										# face for every triangle of a polygon
										face_index = 0
									face_index = face[face_index].split('/')
									v = (0, 0, 0)
									vn = (0, 0, 0)
									vt = (0, 0)
									if face_index[0] != '':
										try:
											v = verts[int(face_index[0]) - 1]  # -1 is needed cause .obj indexes from 1
											calcheight = max(calcheight, math.ceil(v[1]))
										except IndexError:
											print ('[ERROR]', 'Indexing error! while converting piece', piece.name)
											print ('[ERROR]', objfile[i])
											print ('[ERROR]', 'wanted index:', face_index[0], 'len(verts)=', len(verts))
									if face_index[1] != '':
										vt = uvs[int(face_index[1]) - 1]
									if len(face_index) >= 3 and face_index[2] != '':
										vn = normals[int(face_index[2]) - 1]
									else:
										print ('[WARNING]', 'Face does not have specified vertex normals', objfile[i])
									if emittype != 10000000:
										if int(faceindexold) < emittype:
											v = verts[int(face_index[0]) - 1]
											piece.vertices.append((v, vn, vt))
									else:
										piece.vertices.append((v, vn, vt))
									# print len(piece.vertices),piece.vertices[-1]
									piece.indices.append(len(piece.indices))
						i += 1
					if piece.name not in piecedict:
						piecedict[piece.name] = piece
					else:
						piece.name = b"".join([bytes(piece.name.strip() + str(random.random()).encode(),'utf-8') , b'\x00'])
						piecedict[piece.name] = piece
					print ('[INFO]', 'Found piece', piece.name)
					self.root_piece.children.append(piece)
				else:
					i += 1
				if self.height == 0:
					self.height = calcheight
				if self.collision_radius == 0:
					self.collision_radius = math.ceil(calcheight / 2)
				# self.midpoint[1]=math.ceil(self.collision_radius-3)
			# if the parents are specified, we need to rebuild the hierarchy!
			# we need to rebuild post loading, because we cant be sure that the
			# external modification of the obj file retained the piece order
			# also, we must check for new pieces that are not part of anything
			newroot = self.root_piece
			hasroot = False
			for pieceindex in range(len(self.root_piece.children)):
				piece = self.root_piece.children[pieceindex]
				parentname = piece.parent
				if type(parentname) == type(b''):
					print ('[INFO]', piece.name, 'has a parent called:', parentname)
					if parentname == b'\x00':
						newroot = piecedict[piece.name]
						print ('[INFO]', 'The new root piece is', piece.name)
						hasroot = True
					elif parentname in piecedict:
						print ('[INFO]', 'Assigning', piece.name, 'to', piece.parent)
						piecedict[parentname].children.append(piece)
						piece.parent = piecedict[parentname]
					else:
						print ('[INFO]', 'Parent name', parentname, 'not in piece dict!', piecedict, 'adding it to the root piece')
						newroot.children.append(piece)
					# elif piece.parent==self.root_piece:
					# print 'piece',piece.name,'is not in the encoded hierarchy, adding it as a child of root piece:',newroot.name
					# piecedict[newroot.name].children.append(piece)
					# piece.parent=piecedict[newroot.name]
			if not hasroot:
				piecedict[newroot.name] = newroot

			for pieceindex in range(len(self.root_piece.children)):
				piece = self.root_piece.children[pieceindex]
				if piece.parent == self.root_piece and piece.parent != newroot:
					print ('[WARN]', 'Piece', piece.name, 'is not in the encoded hierarchy, adding it as a child of root piece:', newroot.name)
					piecedict[newroot.name].children.append(piece)
					piece.parent = piecedict[newroot.name]
			# now that we have the hiearchy set up right, its time to calculate offsets!

			def recurseprintpieces(p, depth = 0):
				print ('[INFO]',"-"*depth, p.name)
				for child in p.children:
					recurseprintpieces(child, depth=depth+1)
			recurseprintpieces(newroot)

			print ('[INFO]', 'The new root piece is', newroot)

			recurseprintpieces(self.root_piece)
			self.root_piece = newroot

			self.adjustobjtos3ooffsets(self.root_piece, 0, 0, 0)
   
																																					 
   

			if warn == 1:
				print ('[WARN]', 'Tne or more faces had more than 3 vertices, so triangulation was used. \
					This can produce bad results with concave polygons')

	def adjustobjtos3ooffsets(self, piece, curx, cury, curz):
		# print 'adjusting offsets of',piece.name,': current:',curx,cury,curz,'parent offsets:',piece.parent_offset
		for i in range(len(piece.vertices)):
			v = piece.vertices[i]

			v = ((v[0][0] - curx - piece.parent_offset[0], v[0][1] - cury - piece.parent_offset[1],
				  v[0][2] - curz - piece.parent_offset[2]), v[1], v[2])
			# print 'offset:',v[0],piece.vertices[0][0]
			piece.vertices[i] = v
		for child in piece.children:
			self.adjustobjtos3ooffsets(child, curx + piece.parent_offset[0], cury + piece.parent_offset[1],
									curz + piece.parent_offset[2])

	

	def serialize(self):
		#encoded_texpath1 = b"".join([bytes(self.texture_paths[0],'utf-8') , b'\x00'])
		encoded_texpath1 = self.texture_paths[0] + b'\x00'
		encoded_texpath2 = self.texture_paths[1] + b'\x00'

		tex1_offset = _S3OHeader_struct.size
		tex2_offset = tex1_offset + len(encoded_texpath1)
		root_offset = tex2_offset + len(encoded_texpath2)

		args = (b'Spring unit\x00', 0, self.collision_radius, self.height,
				self.midpoint[0], self.midpoint[1], self.midpoint[2],
				root_offset, 0, tex1_offset, tex2_offset)

		header = _S3OHeader_struct.pack(*args)

		data = header + encoded_texpath1 + encoded_texpath2
		data += self.root_piece.serialize(len(data))

		return data



class S3OPiece(object):
	# def __init__(self, data, offset, parent, i):
	# for l in data:
	# if l[0]=='o':

	def __init__(self, data, offset, parent=None, name = b"base"):
		if data != '':
			piece = _S3OPiece_struct.unpack_from(data, offset)

			name_offset, num_children, children_offset, num_vertices, \
				vertex_offset, vertex_type, primitive_type, num_indices, \
				index_offset, collision_data_offset, x_offset, y_offset, \
				z_offset = piece

			self.parent = parent
			self.name = _get_null_terminated_string(data, name_offset)
			self.primitive_type = ["triangles",
								   "triangle strips",
								   "quads"][primitive_type]
			self.parent_offset = (x_offset, y_offset, z_offset)

			self.vertices = []
			for i in range(num_vertices):
				current_offset = vertex_offset + _S3OVertex_struct.size * i
				vertex = _S3OVertex_struct.unpack_from(data, current_offset)

				position = vertex[:3]
				normal = vertex[3:6]
				texcoords = vertex[6:]

				self.vertices.append((position, normal, texcoords))

			self.indices = []
			for i in range(num_indices):
				current_offset = index_offset + _S3OIndex_struct.size * i
				index, = _S3OIndex_struct.unpack_from(data, current_offset)
				self.indices.append(index)

			self.children = []
			for i in range(num_children):
				cur_offset = children_offset + _S3OChildOffset_struct.size * i
				child_offset, = _S3OChildOffset_struct.unpack_from(data, cur_offset)
				self.children.append(S3OPiece(data, child_offset, self))
		else:
			self.parent = None
			self.name = name
			self.primitive_type = "triangles"
			self.parent_offset = (0.0,0.0,0.0)
			self.vertices = []
			self.indices = []
			self.children = []
	def recurse_bin_vertex_ao(self,allbins = {},piecelist = []):
		if piecelist == [] or self.name.lower() in piecelist:
			aobins = {}
			aovalues = []
			for i in range(0,256//4):
				aobins[i] = 0
			for i, vertex in enumerate(self.vertices):
				#vertex_ao_value = (math.floor(vertex[2][0] * 16384.0) / 16384.0 )* 255.0
				vertex_ao_value = get_vertex_ao_value_01(vertex[2][0]) * 255.0
				aovalues.append(vertex_ao_value)
				aobins[(int(vertex_ao_value)//4)%64] +=1
				#print "AO value for piece",self.name,i,vertex_ao_value
			#for i in range(0, 256/4):
				#print '%04i %04i'%(i,aobins[i])
			allbins[self.name] = aobins
			if len(self.vertices)> 4:
				meanao = sum(aovalues)/float(len(aovalues))
				print ('Piece %s has %d vertices, AO range = [%d - %d], AO mean = %d, AO spread = %d'%(
					self.name,
					len(aovalues),
					min(aovalues),
					max(aovalues),
					meanao,
					sum([abs(ao - meanao) for ao in aovalues])/len(aovalues)
				))

		for child in self.children:
			child.recurse_bin_vertex_ao(allbins = allbins,piecelist= piecelist)

		return allbins
		#if aobins[0] ==0 and aobins[256/4-1] == 0 :
		#	return True
		#else:
		#	return False
	
	def mergechildren(self):
		for child in self.children:
			child.mergechildren()
		
		newverts = self.vertices
		newindices = self.indices
		indexoffset = len(self.vertices)
		for child in self.children:
			for v in child.vertices:
				newverts.append(((v[0][0] + child.parent_offset[0],v[0][1] + child.parent_offset[1], v[0][2] + child.parent_offset[2]),v[1], v[2]))
			for index in child.indices:
				newindices.append(index + indexoffset)
			indexoffset += len(child.vertices)
			#print (self.name, child.name, indexoffset)
		self.vertices = newverts
		self.indices = newindices
		self.children = []
	
	def rescale(self, scale):
		self.parent_offset = (self.parent_offset[0] * scale, self.parent_offset[1] * scale, self.parent_offset[2] * scale)
		for i,v in enumerate(self.vertices):
			self.vertices[i] = ((v[0][0] * scale, v[0][1] * scale,v[0][2] * scale),v[1],v[2])
		for child in self.children:
			child.rescale(scale)
			
	def recurse_clear_vertex_ao(self,zerolevel=200,piecelist = []):
		if piecelist == [] or self.name.lower() in piecelist:
			for i, vertex in enumerate(self.vertices):
				vertex_ao_value = (1 / 16384.0 )* ((zerolevel + 5) / 266.0)
				newuv = (
					math.floor(vertex[2][0] * 16384.0) / 16384.0 + vertex_ao_value,
					vertex[2][1]
				)
				# print newuv, vertex
				vertex = (vertex[0], vertex[1], newuv)
				self.vertices[i] = vertex
			print ("[INFO]","Set all vertex ao terms to 200 in piece", self.name,piecelist)
		for child in self.children:
			child.recurse_clear_vertex_ao(zerolevel=zerolevel,piecelist=piecelist)

	def serialize(self, offset):
		name_offset = _S3OPiece_struct.size + offset
		#encoded_name = b"".join(bytes(self.name,'utf-8') , b'\x00')
		encoded_name = self.name + b'\x00'

		children_offset = name_offset + len(encoded_name)
		child_data = b''
		# HACK: make an empty buffer to put size in later
		for i in range(len(self.children)):
			child_data += _S3OChildOffset_struct.pack(i)

		vertex_offset = children_offset + len(child_data)
		vertex_data = b''
		for pos, nor, uv in self.vertices:
			vertex_data += _S3OVertex_struct.pack(pos[0], pos[1], pos[2],
												  nor[0], nor[1], nor[2],
												  uv[0], uv[1])

		index_offset = vertex_offset + len(vertex_data)
		index_data = b''
		for index in self.indices:
			vertex_data += _S3OIndex_struct.pack(index)

		primitive_type = {"triangles": 0,
						  "triangle strips": 1,
						  "quads": 2}[self.primitive_type]

		# Even nastier HACK: if there are no children, vertices or primitives, point one back to avoid pointing outside of file!
		if len(self.children) == 0:
			children_offset = children_offset - 1
		if len(self.vertices) == 0:
			vertex_offset = vertex_offset - 1
		if len(self.indices) == 0:
			index_offset = index_offset - 1

		args = (name_offset, len(self.children), children_offset,
				len(self.vertices), vertex_offset, 0, primitive_type,
				len(self.indices), index_offset, 0) + self.parent_offset

		piece_header = _S3OPiece_struct.pack(*args)

		child_offsets = []

		data = piece_header + encoded_name + child_data + vertex_data + index_data

		serialized_child_data = b''
		for child in self.children:
			child_offset = offset + len(data) + len(serialized_child_data)
			child_offsets.append(child_offset)
			serialized_child_data += child.serialize(child_offset)

		child_data = b''
		for child_offset in child_offsets:
			child_data += _S3OChildOffset_struct.pack(child_offset)

		data = piece_header + encoded_name + child_data + vertex_data + \
			   index_data + serialized_child_data
		return data
