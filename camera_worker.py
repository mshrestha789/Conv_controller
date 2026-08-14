"""Persistent isolated camera worker.

The camera is initialized once and kept running for the life of this worker.
The GUI sends one JSON command per line on stdin. The worker replies with one
JSON event per line on stdout.

Keeping Picamera2 initialized avoids the several-second startup cost before
every photo, while process isolation still lets the GUI kill this worker if a
camera/libcamera call hangs.
"""

import json
import sys
from pathlib import Path

from config import CAMERA_TYPE, PICAMERA_STILL_SIZE, USB_CAMERA_INDEX


def send_event(**payload):
    print(json.dumps(payload), flush=True)


def init_picamera():
    from picamera2 import Picamera2

    if not Picamera2.global_camera_info():
        raise RuntimeError("No CSI camera detected.")

    picam2 = Picamera2()

    if PICAMERA_STILL_SIZE is None:
        camera_config = picam2.create_still_configuration()
    else:
        camera_config = picam2.create_still_configuration(
            main={"size": tuple(PICAMERA_STILL_SIZE)}
        )

    picam2.configure(camera_config)
    picam2.start()
    return picam2


def init_usb_camera():
    import cv2

    camera = cv2.VideoCapture(USB_CAMERA_INDEX)
    if not camera.isOpened():
        camera.release()
        raise RuntimeError("USB camera could not be opened.")
    return camera


def capture_picamera(camera, save_path: Path):
    camera.capture_file(str(save_path))


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
            try:
                save_path.unlink(missing_ok=True)
            except OSError:
                pass

            try:
                if CAMERA_TYPE == "picamera2":
                    capture_picamera(camera, save_path)
                else:
                    capture_usb(camera, save_path)

                if not save_path.exists() or save_path.stat().st_size == 0:
                    raise RuntimeError("Camera returned without creating a valid image.")

                send_event(
                    event="capture",
                    success=True,
                    path=str(save_path),
                    message="",
                )

            except Exception as error:
                try:
                    save_path.unlink(missing_ok=True)
                except OSError:
                    pass

                send_event(
                    event="capture",
                    success=False,
                    path=str(save_path),
                    message=f"Camera capture failed: {error}",
                )

    except Exception as error:
        send_event(event="fatal", message=str(error))
        return 2

    finally:
        close_camera(camera)


if __name__ == "__main__":
    raise SystemExit(main())
