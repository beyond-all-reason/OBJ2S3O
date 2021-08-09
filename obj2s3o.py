#!/usr/bin/env python

from s3o import *
import vertex_cache
import sys
from Tkinter import *
import tkFileDialog
import math
import os
import png

from tooltip import Tooltip

howtoemit=('''const unsigned int count = piece->GetVertexCount();

	if (count == 0) {
		pos = mat.GetPos();
		dir = mat.Mul(float3(0.0f, 0.0f, 1.0f)) - pos;
	} else if (count == 1) {
		pos = mat.GetPos();
		dir = mat.Mul(piece->GetVertexPos(0)) - pos;
	} else if (count >= 2) {
		float3 p1 = mat.Mul(piece->GetVertexPos(0));
		float3 p2 = mat.Mul(piece->GetVertexPos(1));

		pos = p1;
		dir = p2 - p1;
	} else {
		return false;
	}\	//! we use a 'right' vector, and the positive x axis points to the left
	pos.x = -pos.x;
	dir.x = -dir.x;

	return true;
''')


def sizeof_fmt(num):
    for x in ['bytes', 'KB', 'MB', 'GB']:
        if abs(num) < 1024.0:
            return "%3.1f %s" % (num, x)
        num /= 1024.0
    return "%3.1f%s" % (num, 'TB')



