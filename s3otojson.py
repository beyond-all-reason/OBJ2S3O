import s3o
import json

print (json.dumps(s3o.S3O(open("C:\Users\psarkozy\Documents\My Games\Spring\games\Beyond-All-Reason.sdd\objects3d\Units\corlab.s3o",'rb').read()).root_piece))
