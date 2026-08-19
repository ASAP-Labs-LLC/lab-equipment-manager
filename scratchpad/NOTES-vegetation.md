# vegetation.js — round eighteen (2026-08-08): the blob was not a tree, and the crest was already stripped

Two jobs. Both were reports of a place, and in both cases the place turned out
not to be the thing that was named. The lesson is the same one twice: **an
island-wide table cannot answer a question about one location, and the first
step of answering it is to find out what is actually there.**

Every number below names its instrument. New instruments this round:
`harness/vsward.mjs` (the SWARD's own factor-by-factor gate chain — the tree
scatter has had one for four rounds and the mat had never had one),
`harness/vstrand.mjs` (the strand's elevation profile, and the eastern seaward
crest found geometrically rather than typed in as a box), `harness/vblobabl.mjs`
(per-tier visibility ablation in one page session), `harness/vproj.mjs`
(instances projected through the judged camera into a named screen rectangle),
`harness/vblob.mjs` (raycast). `vslope.mjs` gained an
`easternSeawardCrest` named-place block, which is the gate the island-wide
quartile table could not be.

---

## 1. THE BLOB ON THE SPIT WAS NOT A TREE, AND THREE PEOPLE ASSUMED IT WAS

A blind art director, then the props round, then this round's brief, all
described "a large dark round VEGETATION MASS... a scatter instance that landed
outside its mask", and props added the useful guess that it might be "one
instance whose mask sample was taken at a different pitch from its placement".

It is fourteen **sward** cards — the ground-cover mat — in a forty-five-metre
ellipse. `harness/vblobabl.mjs` proves it by hiding each tier in turn in ONE page
session: with the trees hidden the mass is still there; with the clutter hidden
it is still there; with the grass hidden it is still there; with the SWARD hidden
it is gone and only a dark patch of terrain shading remains.

Two prior instruments got this wrong and are worth recording:

- A raycast (`vblob.mjs`) through the same pixels answered "terrain" for most of
  them, because the mat lies flat on the ground under a scatter of small pines
  and the ray reaches the ground between the cards. A raycast answers what is
  NEAREST; the question was what is THERE.
- My own first isolation probe looked for a LOW, ISOLATED TREE — `altM < 6`,
  nearest neighbour > 22 m — and found **zero** on the whole island, which is a
  confident correct answer to the wrong question. The mat sits at altM 9-15 m,
  55-70 m from the water, and no tree there is isolated at all: there are 79
  stems in the same rectangle, median height 4.4 m.

### What actually let it through: the mat had no coastal rule

`harness/vsward.mjs`, the first factor table this tier has ever had. Every land
sample, the mat's `cover` product decomposed, binned by the salt band:

```
    salt              0-0.2   0.2-0.4  0.4-0.6  0.6-0.8  0.8-1.0
    sward shore       0.997   0.934    0.890    0.846    0.787
    tree  shore       0.991   0.814    0.690    0.566    0.410
    sward COVER       0.522   0.593    0.570    0.505    0.515   <-- flat
```

`SWARD_SALT` was **0.22** against the wood's 0.62 — a third of it — and the mat
had **no wind or exposure term at all**. After the seven other factors the total
came out at 0.515 on the saltiest ground on the island against 0.522 in the
sheltered interior. A tier whose density on a salt-blasted headland equals its
density in an inland glade is not a coastal rule that is mistuned; it is a
coastal rule that does not exist. Same family as the six inert rules in
REQUESTS.md, reached by a coefficient being an order too small rather than by a
threshold sitting off the end of a distribution.

At the blob itself (124, 328): salt 1.000, wind 1.000, exposure 0.992, and
`cover` **0.711** — 34% ABOVE the island mean of 0.529. The most exposed,
saltiest ground in the frame carried the thickest mat on the island.

Fixed by giving the mat the coast the wood already has: `SWARD_SALT` 0.22 ->
0.55, and a new `SWARD_WIND` = 0.55 keyed to `WIND_CUT` — the knee the wind-cut
crown variant is ALREADY drawn against, so the two rules cannot end up
disagreeing about where a headland is.

```
    salt              0-0.2   0.2-0.4  0.4-0.6  0.6-0.8  0.8-1.0
    sward COVER  before  0.522   0.593   0.570   0.505   0.515   ratio 0.99
    sward COVER  after   0.499   0.412   0.345   0.280   0.237   ratio 0.48
    at the blob      cover 0.711 -> 0.184     island sward 2,302 -> 1,866
```

### And the other half: `beach` was a PLAN DISTANCE and the spit is flat

`SHORE_BEACH = 26` is metres inland. A beach is a strip of constant width only on
a coast of constant steepness. Measured (`vstrand.mjs`) on this island:

```
    ground below      reaches inland   p50    p90     max
      1 m                             16 m    29 m    41 m
      3 m                             19 m    43 m    73 m
      4 m                             21 m    48 m    85 m
      6 m                             24 m    58 m    92 m
```

So on the flat ground the sand runs to 85 m and the veto died at 26. Cross-tabbed
against terrain's own painted sand (altM < 2.95, props' inverted `WASH_LINE`):
**976 of 16,934 land samples — 5.8% — are sand that terrain paints and that
`_shore().beach` called inland**, and 418 stems and 106 clutter pieces stood on
it. THE SIBLING PATTERN exactly: the field varies, it is not saturated, its sign
is right, and it describes "distance from the waterline" while its name says
"beach".

`_shore().beach` is now the MAX of the old distance term and an elevation term.
`Math.max` and not a product, because the two are two sufficient reasons to be a
beach and multiplying would have made the OLD rule weaker, which is the one thing
the change must not do.

**The elevation is measured, not copied.** props.js carries `WASH_LINE = 2.95`
hand-inverted out of terrain's shader and says so in REQUESTS.md; terrain's own
paint is `smoothstep(8.0, 0.5, aboveWater)` in a function nobody publishes.
Copying either is the bug this project has shipped six times. `_measureStrand()`
takes the **p90 of height above the tide among coast-field cells inside
`SHORE_BEACH` of the water** — a band that IS beach by everyone's definition, so
however high it stands is how high this coast's apron stands.

```
    _strandStats   cells 263   p50 0.88 m   p90 5.22 m   p99 9.63 m   used 5.22
```

5.2 m against terrain's paint being half gone at 4.25 m and finished at 8 m. Two
independent derivations agreeing to a metre is the only reason the number is
trusted. It is clamped to `STRAND_TOP = [1.5, 9.0]` with a `console.warn` on
either rail, because a cliff coast would put the p90 at twenty metres and turn a
beach rule into a second treeline.

Three guards on the elevation half, each stopping a specific wrong thing:

- **level.** A beach is what the sea can throw material onto; ground eight metres
  up at thirty degrees is a cliff foot. Written on `_slopeNorm` — median-centred
  on this island's own slope distribution — not on an absolute gradient, because
  four rules in this file have gone inert under a terrain retune for want of
  exactly that.
- **near.** Gated to `STRAND_REACH` = 110 m, or the rule follows a river valley
  inland and vetoes the riparian wood, which is the one low ground that SHOULD
  carry trees.
- **monotone is fine here.** props' `beachnessAt` was bitten because a strictly
  descending `low` term RANKS the sea first. This is a VETO, and `_site` has
  already refused everything below the waterline before `_shore` is called.

```
    stems on low level ground outside the old mask   418 -> 252   (-40%)
    trees in the spit box (40..200, 270..400)        159 -> 100   (-37%)
    sward in the same box                             15 ->   8   (-47%)
    island totals   tree 9,483 -> 9,110 (-3.9%)   sward 2,302 -> 1,866 (-19%)
                    clutter 19,259 -> 19,422 (+0.8%)  <- marram gained the strand
```

The clutter rise is wanted and is the answer to "nothing anchors it": marram's
own rule is `sh.beach * 0.85 + sh.salt * 0.55`, so a truer beach mask puts marram
ON the sand instead of leaving it bare.

---

## 2. THE EASTERN SEAWARD CREST IS IN Q4 AND IT WAS ALREADY STRIPPED

The brief set the test: sample that place, report its wind quartile; if it is not
in Q4 the field cannot see it, and if it IS in Q4 and still carries the heaviest
mass then something downstream is overriding it. `vslope.mjs` now carries the
probe (`easternSeawardCrest`). The crest is found GEOMETRICALLY — walk inland
from the waterline on each east-facing bearing, stop at the first ridge, keep
only bearings whose ridge is still within 145 m of the water — because a ridge
200 m inland is a different hill and averaging it in is how the island-wide
table hid this.

```
    ridge points found                 23
    mean wind exposure                 0.897     island Q4 edge 0.679
    wind quartile histogram            Q1 0  Q2 0  Q3 3  Q4 20      (87% Q4)

    stems within 40 m of the crest     n 877    mean height  8.90 m
    sheltered control, SAME altitude
      band, wind quartile 1            n 3662   mean height 14.50 m
                                                ratio       0.614
```

**The field sees it, and the krummholz is firing on it.** Both of the first two
things the critic asked for — a height gradient as well as a spacing gradient —
exist at that place and are large. On the crest bearings alone, band by band
inland from that coast:

```
    coast m     0-30   30-60   60-90   90-130  130-200  200-400
    wind        0.35   0.51    0.84    0.79    0.37     0.41
    stems/ha    54.7   162.3   161.4   142.1   196.0    121.3
    height m    8.96   8.34    9.22    6.27    8.24     13.93
    crown m2/ha 1821   4716    6354    2161    5062     10294
```

The crest band (90-130 m, altM 33.7) is the **lightest wooded band on its own
transect**: a fifth of the inland wood's canopy area and 45% of its height.

So what is the frame's right flank actually showing? The same table over the WIDE
east sector (+/-60 degrees, which is what "the right flank" means to a viewer)
rather than over the crest bearings:

```
    coast m           0-30  30-60  60-90  90-130  130-200  200-400
    crown m2/ha        905   3351  11042  18718    7166    13906
    shelter (mean)    0.514  0.335  0.325  0.376   0.576    0.627
```