class App:
	def __init__(self, master):
		self.initialdir=os.getcwd()
		master.title('OBJ <--> S3O - By Beherith - Thanks to Muon\'s wonderful s3o library!')
		frame = Frame(master)
		objtos3oframe = Frame(master, bd=1, relief = SUNKEN)
		s3otoobjframe = Frame(master, bd=3, relief = SUNKEN)
		opts3oframe   = Frame(master, bd=3, relief = SUNKEN)

		clearaoframe   = Frame(master, bd=3, relief = SUNKEN)
		printaoframe   = Frame(master, bd=3, relief = SUNKEN)
		generateaoframe   = Frame(master, bd=3, relief = SUNKEN)
		xnormalframe = Frame(generateaoframe, bd=3, relief = SUNKEN)
		aooptsframe = Frame(generateaoframe, bd=3, relief = SUNKEN)
		aooptsframe2 = Frame(generateaoframe, bd=3, relief = SUNKEN)
		aoplateframe = Frame(generateaoframe, bd = 3, relief = SUNKEN)
		swaptexframe   = Frame(master, bd=3, relief = SUNKEN)

		frame.pack()
		objtos3oframe.pack(side=TOP,fill=X)
		s3otoobjframe.pack(side=TOP,fill=X)
		opts3oframe.pack(side=TOP,fill=X)
		clearaoframe.pack(side=TOP,fill=X)
		printaoframe.pack(side=TOP,fill=X)
		generateaoframe.pack(side=TOP,fill=X)
		xnormalframe.pack(side=TOP,fill=X)
		aooptsframe.pack(side=TOP,fill=X)
		aooptsframe2.pack(side=TOP,fill=X)
		swaptexframe.pack(side=TOP,fill=X)
		quitbutton = Button(frame, text="QUIT", fg="red", command=frame.quit)
		quitbutton.pack(side=TOP)
		Tooltip(quitbutton, text = "Quit the application", wraplength=200)

		self.prompts3ofilename=IntVar()
		optimizes3obutton = Button(opts3oframe , text='Optimize s3o', command=self.optimizes3o)
		optimizes3obutton.pack(side=LEFT)
		Tooltip(optimizes3obutton,text = "Select an S3O file, and clean up redundant vertices, perform vertex cache optimization and fix null normals. Modifies S3O file in place.", wraplength= 400)

		Label(opts3oframe,text='Removes redundant vertices and performs vertex cache optimization').pack(side=LEFT)

		#-----AO stuff----
		clearaos3obutton = Button(clearaoframe , text='Clear AO s3o', command=self.clearaos3o)
		clearaos3obutton.pack(side=LEFT)
		Tooltip(clearaos3obutton,
				text="If no list is specified, resets all vertex ambient ambient occlusion data in-place. If list is specified, then AO data will only removed from those pieces. Use this to clear AO from spinning or fully occluded pieces.",
				wraplength=400)

		Label(clearaoframe, text='Reset all AO data, or list pieces to remove from:').pack(side=LEFT)

		self.clearaopiecelist = StringVar()
		clearaopiecelistentry = Entry(clearaoframe,width=32,textvariable=self.clearaopiecelist)
		clearaopiecelistentry.pack(side=LEFT)
		Tooltip(clearaopiecelistentry,text="A comma separated list of pieces to remove AO data from.", wraplength=200)

		self.ao_zerolevel = StringVar()
		self.ao_zerolevel.set("200")
		Label(clearaoframe, text='Set to:').pack(side=LEFT)
		aozerolevelentry = Entry(clearaoframe,width=4,textvariable=self.ao_zerolevel)
		aozerolevelentry.pack(side=LEFT)
		Tooltip(aozerolevelentry,text="What AO value to reset to, where 0 is fully black, 255 is fully white and ~200 is default.")

		printaos3obutton = Button(printaoframe , text='Print AO information', command=self.printaos3o)
		printaos3obutton.pack(side=LEFT)
		Tooltip(printaos3obutton, text = "Prints information about the AO data preset in a model to console. For developers and debugging.")

		self.xnormalpath = StringVar()
		self.xnormalpath.set("C:\\Program Files\\xNormal\\3.19.3\\x64\\xNormal.exe")
		Label(xnormalframe, text='Path to xNormal:').pack(side=LEFT)
		xnormalpathentry = Entry(xnormalframe,width=84,textvariable=self.xnormalpath)
		xnormalpathentry.pack(side=LEFT)
		Tooltip(xnormalpathentry,text="Set your path to xNormal.exe here. Sorry this is not remembered on relaunch. Its easier if you just install xnormal to the default path.")

		Label(aooptsframe, text='Groundplate:').pack(side=LEFT)
		self.aotype_building = IntVar()
		aotypebuildingcheckbutton = Checkbutton(aooptsframe, text='Building (big)', variable=self.aotype_building)
		aotypebuildingcheckbutton.pack(side=LEFT)
		Tooltip(aotypebuildingcheckbutton,text = "Enable this when baking AO for buildings. This puts a larger than normal groundplate underneath the unit, to make sure the building is only lit from the top hemisphere.")

		self.aotype_flying = IntVar()
		aotypeflyingcheckbutton = Checkbutton(aooptsframe, text='Flying (none)', variable=self.aotype_flying)
		aotypeflyingcheckbutton.pack(side=LEFT)
		Tooltip(aotypeflyingcheckbutton,text="Use for aircraft, this remove the groundplate from under the unit, so it can get lit from all directions.")

		self.ao_explode = IntVar()
		aoexplodecheckbutton = Checkbutton(aooptsframe, text='Explode all piecewise', variable=self.ao_explode)
		aoexplodecheckbutton.pack(side=LEFT)
		Tooltip(aoexplodecheckbutton,text="Move ALL pieces in a model far away from each other, so they dont occlude each other. Use this for cars with wheels, or for models that open and unfold.")

		self.ao_minclamp = StringVar()
		self.ao_minclamp.set("0")
		Label(aooptsframe, text='Clamp:').pack(side=LEFT)
		aominclampentry = Entry(aooptsframe, width=4, textvariable=self.ao_minclamp)
		aominclampentry.pack(side=LEFT)
		Tooltip(aominclampentry,text="The darkest possible level AO shading will go to. 0 means even the darkes is allowed, 255 means that everything will be full white. 128 is good if you dont want peices to go too dark")

		self.ao_bias = StringVar()
		self.ao_bias.set("0.0")
		Label(aooptsframe, text='Bias:').pack(side=LEFT)
		aobiasentry = Entry(aooptsframe, width=4, textvariable=self.ao_bias)
		aobiasentry.pack(side=LEFT)
		Tooltip(aobiasentry,text="Add this much to every vertex AO value, positive values brighten, negative values darken. Sane range [-255;255] ")

		self.ao_gain = StringVar()
		self.ao_gain.set("1.0")
		Label(aooptsframe, text='Gain:').pack(side=LEFT)
		aogainentry = Entry(aooptsframe, width=4, textvariable=self.ao_gain)
		aogainentry.pack(side=LEFT)
		Tooltip(aogainentry, text= "Multiply calculated AO terms with this value. A value of 2.0 would double the brightness of each value, 0.5 would half it. AO_out = min(255, max(clamp, AO_in * bias + gain)) ")

		self.ao_explodepieceslist = StringVar()
		Label(aooptsframe2, text='List of pieces to explode').pack(side=LEFT)
		aoexplodepieceslistentry = Entry(aooptsframe2, width=76, textvariable=self.ao_explodepieceslist)
		aoexplodepieceslistentry.pack(side=LEFT)
		Tooltip(aoexplodepieceslistentry,text="Comma separated list of pieces that should not be occluded by other pieces")

		getpiecelistbutton = Button(aooptsframe2 , text='Get', command=self.getpiecelist)
		getpiecelistbutton.pack(side=LEFT)
		Tooltip(getpiecelistbutton,text = "Load the list of pieces from an S3O model")

		bakeaobutton = Button(generateaoframe , text='Bake Vertex AO with above parameters for (multiple) units', command=self.bakeao)
		bakeaobutton.pack(side=TOP)
		Tooltip(bakeaobutton,text="Load (multiple) S3O files, and perform the AO baking, modifying the S3O files in-place")

		# ---- AO plate stuff
		bakeaoplatebutton = Button(aoplateframe , text='Bake AO plane for building(s)', command=self.bakeaoplate)
		bakeaoplatebutton.pack(side=LEFT)
		Tooltip(bakeaoplatebutton,text="Load (multiple) S3O files, and perform the AO baking, and output the mymodel_aoplane.dds files")

		self.aoplate_xsize = StringVar()
		self.aoplate_xsize.set("5")
		Label(aoplateframe, text='Size X:').pack(side=LEFT)
		aoplatexentry = Entry(aoplateframe, width=4, textvariable=self.aoplate_xsize)
		aoplatexentry.pack(side=LEFT)
		Tooltip(aoplatexentry,text="X Size of the footprint of the building (unitdef: buildinggrounddecalsizex)")

		self.aoplate_zsize = StringVar()
		self.aoplate_zsize.set("5")
		Label(aoplateframe, text='Size Z:').pack(side=LEFT)
		aoplatezentry = Entry(aoplateframe, width=4, textvariable=self.aoplate_zsize)
		aoplatezentry.pack(side=LEFT)
		Tooltip(aoplatezentry,text="Z Size of the footprint of the building (unitdef: buildinggrounddecalsizeZ)")

		self.aoplate_rez = StringVar()
		self.aoplate_rez.set("128")
		Label(aoplateframe, text='Resolution:').pack(side=LEFT)
		aoplateresentry = Entry(aoplateframe, width=4, textvariable=self.aoplate_rez)
		aoplateresentry.pack(side=LEFT)
		Tooltip(aoplateresentry,text="Resolution of the generated image, default 128, use power-of-two values")

		aoplateframe.pack(side = TOP, fill =X)
		#--- end AO stuff

		
		swaptexbutton = Button(swaptexframe , text='Override texture', command=self.swaptex)
		swaptexbutton.pack(side=LEFT)
		Tooltip(swaptexbutton,text="Change only the textures of (multiple) S3O files, to the ones specified in Tex1 and Tex2")

		Label(swaptexframe,text='Tex1:').pack(side=LEFT)
		self.tex1=StringVar()
		Entry(swaptexframe,width=20,textvariable=self.tex1).pack(side=LEFT)
		Label(swaptexframe,text='Tex2:').pack(side=LEFT)
		self.tex2=StringVar()
		Entry(swaptexframe,width=20,textvariable=self.tex2).pack(side=LEFT)
		
		openobjbutton = Button(objtos3oframe , text='Convert OBJ to S3O', command=self.openobj)
		openobjbutton.pack(side=LEFT)
		Tooltip(openobjbutton, text = "Choose any mymodel.OBJ file(s), and convert them into mymodel.S3O. Each object in an OBJ file will be a separate piece in the S3O file. If the OBJ file was created by this tool, and the object names were left intact, you will retain all piece hiearchy and origins information.")

		prompts3ofilenamecheckbutton = Checkbutton(objtos3oframe,text='Prompt output filename', variable=self.prompts3ofilename)
		prompts3ofilenamecheckbutton.pack(side=LEFT)
		Tooltip(prompts3ofilenamecheckbutton,text = "Allows you to choose what name you want to save your S3O file as.")
		
		opens3obutton = Button(s3otoobjframe , text='Convert S3O to OBJ', command=self.opens3o)
		opens3obutton.pack(side=LEFT)
		Tooltip(opens3obutton,text="Convert mymodel.S3O into and editable mymodel.OBJ, while keeping all S3O information in the object names.")

		self.optimize_for_wings3d=IntVar()
		self.optimize_for_wings3d.set(1)
		optimizeforwingscheckbutton = Checkbutton(s3otoobjframe,text='Optimize for Wings3d', variable=self.optimize_for_wings3d)
		optimizeforwingscheckbutton.pack(side=LEFT)
		Tooltip(optimizeforwingscheckbutton,text="This should be ON, and it specifies hard/soft edges via .obj smoothing group operators, and ensures mesh continuity across triangles")

		self.promptobjfilename=IntVar()
		promptobjfilenamecheckbutton = Checkbutton(s3otoobjframe,text='Prompt output filename', variable=self.promptobjfilename)
		promptobjfilenamecheckbutton.pack(side=LEFT)
		Tooltip(promptobjfilenamecheckbutton,text = "Allows you to choose what name you want to save your OBJ file as.")
		
		self.transform=IntVar()
		transformcheckbutton = Checkbutton(objtos3oframe,text='Transform UV coords:', variable=self.transform)
		transformcheckbutton.pack(side=LEFT)
		Tooltip(transformcheckbutton, text = "Perform a linear transformation of the UV space of a model when converting S3O to OBJ")

		Label(objtos3oframe,text='U=').pack(side=LEFT)
		self.transformA=StringVar()
		transformaentry = Entry(objtos3oframe,width=4,textvariable=self.transformA)
		transformaentry.pack(side=LEFT)
		Tooltip(transformaentry, text="How much to multiply all U (horizontal) coordinates with")
		self.transformA.set('1')
		
		Label(objtos3oframe,text='* U +').pack(side=LEFT)
		self.transformB=StringVar()
		transformbentry = Entry(objtos3oframe,width=4,textvariable=self.transformB)
		transformbentry.pack(side=LEFT)
		Tooltip(transformbentry, text="How much to add to all U (horizontal) coordinates")
		self.transformB.set('0')
		
		Label(objtos3oframe,text='    V=').pack(side=LEFT)
		self.transformC=StringVar()
		transformcentry = Entry(objtos3oframe,width=4,textvariable=self.transformC)
		transformcentry.pack(side=LEFT)
		Tooltip(transformcentry, text="How much to multiply all V (vertical) coordinates with")
		self.transformC.set('1')
		
		Label(objtos3oframe,text='* V +').pack(side=LEFT)
		self.transformD=StringVar()
		transformdentry = Entry(objtos3oframe,width=4,textvariable=self.transformD)
		transformdentry.pack(side=LEFT)
		Tooltip(transformdentry, text="How much to add to all U (horizontal) coordinates")
		self.transformD.set('0')


		Label(frame,wraplength=600, justify=LEFT, text ='Instructions and notes:\n1. Converting S3O to OBJ:\n Open an s3o file, and the obj file will be saved with the same name and an .obj extension\n The name of each object in the .obj file will reflect the naming and pieces of the s3o file. All s3o data is retained, and is listed as a series of parameters in the object\'s name.\nExample:\no base,ox=-0.00,oy=0.00,oz=0.00,p=,mx=-0.00,my=4.00,mz=0.00,r=17.50,h=21.00,t1=tex1.png,t2=tex2.png\n ALL s3o info is retained, including piece hierarchy, piece origins, smoothing groups, vertex normals, and even degenerate pieces with no geometry used as emit points and vectors. These emit pieces will be shown as triangles with their correct vertex ordering.\n2. Converting OBJ to S3O:\n The opened .obj file will be converted into s3o. If the piece names contain the information as specified in the above example, the entire model hierarchy will be correctly converted. If it doesnt, then the program will convert each object as a child piece of an empty base object.').pack(side=BOTTOM)

	def openobj(self):
		self.objfile = tkFileDialog.askopenfilename(initialdir= self.initialdir, filetypes = [('Object file','*.obj'),('Any file','*')],multiple = True)
		self.objfile = string2list(self.objfile) 
		for file in self.objfile:
			if 'obj' in file.lower():
				self.initialdir=file.rpartition('/')[0]
				if self.prompts3ofilename.get()==1:
					outputfilename=tkFileDialog.asksaveasfilename(initialdir= self.initialdir,filetypes = [('Spring Model file (S3O)','*.s3o'),('Any file','*')])
					if '.s3o' not in outputfilename.lower():
						outputfilename+='.s3o'
				else:
					outputfilename=file.lower().replace('.obj','.s3o')
				transform=self.transform.get()
				a=b=c=d=0
				if transform==1:
					try:
						a=float(self.transformA.get())
						b=float(self.transformB.get())
						c=float(self.transformC.get())
						d=float(self.transformD.get())
						print '[INFO]','Using an UV space transform U=%.3f * U + %.3f  V=%.3f * V + %.3f'%(a,b,c,d)
					except ValueError:
						print '[WARN]','Failed to parse transformation parameters, ignoring transformation!'
						transform=0
				OBJtoS3O(file, transform,outputfilename,a,b,c,d)

	def opens3o(self):
		self.s3ofile = tkFileDialog.askopenfilename(initialdir= self.initialdir,filetypes = [('Spring Model file (S3O)','*.s3o'),('Any file','*')], multiple = True)
		self.s3ofile = string2list(self.s3ofile) 
		for file in self.s3ofile:
			if 's3o' in file.lower():
				self.initialdir=file.rpartition('/')[0]
				if self.promptobjfilename.get()==1:
					outputfilename=tkFileDialog.asksaveasfilename(initialdir= self.initialdir,filetypes = [('Object file','*.obj'),('Any file','*')])
					if '.obj' not in outputfilename.lower():
						outputfilename+='.obj'
				else:
					outputfilename=file.lower().replace('.s3o','.obj')
				S3OtoOBJ(file,outputfilename,self.optimize_for_wings3d.get()==1)

	def optimizes3o(self):
		self.s3ofile = tkFileDialog.askopenfilename(initialdir= self.initialdir,filetypes = [('Spring Model file (S3O)','*.s3o'),('Any file','*')], multiple = True)
		self.s3ofile = string2list(self.s3ofile) 
		for file in self.s3ofile:
			if 's3o' in file.lower():
				self.initialdir=file.rpartition('/')[0]
				optimizeS3O(file)

	def clearaos3o(self):
		self.s3ofile = tkFileDialog.askopenfilename(initialdir= self.initialdir,filetypes = [('Spring Model file (S3O)','*.s3o'),('Any file','*')], multiple = True)
		self.s3ofile = string2list(self.s3ofile)
		piecelist = self.clearaopiecelist.get()
		piecelist = piecelist.strip().lower().split(',')
		if piecelist == ['']:
			piecelist = []
		ao_zerolevel = float(self.ao_zerolevel.get())
		for file in self.s3ofile:
			if 's3o' in file.lower():
				print '[INFO]','Clearing AO for',file,'piecelist:',piecelist
				clearAOS3O(file, piecelist=piecelist, zerolevel=ao_zerolevel)

	def printaos3o(self):
		self.s3ofile = tkFileDialog.askopenfilename(initialdir= self.initialdir,filetypes = [('Spring Model file (S3O)','*.s3o'),('Any file','*')], multiple = True)
		self.s3ofile = string2list(self.s3ofile)
		piecelist = self.clearaopiecelist.get()
		piecelist = piecelist.strip().lower().split(',')
		ao_zerolevel = float(self.ao_zerolevel.get())
		for file in self.s3ofile:
			if 's3o' in file.lower():
				self.initialdir=file.rpartition('/')[0]
				print '[INFO]','Clearing AO for',file,'piecelist:',piecelist
				printAOS3O(file)

	def swaptex(self):
		self.s3ofile = tkFileDialog.askopenfilename(initialdir= self.initialdir,filetypes = [('Spring Model file (S3O)','*.s3o'),('Any file','*')], multiple = True)
		self.s3ofile = string2list(self.s3ofile) 
		for file in self.s3ofile:
			if 's3o' in file.lower():
				self.initialdir=file.rpartition('/')[0]
				swaptex(file,self.tex1.get(),self.tex2.get())

	def getpiecelist(self):
		self.s3ofile = tkFileDialog.askopenfilename(initialdir= self.initialdir,filetypes = [('Spring Model file (S3O)','*.s3o'),('Any file','*')], multiple = True)
		self.s3ofile = string2list(self.s3ofile)
		datafile = open(
		self.s3ofile[0], 'rb')
		data = datafile.read()
		datafile.close()
		model = S3O(data)
		def recurse_piecenames(piece):
			r = [piece.name]
			for child in piece.children:
				r += recurse_piecenames(child)
			return r
		piecenamelist = recurse_piecenames(model.root_piece)
		self.ao_explodepieceslist.set(','.join(piecenamelist))

	def bakeao(self):
		self.s3ofile = tkFileDialog.askopenfilename(initialdir=self.initialdir,
													filetypes=[('Spring Model file (S3O)', '*.s3o'), ('Any file', '*')],
													multiple=True)
		self.s3ofile = string2list(self.s3ofile)
		explodepiecelist = self.ao_explodepieceslist.get().strip().lower().split(',')
		if explodepiecelist == ['']:
			explodepiecelist = []
		for file in self.s3ofile:
			if 's3o' in file.lower():
				bakeAOS3O(file,
						  self.xnormalpath.get(),
						  isbuilding=bool(self.aotype_building.get()),
						  isflying=bool(self.aotype_flying.get()),
						  explode=bool(self.ao_explode.get()),
						  minclamp=float(self.ao_minclamp.get()),
						  bias = float(self.ao_bias.get()),
						  gain = float(self.ao_gain.get()),
						  explodepieces=explodepiecelist)

	def bakeaoplate(self):
		self.s3ofile = tkFileDialog.askopenfilename(initialdir=self.initialdir,
													filetypes=[('Spring Model file (S3O)', '*.s3o'),
															   ('Any file', '*')],
													multiple=True)
		self.s3ofile = string2list(self.s3ofile)
		for file in self.s3ofile:
			if 's3o' in file.lower():
				bakeAOPlateS3O(file,
						  self.xnormalpath.get(),
						  sizex=int(self.aoplate_xsize.get()),
						  sizez=int(self.aoplate_zsize.get()),
						  resolution=int(self.aoplate_rez.get()))

