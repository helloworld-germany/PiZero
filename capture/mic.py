"""
Microphone capability detector / audio device selector.

Helps recorder.py choose the right ALSA capture device based on
MIC_TYPE config: "i2s" (default), "usb", or "none".

Does NOT record audio itself – that remains the job of recorder.py.

Common I2S microphone wiring (e.g. INMP441, SPH0645):
    BCLK/SCK  -> GPIO18 / physical pin 12
    WS/LRCLK  -> GPIO19 / physical pin 35
    SD/DOUT   -> GPIO20 / physical pin 38
    VDD       -> 3.3V
    GND       -> GND
    L/R       -> GND (left) or 3.3V (right)
"""

import logging
import subprocess

from . import config

log = logging.getLogger(__name__)


def _any_capture_card() -> bool:
    """Return True if arecord lists at least one capture card."""
    try:
        result = subprocess.run(
            ["arecord", "-l"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return b"card" in result.stdout
    except Exception:
        return False


def has_audio_input() -> bool:
    """Return True if *any* audio capture device is available."""
    return preferred_audio_device() is not None


def preferred_audio_device() -> str | None:
    """Return the ALSA device string to use for recording, or None.

    MIC_TYPE selects the device:
      - "i2s":  boosted I2S mic via plughw (default)
      - "usb":  standard USB / ALSA mic ("default" device)
      - "none": no microphone
    """
    mic_type = config.MIC_TYPE

    if mic_type == "none":
        log.info("MIC_TYPE=none – audio disabled")
        return None

    if mic_type == "i2s":
        if _any_capture_card():
            log.info("I2S mic selected, capture card found – using boosted_mic")
            return "boosted_mic"
        log.warning("I2S mic selected but no capture card found")
        return None

    if mic_type == "usb":
        if _any_capture_card():
            log.info("USB mic selected – using default ALSA device")
            return "default"
        log.warning("USB mic selected but no capture card found")
        return None

    log.error("Unknown MIC_TYPE=%r – treating as none", mic_type)
    return None
