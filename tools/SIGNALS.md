# Signals

Every column `scene_class.ps1` writes, what it means, and whether to trust it.

Three different things are easy to conflate, so:

| | Fields | Purpose |
|---|---|---|
| **The signal** | 5 | what the collector will ship to the aggregator |
| **Classifier inputs** | ~15 | what the rules consume to produce the class |
| **This CSV** | 59 | exploration — you cannot recover a feature you did not record |

The CSV is deliberately wide and **temporary**. After a correlation run most of
these will have predicted nothing and should be deleted. A wide file costs
nothing; a wide descriptor would be wrong, and that one is already compact.

---

## On-screen only

Every count below is taken over the nodes that are **actually in the
viewport**. The dump carries the whole tree, including the rows of a long page
that are scrolled past the bottom, and counting those was wrong in a way that
showed: a page whose top is a video and whose body is a list kept reporting
`MEDIA_PLAYER` after the video had scrolled away, because the `XComponent` was
still in the tree.

Each node's rect is clipped against the window rect. A node with no remaining
area is off-screen and is not counted — it lands in `offscreen` instead.
Nodes carrying `visibility: Hidden/None`, `isVisible: false` or `opacity: 0`
land in `hidden`.

This needs rects. When the dump carries none, `geo` is `0`, nothing is
filtered, and every rule that depends on position stands down rather than
guessing — see **Availability** at the end.

---

## What counts as "the scene"

Four things get called the same word, and the class is only meaningful once
you say which one it labels:

| | |
|---|---|
| **window** | a WindowManager object: rect, ZOrder, type, pid |
| **viewport** | that window's rect clipped to the display |
| **visible region** | the viewport minus whatever sits on top. A window can be `visible` and entirely covered by a dialog |
| **rendered set** | the nodes inside the visible region that are not clipped by a scroller, hidden, or transparent |

The definition used here:

> **The scene is the topmost non-overlay window covering a material share of
> the display. Everything above it is context, not a separate scene.**

A phone screen is nearly always several windows — a launcher under a status
bar under a gesture strip. Calling the topmost one "the scene" is how the
classifier ended up describing a 40-pixel gesture hot zone. ZOrder alone
cannot express this and area alone picks the wallpaper; it needs both, plus
occlusion: if something above genuinely covers the host, *that* is the scene.

What sits above becomes `scene_scope`, because it changes the cost without
changing what the scene is:

| Value | Meaning |
|---|---|
| `IME_UP` | a keyboard is up — content is shorter and there is a second animating surface |
| `SHADE_ABOVE` | notification shade or control panel over the scene |
| `TRANSIENT_ABOVE` | a toast, banner or volume popup |
| `WINDOW_ABOVE` | another app window above, not covering |
| `MULTI_WINDOW` | two or more content windows each over 20% of the display |
| `PROMOTED_OVER_<id>` | the pick was overridden: something above covered the host |
| `NO_CONTENT_WINDOW` | nothing qualified; the pick is a fallback |
| `NO_WINDOW_RECTS` | the window table carried no geometry, so share and occlusion are unknown |

`win_share` is the host's share of the display, as a percentage, or `-1` when
rects are unavailable.

In `-Watch`, re-scoping needs **two consecutive ticks**. A scene that flickers
between two windows produces a flickering class, and a flickering class makes
a governor oscillate — the exact failure this project exists to avoid.

## Identity and timing

| Column | Description |
|---|---|
| `host_ts` | Windows wall clock, ms resolution. Bookkeeping only |
| `dev_mono` | **Device `CLOCK_MONOTONIC` seconds**, from `/proc/uptime`. The field that lets rows align with a scope capture or a device-side trace. Host timestamps share no clock with the phone |
| `window` | Window id probed |
| `name` | Window name from the WMS table |
| `pid` | Owning process |

## The signal

| Column | Description |
|---|---|
| `scene` | The class, one of twenty-one |
| `candidates` | Pipe-separated set the tree narrows to. Equals `scene` where decisive, wider where not |
| `confidence` | `structural` / `weak` / `low` / `none`, derived from `margin` |
| `score` | Winning class's accumulated evidence score |
| `margin` | How far ahead of the next plausible class it finished. **This is the number to filter on** — a 0.5 margin is a coin toss wearing a label |
| `ranked` | Top four classes with their scores. The fastest way to see what the screen nearly was |
| `truth` | Your label, when the row was recorded with `-Label`. Empty otherwise |
| `modifiers` | Pipe-separated state flags (see below) |
| `stable_ticks` | Consecutive ticks in this class. Low values mean the classifier is flickering, which matters because a flickering signal causes policy oscillation |

