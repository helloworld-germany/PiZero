#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# PiZero Capture – one-shot setup script
# Run on a fresh Raspberry Pi OS (Bookworm / Trixie) installation.
# After running: reboot → system is ready.
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

NEEDS_REBOOT=false

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

# ── Hardware peripherals ──────────────────────────────────────────
echo ""
echo "  ── Hardware setup ──"
echo ""

# Microphone type
echo "  Microphone type:"
echo "    1) I2S  (e.g. INMP441 via googlevoicehat-soundcard) [default]"
echo "    2) USB  (any USB microphone)"
read -rp "  Choose [1]: " mic_choice
mic_choice="${mic_choice:-1}"
if [[ "$mic_choice" == "2" ]]; then
    echo ""
    echo "  Available capture devices:"
    arecord -l 2>/dev/null || echo "    (none detected – plug in your USB mic and re-run)"
    echo ""
    read -rp "  ALSA device (e.g. hw:1,0) [default]: " usb_dev
    usb_dev="${usb_dev:-default}"
    sed -i "s|^AUDIO_BACKEND=.*|AUDIO_BACKEND=alsa|"     "$CONFIG_FILE"
    sed -i "s|^AUDIO_DEVICE=.*|AUDIO_DEVICE=$usb_dev|"   "$CONFIG_FILE"
    sed -i "s|^USE_I2S_MIC=.*|USE_I2S_MIC=false|"        "$CONFIG_FILE"
    echo "  ✓ USB mic → AUDIO_DEVICE=$usb_dev"
else
    sed -i "s|^AUDIO_BACKEND=.*|AUDIO_BACKEND=i2s|"       "$CONFIG_FILE"
    sed -i "s|^USE_I2S_MIC=.*|USE_I2S_MIC=true|"          "$CONFIG_FILE"
    echo "  ✓ I2S mic (INMP441)"

    # ── Configure /boot/config.txt for I2S audio ──────────────────
    # Trixie: /boot/firmware/config.txt   Bookworm: /boot/config.txt
    if [[ -f /boot/firmware/config.txt ]]; then
        BOOT_CONFIG="/boot/firmware/config.txt"
    else
        BOOT_CONFIG="/boot/config.txt"
    fi
    echo ""
    echo "  Configuring I2S audio in $BOOT_CONFIG …"

    # Enable I2S interface
    if grep -q "^dtparam=i2s=on" "$BOOT_CONFIG" 2>/dev/null; then
        echo "  ✓ dtparam=i2s=on already set"
    else
        echo "dtparam=i2s=on" | sudo tee -a "$BOOT_CONFIG" > /dev/null
        echo "  ✓ Added dtparam=i2s=on"
        NEEDS_REBOOT=true
    fi

    # Add sound card overlay
    read -rp "  I2S overlay name [googlevoicehat-soundcard]: " i2s_overlay
    i2s_overlay="${i2s_overlay:-googlevoicehat-soundcard}"
    if grep -q "^dtoverlay=$i2s_overlay" "$BOOT_CONFIG" 2>/dev/null; then
        echo "  ✓ dtoverlay=$i2s_overlay already set"
    else
        echo "dtoverlay=$i2s_overlay" | sudo tee -a "$BOOT_CONFIG" > /dev/null
        echo "  ✓ Added dtoverlay=$i2s_overlay"
        NEEDS_REBOOT=true
    fi
fi

# Buzzer
echo ""
read -rp "  Enable piezo buzzer on GPIO23? [Y/n]: " use_buzzer
if [[ "$use_buzzer" =~ ^[Nn]$ ]]; then
    sed -i "s|^USE_BUZZER=.*|USE_BUZZER=false|" "$CONFIG_FILE"
    echo "  ✓ Buzzer disabled"
else
    sed -i "s|^USE_BUZZER=.*|USE_BUZZER=true|"  "$CONFIG_FILE"
    echo "  ✓ Buzzer enabled (GPIO23)"
fi

# Button
echo ""
read -rp "  Enable push button on GPIO3? [Y/n]: " use_button
if [[ "$use_button" =~ ^[Nn]$ ]]; then
    sed -i "s|^USE_BUTTON=.*|USE_BUTTON=false|" "$CONFIG_FILE"
    echo "  ✓ Button disabled"
else
    sed -i "s|^USE_BUTTON=.*|USE_BUTTON=true|"  "$CONFIG_FILE"
    echo "  ✓ Button enabled (GPIO3)"
fi

# LED
echo ""
read -rp "  Enable status LED on GPIO17? [Y/n]: " use_led
if [[ "$use_led" =~ ^[Nn]$ ]]; then
    sed -i "s|^LED_PIN=.*|#LED_PIN=17|"         "$CONFIG_FILE"
    echo "  ✓ LED disabled"
else
    sed -i "s|^.*LED_PIN=.*|LED_PIN=17|"        "$CONFIG_FILE"
    echo "  ✓ LED enabled (GPIO17)"
fi

# ── Camera check ──────────────────────────────────────────────────
echo "[4/5] Checking camera …"
if command -v rpicam-hello >/dev/null 2>&1; then
    echo "  Running quick camera test (2s) …"
    timeout 3 rpicam-hello -t 2000 --nopreview 2>/dev/null && echo "  ✓ Camera OK" || echo "  ⚠ Camera test failed – check connection"
else
    echo "  ⚠ rpicam-hello not found – is Pi Camera connected?"
fi

# ── Audio check ───────────────────────────────────────────────────
echo "[5/5] Checking audio devices …"
arecord -l 2>/dev/null || echo "  ⚠ No capture devices found – plug in a USB microphone"

# ── RAM disk for capture directory ─────────────────────────────────
CAPTURE_DIR="/run/picapture"
echo ""
echo "[+] Setting up tmpfs RAM disk at $CAPTURE_DIR …"
TMPFS_LINE="tmpfs $CAPTURE_DIR tmpfs nodev,nosuid,size=200M,uid=$(id -u),gid=$(id -g) 0 0"
if ! grep -qF "$CAPTURE_DIR" /etc/fstab 2>/dev/null; then
    echo "$TMPFS_LINE" | sudo tee -a /etc/fstab > /dev/null
    echo "  ✓ Added tmpfs entry to /etc/fstab"
    NEEDS_REBOOT=true
else
    echo "  ✓ tmpfs entry already in /etc/fstab"
fi
sudo mkdir -p "$CAPTURE_DIR"
sudo mount "$CAPTURE_DIR" 2>/dev/null || true
echo "  ✓ $CAPTURE_DIR mounted (RAM-backed, 200 MB)"

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

if [[ "$NEEDS_REBOOT" == "true" ]]; then
    echo ""
    echo "  ⚠  A reboot is required for hardware changes"
    echo "     (I2S audio overlay / tmpfs mount)."
    echo ""
    read -rp "  Reboot now? [Y/n]: " do_reboot
    if [[ ! "$do_reboot" =~ ^[Nn]$ ]]; then
        echo "  Rebooting …"
        sudo reboot
    else
        echo "  → Please reboot manually before running PiZero Capture."
    fi
else
    echo ""
    echo "  No reboot needed. Start with:"
    echo "    source $VENV_DIR/bin/activate && python -m capture"
fi
