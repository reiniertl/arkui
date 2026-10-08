#!/usr/bin/env python3
"""
correlate.py — join Ark scene descriptors to CPU frequency / power samples and
report which Ark-derived features actually predict them.

The measurement step, before any classifier exists. It answers the question
the project rests on: do features derived from ArkUI/ArkWeb carry information
about power and frequency that the schedule does not already have?

    ./correlate.py --descriptors scenes.jsonl --power power.csv --out joined.csv

Inputs
  --descriptors  JSONL, one ark_scene_descriptor_t per line (ABI 2).
  --power        CSV from sample_power.sh. Must share CLOCK_MONOTONIC with
                 every descriptor producer.

Output
  joined.csv     one row per interval: derived features plus the mean
                 frequency / power over it.
  stdout         a data-integrity report, then features ranked by |Pearson r|
                 against each target with a lead-correlation check.

Three ABI-2 fields change the analysis and are enforced here:

  vsyncs       cadence is frames/vsyncs, never frames/second. Thirty frames in
               half a second is saturation at 60Hz and half rate at 120Hz, and
               variable refresh moves the panel rate inside a session.
  visible      background windows are excluded by default. A background music
               player producing frames is not a foreground scene.
  seq          gaps mean the ring dropped records — which happens when the
               system was busiest. A gap must never be read as idle.

On the lead column: a feature that only correlates at lag 0 describes the
present, which the governor already sees from utilisation. A feature that
correlates with the NEXT interval's target is predictive, and that is the only
kind worth putting in a scheduler.
"""

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

NS_PER_S = 1_000_000_000

VIS_BACKGROUND, VIS_OCCLUDED, VIS_FOREGROUND = 0, 1, 2
FLUSH_NAMES = {0: "timer", 1: "gesture_edge", 2: "page_change",
               3: "config_change", 4: "visibility", 5: "shutdown"}
FLUSH_CONFIG_CHANGE = 3


# ---------------------------------------------------------------- loading

def load_descriptors(path: str) -> List[dict]:
    out = []
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as exc:
                print(f"warn: {path}:{lineno}: {exc}", file=sys.stderr)
    out.sort(key=lambda d: d.get("t_start_ns", 0))
    return out


def load_power(path: str) -> Tuple[List[str], List[Dict[str, float]]]:
    rows: List[Dict[str, float]] = []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        rdr = csv.DictReader(fh)
        cols = [c for c in (rdr.fieldnames or []) if c and c != "t_ns"]
        for r in rdr:
            try:
                rec = {"t_ns": float(r["t_ns"])}
            except (KeyError, TypeError, ValueError):
                continue
            for c in cols:
                try:
                    rec[c] = float(r[c])
                except (TypeError, ValueError):
                    rec[c] = float("nan")
            rows.append(rec)
    rows.sort(key=lambda r: r["t_ns"])
    return cols, rows


# ------------------------------------------------------- integrity checks

def check_sequences(descs: List[dict]) -> Dict[Tuple, int]:
    """Count records lost per (window_id, source) from gaps in seq.

    A dropped record is indistinguishable from an idle interval without this,
    and loss concentrates exactly where load was highest.
    """
    last: Dict[Tuple, int] = {}
    lost: Dict[Tuple, int] = defaultdict(int)
    for d in descs:
        key = (d.get("window_id"), d.get("source"))
        s = d.get("seq")
        if s is None:
            continue
        prev = last.get(key)
        if prev is not None and s > prev + 1:
            lost[key] += s - prev - 1
        last[key] = s
    return dict(lost)


# ---------------------------------------------------------------- features

