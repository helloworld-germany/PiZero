#!/usr/bin/env python3
"""
PiZero Capture – main entry point.

State machine:
    State 1 – IDLE:     QR scan (low-res, low-fps). LED off.
    State 2 – TRIGGER:  QR detected → buzzer 1×, LED on, start capture.
    State 3 – CAPTURE:  30s chunks, each uploaded immediately.
                         Backend may respond {"action":"stop"} → end session.
    State 4 – BUTTON:   Short press  → pause/resume (LED pulse, buzzer 2×)
                         Long press   → end session (upload last chunk, LED off)
                         Very long    → sudo halt (buzzer 3× fast, LED off)
    State 5 – TIMEOUT:  Hard timeout at 30 min. Smart stop via backend.

Usage:
    python -m capture
"""

import logging
import os
import signal
import subprocess
import sys
import threading
import time

from . import config
from . import led
from . import buzzer
from . import button
from .camera import create_camera, configure_qr_mode, configure_capture_mode
from .qr_scanner import run_scanner
from .recorder import record_chunk
from .uploader import upload_recording, finish_session, connect_session

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("capture")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_shutdown = False


def _handle_signal(sig, _frame):
    global _shutdown
    log.info("Received signal %s – shutting down", sig)
    _shutdown = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ---------------------------------------------------------------------------
# Session-scoped threading events (used by button callbacks)
# ---------------------------------------------------------------------------
_pause_event = threading.Event()   # set = paused
_stop_event = threading.Event()    # set = end session
_halt_requested = threading.Event()

# LED pulse thread for pause state
_pulse_thread = None
_pulse_stop = threading.Event()


def _start_pause_pulse():
    """Slow LED pulse while paused."""
    global _pulse_thread
    _stop_pause_pulse()
    _pulse_stop.clear()

    def _run():
        while not _pulse_stop.is_set():
            led._led_high()
            if _pulse_stop.wait(0.5):
                break
            led._led_low()
            if _pulse_stop.wait(0.5):
                break

    _pulse_thread = threading.Thread(target=_run, daemon=True)
    _pulse_thread.start()


def _stop_pause_pulse():
    global _pulse_thread
    if _pulse_thread and _pulse_thread.is_alive():
        _pulse_stop.set()
        _pulse_thread.join(timeout=2)
    _pulse_thread = None

# ---------------------------------------------------------------------------
# Button callbacks
# ---------------------------------------------------------------------------


def _on_short_press():
    """Toggle pause / resume."""
    if _pause_event.is_set():
        log.info("Button: RESUME")
        _pause_event.clear()
        _stop_pause_pulse()
        led.on()
    else:
        log.info("Button: PAUSE")
        _pause_event.set()
        buzzer.double_beep()
        _start_pause_pulse()


def _on_long_press():
    """End session manually."""
    log.info("Button: END SESSION")
    _stop_event.set()
    _pause_event.clear()
    _stop_pause_pulse()


def _on_vlong_press():
    """Request safe shutdown (sudo halt)."""
    log.info("Button: SHUTDOWN requested")
    buzzer.triple_beep()
    led.blink(3, 0.15)
    led.off()
    _halt_requested.set()
    _stop_event.set()
    _pause_event.clear()

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main():
    if not config.API_BASE_URL:
        log.error(
            "VIDAUGMENT_API_BASE_URL is not set. "
            "Edit capture/config.env or export the variable."
        )
        sys.exit(1)

    led.setup()
    buzzer.setup()
    button.setup()
    button.register(
        on_short_press=_on_short_press,
        on_long_press=_on_long_press,
        on_vlong_press=_on_vlong_press,
    )
    picam2 = create_camera()

    log.info("══════════════════════════════════════════")
    log.info("  PiZero Capture System")
    log.info("  API: %s", config.API_BASE_URL)
    log.info("  Chunk duration: %ds", config.RECORD_DURATION_S)
    log.info("  Hard timeout: %ds", config.HARD_TIMEOUT_S)
    log.info("  Button: %s  Buzzer: %s  I2S mic: %s",
             config.USE_BUTTON, config.USE_BUZZER, config.USE_I2S_MIC)
    log.info("══════════════════════════════════════════")

    try:
        while not _shutdown:
            if _halt_requested.is_set():
                break
            _run_cycle(picam2)
            if _shutdown:
                break
    except KeyboardInterrupt:
        log.info("Interrupted")
    finally:
        _cleanup(picam2)

    if _halt_requested.is_set():
        log.info("Executing sudo halt …")
        subprocess.run(["sudo", "halt"], check=False)


