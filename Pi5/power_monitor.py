#!/usr/bin/env python3
"""
Pi 5 — Headless Power Monitor + Telegram Command Listener

Detection:
  1. Ping Pico every PING_INTERVAL seconds
  2. After MAX_FAILED_PINGS consecutive failures → power loss declared
  3. Start SHUTDOWN_COUNTDOWN_MIN countdown
  4. If Pico responds → abort, resume monitoring
  5. If countdown expires → sync + systemctl poweroff

All events logged to syslog. Telegram alerts on key events.

Telegram commands this device handles:
  /shutdown pi5   — shutdown this Pi
  /shutdown all   — shutdown this Pi
  /restart pi5    — reboot this Pi
  /restart all    — reboot this Pi
  /ping pi5       — reply alive + uptime
"""

import configparser
import logging
import logging.handlers
import os
import signal
import subprocess
import sys
import threading
import time

import requests

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

BOT_TOKEN = config["telegram"]["bot_token"]
CHAT_ID = config["telegram"]["chat_id"]
PICO_IP = config["network"]["pico_ip"]
PING_INTERVAL = int(config["power"]["ping_interval_sec"])
MAX_FAILED_PINGS = int(config["power"]["max_failed_pings"])
SHUTDOWN_COUNTDOWN_MIN = int(config["power"]["shutdown_countdown_min"])
PING_TIMEOUT = int(config["power"]["ping_timeout_sec"])
DEVICE_NAME = config["identity"]["name"]  # "pi5"

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


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
# Telegram
# ---------------------------------------------------------------------------
def send_telegram(text):
    try:
        requests.post(
            f"{TELEGRAM_URL}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)


def get_updates(offset=None):
    try:
        params = {"timeout": 5}
        if offset is not None:
            params["offset"] = offset
        resp = requests.post(
            f"{TELEGRAM_URL}/getUpdates",
            json=params,
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json().get("result", [])
    except Exception as e:
        logger.warning("Telegram getUpdates failed: %s", e)
    return []


# ---------------------------------------------------------------------------
# Telegram command listener (background thread)
# ---------------------------------------------------------------------------
_shutting_down = False


def telegram_listener():
    global _shutting_down
    last_update_id = None

    # Flush stale updates on start
    updates = get_updates()
    if updates:
        last_update_id = updates[-1]["update_id"] + 1

    while True:
        try:
            updates = get_updates(offset=last_update_id)
            for update in updates:
                uid = update.get("update_id")
                last_update_id = uid + 1

                msg = update.get("message", {})
                text = (msg.get("text") or "").strip().lower()
                chat = msg.get("chat", {})
                cid = str(chat.get("id", ""))

                if cid != CHAT_ID:
                    continue

                if not text.startswith("/"):
                    continue

                if DEVICE_NAME not in text and "all" not in text:
                    continue

                if text.startswith("/shutdown"):
                    if _shutting_down:
                        send_telegram(f"⚠️ {DEVICE_NAME}: Already shutting down.")
                        continue
                    _shutting_down = True
                    logger.warning("Shutdown command received via Telegram")
                    send_telegram(f"🔌 {DEVICE_NAME}: Shutdown command received. Executing...")
                    subprocess.run(["sync"], check=False)
                    subprocess.run(["systemctl", "poweroff"], check=False)

                elif text.startswith("/restart"):
                    if _shutting_down:
                        send_telegram(f"⚠️ {DEVICE_NAME}: Already shutting down.")
                        continue
                    _shutting_down = True
                    logger.warning("Restart command received via Telegram")
                    send_telegram(f"🔄 {DEVICE_NAME}: Restart command received. Rebooting...")
                    subprocess.run(["systemctl", "reboot"], check=False)

                elif text.startswith("/ping"):
                    temp = get_cpu_temp()
                    uptime = get_uptime_string()
                    send_telegram(
                        f"🏓 {DEVICE_NAME} is alive!\n"
                        f"CPU Temp: {temp}°C\n"
                        f"Uptime: {uptime}"
                    )

        except Exception as e:
            logger.warning("Telegram listener error: %s", e)

        time.sleep(10)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    logger.info("Headless Power Monitor started (device: %s). Watching %s",
                DEVICE_NAME, PICO_IP)

    # Start Telegram listener thread
    t = threading.Thread(target=telegram_listener, daemon=True)
    t.start()

    last_ping_time = 0
    failed_ping_count = 0
    is_power_lost = False
    power_loss_start = 0
    alert_sent = False
    log_throttle = 0

    global _shutting_down

    while True:
        now = time.time()

        # --- Ping check ---
        if now - last_ping_time >= PING_INTERVAL:
            if ping_pico():
                failed_ping_count = 0
                if is_power_lost:
                    is_power_lost = False
                    alert_sent = False
                    log_throttle = 0
                    logger.info("Power restored. Ping successful. Shutdown aborted.")
                    send_telegram(
                        "✅ GRID POWER RESTORED: Pi 5 sees Pico W is back online. "
                        "Shutdown aborted."
                    )
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

            if not alert_sent:
                send_telegram(
                    f"⚠️ GRID POWER LOST! Pico W ({PICO_IP}) unresponsive. "
                    f"UPS active. Pi 5 commencing {SHUTDOWN_COUNTDOWN_MIN}-minute "
                    f"shutdown countdown."
                )
                alert_sent = True

            if remaining <= 0:
                logger.critical("SHUTDOWN INITIATED: Timer elapsed.")
                send_telegram(
                    "🚨 SHUTDOWN INITIATED: Timer elapsed. "
                    "Shutting down Pi 5 to protect NVMe."
                )
                _shutting_down = True
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
