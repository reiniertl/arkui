# One entry per feature. Field order is the order the cards render.
# status: live | constant | pruned   (constant = no variance in this session,
# pruned = removed by --decorrelate 0.55 as repeating another feature)

COMMON = {
  "pipeline": [
    ("Capture", "hdc shell hidumper -s RenderService -a RSTree  →  ~772 KB, ~1057 lines, two passes"),
    ("Parse", "One regex pass per line builds 517 nodes and 11 surfaces with rects, tags, flags"),
    ("Own", "Each node is assigned to the outermost surface above it; nested surfaces merge into their window"),
    ("Measure", "Per contributor: one pass over its admitted nodes accumulates every feature below"),
    ("Combine", "Display-level features are averaged across contributors weighted by painted area; arrangement comes from the owner alone"),
    ("Normalise", "transform → missing filled with the column mean → z-score over the dataset → × weight"),
  ],
  "cost": [
    ("Parse the dump text", "12.6 ms", "O(L) over 1057 lines", "95% of the whole cost"),
    ("Build every feature", "2.66 ms", "O(N) + O(R log R)", "all 29, from the parsed tree"),
    ("Area unions (46 calls)", "included", "O(R log R) sweep-line", "largest call 160 rects"),
    ("Whole sample, end to end", "32.9 ms", "", "incl. file I/O, client dump, signature, CSV row"),
    ("Same work in PowerShell", "~860 ms", "", "26× slower; why the port exists"),
  ],
}

F = []
def f(**kw): F.append(kw)

# ------------------------------------------------------------ repeat shape
f(name="rs_item_h0 … rs_item_h4", group="Repeat shape", status="live",
  weight="0.45 each", rng="0 – 1, the five sum to 1",
  origin="RSTree, pass 1 — <code>innerAbsDrawRect</code> on every leaf node of the owning contributor",
  raw="""innerAbsDrawRect: [0, 0, 1316, 2832]
innerAbsDrawRect: [24, 310, 1268, 1690]
innerAbsDrawRect: [24, 2016, 1268, 1690]""",
  extract="""Leaves are grouped by their own size rounded to whole vp: key = (round(w/density), round(h/density)).
A group with two or more members is a repeating group. Each group's covered area is the UNION of its
members' rects (a sweep-line, not a sum — members overlap and summing counted the overlaps twice).
Each group's item height, in display heights, is spread over five band centres — 0.03, 0.08, 0.18,
0.40, 0.85 — by linear interpolation in log space, weighted by that group's area. The five are then
divided by the total so they are shares.

Two ramps decide how much each group counts. An item fades in between 8 and 16 vp on its shorter
side, so a row of indicator dots contributes nothing; and it fades out between 45% and 75% of the
display, so a full-bleed item is a pane rather than a repeating unit. Both were hard cuts once, and
a card crossing one left the set entirely rather than getting smaller.

A group is sized by its OWN rectangle, never by how much of it is on screen. An item running off the
bottom or the right is the same item it would be if it fitted, and its height is read unchanged —
which is the point: a feed scrolled halfway must report the same shape it reports when settled.""",
  why="""These five replaced a single <code>item_h</code> that came from whichever group covered the most area.
A screen normally has several repeating things at once — a feed has cards and an action rail — and
the winner-take-all read discarded the rest. Worse, when two groups covered similar area the pick
alternated between samples: one screen crossed between two clusters sixteen times without changing.""",
  tells="""The shape of the repeating unit, which is most of what distinguishes a layout: a 70-permille
row (band 1) is a settings list, a 400-permille card (band 3) is a feed, an 850-permille item
(band 4) is a full-bleed player. A screen with mass in two bands genuinely has two kinds of item.""",
  cost_script="O(L) to group + O(G · R log R) for the area unions. Part of the 2.66 ms.",
  cost_tree="The leaf rects are already laid out. One pass over the children of the owning window plus one interval union per group. Sub-millisecond for a 500-node tree; no text is involved.",
  norm="Already a share in 0–1, so <code>unit</code> does nothing but clamp. Then z-scored and × 0.45. The weights of the five sum in square to the 1.0 the single <code>item_h</code> carried, so splitting the feature into five did not quietly increase its influence.",
  example="""three leaves of 1268 × 1690 px, density 3.5, display height 2832 px
  → group key (362, 483) vp, 3 members, union area 2 142 920 px²
  → item height 1690 / 2832 = 0.597 of the display
  → log-interpolated between band 3 (0.40) and band 4 (0.85): 0.53 / 0.47
  → with a second group of 1268 × 92 rows contributing to band 0
  → item_h0 0.31   item_h1 0.00   item_h2 0.00   item_h3 0.36   item_h4 0.33""",
  extra="""The sixteen layouts below are rectangle lists run through this exact code, so the numbers
under each picture are computed, not illustrative. Highest SHAP attribution of any feature group, and
dropping the block entirely costs real structure — silhouette 0.476 → 0.405 — so it is signal.
<br><br><b>Superseded by rs_pat_*, which is in the same group in the menu.</b>
The replacement groups BRANCHES by what they contain and how it is arranged, rather than leaves by
their size, and separates overlays from layout. <b>See the observed section underneath for why.</b> Checked against the capture, the layouts are
worked cases rather than motifs that occur: six of fourteen never appear, and half of all samples emit
a band profile while their repeating structure covers under 5% of the window. Because the bands are
shares they look equally confident either way, which is a defect in the feature as designed, not in
the measurement.""")

