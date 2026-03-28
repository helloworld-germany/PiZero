"""Camera helper – manages picamera2 lifecycle and mode switching."""

import logging

from . import config

log = logging.getLogger(__name__)


def create_camera():
    """Create and return a Picamera2 instance (not yet started)."""
    from picamera2 import Picamera2  # type: ignore[import-untyped]

    picam2 = Picamera2()
    return picam2


def configure_qr_mode(picam2):
    """
    Configure the camera for low-power QR scanning:
    low resolution, low frame rate.
    """
    from picamera2 import Picamera2  # type: ignore[import-untyped]
    from libcamera import controls  # type: ignore[import-untyped]

    cam_config = picam2.create_still_configuration(
        main={"size": (config.QR_SCAN_WIDTH, config.QR_SCAN_HEIGHT), "format": "RGB888"},
    )
    picam2.configure(cam_config)
    picam2.start()
    # Lower frame rate for power saving
    try:
        picam2.set_controls({
            "FrameRate": config.QR_SCAN_FPS,
        })
    except Exception as exc:
        log.debug("Could not set QR scan frame rate: %s", exc)

    log.info(
        "Camera in QR-scan mode (%dx%d @ %d fps)",
        config.QR_SCAN_WIDTH, config.QR_SCAN_HEIGHT, config.QR_SCAN_FPS,
    )


def configure_capture_mode(picam2):
    """
    Configure the camera for high-quality 20s video capture.
    """
    cam_config = picam2.create_video_configuration(
        main={"size": (config.VIDEO_WIDTH, config.VIDEO_HEIGHT), "format": "YUV420"},
    )
    picam2.configure(cam_config)
    picam2.start()
    try:
        picam2.set_controls({
            "FrameRate": config.VIDEO_FPS,
        })
    except Exception as exc:
        log.debug("Could not set capture frame rate: %s", exc)

    log.info(
        "Camera in capture mode (%dx%d @ %d fps)",
        config.VIDEO_WIDTH, config.VIDEO_HEIGHT, config.VIDEO_FPS,
    )