def string2list(input_string):
	if '{' not in input_string:# and input_string.count(':')>1:
		return input_string
	input_string = input_string.lstrip('{')
	input_string = input_string.rstrip('}')
	output = input_string.split('} {')
	return output

def S3OtoOBJ(filename,outputfilename,optimize_for_wings3d=True):
	if '.s3o' in filename.lower():
		data=open(filename,'rb').read()
		model=S3O(data)
		model.S3OtoOBJ(outputfilename,optimize_for_wings3d)
		print '[INFO]',"Succesfully converted", filename,'to',outputfilename

def OBJtoS3O(objfile,transform,outputfilename,a,b,c,d):
	if '.obj' in objfile.lower():
		data = open(objfile).readlines()
		if transform==1:
			for line in range(len(data)):
				if data[line][0:2]=='vt':
					s=data[line].split(' ')
					data[line]=' '.join([s[0],str(float(s[1])*a+b),str(float(s[2])*c+d)])
		isobj=True
		model = S3O(data,isobj)
		recursively_optimize_pieces(model.root_piece)
		optimized_data = model.serialize()
		output_file=open(outputfilename,'wb')
		output_file.write(optimized_data)
		output_file.close()
	#	if (self.tex1.get()!='' and self.tex2.get()!=''):
	#		swaptex(outputfilename, self.tex1.get(),self.tex2.get())
		print '[INFO]',"Succesfully converted", objfile,'to',outputfilename
		
