"""
Optional push-button support (GPIO).

Detects short press, long press (3s), and very-long press (8s).
No-ops gracefully when USE_BUTTON is false or RPi.GPIO is unavailable.

Default wiring: GPIO3 (BCM 3 / physical pin 5), active-low with internal pull-up.
"""

import logging
import threading
import time

from . import config

log = logging.getLogger(__name__)

_gpio = None
_enabled = False
_monitor_thread = None
_monitor_stop = threading.Event()

# Callback holders – set by the main module via register()
_on_short_press = None   # pause / resume
_on_long_press = None    # end session
_on_vlong_press = None   # shutdown

LONG_PRESS_S = 3.0
VLONG_PRESS_S = 8.0
_DEBOUNCE_S = 0.05


def setup():
    """Initialise GPIO for the button if enabled and available."""
    global _gpio, _enabled
    if not config.USE_BUTTON:
        log.debug("Button disabled (USE_BUTTON=false)")
        return

    try:
        import RPi.GPIO as GPIO  # type: ignore[import-untyped]
        _gpio = GPIO
    except ImportError:
        log.debug("RPi.GPIO not available – button support disabled")
        return

    _gpio.setwarnings(False)
    _gpio.setmode(_gpio.BCM)
    pull = _gpio.PUD_UP if config.BUTTON_ACTIVE_LOW else _gpio.PUD_DOWN
    _gpio.setup(config.BUTTON_PIN, _gpio.IN, pull_up_down=pull)
    _enabled = True
    log.info("Button enabled on GPIO%d (active_low=%s)", config.BUTTON_PIN, config.BUTTON_ACTIVE_LOW)


def register(on_short_press=None, on_long_press=None, on_vlong_press=None):
    """Register callbacks for button events."""
    global _on_short_press, _on_long_press, _on_vlong_press
    _on_short_press = on_short_press
    _on_long_press = on_long_press
    _on_vlong_press = on_vlong_press


def start_monitor():
    """Start a background thread that polls the button."""
    global _monitor_thread
    if not _enabled:
        return
    stop_monitor()
    _monitor_stop.clear()
    _monitor_thread = threading.Thread(target=_poll_loop, daemon=True)
    _monitor_thread.start()
    log.debug("Button monitor started")


def stop_monitor():
    """Stop the background polling thread."""
    global _monitor_thread
    if _monitor_thread and _monitor_thread.is_alive():
        _monitor_stop.set()
        _monitor_thread.join(timeout=2)
    _monitor_thread = None


def _is_pressed() -> bool:
    if not _gpio:
        return False
    val = _gpio.input(config.BUTTON_PIN)
    return (val == _gpio.LOW) if config.BUTTON_ACTIVE_LOW else (val == _gpio.HIGH)


def _poll_loop():
    """Poll the button and classify press duration."""
    while not _monitor_stop.is_set():
        if not _is_pressed():
            _monitor_stop.wait(0.05)
            continue

        # Button is pressed – measure how long
        press_start = time.monotonic()
        while _is_pressed() and not _monitor_stop.is_set():
            _monitor_stop.wait(_DEBOUNCE_S)

        duration = time.monotonic() - press_start
        if duration < _DEBOUNCE_S:
            continue  # noise

        if duration >= VLONG_PRESS_S:
            log.info("Button: very-long press (%.1fs)", duration)
            _fire(_on_vlong_press)
        elif duration >= LONG_PRESS_S:
            log.info("Button: long press (%.1fs)", duration)
            _fire(_on_long_press)
        else:
            log.info("Button: short press (%.1fs)", duration)
            _fire(_on_short_press)

        # Small cooldown to avoid double triggers
        _monitor_stop.wait(0.3)


def _fire(callback):
    if callback:
        try:
            callback()
        except Exception:
            log.exception("Button callback error")


def cleanup():
    stop_monitor()
