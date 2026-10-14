# Clustering captured screens

Turns a capture session into a set of measured screen shapes, and tells you
where ArkUI cannot see.

The classifier in `scene_class.ps1` is a **distilled form**: twenty-three class
names written by hand before any data existed. This does it the other way
round — record what real screens look like, group them on geometry alone, and
let the class set fall out of the groups.

## Two rules it is built around

**Cluster screens, not apps.** One app shows a video player, a chat, a settings
page and a feed, often from a single window — TikTok's three states come out of
one *view*. Nothing about the app predicts the screen, so nothing identifying
the app enters the vector. The package name is used exactly once, in the
purity alarm, *after* the clustering.

**The hand-written taxonomy is held out.** `shape` is a guess at the answer.
Feeding the guess in and reading it back out proves nothing, so it is scored
against the clusters instead. A cluster that is cleanly one shape confirms it;
a cluster split between two says those two are really one; one shape spread
over four clusters says it is too coarse.

## Running it

```
scene_class.cmd -Capture 1800 -DumpOpt uitest     on the box with the phone
cluster.cmd captures\20261014_101500
```

`cluster.cmd` creates its own virtual environment on first run and then just
runs. If that machine has no package index:

```
pip download -r requirements.txt -d D:\wheels     on a machine that does
set ARKUI_WHEELS=D:\wheels
cluster.cmd captures\20261014_101500
```

Then open `viewer.html` inside the capture directory. No server, no network —
it is a plain file, and `clusters.js` is loaded as a script rather than
fetched because `file://` blocks `fetch()` but not `<script src>`.

## The vector

| Group | Columns | Transform |
|---|---|---|
| repetition | `rep_frac`, `rep_n`, `cols` | per-mille; `log1p` on the count; `cols` clipped at 6 |
| item geometry | `item_h`, `item_aspect` | per-mille; **log** on the aspect, so 0.5 and 2.0 sit equally far from square |
| division | `split_n`, `split_ratio`, `overlay` | per-mille, `split_n` clipped at 4 |
| anchors | `bar_top`, `bar_bot`, `bar_edit`, `pager` | per-mille; the two flags down-weighted |
| surface | `surf_frac`, `surf_visible`, `cover_web`, `cover_xc`, `cover_emb` | per-mille |

Every column is already normalised against the viewport, so no pixel count and
no panel size reaches the clusterer.

Weights exist because standardisation alone inflates a rare binary flag into a
dominant axis: `bar_edit` is 1 on perhaps a twentieth of screens, so its
z-score swings by 4.5 while `rep_frac` swings by 1.

**Held out, never clustered on:** `shape`, `split_main`, `scene`, `window`,
`pid`, `described`. Also every component tag count — those are what the current
classifier uses, and they are exactly what makes one app's screens resemble
each other.

## Algorithm

`sklearn.cluster.HDBSCAN`, `min_cluster_size` defaulting to 2% of distinct
screens. Three properties matter here specifically:

- **It is not told K.** Fixing K assumes the answer, and the answer is the
  question — whether five shapes is right, or three, or nine.
- **Variable density.** `ROWS` will be a tight ball of thousands; a map view
  will be forty points in a loose one. k-means splits the ball and swallows the
  sparse cluster.
- **It labels outliers** instead of forcing them. The outliers are the find —
  a screen that fits no cluster is either a shape nobody listed or a screen
  ArkUI cannot describe.

Validated with silhouette, reported in the header.

## Deduplication

One entry per distinct layout signature; the number of samples sharing it
becomes its **dwell**. Without this, half an hour parked on one feed outvotes
every other screen in the session, and the clusters describe how the phone was
used rather than what screens exist.

## The purity alarm, and what it is not

A cluster that is 90% one app gets looked at, but purity alone flags two very
different things — and loudly flagging both made it useless. In test it fired
on eight clusters of twelve when exactly one app was the planted trap.

The real signature of a design language is **not** one cluster. It is one app
carved out *across several shapes at once*, while other apps produce those same
shapes together elsewhere. So the test is per app:

- the app is alone in ≥2 clusters, spanning ≥2 different hand-shapes
- ≥80% of that app's screens are in those app-only clusters
- other apps produce those shapes elsewhere

One app alone in one cluster is reported separately and much more quietly: it
usually means nobody else in the session showed that shape, which is a gap in
the capture, not a fault in the clustering.

## The output that matters most

With a screenshot beside every dump, the blind spot becomes measurable instead
of arguable:

- what fraction of real screens, in real use, are describable from the tree
  alone — split by Web / XComponent / embedded, which is exactly the producer
  routing decision
- clusters that are tight in feature space but visually heterogeneous: ArkUI
  cannot tell apart screens that look different, which is the worse failure

Nothing else in this pipeline produces that number.

## Screenshots never enter this repository

It is public. `captures/`, every image extension, `clusters.js`, `thumbs/` and
the row files are all excluded in `.gitignore`. `viewer.html` and `cluster.py`
are code and live here; everything they read stays on the machine that drives
the device.
