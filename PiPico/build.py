#!/usr/bin/env python3
"""
Build script: reads PiPico/config.json and produces PiPico/main_built.py
with all config values hardcoded inline. Upload main_built.py to the Pico
as main.py — no need to also upload config.json.

Usage:
  python3 PiPico/build.py
  # Then upload PiPico/main_built.py to the Pico as main.py
"""

import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(SCRIPT_DIR, "config.json")
SOURCE_PATH = os.path.join(SCRIPT_DIR, "main.py")
OUTPUT_PATH = os.path.join(SCRIPT_DIR, "main_built.py")


def main():
    # Load config
    if not os.path.exists(CONFIG_PATH):
        print(f"ERROR: {CONFIG_PATH} not found.")
        print(f"Copy config.example.json to config.json and fill in your values.")
        sys.exit(1)

    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)

    # Validate required fields
    required = ["wifi_ssid", "wifi_password", "bot_token", "chat_id", "pi4_mac", "pi5_mac"]
    missing = [k for k in required if k not in config or str(config[k]).startswith("YOUR_")]
    if missing:
        print(f"ERROR: These config fields are missing or still have placeholder values:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)

    # Read source
    with open(SOURCE_PATH, "r") as f:
        source = f.read()

    # Replace the config loading block with hardcoded values
    # Find the config section and replace it
    hardcoded_config = f'''
# ---------------------------------------------------------------------------
# Config (hardcoded by build.py — do NOT commit this file)
# ---------------------------------------------------------------------------
config = {{
    "wifi_ssid": {json.dumps(config["wifi_ssid"])},
    "wifi_password": {json.dumps(config["wifi_password"])},
    "bot_token": {json.dumps(config["bot_token"])},
    "chat_id": {json.dumps(str(config["chat_id"]))},
    "pi4_mac": {json.dumps(config["pi4_mac"])},
    "pi5_mac": {json.dumps(config["pi5_mac"])},
    "wol_boot_delay_sec": {json.dumps(config.get("wol_boot_delay_sec", 180))},
}}
'''

    # Replace everything between the Config markers
    start_marker = "# ---------------------------------------------------------------------------\n# Config\n# ---------------------------------------------------------------------------"
    end_marker = "\nWIFI_SSID"

    if start_marker in source:
        before = source.split(start_marker)[0]
        after = end_marker + source.split(end_marker, 1)[1]
        output = before + hardcoded_config + after
    else:
        print("ERROR: Could not find config section markers in main.py")
        sys.exit(1)

    # Remove the load_config function and its call since we don't need it
    # Replace the load_config call with a pass-through
    output = output.replace(
        'def load_config():\n'
        '    try:\n'
        '        with open("config.json", "r") as f:\n'
        '            return ujson.load(f)\n'
        '    except Exception as e:\n'
        '        print(f"FATAL: Cannot load config.json: {e}")\n'  # noqa
        '        # Error pattern: 3 fast blinks, 1 s pause, repeat\n'
        '        while True:\n'
        '            led_blink(3, on_ms=80, off_ms=80)\n'
        '            utime.sleep_ms(1000)\n'
        '\n'
        '\n'
        'config = load_config()',
        '# Config loaded inline by build.py'
    )

    # Write output
    with open(OUTPUT_PATH, "w") as f:
        f.write(output)

    print(f"✅ Built: {OUTPUT_PATH}")
    print(f"   Upload this file to the Pico as 'main.py'")
    print(f"   WiFi SSID: {config['wifi_ssid']}")
    print(f"   Pi4 MAC:   {config['pi4_mac']}")
    print(f"   Pi5 MAC:   {config['pi5_mac']}")
    print(f"   WOL delay: {config.get('wol_boot_delay_sec', 180)}s")


if __name__ == "__main__":
    main()
