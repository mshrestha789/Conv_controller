import shutil
from pathlib import Path

from config import (
    IMAGE_DIR,
    USB_MOUNT_ROOTS,
    USB_IMAGE_FOLDER,
)


class StorageManager:
    """
    Local image storage and USB-copy helper.
    """

    def __init__(self):
        self.ensure_image_directory()

    # ========================================================
    # LOCAL IMAGES
    # ========================================================

    def ensure_image_directory(self):
        IMAGE_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

    def get_images(self):
        self.ensure_image_directory()

        return sorted(
            IMAGE_DIR.glob("*.jpg")
        )

    def delete_image(self, image_path: Path):
        if image_path is None:
            return False

        try:
            image_path.unlink(
                missing_ok=True
            )
            return True

        except Exception as error:
            print("Could not delete image.")
            print(error)
            return False

    # ========================================================
    # USB
    # ========================================================

    def find_usb_mount(self):
        """
        Search common Linux/Raspberry Pi mount locations.

        Returns a Path if a mounted directory is found,
        otherwise None.
        """
        checked = set()

        for root in USB_MOUNT_ROOTS:
            root = Path(root).expanduser()

            try:
                resolved = root.resolve()
            except OSError:
                resolved = root

            if resolved in checked:
                continue

            checked.add(resolved)

            if not root.exists():
                continue

            try:
                candidates = [
                    item
                    for item in root.iterdir()
                    if item.is_dir()
                ]

                if candidates:
                    return candidates[0]

            except (PermissionError, OSError):
                continue

        return None

    def copy_image_to_usb(self, image_path: Path):
        usb_mount = self.find_usb_mount()

        if usb_mount is None:
            return None

        destination_dir = (
            usb_mount / USB_IMAGE_FOLDER
        )

        destination_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        destination = (
            destination_dir / image_path.name
        )

        shutil.copy2(
            image_path,
            destination,
        )

        return destination

    def copy_all_images_to_usb(self, image_files):
        usb_mount = self.find_usb_mount()

        if usb_mount is None:
            return None

        destination_dir = (
            usb_mount / USB_IMAGE_FOLDER
        )

        destination_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        copied = 0

        for image_path in image_files:
            shutil.copy2(
                image_path,
                destination_dir / image_path.name,
            )
            copied += 1

        return copied
