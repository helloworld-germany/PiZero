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
    Configure the camera for fast, reliable QR scanning:
    moderate resolution, continuous autofocus, video stream.
    """
    from libcamera import controls as libcamera_controls  # type: ignore[import-untyped]

    cam_config = picam2.create_video_configuration(
        main={"size": (config.QR_SCAN_WIDTH, config.QR_SCAN_HEIGHT), "format": "YUV420"},
        buffer_count=4,
    )
    picam2.configure(cam_config)
    picam2.start()
    try:
        picam2.set_controls({
            "FrameRate": config.QR_SCAN_FPS,
            "AfMode": libcamera_controls.AfModeEnum.Continuous,
            "AfSpeed": libcamera_controls.AfSpeedEnum.Fast,
        })
        log.info("Continuous autofocus enabled")
    except Exception as exc:
        log.warning("Could not enable autofocus (fixed-focus camera?): %s", exc)

    log.info(
        "Camera in QR-scan mode (%dx%d @ %d fps)",
        config.QR_SCAN_WIDTH, config.QR_SCAN_HEIGHT, config.QR_SCAN_FPS,
    )
