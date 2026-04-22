# Home-Mesh — UPS-Aware Power Monitoring System

A three-device power monitoring and management system for a Raspberry Pi homelab, designed to gracefully shut down NVMe-equipped Pi's during extended power outages and wake them back up when power returns.

## Architecture

```mermaid
graph TD
    subgraph PICO["Pi Pico W — Ping Target + Bot"]
        P1["📡 Ping Target"]
        P2["🤖 Telegram Bot"]
        P3["⚡ WOL Sender"]
        P4["📶 WiFi Auto-Reconnect"]
    end

    subgraph PI4["Pi 4 — LCD + Power Monitor"]
        L1["🖥️ LCD: Temp + Uptime"]
        L2["🔍 Ping Monitor"]
        L3["📨 Telegram Listener"]
        L4["⏱️ Shutdown Countdown"]
    end

    subgraph PI5["Pi 5 — Headless Monitor"]
        H1["📋 Syslog Monitor"]
        H2["🔍 Ping Monitor"]
        H3["📨 Telegram Listener"]
        H4["⏱️ Shutdown Countdown"]
    end

    USER["👤 You on Telegram"]

    PI4 -- "ping every 60s" --> PICO
    PI5 -- "ping every 60s" --> PICO

    USER -- "/wol /status /uptime" --> P2
    USER -- "/shutdown /restart /ping pi4" --> L3
    USER -- "/shutdown /restart /ping pi5" --> H3

    P3 -- "WOL magic packet<br/>(on boot after 3 min)" --> PI4
    P3 -- "WOL magic packet<br/>(on boot after 3 min)" --> PI5
```

## Power Failure Timeline

```mermaid
sequenceDiagram
    participant Grid as ⚡ Grid Power
    participant Pico as Pi Pico W
    participant Pi4 as Pi 4 (LCD)
    participant Pi5 as Pi 5 (Headless)
    participant TG as Telegram

    Note over Grid: Power goes out
    Grid->>Pico: ❌ Power lost
    
    Note over Pi4,Pi5: Ping attempts every 60s
    Pi4->>Pico: ping (fail — strike 1)
    Pi5->>Pico: ping (fail — strike 1)
    Pi4->>Pico: ping (fail — strike 2)
    Pi5->>Pico: ping (fail — strike 2)
    Pi4->>Pico: ping (fail — strike 3)
    Pi5->>Pico: ping (fail — strike 3)
    
    Note over Pi4,Pi5: 3 strikes — power loss declared
    Pi4->>TG: ⚠️ GRID POWER LOST (7 min countdown)
    Pi5->>TG: ⚠️ GRID POWER LOST (7 min countdown)
    Note over Pi4: LCD shows countdown

    Note over Pi4,Pi5: 7 minutes later...
    Pi4->>TG: 🚨 SHUTDOWN INITIATED
    Pi4->>Pi4: sync → poweroff
    Pi5->>TG: 🚨 SHUTDOWN INITIATED
    Pi5->>Pi5: sync → poweroff

    Note over Grid: Power restored
    Grid->>Pico: ✅ Power on
    Note over Pico: WiFi connect → 3 min wait
    Pico->>Pi4: 📡 WOL magic packet
    Pico->>Pi5: 📡 WOL magic packet
    Pico->>TG: ⚡ POWER RESTORED
    Note over Pi4,Pi5: Boot → resume monitoring
```

## Telegram Commands

```mermaid
graph LR
    subgraph "Handled by Pico"
        A["/status"] --> A1["WiFi, uptime, RAM"]
        B["/uptime"] --> B1["Pico uptime"]
        C["/wol pi4∣pi5∣all"] --> C1["WOL magic packet"]
        D["/help"] --> D1["Command list"]
    end

    subgraph "Handled by Pi Monitors"
        E["/shutdown pi4∣pi5∣all"] --> E1["sync + poweroff"]
        F["/restart pi4∣pi5∣all"] --> F1["reboot"]
        G["/ping pi4∣pi5"] --> G1["Alive + temp + uptime"]
    end
```

## Setup

### 1. Pi Pico W

```bash
# 1. Copy config template and fill in your values
cp PiPico/config.example.json PiPico/config.json
# Edit config.json with your WiFi, Telegram, and MAC addresses

# 2. Build a standalone main.py with secrets baked in
python3 PiPico/build.py
# This produces PiPico/main_built.py (gitignored)

# 3. Flash to Pico W using Thonny (from the project venv)
source venv/bin/activate
thonny
# In Thonny:
#   - Set interpreter to MicroPython (Raspberry Pi Pico)
#   - Upload PiPico/main_built.py to the Pico as "main.py"
#   - Upload PiPico/boot.py to the Pico as "boot.py"
#   - No need to upload config.json — values are baked into main_built.py
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
└── legacy/                     # Archived C code and old scripts (gitignored)
```

## Security Notes

- **All secrets** (Telegram tokens, WiFi passwords, chat IDs) are in gitignored config files
- **Telegram commands** are validated against `chat_id` — unauthorized users are ignored
- The `legacy/` directory is gitignored and won't be pushed