## Where the tree comes from

`-DumpOpt` selects the source, and the sources are not equivalent.

| Source | Carries |
|---|---|
| `inspector` | the component tree. On some builds, names and structure only — no rects, no text, no attributes |
| `render`, `element`, `frontend`, `navigation` | on some builds, the window header and nothing else |
| `uitest` | `bounds`, `type`, `text`, `id`, `description`, `hostWindowId` per node, from the UI-test harness |

`-FindRects` reports which of them answers on the device in front of you. A
build that serialises names only is not a broken device: the layout results
exist, nothing in the window manager's dumps prints them. `uitest` prints them
because that is what it exists for — at the cost of reading the accessibility
projection of the tree rather than the tree itself, so accessibility-hidden and
purely decorative nodes are absent.

The window header carries `WindowRect` even when the window table has no
geometry columns, so window share, occlusion and the panel size are recovered
from there and cached for the session.

## Parse and viewport

| Column | Description |
|---|---|
| `nodes` | Nodes in the whole dump, on screen or not |
| `offscreen` | Nodes with a rect that falls outside the viewport. **Laid out but not rendered** — high values mean virtualisation is not doing its job |
| `hidden` | Nodes explicitly marked not visible |
| `geo` | `1` if rects were parsed. **`0` disables the geometry and position columns and the rules that read them** |
| `vp_w`, `vp_h` | Viewport size in px, as parsed. Sanity-check these against the panel |
| `visible_boxes` | Nodes that survived both filters — the ones everything else counts |
| `boxes` | Nodes that carried a rect at all |

## Component counts — on-screen nodes only

| Column | Description |
|---|---|
| `total` | On-screen tags. Scale of what is being rendered, not of the document |
| `text` | Text + Span + RichText |
| `image` | Image nodes |
| `button` | Buttons |
| `slider` | Sliders. A seekbar under a surface is the player signal |
| `progress` | Progress + LoadingProgress. Splash and loading |
| `toggle` | Toggle, Checkbox, Radio, Switch |
| `editable` | TextInput, TextArea, Search, RichEditor |
| `listlike` | List + ListItem |
| `gridlike` | Grid + GridItem + WaterFlow |
| `swiper` | Swiper + Tabs + TabContent |
| `scroll` | Scroll containers |
| `scrollers` | Sum of the four above |
| `web` | Web components. Opaque — ArkWeb's to describe |
| `xcomponent` | XComponents. Opaque — outside-only, permanently |
| `video` | Native Video components. Not opaque |
| `canvas` | Canvas nodes |
| `opaque` | `web + xcomponent`. The coverage number |

## Opaque regions — what is probably inside

We cannot look inside an `XComponent`; it is app-owned content and always will
be. The **declaration** is ours, though, and it says more than it looks.

| Column | Description |
|---|---|
| `xc_name` | The developer's own id for the component. Free text, so a guess — but people do call a video surface `videoPlayer` |
| `xc_lib` | The native library registered to drive it. `ijkplayer` and `nativerender` say very different things |
| `xc_type` | `SURFACE` composites independently; `TEXTURE` is read back into the UI tree, costing an extra pass. A cost difference, not just a taxonomy |
| `xc_a11y` | Accessibility label — the one string written to be read by a human |
| `xc_secure` | `enableSecure`, i.e. DRM. **Not a guess**: protected content is video |
| `xc_hdr` | HDR brightness set |
| `xc_aspect` | Aspect of the surface rect. 1.6–1.85 is video's shape |
| `xc_hint` | `VIDEO` / `CAMERA` / `GAME` / `MAP` / `CHART` / `AR` / `AUDIO_VIS` / `UNKNOWN` / `NONE` |
| `xc_conf` | 0–100. **Treat under 50 as no better than a prior** |
| `xc_flags` | Which evidence fired: `SECURE`, `HDR`, `NAME_MATCH`, `LIBRARY_MATCH`, `A11Y_MATCH`, `SIBLING_MATCH`, `ASPECT_MATCH` |

The hint feeds the scorer with a weight proportional to its confidence, so a
DRM surface pushes `MEDIA_PLAYER` hard and a 16:9 rect barely nudges it.

**None of this is authoritative, and it is not meant to be.** The buffer queue
knows: the usage flags literally say video decoder or camera, and the format
says YUV or RGBA. This is what ArkUI can offer *before* that join, and a prior
to weigh the answer against. The descriptor ships `join_id` so the aggregator
can settle it against the subsystem that knows.