def _run_cycle(picam2):
    """One full idle → capture (chunked) → finish cycle."""

    # ── STATE 1: IDLE – scan for QR ───────────────────────────────
    log.info("── IDLE ── scanning for QR code …")
    led.idle_blink()
    configure_qr_mode(picam2)

    # Reset session-scoped events
    _pause_event.clear()
    _stop_event.clear()
    _stop_pause_pulse()
    button.start_monitor()

    master_session_id = run_scanner(picam2, shutdown_check=lambda: _shutdown or _halt_requested.is_set())
    if _shutdown or _halt_requested.is_set() or not master_session_id:
        button.stop_monitor()
        return

    # ── STATE 2: TRIGGER ──────────────────────────────────────────
    log.info("Session acquired: %s", master_session_id)
    led.connected_flash()
    buzzer.beep()
    connect_session(master_session_id)
    picam2.stop()

    # ── STATE 3: CHUNKED CAPTURE + UPLOAD ─────────────────────────
    log.info("── CAPTURE ── chunked recording (chunk=%ds, hard_timeout=%ds)",
             config.RECORD_DURATION_S, config.HARD_TIMEOUT_S)
    led.on()
    configure_capture_mode(picam2)

    session_start = time.monotonic()
    chunk_index = 0

    while not _shutdown and not _stop_event.is_set() and not _halt_requested.is_set():
        # Hard timeout check
        elapsed = time.monotonic() - session_start
        if elapsed >= config.HARD_TIMEOUT_S:
            log.warning("Hard timeout (%ds) reached – ending session", config.HARD_TIMEOUT_S)
            break

        remaining = config.HARD_TIMEOUT_S - elapsed
        chunk_dur = min(config.RECORD_DURATION_S, int(remaining))
        if chunk_dur <= 0:
            break

        # Record one chunk
        try:
            output_file = record_chunk(
                picam2,
                chunk_duration=chunk_dur,
                pause_event=_pause_event,
                stop_event=_stop_event,
            )
        except Exception:
            log.exception("Chunk %d recording failed", chunk_index)
            led.error_flash()
            buzzer.error_beep()
            break

        if output_file is None:
            break

        # Upload chunk immediately
        log.info("── UPLOAD chunk %d ──", chunk_index)
        try:
            resp = upload_recording(master_session_id, output_file)
        except Exception:
            log.exception("Chunk %d upload failed", chunk_index)
            led.error_flash()
            buzzer.error_beep()
            # Keep file for retry; end this session
            break

        output_file.unlink(missing_ok=True)
        chunk_index += 1

        # Smart timeout: backend can signal stop
        action = resp.get("action", "continue") if isinstance(resp, dict) else "continue"
        if action == "stop":
            log.info("Backend requested stop – ending session")
            break

        # Without button, record only one chunk (original single-shot behavior)
        if not config.USE_BUTTON:
            log.info("Button disabled – single-chunk mode, ending session")
            break

    picam2.stop()

    # ── FINISH SESSION ────────────────────────────────────────────
    log.info("── FINISH ──")
    try:
        finish_session(master_session_id)
    except Exception:
        log.exception("Finish-session failed (chunks were uploaded)")

    if _stop_event.is_set() and not _halt_requested.is_set():
        buzzer.long_beep()
    else:
        buzzer.double_beep()
    led.blink(2, 0.3)
    led.off()

    log.info("── CYCLE COMPLETE ── %d chunks uploaded, returning to idle in 3s", chunk_index)
    time.sleep(3)


def _cleanup(picam2):
    log.info("Cleaning up …")
    button.stop_monitor()
    button.cleanup()
    _stop_pause_pulse()
    led.off()
    led.cleanup()
    buzzer.cleanup()
    try:
        picam2.stop()
    except Exception:
        pass
    try:
        picam2.close()
    except Exception:
        pass
    log.info("Goodbye.")


if __name__ == "__main__":
    main()
