"""Package every motif: a schematic drawn from its median geometry, a short
description, and every pictured scene that fell into it."""
import csv, json, collections, statistics as st, os, base64

DESC = {
 "no dominant repeat":
   "Something repeats, but it covers less than 15% of the window - too little to call it the "
   "screen's layout. In this capture these are almost all full-screen video: the only things "
   "that repeat are the action rail and the caption drawn over the top of it.",
 "(no pattern at all)":
   "No two sibling branches shared a description. Either the screen genuinely has nothing "
   "repeating, or every candidate cell held more than eight pieces of content and was rejected "
   "as a page rather than a cell.",
 "media card list":
   "Cards stacked one under the other, each holding several images and lines of text. Comment "
   "threads, settings lists and live-stream rows all land here.",
 "media card grid, 2 columns":
   "Two columns of cards, each carrying images and text. The recommendation grid under a video, "
   "and product walls.",
 "image grid, 3 columns":
   "Three columns of plain images with no text inside the cell. A gallery, or a profile's wall "
   "of posts.",
 "plain box grid, 3 columns":
   "A three-column grid whose cells draw something but carry no recognisable content kind - the "
   "component tags were wrappers and the boxes were the wrong shape to read as icon or text.",
 "plain box grid, 2 columns":
   "As above with two columns. The cell content could not be identified.",
 "icon and label list":
   "Rows each holding one icon-sized square beside a line of text. A menu or a settings list.",
 "image list": "Full-width images stacked vertically, one per row.",
 "image row": "Images side by side on a single line.",
 "image stack": "Two or more images occupying the same spot - a crossfade, or a layered cover.",
 "media card row": "Cards side by side on one line: a horizontal carousel.",
 "plain box stack": "Untyped painted boxes on the same spot.",
}

DW, DH = 1316.0, 2832.0


def schematic(cols, rows, h, aspect, cover, arr):
    """Draw the motif from its own median numbers, not from an idea of it."""
    ih = max(0.02, min(h, 1.2)) * DH
    iw = max(40.0, ih * (aspect or 1.0))
    gap = 18.0
    cols = max(1, min(int(cols or 1), 6))
    if arr in ("column", "stack"):
        cols = 1
    total_w = cols * iw + (cols - 1) * gap
    if total_w > DW - 48:                       # fit the drawing to the screen
        iw = (DW - 48 - (cols - 1) * gap) / cols
        ih = iw / (aspect or 1.0) if aspect else ih
    rows = max(1, min(int(rows or 1), int((DH - 320) // max(1.0, ih + gap)) + 1, 8))
    x0 = (DW - (cols * iw + (cols - 1) * gap)) / 2
    y0 = 300.0
    p = ['<svg viewBox="-40 -40 %d %d" preserveAspectRatio="xMidYMid meet" role="img">'
         % (DW + 80, DH + 80)]
    p.append('<rect x="0" y="0" width="%d" height="%d" fill="var(--bg,#fff)" '
             'stroke="#64748b" stroke-width="10" rx="26"/>' % (DW, DH))
    p.append('<rect x="0" y="0" width="%d" height="112" fill="#94a3b8" opacity=".18"/>' % DW)
    p.append('<rect x="0" y="%d" width="%d" height="96" fill="#94a3b8" opacity=".18"/>'
             % (DH - 96, DW))
    n = 0
    for r in range(rows):
        for c in range(cols):
            x = x0 + c * (iw + gap)
            y = y0 + r * (ih + gap)
            if y > DH + 40:
                continue
            off = (y + ih) > DH
            p.append('<rect x="%.0f" y="%.0f" width="%.0f" height="%.0f" rx="14" '
                     'fill="#2563eb" opacity="%s" stroke="#2563eb" stroke-width="8"%s/>'
                     % (x, y, iw, ih, ".20" if off else ".30",
                        ' stroke-dasharray="28 18"' if off else ""))
            n += 1
    if arr == "stack" and n:
        p.append('<rect x="%.0f" y="%.0f" width="%.0f" height="%.0f" rx="14" fill="none" '
                 'stroke="#2563eb" stroke-width="8" stroke-dasharray="20 14" '
                 'transform="translate(26,26)"/>' % (x0, y0, iw, ih))
    p.append('</svg>')
    return "".join(p)


rows = list(csv.DictReader(open("patterns.csv")))
by = collections.defaultdict(list)
for r in rows:
    by[r["motif"] or "(no pattern at all)"].append(r)

out = []
for m, v in by.items():
    def med(k, d=0.0):
        xs = [float(r[k]) for r in v if r.get(k) not in (None, "")]
        return st.median(xs) if xs else d
    pics = [r for r in v if r["shot"]]
    cells = collections.Counter(r["pat"] for r in v if r["pat"])
    imgs = []
    for r in sorted(pics, key=lambda r: -float(r["pat_cover"] or 0)):
        f = "patreps/" + r["shot"].replace("/", "__")
        if not os.path.exists(f):
            continue
        imgs.append({"img": "data:image/jpeg;base64," +
                     base64.b64encode(open(f, "rb").read()).decode(),
                     "cover": r["pat_cover"], "h": r["pat_h"], "pat": r["pat"],
                     "cols": r["pat_cols"], "n": r["pat_n"], "ovl": r["overlay_frac"]})
    out.append({"motif": m, "n": len(v), "pictured": len(pics),
                "shown": len(imgs),
                "cover": round(med("pat_cover"), 2),
                "h": round(med("pat_h"), 2),
                "cols": round(med("pat_cols"), 1),
                "members": round(med("pat_n"), 1),
                "cell": (cells.most_common(1)[0][0] if cells else ""),
                "desc": DESC.get(m, ""),
                "svg": schematic(med("pat_cols", 1), med("pat_rows", 1), med("pat_h", .2),
                                 med("pat_aspect", 1), med("pat_cover"),
                                 (v[0]["pat_arr"] or "grid")),
                "reps": imgs})
out.sort(key=lambda d: (d["motif"].startswith("no ") or d["motif"].startswith("("), -d["n"]))
json.dump({"groups": out, "total": len(rows)}, open("pat_payload.json", "w"))
print("%d motifs, %d pictures embedded, %.1f MB"
      % (len(out), sum(len(g["reps"]) for g in out),
         os.path.getsize("pat_payload.json") / 1048576))
for g in out:
    print("   %-28s %3d scenes, %2d shown  %s" % (g["motif"], g["n"], g["shown"],
          "MISSING DESC" if not g["desc"] else ""))
