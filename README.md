# OBJ2S3O
Tools to convert Spring S3O models to OBJ format and back. Also, for baking Ambient Occlusion stuff.

# GUI usage:



# Command line Usage
```
obj2s3o.exe --help
usage: obj2s3o.exe [-h] [-i INPUT] [-o OUTPUT] [--s3otoobj] [--wings3d]
                   [--objtos3o]
                   [--transformuv TRANSFORMUV TRANSFORMUV TRANSFORMUV TRANSFORMUV]
                   [--swaptex SWAPTEX SWAPTEX] [--optimize] [--printao]
                   [--clearao] [--piecelist PIECELIST [PIECELIST ...]]
                   [--zerolevelao ZEROLEVELAO] [--bakeaoplate]
                   [--aoplatesizex AOPLATESIZEX] [--aoplatesizez AOPLATESIZEZ]
                   [--aoplateresolution AOPLATERESOLUTION]
                   [--xnormalpath XNORMALPATH] [--bakevertexao] [--isbuilding]
                   [--isflying] [--minclamp MINCLAMP] [--bias BIAS]
                   [--gain GAIN] [--merge] [--scale SCALE] [--recenter]
                   [--adds3o ADDS3O] [--splits3o]

optional arguments:
  -h, --help            show this help message and exit
  -i INPUT, --input INPUT
                        The file to work on (s3o or obj)
  -o OUTPUT, --output OUTPUT
                        The name of the output file. If not specified,
                        modification operations are done in-place
  --s3otoobj            Convert a file from s3o to obj
  --wings3d             Optimize smoothing groups for Wings3D OBJ output
  --objtos3o            Convert a file from obj to s3o
  --transformuv TRANSFORMUV TRANSFORMUV TRANSFORMUV TRANSFORMUV
                        Transform UV space
  --swaptex SWAPTEX SWAPTEX
                        Specify the two textures for s3o
  --optimize            Convert a file from obj to s3o
  --printao             print the AO information for an s3o
  --clearao             print the AO information for an s3o
  --piecelist PIECELIST [PIECELIST ...]
                        Piece list to clear for --clearao, and piece list to
                        explode for --bakevertexao
  --zerolevelao ZEROLEVELAO
                        Specify zero level for AO
  --bakeaoplate         Bake an AO plate for the model
  --aoplatesizex AOPLATESIZEX
                        AO plate size X in footprint units
  --aoplatesizez AOPLATESIZEZ
                        AO plate size Z in footprint units
  --aoplateresolution AOPLATERESOLUTION
                        AO plate resolution in pixels
  --xnormalpath XNORMALPATH
                        Path to xnormal.exe
  --bakevertexao        Bake vertex AO for the model
  --isbuilding          Vertex AO. Enable this when baking AO for buildings.
                        This puts a larger than normal groundplate underneath
                        the unit, to make sure the building is only lit from
                        the top hemisphere
  --isflying            Vertex AO. Use for aircraft, this remove the
                        groundplate from under the unit, so it can get lit
                        from all directions
  --minclamp MINCLAMP   Vertex AO. The darkest possible level AO shading will
                        go to. 0 means even the darkes is allowed, 255 means
                        that everything will be full white. 128 is good if you
                        dont want peices to go too dark.
  --bias BIAS           Vertex AO. Add this much to every vertex AO value,
                        positive values brighten, negative values darken. Sane
                        range [-255;255] .
  --gain GAIN           Vertex AO.Multiply calculated AO terms with this
                        value. A value of 2.0 would double the brightness of
                        each value, 0.5 would half it. AO_out = min(255,
                        max(clamp, AO_in * bias + gain)).
  --merge               merge all pieces in an s3o
  --scale SCALE         merge all pieces in an s3o
  --recenter            recalculate center, midpoint, height
  --adds3o ADDS3O       Take all the pieces of this file, and add it to the
                        root of input
  --splits3o            take the piecelist, and split that out into a new s3o
  ```
  
