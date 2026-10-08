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

If no view carries them, `-RsProbe` is the fallback: render_service has layer
bounds because it cannot composite without them.

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
| `IMMERSIVE_SURFACE` | A surface filling a bare tree | Nothing else to go on. Honestly `{VIDEO, GAME, MAP, CAMERA}` |
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
| `LIST` | Settings, contacts, any dense list | Small icons, short labels. Switches or a search field at the **top** |
| `FEED` | Cards, timeline | **Large** media items or **snippet**-length text |
| `GALLERY_GRID` | Photo grid | A grid where images outnumber text, or carry no captions |
| `READING` | Article, document | Text dominates images, with a long contiguous block |
| `CHAT` | Conversation | An input at the **bottom**, below the content, with message-length text |

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

Three pairs are deliberately close and will often share a candidate set:
`ICON_PAGER`/`ICON_GRID`, `LIST`/`FEED`, and `MEDIA_PLAYER`/`IMMERSIVE_SURFACE`.
When the evidence genuinely does not separate them you get a **low margin**
with the other in `candidates`, rather than a confident wrong answer. Filter
your correlation on `margin`, not on `scene`.

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
| `-Calib <file>` | `scene_calib.csv` | calibration file to read and write |
| `-RsProbe` | off | which render_service dump arguments answer on this build |
| `-RsFps <layer>` | none | submit rate for one composited layer |
| `-Deep` | off | process evidence: threads, libraries, device nodes. No verdict |
| `-Services` | off | list what `hidumper` exposes, then exit |
| `-DumpOpt <opt>` | `inspector` | hidumper view: `inspector`, `element`, `render`, `frontend`, `navigation` |
| `-Explain` | off | first 25 parsed nodes with tag, indent, rect, on/off |
| `-Raw` | off | write `tree_w<id>.txt` and print the tag tally |
| `-ShowCmd` | off | print each hdc command before running it |

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
