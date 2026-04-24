#!/usr/bin/env python3
"""
Pi 5 — Headless Power Monitor

Detection:
  1. Ping Pico every PING_INTERVAL seconds
  2. After MAX_FAILED_PINGS consecutive failures → power loss declared
  3. Start SHUTDOWN_COUNTDOWN_MIN countdown
  4. If Pico responds → abort, resume monitoring
  5. If countdown expires → sync + systemctl poweroff

All events logged to syslog.
"""

import configparser
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import time

# ---------------------------------------------------------------------------
# Logging — syslog + console
# ---------------------------------------------------------------------------
logger = logging.getLogger("Pi5_PowerMonitor")
logger.setLevel(logging.INFO)

syslog_handler = logging.handlers.SysLogHandler(address="/dev/log")
syslog_handler.setFormatter(
    logging.Formatter("Pi5_PowerMonitor[%(process)d]: %(message)s")
)
logger.addHandler(syslog_handler)

console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
logger.addHandler(console_handler)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config():
    cfg = configparser.ConfigParser()
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
    if not os.path.exists(config_path):
        logger.critical("config.ini not found at %s", config_path)
        sys.exit(1)
    cfg.read(config_path)
    return cfg


config = load_config()

DEVICE_NAME = config["identity"]["name"]  # "pi5"


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------
def _signal_exit(sig, frame):
    logger.info("Received signal %s, exiting cleanly.", sig)
    sys.exit(0)


signal.signal(signal.SIGINT, _signal_exit)
signal.signal(signal.SIGTERM, _signal_exit)


# ---------------------------------------------------------------------------
# System info
# ---------------------------------------------------------------------------
def get_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        return None


def get_uptime_string():
    try:
        with open("/proc/uptime", "r") as f:
            uptime_sec = int(float(f.read().split()[0]))
    except Exception:
        return "Unknown"

    days = uptime_sec // 86400
    hours = (uptime_sec % 86400) // 3600
    minutes = (uptime_sec % 3600) // 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def format_ago(ts):
    """Format a timestamp as a human-readable 'X ago' string."""
    if ts is None:
        return "Never"
    delta = int(time.time() - ts)
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m {delta % 60}s ago"
    hours = delta // 3600
    mins = (delta % 3600) // 60
    return f"{hours}h {mins}m ago"


# ---------------------------------------------------------------------------
# Ping
# ---------------------------------------------------------------------------
def ping_pico():
    try:
        result = subprocess.run(
            ["ping", "-c", "1", "-W", str(PING_TIMEOUT), PICO_IP],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return result.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    global _last_successful_ping

    logger.info("Headless Power Monitor started (device: %s). Watching %s",
                DEVICE_NAME, PICO_IP)

    last_ping_time = 0
    failed_ping_count = 0
    is_power_lost = False
    power_loss_start = 0
    log_throttle = 0

    global _shutting_down

    while True:
        now = time.time()

        # --- Ping check ---
        if now - last_ping_time >= PING_INTERVAL:
            if ping_pico():
                _last_successful_ping = time.time()
                failed_ping_count = 0
                if is_power_lost:
                    is_power_lost = False
                    log_throttle = 0
                    logger.info("Power restored. Ping successful. Shutdown aborted.")
            else:
                if not is_power_lost:
                    failed_ping_count += 1
                    if failed_ping_count >= MAX_FAILED_PINGS:
                        is_power_lost = True
                        power_loss_start = time.time()
                        logger.warning(
                            "Ping failed %d times. Power loss assumed. "
                            "Starting %d-minute countdown.",
                            MAX_FAILED_PINGS, SHUTDOWN_COUNTDOWN_MIN,
                        )

            last_ping_time = time.time()

        # --- Power loss handling ---
        if is_power_lost:
            elapsed = time.time() - power_loss_start
            total = SHUTDOWN_COUNTDOWN_MIN * 60
            remaining = int(total - elapsed)

            if remaining <= 0:
                logger.critical("SHUTDOWN INITIATED: Timer elapsed.")
                subprocess.run(["sync"], check=False)
                subprocess.run(["systemctl", "poweroff"], check=False)
                break
            else:
                elapsed_int = int(elapsed)
                if elapsed_int % 300 == 0 and elapsed_int != log_throttle:
                    logger.warning(
                        "Power still lost. Shutting down in %d minutes.",
                        remaining // 60,
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
