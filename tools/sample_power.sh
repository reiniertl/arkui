#!/bin/sh
# sample_power.sh — minimal CPU frequency / idle residency sampler.
#
# Writes CSV on stdout with a CLOCK_MONOTONIC timestamp per row. The clock
# MUST match the one the collector stamps descriptors with, or the join in
# correlate.py is meaningless.
#
#   ./sample_power.sh 50 > power.csv       # 50ms period
#
# Standard-system OpenHarmony is Linux, so the usual cpufreq/cpuidle sysfs
# applies. Paths for the GPU and for any power rail are board-specific:
# set GPU_FREQ_PATH and POWER_UW_PATH for your hardware.
#
# On the fuel gauge: an on-device gauge typically samples around 1Hz and is
# noisy. It is adequate for a long steady-state soak and inadequate for
# attributing power to scene transitions. For transition work, put a shunt
# and a DAQ on the SoC rail and feed that series in as POWER_UW_PATH or
# merge it offline.

set -eu

PERIOD_MS="${1:-100}"
GPU_FREQ_PATH="${GPU_FREQ_PATH:-}"
POWER_UW_PATH="${POWER_UW_PATH:-}"

CPUS=$(ls -d /sys/devices/system/cpu/cpu[0-9]* 2>/dev/null | sort -V)

# Header
printf 't_ns'
for c in $CPUS; do printf ',%s_khz' "$(basename "$c")"; done
for c in $CPUS; do printf ',%s_idle_us' "$(basename "$c")"; done
[ -n "$GPU_FREQ_PATH" ] && printf ',gpu_khz'
[ -n "$POWER_UW_PATH" ] && printf ',power_uw'
printf '\n'

read_or_zero() { [ -r "$1" ] && cat "$1" 2>/dev/null || echo 0; }

# Sum of all cpuidle state residencies for one cpu, in microseconds.
idle_us_for() {
    _total=0
    for s in "$1"/cpuidle/state*/time; do
        [ -r "$s" ] || continue
        _v=$(cat "$s" 2>/dev/null || echo 0)
        _total=$((_total + _v))
    done
    echo "$_total"
}

while :; do
    T=$(date +%s%N)
    printf '%s' "$T"
    for c in $CPUS; do
        printf ',%s' "$(read_or_zero "$c/cpufreq/scaling_cur_freq")"
    done
    for c in $CPUS; do
        printf ',%s' "$(idle_us_for "$c")"
    done
    [ -n "$GPU_FREQ_PATH" ] && printf ',%s' "$(read_or_zero "$GPU_FREQ_PATH")"
    [ -n "$POWER_UW_PATH" ] && printf ',%s' "$(read_or_zero "$POWER_UW_PATH")"
    printf '\n'

    # Busy-free sleep; BusyBox sleep takes fractional seconds.
    sleep "$(awk -v ms="$PERIOD_MS" 'BEGIN{printf "%.3f", ms/1000}')"
done
