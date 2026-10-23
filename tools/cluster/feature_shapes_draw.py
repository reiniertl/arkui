"""Draw each synthetic layout, colouring items by the group they fall in and
marking the groups the ramps exclude. The geometry drawn and the numbers
reported both come from shapes.py, so the picture cannot drift from the value."""
import json, sys
sys.path.insert(0, "/home/reiniertorreslabrada@tphwai.local/ark_ui/tools/cluster")
from rs_extract import soft_bins, ITEM_H_BANDS, region_area
from collections import defaultdict
import feature_shapes as S

HUES = ["#2563eb", "#0f766e", "#b45309", "#7c3aed", "#be123c", "#0369a1"]
OUT = "#94a3b8"


def svg_for(items):
    """Returns (svg, legend) - legend lists each group with its fate."""
    grp = defaultdict(list)
    for r in items:
        grp[(round(r[2] / S.DENS), round(r[3] / S.DENS))].append(r)
    order = sorted(grp, key=lambda k: (-k[1], -k[0]))
    fate, legend = {}, []
    ci = 0
    for k in order:
        gw, gh = k
        mem = grp[k]
        if len(mem) < 2:
            fate[k] = (None, "single, not a repeat")
            legend.append({"k": f"{gw}×{gh}vp", "n": len(mem), "c": OUT,
                           "why": "one member, not a repeating group", "in": False})
            continue
        small = min(1.0, max(0.0, (min(gw, gh) - 8.0) / 8.0))
        frac = (gw * S.DENS) * (gh * S.DENS) / S.DISP_AREA
        large = min(1.0, max(0.0, (0.75 - frac) / 0.30))
        ramp = small * large
        if ramp <= 0.0:
            why = (f"{min(gw, gh)}vp is under the 8vp floor" if small <= 0
                   else f"one item covers {frac:.0%} of the display, over the 75% ceiling")
            fate[k] = (None, why)
            legend.append({"k": f"{gw}×{gh}vp", "n": len(mem), "c": OUT,
                           "why": why, "in": False})
            continue
        c = HUES[ci % len(HUES)]; ci += 1
        fate[k] = (c, "")
        hfrac = gh * S.DENS / S.DH
        b = soft_bins(hfrac, ITEM_H_BANDS)
        top = max(range(len(b)), key=lambda i: b[i])
        legend.append({"k": f"{gw}×{gh}vp", "n": len(mem), "c": c, "in": True,
                       "why": (f"height {hfrac:.2f} of the display → mostly band {top}"
                               + (f", ramped to {ramp:.0%}" if ramp < 1 else ""))})

    xs = [r[0] for r in items] + [0]; ys = [r[1] for r in items] + [0]
    xe = [r[0] + r[2] for r in items] + [S.DW]; ye = [r[1] + r[3] for r in items] + [S.DH]
    pad = 70
    x0, y0 = min(xs) - pad, min(ys) - pad
    x1, y1 = max(xe) + pad, max(ye) + pad
    w, h = x1 - x0, y1 - y0

    p = [f'<svg viewBox="{x0:.0f} {y0:.0f} {w:.0f} {h:.0f}" '
         f'preserveAspectRatio="xMidYMid meet" role="img">']
    # Diagonals drawn explicitly rather than with patternTransform: several
    # renderers ignore the transform and the hatch comes out as a grid.
    p.append('<defs><pattern id="hatch" width="60" height="60" '
             'patternUnits="userSpaceOnUse">'
             f'<rect width="60" height="60" fill="{OUT}" opacity=".08"/>'
             f'<path d="M-15 15 L15 -15 M0 60 L60 0 M45 75 L75 45" stroke="{OUT}" '
             'stroke-width="11" opacity=".42" fill="none"/></pattern></defs>')
    # the area outside the display, so overflow reads as overflow
    p.append(f'<rect x="{x0:.0f}" y="{y0:.0f}" width="{w:.0f}" height="{h:.0f}" '
             'fill="#94a3b8" opacity=".07"/>')
    p.append(f'<rect x="0" y="0" width="{S.DW:.0f}" height="{S.DH:.0f}" '
             'fill="var(--bg,#fff)" stroke="#64748b" stroke-width="10"/>')
    for r in items:
        k = (round(r[2] / S.DENS), round(r[3] / S.DENS))
        c, _ = fate[k]
        off = (r[0] < 0 or r[1] < 0 or r[0] + r[2] > S.DW or r[1] + r[3] > S.DH)
        if c is None:
            p.append(f'<rect x="{r[0]}" y="{r[1]}" width="{r[2]}" height="{r[3]}" '
                     f'fill="url(#hatch)" stroke="{OUT}" stroke-width="7" '
                     'stroke-dasharray="26 18" rx="14"/>')
        else:
            p.append(f'<rect x="{r[0]}" y="{r[1]}" width="{r[2]}" height="{r[3]}" '
                     f'fill="{c}" opacity="{0.22 if off else 0.34}" stroke="{c}" '
                     f'stroke-width="9" rx="14"'
                     + (' stroke-dasharray="30 20"' if off else '') + '/>')
    p.append('</svg>')
    return "".join(p), legend


data = json.load(open("shapes.json"))
for sh, (name, note, items) in zip(data["shapes"], S.SHAPES):
    svg, legend = svg_for(items)
    sh["svg"] = svg
    sh["legend"] = legend
json.dump(data, open("shapes.json", "w"), ensure_ascii=False)
print(f"drew {len(data['shapes'])} shapes")
for sh in data["shapes"]:
    inc = sum(1 for l in sh["legend"] if l["in"])
    print(f"  {sh['name']:<28} groups {len(sh['legend'])}, counted {inc}, "
          f"svg {len(sh['svg'])} bytes")
