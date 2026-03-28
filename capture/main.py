#!/usr/bin/env python3
"""
PiZero Capture – main entry point.

State machine:
    IDLE  →  (QR detected)  →  CAPTURE  →  UPLOAD  →  FINISH  →  IDLE
              ↑                                                     │
              └─────────────────────────────────────────────────────┘

Usage:
    python -m capture.main
"""

import logging
import signal
import sys
import time

from . import config
from . import led
from .camera import create_camera, configure_qr_mode, configure_capture_mode
from .qr_scanner import run_scanner
from .recorder import record
from .uploader import upload_recording, finish_session

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
    picam2 = create_camera()

    log.info("══════════════════════════════════════════")
    log.info("  PiZero Capture System")
    log.info("  API: %s", config.API_BASE_URL)
    log.info("  Record duration: %ds", config.RECORD_DURATION_S)
    log.info("══════════════════════════════════════════")

    try:
        while not _shutdown:
            _run_cycle(picam2)
            if _shutdown:
                break
    except KeyboardInterrupt:
        log.info("Interrupted")
    finally:
        _cleanup(picam2)


def _run_cycle(picam2):
    """One full idle → capture → upload → finish cycle."""

    # ── IDLE: scan for QR ──────────────────────────────────────────
    log.info("── IDLE ── scanning for QR code …")
    led.off()
    configure_qr_mode(picam2)

    master_session_id = run_scanner(picam2, shutdown_check=lambda: _shutdown)
    if _shutdown or not master_session_id:
        return

    log.info("Session acquired: %s", master_session_id)
    picam2.stop()

    # ── CAPTURE ────────────────────────────────────────────────────
    log.info("── CAPTURE ── recording %ds …", config.RECORD_DURATION_S)
    led.on()
    configure_capture_mode(picam2)

    try:
        output_file = record(picam2)
    except Exception:
        log.exception("Recording failed")
        picam2.stop()
        led.blink(5, 0.1)
        return

    picam2.stop()

    # ── UPLOAD ─────────────────────────────────────────────────────
    log.info("── UPLOAD ──")
    led.blink(3, 0.2)

    try:
        upload_recording(master_session_id, output_file)
    except Exception:
        log.exception("Upload failed")
        led.blink(5, 0.1)
        # Keep the file for manual retry
        return

    # Clean up local file after successful upload
    output_file.unlink(missing_ok=True)

    # ── FINISH SESSION ─────────────────────────────────────────────
    log.info("── FINISH ──")
    try:
        finish_session(master_session_id)
    except Exception:
        log.exception("Finish-session failed (recording was uploaded)")

    led.blink(2, 0.3)
    log.info("── CYCLE COMPLETE ── returning to idle in 3s")
    time.sleep(3)


def _cleanup(picam2):
    log.info("Cleaning up …")
    led.off()
    led.cleanup()
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
