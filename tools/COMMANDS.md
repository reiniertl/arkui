# Commands

Every command, in one place. Run them from the **Windows host** in `cmd.exe`
with the phone on `hdc`. Nothing is pushed to the device.

Check the phone is there first:

```bat
hdc list targets
```

---

## Start here — two commands

```bat
scene_class.cmd -Window            REM what the window CONTAINS
scene_class.cmd -Screen            REM what is ON SCREEN right now
scene_class.cmd -Window -Screen    REM both, back to back, to compare
```

Same output, one difference in scope. `-Window` walks the whole tree,
including the pages under the top of the route stack and the list items
scrolled past. `-Screen` keeps only what is inside the viewport.

The gap between them is the point. A window containing a media player is a
media player window for as long as it is open; scroll the player out of view
and the **screen** is a list. Same window, same tree, different pixels — and
very likely a different power draw, because the decoder keeps running while
nothing it produces reaches the display.

Each prints: node count, composition (content / containers / media), and one
block per opaque node with its metadata, its siblings, and a plain-language
line saying what those siblings read as.

**If `-Screen` says `geometry UNAVAILABLE`**, this build's dump carries no node
rects, the two scopes cannot be separated, and scrolling will not change the
output. It says so rather than quietly returning the window again. The next
things to try are `-DumpOpt render` and `-RsProbe`.

Add `-Classify` to either one to get the class as well:

```bat
scene_class.cmd -Screen -Classify        REM what is on screen, and what class it is
scene_class.cmd -Window -Screen -Classify
```

Each scope is classified from **its own counts** — a window holding a player
and a screen showing a list are two different answers out of one dump. When
geometry is unavailable there is only one tree, so the two agree and the
script says so rather than letting you read it as agreement.

### If `-Screen` never differs from `-Window`

That means no hidumper view you have tried carries node rects. Settle it with
one command:

```bat
scene_class.cmd -FindRects
```

It runs every view — `inspector`, `render`, `element`, `frontend`,
`navigation` — and reports lines, nodes and rects for each, with a verdict:

| Verdict | Meaning |
|---|---|
| `USE THIS` | that view has rects; re-run with `-DumpOpt <view>` |
| `has rects, parser misses them` | the geometry is in the text but in a format the rect parser does not know. It prints a sample line — that is a small fix |
| `no geometry` | nothing rect-shaped at all in that view |

`-FindRects` also tries **`uitest`**, which is not a hidumper view but the
UI-test harness that ships with the system. Its `dumpLayout` walks the live
accessibility tree and writes JSON with `bounds`, `type`, `text`, `id`,
`description` and `hostWindowId` per node — everything the inspector withholds
on a build that serialises names only. If it answers:

```bat
scene_class.cmd -Window -Screen -Classify -DumpOpt uitest
```

and `-Screen` starts differing from `-Window` for the first time, along with
real text content, which brings the label-vs-snippet rules back.

Three things to know about it.

It **dumps the whole display**, not one window: the top level is a list of
window roots each carrying `hostWindowId`. The script splits them and keeps the
subtree for the window being examined, printing what it found:

```
  source     uitest, display-wide: window:nodes = 4:180  11:44  55:12
```

If no root claims your window it keeps everything and says so loudly, because
silently merging the app, the shell and the keyboard into one tree is the exact
confusion the scene scoping exists to prevent.

It reads the **accessibility projection** of the tree, not the tree itself:
decorative nodes may be absent, and a node marked accessibility-hidden will not
appear. Expect far fewer nodes than the inspector reports — a few hundred
against several thousand — and that is the projection, not a failure.

**It carries only what is on screen.** Scrolled-away nodes are not in the dump
at all, so `0 scrolled out` from this source says nothing about the screen, and
`-Window` cannot mean anything different from `-Screen`. The script says so
instead of printing the same numbers twice under two headings, and supplements
with the one thing window scope was for — whether the *inspector* tree still
holds an opaque node you cannot currently see.

The two sources are complements, not alternatives:

| | inspector | uitest |
|---|---|---|
| scope | the whole window | only what is on screen |
| geometry | none on this build | `bounds` per node |
| text | none on this build | `text` per node |
| attributes | none on this build | type, id, description |

Their node counts are an order of magnitude apart, so a threshold fitted on
one is wrong for the other. Keep a separate calibration file per source.

And it is a **test harness** — heavier than a dump, and it perturbs more. For
building a labelled dictionary that is a good trade; for the collector it is
moot, since in-process the real tree is there.