f(name="rs_item_ar0 … rs_item_ar2", group="Repeat shape", status="live",
  weight="0.58 each", rng="0 – 1, the three sum to 1",
  origin="RSTree, pass 1 — the same leaf rects as the height bands",
  raw="""innerAbsDrawRect: [24, 310, 1268, 1690]   → 1268 wide, 1690 tall
innerAbsDrawRect: [24, 112, 1268, 92]     → 1268 wide, 92 tall""",
  extract="Same grouping. Each group's aspect is width / height, spread over three band centres — 0.35 (tall), 1.0 (square), 3.0 (wide) — by log interpolation, weighted by the group's covered area, then normalised to shares.",
  why="Height alone does not separate a tall card from a wide banner of the same area. Aspect was previously one number from the winning group, with the same flipping problem, and with the additional defect that a missing value was written as 1.0 — indistinguishable from a genuinely square item, on 1,266 samples.",
  tells="Whether the repeating unit is a row (wide), a tile (square) or a card/poster (tall). A gallery and a chat list can share an item height and differ entirely here.",
  cost_script="Free — computed in the same loop as the height bands.",
  cost_tree="Free, same pass.",
  norm="<code>unit</code>, z-scored, × 0.58. Three bands at 0.58 sum in square to the 1.0 the old <code>item_aspect</code> carried.",
  example="""group A 1268 × 1690 → aspect 0.75, between band 0 (0.35) and band 1 (1.0)
group B 1268 ×   92 → aspect 13.8, above band 2 (3.0) → all mass to band 2
  → item_ar0 0.22   item_ar1 0.45   item_ar2 0.33""",
  extra="Under the old argmax, <code>item_aspect</code> ranked top by SHAP but was dropped first by the collinearity prune — it correlated −0.57 with item_h. The band form separates the two.")

f(name="rs_item_cols", group="Repeat shape", status="live",
  weight="1.2 — the highest in the vector", rng="0 – 6 after clipping",
  origin="RSTree, pass 1 — x coordinates of repeating leaves",
  raw="""innerAbsDrawRect: [  24, 310, 640, 640]
innerAbsDrawRect: [ 676, 310, 640, 640]
innerAbsDrawRect: [  24, 970, 640, 640]""",
  extract="For each repeating group, the number of distinct x positions with members, tolerated to 40 px (a grid's columns are not pixel-exact). Averaged across groups, weighted by covered area.",
  why="Named the single best axis on the original vector and it survived every pruning. A gallery and a feed differ here before they differ anywhere else. It was previously the winning group's column count only.",
  tells="One column is a list or a feed; two to four is a grid or gallery; more is a dense tile wall. Combined with the aspect bands it distinguishes a 2-column poster grid from a 2-column icon grid.",
  cost_script="O(L) — a set of rounded x values per group.",
  cost_tree="Free from laid-out child rects.",
  norm="<code>clip6</code> — anything above six columns reads the same, because beyond that the distinction stops being structural. Then z-scored and × 1.2.",
  example="x values 24, 676, 24 → rounded /40 → {0, 16} → 2 distinct columns → rs_item_cols 2.0",
  extra="Carries 11.5% of the overall distance between scenes but only 1.4% of the near-pair distance, which is why it had almost no say in deduplication under the old metric.")

f(name="rs_repeat_frac", group="Repeat shape", status="live",
  weight="1.0", rng="0 – 1",
  origin="RSTree, pass 1 — union of the winning repeating group's rects",
  raw="three leaves 1268 × 1690 at y = 310, 2016, 3722; contributor visible area 1316 × 2832",
  extract="Union area of the best-scoring group, divided by the contributor's own visible area, clamped to 1.",
  why="Separates how MUCH of the screen repeats from what shape the repeats are — the bands carry shape, this carries magnitude. Keeping them apart stops the block from stating the same fact twice.",
  tells="A feed is mostly repeating content (0.6–0.9); a settings page has a repeating list inside chrome (0.3–0.5); a video player or a form has little or none.",
  cost_script="One sweep-line union, O(R log R). The largest such call in a sample was 160 rects.",
  cost_tree="Same union, on rects already available.",
  norm="<code>unit</code>, z-scored, × 1.0.",
  example="union 2 142 920 px² ÷ own 3 726 912 px² = 0.575 → rs_repeat_frac 0.575",
  extra="Once read 2.551 — a sum of overlapping member areas, which is not a fraction of anything. Fixed to a union.")

f(name="rs_repeat_n", group="Repeat shape", status="pruned",
  weight="0.7 (dropped at --decorrelate 0.55)", rng="log1p of a count",
  origin="RSTree, pass 1",
  raw="the winning group's member list",
  extract="Member count of the best-scoring repeating group.",
  why="Written as a direct measure of how many repeating items are on screen.",
  tells="In principle, list length. In practice it tracks what is visible rather than what exists, because only rendered nodes are in the tree.",
  cost_script="Free — a list length.",
  cost_tree="Free.",
  norm="<code>log1p</code>, z-scored, × 0.7.",
  example="3 members → log1p(3) = 1.386",
  extra="Correlates 0.81 with <code>rs_has_repeat</code> and 0.60 with <code>rs_repeat_frac</code>. It is the first feature the prune removes at any threshold, and removing it raises the silhouette. It stays in the CSV for reading, not for clustering.")

