"""
Buzzer / GPIO helper for audio feedback.

Uses an active piezo buzzer on BUZZER_PIN (HIGH = sound, LOW = silent).
No-ops gracefully during development without hardware.
"""

import logging
import time

from . import config

log = logging.getLogger(__name__)

_gpio = None
try:
    import RPi.GPIO as GPIO  # type: ignore[import-untyped]
    _gpio = GPIO
    log.debug("Using GPIO buzzer on pin %s", config.BUZZER_PIN)
except ImportError:
    log.debug("RPi.GPIO not available – buzzer feedback disabled")


def setup():
    if _gpio:
        _gpio.setwarnings(False)
        _gpio.setmode(_gpio.BCM)
        _gpio.setup(config.BUZZER_PIN, _gpio.OUT, initial=_gpio.LOW)


def beep(duration: float = 0.15):
    """Single short beep."""
    if _gpio:
        _gpio.output(config.BUZZER_PIN, _gpio.HIGH)
        time.sleep(duration)
        _gpio.output(config.BUZZER_PIN, _gpio.LOW)


def double_beep():
    """Two quick beeps – upload complete / success."""
    beep(0.1)
    time.sleep(0.1)
    beep(0.1)


def error_beep():
    """Three rapid beeps – something went wrong."""
    for _ in range(3):
        beep(0.08)
        time.sleep(0.08)


def cleanup():
    if _gpio:
        _gpio.output(config.BUZZER_PIN, _gpio.LOW)
