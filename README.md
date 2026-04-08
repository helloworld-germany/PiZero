# PiZero Capture

Raspberry Pi Zero capture frontend for **vidaugment** (20sVA).

The Pi acts as an autonomous recording device that replaces the phone/browser capture flow:

```
┌─────────────────────────────────────────────────────┐
│  IDLE (low-power QR scan)                           │
│  Camera at 480×480 @ 15 fps, scanning for QR code   │
│  from viewer.html                                   │
└──────────────────┬──────────────────────────────────┘
                   │ QR detected → masterSessionId
                   ▼
┌─────────────────────────────────────────────────────┐
│  CAPTURE (high-performance)                         │
│  720×1280 @ 30 fps video + ALSA audio, 20 seconds   │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────┐
│  UPLOAD  →  POST /api/uploadVideo                   │
│  FINISH  →  POST /api/masterSession/<id>/finish     │
└──────────────────┬──────────────────────────────────┘
                   │
                   ▼  back to IDLE
```

## Hardware

| Component | Notes |
|-----------|-------|
| Raspberry Pi Zero 2 W | Also works on Pi 3/4/5 |
| Pi Camera Module 3 | Connected via ribbon cable |
| I2S microphone (INMP441) | Default: via Google voiceHAT overlay on `hw:0,0` |
| USB microphone (alternative) | Plugs in as `hw:1,0` |
| Status LED (optional) | GPIO 17, simple on/off feedback |
| Piezo buzzer (optional) | GPIO 23, tonal PWM feedback |

### Wiring (optional)

```
Pi GPIO 17 ──┤330Ω├── LED (+) ── GND
Pi GPIO 23 ────────── Buzzer (+) ── GND

I2S INMP441:
  BCLK/SCK  → GPIO 18 (pin 12)
  WS/LRCLK  → GPIO 19 (pin 35)
  SD/DOUT   → GPIO 20 (pin 38)
  VDD       → 3.3 V
  GND       → GND
  L/R       → GND (left channel, mono)
```

- **LED**: Any standard 3mm/5mm LED with a 330Ω resistor in series. Long leg (anode) toward the resistor, short leg (cathode) to GND.
- **Buzzer**: Passive piezo buzzer (PWM-driven for tonal feedback). `+` to GPIO, `−` to GND. No resistor needed.
- **I2S mic**: INMP441 or SPH0645. Directly soldered, no external board needed. Exposed via the `googlevoicehat-soundcard` overlay.

LED and buzzer are optional — the software no-ops gracefully without them.

## Quick Start

```bash
# 1. Clone onto the Pi
git clone https://github.com/helloworld-germany/PiZero.git
cd PiZero

# 2. Run setup (will prompt for sudo where needed)
./setup.sh

# 3. Reboot if prompted (needed for I2S audio / tmpfs)
#    After reboot the system is ready to run.

# 4. Run
source ~/.venvs/picapture/bin/activate
python -m capture
```

## Configuration

All settings live in `capture/config.env` (or as environment variables):

| Variable | Default | Description |
|----------|---------|-------------|
| `VIDAUGMENT_API_BASE_URL` | — | Backend URL (required) |
| `RECORD_DURATION_S` | `20` | Capture length in seconds |
| `VIDEO_WIDTH` / `VIDEO_HEIGHT` | `720` / `1280` | Capture resolution |
| `VIDEO_FPS` | `30` | Capture frame rate |
| `VIDEO_BITRATE` | `2000000` | H.264 target bitrate in bits per second |
| `QR_SCAN_WIDTH` / `QR_SCAN_HEIGHT` | `480` / `480` | QR scanner resolution |
| `QR_SCAN_FPS` | `15` | Idle scanner frame rate |
| `MIC_TYPE` | `i2s` | Microphone: `i2s`, `usb`, or `none` |
| `CAPTURE_DIR` | `/run/picapture` | Capture directory (tmpfs RAM disk) |
| `PAUSE_IDLE_TIMEOUT_S` | `60` | Auto-end session after this many seconds paused |
| `LED_PIN` | `17` | BCM GPIO pin for status LED |
| `BUZZER_PIN` | `23` | BCM GPIO pin for piezo buzzer |

