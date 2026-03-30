"""
Buzzer / GPIO helper for audio feedback.

Uses an active piezo buzzer on BUZZER_PIN (default BCM 23 / physical pin 16).
Supports simple HIGH/LOW drive or optional PWM mode for passive buzzers.
No-ops gracefully when USE_BUZZER is false or RPi.GPIO is unavailable.

Note: GPIO18 is deliberately avoided as default because it may be needed
for I2S BCLK.
"""

import logging
import time

from . import config

log = logging.getLogger(__name__)

_gpio = None
_pwm = None
_enabled = False


def setup():
    global _gpio, _pwm, _enabled
    if not config.USE_BUZZER:
        log.debug("Buzzer disabled (USE_BUZZER=false)")
        return

    try:
        import RPi.GPIO as GPIO  # type: ignore[import-untyped]
        _gpio = GPIO
    except ImportError:
        log.debug("RPi.GPIO not available – buzzer feedback disabled")
        return

    _gpio.setwarnings(False)
    _gpio.setmode(_gpio.BCM)
    _gpio.setup(config.BUZZER_PIN, _gpio.OUT, initial=_gpio.LOW)

    if config.BUZZER_PWM:
        _pwm = _gpio.PWM(config.BUZZER_PIN, config.BUZZER_FREQUENCY)
        log.debug("Buzzer PWM mode at %d Hz", config.BUZZER_FREQUENCY)

    _enabled = True
    log.info("Buzzer enabled on GPIO%d (pwm=%s)", config.BUZZER_PIN, config.BUZZER_PWM)


def _buzz_on():
    if _pwm:
        _pwm.start(50)
    elif _gpio:
        _gpio.output(config.BUZZER_PIN, _gpio.HIGH)


def _buzz_off():
    if _pwm:
        _pwm.stop()
    elif _gpio:
        _gpio.output(config.BUZZER_PIN, _gpio.LOW)


def beep(duration: float = 0.15):
    """Single short beep."""
    if not _enabled:
        return
    _buzz_on()
    time.sleep(duration)
    _buzz_off()


def double_beep():
    """Two quick beeps – upload complete / success."""
    beep(0.1)
    time.sleep(0.1)
    beep(0.1)


def long_beep(duration: float = 0.6):
    """One long beep – session ended manually."""
    beep(duration)


def triple_beep():
    """Three rapid beeps – shutdown sequence."""
    for _ in range(3):
        beep(0.08)
        time.sleep(0.08)


def error_beep():
    """Three rapid beeps – something went wrong."""
    for _ in range(3):
        beep(0.08)
        time.sleep(0.08)


def cleanup():
    if _pwm:
        _pwm.stop()
    if _gpio and _enabled:
        _gpio.output(config.BUZZER_PIN, _gpio.LOW)
