#!/usr/bin/env python3
"""
Pi 4 — One-shot LCD Message Utility

Usage:
  python3 lcd_message.py "Line 1" ["Line 2"] [seconds]
  python3 lcd_message.py "Line 1|Line 2" [seconds]

Stops the power-monitor service while displaying, restarts it on exit.
GPIO is cleaned up on all exit paths.
"""

import atexit
import signal
import subprocess
import sys
import time

from RPLCD.gpio import CharLCD
from RPi import GPIO

LCD_WIDTH = 16
MONITOR_SERVICE = "power-monitor.service"


# ---------------------------------------------------------------------------
# LCD
# ---------------------------------------------------------------------------
lcd = CharLCD(
    pin_rs=25,
    pin_e=24,
    pins_data=[23, 17, 18, 22],
    numbering_mode=GPIO.BCM,
    cols=LCD_WIDTH,
    rows=2,
    compat_mode=True,
)
lcd.cursor_mode = "hide"


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
_cleanup_done = False


def cleanup():
    global _cleanup_done
    if _cleanup_done:
        return
    _cleanup_done = True
    try:
        GPIO.cleanup()
    except Exception:
        pass
    # Restart the monitor service
    subprocess.run(
        ["systemctl", "start", MONITOR_SERVICE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    print("Cleaned up. Monitor service restarted.")


atexit.register(cleanup)
signal.signal(signal.SIGINT, lambda s, f: sys.exit(0))
signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # Parse args
    line1 = "Hello"
    line2 = ""
    duration = 10

    args = sys.argv[1:]
    if len(args) >= 1:
        line1 = args[0]
    if len(args) >= 2:
        # Second arg: could be line2 or duration
        try:
            duration = int(args[1])
        except ValueError:
            line2 = args[1]
    if len(args) >= 3:
        try:
            duration = int(args[2])
        except ValueError:
            pass

    # Support pipe separator
    if "|" in line1:
        parts = line1.split("|", 1)
        line1 = parts[0]
        if not line2:
            line2 = parts[1]

    if duration < 1:
        duration = 1

    # Stop the monitor service so we can take the LCD
    subprocess.run(
        ["systemctl", "stop", MONITOR_SERVICE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)

    # Display
    lcd.clear()
    lcd.cursor_pos = (0, 0)
    lcd.write_string(line1[:LCD_WIDTH].ljust(LCD_WIDTH))
    if line2:
        lcd.cursor_pos = (1, 0)
        lcd.write_string(line2[:LCD_WIDTH].ljust(LCD_WIDTH))

    # Countdown on line 2 if no line2 text
    for remaining in range(duration, 0, -1):
        if not line2:
            lcd.cursor_pos = (1, 0)
            lcd.write_string(f"{remaining}s left".ljust(LCD_WIDTH))
        time.sleep(1)


if __name__ == "__main__":
    main()