The heaviest canopy in the frame is the **90-130 m coastal belt of the flanking
bearings** — the north-east and south-east, where there is no seaward crest at
all — at about 31,000 m2 of crown per hectare once the crest bearings are taken
out of it. And `_shelter` there is 0.376 against 0.576 in the band behind it,
i.e. **the density chain already says this is the poorer ground and it is being
overridden by something outside `_shelter`.** That is this round's biggest open
item and it is stated with its number rather than guessed at.

### There is NO prevailing wind direction in this model, and I did not invent one

The critic's second ask was "asymmetric crowns or lean away from the prevailing
direction". Checked rather than assumed:

- `_windExposure` is isotropic by construction: a box sea-fraction over `EXPOSE_R`
  plus an isotropic prominence disc over `PROM_R`. It has a magnitude and no
  bearing.
- `weather.js` publishes a `windAngle`, and it **veers continuously** —
  `p.windAngle = (p.windAngle + dt * (0.004 + p.wind * 0.010)) % 2PI`. It is an
  animation phase, not a climate. Baking a crown asymmetry against it at build
  would freeze one arbitrary bearing into the geometry that the animated wind
  then contradicts a minute later.

A prevailing direction is a DESIGN DECISION about this world, not a defect in
this file, and it is the operator's to make. If it is wanted, the cheap honest
version is a per-island constant published by terrain or weather alongside the
island, so vegetation, sky and weather all lean the same way.

### The third ask — bare ground on the windward face — is partly answered

`SWARD_WIND` is that rule, and it is the same edit as job 1. At the eastern crest
(563, -47) the mat's wind factor is 0.523 and its `cover` goes 1.000 -> 0.395, so
the soil-coloured mat now thins to a broken cover on the windward top and the
terrain's own stone shows through. The lichen TONE is terrain's paint and not
mine.

---

## The instrument that lied, and the one that caught it

`vsward.mjs` shipped with a hand-typed copy of the mat's eight-factor product. I
then moved `SWARD_SALT` in vegetation.js and the probe went on printing the old
table with complete confidence. It caught itself only because it ALSO predicts
the placed count from the same arithmetic, and that came out **2,280 predicted
against 1,866 actual** where the agreement had been 2% before.

The expression is now `_matCover()` in vegetation.js and the probe calls it, in
the same way `_shelter` was lifted out of the tree scatter last round for
`vdens2`. **The prediction check is kept anyway** — a probe that can no longer
detect its own drift is a probe that will drift.

Also recorded: `vsward.mjs` samples through `_site(..., this.plantFloor)`, and
`plantFloor` is `waterY + 9`, so its altitude bins below 6 m are EMPTY by
construction. It is structurally blind to the strand and its `treeShore` column
correctly did not move when the beach mask widened. The tree-side numbers in
job 1 all come from `vstrand.mjs`, which samples the land directly.

---

## The gates

```
                                          before                  after
pytest tests/ -q                    1043 passed, 7 skipped  1043 passed, 7 skipped
soak --parses 500 --layouts 6                               PASS, 498 parses,
                                                            6 layouts, all eight
                                                            counters 0
pr-clear.mjs                                                PASS, 0 faults,
                                                            0 console errors,
                                                            minAlt 2.96-3.18 m
vinv stems @ 160/320/640/1200/2200                          928/935/935/935/935
vinv sward drawn                                            106/131/131/131/131
vslope wind quartiles, stems/ha       398.9 .. 162.6        392.3/318.8/221.8/153.3
       mean height / m                14.00 .. 8.82         14.18/13.12/13.10/8.87
shot cam=far ultra, draws / tris      244 / 1.451 M         243 / 1.447 M
console errors                        0                     0
```

**Distance invariance holds** — the stem column is flat from 320 m to 2,200 m and
the 160 m row is short by the frustum exactly as it always is; the sward column
is flat over the same range. Nothing in this round touched a camera-dependent
quantity: both changes are build-time scatter rules.

The vinv absolute is 935 against the 1,187 quoted from the previous round. That
is a DIFFERENT PATCH — vinv picks its disc from the tree list itself, and the
tree list moved. Checked rather than assumed: at the patch centre (-65.7,
-316.3) the ground is altM 10.09 m, coast 44.9 m, `beach` 0.000 and `beachLow`
0.000, so the beach change cannot have fired there and the tree rules at that
point are bit-identical to before.

Two of the first four gate shots came back `buildStable: false` and were retaken;
`gi.js` and `engine.js` were both written during the capture window by the live
gi round.

---

## What could not be closed

- **The heaviest canopy in the frame is the north-east and south-east coastal
  belt at 90-130 m, not the crest**, at roughly 31,000 m2 of crown per hectare
  against 13,900 in the sheltered interior — and `_shelter` there is ALREADY its
  lowest on the transect (0.376 vs 0.576 behind it). So the override is outside
  the shelter sum: the remaining multiplicands are `standN` (the stand fbm),
  `_openness` (measured 1.0 there, so not it) and the age field. Whoever takes
  this next should start by binning `standN` and `age` on that belt. This is the
  actual subject of "the heaviest mass in the frame" and it is not a wind
  problem.
- **No prevailing wind direction exists** (above). Until one does, "asymmetric
  crowns or lean away from the prevailing direction" cannot be built honestly.
- **The critic's frame and the measurement disagree about the crest and the
  measurement is on firmer ground.** The crest is 87% Q4, 8.90 m against 14.50 m
  matched, and a fifth of the inland canopy area. If it still reads heavy at
  900 m, the cause is more likely that a 40 m ribbon of stripped ridge is a few
  pixels wide at that range than that the rule is not firing.
- **`fGully` and `fOpen` in the mat's table are reported with an "inert %" that
  is meaningless for them** — both have a minimum at or above 1.0, so "% above
  0.95" is trivially near 100 and says nothing. The column is right for the
  factors that subtract and wrong for the two that add. Not fixed; noted so the
  next reader does not report `fGully` as a dead rule.
- **`cliffFactor` still has no cliff to refuse** — mean 0.997, 99.2% above 0.95.
  Fifth round running.
- **One island.** `_measureStrand` is a per-island measurement and is guarded and
  warned, but nobody has run six layouts through it and checked that the p90
  lands inside `STRAND_TOP` on a coast of a different shape.

---

# vegetation.js — round fifteen (2026-08-08): a field that was always zero, and a fringe that was always the same width

Two jobs, one theme, and the theme is the one this file keeps rediscovering:
**a rule written against somebody else's field, on an assumption about that
field that nobody measured.** Round twelve found four of them. This round found
the fifth and its mirror image — a field so far unread, and a field read so
narrowly that it could only draw one shape.

Every number below names its instrument. Where a before and an after are quoted
they come from an **in-session ablation** (`harness/_vabl15.mjs`) and not from
two runs, because the dev server relayouts between processes: two consecutive
probe runs of this round saw the island go from r 597 to r 646 and the land
sample count from 13,006 to 14,799. A coefficient of variation from one process
is not a control for one from another, and half an hour of this round was spent
believing otherwise.

---

## 1. There were no riparian rules. Not broken ones — none.

terrain's note was that `biomeAt().flow` and `kind === 'stream'` fire for the
first time, that `FLOW_LO/FLOW_HI` at 3.4/8.0 sat above the top of this island's
log(acc) distribution (max 5.25), that the largest flow occurring anywhere was
0.355 against a stream threshold of 0.55, and that any riparian rules here had
therefore never once executed and should be assumed wrong.

**The honest answer is that this file contained no rule keyed to either.**
`command grep -c flow vegetation.js` returned **0**. The only thing read off
`biomeAt`'s classification was `kind === 'rock'`. The drainage network arrived
into a file with nowhere to put it.

So the round opened by measuring the field rather than by tuning against it —
`harness/vflow.mjs`, over the same land samples `vdens2` walks, using this
file's own `_site` to decide what land is:

```
flow      p50 0.000  p80 0.049  p90 0.177  p95 0.316  p98 0.499  max 0.936
land above 0.10  14.8%    above 0.20  9.1%    above 0.55  1.6%
kind === 'stream'   226 of 14,799 samples (1.53%)
```

Four things came out of that measurement and three of them decided a constant:

- **It is not moisture again.** r(flow, `site.wet`) = **0.416**, r(flow,
  `wetRaw`) = 0.459 — terrain feeds `flow * 0.55` into moisture, so a third of
  it is already represented and two thirds is new. Enough that a careless rule
  would have been a second copy of the moisture term; far from enough that the
  field was already being read.
- **It is not the coastline.** r(flow, `coastDist`) = **-0.044**, r(flow, alt)
  = -0.112. This is the single most useful number in the table for part 2: the
  drainage is a driver essentially orthogonal to the distance mask the critique
  named, which is exactly what "tells you nothing about elevation, exposure or
  moisture" was asking for.
- **The bands are set by RUN LENGTH, not by percentile.** Histogramming the
  field's own above-threshold spans across the island:

  ```
              runs   mean cells   longest   runs of one
  flow > 0.20  612         4.38     138 m         19.0%
  flow > 0.40  275         3.73      90 m         21.1%
  flow > 0.55  144         3.24      66 m         30.6%
  ```

  A stand of trees is a hundred-metre object. It can follow the 0.20 band and
  it cannot follow the 0.55 band, a third of which is single cells. So the WOOD
  is keyed to the broad low line and the watercourse itself only ever places
  individual things — `_riparian` returns `gully`, `bank` and `channel` and the
  three go to different tiers on purpose.
- **The forest was already 33% denser in the gullies before anything was
  written**, entirely secondhand through moisture. Mean final density 0.425 in
  the gully band against 0.319 outside it. Worth knowing, because it means the
  change to measure is the ratio and not the presence.

### What was built

`site.flow` and `site.stream` are captured in `_biome` and **not normalised** —
`flow` is a 0..1 field whose meaning is attached to its thresholds rather than
to its median, and half the island is a hard zero by construction, so the
median-centring `wet` needed would have destroyed the thing that makes it
useful. That is the fourth time this file has had to decide the question and the
first time the answer was "leave it alone".

`_riparian(site)` is the reader, the same shape as `_shore` and for the same
reason. Then:

- **`gully` goes into `shelter`**, i.e. through `_cover`, so it can RAISE the
  density. That is the reference forest's own trick — "A's forest thickens in
  the gullies, thins on the exposed shoulders" — and a multiplier could not
  have done it.
