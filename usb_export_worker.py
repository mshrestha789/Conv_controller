"""Background USB export process for the conveyor GUI."""

import argparse
import json
import sys
from pathlib import Path

from storage import StorageManager


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("usb_mount", type=Path)
    arguments = parser.parse_args()

    try:
        copied, destination = StorageManager.export_session_to_usb(
            arguments.manifest,
            arguments.usb_mount,
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "success": False,
                    "error": str(error),
                }
            ),
            flush=True,
        )
        return 1

    print(
        json.dumps(
            {
                "success": True,
                "copied": copied,
                "destination": str(destination),
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
