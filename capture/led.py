"""
LED / GPIO helper for visual status feedback.

Patterns:
    idle_blink            – background breathing (scanning for QR)
    connected_flash       – long flash (QR scanned, session linked)
    run_breathe           – brighter breathing while recording
    shutdown_fade         – smooth fade out before halt
    long_press_confirm    – threshold confirmation for 3s hold
    very_long_press_confirm – threshold confirmation for 8s hold
    upload_blink          – fast blink (uploading)
    error_flash           – triple rapid flash
    blink                 – generic short blinks

Uses gpiozero (works on Debian 13 / Trixie with lgpio) on an external LED
(LED_PIN in config). Falls back to basic on/off LED control where PWM
brightness is unavailable.
No-ops gracefully during development without hardware.
"""

import logging
import socket
import threading
import time
from urllib.parse import urlparse

from . import config

log = logging.getLogger(__name__)

_pattern_thread = None
_pattern_stop = threading.Event()

# ── GPIO backend ──────────────────────────────────────────────────
_led = None  # gpiozero LED/PWMLED instance (or fallback)
_supports_pwm = False

try:
    from gpiozero import PWMLED as _GpioPWMLED  # type: ignore[import-untyped]

    _led = _GpioPWMLED(config.LED_PIN, initial_value=0)
    _supports_pwm = True
    log.debug("Using gpiozero PWMLED on pin %s", config.LED_PIN)
except Exception:
    try:
        from gpiozero import LED as _GpioLED  # type: ignore[import-untyped]

        _led = _GpioLED(config.LED_PIN, initial_value=False)
        _supports_pwm = False
        log.debug("Using gpiozero LED (on/off) on pin %s", config.LED_PIN)
    except Exception:
        try:
            import RPi.GPIO as _GPIO  # type: ignore[import-untyped]

            class _FallbackLED:
                """Minimal adapter matching gpiozero.LED interface via RPi.GPIO."""

                def __init__(self, pin):
                    self._pin = pin
                    _GPIO.setwarnings(False)
                    _GPIO.setmode(_GPIO.BCM)
                    _GPIO.setup(pin, _GPIO.OUT, initial=_GPIO.LOW)

                def on(self):
                    _GPIO.output(self._pin, _GPIO.HIGH)

                def off(self):
                    _GPIO.output(self._pin, _GPIO.LOW)

                def close(self):
                    _GPIO.cleanup(self._pin)

            _led = _FallbackLED(config.LED_PIN)
            _supports_pwm = False
            log.debug("Using RPi.GPIO fallback LED on pin %s", config.LED_PIN)
        except Exception:
            log.debug("No GPIO backend available – LED feedback disabled")


def setup():
    # Initialization already done at import time; kept for API compat.
    pass


def on():
    """LED solid on – indicates recording."""
    _stop_pattern()
    if _led:
        if _supports_pwm:
            _set_level(1.0)
        else:
            _led.on()


def off():
    """LED off."""
    _stop_pattern()
    if _led:
        _set_level(0.0)


def idle_blink():
    """Start background idle pattern for QR scanning.

    Periodically checks network connectivity to the API host.  When the
    network is unreachable the breathing pattern is interleaved with a triple
    error flash so the user can tell the Pi is online vs offline.
    Once connectivity returns the pattern reverts to normal breathing
    automatically.
    """
    global _pattern_thread
    _stop_pattern()
    _pattern_stop.clear()

    def _run():
        cycles_since_check = 0  # first check after a short grace period
        net_ok = True
        while not _pattern_stop.is_set():
            _breathe_cycle(min_level=0.02, max_level=0.45, steps=22, step_delay=0.03, stop_event=_pattern_stop)
            if _pattern_stop.is_set():
                break

            cycles_since_check += 1
            # Check connectivity every ~5 breathing cycles
            if cycles_since_check >= 5:
                cycles_since_check = 0
                net_ok = _check_network()

            # If offline, interleave a triple error flash
            if not net_ok:
                for _ in range(3):
                    _led_high()
                    if _pattern_stop.wait(0.08):
                        return
                    _led_low()
                    if _pattern_stop.wait(0.08):
                        return
                # Extra pause before next normal pulse
                if _pattern_stop.wait(0.6):
                    return

    _pattern_thread = threading.Thread(target=_run, daemon=True)
    _pattern_thread.start()