In the descriptor the id and library are shipped as **FNV-1a hashes**, not
strings: they are app internals and a power trace is not the place for them,
while a hash still joins the same surface across records and runs. This CSV
keeps the plaintext, because you are building the dictionary.

### Per-surface records — `scene_surfaces.csv`

The `xc_*` columns above are **one blended record per window**: the first
value found wins, across every opaque node on screen. That is enough for a
scene class and wrong for anything else — two `XComponent`s are two cost
sources, and a picture-in-picture window has a 900-permille surface and a
120-permille one with completely different submit rates.

`-Window` and `-Screen` write one row per node instead, to `scene_surfaces.csv`:

| Column | Description |
|---|---|
| `scope`, `win`, `window_name` | `window` or `screen`, and which window the node belonged to |
| `Idx` | position among the opaque nodes in this window, 0-based |
| `Tag` | the component name as the dump spells it |
| `Kind` | normalised: `XCOMPONENT`, `WEB`, `VIDEO`, `SURFACE_VIEW`, `EMBEDDED`, `PLUGIN`, `OPAQUE` |
| `JoinKind`, `JoinId` | **the join key** and which spelling it came from (`surfaceId`, `webId`, `nodeId`). Empty means this build does not expose one, and nothing downstream can be matched to this node |
| `Id` | the developer's own name for the component, when there is one |
| `SrcHost` | host of a `Web` or `Video` `src`. **The path is deliberately not written here** — the host answers every question we have, the path is somebody's browsing history |
| `W`, `H`, `Aspect` | the node's rect |
| `Permille` | its share of the **panel**, not of its parent. `-1` when the dump has no rects |
| `Vis`, `Opacity` | declared visibility and opacity |
| `Parent`, `Siblings` | what it is wrapped in |
| `flags` | state flags, `\|`-separated: `SECURE (DRM)`, `HDR`, `ANALYZER`, `INCOGNITO`, `AUTOPLAY`, `LOOP`, `MUTED`, … |
| `attrs` | the kind-specific attributes as `key=value`, `\|`-separated — `libraryname`, `type`, `renderFit` for an XComponent; `renderMode`, `layoutMode`, `cacheMode` for a Web; `objectFit`, `rate` for a Video; `bundleName`, `abilityName` for an embedded one |
| `extra` | `key=value` pairs this build emitted that the parser does not name |

### Opaque is one definition

`XComponent`, `Web`, `Video`, `SurfaceView`, `EmbeddedComponent`,
`UIExtensionComponent`, `Plugin` — a single pattern, used by the feature
counter and the metadata dump alike, because the word has to mean the same
thing in both places: **a node whose contents we do not own and cannot descend
into.** `Web` is on the list even though ArkWeb is patchable platform code;
from the component tree's point of view it is still a hole, and the inside of
it is a separate producer's job.

The cost-shaped fields are worth calling out, because they are the reason to
read a record at all rather than just count the node:

- **XComponent `type`** — `SURFACE` gets its own composition pass, `TEXTURE` is
  read back through the UI tree (an extra pass), `COMPONENT` is drawn by the
  app on the UI thread.
- **Web `renderMode`** — `SYNC_RENDER` draws the web content into the UI tree.
  Same cost shape as a `TEXTURE` XComponent, and the ArkUI-side signals move
  with the page rather than staying flat.
- **Web `layoutMode`** — `FIT_CONTENT` makes web layout drive ArkUI measure.
- **Web `nestedScroll`** — a swipe pays for the web scroll *and* an ArkUI
  scroller.
- **Video `rate`** — off 1.0 changes the decode load directly.

The `extra` column is the point of the mode. The named fields encode what a
declaration was *expected* to carry; builds differ, and the only way to learn
what this one exposes is to look at the keys nobody asked for. When something
useful turns up there, it gets promoted to a named column.

On screen the same records print with the render-side lines from
`-w <id> -render` appended, which is where a self-drawing layer shows up —
a surface composited on its own pass rather than folded into the window is a
direct power difference, and ArkUI does not know which it is.

## Coverage — how complete this record is

ArkUI describes the nodes it owns. A region behind an `XComponent` is app
content it will never see; a region behind a `Web` node belongs to a separate
producer. So every record states how much of the viewport it could not read.

| Column | Description |
|---|---|
| `cover_opaque` | permille of the viewport behind any opaque node |
| `cover_web` | the part of that which is `Web` — recoverable from an ArkWeb producer |
| `cover_xc` | the part which is `XComponent` — only render_service or the buffer queue can speak for it |

