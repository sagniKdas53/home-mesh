#!/usr/bin/env python3
"""
Pi 4 — Standalone LCD Display (Dockerized)

Reads CPU Temp and Uptime, outputs to RPLCD.
Update frequency configurable via DISPLAY_UPDATE_INTERVAL_SEC (default 1.0).
"""

import atexit
import os
import signal
import sys
import time

from RPLCD.gpio import CharLCD
from RPi import GPIO

# Configuration
UPDATE_INTERVAL = float(os.environ.get("DISPLAY_UPDATE_INTERVAL_SEC", "1.0"))
LCD_WIDTH = 16
LCD_ROWS = 2

# LCD setup
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

_cleanup_done = False


def cleanup():
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True
    try:
        lcd.clear()
        lcd.write_string("Display Offline".ljust(LCD_WIDTH))
    except Exception:
        pass
    try:
        GPIO.cleanup()
    except Exception:
        pass
    print("GPIO cleaned up.")


atexit.register(cleanup)


def _signal_exit(sig, frame):
    print(f"Received signal {sig}, exiting...")
    sys.exit(0)


signal.signal(signal.SIGINT, _signal_exit)
signal.signal(signal.SIGTERM, _signal_exit)


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


def lcd_write_line(text, row):
    lcd.cursor_pos = (row, 0)
    lcd.write_string(text[:LCD_WIDTH].ljust(LCD_WIDTH))


def main():
    print(f"Starting Pi 4 LCD Display (Interval: {UPDATE_INTERVAL}s)")
    lcd.clear()

    while True:
        temp = get_cpu_temp()
        if temp is not None:
            lcd_write_line(f"CPU Temp: {temp} C", 0)
        else:
            lcd_write_line("CPU Temp: N/A", 0)

        lcd_write_line(f"Up: {get_uptime_string()}", 1)
        time.sleep(UPDATE_INTERVAL)


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