def swaptex(filename,tex1,tex2):
	datafile=open(filename,'rb')
	data=datafile.read()
	model=S3O(data)
	model.texture_paths=[tex1,tex2]
	datafile.close()
	print '[INFO]','Changed texture to',tex1,tex2
	output_file=open(filename,'wb')
	output_file.write(model.serialize())
	output_file.close()
	print '[INFO]',"Succesfully optimized", filename

def optimizeS3O(filename):
	datafile=open(filename,'rb')
	data=datafile.read()
	model=S3O(data)
	pre_vertex_count=countvertices(model.root_piece)
	recursively_optimize_pieces(model.root_piece)
	optimized_data = model.serialize()
	datafile.close()
	print '[INFO]','Number of vertices before optimization:',pre_vertex_count,' after optimization:',countvertices(model.root_piece)
	output_file=open(filename,'wb')
	output_file.write(optimized_data)
	output_file.close()
	#allbins = model.root_piece.recurse_bin_vertex_ao()
	#print 'bin\t' + '\t'.join(sorted(allbins.keys()))

	#for i in range(0, 256 / 4):
	#	print '%i\t' % i + '\t'.join(['%04d' % allbins[k][i] for k in sorted(allbins.keys())])

	print '[INFO]',"Succesfully optimized", filename