f(name="rs_has_repeat", group="Repeat shape", status="live",
  weight="0.7", rng="0 or 1",
  origin="RSTree, pass 1",
  raw="presence or absence of any group with two or more members",
  extract="1 if any repeating group exists on the owning contributor, 0 otherwise.",
  why="""When no group exists the shape features have nothing to report. Writing 0.0 for item height
said 'items of no height', which on a continuous scale reads as the smallest possible items — the
opposite end from a full-height card. They are now written empty and imputed to the column mean,
and this flag says whether there was anything to measure at all.""",
  tells="Whether the screen has repeating structure. A player, a splash and a single form read 0.",
  cost_script="Free.",
  cost_tree="Free.",
  norm="<code>flag</code>, z-scored, × 0.7.",
  example="score dictionary non-empty → rs_has_repeat 1",
  extra="One of the four exact-match fields in the deduplication rule: two measurements that disagree here are never the same measurement, whatever else agrees.")

# ------------------------------------------------------------------ content
f(name="rs_text_frac", group="Content", status="live",
  weight="0.75", rng="0 – 1",
  origin="RSTree pass 1 for the rects, <code>hidumper -s RenderService -a client</code> for the tag",
  raw="""client:  | CanvasNode[15844134355211], ..., frameNodeId[2718], frameNodeTag[Text]
RSTree:  | CANVAS_NODE[15844134355211], ... innerAbsDrawRect: [48, 1204, 980, 64]""",
  extract="Nodes are joined to their component tag by node id — the join succeeds on 100% of nodes. Every node tagged <code>Text</code> contributes its VISIBLE area (its rect intersected with the contributor's visible region). The sum is divided by the contributor's visible area.",
  why="A form and a player differ here more than anywhere else. The content fractions sit below the structural features on purpose: at equal weight they split three sets of screens a reader called alike, on text alone.",
  tells="How much of the screen is reading matter. Chat and articles run high; galleries and players run near zero.",
  cost_script="O(N) accumulation in the single measurement pass, plus one extra dump to fetch the tags.",
  cost_tree="Free and better — ArkUI knows the component type directly, with no id join and no second dump.",
  norm="<code>unit</code>, z-scored, × 0.75. The 0.75 is the one deliberate departure from parity and was swept: at 0.5 a cluster a reader endorsed broke in two; at 1.0 three alike screens split on text alone.",
  example="Text nodes covering 412 300 px² ÷ own 3 726 912 px² = 0.111 → rs_text_frac 0.111",
  extra="Low SHAP but low ablation ARI (0.72) — it decides which scenes are outliers rather than which cluster a core scene joins.")

f(name="rs_image_frac", group="Content", status="pruned",
  weight="0.75 (dropped at --decorrelate 0.55)", rng="0 – 1",
  origin="Same as text_frac, tag <code>Image</code>",
  raw="frameNodeTag[Image] joined to innerAbsDrawRect",
  extract="Visible area of all Image-tagged nodes ÷ contributor visible area.",
  why="The complement of text: how much of the screen is picture.",
  tells="Galleries, feeds and players run high; settings and forms run near zero.",
  cost_script="Free — same pass as text_frac.",
  cost_tree="Free.",
  norm="<code>unit</code>, z-scored, × 0.75.",
  example="Image nodes covering 2 977 000 px² ÷ 3 726 912 = 0.799 → rs_image_frac 0.799",
  extra="Correlates 0.62 with <code>rs_surface_tag</code> — a video surface is counted as picture by both — so the prune drops it at 0.60 and below.")

f(name="rs_sym_frac", group="Content", status="live",
  weight="0.375 — the lowest in the vector", rng="0 – 1",
  origin="Same as text_frac, tag <code>SymbolGlyph</code>",
  raw="frameNodeTag[SymbolGlyph]",
  extract="Visible area of all SymbolGlyph nodes ÷ contributor visible area.",
  why="Glyph chrome — toolbars, tab rows, icon strips — is structurally different from text and from images and was otherwise invisible to the vector.",
  tells="How much of the screen is icon furniture. High on a launcher or a tool row, near zero inside content.",
  cost_script="Free — same pass.",
  cost_tree="Free.",
  norm="<code>unit</code>, z-scored, × 0.375 — half the other content fractions, because icons are small and their area understates their structural meaning.",
  example="Symbol nodes covering 31 400 px² ÷ 3 726 912 = 0.008 → rs_sym_frac 0.008",
  extra="Correlates −0.78 with <code>rs_top_weight</code>. Both sit at the bottom of SHAP and ablation; this one survives only because it is cheap and occasionally decisive on launcher screens.")

