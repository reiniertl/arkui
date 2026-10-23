"""Fold a viewer directory into one HTML file that opens anywhere.

The viewer already needs no server and no Python - it is a page, a data
file and a folder of thumbnails, and a browser opens it off a disk. But
that is four things to send and three of them break if someone moves one,
so this writes the data and the pictures into the page itself as base64
and hands back a single file. Nothing to install on the far end.

It also carries both languages. The English is the original text; the
Chinese sits beside it in a table and a button swaps them, so one file
serves either reader instead of two files drifting apart.
"""
import base64, os, re, sys, json, argparse

MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
        ".webp": "image/webp"}

# English exactly as the page says it -> Chinese. Whitespace is collapsed on
# both sides before matching, so the long paragraphs may be wrapped freely in
# the source without the key having to match the wrapping.
ZH = {
 "Scene clusters": "场景聚类",
 "Drag to rotate · wheel to zoom · shift-drag to pan · click a point to jump":
   "拖动旋转 · 滚轮缩放 · Shift+拖动平移 · 点击任一点跳转",
 "reset view": "重置视角",
 "Every scene, projected to three components by PCA over the same weighted "
 "vector the clustering used, so distance here is distance there. Two "
 "components flattened it far enough that separated clusters landed on top "
 "of each other; turning the cloud shows which pairs are genuinely adjacent. "
 "Click a point to jump to its cluster.":
   "每个场景都用 PCA 投影到三个主成分上，所用向量与聚类时完全相同（含权重），"
   "因此这里的距离就是那里的距离。只用两个主成分会把空间压得太扁，"
   "本来分开的簇会重叠在一起；旋转点云才能看出哪些簇是真正相邻的。"
   "点击任一点可跳转到它所属的簇。",
 "Every scene is shown. Ordered from the centre of its cluster outwards, so "
 "the last tiles are where it frays. Write a name once the pictures agree on "
 "one, then Download to keep them.":
   "所有场景均已列出，按照距离簇中心由近及远排列，"
   "因此最后几张就是该簇开始松散的地方。"
   "当这些图片指向同一类场景时，为它写一个名称，然后点击下载保存。",
 "Download names": "下载名称",
 "clusters": "簇数",
 "scenes clustered": "已归类场景",
 "fitted none": "未归入任何簇",
 "silhouette": "轮廓系数",
 "fitted no cluster": "未归入任何簇",
 "undecided scene": "未定场景",
 "never held still": "从未稳定",
 "surface": "合成面",
 "scenes": "个场景",
 "samples": "个采样",
 "cohesion": "凝聚度",
 "English": "English",
}


def collapse(s):
    return re.sub(r"\s+", " ", s).strip()


def build(src, out):
    html = open(os.path.join(src, "viewer.html"), encoding="utf-8").read()
    data = open(os.path.join(src, "clusters.js"), encoding="utf-8").read()

    thumbs, tdir = {}, os.path.join(src, "thumbs")
    if os.path.isdir(tdir):
        for f in sorted(os.listdir(tdir)):
            ext = os.path.splitext(f)[1].lower()
            if ext not in MIME:
                continue
            with open(os.path.join(tdir, f), "rb") as fh:
                thumbs[f] = "data:%s;base64,%s" % (
                    MIME[ext], base64.b64encode(fh.read()).decode("ascii"))

    # the data file and the pictures, inlined
    tag = '<script src="clusters.js"></script>'
    if tag not in html:
        sys.exit("viewer.html does not load clusters.js the expected way")
    html = html.replace(tag, "<script>\n%s\n</script>\n<script>\nvar THUMBS = %s;\n</script>"
                        % (data, json.dumps(thumbs)))

    # thumb() now reads the inlined map instead of a path on disk
    old = '''  function thumb(shot) {
    return shot ? "thumbs/" + shot.replace(/\\.\\.\\//g, "").replace(/\\//g, "__")
      .replace(".jpeg", ".jpg").replace(".png", ".jpg") : "";
  }'''
    new = '''  function thumb(shot) {
    if (!shot) { return ""; }
    var k = shot.replace(/\\.\\.\\//g, "").replace(/\\//g, "__")
      .replace(".jpeg", ".jpg").replace(".png", ".jpg");
    return (typeof THUMBS !== "undefined" && THUMBS[k]) ? THUMBS[k] : "";
  }'''
    if old not in html:
        sys.exit("viewer.html does not define thumb() the expected way")
    html = html.replace(old, new)

    # the language switch, appended so it runs after the page has rendered
    table = {collapse(k): v for k, v in ZH.items()}
    switch = """
<style>
  #langbar { position:fixed; top:10px; right:12px; z-index:50; }
  #langbar button { font:inherit; font-size:12px; padding:4px 10px; cursor:pointer;
    border-radius:6px; border:1px solid var(--line, #d7dbe0);
    background:var(--card, #fff); color:inherit; }
</style>
<div id="langbar"><button id="lang">中文</button></div>
<script>
(function () {
  var ZH = %s;
  var lang = "en", orig = new WeakMap();
  // A render replaces whole subtrees, so the pass runs again on every
  // mutation rather than once at load. Originals are remembered per node so
  // switching back is exact rather than a reverse lookup.
  function walk(node) {
    if (node.nodeType === 3) {
      if (!orig.has(node)) { orig.set(node, node.nodeValue); }
      var en = orig.get(node), key = en.replace(/\\s+/g, " ").trim();
      if (!key) { return; }
      // Replace the node's text outright, keeping only its surrounding
      // whitespace. en.replace(key, ...) looked right and silently did
      // nothing on every wrapped paragraph: key is collapsed to single
      // spaces, en still carries the source's line breaks, so the search
      // string was never present and the English stayed put.
      if (lang === "zh" && ZH[key]) {
        var pad = en.match(/^(\\s*)[\\s\\S]*?(\\s*)$/);
        node.nodeValue = pad[1] + ZH[key] + pad[2];
      } else { node.nodeValue = en; }
      return;
    }
    if (node.nodeType !== 1) { return; }
    if (node.id === "langbar" || node.tagName === "SCRIPT"
        || node.tagName === "STYLE" || node.tagName === "INPUT") { return; }
    for (var i = 0; i < node.childNodes.length; i++) { walk(node.childNodes[i]); }
  }
  function apply() { walk(document.body); }
  document.getElementById("lang").addEventListener("click", function () {
    lang = (lang === "en") ? "zh" : "en";
    this.textContent = (lang === "en") ? "中文" : "English";
    apply();
  });
  new MutationObserver(function () { if (lang === "zh") { apply(); } })
    .observe(document.body, {childList: true, subtree: true});
  apply();
})();
</script>
</body>"""  % json.dumps(table, ensure_ascii=False)
    html = html.replace("</body>", switch, 1)

    with open(out, "w", encoding="utf-8") as fh:
        fh.write(html)
    mb = os.path.getsize(out) / (1024.0 * 1024.0)
    print("  %s  (%.1f MB, %d thumbnails inlined)" % (out, mb, len(thumbs)))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src", help="a directory holding viewer.html, clusters.js and thumbs/")
    ap.add_argument("--out", default="", help="output html (default: <src>/standalone.html")
    a = ap.parse_args()
    build(a.src, a.out or os.path.join(a.src, "standalone.html"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
