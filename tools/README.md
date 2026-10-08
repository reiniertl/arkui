# Tools

**Looking for a command? [COMMANDS.md](COMMANDS.md) has every one of them with
examples.** This file is the workflow those commands belong to.

| | Runs on | Purpose |
|---|---|---|
| `scene_class.ps1` / `.cmd` | host | Classify the scene on screen, live. The main tool |
| `probe_batch.ps1` / `.cmd` | host | Audit how much of an app's scene ArkUI can describe |
| `correlate.py` | host | Join descriptors to power, integrity-check, rank features |
| `sample_power.sh` | device | Sample cpufreq / cpuidle on the shared clock |
| `scene_probe.sh` | device | Device-side single-window probe. Superseded by `probe_batch` |

The first two answer **"is this project viable on my app set?"** before you
write any collector code. The last three are the measurement phase.

## Prerequisites

- `hdc` on PATH, a reachable device (`hdc list targets`).
- Shell privilege on the device for `hidumper` and `param set`. Fine on an
  engineering build; likely restricted on a locked retail device.
- The ArkUI dump path enabled, **and every target app restarted afterwards**.
  The path is wired at app start, so an already-running app produces an empty
  dump and no error worth reading.

---

## Phase 1 — coverage audit

### Why first

An app whose UI is rendered into a surface it owns is invisible to tree
instrumentation. If a large share of your target scenes turn out that way, the
whole approach needs rescoping — and you can find out in an afternoon instead
of after writing a collector.

Coverage also varies **by screen within one app**: a video app's feed may be
ArkUI-rich while its player is a surface. So the unit is `(app, scene)`.

### Setup

```sh
./probe_batch.sh --setup
```

Pushes the probe to `/data/local/tmp` and sets `persist.ace.debug.enabled`.
Then force-stop and relaunch anything you intend to probe.

### One window at a time

```sh
hdc shell sh /data/local/tmp/scene_probe.sh                 # foreground
hdc shell sh /data/local/tmp/scene_probe.sh -l              # list windows
hdc shell sh /data/local/tmp/scene_probe.sh -w 28 -s player
```

With no `-b` it probes whatever is on screen, so you can drive the phone by
hand and just label what you see.

```
bundle    : com.example.app
window    : 28        scene: list
elements  : 412
opaque    : 0   (Web 0, XComponent 0)
content   : Text 180, Image 120, Video 0, scrollers 3, inputs 0
engine so : -
verdict   : ARKUI   (full coverage)
```

### A whole plan

Copy `plan.example.txt`, replace the placeholder bundles with real ones, then:

```sh
./probe_batch.sh --plan plan.txt --out coverage.csv
```

It prompts before each row so you can navigate to the scene. `s` skips, `q`
stops early. Add `--launch` to auto-start rows that name an ability — useful
for landing screens, useless for anything deeper.

```sh
./probe_batch.sh --summary coverage.csv    # re-print without re-running
```

### Reading the verdicts

| Verdict | Meaning | What it implies |
|---|---|---|
| `ARKUI` | full coverage | instrument and go |
| `MIXED` | a `Web` or `XComponent` alongside a real tree | needs the ArkWeb record, or outside-only description |
| `THIN` | native shell around a surface | attributes only — area, submit rate, bound producer |
| `BLIND` | surface-rendered | the tree sees nothing; exclude or accept the gap |
| `SPARSE` | few elements, no opaque node | almost always a parse problem — check with `-k` |

The resulting table is your go/no-go, and it sizes the ArkWeb work with data
rather than argument.

---

## Phase 1b — scene signals

Once the audit says a scene is visible, `scene_class.ps1` extracts the signals
and classifies it. Same plumbing, nothing pushed.

```bat
scene_class.cmd                        REM foreground window
scene_class.cmd -ListWindows           REM see the table, check the pick
scene_class.cmd -Watch 1 -Out signals.csv
scene_class.cmd -WindowId 100 -Raw
scene_class.cmd -Label LIST             REM record ground truth for -Fit
scene_class.cmd -Explain                REM the first 25 parsed nodes, with rects
```

### What is in the window, and what is on screen

Two commands, same output, one difference in scope — and no class printed by
either. `-Window` walks the whole tree, including pages under the top of the
route stack and list items scrolled past. `-Screen` keeps only what is inside
the viewport.