f(name="rs_paint_frac", group="Content", status="live",
  weight="0.75", rng="0 – 1",
  origin="RSTree pass 2 — <code>DrawableVec</code>",
  raw="""RS_NODE[0], hasRenderNode: 1, DrawableVec:[24, 29, 31], ... shouldPaint_[1]""",
  extract="Slot 28 of DrawableVec is the content pass; slot 29 is children-only. A node whose vector contains 28 actually paints something itself. Their visible areas are summed and divided by the contributor's visible area.",
  why="""Distinguishes a screen that is full of marks from a screen that is full of boxes. The obvious
field, <code>hasDrawContent</code>, is not a content flag — it was set on 15 of 501 nodes, all of them
text — so the drawable vector is read instead.""",
  tells="How much of the layout is actually inked. A deep container hierarchy with little paint is a scaffold; high paint with few nodes is a media surface.",
  cost_script="O(N) over pass 2, which is why both passes are parsed.",
  cost_tree="Free — the drawable state is what ArkUI just computed.",
  norm="<code>unit</code>, z-scored, × 0.75.",
  example="painting leaves union 3 310 000 px² ÷ 3 726 912 = 0.888 → rs_paint_frac 0.888",
  extra="Also the basis of each contributor's <code>weight</code>, which decides who owns the scene.")

# ----------------------------------------------------------------- topology
f(name="rs_node_den", group="Topology", status="pruned",
  weight="1.0 (dropped at --decorrelate 0.55)", rng="log1p, typically 0.2 – 1.2 before transform",
  origin="RSTree pass 1 — count of admitted nodes, and the contributor's visible area",
  raw="227 admitted nodes; visible region [0, 0, 1316, 2832]; density 3.5",
  extract="Admitted nodes ÷ (visible area in vp² / 1000). Density-relative so the number transfers between devices rather than tracking the panel.",
  why="How crowded the composition is, independent of screen size.",
  tells="A dense settings list against a sparse player. In practice it moves with depth and fan-out rather than independently.",
  cost_script="Free — a count divided by an area already computed.",
  cost_tree="Free.",
  norm="<code>log1p</code>, z-scored, × 1.0.",
  example="227 nodes ÷ (3 726 912 / 3.5² / 1000) = 227 / 304.2 = 0.746 → log1p = 0.557",
  extra="Correlates 0.73 with <code>rs_depth_mean</code> and 0.62 with <code>rs_repeat_n</code>. Pruned at 0.60 and below; the information survives in depth and fan-out.")

f(name="rs_depth_mean", group="Topology", status="pruned",
  weight="0.7 (dropped at --decorrelate 0.55)", rng="log1p of a depth, typically 5 – 25",
  origin="RSTree pass 1 — indentation of each line relative to the owning surface",
  raw="""      | CANVAS_NODE[...]      ← six spaces, depth 3
        | CANVAS_NODE[...]    ← eight spaces, depth 4""",
  extract="Depth is read from the dump's own indentation, then taken relative to the owning surface's depth so a window nested deeper in the display tree is not counted as a deeper layout. Averaged over admitted nodes.",
  why="Separates deep-and-sparse hierarchies from shallow-and-dense ones.",
  tells="Component composition style more than scene type — a framework that wraps heavily reads deep regardless of what is on screen.",
  cost_script="Free — a character count per line, during parsing.",
  cost_tree="Free and exact: real tree depth rather than inferred from text layout.",
  norm="<code>log1p</code>, z-scored, × 0.7.",
  example="depths 3, 4, 4, 5, 6 relative to surface → mean 4.4 → log1p = 1.686",
  extra="Derived from the dump's TEXT FORMATTING, which is the most fragile provenance of any feature here — a change to how RenderService indents would silently change it. Inside ArkUI this concern disappears.")

f(name="rs_leaf_frac", group="Topology", status="live",
  weight="0.7", rng="0 – 1",
  origin="RSTree pass 1 — <code>sortedChildren</code>",
  raw="sortedChildren: 1(8061653614592 )     ← one child, not a leaf\nsortedChildren: 0()                   ← a leaf",
  extract="Nodes with no children ÷ all admitted nodes.",
  why="A direct read of how much of the tree is structure and how much is content.",
  tells="A high leaf fraction means a flat sheet of items; a low one means deep wrapping.",
  cost_script="Free.",
  cost_tree="Free.",
  norm="<code>unit</code>, z-scored, × 0.7.",
  example="86 leaves of 227 admitted → 0.378 → rs_leaf_frac 0.378",
  extra="Second-highest SHAP in the original set but ablation ARI 0.97 — it describes the partition without deciding it.")

f(name="rs_fanout", group="Topology", status="live",
  weight="0.7", rng="log1p, typically 1 – 8",
  origin="RSTree pass 1 — <code>sortedChildren</code> counts",
  raw="sortedChildren: 3(1584... 1584... 1584... )",
  extract="Mean number of children over nodes that have at least one. Childless nodes are excluded so the number means branching, not sparsity — that is what leaf_frac is for.",
  why="Branching width: a list of forty siblings and a chain of forty wrappers have the same node count and nothing else in common.",
  tells="Wide branching is a container of many items; narrow branching is a decoration chain.",
  cost_script="Free.",
  cost_tree="Free.",
  norm="<code>log1p</code>, z-scored, × 0.7.",
  example="child counts 1, 1, 3, 8 → mean 3.25 → log1p = 1.447",
  extra="The single most decisive feature by ablation — removing it moves the partition further than removing any other (ARI 0.62) — while sitting mid-table on SHAP.")

