from s3o import *
from PIL import Image
import os

#todo:
# find and convert texture
# create correspoding lua file
# gen normals
# estimate height, radius, center
		
_PMDHeader_struct = struct.Struct("< 4s I I I I")
_PMDVertex_struct = struct.Struct("< 3f 3f 2f")
_PMDFace_struct = struct.Struct("< 3H")

pmdscale = 20
outdir = 'pmd/output/'

class BoundingBox:
	def __init__(self):
		self.mins = [10000,10000,1000]
		self.maxs = [-10000,-10000,-1000]
		
	def add(self,v):
		for i in range(3):
			self.mins[i] = min(v[i],self.mins[i])
			self.maxs[i] = max(v[i],self.maxs[i])
	



def pmd3s3o(pmdfile, texturename = None):
	pmddata= open(pmdfile,'rb').read()
	_,basefilename = os.path.split(pmdfile)
	magic, version, data_size, numVertices, numTexCoords = _PMDHeader_struct.unpack_from(pmddata,0)
	print (magic, version, data_size, numVertices, numTexCoords)
	bb = BoundingBox()
	s3o = S3O("", isobj = True)
	root = s3o.root_piece
	
	offset = struct.calcsize(_PMDHeader_struct.format)
	vs = struct.calcsize(_PMDVertex_struct.format) + 20 + (numTexCoords-1)*8# 4u + 4f
	for vi in range(numVertices):
		vx,vy,vz, nx,ny,nz, uv1,uv2 = _PMDVertex_struct.unpack_from(pmddata, offset)
		vpos = (vx*pmdscale,vy*pmdscale,vz*pmdscale)
		bb.add(vpos)
		root.vertices.append(((vx*pmdscale,vy*pmdscale,vz*pmdscale), (nx,ny,nz), (uv1,uv2)))
		offset += vs
		
		pass
	numFaces = struct.unpack_from("< I", pmddata,offset)
	offset += 4
	for fi in range(numFaces[0]):
		a,b,c = _PMDFace_struct.unpack_from(pmddata,offset)
		offset += 6
		root.indices.append(a)
		root.indices.append(c)
		root.indices.append(b) #bad order?
		
	s3o.height = bb.maxs[1]
	s3o.collision_radius = (bb.maxs[0]+bb.maxs[2])/4
	s3o.midpoint = (0, s3o.collision_radius /2, 0)
	
	s3ofile = open(outdir+basefilename[0:-4]+'.s3o','wb')
	s3ofile.write( s3o.serialize())
	s3ofile.close()


	
	
filelist = ['bamboo_dragon_1.dae.cached.pmd']
sys.argv.append('pmd/meshes/')
if len(sys.argv)>1:
	for file in os.listdir(sys.argv[1]):
		if file.lower().endswith('.pmd'):
			filelist.append(sys.argv[1]+file)

for file in filelist:
	pmd3s3o(file)


print Image.PILLOW_VERSION

texdir = "pmd/textures/"
for file in os.listdir("pmd/textures/"):
	if file.lower().endswith('cached.tga'):
		print file
		img = Image.open(texdir+file)
		tex1 = img.copy()
		px1 = tex1.load()
		tex2 = img.copy()
		px2 = tex2.load()
		for x in range(img.size[0]):
			for y in range(img.size[1]):
				p = px1[x,y]
				px1[x,y]=(p[0],p[1],p[2],0)
				px2[x,y] = (0,5,160,p[3])
		newfbasename = file.replace('.dds','').replace('.cached','').replace('.tga','')
		tex1.save(outdir+newfbasename+'_1.tga', format  = 'TGA')
		tex2.save(outdir+newfbasename+'_2.tga', format  = 'TGA')
		
		