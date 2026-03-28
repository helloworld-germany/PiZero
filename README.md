# PiZero Capture

Raspberry Pi Zero capture frontend for **vidaugment** (20sVA).

The Pi acts as an autonomous recording device that replaces the phone/browser
`index.html` capture flow:

```
┌─────────────────────────────────────────────────────┐
│  IDLE (low-power QR scan)                           │
│  Camera at 640×480 @ 5 fps, scanning for QR code    │
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
| USB microphone | Any ALSA-compatible device |
| Status LED (optional) | GPIO 17, simple on/off feedback |
| Active buzzer (optional) | GPIO 27, audio beep feedback |

### Wiring (optional)

```
Pi GPIO 17 ──┤330Ω├── LED (+) ── GND
Pi GPIO 27 ────────── Buzzer (+) ── GND
```

- **LED**: Any standard 3mm/5mm LED with a 330Ω resistor in series. Long leg (anode) toward the resistor, short leg (cathode) to GND.
- **Buzzer**: Active buzzer (has built-in oscillator, just needs HIGH/LOW). `+` to GPIO, `−` to GND. No resistor needed.

Both are optional — the software no-ops gracefully without them.

## Quick Start

```bash
# 1. Clone onto the Pi
git clone https://github.com/helloworld-germany/PiZero.git
cd PiZero

# 2. Run setup (installs deps, creates venv, optionally enables systemd)
./setup.sh

# 3. Configure the API endpoint
#    Edit capture/config.env and set VIDAUGMENT_API_BASE_URL

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
| `QR_SCAN_WIDTH` / `QR_SCAN_HEIGHT` | `640` / `480` | QR scanner resolution |
| `QR_SCAN_FPS` | `5` | Idle scanner frame rate |
| `AUDIO_DEVICE` | `default` | ALSA device (`arecord -l` to list) |
| `LED_PIN` | `17` | BCM GPIO pin for status LED |
| `BUZZER_PIN` | `27` | BCM GPIO pin for active buzzer |

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
  camera.py         # picamera2 mode switching
  qr_scanner.py     # low-power QR detection (pyzbar)
  recorder.py       # 20s video+audio capture + ffmpeg mux
  uploader.py       # HTTP upload & session finish
  led.py            # GPIO LED feedback
  buzzer.py         # GPIO active buzzer feedback
setup.sh            # one-shot Pi setup
requirements.txt    # Python dependencies
```
