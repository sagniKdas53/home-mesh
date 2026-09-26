#!/usr/bin/env python3
"""
Home Mesh — Power Monitor (shared by Pi 4 and Pi 5)

Usage:
  power_monitor.py /path/to/config.ini

Detection:
  1. GET http://<pico_ip>/status every PING_INTERVAL seconds
  2. After MAX_FAILED_PINGS consecutive failures -> power loss declared
  3. Start SHUTDOWN_COUNTDOWN_MIN countdown
  4. If the Pico responds again -> abort, resume monitoring
  5. If the countdown expires -> sync + systemctl poweroff

Set POWER_MONITOR_DRY_RUN=1 to log the shutdown instead of running it
(use this to test on a machine you don't want to power off).

All events logged to syslog.
"""

import configparser
import json
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------------------
# Logging — syslog + console
# ---------------------------------------------------------------------------
logger = logging.getLogger("PowerMonitor")
logger.setLevel(logging.INFO)

syslog_handler = logging.handlers.SysLogHandler(address="/dev/log")
syslog_handler.setFormatter(
    logging.Formatter("PowerMonitor[%(process)d]: %(message)s")
)
logger.addHandler(syslog_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
logger.addHandler(console_handler)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config():
    if len(sys.argv) != 2:
        logger.critical("Usage: power_monitor.py /path/to/config.ini")
        sys.exit(1)

    config_path = sys.argv[1]
    if not os.path.exists(config_path):
        logger.critical("config.ini not found at %s", config_path)
        sys.exit(1)

    cfg = configparser.ConfigParser()
    cfg.read(config_path)
    return cfg


config = load_config()

DEVICE_NAME = config["identity"]["name"]
PICO_IP = config["network"]["pico_ip"]
PING_INTERVAL = config.getint("power", "ping_interval_sec")
MAX_FAILED_PINGS = config.getint("power", "max_failed_pings")
SHUTDOWN_COUNTDOWN_SEC = config.getint("power", "shutdown_countdown_sec")
PING_TIMEOUT = config.getint("power", "ping_timeout_sec")

STATUS_URL = f"http://{PICO_IP}/status"
DRY_RUN = os.environ.get("POWER_MONITOR_DRY_RUN") == "1"


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------
def _signal_exit(sig, frame):
    logger.info("Received signal %s, exiting cleanly.", sig)
    sys.exit(0)


signal.signal(signal.SIGINT, _signal_exit)
signal.signal(signal.SIGTERM, _signal_exit)


# ---------------------------------------------------------------------------
# Pico status query
# ---------------------------------------------------------------------------
def query_pico():
    """Return the Pico's /status JSON dict, or None if unreachable."""
    try:
        with urllib.request.urlopen(STATUS_URL, timeout=PING_TIMEOUT) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    logger.info(
        "Power Monitor started (device: %s, dry_run=%s). Watching %s",
        DEVICE_NAME, DRY_RUN, STATUS_URL,
    )

    last_check_time = 0
    failed_count = 0
    is_power_lost = False
    power_loss_start = 0
    log_throttle = 0

    while True:
        now = time.time()

        # --- Status check ---
        if now - last_check_time >= PING_INTERVAL:
            status = query_pico()
            if status is not None:
                failed_count = 0
                if is_power_lost:
                    is_power_lost = False
                    log_throttle = 0
                    logger.info(
                        "Power restored. Pico status: %s. Shutdown aborted.", status
                    )
            else:
                if not is_power_lost:
                    failed_count += 1
                    if failed_count >= MAX_FAILED_PINGS:
                        is_power_lost = True
                        power_loss_start = time.time()
                        logger.warning(
                            "Status check failed %d times. Power loss assumed. "
                            "Starting %ds countdown.",
                            MAX_FAILED_PINGS, SHUTDOWN_COUNTDOWN_SEC,
                        )

            last_check_time = time.time()

        # --- Power loss handling ---
        if is_power_lost:
            elapsed = time.time() - power_loss_start
            remaining = int(SHUTDOWN_COUNTDOWN_SEC - elapsed)

            if remaining <= 0:
                logger.critical("SHUTDOWN INITIATED: Timer elapsed.")
                if DRY_RUN:
                    logger.critical("[DRY RUN] Would run: sync && systemctl poweroff")
                else:
                    subprocess.run(["sync"], check=False)
                    subprocess.run(["systemctl", "poweroff"], check=False)
                break
            else:
                elapsed_int = int(elapsed)
                if elapsed_int % 15 == 0 and elapsed_int != log_throttle:
                    logger.warning(
                        "Power still lost. Shutting down in %ds.", remaining,
                    )
                    log_throttle = elapsed_int

        time.sleep(1)

    logger.info("Monitor exited cleanly.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical("Fatal error: %s", e)
        sys.exit(1)
