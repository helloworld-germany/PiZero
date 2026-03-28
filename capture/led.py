"""
LED / GPIO helper for visual status feedback.

Uses RPi.GPIO when available, otherwise falls back to no-op so the rest
of the system keeps working during development.
"""

import logging

from . import config

log = logging.getLogger(__name__)

_gpio = None

try:
    import RPi.GPIO as GPIO  # type: ignore[import-untyped]
    _gpio = GPIO
except ImportError:
    log.debug("RPi.GPIO not available – LED feedback disabled")


def setup():
    if not _gpio:
        return
    _gpio.setwarnings(False)
    _gpio.setmode(_gpio.BCM)
    _gpio.setup(config.LED_PIN, _gpio.OUT, initial=_gpio.LOW)


def on():
    """LED on – indicates active / recording."""
    if _gpio:
        _gpio.output(config.LED_PIN, _gpio.HIGH)


def off():
    """LED off – indicates idle."""
    if _gpio:
        _gpio.output(config.LED_PIN, _gpio.LOW)


def blink(times: int = 3, interval: float = 0.2):
    """Quick blink pattern – indicates upload or transition."""
    import time
    for _ in range(times):
        on()
        time.sleep(interval)
        off()
        time.sleep(interval)


def cleanup():
    if _gpio:
        _gpio.cleanup()
