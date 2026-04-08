"""
Optional push-button support (GPIO).

Detects short press, long press (3s), and very-long press (8s).
No-ops gracefully when USE_BUTTON is false or no GPIO backend is available.

Uses gpiozero (works on Debian 13 / Trixie with lgpio).
Falls back to RPi.GPIO on older systems.

Default wiring: GPIO3 (BCM 3 / physical pin 5), active-low with internal pull-up.
"""

import logging
import threading
import time

from . import config

log = logging.getLogger(__name__)

_btn = None  # gpiozero Button or fallback wrapper
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
    global _btn, _enabled
    if not config.USE_BUTTON:
        log.debug("Button disabled (USE_BUTTON=false)")
        return

    # Try gpiozero first
    try:
        from gpiozero import Button as _GpioButton  # type: ignore[import-untyped]
        _btn = _GpioButton(
            config.BUTTON_PIN,
            pull_up=config.BUTTON_ACTIVE_LOW,
            bounce_time=_DEBOUNCE_S,
        )
        _enabled = True
        log.info("Button enabled (gpiozero) on GPIO%d (active_low=%s)",
                 config.BUTTON_PIN, config.BUTTON_ACTIVE_LOW)
        return
    except Exception:
        pass

    # Fallback to RPi.GPIO
    try:
        import RPi.GPIO as _GPIO  # type: ignore[import-untyped]

        class _FallbackButton:
            """Minimal adapter matching the interface we need via RPi.GPIO."""
            def __init__(self, pin, active_low):
                self._pin = pin
                self._active_low = active_low
                _GPIO.setwarnings(False)
                _GPIO.setmode(_GPIO.BCM)
                pull = _GPIO.PUD_UP if active_low else _GPIO.PUD_DOWN
                _GPIO.setup(pin, _GPIO.IN, pull_up_down=pull)
            @property
            def is_pressed(self):
                val = _GPIO.input(self._pin)
                return (val == _GPIO.LOW) if self._active_low else (val == _GPIO.HIGH)
            def close(self):
                _GPIO.cleanup(self._pin)

        _btn = _FallbackButton(config.BUTTON_PIN, config.BUTTON_ACTIVE_LOW)
        _enabled = True
        log.info("Button enabled (RPi.GPIO fallback) on GPIO%d (active_low=%s)",
                 config.BUTTON_PIN, config.BUTTON_ACTIVE_LOW)
    except Exception:
        log.debug("No GPIO backend available – button support disabled")


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
    if not _btn:
        return False
    return _btn.is_pressed


def _poll_loop():
    """Poll the button and classify press duration."""
    from . import buzzer as _buzzer

    while not _monitor_stop.is_set():
        if not _is_pressed():
            _monitor_stop.wait(0.05)
            continue

        # Button is pressed – measure how long, with live feedback
        press_start = time.monotonic()
        long_signalled = False
        vlong_signalled = False

        while _is_pressed() and not _monitor_stop.is_set():
            held = time.monotonic() - press_start

            # Feedback at long-press threshold (3s)
            if not long_signalled and held >= LONG_PRESS_S:
                long_signalled = True
                _buzzer.beep(0.1)
                log.debug("Button: long-press threshold reached")

            # Feedback at very-long-press threshold (8s) – chord signals halt is committed
            if not vlong_signalled and held >= VLONG_PRESS_S:
                vlong_signalled = True
                _buzzer.chord_down()
                log.debug("Button: very-long-press threshold reached")

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
    if _btn:
        try:
            _btn.close()
        except Exception:
            pass
