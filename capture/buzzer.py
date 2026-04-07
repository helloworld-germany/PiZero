"""
Buzzer / GPIO helper for audio feedback.

Uses a piezo buzzer on BUZZER_PIN (default BCM 23 / physical pin 16).
Default mode is PWM (works with both passive and active buzzers).
No-ops gracefully when USE_BUZZER is false or no GPIO backend is available.

Uses gpiozero (works on Debian 13 / Trixie with lgpio).
Falls back to RPi.GPIO on older systems.

Note: GPIO18 is deliberately avoided as default because it may be needed
for I2S BCLK.
"""

import logging
import time

from . import config

log = logging.getLogger(__name__)

_buzzer = None  # gpiozero device or fallback wrapper
_is_tonal = False  # True when using TonalBuzzer (needs .play/.stop)
_enabled = False


def setup():
    global _buzzer, _is_tonal, _enabled
    if not config.USE_BUZZER:
        log.debug("Buzzer disabled (USE_BUZZER=false)")
        return

    # Try gpiozero first (Debian 13+), then RPi.GPIO fallback
    try:
        if config.BUZZER_PWM:
            from gpiozero import PWMOutputDevice  # type: ignore[import-untyped]
            _buzzer = PWMOutputDevice(config.BUZZER_PIN, initial_value=0, frequency=config.BUZZER_FREQUENCY)
            _is_tonal = True
            log.debug("Buzzer gpiozero PWMOutputDevice at GPIO%d (%d Hz)", config.BUZZER_PIN, config.BUZZER_FREQUENCY)
        else:
            from gpiozero import Buzzer as _GpioBuzzer  # type: ignore[import-untyped]
            _buzzer = _GpioBuzzer(config.BUZZER_PIN, initial_value=False)
            log.debug("Buzzer gpiozero Buzzer (on/off) at GPIO%d", config.BUZZER_PIN)
        _enabled = True
    except Exception:
        try:
            import RPi.GPIO as _GPIO  # type: ignore[import-untyped]

            class _FallbackBuzzer:
                """Minimal adapter via RPi.GPIO."""
                def __init__(self, pin, pwm, freq):
                    self._pin = pin
                    self._pwm_obj = None
                    _GPIO.setwarnings(False)
                    _GPIO.setmode(_GPIO.BCM)
                    _GPIO.setup(pin, _GPIO.OUT, initial=_GPIO.LOW)
                    if pwm:
                        self._pwm_obj = _GPIO.PWM(pin, freq)
                def on(self):
                    if self._pwm_obj:
                        self._pwm_obj.start(50)
                    else:
                        _GPIO.output(self._pin, _GPIO.HIGH)
                def off(self):
                    if self._pwm_obj:
                        self._pwm_obj.stop()
                    else:
                        _GPIO.output(self._pin, _GPIO.LOW)
                def close(self):
                    self.off()
                    _GPIO.cleanup(self._pin)

            _buzzer = _FallbackBuzzer(config.BUZZER_PIN, config.BUZZER_PWM, config.BUZZER_FREQUENCY)
            _enabled = True
            log.debug("Buzzer RPi.GPIO fallback at GPIO%d", config.BUZZER_PIN)
        except Exception:
            log.debug("No GPIO backend available – buzzer feedback disabled")
            return

    log.info("Buzzer enabled on GPIO%d (pwm=%s, freq=%d)",
             config.BUZZER_PIN, config.BUZZER_PWM, config.BUZZER_FREQUENCY)


def _buzz_on():
    if not _buzzer:
        return
    if _is_tonal:
        _buzzer.value = 0.5  # 50% duty cycle
    else:
        _buzzer.on()


def _buzz_off():
    if not _buzzer:
        return
    if _is_tonal:
        _buzzer.value = 0
    else:
        _buzzer.off()


def beep(duration: float = 0.15):
    """Single short beep."""
    if not _enabled:
        return
    _buzz_on()
    time.sleep(duration)
    _buzz_off()


def _play_tone(freq: int, duration: float = 0.15):
    """Play a single tone at the given frequency (PWM only, else plain beep)."""
    if not _enabled:
        return
    if _is_tonal:
        _buzzer.frequency = freq
    _buzz_on()
    time.sleep(duration)
    _buzz_off()


# Major chord tones (C5–E5–G5)
_CHORD_LOW = 523    # C5
_CHORD_MID = 659    # E5
_CHORD_HIGH = 784   # G5


def chord_up():
    """Three tones low→high (major chord ascending). System ready."""
    if not _enabled:
        return
    _play_tone(_CHORD_LOW, 0.12)
    time.sleep(0.06)
    _play_tone(_CHORD_MID, 0.12)
    time.sleep(0.06)
    _play_tone(_CHORD_HIGH, 0.18)
    # Restore default frequency
    if _is_tonal:
        _buzzer.frequency = config.BUZZER_FREQUENCY


def chord_down():
    """Three tones high→low (major chord descending). Shutdown."""
    if not _enabled:
        return
    _play_tone(_CHORD_HIGH, 0.12)
    time.sleep(0.06)
    _play_tone(_CHORD_MID, 0.12)
    time.sleep(0.06)
    _play_tone(_CHORD_LOW, 0.18)
    if _is_tonal:
        _buzzer.frequency = config.BUZZER_FREQUENCY


def double_beep():
    """Two quick beeps."""
    beep(0.1)
    time.sleep(0.1)
    beep(0.1)


def triple_beep():
    """Three quick beeps – end session."""
    for _ in range(3):
        beep(0.08)
        time.sleep(0.08)


def long_beep(duration: float = 0.6):
    """One long beep – session ended manually."""
    beep(duration)


def error_beep():
    """Three rapid beeps – something went wrong."""
    for _ in range(3):
        beep(0.08)
        time.sleep(0.08)


def cleanup():
    if _buzzer:
        try:
            _buzzer.close()
        except Exception:
            pass
