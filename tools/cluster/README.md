# The two vectors

Turns a capture session into two measurements per scene, clusters each on its
own, and puts every scene on one page.

```sh
python rs_pattern.py    ../../captures --out patterns.csv
python vectors_build.py patterns.csv --captures ../../captures
python vectors_page.py  two_payload.json --out vectors.html
```

| | |
|---|---|
| `rs_extract.py` | parse a RenderService dump: tree, surfaces, paint order, ownership |
| `rs_pattern.py` | measure both vectors, one row per scene |
| `vectors_build.py` | cluster each vector, embed the screenshots, write the payload |
| `vectors_page.py` | render the payload to a single page |

`rs_extract.py` is a library now. It used to be a second feature extractor —
a port of a PowerShell one, 26 columns wide, three of them constant and six
pruned as redundant. All of it is gone; what remains is the parsing that
`rs_pattern.py` stands on.

## Pattern vector

What repeats in the layout, and how much of the window it holds.

Branches are grouped by a description of their subtree — the kinds of content
in it and how those are arranged — and the group covering the most area wins.
Ties break on vertical position rather than on the area they are nearly tied
on, so two similar groups cannot swap slots between samples and jump the whole
vector.

| column | transform | weight | measures |
|---|---|---|---|
| `pat1_cover` | unit | 1.0 | share of the window that repeats |
| `pat1_h` | unit | 1.0 | the repeating item's height, in display heights |
| `pat1_aspect` | logratio | 1.0 | its width / height |
| `pat1_cols` | clip6 | 1.2 | distinct columns |
| `pat1_n` | log1p | 0.7 | members in the group |
| `overlay_frac` | unit | 0.7 | area drawn over the layout |
| `pat_n_groups` | log1p | 0.7 | how many repeating groups exist |

A second group is measured into `pat2_*` and `has_slot2`, and is not in the
vector. An absent second group writes **empty, not zero**: a zero coverage and
a zero height would place it at the small end of both scales instead of
nowhere, which is a mistake an earlier column made on 1,266 samples.

## Surface vector

What the display is finally made of, after occlusion.

Every node that draws content, and every externally composed surface, walked
topmost first, each claiming only the pixels nothing above it claimed.
Substrate is the complement — it does not compete, because a background will
win any contest it is entered into.

| column | transform | weight | measures |
|---|---|---|---|
| `vis_xcomp` | unit | 1.0 | externally composed surface |
| `vis_image` | unit | 1.0 | nodes tagged `Image` |
| `vis_text` | unit | 1.0 | nodes tagged `Text` |
| `vis_content` | unit | 0.7 | draws, but no tag names the type |
| `vis_substrate` | unit | 0.7 | claimed by nothing: background, chrome, blank |

**This one is not yet trustworthy.** On the captures measured so far it
collapses towards a single axis — substrate correlates about −0.8 with content —
and the silhouette rewards a split that a person looking at the tiles would
not. It is reported because it separates an external surface holding the
display from everything else, which is real, and the rest of it is not settled.

## No class names anywhere

Nothing here names a scene. A cluster is reported by its id, its size, its
cohesion and the median of its own columns; a scene by the values measured
from it. The clustering is given the vectors and nothing else.

This is deliberate and it is a reversal. A naming function used to sort each
measured repeat into one of twenty-eight pre-written shapes — seven cell kinds
crossed with four arrangements — and print the name beside the cluster. The
name was never an input to anything; it was a pre-conditioned taxonomy
travelling alongside the measurement and getting read as if it were one.

## Reading the page

Every scene appears, ordered from its cluster's centre outwards, so the last
tiles in a row are where the cluster frays. Scenes fitting no cluster are
reported in their own row rather than forced into the nearest group.

## Inputs that are not in this repository

`--captures` is the capture tree. A scene is a photographed sample
(`rs_sample.py`, ALG-PICTURE-PAIR): a picture named in `samples.csv` and the
render tree read in that same sample, kept only when the display is unchanged
in the next sample. Its picture is `<captures>/<shot>`, its own, so every
tile has one. Every picture that became no scene is in `patterns.lost.csv`
with its reason. Screenshots are pictures of a real phone in real use and
never enter the repository, so the tree lives on the capture machine.