- **`RIP_TALL` is the counterpart of `CREST_SHORT`.** The crest prunes a tree to
  two thirds; the gully lets it reach past full. A density difference alone at a
  hundred metres reads as noise; a height difference in silhouette reads as
  ground.
- **The outlet**: `mouth` lifts most of the beach's total veto and softens the
  salt band behind it, because terrain's channels reach the sea now and the one
  place a beach is not dry sand is a channel mouth.
- **Birch follows the channel.** This rule was WRONG rather than absent:
  `wetEdge = sh.edge * ...` is a COAST band, so "willow and alder at the water's
  edge" has always meant "birch near the sea" — botanically backwards, and one
  more thing keyed to the distance mask. `rip.bank` is where it should always
  have pointed. Birch inland went 1.9% → 3.6% of stems.
- **The near field carries what the wood cannot.** Fern and bracken thicken
  along the channel (a clump can follow a 25 m run, a stand cannot), gorse
  thins in it (gorse is a dry plant), shingle appears in the bed, and the marram
  band PARTS where a stream crosses the beach — one multiply, no new instance,
  no new draw, and a gap in a repeating band is more legible at a far camera
  than anything that could be added to it.

Measured, and this is the whole of part 1 in one table
(`harness/vflow.mjs`, mean final density of the scatter's own chain):

```
                      before   after     cover before -> after
gully  (flow > 0.20)   0.425   0.657        0.978 -> 1.112
dry    (flow <= 0.20)  0.319   0.314        0.746 -> 0.732
stream (flow > 0.55)   0.548   0.828        1.097 -> 1.212
outlet (gully, < 42 m
        from the water) 0.276  0.563        1.049 -> 1.198
```

The gully/dry ratio goes **1.33 -> 2.09**. The dry ground did not move.

### The mistake this part made, and it took a near camera to see

The first pass shipped `RIP_SHELTER` 0.34, `RIP_TALL` 1.28 and a mouth relief of
0.30, and `harness/_vrip.mjs` — which finds the island's strongest channel and
stands the rig on the shoulder above it, ablated and unablated in one page —
photographed an **unbroken wall of canopy filling the frame at a 78 m camera**,
over ground that had been open sand with scattered stems.

Nothing in it was individually large. **Four rules fired on the same stem and
multiplied**: the gully raised shelter into the closed band, the closed band
raised the height through `t.cover`, `RIP_TALL` raised it again, and the outlet
relief lifted the salt band's own 0.46 shrink off the top. 1.28 x (0.76/0.46) is
2.1 — twice the height of the fringe either side of it and several times as many
stems. That is not a riparian stand, it is a blob, which is the failure the
critique names about this scene from the other direction.

A rule that is right about a place and a rule that is right about the same place
do not compose. `RIP_SHELTER` 0.34 -> 0.26, `RIP_TALL` 1.28 -> 1.18, mouth
relief 0.30 -> 0.20, `OUTLET_OPEN` 0.78 -> 0.62, and the gully's height lift is
now scaled OFF by the mouth so a coastal channel gets one cue and not two.
`shots/veg15/rip-after.png` -> `shots/veg15/rip2-after.png` is the pair.

**None of the four gate cameras could have caught this.** `wide`, `low`,
`street` and `yard` all frame the site, and the site is pads and hardstanding —
the one part of the island where `_openness` switches the planting rules off.
`_vrip.mjs` chooses its own subject and prints which one, for the reason
`vsward.mjs` had to learn twice.

---

## 2. The fringe was a constant-width band, and it was measurable

The blind critique, twice and unprompted: "a beaded fringe of constant width
following the coastline — same size, same value, same silhouette, one instance
repeated"; "a distance-from-coastline mask, not a biome."

Four claims. `harness/vfringe.mjs` reads all four off the PLACED matrices — the
composed instance transforms after every scale rule, not what a rule said —
and three of the four were true:

```
stems per hectare by metres inland      0-40   40-90   90-150  150-260  260+
                                          46     222      389      216     47
```

A ring, peaking a hundred metres in, thinning both ways, exactly as described.
And the coefficient of variation of that density **around the compass** inside
the 40-90 m band was **0.296**: near enough the same wood on every bearing.

```
salt band (< 90 m) crown variant mix    [11.6, 10.5, 78.0]  evenness 0.655
inland    (> 200 m)                     [31.1, 22.6, 46.3]  evenness 0.837
```

**78% of the fringe was one crown variant**, forced by a single line
(`if (sh.salt > 0.35 && rnd() < sh.salt * 0.8) vi = 2`). "One instance repeated"
was very nearly arithmetically true of the coastal silhouette.

The one claim that was NOT true is "never mass": nearest-neighbour spacing
against crown radius says the crowns overlap by a factor of **4.45 in the salt
band and 7.24 inland**, and 96.7% / 99.2% of stems have a neighbour whose crown
touches theirs. The wood masses geometrically and always did. Whatever makes it
read as legible blobs is a value and shading question, not a spacing one, and no
amount of extra density will close it. Filed as unclosed rather than attempted.

### The cause was that there was exactly one coastal number

`_coastDist`, and a function of one scalar can only draw a contour. The missing
number is EXPOSURE: a wind-blasted headland and the head of a sheltered inlet
are the same distance from the water and are not the same place.

`_buildExposure()` takes it as the fraction of open sea in a disc, off the coast
field's own land/sea mask by a summed-area table — the mask is already in
memory, the table is one pass, every query is four adds. **1 millisecond** at
build (`vdens2` reports `expoStats`), on a 16 m grid with a 9-cell radius.

**And then it subtracts off what a point at that coast distance typically has**,
which is the rule rather than a refinement. Measured first, because four rules
in this file have shipped inert for want of that question
(`harness/vexp.mjs`, spread of the candidate WITHIN each coast band):

```
                    r with coastDist    sd inside 40-90 m band
seaFrac  90 m              -0.750       0.091  (but 0.015 by 90-150 m: too short)
seaFrac 150 m              -0.880       0.083   0.13 .. 0.54 at one distance
seaFrac 240 m              -0.946       0.076  (this IS coastDist again)
mean fetch to sea, 300 m   +0.928       0.065  (likewise)
```

Raw sea fraction at any radius is 0.75 to 0.95 correlated with distance from the
coast. Shipped as it stands it would have been the same mask under a new name.
The bin means and standard deviations are measured on THIS island at build and
interpolated between bin centres (a step in the normaliser is a contour drawn at
24 m intervals, which is the same fault at a smaller scale), so the field is
median-centred by construction and travels to an island of another shape without
a constant being retuned. Inland every cell's sea fraction is exactly zero and
so is the bin mean, so it returns 0.5 and every rule on it does nothing — which
is correct, and is the failure mode a missing field has to have.

Measured on the shipped field: mean **0.481**, 1.6% above 0.95, 1.5% below 0.05,
full 0..1 range. Not inert.

### What it drives

The coefficients are chosen so that **exposure 0.5 reproduces the old numbers
exactly** — salt reach 130 m, strength 1.0. So this is a redistribution of the
fringe around the compass and not a global change to how much of it there is,
which is the only form in which it can be measured against the before.

- `_shore().salt` gets both its REACH (52 m in a cove, 208 m on a headland) and
  its STRENGTH from exposure. A weaker band of the same width is still a band of
  the same width, and the measured fault was the width.
- The wind-shaped variant is gated on exposure rather than distance, so the
  headlands keep their comb of wind-cut crowns and the coves get the mixed wood
  they should have had.

### Measured, one page, one instant, only the two readers stubbed

`harness/_vabl15.mjs` — `_exposure` returns the neutral half it returns when
there is no field and `_riparian` returns the three zeros it returned for every
round before terrain's retune, so the "before" column is this file behaving as
it did when `flow` was a hard zero and the coast was one scalar.

```
                                   before      after
stems placed                         9,205      9,849   (+7.0%)
triangles                        1,261,132  1,265,796   (+4,664)
band open / margin / closed    875/5242/3088  725/4723/4401   (closed +42.5%)
stems in gully / bank / mouth        0/0/0   1840/784/260

density around the compass, coefficient of variation
   0-40 m                           0.457      0.496
   40-90 m                          0.440      0.674     <- the judged band
   90-150 m                         0.747      0.820
   150-260 m                        1.023      1.038
40-90 m band, min..max per hectare   0..354     0..733

salt-band stem size  mean/sd/p90  0.448/0.268/0.84   0.518/0.337/1.00
salt-band variant mix             [10.7,11.9,77.4]  [13.3,15.4,71.3]
salt-band evenness                     0.651             0.759
0-40 m band, stems per hectare          50.9              68.0
```

**Every band's variation around the compass went up.** The judged one went up
54%. The fringe's emergents came back — its p90 stem size rose 19% and its
spread 26%, so the band is no longer one size — and its silhouette evenness rose
from 0.651 to 0.759 against an inland 0.84.

One caveat stated plainly: the ablation's "before" is the NEW code with its
inputs neutralised, which reproduces the old behaviour for everything driven by
field VALUES but not for the variant line, whose constant also changed
(0.8 -> 0.28 + 0.72·exposure, i.e. 0.64 at the neutral half). The true original
number for that row is the cross-run 78.0% from `vfringe.mjs` on the unmodified
file, not the ablation's 77.4%.

`shots/veg15/abl-far-before.png` -> `abl2-far-after.png` is the pair at the
critic's camera: the north-east headland thins, the western treeline breaks into
arcs with sand through it, and the sheltered southern ground masses darker.

---

## The gates

```
                                   before                     after
pytest tests/ -q            1043 passed, 7 skipped     1043 passed, 7 skipped
soak --parses 500 --layouts 6   PASS, all counters 0    PASS, all counters 0
                                                        (edge 0, the first time
                                                         — terrain's coast)
vinv stems @ 160/320/640/1200/2200
                            1980/2003/2003/2003/2003
                            2485/2504/2504/2504/2504
vinv sward drawn             166/216/216/216/216
                             296/333/333/333/333
visl  tree 9910 inWater 0 inBuilding 0 · clutter 19,280 0/0 · sward 2,292 0/0
      grass 38,176 0/0
shot ultra, drawCalls / triangles
      cam=wide                                     350 / 2.374 M
      cam=top                                      249 / 1.893 M
      cam=low                                      328 / 2.041 M
      cam=street                                   300 / 1.989 M
      cam=far                                      221 / 1.272 M
console errors                    0                          0
vegetation build                                          556 ms
_buildExposure                                              1 ms
```

**Distance invariance holds.** The stem column is flat from 320 m to 2,200 m and
the 160 m row is short by the frustum exactly as it was; the sward column is
flat over the same range and its 160 m row is the hand-off to the tuft ring.
Nothing in this round touched a camera-dependent quantity.

Mid-round, `pytest` reported one failure —
`test_floor_ui.py::test_the_drop_snaps_to_a_whole_bay`, a regex over
**index.js**, whose fixture reads only that file. It passed again an hour later
without anything here changing. Recorded because a red gate that is not yours is
worth naming rather than absorbing.

Two of the first five gate shots came back `buildStable: false` and were
retaken; rail, terrain and gi are all live.

---

## What could not be closed

- **"B's trees never mass" is not a density problem and this round did not fix
  it.** Measured: 96.7% of salt-band stems and 99.2% of inland stems have a
  neighbour whose crown overlaps theirs, at mean overlap ratios of 4.45 and
  7.24. The crowns already interpenetrate several times over. What the critic is
  reading as "individually legible blobs" is that each crown is separately lit
  and separately valued at that range — a shading question. `shade =
  lerp(1.12, 0.74, crowded)` is the only thing in the file addressing it and it
  is a per-instance tint, not a canopy surface. Adding stems will not close it
  and would cost the frame budget for nothing.
- **The 90-150 m band's variation barely moved** (0.747 -> 0.820) and it is the
  DENSEST band on the island. The exposure term reaches it (a headland's salt
  band now runs to 208 m) but the gully lift is uniform in coast distance by
  construction — r(flow, coastDist) = -0.044 — so it adds an unpatterned rise
  there. If the ring still reads as a ring, that band is where to look.
- **The outlets are small.** 151 of 14,799 land samples carry gully flow inside
  42 m of the water, which is about a dozen mouths. That is the right size for a
  legible interruption and it is near the floor of what a far camera can
  resolve; if it does not read, the answer is the marram gap and the shingle
  rather than more trees, because widening the mouth rule is how the wall of
  canopy happened.
- **The inland wood is 87% conifer** — 34.6% spruce and 52.2% pine past 200 m
  from the water, against 3.6% birch and 9.7% oak. Nothing this round caused it
  and nothing this round looked at it, but a wood that is seven-eighths one
  needle shape is the same class of finding as the 78% variant fringe and is
  probably the next one.
- **`site.flow` has no fallback worth the name.** The solo branch synthesises it
  from the Laplacian, which is a CONVERGENCE and not an accumulation — it has no
  memory of what is uphill, so it cannot tell a headwater dimple from the trunk
  of a catchment — and it is capped below `RIP_CHANNEL` so it can never invent a
  watercourse. The rules are exercised solo; they are not correct solo.
- **`matGrove` is null and the outer wood does not exist.** Deliberate and
  documented at the declaration ("null is the off position"), from a round after
  round twelve's notes. Restated here only because `visl` and `vinv` both report
  `grove: 0` at every distance and a reader could mistake it for this round.
- **Undergrowth is still camera-bound**, 2,653 pieces at 160/320/640 m and zero
  past that. Unchanged from round eleven, still the next thing after the above.
- **`cliffFactor` still has no cliff to refuse** — mean 0.995, 98.6% above 0.95.
  Fourth round running.
- **One island.** `EXPOSE_SPREAD`, `RIP_GULLY` and `COVER_BAND` all sit on
  fields that are now measured rather than assumed, so they should travel — but
  nobody has run six layouts through `vdens2` and `vfringe` and checked that the
  bands and the compass variation survive a different coastline.

## New harness

`vflow.mjs` (the drainage field as vegetation sees it: distribution, thresholds,
collinearity with everything this file already reads, run lengths, outlets, and
what the scatter does at the gully cells), `vfringe.mjs` (the four fringe claims
read off the placed matrices — constant width as a compass CV, size, silhouette
evenness, and whether the crowns actually touch), `vexp.mjs` (would an exposure
term vary at fixed coast distance, before building one), `_vabl15.mjs` (the
round ablated in one page, with the fringe measurement folded in so the layout
cannot move under the comparison), `_vrip.mjs` (the riparian work at a camera
that can see it, subject chosen and named), `_gq.mjs`. `vdens2.mjs` extended
with the exposure, riparian and outlet terms.

---

# vegetation.js — round twelve (2026-08-08): three rules that were switched off, and a hillside with a railway in it

Three defects from the operator. Two of them turned out to be one sentence:
**a rule written against a field somebody else owns, on the assumption that the
field runs 0..1 with a half in the middle.** It has now happened four times in
this file and it is the only thing worth remembering from this round.

Every number below names its instrument. Five instruments have given confident
wrong answers on this project in the last day, and two of the five below were
mine and wrong for an hour each.

---

## 1. "Rocks normals are flipped so it only renders the insides"

**They were, and so were the trunks, the stumps and the fallen logs.**

`Mesher.tube` and `_rockGeo` share an index pattern, `push(a, c, b, b, c, d)`.
On a tube the local frame is `(s, t, d)` with `t = d x s`, so the ring runs from
`s` toward `t`; walking (this ring -> next ring -> next angle) crosses "along the
axis" into "around the tube", and `axis x around` is `d x t = -s`, the INWARD
radius. On the boulder, `i` climbs downward and `j` runs +X toward +Z, and
(down x round) points at the centre. Both meshes have been wound inside out
since they were written, carrying correct outward vertex normals on back-facing
triangles.

It was invisible on trunks because `matBark` is `DoubleSide` and visible on the
boulders because `matRock` is not — which is why the operator reported it from a
rock. **DoubleSide was the blindfold, not the fix.** Fixed at the winding.

`harness/vwind.mjs` (new) is the instrument, and it is geometric rather than
photographic: for every triangle of every mesh in the subsystem, `cross(b-a,
c-a)` is where the front face looks and the mean of the three vertex normals is
where the surface says it looks; the sign of the dot decides. Measured by
reverting the two lines in-session and running it against both:

```
                    tris  agree  disagree   side     before -> after
clutter.rock          36      0 -> 24    24 -> 0    front    INVERTED -> ok
clutter.stump         28      0 -> 28    28 -> 0    front    INVERTED -> ok
clutter.log           28      0 -> 28    28 -> 0    front    INVERTED -> ok
tree.trunk (x15)   144-192   0 -> all   all -> 0    double   INVERTED -> ok
solid meshes judged   52          inverted 15 -> 0           FAIL -> PASS
```

The fifteen `mixed` rows the probe still reports are crown-card meshes whose
normals are deliberately fanned outward from the crown centre by `Mesher.card`'s
`bend` option; a card mesh lands near half by construction, so the probe's
INVERTED threshold is 90% and not 50%. It called one canopy at 54% an inversion
until that was fixed — a probe with a crude threshold is a probe that reports
the wrong thing confidently, which is the theme of the day.

**`matBark` stays DoubleSide and the comment there now says which kind of
DoubleSide it is.** Half the trees are mirrored (`scl.x *= -1`), a mirrored
instance reverses the winding, and three.js can flip face culling for a
negative-determinant OBJECT matrix but not for a negative-determinant INSTANCE
matrix. So a mirrored trunk still presents its back face, and
`<normal_fragment_begin>` negates the normal on a double-sided back face: those
are still lit as if from inside. What changed is that the non-mirrored half now
is not. Closing the other half means a second matrix array for the trunk mesh
with the flip taken out — 750 kB and a partition change — and is filed rather
than done.

---

## 2. "Trees above the tunnels don't populate properly, because they think they are level with the rail"

Two separate faults under one sentence, and the second one is the one that was
actually visible.

### 2a. The permanent way's keep-out was two-dimensional

`_buildRailField` rasterised every rendered frame of every track into a hash
grid and `_clearOf` refuses any candidate within `RAIL_FORMATION + TREE_CESS +
9` = **31 m** of one. In plan. A tunnel bore is a railway, so the hill above it
got a 62 m bald stripe painted across it.

Measured before the change, `harness/vtunnel.mjs` (new) — it walks
`ctx.railEarthworks`'s own points and counts PLACED stems, not drawn ones:

```
kind      spans  metres  ground above formation   stems/45m disc
cut          17     489                    0.5 m            9.9
fill         13     780                    0.0             23.6
grade        28    2453                    0.1             20.3
tunnel        4     190                    8.8             43.7
viaduct       2      76                   -2.8             32.0
island control (400 random land points)                    82.3
```

Eight point eight metres of hill over the bores, and the forest on top of it at
half the island's density.

`_railStructures()` now reads `ctx.railEarthworks` and a **tunnel span's frames
are excluded from the keep-out entirely** — the hill over a bore is planted
exactly as the hill beside it. `viaduct` and `bridge` frames go into a second,
narrower field (`RAIL_FORMATION + r`, no cess: 18 m rather than 31), because a
pier is physically present and a spruce through a girder is a defect the
operator would report next. And there is a geometric fallback that needs no
declaration at all — `STRUCT_UNDER` 4 m below the ground is a bore however it
was declared, `STRUCT_HEAD` 7 m above it is a deck — because the solo harness
runs this file with no rail in the room.

Measured after, with a same-hill control (the identical query 130 m either side
of the alignment, so a tunnel is compared against the hill it is inside rather
than against the island mean):

```
kind       corridor  same hill  ratio      what the ratio should be
cut             8.5      123.8   0.07      low: the ground became a formation
fill           19.7       61.7   0.32      low
grade          28.9       71.8   0.40      low
tunnel         20.6       60.1   0.34  ->  1.0
viaduct        37.5      141.0   0.27
```

**And that is an honest failure to close, for a reason worth writing down.**
rail.js changed its alignment three times while this round was open: the tunnels
went from 4 spans / 190 m / 8.8 m of cover to **2 spans / 39 m / 1.0 m of
cover**, i.e. bores of 16 and 22 metres. In-session ablation
(`vtunnel.mjs` stubs `_railStructures` to an empty map, rebuilds the field and
re-scatters, all in one page — because two page loads half an hour apart are two
different railways) moves the whole island by **three stems**:

```
                        stems   tunnel corridor/control
structures in keep-out   7082                    0.29
structures exempt        7085                    0.34
```

The mechanism is right and the geometry gives it nothing to do. At the midpoint
of each declared structure, `_railDist` — the distance to the nearest rail
sample the keep-out still holds — goes from

```
branch0 viaduct 33 m:   1.5 m -> 19.4 m
branch0 tunnel  22 m:   1.5 m -> 12.0 m
branch1 viaduct 22 m:   1.5 m -> 12.0 m
branch1 tunnel  16 m:   0.0 m ->  9.0 m
```

...and every one of those is still inside 31 m, because **a 16 m tunnel is
shorter than the keep-out discs of its own two portals.** The portals are
declared `cut`, correctly — there is an approach cutting there — and their
31 m keep-out reaches straight through the bore and out the other side. On the
190 m bores the operator was looking at, the midpoint is 95 m from a portal and
the rule bites. Filed to rail.js in REQUESTS.md.

### 2b. The ground moved after the trees were planted — 3,954 of them, worst 12.5 m

This is the one that was actually in the frame, and terrain.js's own note in
REQUESTS.md says it cannot happen: *"vegetation.js does not need it — it builds
after rail, so it already sees the final ground."*

`harness/_vheight.mjs` (new) compares every placed instance's **matrix** Y
against `ctx.ground()` at the same point, on a world that has been quiet for
nine seconds:

```
                    before          after
stems > 1 m out       164              0
stems > 3 m out        13              0
worst stem          7.75 m         0.02 m
undergrowth > 1 m     947              8   (the 8 are intended sink)
sward patches > 1 m   149             40   (likewise)
```

and the worst of them beside a tunnel bore, which is the operator's sentence
almost word for word: the trees really did think they were level with the rail,
because they were seated from a height field that was re-graded against the
railway *after* they were placed.

The fix is `terrain:regraded`, which terrain publishes and offered to anyone who
seats from `ctx.ground()`. `_seatOffsets()` records, once, immediately after the
scatter and from the matrices that already exist, how far each instance sits
above its ground — capturing every tier's own sink, lift and slope correction
without any of the four scatter loops having to report them — and `_reseat()`
rewrites element 13 of each matrix and forces a partition. On the demo fleet:

```
[vegetation] re-seated 3954 instances on terrain:regraded, worst 12.53 m
```

0.6 ms for the snapshot, 0.6 ms for the walk, measured on the live page.

---

## 3. "Cliff edges and vegetation elevation rules would help"

The blind art direction, quoted in the brief: *"B has exactly one density
everywhere ... Nothing merges. The forest never becomes forest, it stays a
scatter of assets ... the ring of trees around the island reads as a placed
border rather than as vegetation responding to soil, wind, and slope."*

**That was literally, arithmetically true, and it was one line.**
`harness/vdens2.mjs` (new) re-runs the scatter's own density chain over the
island calling this file's own `_site`, `_openness` and `_shore`, and reports
what each factor actually does over 12,568 land samples:

```
rule              mean   above 0.95   min .. max
stand gate       0.975       76.9%    0.81 .. 1.00
slope            0.993       96.7%    0.12 .. 1.00
treeline         1.000      100.0%    1.00 .. 1.00
rock             0.959       93.4%    0.38 .. 1.00
shore            0.833       57.7%    0.00 .. 1.00
openness         0.764       72.9%    0.00 .. 1.00
```

The stand field — the thing that is supposed to put woods and meadows on an
island — is `smoothstep(0.14, 0.34, stand)`, and `ctx.Tex.fbm` **never leaves
[0.40, 0.72]**. The gate returned 1.000 at every one of 12,568 samples. The
treeline is in metres above the sea and the island is 65 m tall, so it returned
1.000 too, which is correct and by design. Slope returned above 0.95 on 96.7%
because nothing on this island is steeper than 20 degrees.

So the only two things varying the forest were the site's clearing mask and the
coast. One density everywhere, exactly as described, and four rounds of arguing
about the forest from screenshots could not see it.

### The unit trap, for the fourth time

`_probeFields` replaces `_probeStandField` and measures both fields the cover
map is built from, **on land**, and maps each on to 0..1 **median-centred**.
Three numbers, not two, and two land-only mistakes were made getting there:

- p5/p95 mapped to 0/1 centres a *symmetric* field and does nothing for a skewed
  one. `biomeAt().moisture` is strongly skewed; the stretch put its MEAN at
  0.161 and the closed band fired on 79 of 12,568 samples.
- sampling the island's SQUARE on an island is mostly sampling sea, and terrain
  reports moisture near one under water: the median came back at 0.767, which is
  about the ninetieth percentile of the land.

Measured, both wrong, both caught by the probe within the hour. The land-only
median-centred version gives `standRange [0.253, 0.463, 0.638]` and
`wetRange [0.000, 0.198, 0.783]`.

And the normalisation went **into `_biome`**, not into the eleven call sites.
`site.wet` is the mapped field and `site.wetRaw` is terrain's number. The
conifer roll, the oak roll, `spec.wet`, the fern rule and the sward's own cover
were every one of them written as though a half meant average ground and every
one of them had been separately tuned against a field whose median was 0.18.

### Three densities doing three jobs

`_cover(standN, shelter)` is two smoothsteps over `standN*0.52 + shelter*0.48`,
landing on three plateaux:

| band | what it is | cover |
|---|---|---|
| `COVER_OPEN` | heath and pasture: low wind-shaped trees over sward | 0.18 |
| `COVER_MARGIN` | open woodland: legible individual trees, sky between | 0.62 |
| `COVER_CLOSED` | a wood: crowns touching, ground not seen | 1.30 |

`shelter` is the half that answers "responding to soil, wind and slope": damp
hollows carry closed canopy, exposed crests and salt-blown headlands carry
heath. It goes in through `_cover` rather than as another multiplier **so it can
raise the density as well as lower it** — every rule in this file before today
could only subtract, and a rule that can only subtract cannot describe a wood,
only where a wood is not.

`TREE_STEP` came down 3.6 -> 3.0 m so the closed band can be denser than the old
uniform forest rather than merely as dense, and it costs LESS build time, not
more: the die is now tested against the band BEFORE the seven terrain samples
`_site` costs. Measured, `vdens2.mjs`, same island:

```
band     share of land   stems/ha
open             18.4%      143.6
margin           60.7%      226.6
closed           20.9%      380.7
```

and the cover field itself runs 0.14 to 1.00 with 15.9% of the land at the top
plateau and 7% at the bottom — a better than eight-to-one range where the old
one was 0.81..1.00.

Total population is unchanged: **7,017 stems against 7,048 before**, 63/ha over
112 ha. The forest was not thinned; it was moved.

Two cues, not one, because a rule that only changes how MANY trees there are
changes the density and nothing else — a stand of full-height timber at half
spacing reads as felling. So `CREST_SHORT` prunes a tree on an exposed top to
two thirds of the height it reaches in the hollow below (silhouette against sky
is where a height difference is legible and a density difference is not), and
open-grown trees on heath are shorter and broader than stems competing for light
in a closed stand.

And the gorse follows the wood: the bush rule gained `(0.55 + 1.05*(1 - stand))`
and the sward gained `(0.62 + 0.72*(1 - stand))`, because **thinning the wood
over an exposed top without putting anything in its place does not make heath,
it makes bald ground** — which is "barren", the exact complaint four rounds of
this file have been answering from the other direction.

### Cliffs

Two tests, because a cliff has two signatures and a gradient carries one:

- `CLIFF_SLOPE` 1.30 (52 degrees) is a **refusal**, not a factor. The soft ramp
  below it leaves a tenth of a vertical face planted.
- `site.drop` is the vertical STEP the crown straddles, and it is free: the six
  hexagon samples `_clearOf` already takes for the waterline are enough for
  three second differences, `h(k) + h(k+3) - 2h0`, which is **exactly zero on
  any plane however steep** and is the height of the face on a lip. terrain
  samples its height field on a 17 m grid, so a 16 m face inside one cell comes
  back as a mild gradient and a wall. `CLIFF_DROP` [4.5, 9.0] thins and then
  refuses.

On today's island this fires on 3 to 11 candidates a build and the soft term
averages 0.997 — i.e. it is not a second treeline, which is what the rock rule
became when it was written carelessly. Its range is the thing to watch: min
0.13, so where there IS a step it takes 87% of the density. terrain's harsher
relief is in a parallel round and this is waiting for it.

---

## The gates

```
                                      before          after
pytest tests/ -q                1043 passed, 7 skipped   unchanged
soak --parses 500 --layouts 6   PASS, all counters 0     PASS, all counters 0
vinv stems @ 160/320/640/1200/2200 m
                                2047/2062/2062/2062/2062
                                2525/2539/2539/2539/2539
vinv sward drawn                 332/370/370/370/370
                                 203/265/265/265/265
shot cam=wide ultra              387 draws / 2.344 M      387 / 2.376 M
     cam=top                                              256 / 1.869 M
     cam=low                                              326 / 2.013 M
     cam=street                                           321 / 2.056 M
console errors                          0                        0
vegetation build                                          540-563 ms
```

Distance invariance holds: the tree column is flat from 320 m to 2,200 m and the
160 m row is short by the frustum exactly as it was before. The sward column is
flat over the same range; its 160 m row is the hand-off to the tuft ring
underneath it (`SWARD_UNDER`), which is the shape it had before.

The gate shots were retaken because `shot.mjs` reported `buildStable: false` on
two of the first four — terrain and rail are both live — and `cam=low` is
`false` even in the final set. Everything quoted here is from a `settled: true`
frame.

New work, timed on the live page (`harness/_vcost12.mjs`) rather than
differenced from two wall clocks that moved for other people's reasons:

```
_probeFields   0.4 ms      _seatOffsets  0.6 ms
_buildRailField 0.1 ms     _reseat       0.6 ms
_scatterTrees  85.2 ms   (of a 540 ms build; 1.44x the candidates of before,
                          rejecting 60% of them on arithmetic instead of 25%
                          of them on height lookups)
```

## Screenshots

- `shots/veg12-base-far.png` -> `shots/veg12-b-far.png` — the whole argument for
  part 3. Before: one density, edge to edge, the coastline of trees the critique
  called a placed border. After: closed dark masses on the sheltered ground,
  glades, and a thin scatter over the exposed shoulders.
- `shots/veg12-base-wide.png` -> `shots/veg12-gate2-wide.png` — the same at the
  judged camera.
- `shots/veg12-gate2-{wide,top,low,street}.png` — the gate, ultra.

## New harness

`vwind.mjs` (winding vs normals, per mesh, with the DoubleSide flag beside it),
`vtunnel.mjs` (earthwork spans vs planting, with a same-hill control and an
in-session ablation of the rule), `vdens2.mjs` (which planting rule is inert,
and the cover distribution), `_vheight.mjs` (is anything seated on ground that
has since moved), `_vcost12.mjs`, `_veglog.mjs`. `vsward.mjs` was patched: its
subject chooser required 92% land within 200 m, found nothing on a 638 m island
with a ragged coast, returned null and **threw** instead of saying it could not
find its subject. The land bar is now part of the relaxation ladder and a
failure to find a subject is reported.

## Still weak

- **The tunnel rule cannot be demonstrated on the current railway.** 16 and 22 m
  bores are shorter than their own portals' keep-out. Filed to rail.js.
- **Undergrowth is still camera-bound** — 2,588 pieces at 160/320/640 m and zero
  past that. Unchanged from round eleven, still the next thing.
- **A mirrored instanced trunk is still shaded from inside.** three.js cannot
  flip winding per instance; see above.
- **The sward's ceiling is `_site`, not its own rule.** 14,258 of 23,409 lattice
  cells fail the site test (freeboard, blockers, the 6.5 m reach hexagon), so the
  mat tops out near 4,100 patches and places 1,773 of them. The open band is now
  a fifth of the island and the mat is what has to carry its colour; if the
  glades photograph bare, that ratio is where to look and not at the cover rule.
- **`cliffFactor` has never had a cliff to refuse.** Everything about it is
  measured except the case it exists for.
- **The band split is tuned against one island.** `COVER_BAND` sits on a field
  that is now measured, so it should travel — but nobody has run six layouts
  through `vdens2.mjs` and checked that the three bands stay populated.

---

# vegetation.js — round eleven (2026-08-07): the ground was the half nobody measured

Ryan's rule, and the one this file is judged on: *"make vegetation static, so
same size, same color, same tree and grass just increase effects and detail as
you get closer. Zooming in and out should not make it more/less barren."*

The trees already obeyed it when this round opened, and the first thing done was
to prove that rather than assume it. `harness/vinv.mjs` counts what is DRAWN
inside one fixed 130 m disc from five camera distances:

```
dist      160    320    640   1200   2200      placed
stems    2047   2062   2062   2062   2062        2062
```

Flat. Nothing is culled for being far except the frustum, and the far card's
range is the island rather than a circle round the camera. That work landed last
round and it holds.

**The ground layer did not, and no tree probe could see it.** The same run, same
disc, before this round:

```
tufts   33413  18603      0      0      0
clutter  1102   1102   1102      0      0
```

76,000 grass tufts and 18,000 pieces of undergrowth live in discs drawn round
the camera, because a map-wide field at one tuft every two metres is two million
instances. So half of "zooming out makes it more barren" was still exactly true,
and it was the half you cannot fix by moving a radius: the eye sees a green
meadow at a hundred metres and a tan plain at four hundred, and both are the
same ground.

## What was built: the sward

The meadow gets what the wood already has — a representation cheap enough to
cover all of it. One horizontal alpha-cut card per patch, **two triangles**,
painted with grass seen from directly above out of `PAL.grass`, the same palette
object the tuft tile is painted from. Scattered once at build over the whole
island by the same gates the tuft ring asks — openness, the blocker list, the
permanent way, the waterline — and drawn wherever it is in frustum, however far
the camera stands.

**Ground-parallel is the whole trick.** A vertical card over-covers at a grazing
angle and under-covers from above. A card lying in the ground plane covers the
same *fraction of projected ground* from every elevation, and that is the
quantity which has to be invariant. It also means the tier needs no view
alignment, no wind, no translucency and no shadow. It is the ground being green.

Four paintings on a 512² page of its own, each closed with an elliptical lens
rather than filling its square — the lesson the fourth LOD's clump page had to
learn twice, because a filled tile mips to a filled tile and a hillside of them
becomes a hillside of rectangles with perfectly straight tops. `polygonOffset`
rather than a lift: a 15.5 m card on a heightfield sampled at 17 m cells cannot
follow it, so any lift big enough to clear the convexities is big enough to be
seen floating from a low camera.

## The hand-off, and the number that was wrong first time

The mat and the tufts describe one meadow, so they have to sum to one. The first
build eroded the mat to nothing inside the tuft ring on exactly that argument —
and the `street` frame said plainly it was wrong. 76,000 rigid 40 cm cards on a
dry soil are chips of green on bare earth; the mat at the same place is a
continuous forty-odd percent. Fading it out under them made the *near* field
emptier than the far — the same fault as before, pointing the other way, and
only visible because the near frame was looked at.

So the mat runs everywhere and drops to `SWARD_UNDER` = 0.55 under the ring,
which is also the more honest description: the sward is the ground and the tufts
are what stands up out of it. After:

```
dist          160    320    640   1200   2200      placed
sward         332    370    370    370    370         370
sward alpha   244    339    370    370    370
tufts       34009  10333      0      0      0
```

Population invariant. The 34% of coverage the mat gives up at 160 m is exactly
what 34,000 tufts are standing in.

## What it costs

Ablated in session — the sward meshes' `visible` flag toggled and the renderer's
own counters re-read, median of ten frames — because **the scene totals moved
under this round by 850,000 triangles and 300 ms of first frame while nothing in
this file changed.** Two `shot.mjs` runs forty minutes apart at `cam=wide`
reported 357 draws / 2.38 M and 216 draws / 1.52 M; terrain, rail and gi are all
live. Any per-file number taken by differencing two runs from disk is somebody
else's work.

| | draws | triangles |
|---|---|---|
| sward, `cam=top`, 741 patches drawn | **5** | **3,190** |
| sward, `cam=wide`, 757 patches drawn | **6** | **4,030** |
| paying for it: `GRASS_RADIUS` 175 → 150 | 0 | **−60,000** |

The radius cut is not a reduction. The tufts used to be the only green on the
ground, so their radius was the radius of the meadow; they are now the near tier
of a two-tier cover and only have to run as far as a tuft can be told from a
mat, which on the `low` frame is about 150 m. 27% off the area is ~15,000
instances and the sward buys all of it back at a fortieth of the price.

Build time: the page paints in **3.8 ms** and the scatter runs in **7.1 ms**, of
a 471 ms vegetation build (`harness/_swcost.mjs` times both directly on the live
page rather than inferring them from a wall clock that moved for other reasons).
Net first-frame effect is inside the noise and probably negative, since the
grass ring is 27% smaller and it rebuilds on the first frame too.

Gates at ultra, all four cameras, zero console errors:
`wide` 216 / 1.52 M · `top` 205 / 1.37 M · `low` 153 / 1.31 M · `street` 164 /
1.31 M, against 450 / 2.5 M. `--quality floor` `wide`: 178 / 1.03 M.

## The four faults, re-checked rather than assumed

`harness/visl.mjs` walks every tier's own **placement list** (not the drawn
transforms — a camera-relative probe only measures what that camera can see) and
tests each against the waterline with the plant's reach, and against
buildings.js's own footprints:

