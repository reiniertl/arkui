#!/usr/bin/env python3
"""The viewer renders, and renders what it was given.

    ./viewer_renders.py --page scene_feature_reference.html \\
                        --payload two_payload.json --patterns patterns.csv

Holds five invariants of IF-VIEWER and IF-PAGE-PAYLOAD:

  INV-VIEWER-RENDERS                the page's own script runs to the end
                                    and every tab, every declared cell and
                                    every cluster has a rendered group
  INV-GROUP-ORDER                   inside a group: picture, description,
                                    then the screenshots
  INV-EVERY-SCENE-IN-EVERY-TAB      each measured scene appears exactly
                                    once in each tab
  INV-CLUSTERING-MATCHES-NO-PATTERN the clustering's columns are the two
                                    vectors' and nothing else
  INV-DESCRIPTION-FROM-MEASUREMENT  describe_cell reads only measured
                                    columns and words only from the
                                    measurement's own alphabet

The page is EXECUTED, not read. On 2026-10-28 a page that threw on its first
statement passed every check there was, because every check read the
payload; it opened as a blank main over 20.9 MB of sound data. This runs the
page's script under node against a stub of the few DOM calls it makes and
inspects what each tab wrote. Needs node and nothing beyond the standard
library, so it runs on either machine.

Exit 0 when all five hold, 1 when any does not.
"""
import argparse, ast, collections, csv, json, os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(os.path.dirname(HERE), "cluster", "vectors_build.py")

# The stub answers exactly what the page asks of a browser and records what
# it writes. A missing call shows up as a thrown error, which is the point.
HARNESS = r"""
const fs = require('fs'), vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const a = html.indexOf('<script>'), b = html.lastIndexOf('</script>');
if (a < 0 || b < 0) { console.log(JSON.stringify({threw: 'no <script> in the page'})); process.exit(0); }
const js = html.slice(a + 8, b);
const els = {}, anchors = [];
function el(id) {
  if (!els[id]) els[id] = {id, innerHTML: '', dataset: {}, onclick: null,
    classList: {on: false, toggle(_, v) { this.on = !!v; }},
    addEventListener() {}};
  return els[id];
}
function navAnchors() {
  if (!anchors.length) {
    for (const m of (el('menu').innerHTML || '').matchAll(/data-k='([^']+)'/g))
      anchors.push({dataset: {k: m[1]}, classList: {toggle() {}}, addEventListener() {}});
  }
  return anchors;
}
const ctx = {
  document: {getElementById: el, querySelectorAll: navAnchors, body: {}},
  window: {devicePixelRatio: 1, addEventListener() {}, requestAnimationFrame(f) { f(); }},
  getComputedStyle() { return {getPropertyValue() { return ''; }}; },
  console,
};
let threw = null;
try { vm.createContext(ctx); new vm.Script(js, {filename: 'page.js'}).runInContext(ctx); }
catch (e) { threw = e.constructor.name + ': ' + e.message; }
const out = {threw, menu: el('menu').innerHTML, tabs: {}};
for (const t of ['pattern', 'surface', 'clustering']) out.tabs[t] = el('t-' + t).innerHTML;
fs.writeFileSync(process.argv[3], JSON.stringify(out));
"""

TABS = ("pattern", "surface", "clustering")


def run_page(page):
    with tempfile.TemporaryDirectory() as tmp:
        h, o = os.path.join(tmp, "h.js"), os.path.join(tmp, "out.json")
        open(h, "w", encoding="utf-8").write(HARNESS)
        p = subprocess.run(["node", "--max-old-space-size=4096", h, page, o],
                           capture_output=True, text=True)
        if not os.path.exists(o):
            return {"threw": "node failed: " + (p.stderr or p.stdout).strip()[:300],
                    "tabs": {}}
        return json.load(open(o, encoding="utf-8"))


def groups_of(html):
    """Each rendered group: its key attribute and its own HTML."""
    out = []
    starts = [m.start() for m in re.finditer(r"<div class='grp", html)]
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else len(html)
        seg = html[s:e]
        m = re.match(r"<div class='grp[^']*'[^>]*?data-(cell|cluster)='([^']*)'", seg)
        out.append(((m.group(1), m.group(2)) if m else (None, None), seg))
    return out


def vector_columns(src):
    """The VECTORS table's columns, read from source, in order."""
    body = src[src.index("VECTORS = {"):src.index("\n}\n", src.index("VECTORS = {"))]
    return ["rs_" + n for n in re.findall(r'\(\s*"(\w+)",\s*"\w+",\s*[\d.]+,', body)]