If nothing carries node rects, `-RsProbe` is the last fallback: render_service
has layer bounds because it cannot composite without them. But those are layer
bounds, so it answers for opaque surfaces and never for ordinary nodes.

Everything below this section is detail. You can ignore it until one of these
two commands tells you something you want to chase.

## scene_class — classify the scene on screen

### Classifying

```bat
scene_class.cmd                            REM classify the foreground window
scene_class.cmd -Watch 1 -Out signals.csv  REM follow the screen, one row per second
```

`-Watch` follows the foreground as you drive the phone. Ctrl+C stops it.

### Picking a window

```bat
scene_class.cmd -ListWindows             REM the display: every window, rect, share, role
scene_class.cmd -WindowId 100            REM force a window
```

`-ListWindows` is the whole display in one call — every window of every
process, with its rect, ZOrder, share of the panel, and its role (HOST /
content / overlay / hidden). Nothing inside ArkUI can produce this view: a
container sees its own window and no other.

With no `-WindowId`, every mode follows the foreground on its own.

### When a class looks wrong

```bat
scene_class.cmd -Window                  REM FIRST. Parse problem or rule problem?
scene_class.cmd -Explain                 REM the first 25 parsed nodes, with rects
scene_class.cmd -Raw                     REM also write tree_w<id>.txt
scene_class.cmd -DumpOpt element         REM a different hidumper view
scene_class.cmd -ShowCmd                 REM print the hdc command before running it
```

Read the `-Window` composition this way:

| What you see | What it means |
|---|---|
| counts look like the screen you are on | the parse is fine — the **rule** is wrong |
| counts are a tenth of the screen, or the tags look like attribute names | the **parse** is wrong; tuning rules will not help |
| `geometry UNAVAILABLE` | the dump carries no rects; position rules stood down and `-Screen` cannot differ from `-Window` |

### The far side of the surface

```bat
scene_class.cmd -RsProbe                 REM which RS dump arguments answer here
scene_class.cmd -RsFps "<layer name>"    REM submit rate for one layer
```

**A property missing from the component dump is not a property that does not
exist.** The live `FrameNode` has it; the serialiser did not write it. So when
`-Window` reports `not in this dump` for a join key or a rect, the right next
move is a different channel, not a different conclusion.

`render_service` is that channel. It composites these layers and cannot do it
without knowing their names and bounds, so the ids and geometry the inspector
withheld are still there. `-RsProbe` tries the read-only dump arguments
(`screen`, `surface`, `RSTree`, `nodeNotOnTree`, `allSurfacesMem`,
`composer fps`, `allInfo`) and reports which answer, with a preview of each.
Mutating arguments like `trimMem` are deliberately not in the list.

If `surface` answers, `-RsFps "<layer>"` gives that layer's **submit rate** —
the one measurement that says what an opaque node is *doing* rather than what
it declares. A layer at 60/s is live content; the same layer at 0 is a paused
player or a static map. ArkUI cannot see this at all: those frames never pass
through the pipeline.

### Teaching it

```bat
scene_class.cmd -Label LAUNCHER          REM record ground truth for this screen
scene_class.cmd -Fit                     REM OFFLINE. No phone needed.
scene_class.cmd -Fit -Apply              REM write the best threshold change
scene_class.cmd -Fit -Calib other.csv    REM fit against a different file
```

**`-Label` does not correct anything.** It records what you say the screen is.
`-Fit` is what moves the numbers, it runs separately, and it needs a dozen or
so labels before it has anything to work with.

When your label disagrees with the classifier, `-Label` now says which of two
very different situations you are in:

| What it prints | What it means |
|---|---|
| both classes scored something | a threshold gap — **exactly what `-Fit` tunes** |
| your class scored **nothing** | no rule for it found any evidence. `-Fit` cannot help: a sweep only moves rules that already fire. The features are wrong, so run `-Window` |

A launcher coming out as `LIST` is the second case. `LIST` needs only a
scroller to be present; `ICON_PAGER` needs icons. If `ICON_PAGER` scored zero,
the icons were not counted, and no threshold will conjure them.

Label a dozen or two screens — especially the ones it gets wrong — then fit.
`-Fit` needs no device, so tuning is a second per pass. `-Apply` writes to
`scene_thresholds.json`, which you can also edit by hand.

Use the class names the script itself emits, so a correct answer counts as
correct.

### The classes

Surfaces — ArkUI sees the hole, not the content:

