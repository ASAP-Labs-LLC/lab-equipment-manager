#!/bin/zsh
# hist.sh <png> — mean/std/percentiles in 0..255 plus two named patches
f=$1
magick "$f" -colorspace gray -format "mean=%[fx:mean*255] std=%[fx:standard_deviation*255] " info:
magick "$f" -colorspace gray txt:- 2>/dev/null | tail -n +2 | awk -F'gray\\(' '{print $2}' | tr -d ')%' | sort -n | awk '{a[NR]=$1} END{printf "p1=%.0f p5=%.0f p50=%.0f p95=%.0f p99=%.0f ", a[int(NR*0.01)]*2.55, a[int(NR*0.05)]*2.55, a[int(NR*0.5)]*2.55, a[int(NR*0.95)]*2.55, a[int(NR*0.99)]*2.55}'
magick "$f" -format "BmR=%[fx:(mean.b-mean.r)*255] " info:
magick "$f" -crop 40x30+985+950 +repage -colorspace gray -format "shadow=%[fx:mean*255] " info:
magick "$f" -crop 40x30+855+865 +repage -colorspace gray -format "lit=%[fx:mean*255]\n" info:
