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
                         Very long    → sudo halt (descending chord, LED off)
    State 5 – TIMEOUT:  Hard timeout at 30 min. Smart stop via backend.

Usage:
    python -m capture
"""

import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time

from . import config
from . import led
from . import buzzer
from . import button
from .camera import create_camera, configure_qr_mode
from .qr_scanner import run_scanner
from .recorder import start_recording, stop_recording, find_ready_chunks, find_all_chunks, drain_stderr
from .uploader import upload_recording, finish_session, connect_session, notify_pause

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
_master_session_id = None          # set during active capture cycle

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
    """Toggle pause / resume.  The main loop detects _pause_event and stops/restarts rpicam-vid."""
    if _pause_event.is_set():
        log.info("Button: RESUME")
        _pause_event.clear()
        _stop_pause_pulse()
        buzzer.beep()
        led.on()
        if _master_session_id:
            threading.Thread(target=notify_pause, args=(_master_session_id, False), daemon=True).start()
    else:
        log.info("Button: PAUSE")
        _pause_event.set()
        buzzer.beep()
        _start_pause_pulse()
        if _master_session_id:
            threading.Thread(target=notify_pause, args=(_master_session_id, True), daemon=True).start()


def _on_long_press():
    """End session manually."""
    log.info("Button: END SESSION")
    buzzer.triple_beep()
    _stop_event.set()
    _pause_event.clear()
    _stop_pause_pulse()
    led.off()


def _on_vlong_press():
    """Request safe shutdown (sudo halt)."""
    log.info("Button: SHUTDOWN requested")
    led.off()
    _halt_requested.set()
    _stop_event.set()
    _pause_event.clear()

# ---------------------------------------------------------------------------
# Stale chunk cleanup
# ---------------------------------------------------------------------------

def _cleanup_stale_chunks():
    """Remove leftover files from a previous crash to free RAM disk space."""
    try:
        stale = list(config.CAPTURE_DIR.glob("*_vid_*.h264")) + \
                list(config.CAPTURE_DIR.glob("*_aud_*.wav")) + \

                list(config.CAPTURE_DIR.glob("*_chunk_*.mkv")) + \
                list(config.CAPTURE_DIR.glob("*_chunk_*.mp4")) + \
                list(config.CAPTURE_DIR.glob("chunk-*.mp4")) + \
                list(config.CAPTURE_DIR.glob("chunk-*.mkv")) + \
                list(config.CAPTURE_DIR.glob("raw-*.*"))
        if stale:
            log.warning("Removing %d stale chunk(s) from previous run", len(stale))
            for f in stale:
                f.unlink(missing_ok=True)
                log.debug("  removed %s", f.name)
    except Exception as exc:
        log.debug("Stale chunk cleanup skipped: %s", exc)


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
    # Clean up stale chunks from previous crash / unclean shutdown
    _cleanup_stale_chunks()

    log.info("══════════════════════════════════════════")
    log.info("  PiZero Capture System")
    log.info("  API: %s", config.API_BASE_URL)
    log.info("  Chunk duration: %ds", config.RECORD_DURATION_S)
    log.info("  Hard timeout: %ds", config.HARD_TIMEOUT_S)
    log.info("  Button: %s  Buzzer: %s  Mic: %s",
             config.USE_BUTTON, config.USE_BUZZER, config.MIC_TYPE)
    log.info("══════════════════════════════════════════")

    try:
        while not _shutdown:
            if _halt_requested.is_set():
                break
            _run_cycle()
            if _shutdown:
                break
    except KeyboardInterrupt:
        log.info("Interrupted")
    finally:
        _cleanup()

    if _halt_requested.is_set():
        log.info("Executing sudo halt …")
        subprocess.run(["sudo", "halt"], check=False)


def _run_cycle():
    """One full idle → capture (chunked) → finish cycle."""

    # ── STATE 1: IDLE – scan for QR ───────────────────────────────
    log.info("── IDLE ── scanning for QR code …")
    led.idle_blink()
    buzzer.chord_up()
    picam2 = create_camera()
    configure_qr_mode(picam2)

    # Reset session-scoped events
    _pause_event.clear()
    _stop_event.clear()
    _stop_pause_pulse()
    button.start_monitor()

    master_session_id = run_scanner(picam2, shutdown_check=lambda: _shutdown or _halt_requested.is_set())
    if _shutdown or _halt_requested.is_set() or not master_session_id:
        button.stop_monitor()
        picam2.stop()
        picam2.close()
        return

    # ── STATE 2: TRIGGER ──────────────────────────────────────────
    global _master_session_id
    _master_session_id = master_session_id
    log.info("Session acquired: %s", master_session_id)
    led.connected_flash()
    buzzer.beep()
    connect_session(master_session_id)
    picam2.stop()
    picam2.close()  # fully release camera so rpicam-vid can acquire it

    # ── STATE 3: CHUNKED CAPTURE + UPLOAD ─────────────────────────
    log.info("── CAPTURE ── chunked recording (chunk=%ds, hard_timeout=%ds)",
             config.RECORD_DURATION_S, config.HARD_TIMEOUT_S)
    led.on()

    session_start = time.monotonic()
    chunk_index = 0

    # ── Background upload worker ─────────────────────────────────
    upload_q = queue.Queue()
    upload_error = threading.Event()

    def _upload_worker():
        while True:
            item = upload_q.get()
            if item is None:          # poison pill → drain complete
                upload_q.task_done()
                break
            output, idx = item
            try:
                log.info("Chunk %d ready: %s (%.1f KB)", idx,
                         output.name, output.stat().st_size / 1024)
                # Save a debug copy before upload
                if config.DEBUG_SAVE_CHUNKS:
                    import shutil
                    save_dir = config.DEBUG_SAVE_DIR / master_session_id
                    save_dir.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(output, save_dir / output.name)
                    log.info("Debug copy saved: %s", save_dir / output.name)
                resp = upload_recording(master_session_id, output)
                output.unlink(missing_ok=True)
                action = resp.get("action", "continue") if isinstance(resp, dict) else "continue"
                if action == "stop":
                    log.info("Backend requested stop – ending session")
                    _stop_event.set()
            except Exception:
                log.exception("Chunk %d upload failed", idx)
                upload_error.set()
                _stop_event.set()      # stop recording on upload failure
            finally:
                upload_q.task_done()

    upload_thread = threading.Thread(target=_upload_worker, daemon=True)
    upload_thread.start()

    # ── Split capture: video (rpicam-vid) + audio (arecord) ─────
    recorder = start_recording()
    queued = set()       # paths already sent to upload_q
    chunk_index = 0

    def _queue_chunks(chunks):
        nonlocal chunk_index
        for chunk in chunks:
            if chunk not in queued:
                queued.add(chunk)
                log.info("── QUEUED chunk %d for upload ──", chunk_index)
                upload_q.put((chunk, chunk_index))
                chunk_index += 1

    _audio_warned = False  # only log once per session

    while not _shutdown and not _stop_event.is_set() and not _halt_requested.is_set():
        # Hard timeout check
        elapsed = time.monotonic() - session_start
        if elapsed >= config.HARD_TIMEOUT_S:
            log.warning("Hard timeout (%ds) reached – ending session", config.HARD_TIMEOUT_S)
            break

        # Check if rpicam-vid crashed (audio runs independently)
        if not recorder.video_alive():
            stderr = drain_stderr(recorder)
            log.error("rpicam-vid exited unexpectedly%s",
                      f" – stderr: {stderr[-500:]}" if stderr else "")
            led.error_flash()
            buzzer.error_beep()
            break

        # Warn once if audio capture died (video continues)
        if not _audio_warned and recorder.audio_failed():
            log.warning("⚠ Audio capture failed – continuing with video-only")
            _audio_warned = True

        # Pick up completed & muxed chunks
        _queue_chunks(find_ready_chunks(recorder))

        # ── Handle pause ──────────────────────────────────────────
        if _pause_event.is_set():
            stop_recording(recorder)
            _queue_chunks(find_all_chunks(recorder))

            pause_start = time.monotonic()
            while _pause_event.is_set() and not _shutdown and not _stop_event.is_set() and not _halt_requested.is_set():
                if time.monotonic() - pause_start >= config.PAUSE_IDLE_TIMEOUT_S:
                    log.info("Pause idle timeout (%ds) – ending session",
                             config.PAUSE_IDLE_TIMEOUT_S)
                    _pause_event.clear()
                    _stop_event.set()
                    _stop_pause_pulse()
                    break
                time.sleep(0.25)

            # Resume with a fresh recorder
            if not _stop_event.is_set() and not _shutdown and not _halt_requested.is_set():
                led.on()
                time.sleep(1)  # let camera hardware fully release
                recorder = start_recording()
                queued.clear()

            continue

        # Without button, stop after the first completed chunk
        if not config.USE_BUTTON and chunk_index > 0:
            log.info("Button disabled – single-chunk mode, ending session")
            break

        time.sleep(2)

    # ── Stop recording & collect final chunks ─────────────────────
    stop_recording(recorder)
    _queue_chunks(find_all_chunks(recorder))

    # ── Drain pending uploads before finishing session ─────────────
    pending = upload_q.qsize()
    if pending:
        log.info("Waiting for %d pending upload(s) …", pending)
    upload_q.put(None)             # poison pill
    # Keep drain short so _cleanup() runs before systemd SIGKILL (default 90s)
    upload_thread.join(timeout=30)

    if upload_error.is_set():
        led.error_flash()
        buzzer.error_beep()
        time.sleep(1)  # let error flash be visible before end-blink

    # ── FINISH SESSION ────────────────────────────────────────────
    # ── FINISH SESSION (fire-and-forget, don't block return to idle) ─
    log.info("── FINISH ──")

    def _finish_bg():
        try:
            finish_session(master_session_id)
        except Exception:
            log.exception("Finish-session failed (chunks were uploaded)")

    threading.Thread(target=_finish_bg, daemon=True).start()

    led.blink(2, 0.3)
    led.off()

    _master_session_id = None
    log.info("── CYCLE COMPLETE ── %d chunks uploaded, returning to idle", chunk_index)


def _cleanup():
    log.info("Cleaning up …")
    button.stop_monitor()
    button.cleanup()
    _stop_pause_pulse()
    led.off()
    led.cleanup()
    buzzer.cleanup()
    log.info("Goodbye.")


if __name__ == "__main__":
    main()
