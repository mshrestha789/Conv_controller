"""Persistent isolated camera worker.

The camera is initialized once and kept running for the life of this worker.
The GUI sends one JSON command per line on stdin. The worker replies with one
JSON event per line on stdout.

Keeping Picamera2 initialized avoids the several-second startup cost before
every photo, while process isolation still lets the GUI kill this worker if a
camera/libcamera call hangs.
"""

import json
import os
import sys
from pathlib import Path

from config import (
    CAMERA_TYPE, PICAMERA_STILL_SIZE, USB_CAMERA_INDEX,
    CAMERA_FOCUS_ROI, CAMERA_FOCUS_CANDIDATES, CAMERA_MIN_SHARPNESS,
    CAMERA_JPEG_QUALITY, CAMERA_CAPTURE_TIMEOUT_SEC,
)
from camera_focus import (
    validate_settings, sensor_window, image_window, sharpness_score, capture_best,
)


def send_event(**payload):
    print(json.dumps(payload), flush=True)


def init_picamera():
    from picamera2 import Picamera2

    if not Picamera2.global_camera_info():
        raise RuntimeError("No CSI camera detected.")

    from libcamera import controls

    validate_settings(CAMERA_FOCUS_ROI, CAMERA_FOCUS_CANDIDATES, CAMERA_MIN_SHARPNESS)
    picam2 = Picamera2()
    try:
        main = {"format": "YUV420"}
        if PICAMERA_STILL_SIZE is not None:
            main["size"] = tuple(PICAMERA_STILL_SIZE)
        picam2.configure(picam2.create_still_configuration(
            main=main, raw=None, buffer_count=4, queue=False,
        ))
        required = {"AfMode", "AfTrigger", "AfRange", "AfMetering", "AfWindows"}
        missing = required - set(picam2.camera_controls)
        if missing:
            raise RuntimeError(f"Camera lacks autofocus controls: {sorted(missing)}")
        window = sensor_window(picam2.camera_properties["ScalerCropMaximum"], CAMERA_FOCUS_ROI)
        picam2.set_controls({
            "AfMode": controls.AfModeEnum.Auto,
            "AfRange": controls.AfRangeEnum.Full,
            "AfMetering": controls.AfMeteringEnum.Windows,
            # Picamera2 converts coordinate tuples into libcamera Rectangles.
            "AfWindows": [window],
        })
        picam2.options["quality"] = CAMERA_JPEG_QUALITY
        picam2.start()
        return picam2
    except Exception:
        picam2.close()
        raise


def init_usb_camera():
    import cv2

    camera = cv2.VideoCapture(USB_CAMERA_INDEX)
    if not camera.isOpened():
        camera.release()
        raise RuntimeError("USB camera could not be opened.")
    return camera


def capture_picamera(camera, save_path: Path, timeout_sec=CAMERA_CAPTURE_TIMEOUT_SEC):
    from picamera2 import MappedArray
    from libcamera import controls

    window = sensor_window(camera.camera_properties["ScalerCropMaximum"], CAMERA_FOCUS_ROI)

    def score_request(request, focus_window):
        width, height = request.config["main"]["size"]
        box = image_window(focus_window, request.get_metadata()["ScalerCrop"], (width, height))
        left, top, right, bottom = box
        with MappedArray(request, "main") as mapped:
            # Only Y (luminance) is scored. The complete request is saved later.
            return sharpness_score(mapped.array[:height, :width][top:bottom, left:right])

    return capture_best(
        camera, save_path, window=window, candidates=CAMERA_FOCUS_CANDIDATES,
        minimum=CAMERA_MIN_SHARPNESS, timeout_sec=timeout_sec,
        focused_state=controls.AfStateEnum.Focused, score_request=score_request,
    )


def capture_usb(camera, save_path: Path):
    import cv2

    success, frame = camera.read()
    if not success:
        raise RuntimeError("USB camera did not return an image.")
    if not cv2.imwrite(str(save_path), frame):
        raise RuntimeError("OpenCV could not save the image.")


def close_camera(camera):
    if camera is None:
        return

    if CAMERA_TYPE == "picamera2":
        try:
            camera.stop()
        except Exception:
            pass
        try:
            camera.close()
        except Exception:
            pass
    elif CAMERA_TYPE == "usb":
        try:
            camera.release()
        except Exception:
            pass


def partial_capture_path(save_path: Path):
    """Keep an interrupted JPEG out of the final batch image list."""
    return save_path.with_name(
        f".{save_path.stem}.partial{save_path.suffix}"
    )


def commit_capture(temporary_path: Path, save_path: Path):
    """Flush image data, then atomically expose the final JPEG name."""
    with temporary_path.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary_path, save_path)

    try:
        directory_fd = os.open(save_path.parent, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def main():
    camera = None

    try:
        if CAMERA_TYPE == "picamera2":
            camera = init_picamera()
        elif CAMERA_TYPE == "usb":
            camera = init_usb_camera()
        else:
            raise RuntimeError(f"Unsupported CAMERA_TYPE: {CAMERA_TYPE}")

        send_event(event="ready")

        for raw_line in sys.stdin:
            raw_line = raw_line.strip()
            if not raw_line:
                continue

            try:
                command = json.loads(raw_line)
            except json.JSONDecodeError as error:
                send_event(event="error", message=f"Invalid command: {error}")
                continue

            action = command.get("cmd")

            if action == "shutdown":
                send_event(event="shutdown")
                return 0

            if action != "capture":
                send_event(event="error", message=f"Unknown command: {action}")
                continue

            save_path = Path(command.get("path", "")).expanduser()
            if not str(save_path):
                send_event(event="capture", success=False, path="", message="Missing output path.")
                continue

            save_path.parent.mkdir(parents=True, exist_ok=True)
            temporary_path = partial_capture_path(save_path)
            try:
                save_path.unlink(missing_ok=True)
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

            try:
                if CAMERA_TYPE == "picamera2":
                    quality = capture_picamera(camera, temporary_path, command.get("timeout_sec", CAMERA_CAPTURE_TIMEOUT_SEC))
                else:
                    capture_usb(camera, temporary_path)
                    quality = None

                if (
                    not temporary_path.exists()
                    or temporary_path.stat().st_size == 0
                ):
                    raise RuntimeError("Camera returned without creating a valid image.")

                commit_capture(temporary_path, save_path)

                send_event(
                    event="capture",
                    success=True,
                    path=str(save_path),
                    message="",
                    quality=quality,
                )

            except Exception as error:
                try:
                    save_path.unlink(missing_ok=True)
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass

                send_event(
                    event="capture",
                    success=False,
                    path=str(save_path),
                    message=f"Camera capture failed: {error}",
                )
                # Never reuse a camera with a potentially outstanding async job.
                return 2

    except Exception as error:
        send_event(event="fatal", message=str(error))
        return 2

    finally:
        close_camera(camera)


if __name__ == "__main__":
    raise SystemExit(main())
