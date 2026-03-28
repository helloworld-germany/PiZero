"""
Upload a captured recording to the vidaugment backend and finish the session.

Mirrors the browser app.js logic:
  1.  POST /api/uploadVideo?masterSessionId=<ID>   (multipart/form-data)
  2.  POST /api/masterSession/<ID>/finish
"""

import logging
from pathlib import Path

import requests

from . import config

log = logging.getLogger(__name__)

_UPLOAD_TIMEOUT_S = 120
_FINISH_TIMEOUT_S = 30


def upload_recording(master_session_id: str, file_path: Path) -> dict:
    """
    Upload *file_path* to the vidaugment backend.

    Returns the JSON response payload on success.
    Raises on HTTP or network errors.
    """
    if not config.API_BASE_URL:
        raise RuntimeError("VIDAUGMENT_API_BASE_URL is not configured")

    url = f"{config.API_BASE_URL}/api/uploadVideo"
    params = {"masterSessionId": master_session_id}
    mime = "video/mp4"
    filename = file_path.name

    log.info(
        "Uploading %s (%.1f KB) to %s  masterSessionId=%s",
        filename,
        file_path.stat().st_size / 1024,
        url,
        master_session_id,
    )

    with open(file_path, "rb") as fh:
        resp = requests.post(
            url,
            params=params,
            files={"file": (filename, fh, mime)},
            timeout=_UPLOAD_TIMEOUT_S,
        )

    if not resp.ok:
        body = resp.text[:500]
        log.error("Upload failed %d: %s", resp.status_code, body)
        resp.raise_for_status()

    payload = resp.json()
    log.info(
        "Upload success – sessionId=%s  recordings=%s",
        payload.get("sessionId"),
        payload.get("masterSessionRecordingCount"),
    )
    return payload


def finish_session(master_session_id: str) -> dict:
    """POST /api/masterSession/<id>/finish  — marks session as finished."""
    if not config.API_BASE_URL:
        raise RuntimeError("VIDAUGMENT_API_BASE_URL is not configured")

    url = f"{config.API_BASE_URL}/api/masterSession/{master_session_id}/finish"
    log.info("Finishing session %s …", master_session_id)

    resp = requests.post(url, timeout=_FINISH_TIMEOUT_S)
    if not resp.ok:
        body = resp.text[:500]
        log.error("Finish failed %d: %s", resp.status_code, body)
        resp.raise_for_status()

    payload = resp.json()
    log.info("Session %s finished", master_session_id)
    return payload


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