```
tier       n     inWater  inBuilding
tree     7397         0           0
clutter 18605         0           0
sward    1293         0           0
grass   37686         0           0
```

Trees on water and trees through buildings are clear at every tier including the
new one, which passes `_site` a reach of 7.8 m — half its card — for the reason
the grove tier had to: what must clear the water is the painting, not the point
it is anchored at.

`soak.mjs --parses 200 --layouts 4`: collision 0, reversal 0, floating 0,
unreachable 0, relayout 0, consoleErrors 0, deadRailway 0, **edge 12** — the
identical twelve findings at the identical radii as the run before this change,
all terrain's coastline, filed twice already.

## Screenshots, all looked at

- `shots/isl2-wide.png`, `isl2-top.png`, `isl2-low.png`, `isl2-street.png` —
  the gate, ultra.
- `shots/isl2-floor-wide.png` — the floor tier: recognisably the same island,
  same wood in the same places, no grass and no sward at all, fewer trees.
- `shots/swab-top-on.png` / `swab-top-off.png` — **the pair that is the whole
  argument.** One session, one instant, only the sward's visibility changed.
  The ground between the woods goes from dust to grassland; the hardstanding
  does not, because openness says it should not.
- `shots/swlook-220-only.png` — the tier alone over terrain, which is how the
  mat was checked for tiling, straight edges and z-fight. It has none.