```bat
scene_class.cmd -Window                 REM what the window contains
scene_class.cmd -Screen                 REM what is on screen right now
scene_class.cmd -Window -Screen         REM both, to compare
```

The gap is the point. A window containing a media player stays a media player
window while it is open; scroll the player out of view and the **screen** is a
list. Same window, same decoder, different pixels — and very likely a
different power draw.

If `-Screen` prints `geometry UNAVAILABLE`, this build's dump carries no node
rects, the two scopes cannot be separated, and it says so rather than handing
back the window twice.

### Identifying what is behind a surface

The ArkUI tree says an opaque node *exists*. It cannot say what is inside —
that is app-owned content, permanently. What it can say is everything the
**declaration** carries, and that is more than it looks.

```bat
scene_class.cmd -Window       REM metadata for every opaque node in the window
scene_class.cmd -RsProbe      REM the far side: which render_service dumps answer
scene_class.cmd -Deep         REM process evidence: threads, libraries, devices
scene_class.cmd -Services     REM what this build exposes to hidumper
```

`-Window` and `-Screen` print one record per opaque node — `XComponent`,
`Web`, `Video`, `SurfaceView`, `EmbeddedComponent`, `UIExtensionComponent`,
`Plugin`:

```
    [0] XComponent  (XCOMPONENT)  OFF SCREEN
         surfaceId    7823445120
         libraryname  libavplayer.so
         type         SURFACE
         rect         1080x608  aspect 1.776  352 permille of panel
         flags        SECURE (DRM)  HDR
         parent       Stack
         siblings     Slider,Button,Button,Text
         reads as     seekbar + transport buttons: a player with controls
```

A property missing here is not a property that does not exist — the live
`FrameNode` has it, the serialiser did not write it. `-RsProbe` reads the far
side of the surface, where render_service keeps the names and bounds it needs
in order to composite.

`-Deep` adds process evidence — threads, mapped libraries, open device nodes.
It prints **no verdict**: that evidence is process-scoped and the question is
node-scoped, so attributing it to a node on screen was wrong often enough to
be worse than silence.

Evidence is weighted by how hard it is to mistake:

| Source | Weight | Why |
|---|---|---|
| Open device nodes | highest | a hardware decoder is open or it is not |
| Thread names | high | decoders, camera pipelines and game engines all name their threads, and nobody renames them to fool a profiler |
| Mapped libraries | medium | a process that mapped `libavplayer` is doing playback. Weak for `libEGL`/`libGLES`, which ArkUI itself loads |
| Surface names | medium | the render-side node name often carries the XComponent's own id |

A `VERDICT UNKNOWN` is a result as well: it rules out a decoder and a camera,
which is most of what you wanted to separate.

`-Services` lists what `hidumper` exposes on your build and highlights the
render, media, camera and audio services — those are the channels that can
settle a surface's identity for certain, and which exist varies by build.

Everything here is a read. This is identification, not instrumentation.

### Teaching it: label, then fit

The classifier scores rather than matches. Every class accumulates evidence,
the highest total wins, the runner-ups become the candidate set and the margin
between the top two becomes the confidence. Nothing is decided by rule order,
which is what used to let a loose rule placed early eat a screen a better rule
would have caught.

Because the scores come from numbers in one table, you can tune them from
screens you have labelled yourself — on the device, with nothing leaving it.

```bat
REM 1. put a screen up, say what it actually is
scene_class.cmd -Label LAUNCHER
scene_class.cmd -Label SETTINGS_LIST
scene_class.cmd -Label CHAT
REM ... a dozen or two, including the ones it gets wrong

REM 2. fit, offline - no phone, no hdc needed
scene_class.cmd -Fit
scene_class.cmd -Fit -Apply
```

Every label is yours. `-Label AUTO` was removed along with the probe verdict
it rested on: a machine-written label that is wrong poisons the fit silently
and is never noticed, while a missing one costs a single screen.

`-Label` appends the full feature row plus your label to `scene_calib.csv`.
Use the class names the script itself emits, so a correct answer counts as
correct.

`-Fit` re-scores every labelled row through the live classifier and prints:

```
17 labelled screens from scene_calib.csv
  LIST x5, ICON_PAGER x4, CHAT x3, FEED x3, MEDIA_PLAYER x2

baseline  14/17 correct (82%)
  CHAT             -> LIST              LIST:5.5 CHAT:5.0 FEED:3.0
  FEED             -> LIST              LIST:4.5 FEED:4.0

sweeping thresholds...
best single change: SnippetChars  25 -> 20   gives 16/17 (94%)
```

