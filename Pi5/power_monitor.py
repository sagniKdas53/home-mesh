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
  /ping pi5       — reply alive + uptime + last Pico ping
  /status pi5     — same as /ping
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
# Telegram
# ---------------------------------------------------------------------------
def send_telegram(text):
    """Send a Telegram message. Returns message_id on success, None on failure."""
    try:
        resp = requests.post(
            f"{TELEGRAM_URL}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("ok"):
                return data.get("result", {}).get("message_id")
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)
    return None


def get_updates(offset=None):
    try:
        params = {
            "timeout": 5,
            "allowed_updates": ["message", "message_reaction"],
        }
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
_last_successful_ping = None

# Pending reaction-based confirmations: {message_id: {"action": str, "ts": float}}
_pending_confirm = {}
_CONFIRM_TIMEOUT = 120  # seconds


def _execute_shutdown():
    global _shutting_down
    if _shutting_down:
        send_telegram(f"⚠️ {DEVICE_NAME}: Already shutting down.")
        return
    _shutting_down = True
    logger.warning("Shutdown command received via Telegram")
    send_telegram(f"🔌 {DEVICE_NAME}: Shutdown confirmed. Executing...")
    subprocess.run(["sync"], check=False)
    subprocess.run(["systemctl", "poweroff"], check=False)


def _execute_restart():
    global _shutting_down
    if _shutting_down:
        send_telegram(f"⚠️ {DEVICE_NAME}: Already shutting down.")
        return
    _shutting_down = True
    logger.warning("Restart command received via Telegram")
    send_telegram(f"🔄 {DEVICE_NAME}: Restart confirmed. Rebooting...")
    subprocess.run(["systemctl", "reboot"], check=False)


def _send_ping_response():
    temp = get_cpu_temp()
    uptime = get_uptime_string()
    last_ping = format_ago(_last_successful_ping)
    send_telegram(
        f"🏓 {DEVICE_NAME} is alive!\n"
        f"CPU Temp: {temp}°C\n"
        f"Uptime: {uptime}\n"
        f"Last Pico ping: {last_ping}"
    )


def telegram_listener():
    global _shutting_down
    last_update_id = None

    # Flush stale updates on start
    updates = get_updates()
    if updates:
        last_update_id = updates[-1]["update_id"] + 1

    while True:
        try:
            # Expire old confirmations
            now = time.time()
            expired = [mid for mid, e in _pending_confirm.items()
                       if now - e["ts"] > _CONFIRM_TIMEOUT]
            for mid in expired:
                del _pending_confirm[mid]

            updates = get_updates(offset=last_update_id)
            for update in updates:
                uid = update.get("update_id")
                last_update_id = uid + 1

                # --- Handle reactions on confirmation messages ---
                reaction = update.get("message_reaction")
                if reaction:
                    msg_id = reaction.get("message_id")
                    chat = reaction.get("chat", {})
                    cid = str(chat.get("id", ""))
                    if cid == CHAT_ID and msg_id in _pending_confirm:
                        entry = _pending_confirm.pop(msg_id)
                        if time.time() - entry["ts"] <= _CONFIRM_TIMEOUT:
                            if entry["action"] == "shutdown":
                                _execute_shutdown()
                            elif entry["action"] == "restart":
                                _execute_restart()
                    continue

                # --- Handle text commands ---
                msg = update.get("message", {})
                text = (msg.get("text") or "").strip().lower()
                chat = msg.get("chat", {})
                cid = str(chat.get("id", ""))

                if cid != CHAT_ID or not text.startswith("/"):
                    continue

                parts = text.split()
                cmd = parts[0]
                target = parts[1] if len(parts) > 1 else None

                # --- /shutdown and /restart ---
                if cmd in ("/shutdown", "/restart"):
                    action = "shutdown" if cmd == "/shutdown" else "restart"

                    if target == DEVICE_NAME or target == "all":
                        # Explicit target — execute immediately
                        if action == "shutdown":
                            _execute_shutdown()
                        else:
                            _execute_restart()

                    elif target is None:
                        # No target — prompt for confirmation via reaction
                        emoji = "🔌" if action == "shutdown" else "🔄"
                        mid = send_telegram(
                            f"{emoji} {DEVICE_NAME}: React to this message "
                            f"to confirm {action}.\n"
                            f"Usage: /{action} pi4|pi5|all\n"
                            f"Expires in {_CONFIRM_TIMEOUT}s."
                        )
                        if mid:
                            _pending_confirm[mid] = {
                                "action": action, "ts": time.time(),
                            }
                    # else: target is another device, ignore

                # --- /ping and /status ---
                elif cmd in ("/ping", "/status"):
                    if target is None or target in (DEVICE_NAME, "all"):
                        _send_ping_response()

        except Exception as e:
            logger.warning("Telegram listener error: %s", e)

        time.sleep(10)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def main():
    global _last_successful_ping

    logger.info("Headless Power Monitor started (device: %s). Watching %s",
                DEVICE_NAME, PICO_IP)

    # Start Telegram listener thread
    t = threading.Thread(target=telegram_listener, daemon=True)
    t.start()

    # Announce startup
    send_telegram(
        f"🟢 {DEVICE_NAME} power monitor is online.\n"
        f"Watching Pico at {PICO_IP}"
    )

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
                _last_successful_ping = time.time()
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
