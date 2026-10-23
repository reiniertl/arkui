import json, html
import feature_doc as data

import json as _j
SHAPES = _j.load(open("shapes.json", encoding="utf-8"))
REPS = _j.load(open("reps_payload.json", encoding="utf-8"))
PATS = _j.load(open("pat_payload.json", encoding="utf-8"))
SURF = _j.load(open("surf_payload.json", encoding="utf-8"))
payload = {"features": data.F, "common": data.COMMON, "shapes": SHAPES, "reps": REPS, "pats": PATS, "surf": SURF}

HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scene Feature Reference</title>
<style>
  :root { --bg:#ffffff; --fg:#16181d; --dim:#6b7280; --line:#e3e6eb;
    --card:#f7f8fa; --accent:#2563eb; --teal:#0f766e; --amber:#b45309;
    --mute:#64748b; --code:#eef1f5; }
  @media (prefers-color-scheme: dark) {
    :root:not([data-theme="light"]) { --bg:#0f1115; --fg:#e6e8ec; --dim:#9aa1ad;
      --line:#272b33; --card:#171a20; --accent:#60a5fa; --teal:#5eead4;
      --amber:#fbbf24; --mute:#94a3b8; --code:#11141a; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
    font:15px/1.6 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  .shell { display:flex; min-height:100vh; align-items:flex-start; }
  nav { width:268px; flex:0 0 268px; border-right:1px solid var(--line);
    padding:20px 0 60px; position:sticky; top:0; max-height:100vh; overflow-y:auto; }
  nav h1 { font-size:15px; margin:0 18px 4px; }
  nav .sub { font-size:11.5px; color:var(--dim); margin:0 18px 16px; }
  nav .grp { font-size:10.5px; text-transform:uppercase; letter-spacing:.07em;
    color:var(--dim); margin:16px 18px 5px; font-weight:700; }
  nav a { display:block; padding:5px 18px; font-size:13px; cursor:pointer;
    color:var(--fg); text-decoration:none; border-left:3px solid transparent; }
  nav a:hover { background:var(--card); }
  nav a.on { border-left-color:var(--accent); background:var(--card); font-weight:600; }
  nav a .st { float:right; font-size:9.5px; padding:1px 5px; border-radius:3px;
    margin-top:2px; font-weight:600; letter-spacing:.02em; }
  .st.live { background:#d1fae5; color:#065f46; }
  .st.pruned { background:#fef3c7; color:#92400e; }
  .st.constant { background:#e5e7eb; color:#4b5563; }
  .st.prototype { background:#dbeafe; color:#1e40af; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) .st.live
    { background:#064e3b; color:#6ee7b7; } :root:not([data-theme="light"]) .st.pruned
    { background:#78350f; color:#fcd34d; } :root:not([data-theme="light"]) .st.constant
    { background:#374151; color:#d1d5db; } }
  main { flex:1; padding:28px 34px 90px; max-width:1020px; min-width:0; }
  h2 { font-size:21px; margin:0 0 2px; letter-spacing:-.01em; }
  .meta { font-size:12.5px; color:var(--dim); margin-bottom:20px; }
  .meta b { color:var(--fg); font-weight:600; }
  .fld { border-top:1px solid var(--line); padding:13px 0 3px; }
  .fld .k { font-size:10.5px; text-transform:uppercase; letter-spacing:.07em;
    color:var(--dim); font-weight:700; margin-bottom:5px; }
  .fld .v { font-size:14px; }
  pre { background:var(--code); border:1px solid var(--line); border-radius:7px;
    padding:11px 13px; overflow-x:auto; font:12px/1.55 ui-monospace,"SF Mono",
    Menlo,Consolas,monospace; margin:6px 0 0; white-space:pre; }
  code { background:var(--code); padding:1px 5px; border-radius:4px;
    font:12.5px ui-monospace,"SF Mono",Menlo,Consolas,monospace; }
  .two { display:grid; grid-template-columns:1fr 1fr; gap:0 26px; }
  @media (max-width:780px) { .shell { display:block; } nav { width:auto;
    max-height:none; position:static; border-right:0; border-bottom:1px solid var(--line); }
    main { padding:20px 16px 60px; } .two { grid-template-columns:1fr; } }
  .gal { display:grid; grid-template-columns:repeat(auto-fill,minmax(228px,1fr));
    gap:16px; margin-top:10px; }
  .sh { border:1px solid var(--line); border-radius:9px; overflow:hidden;
    background:var(--card); }
  .sh svg { display:block; width:100%; height:190px; background:var(--bg);
    border-bottom:1px solid var(--line); padding:7px 0; }
  .sh .nm { font-size:12.5px; font-weight:650; padding:9px 11px 2px; }
  .sh .nt { font-size:11px; color:var(--dim); padding:0 11px 8px; line-height:1.45; }
  .sh .bands { padding:0 11px 9px; }
  .bar { display:flex; align-items:center; gap:7px; font-size:10.5px;
    color:var(--dim); margin:2px 0; font-variant-numeric:tabular-nums; }
  .bar i { font-style:normal; width:16px; color:var(--dim); }
  .bar u { text-decoration:none; flex:1; height:7px; border-radius:4px;
    background:var(--line); overflow:hidden; display:block; }
  .bar u b { display:block; height:100%; background:var(--accent); }
  .bar s { text-decoration:none; width:30px; text-align:right; }
  .bar.z s, .bar.z i { opacity:.42; }
  .sh .lg { font-size:10.5px; color:var(--dim); padding:0 11px 10px;
    line-height:1.5; border-top:1px dashed var(--line); margin-top:3px;
    padding-top:7px; }
  .sh .lg span.d { display:inline-block; width:8px; height:8px; border-radius:2px;
    margin-right:5px; vertical-align:middle; }
  .warn { border-left:3px solid var(--amber); background:var(--card);
    border-radius:0 8px 8px 0; padding:11px 14px; margin:10px 0 4px; font-size:13.5px; }
  .warn b { color:var(--amber); }
  .rgrp { border:1px solid var(--line); border-radius:9px; margin:14px 0;
    overflow:hidden; }
  .rgrp .hd { padding:10px 13px; background:var(--card);
    border-bottom:1px solid var(--line); }
  .rgrp .hd .t { font-size:13.5px; font-weight:650; }
  .rgrp .hd .s { font-size:11.5px; color:var(--dim); margin-top:2px; }
  .rgrp .pics { display:flex; gap:10px; padding:12px 13px; overflow-x:auto;
    scroll-snap-type:x proximity; }
  .rgrp .pics > * { scroll-snap-align:start; }
  .rgrp .desc { font-size:12.5px; color:var(--fg); padding:0 13px 10px;
    line-height:1.55; }
  .rgrp .nums { font-size:11px; color:var(--dim); padding:0 13px 9px;
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace; }
  .sch { flex:0 0 150px; }
  .sch svg { width:100%; height:224px; display:block; border:1px solid var(--accent);
    border-radius:6px; background:var(--bg); }
  .sch .cap { font-size:10px; color:var(--accent); margin-top:4px; font-weight:650;
    text-align:center; }
  .rgrp .pic { flex:0 0 150px; }
  .rgrp .pic img { width:100%; border-radius:6px; border:1px solid var(--line);
    display:block; }
  .rgrp .pic .cap { font-size:10px; color:var(--dim); margin-top:4px;
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace; line-height:1.5; }
  .flag { color:var(--amber); font-weight:650; }
  .none { font-size:11px; color:var(--amber); padding:0 11px 9px; font-weight:600; }
  table { border-collapse:collapse; width:100%; font-size:13px; margin:8px 0 0; }
  td, th { text-align:left; padding:6px 9px; border-bottom:1px solid var(--line); }
  th { font-size:10.5px; text-transform:uppercase; letter-spacing:.05em;
    color:var(--dim); }
  .step { display:flex; gap:11px; margin:7px 0; font-size:13.5px; }
  .step b { flex:0 0 92px; color:var(--teal); font-size:12px; padding-top:1px; }
</style>
</head>
<body>
<div class="shell">
  <nav>
    <h1>Scene feature reference</h1>
    <div class="sub">23 cards covering the 29 columns. Bands are documented as one card each.</div>
    <a id="nav-overview" class="on">How it fits together</a>
    <div id="menu"></div>
  </nav>
  <main id="pane"></main>
</div>
<script>
var DATA = __DATA__;
(function () {
  function t(s) { return s; }
  function esc(s) { return String(s == null ? "" : s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
  // The copy carries <code> deliberately, so it is inserted as markup; the
  // raw dump excerpts never are, and go through esc() into a <pre>.
  function fld(k, v) {
    return "<div class='fld'><div class='k'>" + t(k) + "</div><div class='v'>" + v + "</div></div>";
  }
  function pre(s) { return "<pre>" + esc(s) + "</pre>"; }

  var groups = [];
  DATA.features.forEach(function (f) {
    if (groups.indexOf(f.group) < 0) { groups.push(f.group); }
  });
  var menu = document.getElementById("menu");
  function buildMenu() {
    var h = "";
    groups.forEach(function (g) {
      h += "<div class='grp'>" + t(g) + "</div>";
      DATA.features.forEach(function (f, i) {
        if (f.group !== g) { return; }
        h += "<a data-i='" + i + "'>" + esc(f.name)
           + "<span class='st " + f.status + "'>" + t(f.status) + "</span></a>";
      });
    });
    menu.innerHTML = h;
    [].forEach.call(menu.querySelectorAll("a"), function (a) {
      a.addEventListener("click", function () { show(+a.dataset.i); });
    });
  }
  function mark(el) {
    [].forEach.call(document.querySelectorAll("nav a"), function (a) {
      a.classList.remove("on"); });
    if (el) { el.classList.add("on"); }
  }
  // Every number under every diagram is produced by the same soft_bins and
  // ramps the extractor runs; the layouts are rect lists fed through it.
  function gallery() {
    var S = DATA.shapes, h = "<div class='fld'><div class='k'>"
      + t("Sixteen layouts and what they report") + "</div><div class='v'>"
      + "<p style='font-size:13px;color:var(--dim);margin:0 0 4px'>"
      + t("Each layout is a list of leaf rectangles passed through the real "
        + "grouping, ramps and soft bins. Colour marks the repeating group; "
        + "hatched items were excluded and the card says why. Dashed edges and "
        + "the grey surround mark what falls outside the display — an item "
        + "is measured by its OWN rectangle, so running off the screen does not "
        + "shrink it.") + "</p><div class='gal'>";
    S.shapes.forEach(function (sp) {
      h += "<div class='sh'>" + sp.svg + "<div class='nm'>" + esc(sp.name) + "</div>"
         + "<div class='nt'>" + esc(sp.note) + "</div>";
      if (!sp.res) {
        h += "<div class='none'>" + t("no repeating group — all five written empty")
           + "</div>";
      } else {
        h += "<div class='bands'>";
        sp.res.h.forEach(function (v, i) {
          h += "<div class='bar" + (v < 0.005 ? " z" : "") + "'><i>h" + i + "</i>"
             + "<u><b style='width:" + (v * 100).toFixed(1) + "%'></b></u>"
             + "<s>" + v.toFixed(2) + "</s></div>";
        });
        h += "</div>";
      }
      h += "<div class='lg'>";
      sp.legend.forEach(function (l) {
        h += "<div><span class='d' style='background:" + l.c + "'></span>"
           + esc(l.k) + " ×" + l.n + " — " + esc(l.why) + "</div>";
      });
      h += "</div></div>";
    });
    h += "</div></div></div>";
    return h;
  }
  // What the capture actually contains, against what the layouts predicted.
  function observed() {
    var R = DATA.reps, h = "<div class='fld'><div class='k'>"
      + t("What the capture actually holds") + "</div><div class='v'>";
    h += "<div class='warn'>" + t("The layouts above are worked cases, not observed "
      + "motifs. Matching all 2,648 settled samples against them, six of the fourteen "
      + "never occur, and the median distance to the NEAREST one is 0.275 on a vector "
      + "whose parts sum to 1. The pictures below are what each group really contains.")
      + "</div>";
    h += "<div class='warn'><b>" + t("The defect this exposes.") + "</b> "
      + t("The bands are shares, so they sum to 1 whether the repeating content covers "
        + "87% of the window or 0.4% of it. ") + R.note_lowrep + " " + t("of the")
      + " " + R.note_total + " " + t("samples that emit a profile have repeat_frac below "
        + "0.05 — a repeating structure covering under a twentieth of the window — "
        + "and a quarter of those still put 80% of their mass in a single band. That is a "
        + "confident statement about a shape that is barely there, and it is why the "
        + "largest group below mixes video players with settings pages: neither has "
        + "repeating structure, so both are described by their leftovers.") + "</div>";
    R.groups.forEach(function (g) {
      var bad = g.lowrep >= 0.5;
      h += "<div class='rgrp'><div class='hd'><div class='t'>"
        + t("nearest to") + " “" + esc(g.name) + "” · " + g.n + " " + t("samples")
        + "</div><div class='s'>" + t("motif profile") + " "
        + g.profile.map(function (v) { return v.toFixed(2); }).join("  ")
        + " · " + t("median repeat_frac") + " "
        + "<span class='" + (bad ? "flag" : "") + "'>" + g.medrep.toFixed(3) + "</span>"
        + (bad ? " · <span class='flag'>" + Math.round(g.lowrep * 100) + "% "
                 + t("of these have almost no repeating structure at all") + "</span>" : "")
        + "</div></div><div class='pics'>";
      g.reps.forEach(function (r) {
        h += "<div class='pic'><img loading='lazy' src='" + r.img + "'>"
           + "<div class='cap'>" + r.h.map(function (v) { return v.toFixed(2); }).join(" ")
           + "<br>repeat " + r.rep + " · cols " + r.cols + "</div></div>";
      });
      h += "</div></div>";
    });
    h += "</div></div>";
    return h;
  }
  // Every motif the detector produced, with representatives spread across its
  // coverage range so a group can be judged by eye rather than by its name.
  function motifs() {
    var P = DATA.pats, h = "<div class='fld'><div class='k'>"
      + t("Every motif found") + "</div><div class='v'>"
      + "<p style='font-size:13px;color:var(--dim);margin:0 0 6px'>"
      + t("250 scenes carry both dumps and 179 of them have a picture. Every pictured "
        + "scene is shown under its motif, ordered by coverage, so the weak end of a "
        + "group is visible rather than hidden behind its best member. The first tile "
        + "in each strip is the motif drawn from its own median geometry \u2014 columns, "
        + "item height and aspect \u2014 not from an idea of what it should look like.")
      + "</p>";
    P.groups.forEach(function (g) {
      var weak = g.cover < 0.15;
      h += "<div class='rgrp'><div class='hd'><div class='t'>" + esc(g.motif)
         + " \u00b7 " + g.n + " " + t("scenes") + "</div><div class='s'>"
         + t("median cover") + " <span class='" + (weak ? "flag" : "") + "'>"
         + g.cover.toFixed(2) + "</span>"
         + (weak ? " \u00b7 <span class='flag'>" + t("too little to name the screen by")
                   + "</span>" : "")
         + "</div></div>";
      if (g.desc) { h += "<div class='desc'>" + t(g.desc) + "</div>"; }
      h += "<div class='nums'>cell {" + esc(g.cell || "\u2014") + "}"
         + " \u00b7 " + t("median") + " " + g.members + " " + t("members")
         + " \u00b7 " + g.cols + " " + t("columns")
         + " \u00b7 " + t("item height") + " " + g.h + " " + t("of the display")
         + " \u00b7 " + g.pictured + " " + t("pictured") + "</div>";
      h += "<div class='pics'><div class='sch'>" + g.svg
         + "<div class='cap'>" + t("the motif") + "</div></div>";
      g.reps.forEach(function (r) {
        h += "<div class='pic'><img loading='lazy' src='" + r.img + "'>"
           + "<div class='cap'>" + (r.pat ? "{" + esc(r.pat) + "}" : "\u2014")
           + "<br>cover " + (r.cover || "\u2014") + " \u00b7 h " + (r.h || "\u2014")
           + "<br>overlay " + r.ovl + "</div></div>";
      });
      h += "</div></div>";
    });
    h += "</div></div>";
    return h;
  }
  // Every scene grouped by the kind of surface found, with all its pictures.
  function surfaces() {
    var S = DATA.surf, h = "<div class='fld'><div class='k'>"
      + t("What each surface kind looks like") + "</div><div class='v'>"
      + "<p style='font-size:13px;color:var(--dim);margin:0 0 6px'>"
      + t("All 250 scenes grouped by the kind of surface found, ordered within a "
        + "group by how much of the window it covers. Every pictured scene is "
        + "shown.") + "</p>";
    S.groups.forEach(function (g) {
      h += "<div class='rgrp'><div class='hd'><div class='t'>"
         + t("kind") + " " + g.kind + " \u2014 " + esc(g.name) + " \u00b7 "
         + g.n + " " + t("scenes") + "</div><div class='s'>"
         + (g.cover === null ? t("nothing to measure")
            : t("median cover") + " " + g.cover.toFixed(2)
              + " \u00b7 " + t("median drawn over") + " " + (g.over || 0)
              + " \u00b7 " + g.full + " " + t("fill the window"))
         + "</div></div>";
      h += "<div class='desc'>" + t(g.desc) + "</div>";
      h += "<div class='pics'>";
      g.reps.forEach(function (r) {
        h += "<div class='pic'><img loading='lazy' src='" + r.img + "'>"
           + "<div class='cap'>cover " + r.cover + (r.full === "1" ? " full" : "")
           + "<br>over " + r.over
           + "<br>" + esc(String(r.slot1).slice(0, 22)) + "</div></div>";
      });
      h += "</div></div>";
    });
    h += "</div></div>";
    return h;
  }
  function overview() {
    mark(document.getElementById("nav-overview"));
    var c = DATA.common, h = "<h2>" + t("How it fits together") + "</h2>"
      + "<div class='meta'>" + t("Every feature below is produced by the same six steps. "
      + "Nothing is measured twice and nothing is read from the accessibility tree.") + "</div>";
    h += "<div class='fld'><div class='k'>" + t("Pipeline") + "</div><div class='v'>";
    c.pipeline.forEach(function (p) {
      h += "<div class='step'><b>" + t(p[0]) + "</b><span>" + t(p[1]) + "</span></div>"; });
    h += "</div></div>";
    h += "<div class='fld'><div class='k'>" + t("Measured cost, one sample")
       + "</div><div class='v'><table><tr><th>" + t("Step") + "</th><th>" + t("Time")
       + "</th><th>" + t("Complexity") + "</th><th>" + t("Note") + "</th></tr>";
    c.cost.forEach(function (r) {
      h += "<tr><td>" + t(r[0]) + "</td><td><code>" + r[1] + "</code></td><td><code>"
        + (r[2] || "—") + "</code></td><td style='color:var(--dim)'>" + t(r[3]) + "</td></tr>"; });
    h += "</table></div></div>";
    h += fld("What the costs mean", "<p>" + t("Parsing text is 95% of the work. Every feature "
      + "is effectively free once the tree is in memory — all 29 together cost 2.66 ms "
      + "against 12.6 ms to read the dump. Inside ArkUI the parse does not exist at all: the "
      + "tree, the rects and the component types are already there, so the whole measurement "
      + "is one pass over the nodes plus a handful of interval unions.") + "</p>"
      + "<p>" + t("The only superlinear step is the area union, a sweep-line at O(R log R). "
      + "A sample makes 46 such calls, the largest over 160 rectangles.") + "</p>");
    h += fld("Status of each column", "<p>" + t("<b>live</b> — carries distance in the "
      + "clustering. <b>pruned</b> — removed by --decorrelate 0.55 for repeating another "
      + "feature; still written to the CSV and still readable. <b>constant</b> — no "
      + "variance in this session, so automatically excluded; expected to vary in a "
      + "system-wide capture.") + "</p>");
    document.getElementById("pane").innerHTML = h;
  }
  function show(i) {
    var f = DATA.features[i];
    mark(menu.querySelector("a[data-i='" + i + "']"));
    var h = "<h2>" + esc(f.name) + "</h2><div class='meta'>"
      + t(f.group) + " · <b>" + t(f.status) + "</b> · " + t("weight") + " " + t(f.weight)
      + " · " + t("range") + " " + t(f.rng) + "</div>";
    h += fld("Where it comes from", t(f.origin));
    h += fld("What the raw data looks like", pre(f.raw));
    h += fld("How it is extracted", "<p>" + t(f.extract) + "</p>");
    h += "<div class='two'>" + fld("Why we have it", "<p>" + t(f.why) + "</p>")
       + fld("What it tells us about the scene", "<p>" + t(f.tells) + "</p>") + "</div>";
    h += "<div class='two'>" + fld("Cost — from a dump, in script",
        "<p>" + t(f.cost_script) + "</p>")
       + fld("Cost — inside ArkUI or RenderService", "<p>" + t(f.cost_tree) + "</p>") + "</div>";
    h += fld("Normalisation", "<p>" + t(f.norm) + "</p>");
    h += fld("Worked example, raw to result", pre(f.example));
    if (f.name.indexOf("rs_item_h0") === 0) { h += gallery() + observed(); }
    if (f.name.indexOf("rs_pat_") === 0) { h += motifs(); }
    if (f.name.indexOf("rs_surf_") === 0) { h += surfaces(); }
    h += fld("Worth knowing", "<p>" + t(f.extra) + "</p>");
    document.getElementById("pane").innerHTML = h;
  }
  document.getElementById("nav-overview").addEventListener("click", overview);
  buildMenu();
  overview();
})();
</script>
</body>
</html>
"""

out = HTML.replace("__DATA__", json.dumps(payload, ensure_ascii=False))
open("feature_reference.html", "w", encoding="utf-8").write(out)
print("wrote feature_reference.html")
