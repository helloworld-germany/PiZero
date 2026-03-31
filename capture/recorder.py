"""
High-performance video + audio capture with chunked recording.

Uses picamera2 for H.264 video and ffmpeg for ALSA audio, then muxes them
into a single .mp4 file that the vidaugment backend accepts.

Supports:
  - Chunked recording (RECORD_DURATION_S per chunk)
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
    Record a single chunk of *chunk_duration* seconds (default RECORD_DURATION_S).

    Like record(), but designed to be called in a loop.  Returns None if
    *stop_event* fires before the chunk finishes (caller should upload any
    partial data separately).

    *picam2* must already be configured and **started** in capture mode.
    """
    chunk_duration = chunk_duration or config.RECORD_DURATION_S
    cap_dir = _ensure_capture_dir()
    ts = int(time.time() * 1000)
    video_h264 = cap_dir / f"chunk-{ts}.h264"
    audio_wav = cap_dir / f"chunk-{ts}.wav"
    output_file = cap_dir / f"chunk-{ts}.mp4"

    audio_device = _resolve_audio_device()

    log.info("Chunk capture %ds  audio=%s", chunk_duration, audio_device or "(none)")

    # Audio – use arecord (more reliable than ffmpeg ALSA input on I2S drivers)
    audio_proc = None
    has_audio = audio_device is not None
    if has_audio:
        audio_cmd = [
            "arecord",
            "-D", audio_device,
            "-f", "S16_LE",
            "-r", str(config.AUDIO_SAMPLE_RATE),
            "-c", "1",
            "-d", str(chunk_duration),
            str(audio_wav),
        ]
        log.info("Audio command: %s", " ".join(audio_cmd))
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
        try:
            audio_proc.wait(timeout=chunk_duration + 10)
        except subprocess.TimeoutExpired:
            log.warning("Audio process did not exit in time – killing")
            audio_proc.kill()
            audio_proc.wait(timeout=5)
        # Always log audio stderr for diagnostics
        audio_stderr = audio_proc.stderr.read().decode(errors="replace") if audio_proc.stderr else ""
        if audio_stderr:
            log.info("Audio capture stderr: %s", audio_stderr.strip()[-800:])

        if audio_proc.returncode not in (0, -15):  # -15 = SIGTERM
            log.warning("Audio process exited with code %d", audio_proc.returncode)
            has_audio = False
        else:
            wav_size = audio_wav.stat().st_size if audio_wav.exists() else 0
            expected_size = config.AUDIO_SAMPLE_RATE * 2 * chunk_duration  # 16-bit mono
            log.info("Chunk audio done (%s, %.1f KB, expected ~%.0f KB)",
                     audio_wav, wav_size / 1024, expected_size / 1024)
            if wav_size < expected_size * 0.5:
                log.warning("Audio WAV is too short! Got %.1f KB, expected ~%.0f KB",
                            wav_size / 1024, expected_size / 1024)
                has_audio = False

    # Mux
    if has_audio and audio_wav.exists():
        af_filter = f"volume={config.AUDIO_GAIN_DB}dB" if config.AUDIO_GAIN_DB else None
        mux_cmd = [
            "ffmpeg", "-y",
            "-f", "h264",                   # explicit demuxer for raw H.264
            "-framerate", str(config.VIDEO_FPS),  # tell demuxer the fps
            "-i", str(video_h264),
            "-i", str(audio_wav),
            "-map", "0:v",          # video from first input
            "-map", "1:a",          # audio from second input
            "-c:v", "copy",
            *((["-af", af_filter] if af_filter else [])),
            "-c:a", "aac", "-b:a", "64k",
            "-movflags", "+faststart",
            str(output_file),
        ]
    else:
        mux_cmd = [
            "ffmpeg", "-y",
            "-i", str(video_h264),
            "-c:v", "copy", "-an", "-movflags", "+faststart",
            str(output_file),
        ]

    log.info("Mux command: %s", " ".join(mux_cmd))
    mux_result = subprocess.run(
        mux_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120,
    )
    mux_stderr = mux_result.stderr.decode(errors="replace")
    if mux_result.returncode != 0:
        log.error("Chunk mux failed (%d): %s", mux_result.returncode, mux_stderr[-500:])
        video_h264.unlink(missing_ok=True)
        audio_wav.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg chunk mux failed: {mux_stderr[:500]}")
    else:
        if mux_stderr:
            log.info("Mux stderr: %s", mux_stderr[-500:])

    video_h264.unlink(missing_ok=True)
    audio_wav.unlink(missing_ok=True)

    # Verify the output has audio
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_name,duration,bit_rate",
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
