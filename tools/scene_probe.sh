#!/bin/sh
# scene_probe.sh — on-device coverage probe.
#
# Answers one question per (app, scene): how much of this window does ArkUI
# actually describe? Run it before committing to any instrumentation work.
#
#   ./scene_probe.sh                      # whatever is in the foreground
#   ./scene_probe.sh -b com.example.app   # a named bundle
#   ./scene_probe.sh -w 28                # a known window id
#   ./scene_probe.sh -l                   # list windows and exit
#   ./scene_probe.sh -s "player" -o cov.csv   # label the scene, append a row
#
# Runs in the device shell (hdc shell). POSIX sh plus awk/grep only — no bash,
# no python.
#
# ---------------------------------------------------------------------------
# VERIFY THE PARSE ONCE BEFORE TRUSTING IT.
# The hidumper element-dump format varies across builds. Run with -k on an app
# you know is ArkUI-native (Settings is a good one) and check that TOP TAGS
# lists real component names. If it lists garbage, the tag extraction in
# tally_tags() needs adjusting to your build's format — everything else in the
# script is format-independent.
# ---------------------------------------------------------------------------

set -u

BUNDLE=""
WINID=""
SCENE="-"
OUTCSV=""
KEEP=0
LISTONLY=0

# First writable scratch directory wins. Device shells differ on what exists.
TMPDIR_PICK=""
for d in "${TMPDIR:-}" /data/local/tmp /data/service/el1/public/temp /tmp .; do
    [ -n "$d" ] && [ -d "$d" ] && [ -w "$d" ] && { TMPDIR_PICK="$d"; break; }
done
[ -n "$TMPDIR_PICK" ] || { echo "error: no writable scratch directory" >&2; exit 1; }

TMP="$TMPDIR_PICK/scene_probe.$$"
RAW="$TMP.dump"
WMS="$TMP.wms"

cleanup() { [ "$KEEP" -eq 1 ] || rm -f "$RAW" "$WMS" 2>/dev/null; }
trap cleanup EXIT INT TERM

usage() {
    cat <<'USAGE'
scene_probe.sh [-b bundle] [-w window_id] [-s scene_label] [-o out.csv] [-l] [-k]
  -b  bundle name (default: the foreground window)
  -w  window id, skips discovery
  -s  scene label recorded in the output row (e.g. "feed", "player")
  -o  append a CSV row to this file (header written if new)
  -l  list windows and exit
  -k  keep the raw dumps for inspection
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        -b) BUNDLE="${2:-}"; shift 2 ;;
        -w) WINID="${2:-}"; shift 2 ;;
        -s) SCENE="${2:-}"; shift 2 ;;
        -o) OUTCSV="${2:-}"; shift 2 ;;
        -l) LISTONLY=1; shift ;;
        -k) KEEP=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage; exit 2 ;;
    esac
done

# --- 0. ArkUI debug dump path ------------------------------------------------
DBG=$(param get persist.ace.debug.enabled 2>/dev/null || echo "")
case "$DBG" in
    1|true) ;;
    *)
        echo "note: persist.ace.debug.enabled is not set."
        echo "      run:  param set persist.ace.debug.enabled 1"
        echo "      then RESTART the target app — the dump path is wired at"
        echo "      app start, so a running app will not pick it up."
        echo ""
        ;;
esac

# --- 1. windows --------------------------------------------------------------
hidumper -s WindowManagerService -a '-a' > "$WMS" 2>/dev/null

if [ ! -s "$WMS" ]; then
    echo "error: WindowManagerService dump was empty. Need shell privilege." >&2
    exit 1
fi

if [ "$LISTONLY" -eq 1 ]; then
    cat "$WMS"
    exit 0
fi

# Window id discovery. Formats differ between builds, so this is deliberately
# loose: find the candidate line, then take its first standalone integer.
if [ -z "$WINID" ]; then
    if [ -n "$BUNDLE" ]; then
        CAND=$(grep -i -- "$BUNDLE" "$WMS" | head -n 1)
    else
        # Foreground: prefer a line that advertises focus, else the last
        # visible/shown window listed.
        CAND=$(grep -iE 'focus|foreground' "$WMS" | grep -viE 'false|0 *$' | head -n 1)
        [ -n "$CAND" ] || CAND=$(grep -iE 'visible|shown' "$WMS" | tail -n 1)
    fi
    WINID=$(printf '%s\n' "$CAND" | awk '{for(i=1;i<=NF;i++) if($i ~ /^[0-9]+$/){print $i; exit}}')
fi

if [ -z "$WINID" ]; then
    echo "error: could not determine a window id." >&2
    echo "       run with -l, find the id by eye, and pass it with -w." >&2
    exit 1
fi

[ -n "$BUNDLE" ] || BUNDLE=$(grep -oE '[a-z][a-z0-9_]+(\.[a-z0-9_]+){2,}' "$WMS" | head -n 1)
[ -n "$BUNDLE" ] || BUNDLE="unknown"