plus the per-class feature ranges, which is what tells you *which* feature
could separate two classes that keep being confused. `-Apply` writes the
winning change to `scene_thresholds.json`; re-run to take the next step. The
sweep moves one threshold at a time on purpose — a single changed number with
a stated effect is reviewable, and a classifier nobody can read is worse than
one that is slightly wrong.

When no threshold helps, it says so: the remaining errors need a rule, not a
number, and the ranked scores say which rule.

`scene_thresholds.json` is a plain file you can also edit by hand; it
overrides the built-in defaults at startup.

### When a class is wrong

```bat
scene_class.cmd -Window
```

The composition block tells a parse problem from a rule problem without
reading a dump. If the counts look like the screen you are on, the rule is
wrong. If they are a tenth of what is on screen, or the tags look like
attribute names rather than components, the parse is wrong and no amount of
rule tuning will help.

`-Explain` prints the first 25 parsed nodes with their rects when that is not
enough, and `-Raw` writes the dump to `tree_w<id>.txt`.

**The clip only runs when it is corroborated.** The viewport rect parsed out
of the tree is checked against the panel size read from the window manager,
and if it does not match, the clip is skipped and every count is taken over
the whole tree. A wrong viewport would otherwise delete the scene, and a
classifier handed an empty tree does not fail loudly — it answers `LIST` for
everything. Rows classified this way carry `GEO_UNTRUSTED`.

`-Watch` follows the foreground and prints one line per tick, so you can drive
the phone and watch the class track:

```
   dev_mono  class              modifiers                            churn       window
  12847.312  ICON_PAGER         STATIC|SINGLE_THREAD_LAYOUT          +0/-0       Launcher
  12848.335  ICON_PAGER         CHURN_SWAP_HIGH|SINGLE_THREAD_LAY…   +43/-41     Launcher
  12850.366  MEDIA_PLAYER       CHURN_GROW_HIGH|OPAQUE_DOMINANT|…    +380/-12    VideoPlayer
```

It emits a **candidate set, not a label**. A fullscreen surface is honestly
{VIDEO, GAME, MAP, CAMERA}; the tree cannot separate those and saying so is
more useful than guessing. Each row carries a `resolve by` note naming who can.

`dev_mono` is device `CLOCK_MONOTONIC`, which is what makes the CSV alignable
with a scope capture. Host timestamps share no clock with the phone.

Everything it counts is **clipped to the viewport**. The dump carries the
whole tree, off-screen rows included, and counting those made a page whose top
is a video and whose body is a list keep reporting `MEDIA_PLAYER` long after
the video had scrolled away. Each node's rect is intersected with the window
rect; what is left over is the scene. Rows that scroll out are reported
separately in `offscreen`, which is a cost signal in its own right — those
nodes were laid out regardless.

This needs rects in the dump. Check the `VIEWPORT` line on the first run: if
it reads `geo 0` the viewport filter and every position-dependent rule stand
down, and the class falls back to counts alone.

**All 59 columns, the twenty-one classes and the modifiers are documented in
[SIGNALS.md](SIGNALS.md)** — including which ones depend on your dump format
and the one-command check to find out.

### What it cannot see

`hidumper` gives a snapshot. Dirty counts, frames, `pending_dirty`, gesture
events, renderer animations and damage exist only inside the running framework
and need the collector. The churn columns are a snapshot-differencing proxy for
the dirty-flag split, and they under-report a transform-only animation.

## Phase 2 — trace collection

```sh
hdc file send tools/sample_power.sh /data/local/tmp/
hdc shell "GPU_FREQ_PATH=/sys/class/devfreq/gpu/cur_freq \
           sh /data/local/tmp/sample_power.sh 50 > /data/local/tmp/power.csv"
```

The sampler and every descriptor producer must share `CLOCK_MONOTONIC`. If
they don't, nothing joins and the phase produces nothing.

Two measurement warnings that decide whether your results are credible:

- **The on-device fuel gauge is probably too coarse** — roughly 1 Hz and
  noisy. Adequate for a steady-state soak, not for attributing power across
  scene transitions. Put a shunt and a DAQ on the SoC rail.
