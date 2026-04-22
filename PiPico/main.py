"""
Pi Pico W — Telegram Bot + WOL Sender + Ping Target

Boot sequence:
  1. Load config.json
  2. Connect WiFi (retry forever)
  3. Sync NTP time
  4. Wait stabilization delay (default 3 min) to avoid power-flicker WOL
  5. Send WOL to Pi 4 + Pi 5
  6. Notify Telegram: POWER RESTORED
  7. Flush stale Telegram updates
  8. Enable hardware watchdog
  9. Main loop: poll Telegram commands

Telegram commands handled by Pico:
  /status   — WiFi RSSI, uptime, free memory
  /uptime   — Pico uptime
  /wol pi4  — WOL magic packet to Pi 4
  /wol pi5  — WOL magic packet to Pi 5
  /wol all  — WOL both
  /help     — List all commands
"""

import network
import urequests
import ujson
import usocket
import ustruct
import utime
import machine
import gc

# ---------------------------------------------------------------------------
# LED helper
# ---------------------------------------------------------------------------
led = machine.Pin("LED", machine.Pin.OUT)


def led_solid(on=True):
    led.value(1 if on else 0)


def led_blink(times, on_ms=100, off_ms=100):
    for _ in range(times):
        led.value(1)
        utime.sleep_ms(on_ms)
        led.value(0)
        utime.sleep_ms(off_ms)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config():
    try:
        with open("config.json", "r") as f:
            return ujson.load(f)
    except Exception as e:
        print(f"FATAL: Cannot load config.json: {e}")
        # Error pattern: 3 fast blinks, 1 s pause, repeat
        while True:
            led_blink(3, on_ms=80, off_ms=80)
            utime.sleep_ms(1000)


config = load_config()

WIFI_SSID = config["wifi_ssid"]
WIFI_PASSWORD = config["wifi_password"]
BOT_TOKEN = config["bot_token"]
CHAT_ID = str(config["chat_id"])
PI4_MAC = config["pi4_mac"]
PI5_MAC = config["pi5_mac"]
WOL_BOOT_DELAY = config.get("wol_boot_delay_sec", 180)
DEBUG_MODE = config.get("debug_mode", False)

if DEBUG_MODE:
    WOL_BOOT_DELAY = 10  # Reduced delay for debugging
    print(f"[DEBUG] Debug mode ON — boot delay reduced to {WOL_BOOT_DELAY}s")

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

print(f"[DEBUG] Config loaded:")
print(f"  SSID: {WIFI_SSID}")
print(f"  CHAT_ID: '{CHAT_ID}' (type={type(CHAT_ID)})")
print(f"  BOT_TOKEN: {BOT_TOKEN[:10]}...{BOT_TOKEN[-5:]}")
print(f"  TELEGRAM_URL: {TELEGRAM_URL[:40]}...")
print(f"  WOL_BOOT_DELAY: {WOL_BOOT_DELAY}s")

boot_time = utime.time()
wlan = network.WLAN(network.STA_IF)

# ---------------------------------------------------------------------------
# WiFi
# ---------------------------------------------------------------------------
def connect_wifi(timeout_s=30):
    """Attempt WiFi connection. Returns True on success."""
    wlan.active(True)
    if wlan.isconnected():
        return True

    print(f"Connecting to WiFi '{WIFI_SSID}'...")
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    deadline = utime.time() + timeout_s
    while not wlan.isconnected() and utime.time() < deadline:
        led_blink(1, on_ms=250, off_ms=250)  # slow blink = connecting

    if wlan.isconnected():
        print(f"WiFi connected: {wlan.ifconfig()}")
        led_solid(True)
        return True
    else:
        print("WiFi connection timed out")
        led_solid(False)
        return False


def ensure_wifi():
    """Block until WiFi is connected, retrying every 15 s."""
    while not wlan.isconnected():
        led_blink(3, on_ms=60, off_ms=60)  # fast blink = disconnected
        if not connect_wifi(timeout_s=30):
            print("WiFi retry in 15 s...")
            utime.sleep(15)