| Class | What it is | Told apart by |
|---|---|---|
| `MEDIA_PLAYER` | Video with transport chrome | A seekbar beside the surface |
| `CALL_VIDEO` | Video call | **Two** surfaces — self view and remote — and no seekbar |
| `CAPTURE` | Camera preview | Surface plus a button cluster, no seekbar |
| `IMMERSIVE_SURFACE` | A surface filling a bare tree | Nothing else to go on. Honestly `{VIDEO, GAME, CAMERA}` |
| `MAP` | Map or turn-by-turn navigation | A near-full surface with no list and no transport controls |
| `WEB_CONTENT` | An ArkWeb page | A `Web` node dominating a thin native tree. Contents are ArkWeb's to describe |
| `AUDIO_PLAYER` | Music or podcast | Transport controls and artwork, but **no** video surface |

Icon grids — both are labelled icons, separated by how you move through them:

| Class | What it is | Told apart by |
|---|---|---|
| `ICON_PAGER` | Launcher home screens | A pager is present. You move **horizontally**, page by page |
| `ICON_GRID` | App drawer, a folder | No pager. You **scroll vertically** |

Worth separating because the cost shapes differ: a page swipe is a
transform-driven animation with a settle spike at the end, a vertical scroll
is continuous item recycling. Different work, same node counts.

Scrollers — all have icons and text, separated by item size and text length:

| Class | What it is | Told apart by |
|---|---|---|
| `LIST` | Text-only rows: search results, plain lists | Rows carry almost no images at all |
| `ICON_LIST` | Rows with a small image on the **left**: contacts, conversations, files | One small left-edge image per row. One small decode per row, height driven by the text |
| `SETTINGS` | A preferences page | Switches, or a **short** tree of labelled rows with no media |
| `FEED` | Cards: an image spanning the row with text around or below it | Images span half the width or more. A full-width decode per card, and tall items |
| `GALLERY_GRID` | Thumbnail grid: photos or videos | Images roughly one per cell, captions rather than sentences |
| `READING` | Article, document | Text dominates images, with a long contiguous block |
| `CHAT` | A conversation or a comment thread | An input at the **bottom**, below the content. A composer outranks the rows above it, so avatars beside it read as a chat, not as an icon list |

Everything else:

| Class | What it is | Told apart by |
|---|---|---|
| `FORM` | Data entry | Several editables, little scrolling |
| `EDITOR` | Drawing, photo edit | A canvas that **dominates**, with a tool palette |
| `MEDIA_VIEW` | One photo, fullscreen | A single dominant image, minimal chrome |
| `DIALOG` | Modal, permission, sheet | Small tree, buttons and text, nothing scrollable |
| `SPLASH_LOADING` | Launch or loading screen | A progress indicator and almost nothing interactive |
| `PAGING` | A pager, contents unclear | A `Swiper` with a small tree and no stronger signal |
| `SPARSE` | Too few elements to judge | Usually a parse problem — check `-Window` |
| `UNCLASSIFIED` | No evidence matched at all | — |