- `shots/vsward/` — the framing-normalised near/far pairs.

New harness: `vsward.mjs` (ground-cover invariance by ablation, framing
normalised), `_swab.mjs` (in-session ablation with median sampling), `_swcost.mjs`
(build-time attribution), `_swlook.mjs`, `_swhy.mjs` (why a candidate was
rejected, by cause), `vbill.mjs`. `vinv.mjs` and `visl.mjs` extended to the new
tier.

## Still weak

- **Undergrowth is still camera-bound.** 18,000 ferns, bushes, stumps and stones
  in a 450 m disc round the partition centre, and past that they are gone —
  1102 / 1102 / 1102 / 0 / 0 across the five distances. It is a much smaller
  offence than the grass was, because at 450 m a fern is sub-pixel and the sward
  now carries the ground colour there, but it is the same class of fault and it
  is the next one to fix. The fix is the same shape: fold the undergrowth's
  aggregate into the sward's own painting past its range rather than deleting it.
- **The measurement of "is the land barren" is harder than it looks and my first
  two attempts were both wrong.** `vsward.mjs` picked its own subject; version
  one chose a coastal headland (half the crop ocean, the rest the salt band
  where every rule deliberately suppresses cover) and reported, correctly, that
  nothing had changed. Version two chose a glade and measured the canopy. It now
  requires the patch to be inland, land-surrounded and away from the wood, and
  it prints which relaxation pass found it. A probe that chooses its own subject
  has to be told what the subject is.
