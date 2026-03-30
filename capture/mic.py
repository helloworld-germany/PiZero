"""
I2S microphone capability detector / audio device selector.

Helps recorder.py choose the right ALSA capture device based on
configuration (AUDIO_BACKEND, USE_I2S_MIC, I2S_AUDIO_DEVICE).

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


def _alsa_device_available(device: str) -> bool:
    """Return True if *device* appears usable as an ALSA capture source."""
    # I2S mics (e.g. INMP441) require specific format/rate/channels.
    # Try several combinations to find one that works.
    probes = [
        ("-f", "S32_LE", "-r", "48000", "-c", "2"),
        ("-f", "S32_LE", "-r", "16000", "-c", "1"),
        ("-f", "S16_LE", "-r", "16000", "-c", "1"),
        ("-f", "S16_LE", "-r", "44100", "-c", "1"),
    ]
    for params in probes:
        try:
            result = subprocess.run(
                ["arecord", "-D", device, "-d", "0", *params, "/dev/null"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass
    return False


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
    device = preferred_audio_device()
    if device is None:
        return False
    return True


def preferred_audio_device() -> str | None:
    """
    Return the ALSA device string to use for recording, or None.

    Selection logic based on AUDIO_BACKEND config:
      - "i2s":  use I2S device; None if unavailable
      - "alsa": use AUDIO_DEVICE; None if unavailable
      - "auto": prefer I2S if enabled & available, else fall back to ALSA
    """
    backend = config.AUDIO_BACKEND.lower().strip()

    if backend == "i2s":
        return _try_i2s()

    if backend == "alsa":
        return _try_alsa()

    # auto
    if config.USE_I2S_MIC:
        dev = _try_i2s()
        if dev is not None:
            return dev
        log.info("I2S mic enabled but not available – falling back to ALSA")

    return _try_alsa()


def _try_i2s() -> str | None:
    if not config.USE_I2S_MIC:
        log.debug("I2S mic not enabled")
        return None
    dev = config.I2S_AUDIO_DEVICE
    if _alsa_device_available(dev):
        # Return plughw: variant so ffmpeg can auto-convert format/rate
        plug_dev = dev.replace("hw:", "plughw:") if dev.startswith("hw:") else dev
        log.info("I2S audio device available: %s (using %s)", dev, plug_dev)
        return plug_dev
    log.warning("I2S audio device '%s' not available", dev)
    return None


def _try_alsa() -> str | None:
    dev = config.AUDIO_DEVICE
    if _alsa_device_available(dev):
        log.debug("ALSA audio device available: %s", dev)
        return dev
    # Fall back to checking if any card exists at all
    if _any_capture_card():
        log.debug("ALSA device '%s' failed probe but cards exist – trying anyway", dev)
        return dev
    log.debug("No ALSA capture devices found")
    return None
