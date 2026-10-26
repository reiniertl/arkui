# arkui — scene signals for power work

Research. The question is whether the structure of what is on a HarmonyOS
display can be measured cheaply enough to be worth reporting, and whether that
measurement carries information about power draw and CPU frequency that the
scheduler does not already have.

If it does, the end of this is a system component: ArkUI is platform code, so
patching it once reaches every app through the pre-warmed `appspawn` fork — no
app touched, recompiled, or aware. That component would report scene
composition and nothing else. **Aggregation, power attribution and scheduling
are decided elsewhere.** The quality of the data is what is decided here.

That component does not exist yet, and this repository is not it.

## What is here

A capture that pulls RenderService dumps off a device, and a Python pipeline
that measures two things from each dump and clusters each on its own.

```
rs_capture.ps1          device  ->  *.rstree.txt, *.client.txt, display.txt
  rs_extract.py                     parse the dumps into a tree
  rs_pattern.py                     measure the two vectors   -> patterns.csv
  vectors_build.py                  cluster each              -> payload.json
  vectors_page.py                   every scene, on one page  -> vectors.html
```

**The pattern vector** — what repeats in the layout, and how much of the window
it holds. Branches are grouped by a description of their subtree, and the group
covering the most area wins. Seven columns: coverage, item height, item aspect,
column count, group size, overlay area, number of groups.

**The surface vector** — what the display is finally made of, after occlusion.
Every node that draws, and every externally composed surface, walked topmost
first, each claiming only the pixels nothing above it claimed. Five columns
summing to one: external surface, image, text, untyped content, substrate.

Both are measurements. Neither is a class.

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

## The open question

`correlate.py` states the premise the whole thing rests on and **has never been
run**. It joins scene records to power and frequency samples and asks which
features predict them, with a lead check — a feature that only correlates at
lag zero describes a present the governor already sees from utilisation.

Until it has an answer, every other decision here is downstream of something
unmeasured.

## Layout

| Path | What |
|---|---|
| `tools/rs_capture.ps1` | the device side. Pulls dumps, computes nothing |
| `tools/cluster/rs_extract.py` | parse a RenderService dump into a tree |
| `tools/cluster/rs_pattern.py` | measure the two vectors |
| `tools/cluster/vectors_build.py` | cluster each vector, build the page payload |
| `tools/cluster/vectors_page.py` | render the payload |
| `tools/correlate.py` | the premise: do these features predict power |
| `tools/sample_power.sh` | device-side cpufreq / cpuidle sampling |
| `tools/deck/` | the slides describing data, origin and results |
| `combobul/` | the spec engine (see `CLAUDE.md`) |

Captures live on the capture machine and **never** in this repository: they are
screenshots of a real phone in real use.

## Running it

```powershell
# on the capture host, phone attached, hdc on PATH
.\tools\rs_capture.ps1 -Seconds 600 -Dir C:\ark_ui\captures
```

```sh
# anywhere, against those files
python tools/cluster/rs_pattern.py   captures --out patterns.csv
python tools/cluster/vectors_build.py patterns.csv --reps patreps --out payload.json
python tools/cluster/vectors_page.py  payload.json --out vectors.html
```

`tools/cluster/requirements.txt` pins what the Python needs.
