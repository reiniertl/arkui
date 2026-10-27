# The two feature spaces, and the partitions over them

This is reasoning, not specification. The contract is in the DAG:
`IF-PATTERN-SPACE`, `IF-SURFACE-SPACE`, `ALG-PSI-PATTERN`, `ALG-PSI-SURFACE`
and the four `INV-PSI-*`. A pre/postcondition written here is a note; the
node is the specification.

## The pipeline is two mappings

    component tree --extract--> measurements --PHI--> feature vector
    feature vector --PSI--> pattern

`PHI` exists and is `rs_pattern.py`. `PSI` did not: it was deleted with three
asserted taxonomies — a C enum of 20, a PowerShell classifier of 23, a naming
function of 28 shapes — and nothing replaced it. That is why both vectors
stopped at stage 1 and why the only thing the page could report was a cluster
count.

**The difference between this and what was deleted.** Those sets were written
first and matched against the data afterwards. A partition is derived from the
structure of the space it partitions, so it can be written down without
looking at a single screenshot. That is the test, and it is why
`INV-NO-SCENE-CLASS` permits this: a partition of a space is a function; a
library of named shapes matched against data is not.

A cell's name is its coordinates. There is no word for it anywhere.

## The surface space

`PHI_s` sends a scene to a point in the standard simplex: six non-negative
coordinates — `vis_xcomp`, `vis_image`, `vis_text`, `vis_glyph`,
`vis_content`, `vis_substrate` — summing to one. `INV-SURFACE-SUMS-TO-ONE`
is what makes that a simplex and not merely six numbers. `vis_unnamed` is not
a coordinate: it is a subset of `vis_content`, carried for diagnosis.

**`PSI_s(s) = (largest coordinate, second largest)`**, with ties broken by a
declared coordinate order. No threshold appears anywhere in it. That was
deliberate: a threshold is a constant, a constant has to be fitted, and
anything fitted on two sessions of one device held by one person is a
property of that capture rather than of the space.

The alternative considered and rejected was `(argmax, whether the largest
exceeds one half)`. One half is defensible — it is the unique point at which a
coordinate exceeds all others combined, so it is derived rather than fitted —
but it separates almost nothing here: `vis_substrate` leads 179 of 299 scenes,
so one cell would hold 60 percent of the capture. The order-based form puts
the largest cell at 23 percent.

**`vis_glyph` is excluded as a ranking candidate.** A `SymbolGlyph` is an icon
glyph and a `Divider` is a hairline; both are bounded small by what the
component *is*, not by what this capture happened to contain. Over 299 scenes
`vis_glyph` never exceeds 0.006 and is neither first nor second in any scene.
The exclusion is an argument from the component. It is the weakest step in
this document and it is the one to revisit first if a device is ever captured
where a glyph fills a display.

Five candidates ranked two deep gives **20 cells**.

## The pattern space

`PHI_p` sends a scene to the repeating group it found: an arrangement, a
coverage, a height, an aspect, a column count, a member count, an overlay
fraction, and how many groups exist — or to nothing, when no two sibling
branches shared a description.

**`PSI_p(p) = (arrangement, whether the group covers more than half the
window)`**, and the single cell `none` when there is no group.

The arrangement axis is closed by construction, which is the point:
`arrangement()` compares distinct x and y positions and returns exactly
`grid`, `column`, `row` or `stack`. It cannot return anything else, so the
axis is not a list anyone chose.

Here one half earns its place, where it did not in the surface space: a group
covering more than half the window IS the screen's layout, and one covering
less is an element within some other layout. That is a statement about what
the measurement means, and the boundary is the unique value that separates the
two readings.

Four arrangements by two coverage classes, plus `none`, gives **9 cells**.

## The four properties

**Total.** Every feature vector lands in exactly one cell. Both partitions
are defined by a maximum and a comparison over a finite coordinate set, both
of which are total functions, and both carry an explicit cell for the absent
case — `none` for a scene with no repeating group. A scene fits nothing only
by landing in a declared cell, never by falling through.

**Disjoint.** The cells are the fibres of a function, so disjointness is not
an additional property to check but a restatement of totality — PROVIDED ties
are resolved. Over 299 scenes exactly one surface scene has a tie for the
largest coordinate, which is why the coordinate order is declared rather than
left to whatever `sort` does.

**Minimal.** No cell is empty. Measured over the 299 scenes of the capture:

| space | declared | reachable | empty |
|---|---|---|---|
| pattern | 9 | **9** | 0 |
| surface | 20 | 16 | **4** |

The pattern partition satisfies it. **The surface partition does not**, and
the four empty cells are exactly the row where `vis_text` leads: text is never
the largest coordinate in any scene. That is not a rounding artefact — text is
numerous and small, it reaches 0.312 at most, and the audit of 2026-10-27
found the same thing before any of this was built.

Two readings, and this document does not choose between them. Either the
device and the applications captured genuinely have no text-dominant screen —
two sessions of one person's phone is a thin basis for believing that — or the
measurement cannot express one, because text sits inside containers whose own
paint takes the pixels first. The second is testable: capture a reader or a
document editor and see whether the cell fills.

**Orthogonal.** Within each space the two axes are independent coordinates:
which kind leads is a question about composition, how much it leads by is a
question about magnitude, and neither determines the other. In the pattern
space the arrangement comes from geometry and the coverage from area; the same
holds.

Across the two spaces, orthogonality is not claimed here. Whether a scene's
surface cell is predictable from its pattern cell is a measurement, it is the
subject of the last item in the list, and it is the one that decides whether
the two vectors are measuring different things at all.

## What is not settled

- Four surface cells are unreachable and the cause is not established.
- `stack` is thin: 1 scene with majority coverage and 3 without. Reachable,
  but on this capture it is 4 scenes out of 299.
- The glyph exclusion rests on an argument about components, not a proof.
- Nothing here has been implemented. `ALG-PSI-PATTERN` and `ALG-PSI-SURFACE`
  are declared with their contracts and no code satisfies them yet.
