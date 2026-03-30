"""
Configuration for the PiZero capture system.

Settings are loaded from environment variables or capture/config.env.
"""

import os
from pathlib import Path

_env_file = Path(__file__).parent / "config.env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key:
            os.environ.setdefault(key, value)

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
API_BASE_URL = os.environ.get("VIDAUGMENT_API_BASE_URL", "").rstrip("/")

# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------
RECORD_DURATION_S = int(os.environ.get("RECORD_DURATION_S", "20"))

# Video
VIDEO_WIDTH = int(os.environ.get("VIDEO_WIDTH", "720"))
VIDEO_HEIGHT = int(os.environ.get("VIDEO_HEIGHT", "1280"))
VIDEO_FPS = int(os.environ.get("VIDEO_FPS", "30"))

# QR scanner (fast scan mode)
QR_SCAN_WIDTH = int(os.environ.get("QR_SCAN_WIDTH", "480"))
QR_SCAN_HEIGHT = int(os.environ.get("QR_SCAN_HEIGHT", "480"))
QR_SCAN_FPS = int(os.environ.get("QR_SCAN_FPS", "15"))

# Audio device (ALSA hw id, e.g. "hw:1,0" for USB mic)
AUDIO_DEVICE = os.environ.get("AUDIO_DEVICE", "default")
AUDIO_SAMPLE_RATE = int(os.environ.get("AUDIO_SAMPLE_RATE", "44100"))

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CAPTURE_DIR = Path(os.environ.get("CAPTURE_DIR", "/tmp/picapture"))

# ---------------------------------------------------------------------------
# LED / GPIO feedback (optional)
# ---------------------------------------------------------------------------
LED_PIN = int(os.environ.get("LED_PIN", "17"))  # BCM numbering

# ---------------------------------------------------------------------------
# Buzzer (optional hardware board)
# ---------------------------------------------------------------------------
USE_BUZZER = os.environ.get("USE_BUZZER", "false").lower() in ("1", "true", "yes")
BUZZER_PIN = int(os.environ.get("BUZZER_PIN", "23"))  # BCM 23 / physical pin 16
BUZZER_PWM = os.environ.get("BUZZER_PWM", "false").lower() in ("1", "true", "yes")
BUZZER_FREQUENCY = int(os.environ.get("BUZZER_FREQUENCY", "1000"))

# ---------------------------------------------------------------------------
# Push-button (optional hardware board)
# ---------------------------------------------------------------------------
USE_BUTTON = os.environ.get("USE_BUTTON", "false").lower() in ("1", "true", "yes")
BUTTON_PIN = int(os.environ.get("BUTTON_PIN", "3"))  # BCM 3 / physical pin 5
BUTTON_ACTIVE_LOW = os.environ.get("BUTTON_ACTIVE_LOW", "true").lower() in ("1", "true", "yes")

# ---------------------------------------------------------------------------
# I2S microphone (optional hardware board)
#   BCLK/SCK: GPIO18, WS/LRCLK: GPIO19, SD/DOUT: GPIO20
#   VDD: 3.3V, GND: GND
# ---------------------------------------------------------------------------
USE_I2S_MIC = os.environ.get("USE_I2S_MIC", "false").lower() in ("1", "true", "yes")
I2S_AUDIO_DEVICE = os.environ.get("I2S_AUDIO_DEVICE", "default")

# Audio backend selection: "auto", "alsa", "i2s"
AUDIO_BACKEND = os.environ.get("AUDIO_BACKEND", "auto")

# ---------------------------------------------------------------------------
# Session limits
# ---------------------------------------------------------------------------
HARD_TIMEOUT_S = int(os.environ.get("HARD_TIMEOUT_S", "1800"))  # 30 minutes

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
