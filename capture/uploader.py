"""
Upload a captured recording to the vidaugment backend and finish the session.

Mirrors the browser app.js logic:
  1.  POST /api/uploadVideo?masterSessionId=<ID>   (multipart/form-data)
  2.  POST /api/masterSession/<ID>/finish
"""

import logging
import time
from pathlib import Path

import requests

from . import config

log = logging.getLogger(__name__)

_UPLOAD_TIMEOUT_S = 120
_FINISH_TIMEOUT_S = 60
_MAX_RETRIES = 3
_RETRY_BACKOFF = [5, 15, 30]  # seconds between retries


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
    _mime_map = {
        ".h264": "video/h264",
        ".wav": "audio/wav",
        ".mp4": "video/mp4",
        ".mkv": "video/x-matroska",
    }
    mime = _mime_map.get(file_path.suffix, "application/octet-stream")
    media_type = "audio" if file_path.suffix == ".wav" else "video"
    filename = file_path.name

    if chunk_index is not None:
        params["chunkIndex"] = str(chunk_index)
    params["mediaType"] = media_type

    log.info(
        "Uploading %s (%.1f KB) to %s  masterSessionId=%s",
        filename,
        file_path.stat().st_size / 1024,
        url,
        master_session_id,
    )

    last_exc = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            with open(file_path, "rb") as fh:
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
        resp = requests.post(url, timeout=5)
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
