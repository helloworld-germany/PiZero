"""
LED / GPIO helper for visual status feedback.

Patterns:
    idle_blink   – slow pulse (scanning for QR)
    connected    – long flash (QR scanned, session linked)
    on           – solid (recording)
    upload_blink – fast blink (uploading)
    error_flash  – triple rapid flash
    blink        – generic short blinks

Uses RPi.GPIO on an external LED (LED_PIN in config).
No-ops gracefully during development without hardware.
"""

import logging
import threading
import time

from . import config

log = logging.getLogger(__name__)

_idle_thread = None
_idle_stop = threading.Event()

# ── GPIO import ───────────────────────────────────────────────────
_gpio = None
try:
    import RPi.GPIO as GPIO  # type: ignore[import-untyped]
    _gpio = GPIO
    log.debug("Using GPIO LED on pin %s", config.LED_PIN)
except ImportError:
    log.debug("RPi.GPIO not available – LED feedback disabled")


def setup():
    if _gpio:
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
            _led_high()
            if _idle_stop.wait(1.0):
                break
            _led_low()
            if _idle_stop.wait(1.0):
                break

    _idle_thread = threading.Thread(target=_run, daemon=True)
    _idle_thread.start()


def _led_high():
    if _gpio:
        _gpio.output(config.LED_PIN, _gpio.HIGH)


def _led_low():
    if _gpio:
        _gpio.output(config.LED_PIN, _gpio.LOW)


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
    _led_high()
    time.sleep(0.8)
    _led_low()


def upload_blink():
    """Fast blink – uploading."""
    _stop_idle()
    for _ in range(10):
        _led_high()
        time.sleep(0.1)
        _led_low()
        time.sleep(0.1)


def error_flash():
    """Triple rapid flash – something went wrong."""
    _stop_idle()
    for _ in range(3):
        _led_high()
        time.sleep(0.08)
        _led_low()
        time.sleep(0.08)


def blink(times: int = 3, interval: float = 0.2):
    """Generic blink pattern."""
    _stop_idle()
    for _ in range(times):
        _led_high()
        time.sleep(interval)
        _led_low()
        time.sleep(interval)


def cleanup():
    _stop_idle()
    if _gpio:
        _gpio.cleanup()
