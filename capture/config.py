"""
Configuration for the PiZero capture system.

Settings are loaded from environment variables or capture/config.env.
"""

import hashlib
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
# Device
# ---------------------------------------------------------------------------
def _get_device_id() -> str:
    """Stable device ID from Pi serial number."""
    try:
        serial = open("/proc/cpuinfo").read()
        for line in serial.splitlines():
            if line.startswith("Serial"):
                sn = line.split(":")[1].strip()
                return "pi-" + hashlib.sha256(sn.encode()).hexdigest()[:12]
    except Exception:
        pass
    import uuid
    return "pi-" + uuid.getnode().to_bytes(6, "big").hex()

DEVICE_ID = _get_device_id()

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
VIDEO_BITRATE = int(os.environ.get("VIDEO_BITRATE", "2000000"))

# QR scanner (fast scan mode)
QR_SCAN_WIDTH = int(os.environ.get("QR_SCAN_WIDTH", "480"))
QR_SCAN_HEIGHT = int(os.environ.get("QR_SCAN_HEIGHT", "480"))
QR_SCAN_FPS = int(os.environ.get("QR_SCAN_FPS", "15"))

# Microphone type: "i2s" (default, boosted I2S), "usb", or "none"
MIC_TYPE = os.environ.get("MIC_TYPE", "i2s").lower().strip()

# Audio capture (independent arecord, uploaded raw)
AUDIO_FORMAT = os.environ.get("AUDIO_FORMAT", "S32_LE")
AUDIO_SAMPLE_RATE = int(os.environ.get("AUDIO_SAMPLE_RATE", "48000"))
AUDIO_CHANNELS = int(os.environ.get("AUDIO_CHANNELS", "2"))

# Audio upload preprocessing (applied only to WAV uploads)
# LEFT_ONLY extracts channel-0 without averaging; GAIN_DB applies software gain
# right before upload in the background uploader thread.
AUDIO_UPLOAD_LEFT_ONLY = os.environ.get("AUDIO_UPLOAD_LEFT_ONLY", "true").lower() in (
    "1", "true", "yes"
)
AUDIO_UPLOAD_GAIN_DB = float(os.environ.get("AUDIO_UPLOAD_GAIN_DB", "0"))

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
CAPTURE_DIR = Path(os.environ.get("CAPTURE_DIR", "/run/picapture"))

# ---------------------------------------------------------------------------
# LED / GPIO feedback (optional)
# ---------------------------------------------------------------------------
LED_PIN = int(os.environ.get("LED_PIN", "17"))  # BCM numbering

# ---------------------------------------------------------------------------
# Buzzer (optional hardware board)
# ---------------------------------------------------------------------------
USE_BUZZER = os.environ.get("USE_BUZZER", "false").lower() in ("1", "true", "yes")
BUZZER_PIN = int(os.environ.get("BUZZER_PIN", "23"))  # BCM 23 / physical pin 16
BUZZER_PWM = os.environ.get("BUZZER_PWM", "true").lower() in ("1", "true", "yes")
BUZZER_FREQUENCY = 1000  # default tone Hz (chords use their own frequencies)

# ---------------------------------------------------------------------------
# Push-button (optional hardware board)
# ---------------------------------------------------------------------------
USE_BUTTON = os.environ.get("USE_BUTTON", "false").lower() in ("1", "true", "yes")
BUTTON_PIN = int(os.environ.get("BUTTON_PIN", "3"))  # BCM 3 / physical pin 5
BUTTON_ACTIVE_LOW = os.environ.get("BUTTON_ACTIVE_LOW", "true").lower() in ("1", "true", "yes")



# ---------------------------------------------------------------------------
# Session limits
# ---------------------------------------------------------------------------
HARD_TIMEOUT_S = int(os.environ.get("HARD_TIMEOUT_S", "1800"))  # 30 minutes
PAUSE_IDLE_TIMEOUT_S = int(os.environ.get("PAUSE_IDLE_TIMEOUT_S", "60"))  # auto-end session if paused this long

# ---------------------------------------------------------------------------
# Debug
# ---------------------------------------------------------------------------
DEBUG_SAVE_CHUNKS = os.environ.get("DEBUG_SAVE_CHUNKS", "false").lower() in ("1", "true", "yes")
DEBUG_SAVE_DIR = Path(os.environ.get("DEBUG_SAVE_DIR", str(Path.home() / "test-captures")))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
