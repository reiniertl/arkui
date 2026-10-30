"""The viewer: scene_feature_reference.html (IF-VIEWER, ALG-VIEWER-RENDER).

    ./vectors_build.py patterns.csv --captures captures --out two_payload.json
    ./vectors_page.py  two_payload.json --out scene_feature_reference.html

One self-contained file, two tabs, in this order:

  MOTIF       one group per dictionary motif: its schematic drawn from
              its definition, the definition, its count, every scene it was
              found in; then the scenes with no motif. Every tile shows its scene's
              tag coverage.
  CLUSTERING  one clustering over the motif vector, matched to no motif: the
              general stats, a rotatable 3D map of three PCA components,
              then every cluster with its count and scenes, unclustered last.

Inside a group the order is fixed - picture, description, screenshots
(INV-GROUP-ORDER) - and a motif or cell nobody reached is drawn empty in
its place. Nothing on the page names a scene, an app or a use: a motif
names a structure, a cell is named by its coordinates and a cluster by its id.

vectors.html is retired. This file replaces it, and the 2026-10-26 build of
the same name whose generator was deleted in 5fce8c6.
"""
import argparse, json, os

HTML = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scene Feature Reference</title>
<style>
  :root { --bg:#fff; --fg:#16181d; --dim:#6b7280; --line:#e3e6eb; --card:#f7f8fa;
          --accent:#2563eb; --ink:#2563eb; --warn:#b45309; --code:#eef1f5; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
    --bg:#0f1115; --fg:#e6e8ec; --dim:#9aa1ad; --line:#272b33; --card:#171a20;
    --accent:#60a5fa; --ink:#60a5fa; --warn:#fbbf24; --code:#11141a; } }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
    font:15px/1.6 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  .shell { display:flex; align-items:flex-start; min-height:100vh; }
  nav { width:220px; flex:0 0 220px; border-right:1px solid var(--line);
    padding:20px 0 50px; position:sticky; top:0; }
  nav h1 { font-size:15px; margin:0 18px 14px; }
  nav a { display:block; padding:9px 18px; font-size:13.5px; cursor:pointer;
    border-left:3px solid transparent; color:var(--fg); }
  nav a:hover { background:var(--card); }
  nav a.on { border-left-color:var(--accent); background:var(--card); font-weight:650; }
  nav a small { display:block; color:var(--dim); font-size:11px; font-weight:400; }
  main { flex:1; min-width:0; padding:26px 32px 80px; max-width:1240px; }
  .tab { display:none; } .tab.on { display:block; }
  h2 { font-size:22px; margin:0 0 4px; }
  .meta { font-size:13px; color:var(--dim); margin-bottom:14px; }
  .meta b { color:var(--fg); }
  .k { font-size:10.5px; text-transform:uppercase; letter-spacing:.07em;
    color:var(--dim); font-weight:700; margin:18px 0 6px; }
  table { border-collapse:collapse; width:100%; font-size:13px; margin:4px 0; }
  td,th { text-align:left; padding:5px 9px; border-bottom:1px solid var(--line); }
  th { font-size:10.5px; text-transform:uppercase; color:var(--dim); letter-spacing:.05em; }
  code { background:var(--code); padding:1px 5px; border-radius:4px;
    font:12.5px ui-monospace,"SF Mono",Menlo,Consolas,monospace; }
  .grp { border:1px solid var(--line); border-radius:10px; margin:16px 0; overflow:hidden; }
  .grp.empty { border-style:dashed; opacity:.7; }
  .grp .hd { padding:9px 13px; background:var(--card); border-bottom:1px solid var(--line);
    font-size:14px; font-weight:650; }
  .grp .hd span { font-weight:400; color:var(--dim); font-size:12px; }
  .grp .body { display:flex; gap:14px; padding:12px 13px 4px; align-items:flex-start; }
  .fig { flex:0 0 132px; }
  .fig svg { width:100%; height:auto; display:block; border:1px solid var(--accent);
    border-radius:6px; background:var(--bg); }
  .fig .none { aspect-ratio:1316/2832; border:1px dashed var(--line); border-radius:6px;
    display:flex; align-items:center; justify-content:center; text-align:center;
    font-size:10px; color:var(--dim); padding:6px; }
  .txt { flex:1; min-width:0; }
  .desc { font-size:13.5px; line-height:1.55; }
  .count { font-size:12px; color:var(--dim); margin-top:6px;
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace; }
  .count .bar { display:inline-block; vertical-align:middle; width:120px; height:5px;
    background:var(--line); border-radius:3px; margin-left:6px; }
  .count .bar i { display:block; height:100%; background:var(--accent); border-radius:3px; }
  .pics { display:flex; gap:9px; padding:10px 13px 13px; overflow-x:auto; }
  .pic { flex:0 0 112px; position:relative; }
  .pic img { width:100%; border-radius:5px; border:1px solid var(--line); display:block; }
  .pic.low img { opacity:.45; }
  .pic .lowcov { position:absolute; left:4px; top:4px; padding:1px 4px; border-radius:3px;
    background:var(--card); border:1px solid var(--line); font-size:8.5px; color:var(--dim); }
  .pic .nopic { width:100%; aspect-ratio:9/19.5; border-radius:5px;
    border:1px dashed var(--line); background:var(--card); display:flex;
    align-items:center; justify-content:center; font-size:10px; color:var(--dim); }
  .pic .cap { font-size:9px; color:var(--dim); margin-top:3px; line-height:1.4;
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace; word-break:break-all; }
  .legend { display:flex; flex-wrap:wrap; gap:12px; font-size:12px; color:var(--dim); }
  .legend i { width:10px; height:10px; border-radius:2px; display:inline-block;
    margin-right:4px; vertical-align:-1px; }
  .stats { display:flex; flex-wrap:wrap; gap:10px; margin:8px 0 14px; }
  .stat { border:1px solid var(--line); border-radius:9px; padding:8px 12px;
    background:var(--card); min-width:110px; }
  .stat b { display:block; font-size:20px; }
  .stat span { font-size:11px; color:var(--dim); }
  .map { border:1px solid var(--line); border-radius:12px; background:var(--card);
    padding:12px 14px; margin:0 0 16px; }
  .map canvas { width:100%; height:440px; display:block; cursor:grab; touch-action:none; }
  .map canvas:active { cursor:grabbing; }
  .note { font-size:12px; color:var(--dim); }
  .flag { color:var(--warn); font-weight:650; }
  button { font:inherit; font-size:12px; padding:2px 8px; border:1px solid var(--line);
    border-radius:5px; background:var(--bg); color:var(--fg); cursor:pointer; }
  @media (max-width:760px){ .shell{display:block} nav{width:auto;position:static;
    border-right:0;border-bottom:1px solid var(--line)} main{padding:18px 14px 50px}
    .grp .body{flex-direction:column} }
</style></head><body>
<div class="shell">
  <nav><h1>Scene feature reference</h1><div id="menu"></div></nav>
  <main>
    <section class="tab" id="t-pattern"></section>
    <section class="tab" id="t-clustering"></section>
  </main>
</div>
<script>
var DATA = __DATA__;
(function () {
  function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;")
    .replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/'/g,"&#39;");}
  var S = DATA._scenes || {}, audit = DATA._audit || {}, UP = DATA._unplaced || {};
  var TOTAL = audit.rows || Object.keys(S).length;
  function pct(n) { return TOTAL ? Math.round(100 * n / TOTAL) : 0; }

  // A tile: the scene's own picture - a scene is a photographed sample -
  // and its tag coverage, how much of what it shows could be named
  // (INV-CLIENT-TAG-COVERAGE). A picture that is not there is a broken
  // join, and the tile says so.
  function tile(sid, extra) {
    var s = S[sid] || {};
    var pic = s.img ? "<img loading='lazy' src='" + s.img + "'>"
      : "<div class='nopic'>picture not found</div>";
    var cov = s.tag_coverage == null ? "—" : Math.round(100 * s.tag_coverage) + "%";
    // under one half: less than half of what it shows could be named
    // (INV-CLIENT-TAG-COVERAGE), so the motifs it carries say little
    var low = s.tag_coverage != null && s.tag_coverage < 0.5;
    return "<div class='pic" + (low ? " low" : "") + "' data-scene='" + esc(sid) + "'>" + pic
      + (low ? "<div class='lowcov'>low tag coverage</div>" : "")
      + "<div class='cap'>" + esc(sid) + "<br><span class='tc'>tagged " + cov + "</span>"
      + (extra ? "<br>" + extra : "") + "</div></div>";
  }
  function count(n) {
    return "<div class='count'>" + n + " of " + TOTAL + " scenes · " + pct(n)
      + "%<span class='bar'><i style='width:" + pct(n) + "%'></i></span></div>";
  }
  // One group, in the order INV-GROUP-ORDER fixes: picture, description,
  // count, then every scene.
  function group(key, attr, title, fig, desc, n, tiles) {
    return "<div class='grp" + (n ? "" : " empty") + "' " + attr + "='" + esc(key) + "'>"
      + "<div class='hd'>" + title + "</div><div class='body'>"
      + "<div class='fig'>" + fig + "</div><div class='txt'>"
      + "<div class='desc'>" + esc(desc) + "</div>" + count(n) + "</div></div>"
      + "<div class='pics'>" + tiles + "</div></div>";
  }
  function noneFig(n) {
    return "<div class='none'>" + (n ? "no drawing:<br>no motif"
      : "no scene<br>reached this cell") + "</div>";
  }
  function unplacedBlock(space) {
    var up = UP[space];
    if (!up) { return ""; }
    var ids = up.unplaced || [], cov = up.tag_coverage || {};
    return "<div class='k'>What the vector could not place</div>"
      + "<div class='note'>no motif found: <b>" + ids.length + "</b> of " + TOTAL
      + " (" + pct(ids.length) + "%), each with its tag coverage"
      + (ids.length ? "<br><span style='opacity:.8'>" + ids.map(function (i) {
          return esc(i) + " " + (cov[i] == null ? "—" : Math.round(100 * cov[i]) + "%");
        }).join(", ") + "</span>" : "") + "</div>"
      + "<div class='note' style='margin-top:4px;opacity:.75'>" + esc(up.note) + "</div>";
  }
  function head(title, cells, blurb) {
    var mt = cells.filter(function (c) { return !c.n; }).length;
    var lost = (audit.lost || []).length;
    return "<h2>" + title + "</h2><div class='meta'><b>" + TOTAL + "</b> scenes · <b>"
      + cells.length + "</b> declared cells, " + (cells.length - mt) + " reached"
      + (mt ? ", <span class='flag'>" + mt + " empty</span>" : "")
      + (lost ? " · <span class='flag'>" + lost + " of " + (audit.pictures || "?")
               + " pictures became no scene</span>" : "")
      + "</div><p class='note'>" + blurb + "</p>";
  }

  // ---- MOTIF -------------------------------------------------------------
  (function () {
    var groups = DATA._patterns || [];
    var dict = groups.filter(function (g) { return g.svg; });
    var hit = dict.filter(function (g) { return g.n; }).length;
    var lost = (audit.lost || []).length;
    var h = "<h2>Motif</h2><div class='meta'><b>" + TOTAL + "</b> scenes · <b>"
      + dict.length + "</b> dictionary motifs, " + hit + " found"
      + (lost ? " · <span class='flag'>" + lost + " of " + (audit.pictures || "?")
               + " pictures became no scene</span>" : "")
      + "</div><p class='note'>One group per motif of the dictionary, each from an ArkUI component contract. A "
      + "scene appears under every motif found on its display. The first picture "
      + "of a group is the motif drawn from its definition alone.</p>";
    groups.forEach(function (g) {
      var tiles = (g.members || []).map(function (sid) {
        var s = S[sid] || {};
        return tile(sid, esc((s.found || []).join(" · ") || "no motif"));
      }).join("");
      h += group(g.cell, "data-cell", esc(g.cell) + " <span>" + g.n + " scenes</span>",
                 g.svg ? g.svg : noneFig(g.n), g.desc, g.n, tiles);
    });
    h += unplacedBlock("pattern");
    document.getElementById("t-pattern").innerHTML = h;
  })();

  // ---- CLUSTERING --------------------------------------------------------
  var C = DATA.clustering || {groups: []};
  function palette(id) {
    if (id === -1) { return "#9aa1ad"; }
    var hues = [210, 28, 142, 320, 48, 260, 0, 175, 95, 300];
    return "hsl(" + hues[((id % hues.length) + hues.length) % hues.length] + " 70% 52%)";
  }
  (function () {
    var real = C.groups.filter(function (g) { return g.id !== -1; });
    var h = "<h2>" + esc(C.title || "Clustering") + "</h2><p class='note'>"
      + esc(C.blurb || "") + "</p><div class='stats'>"
      + "<div class='stat'><b>" + (C.n || 0) + "</b><span>scenes</span></div>"
      + "<div class='stat'><b>" + (C.k || 0) + "</b><span>clusters</span></div>"
      + "<div class='stat'><b>" + (C.outliers || 0) + "</b><span>unclustered · "
      + (C.n ? Math.round(100 * C.outliers / C.n) : 0) + "%</span></div>"
      + "<div class='stat'><b>" + (C.silhouette == null ? "—" : C.silhouette)
      + "</b><span>silhouette, clustered only</span></div>"
      + "<div class='stat'><b>" + (C.explained || []).map(function (x) {
          return Math.round(100 * x) + "%"; }).join(" / ")
      + "</b><span>PCA variance, PC1 / PC2 / PC3</span></div>"
      + "<div class='stat'><b>" + (C.mcs || "") + "</b><span>min_cluster_size</span></div>"
      + "<div class='stat'><b>" + (C.cols || []).length + "</b><span>columns, "
      + (C.constant || []).length + " constant on this capture and kept</span></div>"
      + "</div>";
    h += "<div class='map'><canvas id='scatter'></canvas><div class='note' style='padding:6px 2px'>"
      + "<b>Drag to rotate · wheel to zoom · shift-drag to pan · click a point "
      + "to jump to its cluster</b> <button id='reset'>reset view</button><br>Every scene, "
      + "projected to three components by PCA over the same matrix the clustering used, "
      + "so distance here is distance there.</div><div class='legend'>"
      + C.groups.map(function (g) {
          return "<span><i style='background:" + palette(g.id) + ";border-radius:50%'></i>"
            + (g.id === -1 ? "unclustered" : "cluster " + g.id) + " · " + g.n + "</span>";
        }).join("") + "</div></div>";
    h += "<div class='k'>The columns, used as measured</div><table><tr><th>column</th>"
      + "<th>what it measures</th></tr>"
      + (C.cols || []).map(function (c) {
          return "<tr><td><code>" + esc(c.name) + "</code></td>"
            + "<td class='note'>" + esc(c.what) + "</td></tr>"; }).join("") + "</table>";
    h += "<div class='k'>Every scene in every cluster</div><p class='note'>Ordered from "
      + "each cluster's centre outwards, so the last tiles are where it frays. "
      + real.length + " clusters, then the scenes the clustering could not place.</p>";
    C.groups.forEach(function (g) {
      var none = g.id === -1;
      h += "<div class='grp' id='c" + g.id + "' data-cluster='" + g.id + "'>"
        + "<div class='hd'><i style='display:inline-block;width:10px;height:10px;"
        + "border-radius:50%;background:" + palette(g.id) + "'></i> "
        + (none ? "unclustered" : "cluster " + g.id) + " <span>" + g.n + " scenes"
        + (none ? " · reported rather than forced into the nearest cluster"
                : " · cohesion " + g.cohesion) + "</span></div>"
        + "<div class='body'><div class='txt'><div class='note'>"
        + ((g.centre || []).length ? "motifs in at least half: " + g.centre.map(esc).join(" · ")
                                   : "no motif in at least half") + "</div>"
        + "<div class='note sm'>scene stats: " + Object.keys(g.stats || {}).map(function (c) {
            return esc(c) + " " + g.stats[c]; }).join(" · ") + "</div>" + count(g.n)
        + "</div></div><div class='pics'>"
        + g.reps.map(function (r) {
            var s = S[r.scene] || {};
            return tile(r.scene, esc((s.found || []).join(" · ") || "no motif")
              + "<br>sil " + (r.sil == null ? "—" : r.sil));
          }).join("") + "</div></div>";
    });
    document.getElementById("t-clustering").innerHTML = h;
  })();

  // ---- the 3D map --------------------------------------------------------
  // Recovered from tools/cluster/viewer.html (4421f9b^): its own projection,
  // no library. The per-cluster naming that page had is NOT recovered - a
  // cluster is named by its id, and a typed name would be a scene class.
  var view = {yaw: 0.6, pitch: -0.35, zoom: 1, panx: 0, pany: 0};
  var MAP = {pts: [], mid: {x: 0, y: 0, z: 0}, span: 1, queued: false};
  C.groups.forEach(function (g) {
    g.reps.forEach(function (r) {
      if (typeof r.x === "number") { MAP.pts.push({x: r.x, y: r.y, z: r.z || 0, id: g.id}); }
    });
  });
  if (MAP.pts.length) {
    ["x", "y", "z"].forEach(function (k) {
      var v = MAP.pts.map(function (p) { return p[k]; });
      var lo = Math.min.apply(null, v), hi = Math.max.apply(null, v);
      MAP.mid[k] = (lo + hi) / 2;
      MAP.span = Math.max(MAP.span, hi - lo);
    });
  }
  function project(w, h) {
    var scale = Math.min(w, h) * 0.66 / MAP.span * view.zoom;
    var cy = Math.cos(view.yaw), sy = Math.sin(view.yaw);
    var cp = Math.cos(view.pitch), sp = Math.sin(view.pitch);
    return function (x, y, z) {
      var X = (x - MAP.mid.x) * scale, Y = (y - MAP.mid.y) * scale, Z = (z - MAP.mid.z) * scale;
      var x1 = X * cy + Z * sy, z1 = -X * sy + Z * cy;
      var y1 = Y * cp - z1 * sp, z2 = Y * sp + z1 * cp;
      return {px: w / 2 + x1 + view.panx, py: h / 2 - y1 + view.pany, pz: z2};
    };
  }
  function render() {
    MAP.queued = false;
    var cv = document.getElementById("scatter");
    if (!cv || !cv.getContext || !MAP.pts.length) { return; }
    var w = cv.clientWidth, h = cv.clientHeight;
    if (!w || !h) { return; }                       // its tab is not showing
    var dpr = window.devicePixelRatio || 1;
    if (cv.width !== Math.round(w * dpr)) { cv.width = Math.round(w * dpr); }
    if (cv.height !== Math.round(h * dpr)) { cv.height = Math.round(h * dpr); }
    var g = cv.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    var pr = project(w, h), AXIS = getComputedStyle(document.body)
      .getPropertyValue("--dim").trim() || "#6b7280", L = MAP.span / 2;
    g.font = "11px ui-monospace, Menlo, Consolas, monospace";
    [["PC1", [L, 0, 0]], ["PC2", [0, L, 0]], ["PC3", [0, 0, L]]].forEach(function (a) {
      var pos = pr(MAP.mid.x + a[1][0], MAP.mid.y + a[1][1], MAP.mid.z + a[1][2]);
      var neg = pr(MAP.mid.x - a[1][0], MAP.mid.y - a[1][1], MAP.mid.z - a[1][2]);
      g.strokeStyle = AXIS; g.globalAlpha = 0.65; g.lineWidth = 1;
      g.beginPath(); g.moveTo(neg.px, neg.py); g.lineTo(pos.px, pos.py); g.stroke();
      g.globalAlpha = 0.9; g.fillStyle = AXIS; g.fillText(a[0], pos.px + 5, pos.py - 3);
    });
    MAP.pts.forEach(function (p) { var q = pr(p.x, p.y, p.z); p.px = q.px; p.py = q.py; p.pz = q.pz; });
    // painter's algorithm, the unclustered grey behind everything
    MAP.pts.sort(function (a, b) {
      if ((a.id === -1) !== (b.id === -1)) { return a.id === -1 ? -1 : 1; }
      return a.pz - b.pz;
    });
    var zs = MAP.pts.map(function (p) { return p.pz; });
    var zlo = Math.min.apply(null, zs), zhi = Math.max.apply(null, zs);
    MAP.pts.forEach(function (p) {
      var t = (zhi === zlo) ? 0.5 : (p.pz - zlo) / (zhi - zlo);
      g.beginPath();
      g.arc(p.px, p.py, (p.id === -1 ? 2.2 : 3.2) + t * 2.4, 0, 6.2832);
      g.fillStyle = palette(p.id);
      g.globalAlpha = (p.id === -1 ? 0.28 : 0.55) + t * 0.42;
      g.fill();
    });
    g.globalAlpha = 1;
  }
  function draw() {
    if (MAP.queued) { return; }
    MAP.queued = true;
    (window.requestAnimationFrame || function (f) { f(); })(render);
  }
  (function wire() {
    var cv = document.getElementById("scatter");
    if (!cv || !cv.addEventListener) { return; }
    var drag = null, moved = 0;
    cv.addEventListener("pointerdown", function (ev) {
      drag = {x: ev.clientX, y: ev.clientY, shift: ev.shiftKey}; moved = 0;
      cv.setPointerCapture(ev.pointerId);
    });
    cv.addEventListener("pointermove", function (ev) {
      if (!drag) { return; }
      var dx = ev.clientX - drag.x, dy = ev.clientY - drag.y;
      moved += Math.abs(dx) + Math.abs(dy);
      if (drag.shift) { view.panx += dx; view.pany += dy; }
      else {
        view.yaw += dx * 0.009;
        view.pitch = Math.max(-1.45, Math.min(1.45, view.pitch + dy * 0.009));
      }
      drag = {x: ev.clientX, y: ev.clientY, shift: drag.shift};
      draw();
    });
    cv.addEventListener("pointerup", function (ev) {
      if (drag && moved < 6) {
        var r = cv.getBoundingClientRect();
        var mx = ev.clientX - r.left, my = ev.clientY - r.top, best = null, bd = 1e9;
        MAP.pts.forEach(function (p) {
          var d = Math.pow(p.px - mx, 2) + Math.pow(p.py - my, 2);
          if (d < bd) { bd = d; best = p; }
        });
        if (best && bd < 900) {
          var el = document.getElementById("c" + best.id);
          if (el) { el.scrollIntoView({behavior: "smooth", block: "start"}); }
        }
      }
      drag = null;
    });
    cv.addEventListener("wheel", function (ev) {
      ev.preventDefault();
      view.zoom = Math.max(0.3, Math.min(14, view.zoom * (ev.deltaY < 0 ? 1.12 : 1 / 1.12)));
      draw();
    }, {passive: false});
    var rb = document.getElementById("reset");
    if (rb) { rb.onclick = function () {
      view = {yaw: 0.6, pitch: -0.35, zoom: 1, panx: 0, pany: 0}; draw(); }; }
    window.addEventListener("resize", draw);
  })();

  // ---- the tabs ----------------------------------------------------------
  var TABS = [
    ["pattern", "Motif", (DATA._patterns || []).filter(function (g) { return g.svg; }).length + " motifs"],
    ["clustering", "Clustering", (C.k || 0) + " clusters over the motif vector"]];
  document.getElementById("menu").innerHTML = TABS.map(function (t) {
    return "<a data-k='" + t[0] + "'>" + t[1] + "<small>" + t[2] + "</small></a>";
  }).join("");
  function show(k) {
    TABS.forEach(function (t) {
      var el = document.getElementById("t-" + t[0]);
      if (el && el.classList) { el.classList.toggle("on", t[0] === k); }
    });
    [].forEach.call(document.querySelectorAll("nav a"), function (a) {
      a.classList.toggle("on", a.dataset.k === k); });
    if (k === "clustering") { draw(); }
  }
  [].forEach.call(document.querySelectorAll("nav a"), function (a) {
    a.addEventListener("click", function () { show(a.dataset.k); }); });
  show("pattern");
})();
</script></body></html>
"""

ap = argparse.ArgumentParser(description=__doc__,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("payload", nargs="?", default="two_payload.json",
                help="vectors_build.py's json")
ap.add_argument("--out", default="scene_feature_reference.html")
a = ap.parse_args()

D = json.load(open(a.payload, encoding="utf-8"))
# </script> inside the payload would end the script early; a picture is
# base64 and cannot hold one, but a caption is text.
data = json.dumps(D, ensure_ascii=False).replace("</", "<\\/")
open(a.out, "w", encoding="utf-8").write(HTML.replace("__DATA__", data))
print("wrote %s  %.1f MB" % (a.out, os.path.getsize(a.out) / 1048576))
