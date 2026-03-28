"""
High-performance 20-second video + audio capture.

Uses picamera2 for H.264 video and ffmpeg for ALSA audio, then muxes them
into a single .webm (VP8+Opus) or .mp4 file that the vidaugment backend
accepts.
"""

import logging
import shutil
import subprocess
import time
from pathlib import Path

from . import config

log = logging.getLogger(__name__)


def _ensure_capture_dir() -> Path:
    config.CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
    return config.CAPTURE_DIR


def record(picam2) -> Path:
    """
    Record RECORD_DURATION_S seconds of synchronised video + audio.

    *picam2* should already be configured but **stopped** (this function
    reconfigures for high-quality capture).

    Returns the path to the muxed output file ready for upload.
    """
    cap_dir = _ensure_capture_dir()
    ts = int(time.time() * 1000)
    video_h264 = cap_dir / f"capture-{ts}.h264"
    audio_wav = cap_dir / f"capture-{ts}.wav"
    output_file = cap_dir / f"capture-{ts}.webm"

    duration = config.RECORD_DURATION_S
    log.info(
        "Starting %ds capture  video=%dx%d@%dfps  audio=%s",
        duration,
        config.VIDEO_WIDTH,
        config.VIDEO_HEIGHT,
        config.VIDEO_FPS,
        config.AUDIO_DEVICE,
    )

    # -----------------------------------------------------------------
    # 1.  Start audio recording (ffmpeg reading ALSA)
    # -----------------------------------------------------------------
    audio_cmd = [
        "ffmpeg", "-y",
        "-f", "alsa",
        "-ac", "1",
        "-ar", str(config.AUDIO_SAMPLE_RATE),
        "-i", config.AUDIO_DEVICE,
        "-t", str(duration),
        str(audio_wav),
    ]
    log.debug("Audio cmd: %s", audio_cmd)
    audio_proc = subprocess.Popen(
        audio_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )

    # -----------------------------------------------------------------
    # 2.  Start video recording (picamera2 H.264 encoder)
    # -----------------------------------------------------------------
    from picamera2.encoders import H264Encoder
    from picamera2.outputs import FileOutput

    encoder = H264Encoder(bitrate=2_000_000)
    output = FileOutput(str(video_h264))

    picam2.start_encoder(encoder, output)
    log.info("Recording in progress …")
    time.sleep(duration)
    picam2.stop_encoder(encoder)
    log.info("Video capture done (%s)", video_h264)

    # -----------------------------------------------------------------
    # 3.  Wait for audio to finish
    # -----------------------------------------------------------------
    audio_proc.wait(timeout=duration + 10)
    if audio_proc.returncode != 0:
        stderr = audio_proc.stderr.read().decode(errors="replace")
        log.warning("Audio capture returned %d: %s", audio_proc.returncode, stderr)
    else:
        log.info("Audio capture done (%s)", audio_wav)

    # -----------------------------------------------------------------
    # 4.  Mux into WebM (VP8 + Opus) – matches browser MediaRecorder output
    # -----------------------------------------------------------------
    mux_cmd = [
        "ffmpeg", "-y",
        "-i", str(video_h264),
        "-i", str(audio_wav),
        "-c:v", "libvpx",
        "-crf", "10",
        "-b:v", "1500k",
        "-c:a", "libopus",
        "-b:a", "64k",
        "-shortest",
        str(output_file),
    ]
    log.debug("Mux cmd: %s", mux_cmd)
    mux_result = subprocess.run(
        mux_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=120,
    )
    if mux_result.returncode != 0:
        stderr = mux_result.stderr.decode(errors="replace")
        log.error("Mux failed (%d): %s", mux_result.returncode, stderr)
        raise RuntimeError(f"ffmpeg mux failed: {stderr[:500]}")

    log.info("Muxed output ready: %s (%.1f KB)", output_file, output_file.stat().st_size / 1024)

    # Clean up intermediate files
    video_h264.unlink(missing_ok=True)
    audio_wav.unlink(missing_ok=True)

    return output_file