def run_breathe():
    """Start brighter breathing pattern while recording."""
    global _pattern_thread
    _stop_pattern()
    _pattern_stop.clear()

    def _run():
        while not _pattern_stop.is_set():
            _breathe_cycle(min_level=0.18, max_level=1.0, steps=26, step_delay=0.025, stop_event=_pattern_stop)

    _pattern_thread = threading.Thread(target=_run, daemon=True)
    _pattern_thread.start()


def _led_high():
    _set_level(1.0)


def _led_low():
    _set_level(0.0)


def _set_level(level: float):
    if not _led:
        return
    if _supports_pwm:
        _led.value = max(0.0, min(1.0, level))
    elif level > 0:
        _led.on()
    else:
        _led.off()


def _stop_pattern():
    """Stop any background LED pattern thread if running."""
    global _pattern_thread
    if _pattern_thread and _pattern_thread.is_alive():
        _pattern_stop.set()
        _pattern_thread.join(timeout=2)
    _pattern_thread = None


def _breathe_cycle(min_level: float, max_level: float, steps: int, step_delay: float, stop_event: threading.Event):
    if not _led:
        stop_event.wait(step_delay * max(steps, 1))
        return

    if not _supports_pwm:
        # Approximate breathing for on/off-only backends.
        _led_high()
        if stop_event.wait(0.35):
            return
        _led_low()
        stop_event.wait(0.35)
        return

    span = max_level - min_level
    if steps <= 1:
        _set_level(max_level)
        stop_event.wait(step_delay)
        return

    for i in range(steps + 1):
        lvl = min_level + (span * i / steps)
        _set_level(lvl)
        if stop_event.wait(step_delay):
            return

    for i in range(steps, -1, -1):
        lvl = min_level + (span * i / steps)
        _set_level(lvl)
        if stop_event.wait(step_delay):
            return


def connected_flash():
    """Long flash – QR scanned, session connected."""
    _stop_pattern()
    _led_high()
    time.sleep(0.8)
    _led_low()


def upload_blink():
    """Fast blink – uploading."""
    _stop_pattern()
    for _ in range(10):
        _led_high()
        time.sleep(0.1)
        _led_low()
        time.sleep(0.1)


def error_flash():
    """Triple rapid flash – something went wrong."""
    _stop_pattern()
    for _ in range(3):
        _led_high()
        time.sleep(0.08)
        _led_low()
        time.sleep(0.08)


def blink(times: int = 3, interval: float = 0.2):
    """Generic blink pattern."""
    _stop_pattern()
    for i in range(times):
        _led_high()
        time.sleep(interval)
        _led_low()
        if i < times - 1:
            time.sleep(interval)


def long_press_confirm():
    """Visual confirmation when long-press threshold is reached."""
    _stop_pattern()
    blink(times=2, interval=0.12)


def very_long_press_confirm():
    """Visual confirmation when shutdown hold threshold is reached."""
    _stop_pattern()
    _led_high()
    time.sleep(0.35)
    _led_low()


def shutdown_fade(duration_s: float = 1.2):
    """Fade LED out smoothly during shutdown sequence."""
    _stop_pattern()
    if not _led:
        return
    if _supports_pwm:
        steps = 30
        for i in range(steps, -1, -1):
            _set_level(i / steps)
            time.sleep(duration_s / steps)
        _set_level(0.0)
    else:
        # Best-effort approximation on non-PWM backends.
        blink(times=3, interval=0.08)
        _led_low()


def cleanup():
    """Release GPIO resources."""
    _stop_pattern()
    if _led:
        try:
            _set_level(0.0)
            _led.close()
        except Exception:
            pass


def _check_network() -> bool:
    """Quick connectivity test to the API host (non-blocking, ~1 s timeout)."""
    if not config.API_BASE_URL:
        return False
    try:
        parsed = urlparse(config.API_BASE_URL)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        with socket.create_connection((host, port), timeout=1):
            return True
    except Exception:
        return False
