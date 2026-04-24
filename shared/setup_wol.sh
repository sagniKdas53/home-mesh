#!/bin/bash
set -euo pipefail
# ---------------------------------------------------------------------------
# Enable Wake-on-LAN on Raspberry Pi (Ethernet only)
#
# Run once on both Pi 4 and Pi 5:
#   sudo bash shared/setup_wol.sh
#
# This creates a persistent systemd service so WOL survives reboots.
# ---------------------------------------------------------------------------

# Find the primary ethernet interface
IFACE=$(ip route show default | awk '/default/ {print $5}' | head -1)

if [ -z "$IFACE" ]; then
    echo "ERROR: Could not detect default network interface."
    echo "Make sure the Pi is connected via Ethernet."
    exit 1
fi

echo "Detected interface: $IFACE"

# Check if ethtool is installed
if ! command -v ethtool &>/dev/null; then
    echo "Installing ethtool..."
    sudo apt-get update && sudo apt-get install -y ethtool
fi

# Enable WOL now
echo "Enabling WOL on $IFACE..."
sudo ethtool -s "$IFACE" wol g

# Show current state
echo ""
echo "Current Wake-on settings:"
sudo ethtool "$IFACE" | grep -i "wake-on"
echo ""

# Create a persistent systemd service so WOL survives reboots
SERVICE_FILE="/etc/systemd/system/wol-enable.service"

echo "Creating persistent service at $SERVICE_FILE..."
cat <<EOF | sudo tee "$SERVICE_FILE" > /dev/null
[Unit]
Description=Enable Wake-on-LAN
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/ethtool -s $IFACE wol g
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable wol-enable.service
sudo systemctl start wol-enable.service

echo ""
echo "Done! WOL is enabled and will persist across reboots."
echo "MAC address for this Pi:"
ip link show "$IFACE" | awk '/ether/ {print $2}'
echo ""
echo "Add this MAC address to your Pico's config.json."
