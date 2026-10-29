# arkui — scene extraction from ArkUI data

Research. The question is whether the structure of what is on a HarmonyOS
display can be measured cheaply, from RenderService dumps alone, and reported
as it is.

That is the whole of it. The repository extracts scenes from ArkUI data and
measures one vector over them. What any consumer does with those signals —
aggregation, power attribution, scheduling — is decided elsewhere, by
something that is not here and whose questions are not asked here.

If the measurement holds up, the end of this is a system component: ArkUI is
platform code, so patching it once reaches every app through the pre-warmed
`appspawn` fork — no app touched, recompiled, or aware. That component would
report scene composition and nothing else.

That component does not exist yet, and this repository is not it.

## What is here

A capture that pulls RenderService dumps off a device, and a Python pipeline
that builds each dump's scene in one pass, measures the motif vector on it, and
clusters the scenes once over the motif vector.

```
rs_capture.ps1          device  ->  *.rstree.txt, *.client.txt, display.txt
  rs_extract.py                     index the dump, parse a node when it is reached
  rs_scene.py                       the scene in one pass, and the motif vector on it
  rs_pattern.py                     run the pass on every dump  -> patterns.csv
  vectors_build.py                  motif groups, one clustering -> two_payload.json
  vectors_page.py                   the viewer, three tabs -> scene_feature_reference.html
```

**The scene** — the display's kept tree. One descent from the dump's
`LOGICAL_DISPLAY_NODE` keeps exactly the nodes with visible cells on the
display, by alpha alone, and the containers that hold them, in their
containment. Invisible windows, alpha-0 branches, and branches clipped or
occluded away are dropped without being read.

**The motif vector** — 21 motifs, each derived from an ArkUI component
contract (the "Child Components" section of the OpenHarmony ArkUI reference)
and, where the engine builds the component differently, its runtime form in
arkui_ace_engine; never from a capture. The repeating containers (list, grid,
waterflow, tabs, carousel, stepper, menu, grid_row), the fixed-shape ones
(scroll_page, refresh, sidebar), the leaves (video of three sizes, web,
text_input, button, slider, index_bar) and two icon structures, matched in the
same pass: per motif its distance (1 − Jaccard at its closest root), the share
of the display its matches show, and their count written n/(n+1) - 63 values
in a fixed order. A motif absent from a capture stays and reads d = 1.

Nothing else is computed: how a node is rendered is not this project's
question. The vector is a measurement, not a class.

## No scene classes

There are none, deliberately, and three separate attempts at one have been
deleted:

- a C header declaring twenty scene classes, written before any data existed;
- a PowerShell classifier scoring twenty-three hand-written classes against
  hand-tuned thresholds;
- a naming function that sorted every measured repeat into one of twenty-eight
  pre-written shapes and printed the name beside the result.

Each was a taxonomy asserted in advance and then looked for in the data. The
clustering is not here to confirm a class list. It is given the vectors and
nothing else, and a cluster is reported by its id, its size, its cohesion and
the median of its own columns. What the groups mean, if anything, is read off
the tiles by a person looking at them.

## Layout

| Path | What |
|---|---|
| `tools/rs_capture.ps1` | the device side. Pulls dumps, computes nothing |
| `tools/cluster/rs_extract.py` | parse a RenderService dump into a tree, with each node's rectangle |
| `tools/cluster/rs_scene.py` | the scene in one pass, and the motif vector on it |
| `tools/cluster/rs_pattern.py` | run the pass on every dump of a capture |
| `tools/cluster/vectors_build.py` | cluster each vector, build the page payload |
| `tools/cluster/vectors_page.py` | render the payload |
| `tools/deck/` | the slides describing data, origin and results |
| `combobul/` | the spec engine (see `CLAUDE.md`) |

Captures live on the capture machine and **never** in this repository: they are
screenshots of a real phone in real use.

## Running it

```powershell
# on the capture host, phone attached, hdc on PATH
.\tools\rs_capture.ps1 -Seconds 600 -Dir $HOME\ark_work\captures
```

```sh
# anywhere, against those files
python tools/cluster/rs_pattern.py    captures --out patterns.csv
python tools/cluster/vectors_build.py patterns.csv --captures captures
python tools/cluster/vectors_page.py  two_payload.json --out scene_feature_reference.html
```

`tools/cluster/requirements.txt` pins what the Python needs.