def printAOS3O(filename):
	datafile = open(filename, 'rb')
	data = datafile.read()
	datafile.close()
	model = S3O(data)
	print '[INFO]', 'AO data in:',filename
	model.root_piece.recurse_bin_vertex_ao()
	print '[INFO]', "Printing done for", filename

def clearAOS3O(filename,piecelist = [], zerolevel = 200):
	datafile = open(filename, 'rb')
	data = datafile.read()
	model = S3O(data)
	pre_vertex_count = countvertices(model.root_piece)
	print '[INFO]', 'AO data before clearing:'
	model.root_piece.recurse_bin_vertex_ao()
	model.root_piece.recurse_clear_vertex_ao(piecelist = piecelist,zerolevel=zerolevel)
	print '[INFO]', 'AO data after clearing:'
	model.root_piece.recurse_bin_vertex_ao(piecelist = piecelist)
	recursively_optimize_pieces(model.root_piece)
	optimized_data = model.serialize()
	datafile.close()
	print '[INFO]', 'Number of vertices before optimization:', pre_vertex_count, ' after optimization:', countvertices(
		model.root_piece)
	output_file = open(filename, 'wb')
	output_file.write(optimized_data)
	output_file.close()
	print '[INFO]', "Succesfully optimized", filename

def delimit(str,a,b):
	return str.partition(a)[2].partition(b)[0]

def bakeAOPlateS3O(filepath, xnormalpath, sizex = 5, sizez = 5, resolution= 128):
	basename = filepath.rpartition('.')[0]
	print '=========================working on', basename, '==============================='
	# check if the unit has a unitdef and if that unit is not a flying unit.
	# also, make bigger plates for buildings :)

	mys3o = S3O(open(filepath, 'rb').read())
	objfile = basename + '_AOplate.obj'
	pngfile = basename + "_ao.png"
	pngfilexnormal = basename + "_ao_occlusion.png"
	mys3o.S3OtoOBJ(objfile, optimize_for_wings3d=False)

	objfilehandle = open(objfile)
	objlines = objfilehandle.readlines()
	objfilehandle.close()
	vertex_cnt = 0
	vnormal_cnt = 0
	uv_cnt = 0

	for i,line in enumerate(objlines):
		if line[0:2] == 'v ':
			vertex_cnt += 1
		if line[0:3] == 'vn ':
			vnormal_cnt += 1
		if line[0:3] == 'vt ':
			objlines[i] = 'vt 0 0\n'
			uv_cnt += 1

	objlines.append('v ' + str(sizex*8) + ' 0 ' + str(sizez*8) + '\n')
	objlines.append('v ' + str(sizex*8) + ' 0 ' + str(-sizez*8) + '\n')
	objlines.append('v ' + str(-sizex*8) + ' 0 ' + str(-sizez*8) + '\n')
	objlines.append('v ' + str(-sizex*8) + ' 0 ' + str(sizez*8) + '\n')
	objlines.append('vt 1 0\n')
	objlines.append('vt 1 1\n')
	objlines.append('vt 0 1\n')
	objlines.append('vt 0 0\n')
	for i in range(4):
		objlines.append('vn 0 1 0\n')

	aoplaneface = 'f'
	for i in range(1,5):
		aoplaneface+= ' %d/%d/%d'%(vertex_cnt + i, uv_cnt+i, vnormal_cnt+i)
	objlines.append(aoplaneface+'\n')

	objfilehandle = open(objfile, 'w')
	objfilehandle.write(''.join(objlines))
	objfilehandle.close()

	# --------------------- edit xnormal settings XML file------------------------
	xml = open('aoplane.xml', 'r')
	xmlln = xml.readlines()
	xmlfilename = 'xnormalsettings.xml'
	xmlout = open(xmlfilename, 'w')
	for l in xmlln:
		if 'S:\\models\\!AO\\corfus+plane2.obj' in l:
			l = l.replace('S:\\models\\!AO\\corfus+plane2.obj', objfile.replace('/','\\'))
			print 'found .obj in xml', l
		if 'S:\\models\\!AO\\corfusplaneuniform.bmp' in l:
			l = l.replace('S:\\models\\!AO\\corfusplaneuniform.bmp', pngfile.replace('/','\\'))
			print 'found .bmp in xml' , l

		if 'Width=\"128\" Height=\"128\"' in l:
			l = l.replace('Width=\"128\" Height=\"128\"', 'Width=\"' + str(resolution) + '\" Height=\"' + str(resolution) + '\"')
			print l
		xmlout.write(l)
	xmlout.close()

	# --------------------- run xnormal------------------------
	print "Deleting old bmp file", pngfilexnormal
	os.system('del "' + pngfilexnormal.replace('/','\\')+'"')
	xnormalcmd = '""%s" %s"'%(
		xnormalpath,
		xmlfilename,
	)
	print "[INFO]",'xNormal command is:',xnormalcmd
	os.system(xnormalcmd)
	#		#--------------------- Adjust color balance of AO bake ------------------------------------------
	r = png.Reader(pngfilexnormal)
	w,h,rows,info= r.read()
	rows = list(rows)
	# list of rows
	# each row is 4x width size, for RGBA pixels. A subpixel is always 255
	print pngfilexnormal,info

	tl=rows[0][0]
	bl=rows[-1][0]
	tr=rows[0][-4]
	br=rows[-1][-4]
	print tl,bl,tr,br
	modifier= min(tl,bl,tr,br)-3
	modifier=255 -int(modifier)
	maxdarkness =32

	outpngfilehandle = open(basename+'_aoplane.png','wb')
	outpngwriter = png.Writer(width=w, height=h, alpha=True,greyscale=False)
	outrows = []

	#also clamp N pixels on each side, where N is resolution/(size*2)


	for x in range(w):
		outrows.append([])
		for y in range(h):
			p = ( rows[x][4*y ] + rows[x][4*y +1] +rows[x][4*y +1] )/3
			p=min(p+modifier,255)
			p=p-(255-p)/8 #some darkenening? hell if i remember

			p=int(max(maxdarkness,p))
			p = 255-p # invert

			# we want 0 alpha near edges

			xthres = w/float(sizex*2)
			ythres = h/float(sizez*2)
			if (x < xthres):
				p = min(p, p*x/xthres)
			if (x > w-xthres):
				p = min(p, p*(w-x)/xthres)
			if (y < ythres):
				p = min(p, p*y/ythres)
			if (y > h-ythres):
				p = min(p, p*(h-y)/ythres)
			#print (p)
			p= int(p)
			outrows[x] += [0,0,0,p]
	outpngwriter.write(outpngfilehandle,outrows)
	outpngfilehandle.close()


	#--------------------- compress to dds------------------------
	cmd='nvdxt -flip -dxt5 -quality_highest -file "%s"'%(basename+"_aoplane.png")
	print cmd
	os.system(cmd)

	print '[INFO]',"Ding, fries are done!"