# -------------------------------------------------------------- composition
f(name="rs_surface_tag", group="Composition", status="live",
  weight="1.0", rng="0 – 4 after clipping",
  origin="<code>client</code> dump tags joined to RSTree nodes",
  raw="frameNodeTag[XComponent] · frameNodeTag[Web] · frameNodeTag[UIExtensionSurface]",
  extract="Count of nodes whose tag matches XComponent, Web, UIExtension, Embedded or Plugin.",
  why="A video or web surface is a different kind of scene from a laid-out component tree, and nothing else in the vector sees it — such a surface is one node that paints a whole frame.",
  tells="Presence of externally-rendered content. The strongest single indicator of a player or an embedded browser, which matters because their frame cost is not predictable from their node count.",
  cost_script="Free once tags are joined; needs the second dump.",
  cost_tree="Free and direct.",
  norm="<code>clip4</code> — four such surfaces and forty mean the same thing. Then z-scored, × 1.0.",
  example="one XComponent node found → rs_surface_tag 1 → clip4 → 1.0",
  extra="One of the four exact-match fields in the deduplication rule. One step of it is 1.26 standard deviations — the largest single-step cost of any feature.")

f(name="rs_scroller", group="Composition", status="pruned",
  weight="0.7 (dropped at --decorrelate 0.55)", rng="0 – 4 after clipping",
  origin="<code>client</code> dump tags",
  raw="frameNodeTag[List] · frameNodeTag[Grid] · frameNodeTag[Swiper] · frameNodeTag[Tabs]",
  extract="Count of nodes tagged List, Grid, Swiper, Scroll, Tabs, WaterFlow or Navigation.",
  why="Scrollable containers are what make a scene's cost depend on interaction rather than on its static composition.",
  tells="How many independently scrollable regions exist. A tabbed feed with a horizontal rail reads higher than a single list.",
  cost_script="Free once tags are joined.",
  cost_tree="Free.",
  norm="<code>clip4</code>, z-scored, × 0.7.",
  example="a List and a Tabs node → 2 → clip4 → 2.0",
  extra="One of the four exact-match deduplication fields, and pruned from the distance at 0.55 for correlating 0.56 with text_frac and repeat_n. It still decides what counts as the same measurement.")

f(name="rs_bar_top", group="Composition", status="live",
  weight="0.5", rng="0 – 1, in practice 0 – 0.12",
  origin="RSTree pass 1 — leaf rects against the display edges",
  raw="innerAbsDrawRect: [0, 0, 1316, 112]    ← full width, thin, at the top",
  extract="Among leaves at least 90% of display width and at most 12% of display height, those within the top 10% of the display. The tallest such bar's height, as a fraction of display height.",
  why="Chrome is the clearest structural difference between a player and a page, and it is invisible to every other feature — a status bar is a few small nodes of negligible area.",
  tells="Whether the scene has a top bar at all, and how tall. Immersive content hides it; pages keep it.",
  cost_script="O(L) with a constant test.",
  cost_tree="Free; the window's layout already knows its insets, more reliably than inferring from rects.",
  norm="<code>unit</code>, z-scored, × 0.5.",
  example="a 1316 × 112 leaf at y = 0 → 112 / 2832 = 0.040 → rs_bar_top 0.040",
  extra="Only eleven distinct values occur across the session — effectively a categorical feature wearing a continuous scale.")

f(name="rs_bar_bot", group="Composition", status="live",
  weight="0.5", rng="0 – 1, in practice 0 – 0.12",
  origin="RSTree pass 1 — leaf rects against the display edges",
  raw="innerAbsDrawRect: [0, 2748, 1316, 84]   ← full width, thin, at the bottom",
  extract="Same test, for bars whose bottom edge reaches at least 90% down the display.",
  why="Navigation and tab bars, and their absence in full-screen content.",
  tells="Whether the scene keeps its navigation visible. Together with bar_top it reads immersive-versus-chromed directly.",
  cost_script="Free — same loop as bar_top.",
  cost_tree="Free.",
  norm="<code>unit</code>, z-scored, × 0.5.",
  example="a 1316 × 84 leaf at y = 2748 → 84 / 2832 = 0.030 → rs_bar_bot 0.030",
  extra="Dropping it IMPROVES the silhouette (0.482 against 0.466 baseline) while barely moving the partition — a candidate for removal that was kept because it is interpretable and free.")

f(name="rs_split_n", group="Composition", status="live",
  weight="0.7", rng="0 – 6 after clipping",
  origin="RSTree pass 1 — vertical extents of wide, tall leaves",
  raw="""innerAbsDrawRect: [0,  112, 1316,  900]
innerAbsDrawRect: [0, 1012, 1316, 1736]""",
  extract="Among leaves at least 60% of display width and at least 5% of display height, the number of distinct (top, bottom) pairs rounded to 60 px. Distinct horizontal bands, in other words.",
  why="A split screen and a single pane differ here and nowhere else in the vector.",
  tells="How many stacked content regions the scene has — a video over a comment list reads 2, a plain feed reads 1.",
  cost_script="O(L), a set of rounded pairs.",
  cost_tree="Free.",
  norm="<code>clip6</code>, z-scored, × 0.7.",
  example="bands (2, 17) and (17, 46) after /60 → 2 distinct → rs_split_n 2",
  extra="The dominant term in near-pair distance — 45% of it — because one step costs 0.41 standard deviations, far more than the old 0.25 deduplication threshold. It is now one of the four exact-match fields instead of pretending to be continuous.")