def check_description(src, problems):
    """INV-DESCRIPTION-FROM-MEASUREMENT, statically, over describe_cell."""
    tree = ast.parse(src)
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef)
               and n.name == "describe_cell"), None)
    if fn is None:
        problems.append("describe_cell not found in vectors_build.py")
        return
    allowed = re.compile(r"^(pat1_\w+|pat_n_groups|overlay_frac|vis_\w*)$")
    banned = {"owner", "shot", "session", "stem", "top_name", "img", "imgsrc"}
    for node in ast.walk(fn):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            v = node.value
            if v in banned:
                problems.append("describe_cell reads %r, which is not a measurement" % v)
            elif re.match(r"^[a-z]+\d?_[a-z_0-9]+$", v) and not allowed.match(v):
                problems.append("describe_cell reads column %r, outside the vectors" % v)
        if isinstance(node, ast.Dict) and node.keys:
            problems.append("describe_cell holds its own word table at line %d"
                            % node.lineno)
    # The only word tables it may use are the kind alphabet and the six
    # coordinates, and they must be exactly those.
    names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
    tables = {t.targets[0].id for t in tree.body if isinstance(t, ast.Assign)
              and isinstance(t.targets[0], ast.Name) and t.targets[0].id.isupper()
              and isinstance(t.value, (ast.Dict, ast.DictComp))}
    for t in sorted((names & tables) - {"KIND_WORDS", "SURF_WORDS", "VECTORS"}):
        problems.append("describe_cell uses the table %s, which is not the "
                        "measurement's alphabet" % t)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--page", required=True)
    ap.add_argument("--payload", required=True)
    ap.add_argument("--patterns", required=True)
    ap.add_argument("--build", default=BUILD, help="vectors_build.py, for the static checks")
    a = ap.parse_args()

    D = json.load(open(a.payload, encoding="utf-8"))
    rows = list(csv.DictReader(open(a.patterns, encoding="utf-8-sig")))
    measured = ["%s/%s" % (r.get("session", ""), r.get("stem", "")) for r in rows]
    src = open(a.build, encoding="utf-8").read()
    failed = []

    def report(inv, problems, ok_line):
        print(inv)
        if problems:
            for p in problems[:12]:
                print("    " + p)
            if len(problems) > 12:
                print("    ... and %d more" % (len(problems) - 12))
            print("  ERR-VIEWER-RENDER-FAILED\n  VIOLATED")
            failed.append(inv)
        else:
            print("  " + ok_line + "\n  holds")

    R = run_page(a.page)
    tabs = R.get("tabs", {})
    probs = []
    if R.get("threw"):
        probs.append("the page threw: " + R["threw"])
    for t in TABS:
        if not tabs.get(t):
            probs.append("tab %s rendered nothing" % t)
    rendered = {t: groups_of(tabs.get(t, "")) for t in TABS}
    for t, key in (("pattern", "_pattern_cells"), ("surface", "_surface_cells")):
        have = {k[1] for k, _ in rendered[t] if k[0] == "cell"}
        for c in D.get(key, []):
            if c["cell"] not in have:
                probs.append("%s: declared cell %s has no group" % (t, c["cell"]))
    have = {k[1] for k, _ in rendered["clustering"] if k[0] == "cluster"}
    for g in D.get("clustering", {}).get("groups", []):
        if str(g["id"]) not in have:
            probs.append("clustering: cluster %s has no group" % g["id"])
    report("INV-VIEWER-RENDERS", probs,
           "ran to the end; %s" % ", ".join("%s %d groups" % (t, len(rendered[t]))
                                            for t in TABS))

    probs = []
    for t in ("pattern", "surface"):
        for (kind, key), seg in rendered[t]:
            f, d = seg.find("class='fig'"), seg.find("class='desc'")
            p = seg.find("class='pic")
            if f < 0 or d < 0 or not f < d or (p >= 0 and not d < p):
                probs.append("%s %s: picture, description and screenshots out of "
                             "order (%d, %d, %d)" % (t, key, f, d, p))
    report("INV-GROUP-ORDER", probs, "every group: picture, description, screenshots")

    probs = []
    want = collections.Counter(measured)
    for t in TABS:
        got = collections.Counter(re.findall(r"data-scene='([^']*)'", tabs.get(t, "")))
        missing = [s for s in want if got[s] == 0]
        twice = [s for s in got if got[s] > 1]
        stray = [s for s in got if s not in want]
        if missing:
            probs.append("%s: %d scene(s) missing, e.g. %s" % (t, len(missing), missing[0]))
        if twice:
            probs.append("%s: %d scene(s) shown more than once, e.g. %s"
                         % (t, len(twice), twice[0]))
        if stray:
            probs.append("%s: %d scene(s) never measured, e.g. %s" % (t, len(stray), stray[0]))
    report("INV-EVERY-SCENE-IN-EVERY-TAB", probs,
           "%d scenes, each exactly once in each of the three tabs" % len(want))

    probs = []
    C = D.get("clustering")
    if not C:
        probs.append("the payload has no clustering block")
    else:
        cols = [c["name"] for c in C.get("cols", [])]
        expect = vector_columns(src)
        if cols != expect:
            probs.append("columns %s, the two vectors are %s" % (cols, expect))
        for g in C.get("groups", []):
            if not isinstance(g.get("id"), int):
                probs.append("cluster id %r is not an integer" % g.get("id"))
            for k in ("name", "label", "title", "cell"):
                if k in g:
                    probs.append("cluster %s carries a %s" % (g.get("id"), k))
    if [k for k in D if not k.startswith("_") and k != "clustering"]:
        probs.append("per-vector blocks remain: %s"
                     % [k for k in D if not k.startswith("_") and k != "clustering"])
    report("INV-CLUSTERING-MATCHES-NO-PATTERN", probs,
           "the clustering reads the %d vector columns and names clusters by id"
           % len(vector_columns(src)))

    probs = []
    check_description(src, probs)
    report("INV-DESCRIPTION-FROM-MEASUREMENT", probs,
           "describe_cell reads only measured columns and the kind alphabet")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