# --- 2. element tree ---------------------------------------------------------
hidumper -s WindowManagerService -a "-w $WINID -element -c" > "$RAW" 2>/dev/null

if [ ! -s "$RAW" ]; then
    echo "error: element dump for window $WINID was empty." >&2
    echo "       most likely the debug param is unset, or the app was not" >&2
    echo "       restarted after setting it." >&2
    exit 1
fi

# --- 3. tally ----------------------------------------------------------------
# First alphabetic token per line, which is the component tag in the formats
# seen so far. Format-dependent: this is the part to check with -k.
tally_tags() {
    sed -E 's/^[^A-Za-z]*//' "$RAW" \
      | awk '{ t=$1; gsub(/[^A-Za-z_]/,"",t); if (length(t)>1) print t }' \
      | sort | uniq -c | sort -rn
}

count_tag() {  # exact tag, word-boundary-ish
    tally_tags | awk -v want="$1" '$2==want { s+=$1 } END { print s+0 }'
}

TOTAL=$(tally_tags | awk '{ s+=$1 } END { print s+0 }')
N_WEB=$(count_tag Web)
N_XC=$(count_tag XComponent)
N_TEXT=$(count_tag Text)
N_IMG=$(count_tag Image)
N_LIST=$(count_tag List)
N_GRID=$(count_tag Grid)
N_SWIPER=$(count_tag Swiper)
N_SCROLL=$(count_tag Scroll)
N_INPUT=$(count_tag TextInput)
N_VIDEO=$(count_tag Video)
N_SCROLLER=$((N_LIST + N_GRID + N_SWIPER + N_SCROLL))
N_OPAQUE=$((N_WEB + N_XC))

# --- 4. independent check: what the process actually loaded ------------------
# Format-independent, so it corroborates the dump rather than depending on it.
PID=$(ps -ef 2>/dev/null | awk -v b="$BUNDLE" '$0 ~ b && $0 !~ /awk/ { print $2; exit }')
ENGINE="-"
if [ -n "${PID:-}" ] && [ -r "/proc/$PID/maps" ]; then
    E=$(grep -oiE 'lib(flutter|app|unity|il2cpp|cocos|weex|hummer)[a-z0-9_]*\.so' \
            "/proc/$PID/maps" 2>/dev/null | sort -u | tr '\n' ' ')
    [ -n "$E" ] && ENGINE="$E"
fi

# --- 5. verdict --------------------------------------------------------------
# Thresholds are starting guesses. Calibrate them against one app you know is
# ArkUI-native and one you know is engine-rendered.
if [ "$TOTAL" -lt 15 ] && [ "$N_OPAQUE" -ge 1 ]; then
    VERDICT="BLIND"
    NOTE="surface-rendered; the tree sees nothing"
elif [ "$N_OPAQUE" -ge 1 ] && [ "$TOTAL" -lt 60 ]; then
    VERDICT="THIN"
    NOTE="native shell around a surface; outside attributes only"
elif [ "$N_WEB" -ge 1 ]; then
    VERDICT="MIXED"
    NOTE="needs the ArkWeb record to be complete"
elif [ "$N_XC" -ge 1 ]; then
    VERDICT="MIXED"
    NOTE="XComponent present; describable from outside only"
elif [ "$TOTAL" -ge 60 ]; then
    VERDICT="ARKUI"
    NOTE="full coverage"
else
    VERDICT="SPARSE"
    NOTE="few elements and no opaque node; check the parse with -k"
fi
[ "$ENGINE" = "-" ] || NOTE="$NOTE; engine libs loaded"

# --- 6. report ---------------------------------------------------------------
echo "bundle    : $BUNDLE"
echo "window    : $WINID        scene: $SCENE"
echo "elements  : $TOTAL"
echo "opaque    : $N_OPAQUE   (Web $N_WEB, XComponent $N_XC)"
echo "content   : Text $N_TEXT, Image $N_IMG, Video $N_VIDEO, scrollers $N_SCROLLER, inputs $N_INPUT"
echo "engine so : $ENGINE"
echo "verdict   : $VERDICT   ($NOTE)"
echo ""
echo "TOP TAGS (sanity-check the parse — these should be component names):"
tally_tags | head -n 12 | awk '{ printf "  %6s  %s\n", $1, $2 }'

if [ -n "$OUTCSV" ]; then
    [ -f "$OUTCSV" ] || echo "ts,bundle,scene,window,total,opaque,web,xcomponent,text,image,video,scrollers,inputs,engine,verdict" > "$OUTCSV"
    echo "$(date +%s),$BUNDLE,$SCENE,$WINID,$TOTAL,$N_OPAQUE,$N_WEB,$N_XC,$N_TEXT,$N_IMG,$N_VIDEO,$N_SCROLLER,$N_INPUT,\"$ENGINE\",$VERDICT" >> "$OUTCSV"
    echo ""
    echo "row appended -> $OUTCSV"
fi

[ "$KEEP" -eq 1 ] && echo "raw dumps kept: $RAW  $WMS"
exit 0