- **The display dominates total power.** Correlate against total and you
  mostly measure backlight. Isolate rails, or hold brightness and refresh
  fixed across trials.

---

## Phase 3 — correlation

```sh
./correlate.py --descriptors scenes.jsonl --power power.csv --out joined.csv
```

Foreground-only and config-change-excluded by default. It reports integrity
before it reports correlations:

```
read 62 records, using 60
  dropped 1 non-foreground
  dropped 1 config_change
  !! 2 records LOST to ring drops across 1 streams
     gaps are not idle; treat affected windows as unmeasured
```

Then features ranked against each target, with **two** columns: `r(now)` and
`r(next)`. A feature strong only at `r(now)` describes the present, which the
governor already sees from utilisation. The value of a scene label is
anticipation. **Evaluate on `r(next)`.**

---

## Calibration you must do once

Three numbers in these tools are starting guesses. None is hard to fix; all
are easy to forget.

1. **The dump parse.** Run `scene_probe.sh -k` on an app you know is
   ArkUI-native (Settings is a good one) and check the `TOP TAGS` block lists
   real component names. If it lists garbage, only `tally_tags()` needs
   adjusting — everything downstream is format-independent.
2. **The verdict thresholds** (15 and 60 elements). Calibrate against one
   known-native app and one known engine port.
3. **`kStructChurnThreshold`** in the collector, currently 48. Too low and
   every list scroll triggers a tree walk; too high and Swiper paging goes
   unnoticed. Validate against a real paging trace.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| "element dump was empty" | param unset, or the app was running before you set it — force-stop and relaunch |
| "could not determine a window id" | WMS column layout differs on your build; use `-l` then `-w` |
| `TOP TAGS` is nonsense | dump format differs; adjust `tally_tags()` |
| "WindowManagerService dump was empty" | insufficient shell privilege |
| `noexec` / permission denied | invoke as `sh script.sh`, not `./script.sh` |
| Everything reads `BLIND` | check `engine so` — if it's empty too, suspect the parse, not the apps |
| `correlate.py` reports missing vsync counts | an ABI-1 producer is still running |

---

## Running from Windows

Only the host-side orchestrator differs. `scene_probe.sh` and
`sample_power.sh` run **on the phone**, so they stay as they are;
`correlate.py` is Python and already portable.

| Host | Orchestrator |
|---|---|
| Linux / macOS | `probe_batch.sh` |
| Windows | `probe_batch.ps1`, or `probe_batch.cmd` |

```bat
probe_batch.cmd -Setup
probe_batch.cmd -Plan plan.txt
probe_batch.cmd -Plan plan.txt -Launch
probe_batch.cmd -Summary coverage.csv
```

The `.cmd` is a one-line wrapper that invokes PowerShell with
`-ExecutionPolicy Bypass`, so you don't have to change machine policy to run
an unsigned script. If you'd rather call PowerShell directly:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\probe_batch.ps1 -Setup
```

Written for **Windows PowerShell 5.1** (built into Windows), so it also runs
under PowerShell 7.

### Line endings — the one that will cost you an hour

A shell script with CRLF line endings, or a UTF-8 BOM, fails on the device in
ways that look nothing like their cause: `command not found` on lines that are
obviously fine, or a silent no-op. Editing `scene_probe.sh` in a Windows editor
is the usual way this happens.

`probe_batch.ps1 -Setup` **normalises to LF and strips the BOM before
pushing**, so the supported path is safe. If you ever push by hand, don't:

```bat
REM don't do this from Windows
hdc file send scene_probe.sh /data/local/tmp/scene_probe.sh
```

Use `-Setup`, or configure your editor to save these files as LF. A
`.gitattributes` with `*.sh text eol=lf` is worth adding if these go into a
repo that Windows machines clone.

### Other Windows notes

- **`hdc` on PATH.** It ships with the toolchain; the DevEco terminal has it
  already. Otherwise add the SDK's `toolchains` directory to PATH.
- **Device paths stay forward-slashed.** Windows source paths may use
  backslashes; anything after the device prompt must not.
- **`hdc` writes progress to stderr**, which PowerShell surfaces as error
  records. The script reads exit codes rather than stderr, so ordinary
  progress chatter is not mistaken for failure.
- **Python** for `correlate.py`: `py -3 correlate.py --descriptors ... --power ...`
