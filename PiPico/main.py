"""
Pico W — Home Mesh Status Responder

No boot.py needed: MicroPython runs this file automatically as main.py.

What it does:
  1. Load config.json (wifi_ssid, wifi_password)
  2. Connect to WiFi, retrying forever; track how long the last drop lasted
  3. Serve GET /status on port 80 with JSON:
       {"uptime_s": int, "wifi_connected": bool, "rssi": int|null, "last_outage_s": int|null}
  4. Feed an 8.3s hardware watchdog every loop iteration (after a grace
     period, so a fresh mpremote session has time to interrupt it)

Any device on the LAN can query it, e.g.:
  curl http://<pico-ip>/status
"""

import network
import usocket as socket
import uselect as select
import utime
import machine
import gc

led = machine.Pin("LED", machine.Pin.OUT)


def load_config():
    import ujson
    try:
        with open("config.json") as f:
            return ujson.load(f)
    except Exception as e:
        print("FATAL: cannot load config.json:", e)
        while True:
            for _ in range(3):
                led.toggle()
                utime.sleep_ms(80)
            utime.sleep_ms(1000)


config = load_config()
WIFI_SSID = config["wifi_ssid"]
WIFI_PASSWORD = config["wifi_password"]

BOOT_GRACE_S = 15  # let a fresh mpremote/REPL session in before the watchdog arms
POLL_TIMEOUT_MS = 500

wlan = network.WLAN(network.STA_IF)
wlan.active(True)
wlan.config(pm=network.WLAN.PM_NONE)  # power-save causes dropped/slow pings

uptime_ms_accum = 0
last_tick = utime.ticks_ms()
disconnect_started_at = None
last_outage_s = None
wdt = None


def feed():
    if wdt:
        wdt.feed()


def connect_wifi():
    global disconnect_started_at, last_outage_s

    if wlan.isconnected():
        return True

    if disconnect_started_at is None:
        disconnect_started_at = utime.time()

    print("Connecting to WiFi:", WIFI_SSID)
    wlan.connect(WIFI_SSID, WIFI_PASSWORD)

    deadline = utime.time() + 20
    while not wlan.isconnected() and utime.time() < deadline:
        feed()
        led.toggle()
        utime.sleep_ms(250)

    if wlan.isconnected():
        led.value(1)
        if disconnect_started_at is not None:
            last_outage_s = utime.time() - disconnect_started_at
            disconnect_started_at = None
        print("WiFi connected:", wlan.ifconfig()[0])
        return True

    led.value(0)
    return False


def make_listener():
    s = socket.socket()
    s.bind(("0.0.0.0", 80))
    s.listen(2)
    return s


def status_json():
    connected = wlan.isconnected()
    rssi = wlan.status("rssi") if connected else None
    return '{"uptime_s": %d, "wifi_connected": %s, "rssi": %s, "last_outage_s": %s}' % (
        uptime_ms_accum // 1000,
        "true" if connected else "false",
        rssi if rssi is not None else "null",
        last_outage_s if last_outage_s is not None else "null",
    )


def handle_client(cl):
    try:
        cl.settimeout(2)
        cl_file = cl.makefile("rwb", 0)
        # Only one endpoint exists, so the request line/path is read and ignored.
        while True:
            line = cl_file.readline()
            if not line or line == b"\r\n":
                break
        cl.send("HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
        cl.send(status_json())
    except Exception as e:
        print("client error:", e)
    finally:
        cl.close()


def main():
    global uptime_ms_accum, last_tick, wdt

    print("=" * 40)
    print("Pico W - Home Mesh Status Responder")
    print("=" * 40)

    while not connect_wifi():
        print("Retrying WiFi in 15s...")
        utime.sleep(15)

    listener = make_listener()
    poller = select.poll()
    poller.register(listener, select.POLLIN)
    print("Status server listening on port 80")

    boot_at = utime.time()
    wdt_armed = False

    while True:
        now = utime.ticks_ms()
        uptime_ms_accum += utime.ticks_diff(now, last_tick)
        last_tick = now

        if not wdt_armed and utime.time() - boot_at > BOOT_GRACE_S:
            wdt = machine.WDT(timeout=8388)
            wdt_armed = True
            print("Watchdog armed")

        feed()

        if not wlan.isconnected():
            print("WiFi lost - reconnecting...")
            try:
                listener.close()
            except Exception:
                pass
            connect_wifi()
            listener = make_listener()
            poller = select.poll()
            poller.register(listener, select.POLLIN)
            continue

        for sock, _ in poller.poll(POLL_TIMEOUT_MS):
            cl, addr = sock.accept()
            handle_client(cl)
            feed()

        gc.collect()


main()