# ---------------------------------------------------------------------------
# NTP
# ---------------------------------------------------------------------------
def sync_ntp():
    try:
        import ntptime
        ntptime.settime()
        print("NTP time synced")
    except Exception as e:
        print(f"NTP sync failed (non-fatal): {e}")


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------
def send_telegram(text):
    """Send a message to the configured Telegram chat."""
    if not wlan.isconnected():
        print("[TG] WiFi down — skipping Telegram send")
        return False
    try:
        url = f"{TELEGRAM_URL}/sendMessage"
        payload_dict = {"chat_id": CHAT_ID, "text": text}
        payload = ujson.dumps(payload_dict)
        print(f"[TG] Sending to {url}")
        print(f"[TG] Payload: chat_id='{CHAT_ID}', text='{text[:60]}...'" if len(text) > 60 else f"[TG] Payload: chat_id='{CHAT_ID}', text='{text}'")
        resp = urequests.post(url, data=payload,
                              headers={"Content-Type": "application/json"})
        ok = resp.status_code == 200
        if not ok:
            # Read the response body to see what Telegram says
            try:
                body = resp.text
            except:
                body = "(could not read body)"
            print(f"[TG] ERROR {resp.status_code}: {body}")
        else:
            print(f"[TG] Message sent OK")
        resp.close()
        return ok
    except Exception as e:
        print(f"[TG] Send EXCEPTION: {e}")
        return False


def get_updates(offset=None):
    """Poll Telegram for new messages."""
    if not wlan.isconnected():
        return []
    try:
        params = {"timeout": 5}
        if offset is not None:
            params["offset"] = offset
        url = f"{TELEGRAM_URL}/getUpdates"
        resp = urequests.post(url, data=ujson.dumps(params),
                              headers={"Content-Type": "application/json"})
        if resp.status_code == 200:
            data = resp.json()
            resp.close()
            return data.get("result", [])
        else:
            try:
                body = resp.text
            except:
                body = "(could not read body)"
            print(f"[TG] getUpdates ERROR {resp.status_code}: {body}")
        resp.close()
    except Exception as e:
        print(f"[TG] getUpdates EXCEPTION: {e}")
    return []


def flush_updates():
    """Discard any stale updates from before this boot."""
    print("Flushing stale Telegram updates...")
    updates = get_updates(offset=None)
    if updates:
        last_id = updates[-1]["update_id"]
        # Acknowledge all by requesting offset = last + 1
        get_updates(offset=last_id + 1)
        print(f"  Flushed {len(updates)} stale updates")
    else:
        print("  No stale updates")


# ---------------------------------------------------------------------------
# WOL
# ---------------------------------------------------------------------------
def _mac_to_bytes(mac_str):
    """Convert 'AA:BB:CC:DD:EE:FF' to bytes."""
    return bytes(int(b, 16) for b in mac_str.split(":"))


def send_wol(mac_str):
    """Send a Wake-on-LAN magic packet (3 times for reliability)."""
    mac_bytes = _mac_to_bytes(mac_str)
    magic = b"\xff" * 6 + mac_bytes * 16
    for i in range(3):
        try:
            sock = usocket.socket(usocket.AF_INET, usocket.SOCK_DGRAM)
            sock.setsockopt(usocket.SOL_SOCKET, usocket.SO_BROADCAST, 1)
            sock.sendto(magic, ("255.255.255.255", 9))
            sock.close()
        except Exception as e:
            print(f"WOL send #{i+1} failed: {e}")
        if i < 2:
            utime.sleep(1)
    print(f"WOL sent to {mac_str}")


# ---------------------------------------------------------------------------
# Uptime formatting
# ---------------------------------------------------------------------------
def format_uptime():
    secs = utime.time() - boot_time
    d = secs // 86400
    h = (secs % 86400) // 3600
    m = (secs % 3600) // 60
    s = secs % 60
    parts = []
    if d:
        parts.append(f"{d}d")
    if h:
        parts.append(f"{h}h")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}s")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------
