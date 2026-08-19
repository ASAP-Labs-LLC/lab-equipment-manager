/* Banding is a quantisation artefact, so measure it in the quantised output:
 * walk a vertical column through the smoothest gradient in the frame and count
 * how often the value steps, and by how much. A dithered gradient steps by 1 LSB
 * at irregular rows; a banded one holds flat for a run then jumps. */
import fs from 'node:fs';
import {execFileSync} from 'node:child_process';
const png = process.argv[2];
const py = process.env.HOME + '/LAB-lem/LEM Web Server/.venv/bin/python';
const code = `
from PIL import Image
import sys
im = Image.open("${png}").convert("RGB"); w,h = im.size
for frac in (0.25, 0.5, 0.75):
    x = int(w*frac)
    col = [im.getpixel((x,y)) for y in range(0, int(h*0.30))]
    L = [0.2126*r+0.7152*g+0.0722*b for r,g,b in col]
    runs, cur, jumps = [], 1, []
    for i in range(1,len(L)):
        d = L[i]-L[i-1]
        if abs(d) < 0.4: cur += 1
        else:
            runs.append(cur); jumps.append(abs(d)); cur = 1
    runs.append(cur)
    flat = [r for r in runs if r >= 4]
    import statistics as st
    print(f"  x={frac:.2f}  rows={len(L)}  steps={len(jumps)}  "
          f"median step={st.median(jumps) if jumps else 0:.2f} L  "
          f"runs>=4rows={len(flat)}  longest flat run={max(runs)} rows")
`;
console.log(png.split('/').pop());
console.log(execFileSync(py, ['-c', code]).toString().trimEnd());
