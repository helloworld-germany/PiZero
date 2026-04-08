"""
Video + audio capture via a single rpicam-vid process with ``--segment``.

rpicam-vid runs continuously with ``-t 0 --segment <ms>`` and produces
gapless, individually-decodable chunks (``--inline``).  Audio is muxed
inline via ``--audio-source`` / ``--audio-device``.  No arecord, no ffmpeg, no post-processing.

Capture gain is set system-wide via ALSA mixer (alsamixer + alsactl store).
"""

import glob
import logging
import signal as _signal
import subprocess
import threading
import time
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


def start_recording(
    chunk_duration: int | None = None,
    prefix: str | None = None,
    audio_override: bool | None = None,
) -> tuple[subprocess.Popen, str, str]:
    """Start continuous rpicam-vid recording with ``--segment``.

    Returns ``(proc, prefix, ext)`` where *proc* is the Popen handle,
    *prefix* identifies this recording stretch in filenames, and *ext*
    is ``"mkv"`` (with audio) or ``"mp4"`` (video-only).

    *audio_override*: ``True`` = force audio on, ``False`` = force off,
    ``None`` (default) = auto-detect via mic.py.
    """
    chunk_duration = chunk_duration or config.RECORD_DURATION_S
    cap_dir = _ensure_capture_dir()
    prefix = prefix or str(int(time.time() * 1000))

    if audio_override is None:
        audio_device = _resolve_audio_device()
    elif audio_override:
        audio_device = _resolve_audio_device()
    else:
        audio_device = None
        log.info("Audio disabled by override")

    ext = "mkv" if audio_device else "mp4"

    output_pattern = str(cap_dir / f"{prefix}_chunk_%04d.{ext}")

    cmd = [
        "rpicam-vid",
        "-t", "0",                                  # run until stopped
        "--segment", str(chunk_duration * 1000),     # auto-split interval
        "--inline",                                  # SPS/PPS per segment
        "--width", str(config.VIDEO_WIDTH),
        "--height", str(config.VIDEO_HEIGHT),
        "--framerate", str(config.VIDEO_FPS),
        "--bitrate", "2000000",
        "-n",
        "-o", output_pattern,
    ]
    if audio_device:
        cmd += [
            "--audio-source", "alsa",
            "--audio-device", audio_device,
            "--audio-channels", "1",        # INMP441 = mono (left channel only)
            "--audio-samplerate", "48000",  # native I2S / voiceHAT rate
        ]

    log.info("Starting continuous recording (segment=%ds, audio=%s)",
             chunk_duration, audio_device or "(none)")
    log.debug("rpicam-vid command: %s", " ".join(cmd))

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)

    # Drain stderr in background to prevent pipe-buffer deadlock.
    # Stores last N bytes for diagnostics after the process exits.
    proc._stderr_buf = b""
    proc._stderr_lock = threading.Lock()

    def _drain():
        buf = b""
        try:
            while True:
                data = proc.stderr.read(4096)
                if not data:
                    break
                buf = (buf + data)[-4096:]  # keep last 4 KB
        except Exception:
            pass
        with proc._stderr_lock:
            proc._stderr_buf = buf

    t = threading.Thread(target=_drain, daemon=True)
    t.start()

    return proc, prefix, ext


def drain_stderr(proc: subprocess.Popen) -> str:
    """Return captured stderr from the background drain thread."""
    try:
        with proc._stderr_lock:
            data = proc._stderr_buf
        if data:
            return data.decode(errors="replace").strip()
    except (AttributeError, Exception):
        pass
    return ""


def stop_recording(proc: subprocess.Popen) -> None:
    """Gracefully stop rpicam-vid (SIGINT for clean file finalization)."""
    if proc is None or proc.poll() is not None:
        return
    proc.send_signal(_signal.SIGINT)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        log.warning("rpicam-vid did not exit – killing")
        proc.kill()
        proc.wait(timeout=5)
    stderr = drain_stderr(proc)
    if stderr:
        log.debug("rpicam-vid stderr: %s", stderr[-500:])


def find_ready_chunks(prefix: str, ext: str) -> list[Path]:
    """Return completed chunk paths (all except the one currently being written).

    While rpicam-vid is running, the newest file is still being written to.
    All older files are complete and safe to upload/delete.
    """
    pattern = str(config.CAPTURE_DIR / f"{prefix}_chunk_*.{ext}")
    files = sorted(glob.glob(pattern))
    if len(files) > 1:
        return [Path(f) for f in files[:-1]]
    return []


def find_all_chunks(prefix: str, ext: str) -> list[Path]:
    """Return all chunk paths for a prefix (call after ``stop_recording``)."""
    pattern = str(config.CAPTURE_DIR / f"{prefix}_chunk_*.{ext}")
    return [Path(f) for f in sorted(glob.glob(pattern))]
