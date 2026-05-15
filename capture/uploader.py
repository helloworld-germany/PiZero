"""
Upload a captured recording to the vidaugment backend and finish the session.

Mirrors the browser app.js logic:
  1.  POST /api/uploadVideo?masterSessionId=<ID>   (multipart/form-data)
  2.  POST /api/masterSession/<ID>/finish
"""

import logging
import time
import wave
from pathlib import Path
import tempfile
from array import array
import importlib

try:
    audioop = importlib.import_module("audioop")
except Exception:  # pragma: no cover - Python >=3.13 may not ship audioop
    audioop = None

import requests

from . import config

log = logging.getLogger(__name__)

_UPLOAD_TIMEOUT_S = 120
_FINISH_TIMEOUT_S = 60
_MAX_RETRIES = 3
_RETRY_BACKOFF = [5, 15, 30]  # seconds between retries


def _sample_bounds(sampwidth: int) -> tuple[int, int, str]:
    if sampwidth == 2:
        return -32768, 32767, "h"
    if sampwidth == 4:
        return -2147483648, 2147483647, "i"
    raise ValueError(f"Unsupported WAV sample width for fallback path: {sampwidth}")


def _extract_left_channel(frames: bytes, sampwidth: int, nchannels: int) -> bytes:
    if nchannels <= 1:
        return frames
    if audioop is not None:
        return audioop.tomono(frames, sampwidth, 1.0, 0.0)

    _minv, _maxv, typecode = _sample_bounds(sampwidth)
    samples = array(typecode)
    samples.frombytes(frames)
    left = array(typecode, samples[::nchannels])
    return left.tobytes()


def _apply_gain(frames: bytes, sampwidth: int, gain_factor: float) -> bytes:
    if abs(gain_factor - 1.0) < 1e-6:
        return frames
    if audioop is not None:
        return audioop.mul(frames, sampwidth, gain_factor)

    minv, maxv, typecode = _sample_bounds(sampwidth)
    samples = array(typecode)
    samples.frombytes(frames)
    for i, value in enumerate(samples):
        amplified = int(value * gain_factor)
        if amplified > maxv:
            amplified = maxv
        elif amplified < minv:
            amplified = minv
        samples[i] = amplified
    return samples.tobytes()


def _prepare_wav_for_upload(file_path: Path) -> tuple[Path, bool]:
    """Return (upload_path, is_temporary) for WAV uploads.

    Applies optional channel extraction + gain in a streaming fashion to keep
    CPU and memory usage low on Pi Zero / CM4.
    """
    if file_path.suffix.lower() != ".wav":
        return file_path, False

    need_left_only = config.AUDIO_UPLOAD_LEFT_ONLY
    gain_db = config.AUDIO_UPLOAD_GAIN_DB
    gain_factor = 10.0 ** (gain_db / 20.0) if gain_db else 1.0

    if not need_left_only and abs(gain_factor - 1.0) < 1e-6:
        return file_path, False

    with wave.open(str(file_path), "rb") as src:
        nchannels = src.getnchannels()
        sampwidth = src.getsampwidth()
        framerate = src.getframerate()
        comptype = src.getcomptype()
        compname = src.getcompname()

        # If no effective transform is needed for this source shape, skip copy.
        if nchannels == 1 and abs(gain_factor - 1.0) < 1e-6:
            return file_path, False

        fd, tmp_name = tempfile.mkstemp(
            prefix=f"{file_path.stem}_upl_",
            suffix=".wav",
            dir=str(file_path.parent),
        )
        is_temporary = True

    try:
        with wave.open(str(file_path), "rb") as src, wave.open(tmp_name, "wb") as dst:
            dst.setnchannels(1 if need_left_only else nchannels)
            dst.setsampwidth(sampwidth)
            dst.setframerate(framerate)
            dst.setcomptype(comptype, compname)

            chunk_frames = 4096
            while True:
                frames = src.readframes(chunk_frames)
                if not frames:
                    break

                if need_left_only and nchannels > 1:
                    # Select only left channel (channel 0) without downmix averaging.
                    frames = _extract_left_channel(frames, sampwidth, nchannels)

                if abs(gain_factor - 1.0) >= 1e-6:
                    frames = _apply_gain(frames, sampwidth, gain_factor)

                dst.writeframes(frames)

        return Path(tmp_name), is_temporary
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    finally:
        try:
            # mkstemp returns an open fd; wave opens by path, so close fd here.
            # If already closed, this is harmless.
            import os
            os.close(fd)
        except Exception:
            pass


