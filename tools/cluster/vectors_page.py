"""One page, two vectors, two clusterings. Nothing else.

    ./vectors_build.py patterns.csv --reps patreps --out two_payload.json
    ./vectors_page.py  two_payload.json --out vectors.html

The reference grew 27 cards documenting every column the extractor has ever
produced, most of them pruned, constant or superseded. Two vectors are under
development; this shows those, each clustered on its own, with every scene.

A cluster is headed by its id, its size, its cohesion and the median of its
own columns. It is not named: naming it would be asserting a scene class, and
there is no scene class here to assert.
"""
import argparse, json, os

HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Scene Vectors</title>
<style>
  :root { --bg:#fff; --fg:#16181d; --dim:#6b7280; --line:#e3e6eb; --card:#f7f8fa;
          --accent:#2563eb; --warn:#b45309; --code:#eef1f5; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
    --bg:#0f1115; --fg:#e6e8ec; --dim:#9aa1ad; --line:#272b33; --card:#171a20;
    --accent:#60a5fa; --warn:#fbbf24; --code:#11141a; } }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg);
    font:15px/1.6 -apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }
  .shell { display:flex; align-items:flex-start; min-height:100vh; }
  nav { width:230px; flex:0 0 230px; border-right:1px solid var(--line);
    padding:20px 0 50px; position:sticky; top:0; }
  nav h1 { font-size:15px; margin:0 18px 14px; }
  nav a { display:block; padding:9px 18px; font-size:13.5px; cursor:pointer;
    border-left:3px solid transparent; color:var(--fg); }
  nav a:hover { background:var(--card); }
  nav a.on { border-left-color:var(--accent); background:var(--card); font-weight:650; }
  nav a small { display:block; color:var(--dim); font-size:11px; font-weight:400; }
  main { flex:1; min-width:0; padding:26px 32px 80px; max-width:1180px; }
  h2 { font-size:22px; margin:0 0 4px; }
  .meta { font-size:13px; color:var(--dim); margin-bottom:18px; }
  .meta b { color:var(--fg); }
  .fld { border-top:1px solid var(--line); padding:13px 0 4px; }
  .k { font-size:10.5px; text-transform:uppercase; letter-spacing:.07em;
    color:var(--dim); font-weight:700; margin-bottom:6px; }
  table { border-collapse:collapse; width:100%; font-size:13px; margin:4px 0; }
  td,th { text-align:left; padding:5px 9px; border-bottom:1px solid var(--line); }
  th { font-size:10.5px; text-transform:uppercase; color:var(--dim); letter-spacing:.05em; }
  code { background:var(--code); padding:1px 5px; border-radius:4px;
    font:12.5px ui-monospace,"SF Mono",Menlo,Consolas,monospace; }
  .cl { border:1px solid var(--line); border-radius:9px; margin:14px 0; overflow:hidden; }
  .cl .hd { padding:10px 13px; background:var(--card); border-bottom:1px solid var(--line); }
  .cl .hd .t { font-size:14px; font-weight:650; }
  .cl .hd .s { font-size:11.5px; color:var(--dim); margin-top:2px; }
  .pics { display:flex; gap:9px; padding:11px 13px; overflow-x:auto; }
  .pic { flex:0 0 118px; }
  .pic img { width:100%; border-radius:5px; border:1px solid var(--line); display:block; }
  .cells { display:flex; flex-wrap:wrap; gap:11px; margin:14px 0; }
  .cel { flex:0 0 150px; border:1px solid var(--line); border-radius:9px;
    background:var(--card); padding:9px; }
  .cel.empty { border-style:dashed; opacity:.62; }
  .cel svg { width:100%; height:150px; display:block; }
  .cel .noimg { height:150px; display:flex; align-items:center;
    justify-content:center; font-size:10px; color:var(--dim); text-align:center; }
  .cel .nm { font-size:11.5px; font-weight:650; margin-top:5px; }
  .cel .ct { font-size:10px; color:var(--dim); line-height:1.5; margin-top:2px; }
  .pic.brw { position:relative; }
  .pic.brw img { border-style:dashed; }
  .pic .borrowed { position:absolute; left:4px; top:4px; padding:1px 4px;
    border-radius:3px; background:var(--card); border:1px solid var(--line);
    font-size:8.5px; letter-spacing:.03em; color:var(--dim); }
  .pic .nopic { width:100%; aspect-ratio:9/19.5; border-radius:5px;
    border:1px dashed var(--line); background:var(--card); display:flex;
    align-items:center; justify-content:center; font-size:10px; color:var(--dim); }
  .pic .cap { font-size:9.5px; color:var(--dim); margin-top:3px; line-height:1.45;
    font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace; }
  .flag { color:var(--warn); font-weight:650; }
  @media (max-width:760px){ .shell{display:block} nav{width:auto;position:static;
    border-right:0;border-bottom:1px solid var(--line)} main{padding:18px 14px 50px} }
