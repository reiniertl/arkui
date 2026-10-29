# Tools

Two steps. The device side pulls files; everything after that is Python.

| | Runs on | Purpose |
|---|---|---|
| `rs_capture.ps1` | host | Pull RenderService dumps and screenshots. Computes nothing |
| `cluster/` | anywhere | Measure the motif vector, cluster it, build the page |
| `deck/` | — | Slides: what the data is, where it comes from, what came out |

## Prerequisites

- `hdc` on PATH and a reachable device (`hdc list targets`).
- Shell privilege on the device for `hidumper`. Fine on an engineering build;
  likely restricted on a locked retail device.

## Capture

```powershell
.\rs_capture.ps1 -Seconds 600 -Dir $HOME\ark_work\captures
```

One render-tree read per sample, about 0.3s each. Nothing is computed from it
on the host: the loop pulls both dumps, decides from the raw text whether this
is a scene already seen, and only a new scene pays for storage and a picture.

It writes, per session:

| | |
|---|---|
| `<nnnn>.rstree.txt` | the render tree of a sample the capture judged new |
| `<nnnn>.client.txt` | `frameNodeTag` read at that sample, so a render node can say what component it is |
| `<nnnn>.jpeg`, `p<nnnnnn>.jpeg` | a picture, named on the row of the sample it was taken in |
| `raw/<sample>.rstree.txt` | the render tree of every sample |
| `samples.csv` | the index: `raw`, `stem`, `sig`, `shot` |

Downstream, a scene is a photographed sample: a row with a `shot`, its picture
and `raw/<raw>.rstree.txt`, tagged from the last client dump at or before it
(`cluster/rs_sample.py`). The capture's own notion of a new scene is not used.
| `display.txt` | geometry, density, rotation, cutout |

The two dumps join by node id, exactly, because both come from the same service
microseconds apart.

**Why the capture is still PowerShell.** Only because it works and has been
used for every capture so far. Nothing in it needs PowerShell — the device
surface is eight `hdc` commands and Python's `subprocess` reaches them on the
capture host — so this file is a port away from disappearing.

## Measure

See [cluster/README.md](cluster/README.md). In short:

```sh
python cluster/rs_pattern.py    captures --out patterns.csv
python cluster/vectors_build.py patterns.csv --captures captures
python cluster/vectors_page.py  two_payload.json --out scene_feature_reference.html
```

