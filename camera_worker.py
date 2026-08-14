"""Isolated camera capture worker.

One worker process is launched per photo. A stuck libcamera/Picamera2 call can
therefore be timed out without blocking the Qt GUI or STOP button.
"""

import sys
import time
from pathlib import Path

from config import (
    CAMERA_TYPE,
    CAMERA_WARMUP_SEC,
    PICAMERA_STILL_SIZE,
    USB_CAMERA_INDEX,
    USB_CAMERA_WARMUP_SEC,
)


def capture_picamera(save_path: Path) -> int:
    picam2 = None

    try:
        from picamera2 import Picamera2

        if not Picamera2.global_camera_info():
            print("No CSI camera detected.", file=sys.stderr)
            return 2

        picam2 = Picamera2()

        if PICAMERA_STILL_SIZE is None:
            camera_config = picam2.create_still_configuration()
        else:
            camera_config = picam2.create_still_configuration(
                main={"size": tuple(PICAMERA_STILL_SIZE)}
            )

        picam2.configure(camera_config)
        picam2.start()

        time.sleep(CAMERA_WARMUP_SEC)
        picam2.capture_file(str(save_path))

        if not save_path.exists() or save_path.stat().st_size == 0:
            print(
                "Camera returned without creating a valid image.",
                file=sys.stderr,
            )
            return 3

        return 0

    except Exception as error:
        print(f"Picamera2 capture failed: {error}", file=sys.stderr)
        return 4

    finally:
        if picam2 is not None:
            try:
                picam2.stop()
            except Exception:
                pass

            try:
                picam2.close()
            except Exception:
                pass


def capture_usb(save_path: Path) -> int:
    camera = None

    try:
        import cv2

        camera = cv2.VideoCapture(USB_CAMERA_INDEX)

        if not camera.isOpened():
            print("USB camera could not be opened.", file=sys.stderr)
            return 5

        time.sleep(USB_CAMERA_WARMUP_SEC)
        success, frame = camera.read()

        if not success:
            print("USB camera did not return an image.", file=sys.stderr)
            return 6

        if not cv2.imwrite(str(save_path), frame):
            print("OpenCV could not save the image.", file=sys.stderr)
            return 7

        return 0

    except Exception as error:
        print(f"USB camera capture failed: {error}", file=sys.stderr)
        return 8

    finally:
        if camera is not None:
            camera.release()


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: camera_worker.py OUTPUT_PATH", file=sys.stderr)
        return 64

    save_path = Path(sys.argv[1]).expanduser()
    save_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        save_path.unlink(missing_ok=True)
    except OSError:
        pass

    if CAMERA_TYPE == "picamera2":
        return capture_picamera(save_path)

    if CAMERA_TYPE == "usb":
        return capture_usb(save_path)

    print(f"Unsupported CAMERA_TYPE: {CAMERA_TYPE}", file=sys.stderr)
    return 65


if __name__ == "__main__":
    raise SystemExit(main())