</style></head><body>
<div class="shell">
  <nav><h1>Scene vectors</h1><div id="menu"></div></nav>
  <main id="pane"></main>
</div>
<script>
var DATA = __DATA__;
(function () {
  function esc(s){return String(s==null?"":s).replace(/&/g,"&amp;")
    .replace(/</g,"&lt;").replace(/>/g,"&gt;");}
  var keys = Object.keys(DATA).filter(function (k) { return k.charAt(0) !== "_"; });
  var audit = DATA._audit || {};
  document.getElementById("menu").innerHTML = keys.map(function (k) {
    var v = DATA[k];
    return "<a data-k='" + k + "'>" + esc(v.title)
      + "<small>" + v.k + " clusters \\u00b7 " + v.cols.length + " columns</small></a>";
  }).join("");
  function show(k) {
    var v = DATA[k];
    [].forEach.call(document.querySelectorAll("nav a"), function (a) {
      a.classList.toggle("on", a.dataset.k === k); });
    var lost = (audit.missing || []).length;
    var h = "<h2>" + esc(v.title) + "</h2><div class='meta'>"
      + v.n + " scenes \\u00b7 <b>" + v.k + " clusters</b> \\u00b7 "
      + v.outliers + " fitting none (" + Math.round(100*v.outliers/v.n) + "%)"
      + " \\u00b7 silhouette <b>" + v.silhouette + "</b>"
      + " \\u00b7 min_cluster_size " + v.mcs
      + (audit.rows ? " \\u00b7 over <b>" + audit.rows + "</b> measured scenes, "
          + (lost ? "<span class='flag'>" + lost + " with no picture</span>"
                  : "all with a picture") : "")
      + "</div>";
    h += "<p style='margin:0 0 6px'>" + esc(v.blurb) + "</p>";
    // The PSI cells, shown before the clusters because they are the
    // partition: a cell is what a scene IS, a cluster is where it sits
    // relative to the others. An empty cell is drawn dashed and kept in
    // place, because a declared cell nobody reached is a result.
    var cellKey = key === "pattern" ? "_pattern_cells"
                : key === "surface" ? "_surface_cells" : null;
    if (cellKey && D[cellKey]) {
      var cs = D[cellKey], mt = cs.filter(function(c){return !c.n;});
      h += "<div class='fld'><div class='k'>PSI cells \u00b7 "
         + cs.length + " declared, " + (cs.length - mt.length) + " reachable"
         + (mt.length ? ", <span class='flag'>" + mt.length + " empty</span>" : "")
         + "</div><div class='cells'>";
      cs.forEach(function (c) {
        h += "<div class='cel" + (c.n ? "" : " empty") + "'>"
           // The pattern cells carry a schematic. The surface cells do not:
           // a composition is not a layout, and drawing one would need a
           // renderer specified before it could be written.
           + (c.svg ? c.svg : "<div class='noimg'>" + (c.n ? "\\u2014"
                 : "no scene reached<br>this cell") + "</div>")
           + "<div class='nm'>" + esc(c.cell) + "</div>"
           + "<div class='ct'>" + c.n + " scene" + (c.n === 1 ? "" : "s")
           + (c.centre && c.centre.length
                ? "<br>" + c.centre.map(esc).join("<br>") : "")
           + "</div></div>";
      });
      h += "</div></div>";
    }
    h += "<div class='fld'><div class='k'>The columns</div><table><tr><th>column</th>"
       + "<th>transform</th><th>weight</th><th>what it measures</th></tr>"
       + v.cols.map(function (c) {
           return "<tr><td><code>" + esc(c.name) + "</code></td><td><code>" + esc(c.t)
                + "</code></td><td>" + c.w + "</td><td style='color:var(--dim)'>"
                + esc(c.what) + "</td></tr>"; }).join("")
       + "</table><div style='font-size:12px;color:var(--dim);margin-top:5px'>"
       + "PCA variance explained: "
       + v.explained.map(function (x){return (100*x).toFixed(0)+"%";}).join(" / ")
       + "</div></div>";
    h += "<div class='fld'><div class='k'>Every scene in every cluster</div>"
       + "<div style='font-size:12.5px;color:var(--dim);margin-bottom:4px'>"
       + "Ordered from each cluster's centre outwards, so the last tiles are where it "
       + "frays.</div>";
    v.groups.forEach(function (g) {
      var none = g.id < 0;
      h += "<div class='cl'><div class='hd'><div class='t'>"
         + (none ? "fitted no cluster" : "cluster " + g.id)
         + " \\u00b7 " + g.n + " scenes</div><div class='s'>"
         + (none ? "reported rather than forced into the nearest group"
                 : "cohesion " + g.cohesion + " \\u00b7 median "
                   + g.centre.map(esc).join(" \\u00b7 "))
         + "</div></div><div class='pics'>";
      g.reps.forEach(function (r) {
        // r.cell is the PSI_p cell: what the scene IS under a partition
        // written down before the data was looked at. r.sil is where it
        // sits relative to the others. Different questions, both shown.
        // A backstop frame is of this scene but was not taken for it. The
        // capture takes it on arrival, before knowing whether the scene will
        // last, so nothing confirms the screen had not already moved on
        // between the dump and the shot. Saying so on the tile is the
        // difference between evidence and decoration.
        var pic = r.img
          ? "<img loading='lazy' src='" + r.img + "'>"
            + (r.imgsrc === "backstop" ? "<div class='borrowed'>backstop</div>" : "")
          : "<div class='nopic'>no picture</div>";
        h += "<div class='pic" + (r.imgsrc === "backstop" ? " brw" : "") + "'>"
           + pic + "<div class='cap'>"
           + esc(r.scene || "") + "<br>"
           + "sil " + (r.sil === null ? "\\u2014" : r.sil)
           + (r.cell ? " \\u00b7 " + esc(r.cell) : "")
           + (r.scell ? " \\u00b7 " + esc(r.scell) : "")
           + (r.sig ? " \\u00b7 " + esc(r.sig) : "") + "<br>"
           + r.cap.map(esc).join("<br>") + "</div></div>";
      });
      h += "</div></div>";
    });
    h += "</div>";
    document.getElementById("pane").innerHTML = h;
  }
  [].forEach.call(document.querySelectorAll("nav a"), function (a) {
    a.addEventListener("click", function () { show(a.dataset.k); }); });
  show(keys[0]);
})();
</script></body></html>
"""
ap = argparse.ArgumentParser(description=__doc__,
                             formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("payload", nargs="?", default="two_payload.json",
                help="vectors_build.py's json")
ap.add_argument("--out", default="vectors.html")
a = ap.parse_args()

D = json.load(open(a.payload, encoding="utf-8"))
open(a.out, "w", encoding="utf-8").write(
    HTML.replace("__DATA__", json.dumps(D, ensure_ascii=False)))
print("wrote %s  %.1f MB" % (a.out, os.path.getsize(a.out) / 1048576))