# --------------------------------------------------- occlusion and display
f(name="rs_contributors", group="Occlusion", status="constant",
  weight="0.7 (no variance this session)", rng="0 – 4 after clipping",
  origin="RSTree pass 1 — surface nodes with <code>Visible: 1</code>, alpha above zero and a non-empty visible region",
  raw="""| SURFACE_NODE[15844134354955], ..., Name [SCBWallpaper6], Alpha: 1.000000,
  Visible: 1, VisibleRegion [Empty], OpaqueRegion 1: [0, 0, 1316, 2832]""",
  extract="Count of surfaces that survive the live test, paint something, and are not nested inside another surface. A contributor is a WINDOW, not a surface: the outermost surface owns the chain and nested ones merge into it.",
  why="A scene composed from one window is a different object from a scene composed from five sharing a display, and the whole reason the scene is defined at display level rather than window level.",
  tells="How many windows the compositor is actually combining. Relevant to composition cost in a way no single-window measure can be.",
  cost_script="O(S) over surfaces, plus one union per surface to test for emptiness.",
  cost_tree="Not available inside one application's ArkUI — this is a compositor-level fact. It would come from RenderService or the window manager.",
  norm="<code>clip4</code>, z-scored, × 0.7.",
  example="eleven surfaces parsed → four live, non-nested, painting → rs_contributors 4",
  extra="Constant in this session, so it was automatically excluded from the distance. It will vary in a system-wide capture and is kept for that reason.")

f(name="rs_cover_opaque", group="Occlusion", status="constant",
  weight="0.5 (no variance this session)", rng="0 – 1",
  origin="RSTree pass 1 — <code>OpaqueRegion</code> on window-level surfaces",
  raw="OpaqueRegion 1: [0, 0, 1316, 2832]",
  extract="Union of the opaque regions of every live surface and its nested chain, divided by the full display area. Regions are joined by SURFACE NAME, because the window-manager id and the digits in the RS name are not the same number.",
  why="Says how much of the display is guaranteed covered — what is behind it cannot matter.",
  tells="Whether anything beneath the top window can contribute at all. A full-screen opaque app reads 1.0; a dialog over a blurred background reads lower.",
  cost_script="One sweep-line union over the opaque rects, O(R log R).",
  cost_tree="Compositor-level; not visible from inside an application.",
  norm="<code>unit</code>, z-scored, × 0.5.",
  example="single opaque region 1316 × 2832 ÷ 3 726 912 = 1.0 → rs_cover_opaque 1.0",
  extra="<code>VisibleRegion</code> reads <code>Empty</code> on nested surfaces and is only populated at window level — reading it naively made contributors vanish.")

f(name="rs_uncovered", group="Occlusion", status="constant",
  weight="0.5 (no variance this session)", rng="0 – 1",
  origin="Derived from rs_cover_opaque",
  raw="—",
  extract="1 − cover_opaque, floored at zero.",
  why="Kept as its own column so that a rule reading 'how much is exposed' does not have to invert another feature.",
  tells="Exposed display area. Exactly complementary to cover_opaque, so it carries no independent information.",
  cost_script="Free.",
  cost_tree="Free.",
  norm="<code>unit</code>, z-scored, × 0.5.",
  example="cover_opaque 1.0 → rs_uncovered 0.0",
  extra="Strictly redundant by construction. It survives only because it was constant this session and so never reached the correlation prune, which would certainly have removed it.")

f(name="rs_top_weight", group="Occlusion", status="pruned",
  weight="0.7 (dropped at --decorrelate 0.55)", rng="0 – 1",
  origin="RSTree pass 1 — painted leaf area of each contributor",
  raw="paint_px 3 310 000; usable display area 3 726 912",
  extract="Each contributor's weight is the union of its painting leaf rects ÷ the usable display area (display minus unavailable region and cutout). top_weight is the weight of the contributor that owns the scene — the frontmost, by z order, whose weight is at least 0.2.",
  why="""Says whether one window owns the scene or several share it. The owner selection matters more than
the number: picking the biggest painter chose the WALLPAPER on a launcher screen, so it is now the
frontmost window that paints a meaningful share.""",
  tells="Whether the scene is one application's or a composite. Low values mean no window dominates.",
  cost_script="One sweep-line union per contributor, O(L log L) — the most expensive per-contributor step.",
  cost_tree="Compositor-level.",
  norm="<code>unit</code>, z-scored, × 0.7.",
  example="union of painting leaves 3 310 000 ÷ 3 726 912 = 0.888 → rs_top_weight 0.888",
  extra="Lowest SHAP of all 21 live features and ablation ARI 0.98 — it neither defines nor decides the partition, and the prune removes it at every threshold. The quantity still matters: it is what selects the owning contributor, from which every arrangement feature is read.")

