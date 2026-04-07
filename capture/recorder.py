"""
Video + audio capture: rpicam-vid (video) + arecord (audio) → ffmpeg mux.

rpicam-vid handles H.264 encoding via hardware V4L2 M2M with near-zero CPU.
Audio is captured separately via arecord (which handles I2S correctly)
and muxed with ffmpeg afterward.  This avoids rpicam-vid's libav audio
issues with I2S MEMS microphones (severe volume loss + ALSA xruns).

The ffmpeg mux step copies the video stream (instant) and encodes audio
as AAC with an optional gain boost – all in one pass.
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


def _mux_av(video_file: Path, audio_file: Path | None, output_file: Path,
            gain_db: int = 0) -> Path:
    """Mux video + audio into mp4.  Copies video, encodes audio as AAC with optional gain."""
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video_file)]
    if audio_file and audio_file.exists() and audio_file.stat().st_size > 100:
        cmd += ["-i", str(audio_file)]
        af = f"volume={gain_db}dB" if gain_db else None
        cmd += ["-c:v", "copy"]
        if af:
            cmd += ["-af", af]
        cmd += ["-c:a", "aac", "-b:a", "64k"]
        cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-movflags", "+faststart", str(output_file)]

    log.debug("ffmpeg mux command: %s", " ".join(cmd))
    result = subprocess.run(cmd, timeout=120, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace")[-300:]
        log.warning("ffmpeg mux failed (rc=%d): %s", result.returncode, stderr)
        # Fall back: just rename the raw video
        video_file.rename(output_file)
    else:
        video_file.unlink(missing_ok=True)

    if audio_file:
        audio_file.unlink(missing_ok=True)
    return output_file


def record_chunk(
    chunk_duration: int | None = None,
    stop_event: threading.Event | None = None,
) -> Path | None:
    """Record a single chunk: rpicam-vid (video) + arecord (audio) in parallel.

    Returns the output .mp4 path, or None if *stop_event* fires before any
    data is captured.  On pause (stop_event set mid-chunk), both processes
    are stopped gracefully.
    """
    chunk_duration = chunk_duration or config.RECORD_DURATION_S
    cap_dir = _ensure_capture_dir()
    ts = int(time.time() * 1000)
    raw_video = cap_dir / f"raw-{ts}.h264"
    raw_audio = cap_dir / f"raw-{ts}.wav"
    output_file = cap_dir / f"chunk-{ts}.mp4"
    audio_device = _resolve_audio_device()

    log.info("Chunk capture %ds  audio=%s", chunk_duration, audio_device or "(none)")

    # ── Start rpicam-vid (video only, raw H.264) ──────────────────
    vid_cmd = [
        "rpicam-vid",
        "-t", str(chunk_duration * 1000),
        "--width", str(config.VIDEO_WIDTH),
        "--height", str(config.VIDEO_HEIGHT),
        "--framerate", str(config.VIDEO_FPS),
        "--bitrate", "2000000",
        "-n",
        "-o", str(raw_video),
    ]
    log.debug("rpicam-vid command: %s", " ".join(vid_cmd))
    vid_proc = subprocess.Popen(vid_cmd, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE)

    # ── Start arecord (audio, parallel) ───────────────────────────
    aud_proc = None
    if audio_device:
        # Use hw: device with native S32_LE 48kHz for I2S, plughw for others
        aud_cmd = [
            "arecord", "-D", audio_device,
            "-f", "S32_LE", "-r", "48000", "-c", "2",
            "-d", str(chunk_duration + 2),   # slightly longer to cover video
            str(raw_audio),
        ]
        log.debug("arecord command: %s", " ".join(aud_cmd))
        aud_proc = subprocess.Popen(aud_cmd, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE)

    # ── Wait for rpicam-vid, or stop_event ────────────────────────
    while vid_proc.poll() is None:
        if stop_event and stop_event.is_set():
            vid_proc.send_signal(_signal.SIGINT)
            break
        time.sleep(0.25)

    try:
        vid_proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        log.warning("rpicam-vid did not exit – killing")
        vid_proc.kill()
        vid_proc.wait(timeout=5)

    # Stop arecord (SIGINT for clean WAV header)
    if aud_proc and aud_proc.poll() is None:
        aud_proc.send_signal(_signal.SIGINT)
        try:
            aud_proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            aud_proc.kill()
            aud_proc.wait(timeout=5)

    vid_stderr = vid_proc.stderr.read().decode(errors="replace")
    if vid_stderr:
        log.debug("rpicam-vid stderr: %s", vid_stderr[-500:])

    if not raw_video.exists() or raw_video.stat().st_size < 1000:
        log.error("rpicam-vid produced no output (rc=%d): %s",
                  vid_proc.returncode, vid_stderr[-500:])
        raw_video.unlink(missing_ok=True)
        raw_audio.unlink(missing_ok=True)
        raise RuntimeError(f"rpicam-vid capture failed: {vid_stderr[:500]}")

    # ── Mux video + audio (+ gain) into final mp4 ────────────────
    gain = config.AUDIO_GAIN_DB if audio_device else 0
    _mux_av(raw_video, raw_audio if audio_device else None, output_file,
            gain_db=gain)

    log.info("Chunk ready: %s (%.1f KB)", output_file,
             output_file.stat().st_size / 1024)
    return output_file
