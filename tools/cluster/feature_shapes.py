"""Synthetic display layouts run through the REAL band computation.

The point of the diagrams is that the numbers beside them are not drawn by
hand: each layout is a list of leaf rects in device pixels, passed through
the same grouping, ramps and soft bins that rs_extract.py applies to a
dump. If the algorithm changes, these change with it.
"""
import sys, json
sys.path.insert(0, "/home/reiniertorreslabrada@tphwai.local/ark_ui/tools/cluster")
from rs_extract import soft_bins, ITEM_H_BANDS, ITEM_A_BANDS, region_area
from collections import defaultdict

DW, DH, DENS = 1316.0, 2832.0, 3.5
DISP_AREA = DW * DH


def bands(items):
    """items: list of (x, y, w, h) in px. Returns the five height shares and
    the per-group working so the card can show what happened."""
    grp = defaultdict(list)
    for r in items:
        grp[(round(r[2] / DENS), round(r[3] / DENS))].append(r)
    hmass = [0.0] * len(ITEM_H_BANDS)
    amass = [0.0] * len(ITEM_A_BANDS)
    colw = tot = 0.0
    work = []
    for k, mem in sorted(grp.items(), key=lambda kv: -kv[0][1]):
        gw, gh = k
        if len(mem) < 2:
            work.append({"k": f"{gw}x{gh}vp", "n": len(mem), "why": "only one member", "a": 0})
            continue
        small = min(1.0, max(0.0, (min(gw, gh) - 8.0) / 8.0))
        frac = (gw * DENS) * (gh * DENS) / DISP_AREA
        large = min(1.0, max(0.0, (0.75 - frac) / 0.30))
        ramp = small * large
        if ramp <= 0.0:
            why = "below the 8vp floor" if small <= 0 else f"covers {frac:.0%} of the display"
            work.append({"k": f"{gw}x{gh}vp", "n": len(mem), "why": why, "a": 0})
            continue
        area = region_area([(r[0], r[1], r[2], r[3]) for r in mem])
        a = ramp * area
        hb = soft_bins(gh * DENS / DH, ITEM_H_BANDS)
        ab = soft_bins(gw / max(1.0, gh), ITEM_A_BANDS)
        for i, v in enumerate(hb):
            hmass[i] += a * v
        for i, v in enumerate(ab):
            amass[i] += a * v
        colw += a * len({round(r[0] / 40) for r in mem})
        tot += a
        work.append({"k": f"{gw}x{gh}vp", "n": len(mem),
                     "h": round(gh * DENS / DH, 3), "ramp": round(ramp, 2),
                     "a": round(a), "why": ""})
    if tot <= 0:
        return None, work
    return {"h": [round(v / tot, 3) for v in hmass],
            "ar": [round(v / tot, 3) for v in amass],
            "cols": round(colw / tot, 2)}, work


def rows(n, h, top=112, gap=0, x=24, w=1268):
    return [(x, top + i * (h + gap), w, h) for i in range(n)]


def grid(cols, n, w, h, top=112, gapx=24, gapy=24, left=24):
    out = []
    for i in range(n):
        c, r = i % cols, i // cols
        out.append((left + c * (w + gapx), top + r * (h + gapy), w, h))
    return out


SHAPES = [
 ("Settings list", "Thin full-width rows, all the same height.",
  rows(12, 160, gap=8)),
 ("Chat list", "Taller rows with an avatar column; one repeating group.",
  rows(8, 240, gap=10)),
 ("Feed of cards", "Tall cards, two and a half visible, the third running off the bottom.",
  rows(3, 1180, gap=20)),
 ("Two-column gallery", "Square tiles, four rows deep.",
  grid(2, 8, 622, 622)),
 ("Three-column icon grid", "Small square tiles — a launcher page.",
  grid(3, 12, 400, 400, gapx=20, gapy=40)),
 ("Poster grid", "Two columns of tall posters, the bottom row cut off.",
  grid(2, 6, 622, 940)),
 ("Short-form video", "Items taller than the display; one fills it, the next waits below.",
  [(0, 0, 1316, 2900), (0, 2900, 1316, 2900)]),
 ("Player over comments", "A 16:9 surface on top, repeating comment rows beneath.",
  [(0, 112, 1316, 740), (0, 852, 1316, 740)] + rows(6, 200, top=1600, gap=8)),
 ("Horizontal carousel", "Wide cards running off the right edge.",
  [(24 + i * 900, 600, 860, 1100) for i in range(4)]),
 ("Mixed feed and rail", "Tall cards plus a row of small action buttons.",
  rows(3, 1100, gap=20) + [(1180, 900 + i * 180, 120, 120) for i in range(4)]),
 ("Masonry", "Two columns, three different item heights — three groups at once.",
  [(24, 112, 622, 700), (670, 112, 622, 1100),
   (24, 836, 622, 700), (670, 1236, 622, 1100),
   (24, 1560, 622, 420), (670, 2360, 622, 420)]),
 ("Dense tile wall", "Four columns of small tiles, twenty-four of them.",
  grid(4, 24, 300, 300, gapx=12, gapy=12)),
 ("Page-indicator dots", "Six 7vp dots under one repeating card group: the dots fall under the floor and are ignored.",
  rows(3, 1100, gap=20) + [(560 + i * 40, 2700, 24, 24) for i in range(6)]),
 ("Oversized card, ramped down", "The card covers 58% of the display, inside the 45\u201375% ramp, so it counts at 58% weight \u2014 not excluded, just reduced. The rows beside it count in full.",
  [(420, 0, 896, 2400), (420, 2420, 896, 2400)] + rows(9, 150, top=160, gap=20, x=24, w=360)),
 ("Split panes", "Two half-height panes, each a repeating unit.",
  [(0, 112, 1316, 1340), (0, 1472, 1316, 1340)]),
 ("Single hero, no repeat", "One large item and nothing it repeats with.",
  [(24, 300, 1268, 1800)]),
]

out = []
for name, note, items in SHAPES:
    res, work = bands(items)
    out.append({"name": name, "note": note,
                "items": [[round(v) for v in r] for r in items],
                "res": res, "work": work})
    h = "no repeating group" if not res else "  ".join(f"{v:.2f}" for v in res["h"])
    print(f"{name:<24} {len(items):>2} items   h = {h}")

json.dump({"dw": DW, "dh": DH, "dens": DENS, "bands": list(ITEM_H_BANDS),
           "shapes": out}, open("shapes.json", "w"), ensure_ascii=False)
print("\nwrote shapes.json")