def handle_command(text, chat_id):
    """Process an incoming Telegram command. Only responds to authorized chat."""
    if str(chat_id) != CHAT_ID:
        print(f"Ignoring command from unauthorized chat {chat_id}")
        return

    text = text.strip().lower()

    if text == "/status":
        rssi = wlan.status("rssi") if wlan.isconnected() else "N/A"
        mem_free = gc.mem_free()
        msg = (f"🤖 Pico W Status\n"
               f"WiFi: {'Connected' if wlan.isconnected() else 'Disconnected'}\n"
               f"RSSI: {rssi} dBm\n"
               f"Uptime: {format_uptime()}\n"
               f"Free RAM: {mem_free} bytes")
        send_telegram(msg)

    elif text == "/uptime":
        send_telegram(f"⏱ Pico uptime: {format_uptime()}")

    elif text.startswith("/wol"):
        parts = text.split()
        target = parts[1] if len(parts) > 1 else ""
        if target == "pi4":
            send_wol(PI4_MAC)
            send_telegram(f"📡 WOL sent to Pi 4 ({PI4_MAC})")
        elif target == "pi5":
            send_wol(PI5_MAC)
            send_telegram(f"📡 WOL sent to Pi 5 ({PI5_MAC})")
        elif target == "all":
            send_wol(PI4_MAC)
            send_wol(PI5_MAC)
            send_telegram("📡 WOL sent to Pi 4 and Pi 5")
        else:
            send_telegram("Usage: /wol pi4 | /wol pi5 | /wol all")

    elif text == "/help":
        msg = ("📋 Available commands:\n\n"
               "Pico commands:\n"
               "  /status — WiFi, uptime, memory\n"
               "  /uptime — Pico uptime\n"
               "  /wol pi4|pi5|all — Wake-on-LAN\n"
               "  /help — This message\n\n"
               "Pi commands (handled by Pi monitors):\n"
               "  /shutdown pi4|pi5|all\n"
               "  /restart pi4|pi5|all\n"
               "  /ping pi4|pi5")
        send_telegram(msg)

    else:
        # Not a Pico command — let Pi monitors handle it
        pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 40)
    print("Pi Pico W — Home Mesh Monitor")
    print(f"Boot time: {boot_time}")
    print(f"Free RAM: {gc.mem_free()} bytes")
    print("=" * 40)

    # 1. Connect WiFi
    print("[BOOT] Step 1: Connecting WiFi...")
    ensure_wifi()
    print(f"[BOOT] WiFi OK — IP: {wlan.ifconfig()}")

    # 2. NTP sync
    print("[BOOT] Step 2: NTP sync...")
    sync_ntp()

    # 2.5 Send a simple boot-alive message (ASCII only, minimal)
    print("[BOOT] Step 2.5: Sending boot-alive test message...")
    alive_ok = send_telegram("Pico W booted. Debug mode active.")
    print(f"[BOOT] Boot-alive message result: {alive_ok}")

    # 3. Stabilization delay — prevents WOL on brief power flickers
    print(f"[BOOT] Step 3: Stabilization delay: {WOL_BOOT_DELAY}s before sending WOL...")
    for i in range(WOL_BOOT_DELAY):
        if not wlan.isconnected():
            print("WiFi lost during stabilization — reconnecting")
            ensure_wifi()
        if i % 30 == 0:
            print(f"  ...stabilization {i}/{WOL_BOOT_DELAY}s")
        utime.sleep(1)
    print("[BOOT] Stabilization complete.")

    # 4. Send WOL to both Pi's
    print("[BOOT] Step 4: Sending WOL to Pi 4 and Pi 5...")
    send_wol(PI4_MAC)
    send_wol(PI5_MAC)

    # 5. Telegram: power restored
    print("[BOOT] Step 5: Sending power-restored Telegram message...")
    send_telegram("GRID POWER RESTORED: Pico W online. WOL sent to Pi 4 and Pi 5.")

    # 6. Flush stale updates
    print("[BOOT] Step 6: Flushing stale updates...")
    flush_updates()

    # 7. Enable hardware watchdog (8 s timeout)
    wdt = machine.WDT(timeout=8000)
    print("[BOOT] Step 7: Watchdog enabled (8 s)")

    # 8. Main loop
    print("[BOOT] Step 8: Entering main loop — listening for Telegram commands...")
    print(f"[BOOT] Free RAM after boot: {gc.mem_free()} bytes")
    last_update_id = None
    loop_count = 0

    while True:
        wdt.feed()

        # Check WiFi
        if not wlan.isconnected():
            print("WiFi lost — reconnecting...")
            ensure_wifi()
            sync_ntp()
            continue

        # Poll Telegram
        try:
            updates = get_updates(offset=last_update_id)
            for update in updates:
                uid = update.get("update_id")
                last_update_id = uid + 1

                msg = update.get("message", {})
                text = msg.get("text", "")
                chat = msg.get("chat", {})
                cid = chat.get("id", "")

                if text and text.startswith("/"):
                    print(f"Command: {text} from chat {cid}")
                    handle_command(text, cid)
        except Exception as e:
            print(f"Polling error: {e}")

        # Periodic GC
        loop_count += 1
        if loop_count % 10 == 0:
            gc.collect()

        utime.sleep(2)


# Entry point
main()