- **The site's own apron is most of the open ground on this island**, and it is
  bare by design — `_openness` is zero on the pads and a 17 m cess keeps the mat
  off the permanent way. So from `street` and `low` the change is nearly
  invisible; it is `wide` and `top`, where natural ground is in frame, that show
  it. If the bald middle is still the complaint, the answer is in the site rules
  and not here.
- The sward does not appear at `floor`. That is Ryan's instruction read
  literally — "no grass just trees" — but it does mean the lowest rung is a
  differently-coloured island, and the mat costs 5 draw calls. If the floor tier
  is ever judged on looking like the same place, this is the cheapest thing to
  give back.

---

# vegetation.js — round nine (2026-08-07): the island, and three dead rules

Ryan: *"Making it into an island instead of a patch of land ... would allow for
the island to be more densely vegetated. It can be a sizable island, that
expands dynamically with each equipment added."*

He is right, and the density is what this file spent the round on. But the first
thing measured was worse than any density question.

## The forest did not exist

Probed on the running map before a line was changed: **0 tree buckets, 0 groves,
0 stems.** Grass and bushes on the site and not one tree. Nothing threw, nothing
warned, and the subsystem logged a clean build in 588 ms.

terrain.js now publishes `biomeAt`, and its `altitude` comes back in **metres**
(0–119 here) where every rule in this file is written in 0..1. The treeline is
`d *= 1 - smoothstep(0.70, 0.94, alt)`, so any candidate more than 94 cm above
the lowest ground on the map had its density multiplied by exactly zero. Every
candidate. `_probeAltitude` now measures the unit at build and normalises when
the sampled range escapes [-2, 2]; it warns when it does, because the next
consumer will not be so lucky. Filed to terrain in REQUESTS.md.

