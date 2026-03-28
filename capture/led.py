"""
LED / GPIO helper for visual status feedback.

Patterns:
    idle_blink  – slow pulse (scanning for QR)
    connected   – long flash (QR scanned, session linked)
    on          – solid (recording)
    upload_blink – fast blink (uploading)
    error_flash – triple rapid flash
    blink       – generic short blinks

Uses RPi.GPIO when available, otherwise falls back to no-op so the rest
of the system keeps working during development.
"""

import logging
import threading
import time

from . import config

log = logging.getLogger(__name__)

_gpio = None
_idle_thread = None
_idle_stop = threading.Event()

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
    """LED solid on – indicates recording."""
    _stop_idle()
    if _gpio:
        _gpio.output(config.LED_PIN, _gpio.HIGH)


def off():
    """LED off."""
    _stop_idle()
    if _gpio:
        _gpio.output(config.LED_PIN, _gpio.LOW)


def idle_blink():
    """Start slow background blink (1s on / 1s off) for idle/scanning state."""
    global _idle_thread
    _stop_idle()
    _idle_stop.clear()

    def _run():
        while not _idle_stop.is_set():
            if _gpio:
                _gpio.output(config.LED_PIN, _gpio.HIGH)
            if _idle_stop.wait(1.0):
                break
            if _gpio:
                _gpio.output(config.LED_PIN, _gpio.LOW)
            if _idle_stop.wait(1.0):
                break

    _idle_thread = threading.Thread(target=_run, daemon=True)
    _idle_thread.start()


def _stop_idle():
    """Stop the background idle blink if running."""
    global _idle_thread
    if _idle_thread and _idle_thread.is_alive():
        _idle_stop.set()
        _idle_thread.join(timeout=2)
    _idle_thread = None


def connected_flash():
    """Long flash – QR scanned, session connected."""
    _stop_idle()
    if _gpio:
        _gpio.output(config.LED_PIN, _gpio.HIGH)
    time.sleep(0.8)
    if _gpio:
        _gpio.output(config.LED_PIN, _gpio.LOW)


def upload_blink():
    """Fast blink – uploading."""
    _stop_idle()
    if not _gpio:
        return
    for _ in range(10):
        _gpio.output(config.LED_PIN, _gpio.HIGH)
        time.sleep(0.1)
        _gpio.output(config.LED_PIN, _gpio.LOW)
        time.sleep(0.1)


def error_flash():
    """Triple rapid flash – something went wrong."""
    _stop_idle()
    if not _gpio:
        return
    for _ in range(3):
        _gpio.output(config.LED_PIN, _gpio.HIGH)
        time.sleep(0.08)
        _gpio.output(config.LED_PIN, _gpio.LOW)
        time.sleep(0.08)


def blink(times: int = 3, interval: float = 0.2):
    """Generic blink pattern."""
    _stop_idle()
    if not _gpio:
        return
    for _ in range(times):
        _gpio.output(config.LED_PIN, _gpio.HIGH)
        time.sleep(interval)
        _gpio.output(config.LED_PIN, _gpio.LOW)
        time.sleep(interval)


def cleanup():
    _stop_idle()
    if _gpio:
        _gpio.cleanup()