def upload_recording(master_session_id: str, file_path: Path,
                     chunk_index: int | None = None) -> dict:
    """
    Upload *file_path* to the vidaugment backend.

    *chunk_index* ties video + audio files that belong to the same
    time segment so the backend can mux them correctly.

    Retries up to _MAX_RETRIES times with exponential backoff on network
    errors or 5xx responses.  Raises on persistent failure.
    """
    if not config.API_BASE_URL:
        raise RuntimeError("VIDAUGMENT_API_BASE_URL is not configured")

    url = f"{config.API_BASE_URL}/api/uploadVideo"
    params = {"masterSessionId": master_session_id}
    upload_path, is_temporary = _prepare_wav_for_upload(file_path)

    _mime_map = {
        ".h264": "video/h264",
        ".wav": "audio/wav",
        ".mp4": "video/mp4",
        ".mkv": "video/x-matroska",
    }
    mime = _mime_map.get(upload_path.suffix, "application/octet-stream")
    media_type = "audio" if upload_path.suffix == ".wav" else "video"
    filename = file_path.name

    if chunk_index is not None:
        params["chunkIndex"] = str(chunk_index)
    params["mediaType"] = media_type
    params["source"] = "pizero"

    log.info(
        "Uploading %s (%.1f KB) to %s  masterSessionId=%s",
        filename,
        upload_path.stat().st_size / 1024,
        url,
        master_session_id,
    )

    if is_temporary:
        log.info(
            "Audio preprocessing enabled (left_only=%s, gain_db=%s)",
            config.AUDIO_UPLOAD_LEFT_ONLY,
            config.AUDIO_UPLOAD_GAIN_DB,
        )

    last_exc = None
    try:
        for attempt in range(_MAX_RETRIES + 1):
            try:
                with open(upload_path, "rb") as fh:
                    resp = requests.post(
                        url,
                        params=params,
                        files={"file": (filename, fh, mime)},
                        timeout=_UPLOAD_TIMEOUT_S,
                    )

                if resp.ok:
                    payload = resp.json()
                    log.info(
                        "Upload success – sessionId=%s  recordings=%s",
                        payload.get("sessionId"),
                        payload.get("masterSessionRecordingCount"),
                    )
                    return payload

                # Client error (4xx) — don't retry, it won't help
                if 400 <= resp.status_code < 500:
                    body = resp.text[:500]
                    log.error("Upload failed %d (not retrying): %s", resp.status_code, body)
                    resp.raise_for_status()

                # Server error (5xx) — retry
                last_exc = requests.HTTPError(f"{resp.status_code}: {resp.text[:200]}", response=resp)
                log.warning("Upload failed %d (attempt %d/%d)",
                            resp.status_code, attempt + 1, _MAX_RETRIES + 1)

            except requests.RequestException as exc:
                last_exc = exc
                log.warning("Upload error (attempt %d/%d): %s",
                            attempt + 1, _MAX_RETRIES + 1, exc)

            if attempt < _MAX_RETRIES:
                delay = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
                log.info("Retrying in %ds …", delay)
                time.sleep(delay)

        log.error("Upload failed after %d attempts", _MAX_RETRIES + 1)
        raise last_exc
    finally:
        _cleanup_temp_upload_file(upload_path, is_temporary)
    

def _cleanup_temp_upload_file(path: Path, is_temporary: bool) -> None:
    if is_temporary:
        path.unlink(missing_ok=True)


def finish_session(master_session_id: str) -> dict:
    """POST /api/masterSession/<id>/finish  — marks session as finished."""
    if not config.API_BASE_URL:
        raise RuntimeError("VIDAUGMENT_API_BASE_URL is not configured")

    url = f"{config.API_BASE_URL}/api/masterSession/{master_session_id}/finish"
    log.info("Finishing session %s …", master_session_id)

    last_exc = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            resp = requests.post(url, timeout=_FINISH_TIMEOUT_S)
            if resp.ok:
                payload = resp.json()
                log.info("Session %s finished", master_session_id)
                return payload

            if 400 <= resp.status_code < 500:
                body = resp.text[:500]
                log.error("Finish failed %d (not retrying): %s", resp.status_code, body)
                resp.raise_for_status()

            last_exc = requests.HTTPError(f"{resp.status_code}: {resp.text[:200]}", response=resp)
            log.warning("Finish failed %d (attempt %d/%d)",
                        resp.status_code, attempt + 1, _MAX_RETRIES + 1)

        except requests.RequestException as exc:
            last_exc = exc
            log.warning("Finish error (attempt %d/%d): %s",
                        attempt + 1, _MAX_RETRIES + 1, exc)

        if attempt < _MAX_RETRIES:
            delay = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]
            log.info("Retrying finish in %ds …", delay)
            time.sleep(delay)

    log.error("Finish failed after %d attempts", _MAX_RETRIES + 1)
    raise last_exc


def connect_session(master_session_id: str) -> None:
    """POST /api/masterSession/<id>/connect — fire-and-forget device connect."""
    if not config.API_BASE_URL:
        return

    url = f"{config.API_BASE_URL}/api/masterSession/{master_session_id}/connect"
    try:
        resp = requests.post(url, params={
            "deviceId": config.DEVICE_ID,
            "source": "pizero",
        }, timeout=5)
        if resp.ok:
            log.info("Connected to session %s", master_session_id)
        else:
            log.warning("Connect call returned %d", resp.status_code)
    except Exception as exc:
        log.warning("Connect call failed (non-blocking): %s", exc)


def notify_pause(master_session_id: str, paused: bool) -> None:
    """POST /api/masterSession/<id>/pause — fire-and-forget pause/resume notification."""
    if not config.API_BASE_URL:
        return

    url = f"{config.API_BASE_URL}/api/masterSession/{master_session_id}/pause"
    try:
        resp = requests.post(url, json={"paused": paused}, timeout=5)
        if resp.ok:
            log.info("Notified backend: session %s %s",
                     master_session_id, "paused" if paused else "resumed")
        else:
            log.warning("Pause notify returned %d", resp.status_code)
    except Exception as exc:
        log.warning("Pause notify failed (non-blocking): %s", exc)
