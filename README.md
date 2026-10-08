# arkui — scene classification signals for power work

A system component for HarmonyOS NEXT that patches ArkUI to emit **scene
classification signals**, so that scene composition and rendering behaviour can
be correlated with power draw and CPU frequency.

The aim is not to estimate workload. It is to say, cheaply and continuously,
*what kind of screen this is and what it is doing* — and to hand that to
whoever owns aggregation, power management and scheduling. Those decisions are
not made here. The quality of the data is.

## Why ArkUI

ArkUI is platform code. Patch it once and every app inherits the patch through
the pre-warmed `appspawn` fork — no app is touched, recompiled, or aware.

It is also the only place where the useful numbers exist. The pipeline already
computes, every frame, which nodes are dirty and in what way:
`PROPERTY_UPDATE_MEASURE`, `MEASURE_SELF`, `LAYOUT`, `RENDER`. That split is the
single most power-informative signal in the stack — measure and layout are
single-threaded UI work where frequency helps and cores do not — and it never
leaves the process.

## Layout

| Path | What it is |
|---|---|
| `include/scene_descriptor.h` | the ABI: one fixed-size record per window per interval |
| `collector/` | the in-process accumulator, one per window, and the ArkWeb producer |
| `tools/` | the proof of concept: hdc scripts that observe and classify from outside |
| `docs/` | notes |

Start with **[tools/COMMANDS.md](tools/COMMANDS.md)**.

## The two levels, and why both exist

**Per container.** One window, one ArkUI pipeline, one FrameNode tree, one
record. Everything that correlates with power — the dirty split, frames against
vsyncs, damage, layout work — is pipeline-local, so any other unit sums numbers
that were never comparable.

**Per display.** Power is one rail and frequency is one set of clusters. What a
governor responds to is every visible window composited together. Flattening
per-window records into that is the aggregator's job, not this component's.

## What the scripts are, and are not

The scripts in `tools/` read `hidumper` over `hdc`. They build the labelled
dictionary, they answer whether a class set is discriminable at all, and they
validate the window/composition model against a real display — which is the one
thing no ArkUI instance can do, since a container sees its own window and no
other.

They cannot be the collector. A dump is a snapshot of the tree, not of what was
drawn; no route-change event exists over `hdc`; the sampling gap to a 10 ms
frequency loop is two orders of magnitude; and `hidumper -inspector` is
dispatched to the app's **UI thread** — the exact thread whose cost we are
trying to characterise. Poll it fast enough to be useful and you are measuring
your own instrument.

## Design rules that were learned the hard way

**Unknown must never satisfy a test that a value would.** A zero-sized rect
means "not measured", not "off screen". An absent content string means the dump
carried none, not that the text is short. Treating either as a value collapsed
every scene into one class, twice.

**Geometry failure degrades to counts, never to a gutted tree.** A classifier
handed an empty tree does not fail loudly — it quietly answers `LIST` for every
screen. The viewport clip therefore runs only when the parsed viewport is
corroborated against the panel size, and aborts itself if it would delete the
scene.

**Evidence accumulates; it does not short-circuit.** Scoring replaced a
first-match rule chain, because ordering silently shadowed whole classes.

**No verdict without attribution.** Process evidence — threads, libraries, open
device nodes — is process-scoped, and a screen is not. A browser has web
threads on a settings page. The facts are printed; the confident number that
used to sit under them is not.
