import sys, zlib, struct
sys.path.insert(0,'/Users/rynatical/LAB-lem/scratchpad/harness')
import grade
src,dst,x,y,w,h = sys.argv[1], sys.argv[2], *map(int, sys.argv[3:7])
px = grade.read_jpeg(src) if src.lower().endswith(('.jpg','.jpeg')) else grade.read_png(src)
# grade returns (flat list, width, height)?  inspect
print(type(px), (len(px) if hasattr(px,'__len__') else '?'))
