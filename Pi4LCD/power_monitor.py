#!/usr/bin/env python3
"""
Pi 4 — Unified LCD Stats & Power Monitor

Normal mode:
  Line 1: CPU Temp: 45.2 C
  Line 2: Up: 3d 12h 5m

Power-loss mode:
  Line 1: PWR LOST! UPS ON
  Line 2: Shut in: 06m30s

Detection:
  1. Ping Pico every PING_INTERVAL seconds
  2. After MAX_FAILED_PINGS consecutive failures → power loss declared
  3. Start SHUTDOWN_COUNTDOWN_MIN countdown
  4. If Pico responds → abort, resume normal
  5. If countdown expires → sync + systemctl poweroff
"""

import atexit
import configparser
import os
import signal
import subprocess
import sys
import time
from RPLCD.gpio import CharLCD
from RPi import GPIO


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config():
    """Load config.ini from the same directory as this script."""
    cfg = configparser.ConfigParser()
    config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.ini")
    if not os.path.exists(config_path):
        print(f"FATAL: {config_path} not found. Copy config.example.ini and fill in values.",
              file=sys.stderr)
        sys.exit(1)
    cfg.read(config_path)
    return cfg


config = load_config()

DEVICE_NAME = config["identity"]["name"]  # "pi4"

# ---------------------------------------------------------------------------
# LCD setup
# ---------------------------------------------------------------------------
LCD_WIDTH = 16
LCD_ROWS = 2

lcd = CharLCD(
    pin_rs=25,
    pin_e=24,
    pins_data=[23, 17, 18, 22],
    numbering_mode=GPIO.BCM,
    cols=LCD_WIDTH,
    rows=LCD_ROWS,
    compat_mode=True,
)
lcd.cursor_mode = "hide"

# ---------------------------------------------------------------------------
# Cleanup — covers SIGINT, SIGTERM, atexit (unhandled exceptions)
# ---------------------------------------------------------------------------
_cleanup_done = False


def cleanup():
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True
    try:
        lcd.clear()
        lcd.write_string("Monitor Offline".ljust(LCD_WIDTH))
    except Exception:
        pass
    try:
        GPIO.cleanup()
    except Exception:
        pass
    print("GPIO cleaned up.")


atexit.register(cleanup)


def _signal_exit(sig, frame):
    """Signal handler that triggers a clean exit."""
    print(f"Received signal {sig}, exiting...")
    sys.exit(0)


signal.signal(signal.SIGINT, _signal_exit)
signal.signal(signal.SIGTERM, _signal_exit)

# ---------------------------------------------------------------------------
# System info
# ---------------------------------------------------------------------------

def get_cpu_temp():
    """Read CPU temp in °C from thermal zone."""
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except Exception:
        return None


def get_uptime_string():
    """Return a compact uptime string like '3d 12h 5m'."""
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
# LCD helpers
# ---------------------------------------------------------------------------
def lcd_write_line(text, row):
    """Write text to a specific LCD row, padded to full width."""
    lcd.cursor_pos = (row, 0)
    lcd.write_string(text[:LCD_WIDTH].ljust(LCD_WIDTH))


# ---------------------------------------------------------------------------
# Ping
# ---------------------------------------------------------------------------
def ping_pico():
    """Ping the Pico W. Returns True if reachable."""
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

    print(f"Pi 4 LCD Power Monitor starting (device: {DEVICE_NAME})")
    lcd.clear()

    last_ping_time = 0
    failed_ping_count = 0
    is_power_lost = False
    power_loss_start = 0

    while True:
        now = time.time()

        # --- Ping check ---
        if now - last_ping_time >= PING_INTERVAL:
            if ping_pico():
                _last_successful_ping = time.time()
                failed_ping_count = 0
                if is_power_lost:
                    is_power_lost = False
                    print("✅ GRID POWER RESTORED: Pi 4 sees Pico W is back online. Shutdown aborted.")
            else:
                if not is_power_lost:
                    failed_ping_count += 1
                    if failed_ping_count >= MAX_FAILED_PINGS:
                        is_power_lost = True
                        power_loss_start = time.time()
            last_ping_time = time.time()

        # --- Display ---
        if not is_power_lost:
            # Normal mode: temp + uptime
            temp = get_cpu_temp()
            if temp is not None:
                lcd_write_line(f"CPU Temp: {temp} C", 0)
            else:
                lcd_write_line("CPU Temp: N/A", 0)

            lcd_write_line(f"Up: {get_uptime_string()}", 1)
            time.sleep(1)

        else:
            # Power-loss mode: countdown
            elapsed = time.time() - power_loss_start
            total = SHUTDOWN_COUNTDOWN_MIN * 60
            remaining = int(total - elapsed)

            if remaining <= 0:
                lcd_write_line("SHUTTING DOWN...", 0)
                lcd_write_line("Protecting NVMe", 1)
                print("🚨 SHUTDOWN INITIATED: Timer elapsed. Shutting down Pi 4 to protect NVMe.")
                subprocess.run(["sync"], check=False)
                subprocess.run(["systemctl", "poweroff"], check=False)
                break
            else:
                mins = remaining // 60
                secs = remaining % 60
                lcd_write_line("PWR LOST! UPS ON", 0)
                lcd_write_line(f"Shut in: {mins:02d}m{secs:02d}s", 1)

            time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fatal error: {e}", file=sys.stderr)
        try:
            lcd.clear()
            lcd_write_line("Error!", 0)
        except Exception:
            pass
        sys.exit(1)