Read it as the record's own confidence. At 20 permille the native counts are
the whole story. At 900 they describe the chrome around a hole, and **the
scene itself may be drawn inside that hole** — an icon list, a feed, a map,
rendered by the app into a surface, with ArkUI seeing a `Stack` and a toolbar.

This matters most while labelling. A label states what you *saw*; the features
state what ArkUI *read*. Above 500 permille those are different things, and a
fitter handed both can only learn noise. `-Label` warns, the coverage is
recorded with the row, and `-Fit` sets those rows aside and scores them
separately — a high score there means the chrome happened to agree with what
was behind it, not that the classifier saw anything.

Three modifiers carry the same fact into the correlation:

| Modifier | Meaning |
|---|---|
| `SCENE_BEHIND_SURFACE` | most of the viewport is not in this tree |
| `NEEDS_ARKWEB` | a Web region dominates; its producer should be read for this window |
| `NEEDS_SURFACE_PRODUCER` | an XComponent dominates; render_service or the buffer queue is required |

The last two are routing, not policy: the cheap always-on source saying which
expensive source is worth consulting for this window right now.

## Text volume

| Column | Description |
|---|---|
| `textlen` | Total content length, if the dump carries content strings |
| `textmax` | Longest single block. **The reading-vs-feed discriminator**, and the one nothing outside ArkUI can provide |
| `avgtext` | Content length per text-bearing node. **The label-vs-snippet discriminator**: a settings row runs ~12 characters, a feed card ~90. Counts cannot separate those two screens; this can |

## Position — requires rects

Where a thing sits decides what it is. A search field sits **above** the list,
a chat composer sits **below** it; that single fact is the whole difference
between Settings and a conversation, and no count can see it.

| Column | Description |
|---|---|
| `edit_pos` | Mean centre of the visible editables, 0 at the top of the viewport and 1 at the bottom. `-1` when unknown |
| `slider_pos` | Same for sliders. A seekbar low on a surface is a stronger player signal than a slider anywhere |
| `pos_source` | `viewport` or `none`. **`none` means the two rules above did not run** |

Normalised against the viewport, not the document, so a tall page does not
push its composer to 0.05 merely because the content behind it is long.

## Geometry — requires rects

| Column | Description |
|---|---|
| `overdraw` | Summed clipped box area ÷ viewport area. Composite-cost proxy that node count cannot give |
| `largest_frac` | Largest visible **leaf**, per-mille of the viewport. Leaf, not node: the root always fills the window, and measuring it makes every screen look like fullscreen video |
| `largest_aspect` | Its width ÷ height. ~1.78 suggests video |

## Render-cost multipliers — keyword scan

The same node count costs very different amounts depending on what is applied
to it. Structural, known to ArkUI, invisible downstream.

| Column | Description |
|---|---|
| `fx_blur` | Blur mentions. Forces a readback and a separate pass — the expensive one |
| `fx_shadow` | Shadow mentions |
| `fx_opacity` | Opacity layers |
| `fx_clip` | Clip and mask |
| `fx_gradient` | Gradients |
| `fx_score` | Weighted sum: blur ×8, shadow ×2, rest ×1 |

## Declared demand and recycling

| Column | Description |
|---|---|
| `decl_rate` | `expectedFrameRate` if present. **The app stating its own demand** — about as direct a signal as exists |
| `lazy` | LazyForEach mentions |
| `reusable` | Reusable / recycle mentions |
| `cached` | `cachedCount`. Predicts construct vs recycle on the next scroll tick |

## Churn — snapshot differencing

The only dynamics available without the collector. Direction matters more than
magnitude: arriving nodes cost construction, layout and possibly decode;
leaving nodes cost teardown, which is far cheaper.

| Column | Description |
|---|---|
| `churn_added` | Tags that appeared. **Construction — the expensive direction** |
| `churn_removed` | Tags that vanished. Teardown — cheap |
| `churn_net` | added − removed |
| `churn_delta` | added + removed. Total movement |
| `churn_shape` | `GROW` / `SHRINK` / `SWAP` / `NONE`. Swap is a page change or list recycling |
| `churn_rate` | delta per second |
| `dt` | Seconds since the previous sample |

Churn diffs the **on-screen** tally, so scrolling a long page registers as
change even though the document behind it did not move. That is intentional:
the rows entering the viewport are the ones being laid out and painted.

**Known blind spot:** churn still under-reports a transform-only animation,
because a page sliding under an unchanged tree changes neither the nodes nor
their rects. If a visibly moving screen reads `STATIC`, that is the drag phase
being a transform — which is itself a finding, not a bug.

