# Home-Mesh — UPS-Aware Power Monitoring System

A three-device power monitoring and management system for a Raspberry Pi homelab, designed to gracefully shut down NVMe-equipped Pi's during extended power outages and wake them back up when power returns.

## Architecture

```
┌─────────────────────┐
│     Pi Pico W        │  ← Powered by UPS / wall power
│  • Ping target       │  ← Pi's ping this to detect power loss
│  • Telegram bot      │  ← /wol, /status, /uptime, /help
│  • WOL sender        │  ← Wakes Pi's after power restore
│  • WiFi resilient    │  ← Auto-reconnects on WiFi drop
└─────────┬───────────┘
          │ ping (ICMP)
    ┌─────┴─────┐
    │           │
┌───▼───┐  ┌───▼───┐
│ Pi 4  │  │ Pi 5  │   ← Both on Ethernet (WOL-capable)
│ LCD   │  │Headless│
│ Stats │  │Monitor │
│ +Power│  │ +Power │
│Monitor│  │Monitor │
└───────┘  └───────┘

Telegram commands:
  /shutdown pi4|pi5|all  ← Handled by respective Pi monitor
  /restart  pi4|pi5|all  ← Handled by respective Pi monitor
  /ping     pi4|pi5      ← Handled by respective Pi monitor
  /wol      pi4|pi5|all  ← Handled by Pico
  /status                ← Pico status
  /uptime                ← Pico uptime
  /help                  ← Command list
```

## Power Failure Timeline

```
t=0     Power goes out. Pico loses power.
t=1m    Pi's first ping fails (ping interval = 60s)
t=2m    Second ping fails (strike 2)
t=3m    Third ping fails (strike 3) → POWER LOSS DECLARED
        ├─ Pi 4: LCD shows countdown, Telegram alert sent
        └─ Pi 5: Syslog + Telegram alert sent
t=10m   Countdown expires (3m detection + 7m countdown)
        ├─ Pi 4: sync → systemctl poweroff
        └─ Pi 5: sync → systemctl poweroff

--- Power restored ---

t=0     Pico boots, connects WiFi
t=3m    Stabilization delay expires
        ├─ Pico sends WOL to Pi 4 + Pi 5
        └─ Telegram: "POWER RESTORED"
t=4m    Pi's boot up, resume monitoring
```

## Setup

### 1. Pi Pico W

```bash
# Copy config template and fill in your values
cp PiPico/config.example.json PiPico/config.json
# Edit config.json with your WiFi, Telegram, and MAC addresses

# Flash to Pico W using Thonny or mpremote:
#   - Upload main.py and config.json to the Pico's filesystem
#   - Upload boot.py to auto-start on power-on
```

### 2. Pi 4 (LCD + Power Monitor)

```bash
# Install dependencies
pip install -r Pi4LCD/requirements.txt

# Create config
cp config.example.ini Pi4LCD/config.ini
# Edit Pi4LCD/config.ini — set identity.name = pi4

# Install systemd service
sudo cp Pi4LCD/power-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable power-monitor.service
sudo systemctl start power-monitor.service

# Enable WOL
sudo bash shared/setup_wol.sh
# Note the MAC address printed — add it to Pico's config.json as pi4_mac
```

### 3. Pi 5 (Headless Monitor)

```bash
# Install dependencies
pip install -r Pi5/requirements.txt

# Create config
cp config.example.ini Pi5/config.ini
# Edit Pi5/config.ini — set identity.name = pi5

# Install systemd service
sudo cp Pi5/power-monitor.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable power-monitor.service
sudo systemctl start power-monitor.service

# Enable WOL
sudo bash shared/setup_wol.sh
# Note the MAC address printed — add it to Pico's config.json as pi5_mac
```

### 4. One-Shot LCD Message (Pi 4)

```bash
# Display a temporary message on the LCD (stops monitor, restarts on exit)
sudo python3 Pi4LCD/lcd_message.py "Hello World" "Line 2" 30
sudo python3 Pi4LCD/lcd_message.py "Single|Line" 10
```

## Configuration

### Pi 4 / Pi 5 — `config.ini`

```ini
[telegram]
bot_token = YOUR_BOT_TOKEN
chat_id = YOUR_CHAT_ID

[network]
pico_ip = 192.168.0.107

[power]
ping_interval_sec = 60
max_failed_pings = 3
shutdown_countdown_min = 7
ping_timeout_sec = 5

[identity]
name = pi4   # or pi5
```

### Pico W — `config.json`

```json
{
    "wifi_ssid": "YOUR_SSID",
    "wifi_password": "YOUR_PASSWORD",
    "bot_token": "YOUR_BOT_TOKEN",
    "chat_id": "YOUR_CHAT_ID",
    "pi4_mac": "AA:BB:CC:DD:EE:F1",
    "pi5_mac": "AA:BB:CC:DD:EE:F2",
    "pi4_ip": "192.168.0.XXX",
    "pi5_ip": "192.168.0.XXX",
    "wol_boot_delay_sec": 180
}
```

## File Structure

```
home-mesh/
├── .gitignore
├── README.md
├── config.example.ini          # Template for Pi 4/Pi 5
├── PiPico/
│   ├── main.py                 # Telegram bot + WOL + ping target
│   ├── boot.py                 # MicroPython auto-start
│   ├── config.example.json     # Template
│   └── config.json             # Secrets (gitignored)
├── Pi4LCD/
│   ├── power_monitor.py        # LCD stats + power monitor
│   ├── lcd_message.py          # One-shot LCD message utility
│   ├── power-monitor.service   # systemd unit
│   ├── requirements.txt
│   └── config.ini              # Secrets (gitignored)
├── Pi5/
│   ├── power_monitor.py        # Headless power monitor
│   ├── power-monitor.service   # systemd unit
│   ├── requirements.txt
│   └── config.ini              # Secrets (gitignored)
├── shared/
│   └── setup_wol.sh            # WOL setup for Pi's
└── legacy/                     # Archived C code and old scripts
```

## Security Notes

- **All secrets** (Telegram tokens, WiFi passwords, chat IDs) are in gitignored config files
- **Telegram commands** are validated against `chat_id` — unauthorized users are ignored
- **Old tokens** in the `legacy/` directory should be **rotated** since they were committed to git history