## Three rules that were present, correct, and never invoked

Same shape each time — a default parameter never overridden, a guard on a field
nobody set, a loop that never asked. All invisible on read.

1. **`_site(x, z, r = 0, …)` — every one of the four scatter loops passed `r`
   as zero.** `r` is the plant's reach, and the six-point hexagon in `_clearOf`
   that keeps a *crown* out of the water has therefore never run, in any tier,
   since it was written. That is "trees generating on water": a grove card is
   58 m of painted canopy placed on a stem test. Fixed — 6.5 m for trees, 24 m
   for groves, 1.2 m for undergrowth — and the cheap rules (walls, permanent
   way) moved above the hexagon so the extra six height samples only run on
   candidates that survive everything else.
2. **4,924 of 19,000 grass tufts stood inside a building footprint.** The grass
   ring never called `_clearOf`, so the blocker list every other tier asks was
   not asked. Tested per cell now. This is the second half of "the trees are
   generating through buildings" — the trees were already clear.
3. **`_applyGroundSeason` has never run.** It opens `if (!c.base) continue;` and
   nothing ever set `base`, so the bracken was the same green in February as in
   October under a canopy that turned. `base` is taken once at scatter.

## The island

`_island(plan)` — centre of `plan.bounds`, radius `max(halfspan) + 360 +
13·stations`, floor 560 m — recomputed in `onPlan`, and past a tenth of a radius
of change `_regrow()` re-scatters rather than re-testing, because a bigger
island has ground that did not exist a moment ago. terrain's own island is
preferred if it publishes one (`island` / `coast` / `islandRadius`); it does not
yet.

**Two radii, not one, and the difference is the whole LOD argument.** The
plan-derived island (588 m here) bounds the *dense* scatter — everything that
costs real triangles. `_measureLand()` walks sixteen bearings outward until the
height field goes under the waterline and finds the actual coast at ~1.25 km.
Growing the dense scatter to meet that would be six square kilometres of
candidates thinned by the cap to sixty stems a hectare: the diorama problem
solved by re-creating the density problem. So the cheap tiers use it instead —
the outer wood (a fortieth of a triangle per tree) now covers the land to the
coast, and the marram is scattered on the real beach. Groves drawn at `wide`
went 36 → 375 for five thousand triangles.

Land/sea is never the circle. It is `ground(x, z) > waterY`, which is the only
test that can be right at a coastline.

`_buildCoast()` rasterises the land/sea mask and runs a chamfer 3-4 distance
transform over it: `_coastDist(x, z)` is metres inland, and `_shore(site)` reads
it as three bands — beach (26 m), salt (130 m), and the wet edge between them.
Everything coastal hangs off that one gradient.

## Density achieved

| | before | after |
|---|---|---|
| stems on the map | 27,600 over 2.29 km² = **120/ha** | 23,700 over 1.09 km² = **218/ha** |
| grass tufts | 19,000, and a **quarter-disc** | 38,000, a ring |
| undergrowth | 10,000 | 17,710 (+ a marram set) |
| groves drawn (`wide`) | 36 | 375 |
| veg draws / tris (`wide`, ultra) | — | **87 / 774 k** |
| veg draws / tris (`low`, ultra) | — | 88 / 639 k |
| veg draws / tris (`wide`, **floor**) | — | **45 / 455 k** |
| whole scene (`wide` / `low` / `top`) | — | 2.39 M / 2.18 M / 1.95 M, 386 / 340 / 264 draws |

Against 450 draws / 2.5 M. The before column has no draw figures because **the
before build had no trees in it** — the 11 draws and 167 k triangles it measured
were grass and bushes. The 27,600/120 per hectare is from round eight's notes,
which is the last state anyone measured a forest in.

The grass is the other half of "there's also not enough grass for ultra", and it
was not the count. The ring walked the cell square in raster order from one
corner and the cap always bound, so the sward was a solid quarter-disc behind
the camera ending on a straight line. It walks outward in square shells now, so
the cap truncates the far edge where the density ramp is already thinning. Then
radius came **down** (300 → 175) and per-cell density went **up** (26 → 90):
past a hundred metres a 40 cm tuft is three pixels and terrain's detail texture
is carrying the ground anyway, so a mat that is a mat beats a wider scatter.

`NEAR_RADIUS` is 230, and it moved twice. 340 was tried, put 2,555 modelled
trees in the near set and took the scene to **2.93 M** — over the ceiling.
Density × radius² does not negotiate.

## Ground cover that sticks to the floor

"The grass won't stick to the floor" is a rigid card standing plumb on ground
that is not level: an 0.82 m tuft on a one-in-three slope hangs its downhill
corner fourteen centimetres in the air whatever height its centre is set to.
Height was always sampled per tuft; the tilt was never sampled at all. A surface
normal is now read **per cluster** — per 8 m cell for the grass, per instance for
the undergrowth — and `_tiltTo` lays the plant most of the way over toward it,
with the sink scaled by slope × the plant's own width for the rest.

## Ecology

`biomeAt`'s four numbers plus the coast distance now decide the species rather
than altitude and slope alone: conifers on cold damp north-facing ground (aspect
was being read by nothing), pine on the dry exposed and salty, oak in the
sheltered wet valleys, birch standing in for willow and alder at the water's
edge (the atlas has six rows and no seventh for a real willow), scrub thickest
on the dry crests, bracken under closed canopy, deadwood and stumps weighted by
the stand-density field the tree scatter already computes, marram on the beach
and nothing else there. `spec.wet` had sat unread in the species table since it
was written. Saplings cluster under mature trees (a fifth of the stems in a
closed stand) and the salt band gets the wind-shaped crown variant and 46% of
the height.

**Verified before the coast exists.** terrain has not landed its island, so
`_buildCoast` found no sea and every shore rule was inert. `harness/vcoast.mjs`
floods the same site to 20 m above its lowest ground, rebuilds the field and
re-scatters: 1,854 sea cells, 218 in the beach band, 5,526 stems, **0 below the
waterline**, 2,100 in the salt band, and 1,186 marram of which **0 are off the
beach**. The path works; it is waiting on a coastline.

## Screenshots

`shots/isl-summer.png`, `isl-autumn.png`, `isl-winter.png` (wide),
`isl-low-autumn.png`, `isl-low-winter.png`, `isl-top-summer.png`,
`veg9-low2.png`, `veg9-street.png`. Autumn turns the sward to straw and the
broadleaves to rust with the conifers holding green; winter puts snow on needles
and bare limbs on the broadleaves. All from `ctx.world.autumnality` and
`winterliness` — no thermometer anywhere in this file.

New harness: `visl.mjs` (cost + all four faults in one session, walking
placement lists rather than drawn transforms, because a camera-relative probe
only ever measures what that camera can see), `vcoast.mjs`.

## Still weak

- **The soak fails on `edge`**, 20 findings, all "-26 to -52 m step at
  r = 1280–1620 m". That is terrain's new coastline and the check does not know
  about the sea yet. Everything else passes, including `collision 0`,
  `deadRailway 0` and `consoleErrors 0`, which were failing last round.
- **The middle of the island is bald** and it is the site's own fault: pads,
  yard and a ring railway clear nearly everything within 300 m of where the
  cameras stand, which is why a 340 m near radius was buying empty ground.
- **The dense island and the real land disagree by 2.5×** while terrain
  publishes no island. When it does, `_island` picks it up and the two collapse
  into one number — but the density figures above are for a 588 m island and
  will fall if the dense scatter is asked to cover 1.25 km at the same cap.
- The willow at the water's edge is a birch. The atlas is full at six rows.

---

# vegetation.js — round eight (2026-08-07): the outer wood

Ryan, on the running map: *"the draw distance of the trees need to be longer,
add LOD's . or something."*

He is describing something the frame shows plainly. At `cam=low` the forest was
a band of individual trees between roughly two and six hundred metres, and every
hill behind it — the whole middle distance, the far bank, the ridge — was bare
olive ground. The land runs to the horizon and the soak asserts there is no edge
to it; the forest stopped at 620 m. So the eye found the boundary immediately
and the site read as a diorama on a table. `shots/vr-base-wide.png` and
`shots/vr-base-low.png` are the before.

## What was already tried and why it is not the answer

The obvious fix is to raise `HORIZON_RADIUS`, and the round before this one
*lowered* it — 810 → 620 — for reasons that were measured. Three quarters of the
far cards were living past 607 m, and at that range a whole tree averaged into
one mip of one quad renders as flat blue cardboard with a hard silhouette. That
finding is still true. Raising the number would put the same failure back and
would have been the fourth round in a row to do it.