def derive_features(d: dict) -> Optional[Dict[str, float]]:
    """Flatten one descriptor into rate-normalised features.

    Raw counters are meaningless across intervals of unequal length — and
    intervals ARE unequal once you flush on transition edges — so everything
    that accumulates becomes a per-second rate or a ratio.
    """
    t0 = d.get("t_start_ns")
    t1 = d.get("t_end_ns")
    if t0 is None or t1 is None or t1 <= t0:
        return None
    dt = (t1 - t0) / NS_PER_S

    dyn = d.get("dyn", {})
    st = d.get("st", {})

    frames = float(dyn.get("frames", 0))
    vsyncs = float(dyn.get("vsyncs", 0))
    dm = float(dyn.get("dirty_measure", 0))
    dl = float(dyn.get("dirty_layout", 0))
    dr = float(dyn.get("dirty_render", 0))
    dirty_total = dm + dl + dr

    f: Dict[str, float] = {}

    # --- cadence ----------------------------------------------------------
    # frame_ratio is the interpretable one. fps is kept only because absolute
    # rate matters for some power questions; never compare it across devices
    # or across a refresh-rate switch.
    f["frame_ratio"] = (frames / vsyncs) if vsyncs else float("nan")
    f["fps"] = frames / dt
    f["vsync_rate"] = vsyncs / dt

    # --- dynamics: the discriminative half --------------------------------
    f["dirty_rate"] = dirty_total / dt
    # Render-only dirt with steady frames = a repainting surface (video,
    # animation). Layout dirt = structural churn (list scroll, message
    # arriving). The cheapest separator in the whole set.
    f["render_ratio"] = (dr / dirty_total) if dirty_total else 0.0
    f["layout_ratio"] = ((dm + dl) / dirty_total) if dirty_total else 0.0
    f["mean_damage"] = (float(dyn.get("damage_permille_sum", 0)) / frames) if frames else 0.0
    f["scroll_px_per_s"] = float(dyn.get("scroll_delta_px", 0)) / dt
    f["scroll_evt_per_s"] = float(dyn.get("scroll_events", 0)) / dt
    f["text_mut_per_s"] = float(dyn.get("text_mutations", 0)) / dt
    f["node_churn_per_s"] = (float(dyn.get("nodes_created", 0)) +
                             float(dyn.get("nodes_destroyed", 0))) / dt
    f["node_reuse_per_s"] = float(dyn.get("nodes_reused", 0)) / dt
    f["anim_active_max"] = float(dyn.get("anim_active_max", 0))
    f["gestures_per_s"] = float(dyn.get("gestures", 0)) / dt
    f["media_playing"] = 1.0 if dyn.get("media_playing") else 0.0
    f["media_audible"] = 1.0 if dyn.get("media_audible") else 0.0
    f["ime_visible"] = 1.0 if dyn.get("ime_visible") else 0.0

    # --- structure: the slow half -----------------------------------------
    f["node_count"] = float(st.get("node_count", 0))
    f["max_depth"] = float(st.get("max_depth", 0))
    f["text_len_total"] = float(st.get("text_len_total", 0))
    f["text_len_largest"] = float(st.get("text_len_largest", 0))
    f["editable_count"] = float(st.get("editable_count", 0))
    f["scroller_count"] = float(st.get("scroller_count", 0))
    f["largest_leaf_frac"] = float(st.get("largest_leaf_frac", 0))
    f["largest_leaf_aspect"] = float(st.get("largest_leaf_aspect", 0))
    # High opaque_frac means this record's structure is not trustworthy alone;
    # an ArkWeb or XComponent record should cover the inside.
    f["opaque_frac"] = float(st.get("opaque_frac", 0))
    f["window_area"] = float(d.get("window_area_permille", 1000))

    hist = st.get("role_hist") or []
    names = ["text", "image", "video", "editable", "button",
             "scroller", "container", "canvas", "opaque", "other"]
    total = float(sum(hist)) or 1.0
    for i, n in enumerate(names):
        f[f"role_{n}_frac"] = (float(hist[i]) / total) if i < len(hist) else 0.0

    return f


# ---------------------------------------------------------------- joining

def mean_over(rows: List[Dict[str, float]], col: str,
              t0: float, t1: float) -> float:
    vals = [r[col] for r in rows
            if t0 <= r["t_ns"] <= t1 and not math.isnan(r.get(col, float("nan")))]
    return (sum(vals) / len(vals)) if vals else float("nan")


def delta_over(rows: List[Dict[str, float]], col: str,
               t0: float, t1: float) -> float:
    """For monotonic counters such as cpuidle residency."""
    vals = [r[col] for r in rows
            if t0 <= r["t_ns"] <= t1 and not math.isnan(r.get(col, float("nan")))]
    return (vals[-1] - vals[0]) if len(vals) >= 2 else float("nan")