## Diagnostic

| Column | Description |
|---|---|
| `evidence` | Which rule fired and why. For debugging a wrong class |

---

## Modifiers

| Flag | Meaning |
|---|---|
| `STATIC` | no tree change since the last sample |
| `CHURN_GROW_HIGH` / `_LOW` | nodes arriving — construction |
| `CHURN_SHRINK_HIGH` / `_LOW` | nodes leaving — teardown |
| `CHURN_SWAP_HIGH` / `_LOW` | balanced — page change or recycling |
| `OPAQUE_DOMINANT` | most of the window is not ours to describe |
| `CHROME_VISIBLE` | media controls shown — interacting |
| `CHROME_HIDDEN` | media controls faded — passive watching |
| `EDITABLE_PRESENT` | an input affordance exists |
| `COMPOSER_PRESENT` | an editable pinned **below** the content: expect an IME window above this one and a caret waking the UI thread with no input |
| `SINGLE_THREAD_LAYOUT` | committed layout work is the UI thread: frequency helps, cores do not |
| `DECODE_LIKELY` | image nodes arriving — decode may go parallel, the one case cores help |
| `BLUR_PRESENT` | readback plus an extra pass |
| `FX_HEAVY` | weighted effect score ≥ 20 |
| `OVERDRAW_HIGH` | ≥ 3× viewport area painted |
| `OFFSCREEN_HEAVY` | more nodes scrolled out than on screen — all of them still laid out |
| `RATE_DECLARED_<n>` | the app declared an expected frame rate |
| `LAZY_LIST` / `REUSE_POOL` | virtualisation or recycling in use |
| `NO_GEOMETRY` | the dump carries no rects; the geometry and position columns are unavailable and the rules that need them stood down |
| `NO_TEXT_CONTENT` | the dump carries no content strings; `avgtext` and `textmax` are unknown, not zero, and every text-length test stood down |
| `SYSTEM_WINDOW` | the probed window is system chrome, not an app |

Modifiers the **collector** will add and this script cannot: `WORK_COMMITTED`,
`RENDERER_ANIMATING`, `GESTURE_ACTIVE`, `TRANSITIONING`, `FIRST_FRAMES`,
`IME_UP`, `BACKGROUND`, `PARTIAL_WINDOW`.

---

## When the dump carries neither rects nor strings

This is a real configuration, not a failure — some builds' `-inspector` output
is structure only. Both modifiers then appear and the classifier runs on
**counts alone**: node types, how many, and how they nest.

What still works: every surface rule, the icon-grid rules, switches-in-a-
scroller, text-dominance, tree size. What stands down: everything positional
(`CHAT` vs a search header), item size (`FEED` vs `LIST`), and caption length.

The rules were rewritten so that *unknown never satisfies a test that a value
would*. `avgtext 0` used to pass "captions are short", which is the same class
of bug as treating a zero-sized rect as scrolled off screen. Both are now
guarded, and a test with no data simply does not contribute.

## Availability check — do this before a long run

The geometry, position and effect columns depend on the dump printing
attributes. The script now reports this itself rather than leaving you to
infer it: run

```
scene_class.cmd
```

and read the `VIEWPORT` line and the `geo` column.

| What you see | What it means |
|---|---|
| `geo 1`, `vp_w`/`vp_h` matching the panel | everything works |
| `geo 0`, `NO_GEOMETRY` in the modifiers | no rects. Counts still work; position, overdraw, largest-leaf and the viewport filter do not |
| `geo 1` but `vp_w`/`vp_h` wrong | a rect form parsed, but not the one meant. Send a `-Raw` dump and the regexes get another shape |

`scene_class.cmd -Raw` additionally writes `tree_w<id>.txt` and prints the
on-screen-vs-document tag totals and the top tags, which is the fastest way to
tell a real component tally from a regex that latched onto attribute names.

Two switches separate a parse problem from a classification problem:

| Switch | Use |
|---|---|
| `-Window` vs `-Screen` | the same tree, unclipped and clipped. If the composition is right one way and wrong the other, the rects are being misread — not the rules |
| `-Explain` | print the first 25 parsed nodes with tag, indent, rect and on/off state. A bad regex is obvious here and nowhere else |

The script also warns on its own when the viewport filter drops most of the
tree, or when fewer than ten nodes parse at all. A genuinely long list will
trip the first warning legitimately; a launcher will not.

Whatever is absent is **structurally zero, not measured** — and columns of
zeros look like data. Say which are missing and the regexes can be stripped
rather than left to mislead a correlation.
