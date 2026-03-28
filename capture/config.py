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

# QR scanner (idle / low-power)
QR_SCAN_WIDTH = int(os.environ.get("QR_SCAN_WIDTH", "640"))
QR_SCAN_HEIGHT = int(os.environ.get("QR_SCAN_HEIGHT", "480"))
QR_SCAN_FPS = int(os.environ.get("QR_SCAN_FPS", "5"))
QR_SCAN_INTERVAL_S = float(os.environ.get("QR_SCAN_INTERVAL_S", "0.5"))

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
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
