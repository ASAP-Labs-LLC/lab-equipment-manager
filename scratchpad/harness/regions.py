"""Region statistics on a render — the depth ladder is a per-band question and a
whole-frame mean cannot answer it."""
import sys, os, struct, zlib, subprocess, tempfile

def _paeth(a,b,c):
    p=a+b-c; pa,pb,pc=abs(p-a),abs(p-b),abs(p-c)
    return a if (pa<=pb and pa<=pc) else (b if pb<=pc else c)

def decode(path):
    if path.lower().endswith(('.jpg','.jpeg')):
        tmp = tempfile.NamedTemporaryFile(suffix='.png', delete=False).name
        subprocess.run(['sips','-s','format','png',path,'--out',tmp],capture_output=True,check=True)
        path = tmp
    data=open(path,'rb').read()
    pos,idat,ch=8,b'',3
    while pos<len(data):
        (ln,)=struct.unpack('>I',data[pos:pos+4]); kind=data[pos+4:pos+8]
        body=data[pos+8:pos+8+ln]
        if kind==b'IHDR':
            w,h,depth,colour=struct.unpack('>IIBB',body[:10]); ch=3 if colour==2 else 4
        elif kind==b'IDAT': idat+=body
        elif kind==b'IEND': break
        pos+=12+ln
    raw=zlib.decompress(idat); stride=w*ch
    rows=[]; prev=bytearray(stride); at=0
    for _ in range(h):
        f=raw[at]; at+=1
        line=bytearray(raw[at:at+stride]); at+=stride
        if f==1:
            for i in range(ch,stride): line[i]=(line[i]+line[i-ch])&255
        elif f==2:
            for i in range(stride): line[i]=(line[i]+prev[i])&255
        elif f==3:
            for i in range(stride):
                left=line[i-ch] if i>=ch else 0
                line[i]=(line[i]+((left+prev[i])>>1))&255
        elif f==4:
            for i in range(stride):
                left=line[i-ch] if i>=ch else 0
                ul=prev[i-ch] if i>=ch else 0
                line[i]=(line[i]+_paeth(left,prev[i],ul))&255
        rows.append(bytes(line)); prev=line
    return w,h,ch,rows

def run(path, specs):
    w,h,ch,rows=decode(path)
    print(os.path.basename(path))
    for name,y0,y1,x0,x1 in specs:
        Y0,Y1,X0,X1=int(y0*h),int(y1*h),int(x0*w),int(x1*w)
        n=0; s=[0,0,0]; ls=0.0; l2=0.0
        for y in range(Y0,max(Y1,Y0+1),2):
            row=rows[y]
            for x in range(X0,max(X1,X0+1),3):
                i=x*ch; r,g,b=row[i],row[i+1],row[i+2]
                s[0]+=r; s[1]+=g; s[2]+=b
                L=0.2126*r+0.7152*g+0.0722*b
                ls+=L; l2+=L*L; n+=1
        m=[v/n for v in s]; mL=ls/n; sd=max(l2/n-mL*mL,0)**0.5
        print(f"  {name:14s} R{m[0]:6.1f} G{m[1]:6.1f} B{m[2]:6.1f}  B-R{m[2]-m[0]:+6.1f}  L{mL:6.1f} sd{sd:5.1f}")

DEFAULT=[('top',0.00,0.08,0.0,1.0),('uppersky',0.08,0.18,0.0,1.0),
         ('lowsky',0.18,0.245,0.0,0.6),('ridge',0.25,0.30,0.05,0.55),
         ('treeline',0.27,0.33,0.30,0.75),('midground',0.45,0.60,0.05,0.50),
         ('near',0.80,1.00,0.00,0.35)]
if __name__=='__main__':
    args=sys.argv[1:]
    custom=[a for a in args if a.count(',')==4]
    files=[a for a in args if a.count(',')!=4]
    specs=[(p.split(',')[0],*map(float,p.split(',')[1:])) for p in custom] or DEFAULT
    for f in files: run(f,specs)
