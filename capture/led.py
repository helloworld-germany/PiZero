"""
LED / GPIO helper for visual status feedback.

Patterns:
    idle_blink  – slow pulse (scanning for QR)
    connected   – long flash (QR scanned, session linked)
    on          – solid (recording)
    upload_blink – fast blink (uploading)
    error_flash – triple rapid flash
    blink       – generic short blinks

Uses the onboard ACT LED via /sys/class/leds/ on Pi Zero 2 W.
Falls back to RPi.GPIO on an external LED if ACT is unavailable.
No-ops gracefully during development without hardware.
"""

import logging
import threading
import time
from pathlib import Path

from . import config

log = logging.getLogger(__name__)

_idle_thread = None
_idle_stop = threading.Event()

# ── LED backend detection ─────────────────────────────────────────
# Prefer onboard ACT LED (no wiring needed), fall back to GPIO
_act_path = None
_gpio = None

for _candidate in [
    Path("/sys/class/leds/ACT"),
    Path("/sys/class/leds/led0"),
]:
    if _candidate.is_dir():
        _act_path = _candidate
        break

if not _act_path:
    try:
        import RPi.GPIO as GPIO  # type: ignore[import-untyped]
        _gpio = GPIO
    except ImportError:
        pass

if _act_path:
    log.debug("Using onboard ACT LED at %s", _act_path)
elif _gpio:
    log.debug("Using GPIO LED on pin %s", config.LED_PIN)
else:
    log.debug("No LED backend available – feedback disabled")


def _act_write(value: str):
    """Write to the ACT LED sysfs interface."""
    try:
        (_act_path / "brightness").write_text(value)
    except OSError:
        pass


def setup():
    if _act_path:
        # Take control away from the default mmc0 trigger
        try:
            (_act_path / "trigger").write_text("none")
        except OSError:
            pass
        _act_write("0")
    elif _gpio:
        _gpio.setwarnings(False)
        _gpio.setmode(_gpio.BCM)
        _gpio.setup(config.LED_PIN, _gpio.OUT, initial=_gpio.LOW)


def on():
    """LED solid on – indicates recording."""
    _stop_idle()
    if _act_path:
        _act_write("1")
    elif _gpio:
        _gpio.output(config.LED_PIN, _gpio.HIGH)


def off():
    """LED off."""
    _stop_idle()
    if _act_path:
        _act_write("0")
    elif _gpio:
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
    if _act_path:
        _act_write("1")
    elif _gpio:
        _gpio.output(config.LED_PIN, _gpio.HIGH)


def _led_low():
    if _act_path:
        _act_write("0")
    elif _gpio:
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
    if _act_path:
        # Restore default kernel trigger
        try:
            (_act_path / "trigger").write_text("mmc0")
        except OSError:
            pass
    elif _gpio:
        _gpio.cleanup()