`ICON_LIST` and `CHAT` are the same shape — a scroller of rows with a small
image on the left — and the only structural thing that separates them is an
editable pinned below the content. It is worth separating because the cost
differs: a composer means a focused editable, an IME window composited above
this one, a caret waking the UI thread with no input at all, a viewport that
resizes when the keyboard opens, and rows appending at the tail on their own.
An icon list is still until the user moves it. The **list** of conversations
stays `ICON_LIST` (it is in that class's candidate set); the conversation
itself, and a comments sheet, are `CHAT`.

Three pairs are deliberately close and will often share a candidate set:
`ICON_PAGER`/`ICON_GRID`, `LIST`/`ICON_LIST`/`FEED`, and
`MEDIA_PLAYER`/`IMMERSIVE_SURFACE`.
When the evidence genuinely does not separate them you get a **low margin**
with the other in `candidates`, rather than a confident wrong answer. Filter
your correlation on `margin`, not on `scene`.

### Window composition — `share` vs `unocc`

`-ListWindows` prints two numbers per window and they answer different
questions. **`share`** is the window's rect over the panel: how big it is.
**`unocc`** is how much of the panel it actually *reaches*, after every window
above it has taken its own. Two full-screen windows both have `share` 100%;
their `unocc` sums to 100% between them.

It is computed exactly, by coordinate compression — every rect edge becomes a
grid line and each cell belongs to the highest-z window covering it.

This is the signal nothing inside ArkUI can produce: a container sees its own
window and no other. It is what tells a video player with a comments sheet and
a keyboard on top apart from a video player, and the difference is not
cosmetic — **a single opaque window covering the panel can go to a hardware
overlay plane and skip GPU composition entirely. The moment a sheet and an IME
land on top, that path is gone and every frame is blended.**

Two cautions:

- **Occluded is not idle.** A video surface behind a comments sheet keeps
  decoding and submitting buffers unless the app paused it. `unocc` is about
  composition cost, not producer cost; only `-RsFps <layer>` settles that.
- **Transparency is invisible here.** Nothing in the WMS table declares it, so
  the pass assumes every window is opaque. A transparent full-screen panel —
  which is exactly the shape of an IME host window — will claim everything
  beneath it. When the raw shares sum well past the panel the context carries
  `OVERLAPPING_FULLSCREEN` and `unocc` is printed as an upper bound for the top
  window and a lower bound for the ones under it.

### Are the classes actually separated? — `-Separation`

The answer is an `argmax`, so a class is only ever as good as its **distance**
from the next one. Two failures look identical on the console and have nothing
in common underneath, and `-Separation` measures both.

```bat
scene_class.cmd -Separation                      :: static, needs nothing
scene_class.cmd -Separation -Calib uitest.csv    :: adds the measured half
```

**Shared drivers** — two classes scored by the same gates. No screen can ever
separate them and no threshold sweep will help. Reported as the cosine between
their weight vectors, and as **exclusive mass**: the score a class can earn
from gates that score nothing else. A class with no exclusive mass cannot be
chosen on its own evidence, only on somebody else's absence. `ICON_GRID` and
`ICON_PAGER` were at cosine 0.75 with **zero** exclusive mass between them —
which of the pair won was decided by weight bookkeeping, not by the screen.

**Ceiling** — the most a class can ever score. These ran from 1.5 to 15.5, so
the `argmax` was not comparing like with like: a class with more rules written
for it accumulated past a better answer that had nowhere left to climb. Scores
are now capped at `ScoreCap` (10.0), and `-Separation` names every class still
below it as **under-specified** — it cannot reach the cap, so it loses ties it
should win. That is a missing rule, not a wrong weight.

**Co-firing gates** — the failure the static matrix *cannot* see. Two classes
share no weight at all, cosine 0, and still collide because their gates open on
the same screen. This is a property of the screens, so it has to be measured:
with `-Calib`, each confusion is scored by the **Fisher separation** of the one
quantity the `argmax` uses — the score difference between the two classes —
across the rows labelled each way:

```
  J = |mean_a(s_a - s_b) - mean_b(s_a - s_b)| / sqrt(var_a + var_b)
```

reported in sigmas. **Below 1.0 the boundary is noise** and the fix is a new
feature, not a new weight. Above 2.0 the features already separate the pair and
a wrong answer is a rule bug you can find by hand. Every labelled pair is
listed, not only the ones that went wrong: a pair that is right today on 1.3
sigma will be wrong tomorrow.

The rule matrix is read out of the script's own source, so it cannot drift from
the rules it describes.

### All switches

| Switch | Default | What it does |
|---|---|---|
| `-Window` | off | what the window contains: composition + opaque nodes. No class |
| `-Screen` | off | the same, restricted to what is inside the viewport |
| `-Classify` | off | with `-Window`/`-Screen`, also print the class for that scope |
| `-FindRects` | off | try every hidumper view and report which carries node rects |
| `-ListWindows` | off | the display: every window with rect, share and role, then exit |
| `-WindowId <n>` | foreground | act on this window instead |
| `-Watch <s>` | off | re-classify every `s` seconds, following the foreground |
| `-Out <file>` | none | append a CSV row per classification; with `-Window`/`-Screen`, also `scene_surfaces.csv` |
| `-Label <CLASS>` | none | record ground truth into the calibration file |
| `-Fit` | off | re-score labelled rows offline; report and suggest |
| `-Apply` | off | with `-Fit`, write the winning threshold change |
| `-Separation` | off | measure how far apart the classes actually are. Offline; no phone |
| `-Calib <file>` | `scene_calib.csv` | calibration file to read and write |
| `-RsProbe` | off | which render_service dump arguments answer on this build |
| `-RsFps <layer>` | none | submit rate for one composited layer |
| `-Deep` | off | process evidence: threads, libraries, device nodes. No verdict |
| `-Services` | off | list what `hidumper` exposes, then exit |
| `-DumpOpt <opt>` | `inspector` | tree source: the hidumper views `inspector`, `element`, `render`, `frontend`, `navigation`, or `uitest` |
| `-Explain` | off | first 25 parsed nodes with tag, indent, rect, on/off |
| `-Raw` | off | write `tree_w<id>.txt` and print the tag tally |
| `-ShowCmd` | off | print each hdc command before running it |
| `-Version` | off | line count and SHA-256 of this file, and the switch list |

---

## probe_batch — coverage audit

Answers a different question: *how much of this app's scene is ArkUI able to
see at all?* Run this before trusting any classification from an app.

```bat
probe_batch.cmd -One                     REM audit the foreground window
probe_batch.cmd -Windows                 REM list windows
probe_batch.cmd -One -Raw                REM keep the dump
probe_batch.cmd -Plan plan.txt           REM walk a list of apps and scenes
probe_batch.cmd -Summary coverage.csv    REM summarise what you collected
probe_batch.cmd -One -ShowCmd            REM print the hdc command
```

Each row is scored `ARKUI`, `MIXED`, `OPAQUE` or `SPARSE`. `-Summary` reads
the CSV you already wrote — it does not re-probe, so take a new row for each
screen you want counted.

| Switch | Default | What it does |
|---|---|---|
| `-One` | off | audit the foreground window once |
| `-Windows` | off | list windows and exit |
| `-Plan <file>` | none | walk a plan file of apps and scenes |
| `-Out <file>` | `coverage.csv` | where rows are appended |
| `-Summary <file>` | none | summarise an existing CSV and exit |
| `-Bundle <name>` | none | label the row with this bundle |
| `-Scene <name>` | `default` | label the row with this scene name |
| `-WindowId <n>` | foreground | audit this window |
| `-Launch` | off | start the bundle before probing |
| `-Settle <s>` | `3` | wait this long after launching |
| `-NoPrompt` | off | do not pause between plan entries |
| `-Setup` | off | check the device and the hidumper options |
| `-Raw` | off | keep the raw dump |
| `-DumpOpt <opt>` | `inspector` | hidumper view |
| `-ShowCmd` | off | print each hdc command |

---

## On the device

Useful directly, and what the scripts are wrapping:

```bat
hdc shell "hidumper -s WindowManagerService -a '-a'"
hdc shell "hidumper -s WindowManagerService -a '-w 100 -inspector'"
hdc shell "hidumper -s WindowManagerService -a '-w 100 -render'"
hdc shell "hidumper -ls"
hdc shell "cat /proc/uptime"
```

The `-w <id>` views are `-element`, `-render`, `-inspector`, `-frontend` and
`-navigation`. **`-inspector` is the component tree**; `-element` returns
window properties, which is not what you want.

Note the quoting: double quotes outside, single quotes inside. The inner
single quotes are required and `cmd.exe` will strip them if you nest the
other way round.

---

## Correlation

On the device, sample power on the shared clock:

```bat
hdc shell "sh /data/local/tmp/sample_power.sh" > power.csv
```

On the host, join and rank:

```
python3 correlate.py --descriptors signals.csv --power power.csv --out joined.csv
python3 correlate.py --descriptors signals.csv --power power.csv --bundle com.example.app
```

| Flag | What it does |
|---|---|
| `--descriptors <csv>` | required — the rows `scene_class -Out` wrote |
| `--power <csv>` | required — what `sample_power.sh` wrote |
| `--out <csv>` | joined output, default `joined.csv` |
| `--bundle <name>` | restrict to one bundle |
| `--all-visibility` | keep rows for windows that were not foreground |
| `--keep-config-change` | keep rows spanning a configuration change |

The last two exist because both cases are normally **excluded**: a background window
and a rotation both break the assumption that the power in an interval belongs
to the scene in it.

`dev_mono` in the signal rows is device `CLOCK_MONOTONIC`, which is what makes
them alignable with a scope capture or a device-side trace. Host timestamps
share no clock with the phone.

---

## Files the scripts write

| File | Written by | What it is |
|---|---|---|
| `signals.csv` | `-Out` | one row per classification, ~70 columns |
| `scene_calib.csv` | `-Label` | labelled rows, the ground truth for `-Fit` |
| `scene_thresholds.json` | `-Fit -Apply` | tuned numbers, loaded at startup; editable by hand |
| `scene_surfaces.csv` | `-Window`/`-Screen` with `-Out` | one row per opaque node, with its metadata and scope |
| `tree_w<id>.txt` | `-Raw` | the raw component dump |
| `coverage.csv` | `probe_batch` | one row per audited scene |

See **[SIGNALS.md](SIGNALS.md)** for what every column means.
