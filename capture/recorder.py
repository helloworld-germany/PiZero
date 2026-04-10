"""
Split video + audio capture for maximum speed and I2S compatibility.

Architecture:
    VIDEO  – rpicam-vid writes short H.264 segments (``--segment``) to a
             RAM-backed tmpfs.  No audio flags, no ALSA interaction.
    AUDIO  – ``arecord`` (ALSA / I2S) writes matching WAV chunks
             independently on a separate thread.

Video and audio files are uploaded separately – no on-device muxing.
The backend handles combining them.

Benefits:
    • rpicam-vid never touches ALSA → eliminates I2S audio crashes.
    • Each capture runs at native speed; no blocking on the other.
    • Short-lived files on tmpfs → minimal I/O and memory pressure.
    • No ffmpeg / mux step on device → lower CPU, fewer dependencies.
    • Immediate upload + delete → frees RAM disk instantly.
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


# ──────────────────────────────────────────────────────────────────
# SplitRecorder – manages parallel video + audio capture
# ──────────────────────────────────────────────────────────────────

class SplitRecorder:
    """Parallel video (rpicam-vid) + audio (arecord) recorder."""

    def __init__(
        self,
        chunk_duration: int | None = None,
        prefix: str | None = None,
        audio_enabled: bool = True,
    ):
        self.chunk_duration = chunk_duration or config.RECORD_DURATION_S
        self.prefix = prefix or str(int(time.time() * 1000))
        self.cap_dir = _ensure_capture_dir()

        self.audio_device: str | None = (
            _resolve_audio_device() if audio_enabled else None
        )

        # Video process
        self._vid_proc: subprocess.Popen | None = None
        self._vid_stderr_buf = b""
        self._vid_stderr_lock = threading.Lock()

        # Audio thread + process
        self._aud_thread: threading.Thread | None = None
        self._aud_stop = threading.Event()
        self._aud_failed = threading.Event()   # set when arecord dies
        self._aud_proc: subprocess.Popen | None = None
        self._aud_lock = threading.Lock()

    # ── public properties ─────────────────────────────────────────

    @property
    def has_audio(self) -> bool:
        return self.audio_device is not None

    def video_alive(self) -> bool:
        return self._vid_proc is not None and self._vid_proc.poll() is None

    def audio_failed(self) -> bool:
        """True if audio was enabled but the recording thread has died."""
        return self.audio_device is not None and self._aud_failed.is_set()

    def video_stderr(self) -> str:
        with self._vid_stderr_lock:
            data = self._vid_stderr_buf
        return data.decode(errors="replace").strip() if data else ""

    # ── start / stop ──────────────────────────────────────────────

    def start(self) -> None:
        self._start_video()
        if self.audio_device:
            self._start_audio()
        log.info(
            "Split capture started (segment=%ds, audio=%s)",
            self.chunk_duration,
            self.audio_device or "(none)",
        )

    def stop(self) -> None:
        self._stop_video()
        self._stop_audio()
        log.info("Split capture stopped")

    # ── video ─────────────────────────────────────────────────────

    def _start_video(self) -> None:
        output_pattern = str(
            self.cap_dir / f"{self.prefix}_vid_%04d.h264"
        )
        cmd = [
            "rpicam-vid",
            "-t", "0",
            "--segment", str(self.chunk_duration * 1000),
            "--inline",
            "--codec", "h264",
            "--width", str(config.VIDEO_WIDTH),
            "--height", str(config.VIDEO_HEIGHT),
            "--framerate", str(config.VIDEO_FPS),
            "--bitrate", str(config.VIDEO_BITRATE),
            "-n",
            "-o", output_pattern,
        ]
        log.debug("rpicam-vid command: %s", " ".join(cmd))
        self._vid_proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )

        # Drain stderr in background to prevent pipe-buffer deadlock
        def _drain():
            buf = b""
            try:
                while True:
                    data = self._vid_proc.stderr.read(4096)
                    if not data:
                        break
                    buf = (buf + data)[-4096:]
            except Exception:
                pass
            with self._vid_stderr_lock:
                self._vid_stderr_buf = buf

        threading.Thread(target=_drain, daemon=True).start()

    def _stop_video(self) -> None:
        proc = self._vid_proc
        if proc is None or proc.poll() is not None:
            return
        proc.send_signal(_signal.SIGINT)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            log.warning("rpicam-vid did not exit – killing")
            proc.kill()
            proc.wait(timeout=5)
        stderr = self.video_stderr()
        if stderr:
            log.debug("rpicam-vid stderr: %s", stderr[-500:])

    # ── audio ─────────────────────────────────────────────────────

    def _start_audio(self) -> None:
        self._aud_stop.clear()
        self._aud_thread = threading.Thread(
            target=self._audio_loop, daemon=True,
        )
        self._aud_thread.start()

    def _audio_loop(self) -> None:
        """Record sequential WAV chunks via arecord, one per segment."""
        chunk_idx = 0  # rpicam-vid %04d starts at 0000
        while not self._aud_stop.is_set():
            output = self.cap_dir / f"{self.prefix}_aud_{chunk_idx:04d}.wav"
            cmd = [
                "arecord",
                "-D", self.audio_device,
                "-f", config.AUDIO_FORMAT,
                "-r", str(config.AUDIO_SAMPLE_RATE),
                "-c", str(config.AUDIO_CHANNELS),
                "-d", str(self.chunk_duration),
                str(output),
            ]
            log.debug("Audio chunk %d: %s", chunk_idx, " ".join(cmd))
            try:
                with self._aud_lock:
                    if self._aud_stop.is_set():
                        break
                    self._aud_proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                    )
                self._aud_proc.wait()
                rc = self._aud_proc.returncode
                if rc != 0 and not self._aud_stop.is_set():
                    stderr = self._aud_proc.stderr.read()
                    log.error(
                        "arecord exited %d: %s", rc,
                        stderr.decode(errors="replace")[:500],
                    )
                    self._aud_failed.set()
                    break
            except Exception:
                if not self._aud_stop.is_set():
                    log.exception("Audio recording error on chunk %d", chunk_idx)
                self._aud_failed.set()
                break
            chunk_idx += 1
        if self._aud_failed.is_set():
            log.warning("Audio capture has stopped – video will continue without audio")

    def _stop_audio(self) -> None:
        self._aud_stop.set()
        with self._aud_lock:
            proc = self._aud_proc
        if proc and proc.poll() is None:
            proc.send_signal(_signal.SIGINT)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
        if self._aud_thread and self._aud_thread.is_alive():
            self._aud_thread.join(timeout=10)

    # ── chunk discovery ───────────────────────────────────────────

    def find_ready_video_chunks(self) -> list[Path]:
        """Completed video segments (all except the one being written)."""
        pattern = str(self.cap_dir / f"{self.prefix}_vid_*.h264")
        files = sorted(glob.glob(pattern))
        return [Path(f) for f in files[:-1]] if len(files) > 1 else []

    def find_all_video_chunks(self) -> list[Path]:
        """All video segments (call after stop)."""
        pattern = str(self.cap_dir / f"{self.prefix}_vid_*.h264")
        return [Path(f) for f in sorted(glob.glob(pattern))]

    def _completed_audio_chunks(self) -> list[Path]:
        """Return all completed audio WAV files, sorted."""
        pattern = str(self.cap_dir / f"{self.prefix}_aud_*.wav")
        return [Path(f) for f in sorted(glob.glob(pattern))]

    def find_ready_pairs(self) -> list[tuple[Path, Path | None]]:
        """Pairs whose video is complete, matched to audio by order.

        Matching by sorted file order (not index number) is robust against
        rpicam-vid / arecord starting at different counter values.

        If audio has failed, returns video-only pairs so uploads aren't
        blocked waiting for audio that will never come.
        """
        videos = self.find_ready_video_chunks()
        if not videos:
            return []

        # No audio, or audio died → video-only
        if not self.audio_device or self._aud_failed.is_set():
            return [(v, None) for v in videos]

        audios = self._completed_audio_chunks()
        pairs: list[tuple[Path, Path | None]] = []
        for i, vid in enumerate(videos):
            if i < len(audios):
                pairs.append((vid, audios[i]))
            # else: audio not yet flushed – skip, will pick up next poll
        return pairs

    def find_all_pairs(self) -> list[tuple[Path, Path | None]]:
        """All pairs including the final chunk (call after stop).

        Unmatched videos (e.g. audio cut short) are included with
        ``audio=None`` so they still get uploaded.
        """
        videos = self.find_all_video_chunks()
        if not videos:
            return []

        if not self.audio_device or self._aud_failed.is_set():
            return [(v, None) for v in videos]

        audios = self._completed_audio_chunks()
        pairs: list[tuple[Path, Path | None]] = []
        for i, vid in enumerate(videos):
            aud = audios[i] if i < len(audios) else None
            pairs.append((vid, aud))
        return pairs


# ──────────────────────────────────────────────────────────────────
# Public API used by main.py
# ──────────────────────────────────────────────────────────────────

def start_recording(
    chunk_duration: int | None = None,
    prefix: str | None = None,
    audio_override: bool | None = None,
) -> "SplitRecorder":
    """Start split recording and return the recorder.

    *audio_override*: ``True``/``None`` = auto-detect, ``False`` = no audio.
    """
    audio_enabled = audio_override is not False
    recorder = SplitRecorder(
        chunk_duration=chunk_duration,
        prefix=prefix,
        audio_enabled=audio_enabled,
    )
    recorder.start()
    return recorder


def stop_recording(recorder: "SplitRecorder") -> None:
    """Gracefully stop both video and audio capture."""
    if recorder is not None:
        recorder.stop()


def find_ready_chunks(recorder: "SplitRecorder") -> list[Path]:
    """Return completed files (video + audio) ready for upload."""
    files: list[Path] = []
    for vid, aud in recorder.find_ready_pairs():
        files.append(vid)
        if aud:
            files.append(aud)
    return files


def find_all_chunks(recorder: "SplitRecorder") -> list[Path]:
    """Return all remaining files after stop (video + audio)."""
    files: list[Path] = []
    for vid, aud in recorder.find_all_pairs():
        files.append(vid)
        if aud:
            files.append(aud)
    return files


def drain_stderr(recorder: "SplitRecorder") -> str:
    """Return captured rpicam-vid stderr for diagnostics."""
    if isinstance(recorder, SplitRecorder):
        return recorder.video_stderr()
    return ""
