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
    L/R       -> GND (left channel, mono) or 3.3V (right)
"""

import logging
import subprocess

from . import config

log = logging.getLogger(__name__)


def _list_capture_cards() -> list[tuple[int, str]]:
    """Return ``[(card_number, description), ...]`` from ``arecord -l``."""
    try:
        result = subprocess.run(
            ["arecord", "-l"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        cards = []
        for line in result.stdout.decode(errors="replace").splitlines():
            if line.startswith("card "):
                card_num = int(line.split(":")[0].split()[1])
                cards.append((card_num, line))
        return cards
    except Exception:
        return []


def _find_card(pattern: str) -> int | None:
    """Return the card number of the first capture card whose description matches *pattern*."""
    for num, desc in _list_capture_cards():
        if pattern.lower() in desc.lower():
            return num
    return None


def has_audio_input() -> bool:
    """Return True if *any* audio capture device is available."""
    return preferred_audio_device() is not None


def preferred_audio_device() -> str | None:
    """Return the ALSA device string to use for recording, or None.

    MIC_TYPE selects the device:
      - "i2s":  I2S mic via plughw (default) – looks for googlevoicehat / I2S card
      - "usb":  USB microphone – scans for a USB capture card
      - "none": no microphone
    """
    mic_type = config.MIC_TYPE

    if mic_type == "none":
        log.info("MIC_TYPE=none – audio disabled")
        return None

    if mic_type == "i2s":
        card = _find_card("googlevoice")
        if card is None:
            card = _find_card("i2s")
        if card is not None:
            device = f"plughw:{card},0"
            log.info("I2S mic selected – using %s", device)
            return device
        # Fall back: if any card exists, assume card 0 is the I2S device
        if _list_capture_cards():
            log.warning("I2S mic selected, no googlevoicehat card found – falling back to plughw:0,0")
            return "plughw:0,0"
        log.warning("I2S mic selected but no capture card found")
        return None

    if mic_type == "usb":
        card = _find_card("usb")
        if card is not None:
            device = f"plughw:{card},0"
            log.info("USB mic selected – using %s", device)
            return device
        log.warning("USB mic selected but no USB capture card found")
        return None

    log.error("Unknown MIC_TYPE=%r – treating as none", mic_type)
    return None