f(name="rs_pat_* (repeating layout pattern)", group="Repeat shape", status="prototype",
  weight="not yet in the vector", rng="see each column below",
  origin="RSTree pass 1 and 2 for geometry and paint state, <code>client</code> for component tags",
  raw="""client:  frameNodeId[2718], frameNodeTag[Image]
RSTree:  | CANVAS_NODE[...] innerAbsDrawRect: [24, 310, 622, 622] sortedChildren: 2(...)
         | CANVAS_NODE[...] innerAbsDrawRect: [24, 310, 622, 480]   <- image
         | CANVAS_NODE[...] innerAbsDrawRect: [24, 800, 560,  52]   <- label""",
  extract="""Replaces the leaf grouping entirely. What repeats in a layout is a BRANCH - a cell holding an
icon and a label, a card holding an image and two lines - not a leaf, which is wherever RenderService
stopped descending.

Siblings are sorted into three relationships, which the band feature never separated:
<br>&nbsp;&nbsp;<b>coincident</b> — a node and its child in the same box, within 10% of its area. A wrapper,
a clip, an effect layer. Collapsed before anything is measured; these are most of why the render tree
is deeper than the layout.
<br>&nbsp;&nbsp;<b>overlay</b> — a smaller node, under 40% of the area, sitting at least 80% inside an
EARLIER-painted sibling. The caption and the like counter over a video. Removed from the pattern and
reported separately as <code>overlay_frac</code>.
<br>&nbsp;&nbsp;<b>tiling</b> — siblings sharing edges rather than area. The layout, and the only
candidates for a repeat.

Each remaining branch is described by the content kinds in its subtree, sorted: <code>I</code> image,
<code>T</code> text, <code>S</code> glyph, <code>V</code> video surface. Where the tag is a wrapper —
on this app 103 of 135 leaves are tagged Custom or Stack — the kind is inferred from geometry instead:
a small square is <code>i</code>, a wide thin box <code>t</code>. Siblings sharing a description are a
repeating group; the group covering the most area wins, and its arrangement comes from the distinct x
and y positions of its members.""",
  why="""The band feature grouped leaf rectangles, and a leaf is a render-tree fact, not a layout one.
On a launcher that accidentally worked because every icon is 99x99. On a video feed it reported the
like button and the progress rail, because the feed's own full-screen items were excluded for being
over 75% of the display. Checked against the capture, half of all samples emitted a confident band
profile while their repeating structure covered under 5% of the window.""",
  tells="""The actual layout: a cell description, how it repeats, and over how much of the window.
<code>media card grid 2x2</code> at 0.76 cover was a video with a recommendation grid beneath it in
every screenshot checked; <code>image grid 3x4</code> at 0.75 was a dense three-column wall every
time.""",
  cost_script="""O(N) to collapse and classify siblings, O(C²) per branch for the overlay test where C
is its child count (small - the median branch has 2), O(N) for the patterns, one sweep-line union per
candidate group. Measured at 250 scenes in a few seconds, same order as the existing extractor.""",
  cost_tree="""Cheaper and better inside ArkUI. The wrapper collapse is unnecessary because the
component tree does not have those layers; the component type is on every FrameNode with no dump and
no id join; and a scroller's real item list is available including virtualised items, which the dump
cannot show.""",
  norm="""Not yet normalised - this is a prototype and none of it is in the clustering vector. The
intended shape: <code>pat_cover</code> and <code>overlay_frac</code> as <code>unit</code>,
<code>pat_cols</code> as <code>clip6</code>, <code>pat_h</code> as <code>unit</code>,
<code>pat_aspect</code> as <code>logratio</code>, and the motif as a categorical for the
deduplication key rather than for the distance.""",
  example="""a 2-column gallery cell: branch 622x622 px holding an image and a label
  collapse       one coincident wrapper removed
  overlay test   none; the two children tile
  kinds          {I, T}  ->  pattern "IT"
  siblings       8 branches share "IT", union 2.4 Mpx of a 3.7 Mpx window
  arrangement    2 distinct x, 4 distinct y  ->  grid 2x4
  result         motif "icon and label grid 2x4", pat_cover 0.65, pat_h 0.22""",
  extra="""<b>Coverage is the honest gate.</b> Every motif above 0.6 cover was consistent by eye.
Below 0.1 they were all full-screen video, named after whatever small thing repeated on top: the
action rail became "icon and label list", the caption became "text list". So the name is withheld
below 0.15 and reads "no dominant repeat" - 81 of 250 scenes, median cover 0.04. Overlay density does
not help separate those (r = +0.10 against cover), so coverage is the only gate used.
<br><br>Still open: 20 of 250 scenes produce no pattern at all, the <code>b</code> kind is still
a third of the alphabet on this app, and nothing here has been weighted or clustered yet.""")

