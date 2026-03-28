#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# PiZero Capture – one-shot setup script
# Run on a fresh Raspberry Pi OS (Bookworm) installation.
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

echo "═══════════════════════════════════════════"
echo "  PiZero Capture – Setup"
echo "═══════════════════════════════════════════"

# ── System packages ───────────────────────────────────────────────
echo "[1/5] Installing system packages …"
sudo apt-get update -qq
sudo apt-get install -y -qq \
    python3-pip python3-venv \
    python3-picamera2 \
    libzbar0 \
    ffmpeg \
    alsa-utils

# ── Python venv ───────────────────────────────────────────────────
VENV_DIR="$HOME/.venvs/picapture"
echo "[2/5] Creating Python venv at $VENV_DIR …"
python3 -m venv --system-site-packages "$VENV_DIR"
source "$VENV_DIR/bin/activate"

pip install --quiet --upgrade pip
pip install --quiet \
    pyzbar \
    requests

# ── Config ────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/capture/config.env"

echo "[3/5] Configuration"
if grep -q "^VIDAUGMENT_API_BASE_URL=https://your-" "$CONFIG_FILE" 2>/dev/null; then
    echo ""
    echo "  ⚠  You must set VIDAUGMENT_API_BASE_URL in:"
    echo "     $CONFIG_FILE"
    echo ""
    read -rp "  Enter the API base URL now (or press Enter to skip): " api_url
    if [[ -n "$api_url" ]]; then
        sed -i "s|^VIDAUGMENT_API_BASE_URL=.*|VIDAUGMENT_API_BASE_URL=$api_url|" "$CONFIG_FILE"
        echo "  ✓ Saved"
    else
        echo "  → Skipped. Edit config.env before running."
    fi
else
    echo "  ✓ config.env already configured"
fi

# ── Camera check ──────────────────────────────────────────────────
echo "[4/5] Checking camera …"
if command -v libcamera-hello >/dev/null 2>&1; then
    echo "  Running quick camera test (2s) …"
    timeout 3 libcamera-hello -t 2000 --nopreview 2>/dev/null && echo "  ✓ Camera OK" || echo "  ⚠ Camera test failed – check connection"
else
    echo "  ⚠ libcamera-hello not found – is Pi Camera connected?"
fi

# ── Audio check ───────────────────────────────────────────────────
echo "[5/5] Checking audio devices …"
arecord -l 2>/dev/null || echo "  ⚠ No capture devices found – plug in a USB microphone"

# ── systemd service (optional) ────────────────────────────────────
SERVICE_FILE="/etc/systemd/system/picapture.service"
echo ""
read -rp "Install systemd service for auto-start on boot? [y/N] " install_service
if [[ "$install_service" =~ ^[Yy]$ ]]; then
    sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=PiZero Capture
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$SCRIPT_DIR
ExecStart=$VENV_DIR/bin/python -m capture.main
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
    sudo systemctl daemon-reload
    sudo systemctl enable picapture.service
    echo "  ✓ Service installed. Start with:  sudo systemctl start picapture"
else
    echo "  → Skipped. Run manually with:"
    echo "     source $VENV_DIR/bin/activate"
    echo "     python -m capture.main"
fi

echo ""
echo "═══════════════════════════════════════════"
echo "  Setup complete!"
echo "═══════════════════════════════════════════"