def bakeAOS3O(filepath, xnormalpath, isbuilding = False, isflying = False, explode = False, minclamp = 0.0, bias = 0.0, gain = 1.0,explodepieces = []):
	basename = filepath.rpartition('.')[0]
	print '=========================working on', basename, '==============================='
	# check if the unit has a unitdef and if that unit is not a flying unit.
	# also, make bigger plates for buildings :)

	mys3o = S3O(open(filepath, 'rb').read())
	objfile = basename + '_AO.obj'
	mys3o.S3OtoOBJ(objfile, optimize_for_wings3d=False)
	print basename, 'flying:', isflying, 'building:', isbuilding
	if not isflying:
		objfilehandle = open(objfile)
		objlines = objfilehandle.readlines()
		objfilehandle.close()
		vertex_cnt = 0
		vnormal_cnt = 0
		uv_cnt = 0
		boundingbox = [0, 0, 0, 0, 0, 0]  # xmin, xmax, ymin, ymax, zmin, zmax

		def bind(coords, boundingbox):
			for axis in range(3):
				boundingbox[2 * axis] = min(boundingbox[2 * axis], coords[axis])
				boundingbox[2 * axis + 1] = max(boundingbox[2 * axis + 1], coords[axis])
			return boundingbox

		for line in objlines:
			if line[0:2] == 'v ':
				boundingbox = bind([float(f) for f in line[2:].strip().split(' ')], boundingbox)
				vertex_cnt += 1
			if line[0:3] == 'vn ':
				vnormal_cnt += 1
			if line[0:3] == 'vt ':
				uv_cnt += 1
		for axis in range(3):  # expand the bounding box by 1 in each direction.
			xz_expand = 1
			if isbuilding and axis != 1:  # dont expand y axis
				xz_expand = 12
			boundingbox[2 * axis] = boundingbox[2 * axis] - xz_expand
			boundingbox[2 * axis + 1] = boundingbox[2 * axis + 1] + xz_expand
		for vertex in ([(boundingbox[0], boundingbox[2], boundingbox[4]),
						(boundingbox[0], boundingbox[2], boundingbox[5]),
						(boundingbox[1], boundingbox[2], boundingbox[5]),
						(boundingbox[1], boundingbox[2], boundingbox[4])]):
			objlines.append('v %f %f %f\n' % vertex)
		for i in range(4):
			objlines.append('vn %f %f %f\n' % (0, 1, 0))
			objlines.append('vt %f %f\n' % (0, 0))
		objlines.append(
			'f ' + ' '.join(['%i/%i/%i' % (vertex_cnt + i, uv_cnt + i, vnormal_cnt + i) for i in [1, 2, 3]]) + '\n')
		objlines.append(
			'f ' + ' '.join(['%i/%i/%i' % (vertex_cnt + i, uv_cnt + i, vnormal_cnt + i) for i in [3, 4, 1]]) + '\n')
		objfilehandle = open(objfile, 'w')
		objfilehandle.write(''.join(objlines))
		objfilehandle.close()
	if explode:
		print 'Separating', basename, 'into pieces for AO bake to avoid excessive darkening on hidden pieces'
		objfilehandle = open(objfile)
		objlines = objfilehandle.readlines()
		objfilehandle.close()
		piececount = -1
		for line_index in range(len(objlines)):
			oldline = objlines[line_index]
			if 'v ' == oldline[0:2]:
				oldline = oldline.split(' ')  # we are only gonna replace the Y coords with origY+piececount*100
				objlines[line_index] = 'v %s %f %s' % (
				oldline[1], float(oldline[2]) + 100.0 * piececount, oldline[3])
			if 'o ' == oldline[0:2]:
				piececount += 1

		objfilehandle = open(objfile, 'w')
		objfilehandle.write(''.join(objlines))
		objfilehandle.close()


	# DO THE XNORMAL:
	xnormalcmd = '""%s" -aogpu "%s" 0 1.0 pv "%s" 512 512 2048 0.008 0.0 1.0 1.0 1.0 0 2 cpu true 172.0 0.0 0.0 0.0"'%(
		xnormalpath,
		objfile,
		basename+'.ovb'
	)
	print "[INFO]",'xNormal command is:',xnormalcmd
	os.system(xnormalcmd)

	aovalues = {}

	def parse_ovb_triplet(line):
		line = line.strip().replace('\"', '').strip('<>/').split(' ')
		vertex = []
		for coord in line[1:]:
			vertex.append(float(coord.partition('=')[2]))
		return vertex


	print  'Working on:', filepath
	vertdata = []
	aodata = []
	ovbfile = open(basename+'.ovb').readlines()
	aobins = [0 for i in range(256)]
	vcount = 0

	for line in ovbfile:
		if '<VPos' in line:
			vertdata.append(parse_ovb_triplet(line))
		if '<VCol' in line:
			aodata.append(parse_ovb_triplet(line))
	aomax = 0
	for ao in aodata:
		aobins[int(sum(ao) / 3)] += 1
		aomax = max(aomax, aobins[int(sum(ao) / 3)])

	#for aoval in range(256):  # just display it
		#print aoval, 'O' * int(math.ceil(80 * aobins[aoval] / aomax))
	print "Number of vertices in each AO bin:",aomax, aobins, 'total=',sum(aobins)
	# ao

	olds3ofile = open(basename +  '.s3o', 'rb')
	olds3o = S3O(olds3ofile.read())
	olds3ofile.close()
	for i in range(len(aodata)):
		aodata[i] = sum(aodata[i]) / 3.0

	def recursefoldaoterm(piece, vertex_offset, ignore_these):
		# global ignorepieces
		print 'folding ao terms for', piece.name, 'current offset=', vertex_offset
		ignore = False
		if piece.name.lower() in ignore_these:
			print 'ignoring', piece.name
			ignore = True
		folded_vert_indices = []
		for vertex_i in range(len(piece.indices)):
			if piece.indices[vertex_i] in folded_vert_indices:
				# print 'already did',piece.indices[vertex_i]
				continue
			else:
				folded_vert_indices.append(piece.indices[vertex_i])
				vertex = piece.vertices[piece.indices[vertex_i]]
				# print vertex_offset,len(folded_vert_indices), vertex_i, len(aodata), vertex

				# dont use the entire range, because rounding errors might screw us over later, use only the range from 5-250
				vertex_ao_value = aodata[len(folded_vert_indices) - 1 + vertex_offset]
				vertex_ao_value = min(max(minclamp,vertex_ao_value*gain + bias),255)
				if ignore:
					vertex_ao_value = 200
				newuv = (math.floor(vertex[2][0] * 16384.0) / 16384.0 + 1 / 16384.0 * ((vertex_ao_value + 5) / 266.0),
						 vertex[2][1])
				# print newuv, vertex
				vertex = (vertex[0], vertex[1], newuv)
				piece.vertices[piece.indices[vertex_i]] = vertex
		print 'finished folding ao terms for', piece.name, 'unique vertex count=', len(folded_vert_indices)
		vertex_offset += len(folded_vert_indices)
		for child in piece.children:
			childoffset = recursefoldaoterm(child, vertex_offset, ignore_these)
			print 'in child, vertex offset=', vertex_offset, 'child_offset=', childoffset
			vertex_offset = childoffset
		return vertex_offset

	# parse bos for spin pieces
	ignorepieces = explodepieces
	recursefoldaoterm(olds3o.root_piece, 0, ignorepieces)
	news3ofile = open(basename + '.s3o', 'wb')
	news3ofile.write(olds3o.serialize())
	news3ofile.close()
	print '[INFO]',"Ding, fries are done!"

