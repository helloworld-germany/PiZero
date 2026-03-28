#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────
# prepare-sd.sh – Run on QNAP Ubuntu Linux Station
#
# Flashes Raspberry Pi OS, configures headless boot, and drops the
# first-boot provisioning script onto the SD card.
#
# Usage:
#   1. Plug in USB SD card reader with micro SD
#   2. Edit the variables below (WiFi, device)
#   3. Run: bash prepare-sd.sh
# ──────────────────────────────────────────────────────────────────
set -euo pipefail

# ╔═══════════════════════════════════════════════════════════╗
# ║  EDIT THESE                                              ║
# ╚═══════════════════════════════════════════════════════════╝
WIFI_SSID="YourWiFiName"
WIFI_PASSWORD="YourWiFiPassword"
WIFI_COUNTRY="DE"
PI_PASSWORD="changeme123"          # change this!
SD_DEVICE=""                       # leave empty to auto-detect, or set e.g. /dev/sdb
# ╔═══════════════════════════════════════════════════════════╗

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
IMG_URL="https://downloads.raspberrypi.com/raspios_lite_arm64/images/raspios_lite_arm64-2025-11-19/2025-11-19-raspios-bookworm-arm64-lite.img.xz"
IMG_FILE="/tmp/raspios-lite.img.xz"
IMG_RAW="/tmp/raspios-lite.img"

echo "═══════════════════════════════════════════"
echo "  PiZero SD Card Preparation"
echo "═══════════════════════════════════════════"

# ── Step 1: Install tools ─────────────────────────────────────
echo ""
echo "[1/7] Installing tools …"
sudo apt-get update -qq
sudo apt-get install -y -qq wget xz-utils parted dosfstools

# ── Step 2: Download Pi OS ────────────────────────────────────
echo ""
echo "[2/7] Downloading Raspberry Pi OS Lite (arm64) …"
if [ -f "$IMG_RAW" ]; then
    echo "  → Already downloaded: $IMG_RAW"
else
    wget -q --show-progress -O "$IMG_FILE" "$IMG_URL"
    echo "  Decompressing …"
    xz -dkf "$IMG_FILE"
    mv "${IMG_FILE%.xz}" "$IMG_RAW" 2>/dev/null || true
fi

# ── Step 3: Find SD card ─────────────────────────────────────
echo ""
echo "[3/7] Finding SD card …"
if [ -z "$SD_DEVICE" ]; then
    echo "  Available block devices:"
    lsblk -d -o NAME,SIZE,MODEL,TRAN | grep -v "^loop"
    echo ""
    read -rp "  Enter SD card device (e.g. sdb): " sd_input
    SD_DEVICE="/dev/$sd_input"
fi

echo ""
echo "  ┌──────────────────────────────────────────────┐"
echo "  │  WARNING: ALL DATA ON $SD_DEVICE WILL BE LOST  │"
echo "  └──────────────────────────────────────────────┘"
echo ""
lsblk "$SD_DEVICE"
echo ""
read -rp "  Type YES to continue: " confirm
if [ "$confirm" != "YES" ]; then
    echo "Aborted."
    exit 1
fi

# Unmount any mounted partitions
echo "  Unmounting …"
sudo umount "${SD_DEVICE}"* 2>/dev/null || true

# ── Step 4: Flash image ──────────────────────────────────────
echo ""
echo "[4/7] Flashing image to $SD_DEVICE …"
sudo dd if="$IMG_RAW" of="$SD_DEVICE" bs=4M status=progress conv=fsync
sudo sync
echo "  ✓ Flash complete"

# Re-read partition table
sudo partprobe "$SD_DEVICE" 2>/dev/null || true
sleep 2

# ── Step 5: Mount boot partition ──────────────────────────────
echo ""
echo "[5/7] Configuring headless boot …"
BOOT_PART="${SD_DEVICE}1"
# Some systems use p1 for partition
[ -b "$BOOT_PART" ] || BOOT_PART="${SD_DEVICE}p1"

BOOT_MNT="/tmp/piboot"
sudo mkdir -p "$BOOT_MNT"
sudo mount "$BOOT_PART" "$BOOT_MNT"

# Enable SSH
sudo touch "$BOOT_MNT/ssh"

# WiFi config
sudo tee "$BOOT_MNT/wpa_supplicant.conf" > /dev/null <<EOF
country=$WIFI_COUNTRY
ctrl_interface=DIR=/var/run/wpa_supplicant GROUP=netdev
update_config=1

network={
    ssid="$WIFI_SSID"
    psk="$WIFI_PASSWORD"
}
EOF

# Create user pi with specified password
HASH=$(openssl passwd -6 "$PI_PASSWORD")
echo "pi:$HASH" | sudo tee "$BOOT_MNT/userconf.txt" > /dev/null

echo "  ✓ SSH enabled"
echo "  ✓ WiFi configured ($WIFI_SSID)"
echo "  ✓ User pi created"

sudo umount "$BOOT_MNT"

# ── Step 6: Mount root partition, install first-boot ──────────
echo ""
echo "[6/7] Installing first-boot provisioner …"
ROOT_PART="${SD_DEVICE}2"
[ -b "$ROOT_PART" ] || ROOT_PART="${SD_DEVICE}p2"

ROOT_MNT="/tmp/piroot"
sudo mkdir -p "$ROOT_MNT"
sudo mount "$ROOT_PART" "$ROOT_MNT"

# Copy firstboot script
sudo cp "$SCRIPT_DIR/firstboot.sh" "$ROOT_MNT/usr/local/bin/firstboot.sh"
sudo chmod +x "$ROOT_MNT/usr/local/bin/firstboot.sh"

# Create systemd service for first boot
sudo tee "$ROOT_MNT/etc/systemd/system/firstboot.service" > /dev/null <<EOF
[Unit]
Description=PiZero First Boot Provisioning
After=network-online.target
Wants=network-online.target
ConditionPathExists=/usr/local/bin/firstboot.sh

[Service]
Type=oneshot
ExecStart=/usr/local/bin/firstboot.sh
RemainAfterExit=true

[Install]
WantedBy=multi-user.target
EOF

# Enable the service
sudo ln -sf /etc/systemd/system/firstboot.service \
    "$ROOT_MNT/etc/systemd/system/multi-user.target.wants/firstboot.service"

echo "  ✓ First-boot service installed"

sudo umount "$ROOT_MNT"

# ── Step 7: Done ──────────────────────────────────────────────
echo ""
echo "[7/7] Cleanup …"
sudo sync

echo ""
echo "═══════════════════════════════════════════"
echo "  ✓ SD card ready!"
echo ""
echo "  Next steps:"
echo "  1. Insert SD card into Pi Zero"
echo "  2. Power on – wait ~5 min for first boot"
echo "  3. SSH in:  ssh pi@raspberrypi.local"
echo "     Password: $PI_PASSWORD"
echo "  4. Edit config:"
echo "     nano ~/PiZero/capture/config.env"
echo "  5. Start capture:"
echo "     sudo systemctl start picapture"
echo "═══════════════════════════════════════════"
