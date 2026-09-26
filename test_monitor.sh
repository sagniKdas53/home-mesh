#!/usr/bin/env bash
# Manual test harness for power_monitor.py.
#
# Watches the real Pico (test_config.ini, fast intervals) and prints the
# same detection/countdown/"shutdown" log lines Pi4/Pi5 would produce for
# real - but this ALWAYS runs in dry-run mode, so it will never power off
# this machine no matter what environment variables are already set.
#
# Usage:
#   ./test_monitor.sh
#   (then unplug the Pico and watch; Ctrl-C to stop)

set -euo pipefail
cd "$(dirname "$0")"

env -u POWER_MONITOR_DRY_RUN POWER_MONITOR_DRY_RUN=1 \
    python3 power_monitor.py test_config.ini