## Capture

Recording uses `rpicam-vid` (Bookworm rpicam-apps) with native hardware
muxing: H.264 encoding + ALSA audio capture + MKV container — all in one
binary with **near-zero CPU** usage.

The default capture profile is portrait (`720x1280`). This is valid for
`rpicam-vid`; width does not need to be greater than height. It only affects
the encoded frame dimensions and resulting aspect ratio, not capture stability.

```
rpicam-vid -t 10 --audio-source alsa --audio-device plughw:0,0 \
           --audio-channels 1 --audio-samplerate 48000 -o output.mkv
```

The microphone is selected by `MIC_TYPE` in `capture/config.env`:

| `MIC_TYPE` | ALSA device | Description |
|------------|-------------|-------------|
| `i2s` | `plughw:0,0` | I2S mic via googlevoicehat overlay (default) |
| `usb` | `plughw:<N>,0` | USB microphone (auto-detected card number) |
| `none` | — | No audio |

Audio normalization is handled in the cloud after upload.
Run `arecord -l` to list available capture devices.

## Audio Hardware Configuration

### I2S microphone (default)

Enable the Google voiceHAT overlay in `/boot/firmware/config.txt`
(or `/boot/config.txt` on older images):

```
dtparam=i2s=on
dtoverlay=googlevoicehat-soundcard
```

Reboot, then verify:

```bash
arecord -l
# card 0: sndrpigooglevoi [...], device 0: ...
```

Test recording:

```bash
arecord -D plughw:0,0 -f S32_LE -r 48000 -c 2 -d 3 test.wav
aplay test.wav
```

The raw I2S audio will be quiet – this is normal. Volume normalization
happens server-side after upload.

### USB microphone (alternative)

Plug in a USB mic and check:

```bash
arecord -l
# card 1: ..., device 0: ...
```

Then configure:

```bash
MIC_TYPE=usb
```

## RAM Disk for Recordings

By default, recordings are written to `/run/picapture`, a **tmpfs** RAM disk.
This avoids SD card I/O during capture, resulting in significantly faster
writes and reduced SD card wear. The `setup.sh` script adds the mount
automatically via `/etc/fstab`:

```
tmpfs /run/picapture tmpfs nodev,nosuid,size=200M 0 0
```

The 200 MB size is sufficient for a single 20-second recording cycle. Files are
uploaded and cleaned up before the next capture, so usage stays low. Since tmpfs
lives in RAM, **data is lost on reboot** — this is fine because recordings are
uploaded immediately after capture.

## WiFi

Configure WiFi via `raspi-config` or by editing
`/etc/wpa_supplicant/wpa_supplicant.conf`:

```
network={
    ssid="YourNetwork"
    psk="YourPassword"
}
```

## Auto-Start on Boot

The setup script can install a systemd service:

```bash
sudo systemctl enable picapture
sudo systemctl start picapture
sudo journalctl -u picapture -f   # view logs
```

## Project Structure

```
capture/
  __init__.py
  __main__.py       # python -m capture entry point
  main.py           # state machine orchestrator
  config.py         # settings from config.env / env vars
  config.env        # editable configuration
  camera.py         # picamera2 QR-scan mode
  qr_scanner.py     # low-power QR detection (pyzbar)
  mic.py            # audio device selection (I2S / ALSA / auto)
  recorder.py       # rpicam-vid native H.264+audio capture
  uploader.py       # HTTP upload & session finish
  led.py            # GPIO LED feedback
  buzzer.py         # GPIO piezo buzzer (PWM tonal feedback)
setup.sh            # one-shot Pi setup (sudo required)
requirements.txt    # Python dependencies
```
