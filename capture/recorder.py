"""
High-performance video + audio capture with chunked recording.

Uses picamera2 for H.264 video and ffmpeg for ALSA audio, then muxes them
into a single .mp4 file that the vidaugment backend accepts.

Supports:
  - Chunked recording (CHUNK_DURATION_S, default 30s)
  - Pause / resume via an external threading.Event
  - Audio device selection via mic.py (I2S / ALSA / auto)
  - Graceful fallback to video-only when no audio device is found
"""

import logging
import shutil
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


def record(picam2, pause_event: threading.Event | None = None) -> Path:
    """
    Record RECORD_DURATION_S seconds of synchronised video + audio.

    Thin wrapper around record_chunk() for backward compatibility.
    """
    return record_chunk(
        picam2,
        chunk_duration=config.RECORD_DURATION_S,
        pause_event=pause_event,
    )


def record_chunk(
    picam2,
    chunk_duration: int | None = None,
    pause_event: threading.Event | None = None,
    stop_event: threading.Event | None = None,
) -> Path | None:
    """
    Record a single chunk of *chunk_duration* seconds (default CHUNK_DURATION_S).

    Like record(), but designed to be called in a loop.  Returns None if
    *stop_event* fires before the chunk finishes (caller should upload any
    partial data separately).

    *picam2* must already be configured and **started** in capture mode.
    """
    chunk_duration = chunk_duration or config.CHUNK_DURATION_S
    cap_dir = _ensure_capture_dir()
    ts = int(time.time() * 1000)
    video_h264 = cap_dir / f"chunk-{ts}.h264"
    audio_wav = cap_dir / f"chunk-{ts}.wav"
    output_file = cap_dir / f"chunk-{ts}.mp4"

    audio_device = _resolve_audio_device()

    log.info("Chunk capture %ds  audio=%s", chunk_duration, audio_device or "(none)")

    # Audio
    audio_proc = None
    has_audio = audio_device is not None
    if has_audio:
        audio_cmd = [
            "ffmpeg", "-y",
            "-f", "alsa", "-ac", "1",
            "-ar", str(config.AUDIO_SAMPLE_RATE),
            "-i", audio_device,
            "-t", str(chunk_duration),
            str(audio_wav),
        ]
        audio_proc = subprocess.Popen(
            audio_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )

    # Video
    from picamera2.encoders import H264Encoder
    from picamera2.outputs import FileOutput

    encoder = H264Encoder(bitrate=2_000_000)
    file_output = FileOutput(str(video_h264))
    picam2.start_encoder(encoder, file_output)

    elapsed = 0.0
    step = 0.25
    stopped_early = False
    while elapsed < chunk_duration:
        if stop_event and stop_event.is_set():
            stopped_early = True
            break
        if pause_event and pause_event.is_set():
            time.sleep(step)
            continue
        time.sleep(step)
        elapsed += step

    picam2.stop_encoder(encoder)
    log.info("Chunk video done (%s)", video_h264)

    # Terminate audio early if we stopped before full duration
    if audio_proc is not None:
        if stopped_early:
            audio_proc.terminate()
        audio_proc.wait(timeout=chunk_duration + 10)
        if audio_proc.returncode not in (0, -15):  # -15 = SIGTERM
            has_audio = False
        else:
            log.info("Chunk audio done (%s)", audio_wav)

    # Mux
    if has_audio and audio_wav.exists():
        mux_cmd = [
            "ffmpeg", "-y",
            "-i", str(video_h264), "-i", str(audio_wav),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "64k",
            "-shortest", "-movflags", "+faststart",
            str(output_file),
        ]
    else:
        mux_cmd = [
            "ffmpeg", "-y",
            "-i", str(video_h264),
            "-c:v", "copy", "-an", "-movflags", "+faststart",
            str(output_file),
        ]

    mux_result = subprocess.run(
        mux_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120,
    )
    if mux_result.returncode != 0:
        stderr = mux_result.stderr.decode(errors="replace")
        log.error("Chunk mux failed (%d): %s", mux_result.returncode, stderr)
        video_h264.unlink(missing_ok=True)
        audio_wav.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg chunk mux failed: {stderr[:500]}")

    video_h264.unlink(missing_ok=True)
    audio_wav.unlink(missing_ok=True)

    log.info("Chunk ready: %s (%.1f KB)", output_file, output_file.stat().st_size / 1024)
    return output_file