What failed at that range was the *representation*, not the distance. A card
carrying one tree at 800 m is ten pixels of alpha lattice with sky through every
painted gap. A card carrying fifteen is a solid dark mass with a ragged roof,
which is what a wooded hillside looks like from a kilometre and what `tf2-03`
puts on its background ridge.

## What was built: a fourth LOD

| tier | what it is | where | triangles/tree |
|---|---|---|---|
| near + wood | trunk, boughs, ~30 leaf cards | < 175 m | 262 |
| near canopy | crown cards, no trunk | < 265 m (±128 jitter) | 94 |
| far card | 3 crown billboards at 60° | < 620 m (±250 jitter) | 6 |
| **grove** | **4 clump cards, ~15 trees each** | **> 620 m out to 3.0 km** | **0.5** |

The grove is the new one. One painting is a *stand*, not a tree: four ranks of
crowns drawn back to front so the interior is opaque, tops at four heights,
conifers a quarter taller and two thirds as wide as the broadleaves beside them.
Eight of those paintings on a 1024² page of their own (the tree atlas is full —
36 tiles, nothing spare), two-to-one because that is the shape of a wood.

Scattered over a three-kilometre disc by **the same rules as the trees** — the
same stand noise at the same wavelength, the same slope and treeline and
waterline cuts, the same `_openness` clearings. That is what makes the outer
wood the continuation of the near one rather than a separate forest behind it:
the stands line up across the hand-off and the meadows are the same meadows.

## The hand-off, and how it is known not to pop

Two mechanisms, and neither is a screen-space dither — a dither laid across a
whole treeline is a screen door, which is why this file took one out two rounds
ago.

**The boundary is per instance and complementary.** A far tree is drawn while
`d < HORIZON_RADIUS − HORIZON_BAND·jit` and a grove while
`d > HORIZON_RADIUS − HORIZON_BAND·gjit`, off an independent die. Across the
whole band the two sum to one in expectation: at 500 m, 28% of the individual
trees are still drawn and 72% of the groves have arrived, so the wood neither
thins nor doubles as one becomes the other.

**Each grove fades by eroding, not by appearing.** A per-instance coverage
factor (`aVegAlpha`, a new opt-in shader chunk) multiplies the painted alpha
*before* the cutout, so as it falls the alpha test eats the clump inward from
its own silhouette — ragged edges first, then the thin places between crowns,
then the mass. It is one multiply and one float per instance, it is what a wood
dissolving into haze actually does, and it leaves no lattice for FXAA to resolve.
The same term thins the outermost 30% of the range so the forest does not end on
a circle drawn round the camera.

**Verified, not asserted.** `harness/vhand.mjs` renders the *same view*
repeatedly and moves only the point the forest is partitioned from — the camera
is put 30 m away, the LOD sets are rebuilt for that place, and it is put back
before the frame is drawn. Trains, weather, labels and the wind are frozen. So
any difference between two consecutive frames is the hand-off and nothing else,
and 30 m is five times the 6 m at which a real camera triggers a rebuild.

```
                     changed px, per step, worst three of fifteen
outer wood on        4249 / 4306 / 3891   (runs 45 / 37 / 51 px)
outer wood hidden    4685 / 3132 / 3937   (runs 46 / 37 / 52 px)
```

The compact blobs in `shots/vhand-low-diff.png` are **not the new tier** — they
are the same size and in the same places with the groves hidden, i.e. the
pre-existing near↔far tree hand-off. Totalled over the take the grove build
changes *fewer* pixels than the control (22.5k against 35.3k), because the outer
wood stands behind the near treeline and covers some of the sky those pops
reveal. The new tier adds no measurable pop.

## What it costs

Pinned to `ultra`, which is the tier the budget is written against. This matters:
headless chromium is slow enough that the adaptive ladder settles wherever the
machine's load puts it, and two runs of the same build an hour apart came back at
`high` and at `floor` — 393 draws against 279. `vrange.mjs` pins it now.

| camera | scene draws | scene tris | vegetation draws | vegetation tris | groves drawn |
|---|---|---|---|---|---|
| `wide` | 393 → **393** | 2.270 → **2.296 M** | 94 → **101** | 692 k → **706 k** | 978 |
| `yard` | 327 → **366** | 1.956 → **2.015 M** | 86 → **93** | 435 → **435 k** | 536 |
| `low`  | 329 → **345** | 1.934 → **1.974 M** | 93 → **97** | 437 → **448 k** | 1579 |

Against 450 draws / 2.5 M. **Vegetation's own bill grew by seven draws and about
fourteen thousand triangles** for three kilometres of forest — a fortieth of a
triangle per tree against the far card's six, which is the whole reason the
range is affordable at all. Ablated in-session (hide the grove meshes, re-sample
the same page) the tier reads 6 draws and 3.9–15.6 k triangles depending on how
much of the disc is in frustum, which agrees.

Nothing in the near field changed: near, trunk and far instance counts are
identical before and after at all three cameras (1454/888/1172 at `wide`).

**The scene totals moved under me while I worked** — other builders are live in
`rail.js`, `trains.js` and `engine.js`, and `treeRange` changed from 1.35 to 3.20
mid-round — so the whole-scene columns carry their work as well as mine. The
vegetation columns are the number that belongs to this file.

## The quality ladder, finally read

`engine.js` has carried a `treeRange` since it was written and this file never
read it — which is why the forest's extent was identical on a bench PC and on
the wall display, and why `low` shed density and nothing else. It is now the
multiplier on the two tiers that cost nothing per tree. `GROVE_RANGE` is a
**base** of 940 m, not a finished distance; the ladder takes it from 3.0 km at
ultra to 2.1 km at floor. It is clamped, because an unbounded multiplier from a
file this builder does not own is a way for somebody else's edit to put ten
thousand alpha-tested cards in front of the lens.

```
tier     trees  treeRange   near  trunk   far  grove
ultra     1.00      3.20     963    408  1622    649
high      0.95      3.05     938    395  1572    606
medium    0.85      2.85     869    363  1446    521
low       0.70      2.55     723    292  1147    341
floor     0.55      2.20     477    187   759    191
```

The outer wood sheds 71% of its instances and a kilometre of its radius between
ultra and floor, by density *and* by range. The near set is deliberately not
scaled by `treeRange`: it is where every triangle in this subsystem actually is
and it is already against the scene's ceiling.

## The two false starts, both found by looking

Both cost a round of work and both were found by magnifying the frame rather
than by reasoning, which is the discipline this file's notes keep insisting on.

**Rectangles.** The first clump page filled its tile — foliage to both borders
and to the floor. Fine at mip 0 and fatal at mip 4: the mip chain averages a
filled tile to a filled tile, the average passes the cutout across the quad's
whole area, and the frame showed a hillside tiled with pale rectangles with
perfectly straight tops. `harness/vgrove.mjs` (same instant, grove meshes hidden)
put it beyond doubt. Two fixes: the painting is a *lens* — a `destination-in`
ellipse takes coverage as well as height down at the flanks and across the top
corners — and the material's `alphaBias` runs **negative**, so a rising cutoff
shrinks a grove toward its dense core as the mip climbs. The far card's positive
bias exists because a single tree *loses* silhouette to the mip chain; a clump's
failure is the opposite one. `shots/vg1-on.png` → `shots/vg2-on.png`, same crop,
same camera, 10×.

**Scrub.** The first density — a 62 m step, a 44 m card — was one grove per
eleven thousand square metres against a footprint of two: sixteen percent ground
cover. Magnified on the wide camera that is separate green tufts on an open
hillside, i.e. heath, a few hundred metres behind closed canopy. 40 m and a 58 m
card puts it near two thirds. The instance count trebled and the bill did not
move (six draws either way).

## Screenshots

All in `scratchpad/shots/`.

- `vr-base-wide.png`, `vr-base-low.png` — before.
- `vg3-on.png` / `vg3-off.png` — after, and the same instant with only the outer
  wood hidden. This pair is the whole argument.
- `vcanopy1.png` → `vcanopy3.png` — the clump page over a checker, all three
  versions: filled tile, lens, final.
- `vhand-low-diff.png`, `vhand-low-nog-diff.png` — the hand-off and its control.
- `r4-film-sheet.png`, `r4-film-wide-sheet.png`, `r4-film-low-sheet.png` — the
  film gate at all three cameras.

New harness tools: `vrange.mjs` (per-camera cost with the subsystem ablated),
`vgrove.mjs` (grove ablation + distance distribution), `vcanopy.mjs` (dump the
clump page over a checker), `vtier.mjs` (walk the ladder in one session),
`vhand.mjs` (the hand-off with nothing else moving), `vpop.mjs` (a real dolly,
kept because it is the honest version of the same question).

## Still weak

- **The middle distance is pale.** A grove at 1.5 km comes out of the shader
  dark and arrives at the eye three-quarters sky, because sky.js's aerial
  perspective is chromatic and strong. It is the same finding three rounds have
  logged against the far card and it is not fixable in this file — see
  REQUESTS.md, now four times.
- **Seen from directly above the outer wood is thin.** Four vertical cards have
  no roof, so a top-down camera looks between them. `cam=top` is the one
  framing where the tier does not hold up. A fifth, near-horizontal canopy card
  would fix it for two triangles and needs a canopy-*top* painting the page does
  not have.
- **The clumps do not cast.** Deliberate — the cascades are fitted to the site
  and contain nothing at the nearest range a grove is drawn — but it means the
  outer wood puts no shade on its own hillside, and a real wood at a kilometre
  is visibly darker on its north flank.
- **The disc is scattered once, at build.** Nine thousand candidates and 5,751
  groves, about 60 ms. If the plan ever moves the site by more than a few
  hundred metres the disc is off-centre; `onPlan` re-runs the clearing rule over
  the existing groves but does not re-scatter.
- **The soak gate does not pass**, on `deadRailway` in every one of five runs
  including two with vegetation not loaded at all, and on `collision` in
  rail/trains. The grove tier is cleared of both: a run with vegetation loaded
  and `groves.length = 0` — the subsystem exactly as it was before this round —
  returns the identical twenty collisions. Five-run table in REQUESTS.md.