def pearson(xs: List[float], ys: List[float]) -> Optional[float]:
    pairs = [(x, y) for x, y in zip(xs, ys)
             if not (math.isnan(x) or math.isnan(y))]
    n = len(pairs)
    if n < 3:
        return None
    mx = sum(p[0] for p in pairs) / n
    my = sum(p[1] for p in pairs) / n
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pairs)
    sxx = sum((p[0] - mx) ** 2 for p in pairs)
    syy = sum((p[1] - my) ** 2 for p in pairs)
    if sxx <= 0 or syy <= 0:
        return None
    return sxy / math.sqrt(sxx * syy)


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--descriptors", required=True)
    ap.add_argument("--power", required=True)
    ap.add_argument("--out", default="joined.csv")
    ap.add_argument("--bundle", default=None, help="restrict to one bundle")
    ap.add_argument("--all-visibility", action="store_true",
                    help="keep background and occluded windows (default: foreground only)")
    ap.add_argument("--keep-config-change", action="store_true",
                    help="keep config_change intervals (default: dropped; a rotation "
                         "is a relayout storm, not a scene)")
    args = ap.parse_args()

    descs = load_descriptors(args.descriptors)
    if not descs:
        print("no descriptors", file=sys.stderr)
        return 1

    lost = check_sequences(descs)
    total_in = len(descs)

    if args.bundle:
        descs = [d for d in descs if d.get("bundle") == args.bundle]
    dropped_vis = 0
    if not args.all_visibility:
        before = len(descs)
        descs = [d for d in descs if d.get("visible", VIS_FOREGROUND) == VIS_FOREGROUND]
        dropped_vis = before - len(descs)
    dropped_cfg = 0
    if not args.keep_config_change:
        before = len(descs)
        descs = [d for d in descs if d.get("flush_reason", 0) != FLUSH_CONFIG_CHANGE]
        dropped_cfg = before - len(descs)

    pcols, prows = load_power(args.power)
    if not descs:
        print("no descriptors after filtering", file=sys.stderr)
        return 1
    if not prows:
        print("no power samples", file=sys.stderr)
        return 1

    freq_cols = [c for c in pcols if c.endswith("_khz")]
    idle_cols = [c for c in pcols if c.endswith("_idle_us")]
    has_power = "power_uw" in pcols

    rows: List[Dict[str, float]] = []
    meta: List[dict] = []
    no_vsync = 0
    for d in descs:
        f = derive_features(d)
        if f is None:
            continue
        if math.isnan(f["frame_ratio"]):
            no_vsync += 1
        t0, t1 = float(d["t_start_ns"]), float(d["t_end_ns"])

        tgt: Dict[str, float] = {}
        for c in freq_cols:
            tgt[c] = mean_over(prows, c, t0, t1)
        if freq_cols:
            vals = [tgt[c] for c in freq_cols if not math.isnan(tgt[c])]
            tgt["mean_khz"] = (sum(vals) / len(vals)) if vals else float("nan")
        for c in idle_cols:
            tgt[c + "_delta"] = delta_over(prows, c, t0, t1)
        if has_power:
            tgt["power_uw"] = mean_over(prows, "power_uw", t0, t1)

        rows.append({**f, **tgt})
        meta.append({"bundle": d.get("bundle", ""), "route": d.get("route", ""),
                     "source": d.get("source", ""), "seq": d.get("seq", ""),
                     "visible": d.get("visible", ""),
                     "flush_reason": FLUSH_NAMES.get(d.get("flush_reason", 0), "?"),
                     "gesture_last": d.get("dyn", {}).get("gesture_last", 0),
                     "t_start_ns": t0, "t_end_ns": t1})

    if not rows:
        print("no usable intervals", file=sys.stderr)
        return 1

    # ---- integrity report ------------------------------------------------
    print(f"\nread {total_in} records, using {len(rows)}")
    if dropped_vis:
        print(f"  dropped {dropped_vis} non-foreground (use --all-visibility to keep)")
    if dropped_cfg:
        print(f"  dropped {dropped_cfg} config_change (use --keep-config-change to keep)")
    if lost:
        tot = sum(lost.values())
        print(f"  !! {tot} records LOST to ring drops across {len(lost)} streams")
        print("     gaps are not idle; treat affected windows as unmeasured")
    if no_vsync:
        print(f"  !! {no_vsync} records have no vsync count — cadence is "
              f"uninterpretable for those (ABI < 2 producer?)")

    feature_names = sorted(derive_features(descs[0]).keys())
    target_names = [t for t in ("mean_khz", "power_uw") if t in rows[0]]
    if not target_names:
        target_names = [c for c in rows[0] if c.endswith("_khz")][:1]

    all_cols = (["t_start_ns", "t_end_ns", "bundle", "route", "source", "seq",
                 "visible", "flush_reason", "gesture_last"]
                + feature_names + sorted(set(rows[0]) - set(feature_names)))
    with open(args.out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=all_cols, extrasaction="ignore")
        w.writeheader()
        for m, r in zip(meta, rows):
            w.writerow({**m, **r})
    print(f"  joined table -> {args.out}\n")

    for tname in target_names:
        ys = [r.get(tname, float("nan")) for r in rows]
        ys_next = ys[1:] + [float("nan")]

        scored = []
        for fn in feature_names:
            xs = [r[fn] for r in rows]
            r0 = pearson(xs, ys)
            r1 = pearson(xs, ys_next)
            if r0 is None and r1 is None:
                continue
            scored.append((fn, r0, r1))
        scored.sort(key=lambda t: abs(t[1]) if t[1] is not None else 0, reverse=True)

        print(f"=== {tname} " + "=" * (56 - len(tname)))
        print(f"{'feature':<26} {'r(now)':>9} {'r(next)':>9}   note")
        for fn, r0, r1 in scored[:20]:
            s0 = f"{r0:+.3f}" if r0 is not None else "    -"
            s1 = f"{r1:+.3f}" if r1 is not None else "    -"
            note = ""
            if r0 is not None and r1 is not None:
                if abs(r1) > abs(r0) + 0.05:
                    note = "leading -> predictive"
                elif abs(r0) > 0.3 and abs(r1) < abs(r0) - 0.05:
                    note = "coincident only"
            print(f"{fn:<26} {s0:>9} {s1:>9}   {note}")
        print()

    print("Reminder: a feature strong only in r(now) tells the governor what")
    print("utilisation already told it. Look for r(next).\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
