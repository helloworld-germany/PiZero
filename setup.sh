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
    rpicam-apps-core \
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
    sed -i "s|^MIC_TYPE=.*|MIC_TYPE=usb|" "$CONFIG_FILE"
    echo "  ✓ USB mic"
else
    sed -i "s|^MIC_TYPE=.*|MIC_TYPE=i2s|" "$CONFIG_FILE"
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

    # Deploy .asoundrc for boosted_mic virtual ALSA device (5× gain)
    echo ""
    echo "  Deploying ~/.asoundrc (boosted_mic with 5× software gain) …"
    cp "$SCRIPT_DIR/provision/asoundrc" "$HOME/.asoundrc"
    echo "  ✓ ~/.asoundrc installed"
fi

# Ensure ALSA mixer settings are restored on boot
sudo systemctl enable alsa-restore.service 2>/dev/null || true

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

# ── Hardware self-test ────────────────────────────────────────────
echo ""
echo "  ── Hardware self-test ──"
echo ""

# LED test
if grep -q "^LED_PIN=" "$CONFIG_FILE" 2>/dev/null; then
    read -rp "  Test LED? (will flash 10 times) [Y/n]: " test_led
    if [[ ! "$test_led" =~ ^[Nn]$ ]]; then
        "$VENV_DIR/bin/python" -c "
from capture import led, config
import time
led.setup()
for _ in range(10):
    led.on(); time.sleep(0.05)
    led.off(); time.sleep(0.05)
led.cleanup()
" 2>/dev/null
        read -rp "  Did you see the LED flash? [Y/n]: " led_ok
        if [[ "$led_ok" =~ ^[Nn]$ ]]; then
            echo "  ⚠ Check LED wiring (GPIO17 → 330Ω → LED → GND)"
        else
            echo "  ✓ LED OK"
        fi
    fi
fi

# Buzzer test
if grep -q "^USE_BUZZER=true" "$CONFIG_FILE" 2>/dev/null; then
    read -rp "  Test buzzer? (will play ascending chord) [Y/n]: " test_buzzer
    if [[ ! "$test_buzzer" =~ ^[Nn]$ ]]; then
        "$VENV_DIR/bin/python" -c "
from capture import buzzer
buzzer.setup()
buzzer.chord_up()
buzzer.cleanup()
" 2>/dev/null
        read -rp "  Did you hear the chord? [Y/n]: " buzzer_ok
        if [[ "$buzzer_ok" =~ ^[Nn]$ ]]; then
            echo "  ⚠ Check buzzer wiring (GPIO23 → Buzzer+ → GND)"
        else
            echo "  ✓ Buzzer OK"
        fi
    fi
fi

# Microphone test
read -rp "  Test microphone? (will record 2 seconds) [Y/n]: " test_mic
if [[ ! "$test_mic" =~ ^[Nn]$ ]]; then
    # Determine ALSA device from config
    mic_type=$(grep "^MIC_TYPE=" "$CONFIG_FILE" | cut -d= -f2)
    mic_type="${mic_type:-i2s}"
    if [[ "$mic_type" == "i2s" ]]; then
        mic_dev="boosted_mic"
    else
        mic_dev="default"
    fi

    echo "  Recording 2s from $mic_dev …"
    TEST_WAV="/tmp/picapture-mic-test.wav"
    # Try multiple formats – I2S mics (e.g. INMP441/Voice HAT) often need
    # S32_LE at 48kHz stereo, USB mics typically work with S16_LE at 44.1kHz
    MIC_OK=false
    for fmt in "S32_LE 48000 2" "S32_LE 16000 1" "S16_LE 16000 1" "S16_LE 44100 1"; do
        read -r afmt arate achans <<< "$fmt"
        if arecord -D "$mic_dev" -f "$afmt" -r "$arate" -c "$achans" -d 2 "$TEST_WAV" 2>/dev/null; then
            wav_size=$(stat -c%s "$TEST_WAV" 2>/dev/null || echo 0)
            if [[ "$wav_size" -gt 10000 ]]; then
                MIC_OK=true
                echo "  ✓ Mic OK – captured $(( wav_size / 1024 )) KB (${afmt} ${arate}Hz ${achans}ch)"
                break
            fi
        fi
    done
    if [[ "$MIC_OK" == "true" ]]; then
        # Signal confirmation via available hardware
        if grep -q "^USE_BUZZER=true" "$CONFIG_FILE" 2>/dev/null; then
            "$VENV_DIR/bin/python" -c "
from capture import buzzer
buzzer.setup()
buzzer.beep(0.3)
buzzer.cleanup()
" 2>/dev/null
        elif grep -q "^LED_PIN=" "$CONFIG_FILE" 2>/dev/null; then
            "$VENV_DIR/bin/python" -c "
from capture import led
import time
led.setup()
led.on(); time.sleep(1.0); led.off()
led.cleanup()
" 2>/dev/null
        fi
    else
        echo "  ⚠ arecord failed – check ALSA device ($mic_dev)"
    fi
    rm -f "$TEST_WAV"
fi

# ── ALSA capture gain (system-wide) ──────────────────────────────
echo ""
echo "  ── Audio capture gain ──"
echo ""
echo "  Audio gain is set system-wide via ALSA mixer controls."
echo "  This avoids per-chunk software gain in ffmpeg."
echo ""
read -rp "  Set capture gain now via alsamixer? [Y/n]: " set_gain
if [[ ! "$set_gain" =~ ^[Nn]$ ]]; then
    echo "  Opening alsamixer – press F6 to select sound card, F4 for capture."
    echo "  Adjust the capture level (I2S MEMS mics typically need high gain)."
    echo "  Press Esc when done."
    alsamixer 2>/dev/null || echo "  ⚠ alsamixer not available (install alsa-utils)"
    echo "  Persisting ALSA mixer settings …"
    sudo alsactl store 2>/dev/null && echo "  ✓ ALSA settings saved (restored automatically on boot)" \
        || echo "  ⚠ alsactl store failed – settings will not persist across reboots"
else
    echo "  → Skipped. You can set gain later with: alsamixer && sudo alsactl store"
fi

# ── Camera check ──────────────────────────────────────────────────
echo ""
echo "[4/5] Checking camera …"
if command -v rpicam-hello >/dev/null 2>&1; then
    echo "  Running quick camera test (2s) …"
    if timeout 3 rpicam-hello -t 2000 --nopreview 2>/dev/null; then
        echo "  ✓ Camera OK"
    else
        echo "  ⚠ Camera test failed – this is normal if the capture"
        echo "    service is currently running (camera busy)."
        echo "    If this is a fresh install, check the ribbon cable."
    fi
else
    echo "  ⚠ rpicam-hello not found – is rpicam-apps installed?"
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
