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
            from gpiozero import TonalBuzzer as _TB  # type: ignore[import-untyped]
            from gpiozero.tones import Tone  # type: ignore[import-untyped]
            _buzzer = _TB(config.BUZZER_PIN)
            _is_tonal = True
            log.debug("Buzzer gpiozero TonalBuzzer at GPIO%d", config.BUZZER_PIN)
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
        from gpiozero.tones import Tone  # type: ignore[import-untyped]
        _buzzer.play(Tone(config.BUZZER_FREQUENCY))
    else:
        _buzzer.on()


def _buzz_off():
    if not _buzzer:
        return
    if _is_tonal:
        _buzzer.stop()
    else:
        _buzzer.off()


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
    if _buzzer:
        try:
            _buzzer.close()
        except Exception:
            pass
    if _gpio and _enabled:
        _gpio.output(config.BUZZER_PIN, _gpio.LOW)
