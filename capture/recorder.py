"""
Video + audio capture using rpicam-vid native hardware muxing.

rpicam-vid (Bookworm rpicam-apps) handles H.264 encoding, ALSA audio
capture, and mp4 container muxing in a single native binary with
near-zero CPU overhead.

Microphone is configurable via AUDIO_DEVICE (supports USB mic, I2S, etc.).
"""

import logging
import signal as _signal
import subprocess
import time
import threading
from pathlib import Path

from . import config
from . import mic

log = logging.getLogger(__name__)


def _resolve_audio_device() -> str | None:
    """Pick the audio capture device using mic.py, falling back gracefully."""
    device = mic.preferred_audio_device()
    if device:
        return device
    log.warning("No audio capture device found – recording video only")
    return None


def _ensure_capture_dir() -> Path:
    config.CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    return config.CAPTURE_DIR


def record_chunk(
    chunk_duration: int | None = None,
    stop_event: threading.Event | None = None,
) -> Path | None:
    """Record a single chunk using rpicam-vid with hardware H.264 + audio muxing.

    Returns the output .mp4 path, or None if *stop_event* fires before any
    data is captured.  On pause (stop_event set mid-chunk), the current chunk
    is gracefully finalised via SIGINT so the mp4 is valid.
    """
    chunk_duration = chunk_duration or config.RECORD_DURATION_S
    cap_dir = _ensure_capture_dir()
    ts = int(time.time() * 1000)
    output_file = cap_dir / f"chunk-{ts}.mp4"
    audio_device = _resolve_audio_device()

    log.info("Chunk capture %ds  audio=%s", chunk_duration, audio_device or "(none)")

    cmd = [
        "rpicam-vid",
        "-t", str(chunk_duration * 1000),   # duration in ms
        "--width", str(config.VIDEO_WIDTH),
        "--height", str(config.VIDEO_HEIGHT),
        "--framerate", str(config.VIDEO_FPS),
        "--bitrate", "2000000",
        "--codec", "libav",
        "--libav-format", "mp4",
        "-n",                                # no preview (headless)
        "-o", str(output_file),
    ]
    if audio_device:
        cmd += [
            "--libav-audio",
            "--audio-source", "alsa",
            "--audio-device", audio_device,
            "--audio-codec", "aac",
            "--audio-bitrate", "64000",
            "--audio-samplerate", str(config.AUDIO_SAMPLE_RATE),
        ]

    log.debug("rpicam-vid command: %s", " ".join(cmd))

    proc = subprocess.Popen(
        cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )

    # Wait for rpicam-vid to finish, or stop_event to fire (pause / stop)
    while proc.poll() is None:
        if stop_event and stop_event.is_set():
            proc.send_signal(_signal.SIGINT)   # graceful stop → valid mp4
            break
        time.sleep(0.25)

    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        log.warning("rpicam-vid did not exit – killing")
        proc.kill()
        proc.wait(timeout=5)

    stderr = proc.stderr.read().decode(errors="replace")
    if stderr:
        log.debug("rpicam-vid stderr: %s", stderr[-500:])

    if not output_file.exists() or output_file.stat().st_size < 1000:
        log.error("rpicam-vid produced no output (rc=%d): %s",
                  proc.returncode, stderr[-500:])
        output_file.unlink(missing_ok=True)
        raise RuntimeError(f"rpicam-vid capture failed: {stderr[:500]}")

    # Quick audio verification
    if audio_device:
        try:
            probe = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a",
                 "-show_entries", "stream=codec_name",
                 "-of", "csv=p=0", str(output_file)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
            )
            audio_info = probe.stdout.decode().strip()
            if audio_info:
                log.info("Chunk audio verified: %s", audio_info)
            else:
                log.warning("Chunk has NO audio stream!")
        except Exception as exc:
            log.debug("ffprobe check skipped: %s", exc)

    log.info("Chunk ready: %s (%.1f KB)", output_file, output_file.stat().st_size / 1024)
    return output_file
