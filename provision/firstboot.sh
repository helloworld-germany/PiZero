#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# firstboot.sh – Runs once on first Pi boot to deploy PiZero capture.
# Place this on the boot partition alongside the other headless files.
# It gets executed by the companion firstboot.service.
# ──────────────────────────────────────────────────────────────────
set -euo pipefail
exec > /var/log/firstboot.log 2>&1

echo "=== PiZero first-boot provisioning ==="
date

# Wait for network
echo "[1] Waiting for network …"
for i in $(seq 1 30); do
    if ping -c1 -W2 github.com >/dev/null 2>&1; then
        echo "    Network OK"
        break
    fi
    echo "    Attempt $i/30 …"
    sleep 5
done

# System update + deps
echo "[2] Installing system packages …"
sudo apt-get update -qq
sudo apt-get install -y -qq \
    git python3-pip python3-venv python3-picamera2 \
    libzbar0 ffmpeg alsa-utils

# Enable camera interface
echo "[3] Enabling camera …"
sudo raspi-config nonint do_camera 0 2>/dev/null || true

# Clone repo
echo "[4] Cloning PiZero repo …"
cd /home/pi
if [ ! -d PiZero ]; then
    # Uses the deploy token baked into the clone URL
    git clone https://github.com/helloworld-germany/PiZero.git
fi
cd PiZero

# Python venv
echo "[5] Setting up Python venv …"
VENV_DIR="/home/pi/.venvs/picapture"
python3 -m venv --system-site-packages "$VENV_DIR"
"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" install --quiet pyzbar requests

# Install systemd service
echo "[6] Installing systemd service …"
sudo tee /etc/systemd/system/picapture.service > /dev/null <<SVCEOF
[Unit]
Description=PiZero Capture
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/home/pi/PiZero
ExecStart=/home/pi/.venvs/picapture/bin/python -m capture.main
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SVCEOF

sudo systemctl daemon-reload
sudo systemctl enable picapture.service

# Deploy .asoundrc for software mic gain boost (boosted_mic device)
cp /home/pi/PiZero/provision/asoundrc /home/pi/.asoundrc
chown pi:pi /home/pi/.asoundrc

# Ensure ALSA mixer settings are restored on boot (alsa-restore.service)
sudo systemctl enable alsa-restore.service 2>/dev/null || true

# Disable self so it doesn't run again
echo "[7] Disabling first-boot service …"
sudo systemctl disable firstboot.service
sudo rm -f /etc/systemd/system/firstboot.service

echo "=== First-boot complete! ==="
echo "Edit /home/pi/PiZero/capture/config.env then:"
echo "  sudo systemctl start picapture"
date