def countvertices(piece):
	numverts=len(piece.vertices)
	for child in piece.children:
		numverts+=countvertices(child)
	return numverts

def add_emit_Triangle_at_origin(filename, piecelist):
	datafile=open(filename,'rb')
	data=datafile.read()
	model=S3O(data)
	datafile.close()
	def recursively_add_tri(piece, piecelist):
		if piece.name in piecelist and piece.primitive_type == "triangles":
			if (min(piece.indices)>=2 or len(piece.indices)<=3):
				print (piece.name, " is already degenerate, no need to add verts")
			else:
				piece.vertices.insert(0,((0,0,0),(0,1,0),(0,0)))
				piece.vertices.insert(0,((0,0,1),(0,1,0),(0,0)))
				for i in range(len(piece.indices)):
					piece.indices[i] = piece.indices[i]+2
				print ("added 2 emit vertices to piece",piece.name)
		for child in piece.children:
			recursively_add_tri(child,piecelist)
	recursively_add_tri(model.root_piece,piecelist)

	output_file=open(filename,'wb')
	output_file.write(model.serialize())
	output_file.close()
	print '[INFO]',"Succesfully add_emit_Triangle_at_origin", filename

def addemptybase(filename):
	datafile=open(filename,'rb')
	data=datafile.read()
	model=S3O(data)
	datafile.close()
	if model.root_piece.name != "base":
		newbase = S3OPiece("",0,parent = None, name = "base")
		newbase.children.append(model.root_piece)
		model.root_piece = newbase
		output_file=open(filename,'wb')
		output_file.write(model.serialize())
		output_file.close()
		print '[INFO]',"Succesfully optimized", filename

def bend_foliage_normals(model, minu = 0, maxu = 0.5, minv = 0, maxv = 1, blendfactor=0.55, heightpct = 0.15):
	print ("Bending")

	maxh = 0
	for vertex in model.root_piece.vertices:
		maxh = max(vertex[0][1],maxh)
	vertexmid = [0,maxh*heightpct,0]
	for i, vertex in enumerate(model.root_piece.vertices):
		v,n,uv = vertex
		if uv[0] >= minu and uv[0] <= maxu and uv[1] >= minv and uv[1] <= maxv:
			#find vertex pointing towards v
			#normalize it
			#blend with original
			#normalize it
			#pack it back
			midtov = vectorminus(v,vertexmid)
			midtov = normalize(midtov)
			n = normalize(n)
			#midtov = vectormult(midtov,[blendfactor,blendfactor,blendfactor])
			mix = vectormix(midtov,n,blendfactor)
			newvn = normalize(mix)
			newuv = (max(minu + 0.008, min(maxu - 0.008,uv[0])), uv[1])#this is what human scum looks like
			model.root_piece.vertices[i] = (v,tuple(newvn),newuv)
		else:
			newuv = ((uv[0]+0.1)*0.9, uv[1])
			model.root_piece.vertices[i] = (v, n, newuv)
	recursively_optimize_pieces(model.root_piece)
	return model



