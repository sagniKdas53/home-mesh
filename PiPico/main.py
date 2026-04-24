"""
Pi Pico W — Telegram Bot + Ping Target

Boot sequence:
  1. Load config.json
  2. Connect WiFi (retry forever)
  3. Sync NTP time
  4. Notify Telegram: POWER RESTORED
  5. Flush stale Telegram updates
  6. Enable hardware watchdog
  7. Main loop: poll Telegram commands

Telegram commands handled by Pico:
  /status   — WiFi RSSI, uptime, free memory
  /uptime   — Pico uptime
  /simulate_power_loss — Disconnect WiFi for 11 minutes to test Pi shutdown
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
PI4_MAC = config.get("pi4_mac", "")
PI5_MAC = config.get("pi5_mac", "")
DEBUG_MODE = config.get("debug_mode", False)

TELEGRAM_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

boot_time_ms = utime.ticks_ms()
wlan = network.WLAN(network.STA_IF)
wdt = None  # Set after boot, used globally to feed watchdog during network ops


def dprint(*args, **kwargs):
    """Debug print — only outputs when DEBUG_MODE is True."""
    if DEBUG_MODE:
        print(*args, **kwargs)


def feed_watchdog():
    """Feed the watchdog if it's been initialized."""
    global wdt
    if wdt is not None:
        wdt.feed()


dprint(f"[DEBUG] Config loaded:")
dprint(f"  SSID: {WIFI_SSID}")
dprint(f"  CHAT_ID: '{CHAT_ID}' (type={type(CHAT_ID)})")
dprint(f"  BOT_TOKEN: {BOT_TOKEN[:10]}...{BOT_TOKEN[-5:]}")
dprint(f"  TELEGRAM_URL: {TELEGRAM_URL[:40]}...")
dprint(f"  TELEGRAM_URL: {TELEGRAM_URL[:40]}...")

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
        dprint("[TG] WiFi down — skipping Telegram send")
        return False
    try:
        feed_watchdog()
        url = f"{TELEGRAM_URL}/sendMessage"
        payload_dict = {"chat_id": CHAT_ID, "text": text}
        payload = ujson.dumps(payload_dict)
        dprint(f"[TG] Sending: '{text[:60]}...'" if len(text) > 60 else f"[TG] Sending: '{text}'")
        resp = urequests.post(url, data=payload,
                              headers={"Content-Type": "application/json"})
        feed_watchdog()
        ok = resp.status_code == 200
        if not ok:
            try:
                body = resp.text
            except:
                body = "(could not read body)"
            print(f"Telegram ERROR {resp.status_code}: {body}")
        else:
            dprint(f"[TG] Message sent OK")
        resp.close()
        return ok
    except Exception as e:
        print(f"Telegram send failed: {e}")
        return False


def get_updates(offset=None):
    """Poll Telegram for new messages."""
    if not wlan.isconnected():
        return []
    try:
        feed_watchdog()
        params = {"timeout": 2}  # Keep short to avoid watchdog timeout
        if offset is not None:
            params["offset"] = offset
        url = f"{TELEGRAM_URL}/getUpdates"
        resp = urequests.post(url, data=ujson.dumps(params),
                              headers={"Content-Type": "application/json"})
        feed_watchdog()
        if resp.status_code == 200:
            data = resp.json()
            resp.close()
            return data.get("result", [])
        else:
            try:
                body = resp.text
            except:
                body = "(could not read body)"
            print(f"getUpdates ERROR {resp.status_code}: {body}")
        resp.close()
    except Exception as e:
        print(f"getUpdates failed: {e}")
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


# Removed WOL



# ---------------------------------------------------------------------------
# Uptime formatting
# ---------------------------------------------------------------------------
def format_uptime():
    secs = utime.ticks_diff(utime.ticks_ms(), boot_time_ms) // 1000
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
        msg = (f"[Pico W Status]\n"
               f"WiFi: {'Connected' if wlan.isconnected() else 'Disconnected'}\n"
               f"RSSI: {rssi} dBm\n"
               f"Uptime: {format_uptime()}\n"
               f"Free RAM: {mem_free} bytes")
        send_telegram(msg)

    elif text == "/uptime":
        send_telegram(f"Pico uptime: {format_uptime()}")

    elif text == "/simulate_power_loss":
        send_telegram("Simulating power loss... Disconnecting WiFi for 11 minutes to trigger Pi shutdown. Pico will be unreachable.")
        wlan.disconnect()
        wlan.active(False)
        # Need to feed watchdog while waiting
        for _ in range(11 * 60):
            feed_watchdog()
            utime.sleep(1)
        wlan.active(True)
        ensure_wifi()
        sync_ntp()
        send_telegram("Power loss simulation complete. Pico reconnected to WiFi.")

    elif text == "/help":
        msg = ("Available commands:\n\n"
               "Pico commands:\n"
               "  /status - WiFi, uptime, memory\n"
               "  /uptime - Pico uptime\n"
               "  /simulate_power_loss - Disable WiFi for 11m to test Pi shutdown\n"
               "  /help - This message\n\n"
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
    dprint(f"Boot time: {boot_time}")
    dprint(f"Free RAM: {gc.mem_free()} bytes")
    print("=" * 40)

    # 1. Connect WiFi
    ensure_wifi()
    print(f"WiFi connected: {wlan.ifconfig()[0]}")

    # 2. NTP sync
    sync_ntp()

    # 2.5 Debug-only boot-alive message
    if DEBUG_MODE:
        print("[BOOT] Sending boot-alive test message...")
        alive_ok = send_telegram("Pico W booted. Debug mode active.")
        print(f"[BOOT] Boot-alive result: {alive_ok}")

    # 3. Flush stale updates from before this boot
    flush_updates()

    # 4. Telegram: power restored
    send_telegram("GRID POWER RESTORED: Pico W online.")

    # 5. Enable hardware watchdog (8.388 s timeout — max on RP2040)
    global wdt
    wdt = machine.WDT(timeout=8388)
    print("Watchdog enabled (8.388 s)")

    # 6. Main loop
    print("Entering main loop...")
    dprint(f"Free RAM after boot: {gc.mem_free()} bytes")
    loop_count = 0
    last_update_id = None

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
            wdt.feed()
            updates = get_updates(offset=last_update_id)
            wdt.feed()
            for update in updates:
                uid = update.get("update_id")
                last_update_id = uid + 1

                msg = update.get("message", {})
                text = msg.get("text", "")
                chat = msg.get("chat", {})
                cid = chat.get("id", "")

                if text and text.startswith("/"):
                    dprint(f"Command: {text} from chat {cid}")
                    wdt.feed()
                    handle_command(text, cid)
                    wdt.feed()
        except Exception as e:
            print(f"Polling error: {e}")

        # Periodic GC
        loop_count += 1
        if loop_count % 10 == 0:
            gc.collect()

        utime.sleep(2)


# Entry point
main()