f(name="rs_pat1_* / rs_pat2_* (two pattern slots)", group="Repeat shape", status="prototype",
  weight="slot 1 at parity, slot 2 at 0.7 of it", rng="per column, as the single-slot version",
  origin="the same branch detection as rs_pat_*",
  raw="""one scene, two repeating groups found under the owning window:
  group A   {IIITTT} x4   grid, 2 columns   cover 0.69   top 0.31
  group B   {it}     x5   column            cover 0.11   top 0.86""",
  extract="""The detector already finds a median of SIX repeating groups per scene and 87% of scenes
have two or more; the single-slot version reported the largest and discarded the rest. Both slots are
now emitted, ordered by coverage with the tie broken on vertical position — topmost first — rather
than on the quantity that is nearly tied, so two groups of similar size cannot swap slots between
samples and jump the whole vector.

An absent second group writes EMPTY, not zero, and <code>has_slot2</code> records the absence. Writing
a zero coverage and a zero height would place a missing group at the small end of both scales instead
of nowhere, which is the mistake <code>item_h</code> made on 1,266 samples.""",
  why="""A screen with a video above a comment list has two structures and the single slot reported
one of them, with nothing in the vector to say which half was being described.""",
  tells="Both of a screen's structures at once: a grid under a player, a feed beside an action rail.",
  cost_script="Free — the groups were already computed and then thrown away.",
  cost_tree="Free.",
  norm="Slot 2 at 0.7 of slot 1's weights, so a secondary structure cannot outvote the primary one.",
  example="""slot1  cover 0.69  h 0.39  aspect 0.57  cols 2  n 4   -> "media card grid, 2 columns"
slot2  cover 0.11  h 0.05  aspect 7.6  cols 1  n 5   -> "icon and label list"
has_slot2 1""",
  extra="""<b>Measured, and it does not pay.</b> Over 179 scenes, adding slot 2 raised the share of
scenes fitting no cluster from 8–20% to 13–35% while moving the silhouette both ways, and no weight
between 0.25 and 1.0 recovered it. Slot 1 alone remains the best vector measured. The columns are
computed and written; nothing is in the clustering vector.
<br><br>The likeliest cause is sample count, not the feature: 179 scenes is thin for 13 dimensions.
The pattern features need the <code>client</code> dump, which the fast capture writes once per
distinct scene rather than once per sample, so 3,263 samples yield only 250 usable scenes. Settling
this needs that capture change first.""")

f(name="rs_surf_* (external surface, graded)", group="Composition", status="prototype",
  weight="not yet in the vector", rng="kind 0 or 3; bands 0–1 summing to 1",
  origin="<code>client</code> tag for the kind, RSTree pass 1 for geometry and paint order",
  raw="""client: frameNodeId[3021], frameNodeTag[XComponent]
RSTree: | CANVAS_NODE[...] innerAbsDrawRect: [0, 899, 1316, 740]""",
  extract="""The largest node carrying an externally-rendered surface tag — XComponent, Web, Video,
UIExtension, Embedded, Plugin — clipped to the window.

Measured UNOCCLUDED. A player with a comment sheet pulled over it is a different scene from the same
player uncovered, and the raw rect calls them identical. Every LEAF painted after the surface that
lands on it is subtracted. Three exclusions, each one found by a wrong answer:
<br>&nbsp;&nbsp;— branches, because a painting branch is a scrim or wrapper whose children are the
real content; two untagged full-window branches once hid a player completely;
<br>&nbsp;&nbsp;— nodes sharing the surface's box, because a player's poster frame is drawn as an
Image at exactly the XComponent's rect; this alone hid the video on 70 of 88 scenes;
<br>&nbsp;&nbsp;— nodes larger than one and a half windows, the same 8198x8198 blur and shadow
sources at negative origins that the pattern block already bounds.

Coverage is then GRADED, not thresholded: four soft bands at a quarter, a half, three quarters and
the whole window, interpolated between neighbours so a surface growing across a boundary moves mass
gradually. A quarter of the window and the whole of it are different scenes with different costs;
one flag said they were the same.""",
  why="""Half the captured scenes have no dominant repeat and were described only by what they
lacked. An external surface is the one thing in the tree whose frame cost is not predictable from
node count, which is exactly where a scene class has to carry the information.""",
  tells="""Whether playback holds the scene, and how much of the window it actually occupies once
everything drawn over it is taken off. The flagged half differs from the rest where a power model
would look: draw slots at 1.00 standardised separation, image fraction 0.96, node count 0.64,
overlay fraction 0.60, animation count 0.45.""",
  cost_script="O(N) for the surface, O(N) again for the occluders, one sweep-line union. Negligible.",
  cost_tree="Free; ArkUI knows which node is an XComponent, and knows what is drawn over it.",
  norm="Bands as <code>unit</code>, <code>surf_kind</code> as a flag, <code>surf_vis</code> as <code>unit</code>.",
  example="""XComponent 1316 x 740 px on a 1316 x 2832 window, a comment list drawn below it
  raw cover 0.261
  occluders: poster frame at the same rect (excluded), 2 HUD icons, 1 label
  visible 0.253  ->  bands  b0 0.99  b1 0.01  b2 0.00  b3 0.00""",
  extra="""<b>What it is good for, and what it is not.</b> The flag separates playback from the rest
of the app cleanly — the pictures agree, and it is NOT the same as <code>rs_surface_tag</code>, which
it matches on only 60% of scenes: that counts matching nodes anywhere, this identifies a scene HELD
by one.
<br><br>Grading made the measure real: raw coverage took 12 distinct values over 88 scenes and
visible coverage takes 39. Occlusion costs a median 0.22 of the window and more than 5% on 69% of
them, so it is not a correction one can skip.
<br><br>Still wrong in one place: a player above a comment sheet can read near zero when the video is
plainly visible. One such scene survives in the "about a quarter or less" strip below, and it is the
layout to fix next.
<br><br>Earlier versions of this block accepted any large painting node and ranked image over plain
box. 167 scenes of that were the window's background fill or a blurred backdrop drawn as one
full-window Image leaf, all reporting cover 1.00 on comment pages, shopping pages and list screens
alike. Two rules were tried to tell a backdrop from content — counting painting descendants, and
measuring what is painted on top — and neither worked, so the kind comes from the tag alone.""")