#def swaptex(filename,tex1,tex2):
chickenlist = """chickena.s3o	chicken_red_l_color.dds	chicken_l_other.png
chickenab.s3o	chicken_redb_l_color.dds	chicken_l_other.png
chickenac.s3o	chicken_redc_l_color.dds	chicken_l_other.png
chickena2.s3o	chicken_redc_l_color.dds	chicken_l_other.png
chickena2b.s3o	chicken_redb_l_color.dds	chicken_l_other.png
chickenf.s3o	chicken_yellow_l_color.dds	chicken_l_other.png
chickenf1b.s3o	chicken_yellowb_l_color.dds	chicken_l_other.png
s_chickenboss_white.s3o	chicken_multi_l_color.dds	chicken_l_other.png
s_chickenboss2_white.s3o	chicken_redb_l_color.dds	chicken_l_other.png
brain_bug.s3o	chicken_redhead4_l_color.dds	chicken_l_other.png
chicken_colonizer.s3o	chicken_blue_l_color.dds	chicken_l_other.png
e_chickenq.s3o	chicken_brown_l_color.dds	chicken_l_other.png
epic_chickenq.s3o	chicken_black_l_color.dds	chicken_l_other.png
h_chickenq.s3o	chicken_apex_l_color.dds	chicken_l_other.png
chickenq.s3o	chicken_crimson_l_color.dds	chicken_l_other.png
ve_chickenq.s3o	chicken_white_l_color.dds	chicken_l_other.png
vh_chickenq.s3o	chicken_vcrimson_l_color.dds	chicken_l_other.png
big_chicken_dodo.s3o	chicken_black_m_color.dds	chicken_m_other.png
chicken2.s3o	chicken_pink_m_color.dds	chicken_m_other.png
chicken2b.s3o	chicken_apex_m_color.dds	chicken_m_other.png
chickenc.s3o	chicken_aqua_m_color.dds	chicken_m_other.png
chickenc2.s3o	chicken_black_m_color.dds	chicken_m_other.png
chickenf1.s3o	chicken_white_m_color.dds	chicken_m_other.png
chicken_listener.s3o	chicken_black_m_color.dds	chicken_m_other.png
chickens.s3o	chicken_green_m_color.dds	chicken_m_other.png
chickens2.s3o	chicken_yellow_m_color.dds	chicken_m_other.png
spiker_gunship.s3o	chicken_green_m_color.dds	chicken_m_other.png
chicken_pidgeon.s3o	chicken_1_m_color.dds	chicken_m_other.png
chicken_pidgeonb.s3o	chicken_1b_m_color.dds	chicken_m_other.png
chicken_pidgeonc.s3o	chicken_1c_m_color.dds	chicken_m_other.png
chicken_pidgeond.s3o	chicken_1d_m_color.dds	chicken_m_other.png
chicken_crow.s3o	chicken_vcrimson_m_color.dds	chicken_m_other.png
chicken_dodo.s3o	chicken_red_s_color.dds	chicken_s_other.png
chicken.s3o	chicken_1_s_color.dds	chicken_s_other.png
chicken1b.s3o	chicken_1b_s_color.dds	chicken_s_other.png
chicken1c.s3o	chicken_1c_s_color.dds	chicken_s_other.png
chicken1d.s3o	chicken_1d_s_color.dds	chicken_s_other.png
chicken1x.s3o	chicken_1x_s_color.dds	chicken_s_other.png
chicken1y.s3o	chicken_1y_s_color.dds	chicken_s_other.png
chicken1z.s3o	chicken_1z_s_color.dds	chicken_s_other.png
chickenc3.s3o	chicken_c3_s_color.dds	chicken_s_other.png
chickenc3b.s3o	chicken_c3b_s_color.dds	chicken_s_other.png
chickenc3c.s3o	chicken_c3c_s_color.dds	chicken_s_other.png
chicken_drone.s3o	chicken_white_s_color.dds	chicken_s_other.png
chicken_droneb.s3o	chicken_whitehc_s_color.dds	chicken_s_other.png
s_chicken_white.s3o	chicken_crimson_s_color.dds	chicken_s_other.png
chickenr1.s3o	chicken_blue_s_color.dds	chicken_s_other.png
chickenr2.s3o	chicken_white_s_color.dds	chicken_s_other.png"""

flyers = """chicken_crow.s3o
chicken_pidgeon.s3o
chicken_pidgeonb.s3o
chicken_pidgeonc.s3o
chicken_pidgeond.s3o
chickenf1.s3o
chickenf1b.s3o
spiker_gunship.s3o
chickenf.s3o"""

flyers = flyers.split('\n')

for line in chickenlist.split('\n'):
	linesp = line.strip().split('\t')
	path = ("C:/Users/Peti/Documents/my games/Spring/games/Beyond-All-Reason.sdd/objects3d/Chickens/"+linesp[0])
	#addemptybase(path)
	#add_emit_Triangle_at_origin(path,['body','head','tail','lthigh','rthigh'])
	#swaptex(path, linesp[1],linesp[2])
	#bakeAOS3O(path,"C:\\Program Files\\xNormal\\3.19.3\\x64\\xNormal.exe",isflying= (linesp[0] in flyers))
#exit(1)
'''
outdir = "N:/maps/features/"
basedir = "N:/maps/features/artturi/"
for file in os.listdir(basedir):
	if file.endswith('.s3o') and "_bend" not in file:
		model =bend_foliage_normals(S3O(open(basedir+file,'rb').read()))
		model.texture_paths = (model.texture_paths[0].replace('mtt-','btreea'), model.texture_paths[1].replace(".tga",".dds").replace('mtt-','btreea'))
		print (file, model.texture_paths[0],model.texture_paths[1])
		outf = open(outdir+file,'wb')
		outf.write(model.serialize())
		outf.close()
exit(1)
'''
root = Tk()
app = App(root)
root.mainloop()