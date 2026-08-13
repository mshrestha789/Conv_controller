import time
from pathlib import Path

from config import (
    CAMERA_TYPE,
    USB_CAMERA_INDEX,
    USB_CAMERA_WARMUP_SEC,
)


class Camera:
    """
    Image-capture interface.

    Supported:
        - USB camera through OpenCV
        - Raspberry Pi CSI camera through Picamera2
    """

    def __init__(self):
        self.picam2 = None

        if CAMERA_TYPE == "picamera2":
            self._initialize_picamera()

    # ========================================================
    # PICAMERA2
    # ========================================================

    def _initialize_picamera(self):
        try:
            from picamera2 import Picamera2

            self.picam2 = Picamera2()
            config = self.picam2.create_still_configuration()
            self.picam2.configure(config)
            self.picam2.start()

            time.sleep(1.0)
            print("Picamera2 initialized.")

        except Exception as error:
            self.picam2 = None
            print("Picamera2 initialization failed.")
            print(error)

    # ========================================================
    # CAPTURE
    # ========================================================

    def capture(self, save_path: Path):
        save_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if CAMERA_TYPE == "picamera2":
            if self.picam2 is not None:
                return self._capture_picamera(save_path)

            print("Picamera2 unavailable. Trying USB camera fallback.")

        return self._capture_usb(save_path)

    def _capture_picamera(self, save_path: Path):
        try:
            self.picam2.capture_file(str(save_path))
            print(f"Image saved: {save_path}")
            return True

        except Exception as error:
            print("Picamera2 capture failed.")
            print(error)
            return False

    def _capture_usb(self, save_path: Path):
        camera = None

        try:
            import cv2

            camera = cv2.VideoCapture(USB_CAMERA_INDEX)

            if not camera.isOpened():
                print("USB camera could not be opened.")
                return False

            time.sleep(USB_CAMERA_WARMUP_SEC)

            success, frame = camera.read()

            if not success:
                print("USB camera did not return an image.")
                return False

            saved = cv2.imwrite(
                str(save_path),
                frame,
            )

            if not saved:
                print("OpenCV could not save the image.")
                return False

            print(f"Image saved: {save_path}")
            return True

        except Exception as error:
            print("USB camera capture failed.")
            print(error)
            return False

        finally:
            if camera is not None:
                camera.release()

    # ========================================================
    # CLEANUP
    # ========================================================

    def close(self):
        if self.picam2 is not None:
            try:
                self.picam2.stop()
            except Exception:
                pass

            self.picam2 = None

        print("Camera closed.")
