#!/usr/bin/env bash
# probe_batch.sh — walk a list of (app, scene) pairs and build a coverage table.
#
# Runs on the HOST and drives the device over hdc. Coverage varies by screen
# within one app, so the unit of work is (app, scene), not app — which is also
# your profiling unit, so the effort is not wasted.
#
#   ./probe_batch.sh --setup                    # push the probe, enable dumps
#   ./probe_batch.sh --plan plan.txt            # guided run (you navigate)
#   ./probe_batch.sh --plan plan.txt --launch   # auto-launch each app first
#   ./probe_batch.sh --summary cov.csv          # re-print the table
#
# Plan file: one row per (app, scene), pipe-separated, # for comments.
#
#   # bundle                  | scene      | ability (optional)
#   com.huawei.hmos.video     | home-feed  | EntryAbility
#   com.huawei.hmos.video     | player     |
#   com.example.chat          | thread     | MainAbility
#
# An empty ability means "do not launch, I will navigate there myself" — which
# is how you reach any scene that is not the landing screen.

set -u

DEV_DIR=/data/local/tmp
DEV_PROBE="$DEV_DIR/scene_probe.sh"
DEV_CSV="$DEV_DIR/coverage.csv"
HERE="$(cd "$(dirname "$0")" && pwd)"

PLAN=""
OUT="coverage.csv"
LAUNCH=0
PROMPT=1
SETUP=0
SUMMARY_ONLY=""
SETTLE=3

usage() {
    sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --plan)       PLAN="${2:-}"; shift 2 ;;
        --out)        OUT="${2:-}"; shift 2 ;;
        --launch)     LAUNCH=1; shift ;;
        --no-prompt)  PROMPT=0; shift ;;
        --settle)     SETTLE="${2:-3}"; shift 2 ;;
        --setup)      SETUP=1; shift ;;
        --summary)    SUMMARY_ONLY="${2:-}"; shift 2 ;;
        -h|--help)    usage 0 ;;
        *) echo "unknown option: $1" >&2; usage 2 ;;
    esac
done

die() { echo "error: $*" >&2; exit 1; }

# ---------------------------------------------------------------- summary

print_summary() {
    local csv="$1"
    [ -f "$csv" ] || die "no such file: $csv"
    echo ""
    awk -F, 'NR>1 {
        printf "  %-32s %-14s %6s el %4s opq  %s\n", $2, $3, $5, $6, $15
    }' "$csv"
    echo ""
    echo "  verdicts:"
    awk -F, 'NR>1 { c[$15]++ } END { for (v in c) printf "    %-8s %d\n", v, c[v] }' "$csv" | sort
    echo ""
    echo "  ARKUI  full coverage, instrument and go"
    echo "  MIXED  needs the ArkWeb record, or outside-only for XComponent"
    echo "  THIN   native shell around a surface"
    echo "  BLIND  surface-rendered; the tree sees nothing"
    echo ""
}

if [ -n "$SUMMARY_ONLY" ]; then
    print_summary "$SUMMARY_ONLY"
    exit 0
fi

# ---------------------------------------------------------------- checks

command -v hdc >/dev/null 2>&1 || die "hdc not on PATH"
hdc shell echo ok >/dev/null 2>&1 || die "no device reachable (try: hdc list targets)"

# ---------------------------------------------------------------- setup

if [ "$SETUP" -eq 1 ]; then
    [ -f "$HERE/scene_probe.sh" ] || die "scene_probe.sh not found beside this script"
    echo "pushing probe..."
    hdc file send "$HERE/scene_probe.sh" "$DEV_PROBE" >/dev/null || die "push failed"
    echo "enabling the ArkUI dump path..."
    hdc shell param set persist.ace.debug.enabled 1 >/dev/null 2>&1 \
        || echo "  warn: param set failed — you may lack privilege"
    echo ""
    echo "Done. IMPORTANT: the dump path is wired at app start, so every app you"
    echo "intend to probe must be restarted (force-stopped and relaunched) after"
    echo "this point. Apps already running will produce empty dumps."
    echo ""
    exit 0
fi

[ -n "$PLAN" ] || usage 2
[ -f "$PLAN" ] || die "no such plan file: $PLAN"
hdc shell "[ -f $DEV_PROBE ]" >/dev/null 2>&1 || die "probe not on device — run --setup first"

# Start a fresh table.
hdc shell rm -f "$DEV_CSV" >/dev/null 2>&1

# ---------------------------------------------------------------- run

rows=0
done_rows=0
skipped=0

while IFS='|' read -r bundle scene ability; do
    bundle="$(echo "${bundle:-}" | tr -d '[:space:]')"
    scene="$(echo "${scene:-}"  | sed 's/^ *//; s/ *$//')"
    ability="$(echo "${ability:-}" | tr -d '[:space:]')"

    case "$bundle" in ''|'#'*) continue ;; esac
    [ -n "$scene" ] || scene="default"
    rows=$((rows + 1))

    echo "────────────────────────────────────────────────────────────"
    echo "[$rows] $bundle    scene: $scene"

    if [ "$LAUNCH" -eq 1 ] && [ -n "$ability" ]; then
        # Force-stop first so the dump path is picked up on the fresh start.
        hdc shell aa force-stop "$bundle" >/dev/null 2>&1
        echo "     launching $ability ..."
        hdc shell aa start -b "$bundle" -a "$ability" >/dev/null 2>&1 \
            || echo "     warn: aa start failed; navigate by hand"
        sleep "$SETTLE"
    fi

    if [ "$PROMPT" -eq 1 ]; then
        printf "     navigate to '%s', then ENTER   (s=skip, q=quit): " "$scene"
        read -r key </dev/tty || key=""
        case "$key" in
            s|S) echo "     skipped"; skipped=$((skipped + 1)); continue ;;
            q|Q) echo "     stopping early"; break ;;
        esac
    fi

    if hdc shell sh "$DEV_PROBE" -b "$bundle" -s "$scene" -o "$DEV_CSV"; then
        done_rows=$((done_rows + 1))
    else
        echo "     probe failed for this row — continuing"
    fi
    echo ""
done < "$PLAN"

# ---------------------------------------------------------------- collect

echo "────────────────────────────────────────────────────────────"
if hdc file recv "$DEV_CSV" "$OUT" >/dev/null 2>&1; then
    echo "probed $done_rows of $rows rows ($skipped skipped) -> $OUT"
    print_summary "$OUT"
else
    die "nothing collected; no rows succeeded"
fi
