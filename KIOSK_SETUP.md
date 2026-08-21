# Kiosk / Developer Mode Update

This guide applies to the barcode-batch kiosk revision of
`mshrestha789/Conv_controller`.

## New operating model

- Raspberry Pi boots to the desktop session and automatically starts the
  systemd-supervised conveyor application.
- Application opens full screen.
- A USB HID barcode scan opens a batch but never starts the conveyor.
- The operator starts, pauses, completes, or cancels each batch explicitly.
- Images and a manifest are stored under a unique barcode session folder.
- An incomplete active batch is offered for recovery after restart.
- The visible `Exit` button becomes `SHUT DOWN` and powers off the Pi only
  after commanding the conveyor OFF.
- Normal window close / Alt+F4 is ignored by the application.
- Touch access: press and hold `STEM IMAGING STATION` for 5 seconds, then
  release it.
- Backup keyboard shortcut: `Ctrl+Alt+Shift+D`.
- Developer PIN entry uses the application's on-screen numeric keypad, so an
  operating-system keyboard is not required.
- First use asks you to create a 4-12 digit PIN.
  Only a salted PBKDF2 hash is stored.
- After authentication, the Developer Menu offers:
  - **Configuration**
  - **Exit to Desktop**
  - **Cancel**
- Developer Configuration stops the conveyor and lets you change operational
  calibration without editing Python source.
- Developer exit is a clean exit, so `Restart=on-failure` does not immediately
  reopen the application. It starts again on the next desktop login/boot.

## Developer-adjustable settings

Stored in `~/stem_conveyor/settings.json`:

- sensor active LOW/HIGH interpretation
- sensor debounce time
- stuck-active timeout
- no-detection timeout
- sensor-to-stop delay
- belt-settle delay
- post-photo restart delay
- final-sample belt runout (0-5 seconds; 0 disables it)
- direction-change dead time
- camera capture timeout

Hardware-critical values are deliberately **not** editable in the GUI:

- sensor GPIO 17
- forward GPIO 22
- reverse GPIO 27
- active-low relay polarity
- camera type / resolution

## Barcode scanner connection

Connect the scanner's USB cable to the Raspberry Pi. It must be configured as
a USB keyboard (HID) and send Enter after each barcode. At the desktop, a quick
test is:

```bash
python3 -c 'code=input("Scan a barcode: "); print("Received:", repr(code))'
```

The scanner can remain in its motion-sensing mode. Its separate red and black
trigger wires are not needed: do not connect them to Raspberry Pi GPIO or 5 V.
Insulate each conductor separately. The application ignores scans outside the
barcode field once a batch is active, and scanning never starts the belt.

Install or update the complete repository as one version. Do not copy only the
GUI file because barcode storage, atomic camera capture, recovery, and kiosk
shutdown behavior span `gui.py`, `storage.py`, `camera.py`, `camera_worker.py`,
`config.py`, and `kiosk_gui.py`.

## Install auto-start and shutdown permission

After placing the files in `~/Conv_controller`:

```bash
cd ~/Conv_controller
chmod +x install_kiosk.sh systemd/start-kiosk.sh
./install_kiosk.sh
```

The installer asks for your sudo password once so it can install a narrowly
scoped sudoers rule that allows the logged-in `conveyer` account to run only:

```text
/usr/bin/systemctl poweroff
```

Then reboot:

```bash
sudo reboot
```

## First developer login

On the touchscreen, press and hold the `STEM IMAGING STATION` title for 5
seconds. Release the title when instructed. The on-screen numeric PIN keypad
will open.

Alternatively, while the full-screen conveyor application has focus, press:

```text
Ctrl + Alt + Shift + D
```

On the first use, create and confirm your developer PIN using the on-screen
keypad. On later uses, enter that PIN to open the Developer Menu.

If you forget the developer PIN, remove this file from a developer shell/SSH
session and create a new PIN on the next shortcut use:

```text
~/stem_conveyor/developer_auth.json
```

## Important limitation

This is an application-level kiosk. It blocks the application's ordinary
window-close route, but the Raspberry Pi desktop/window manager may still have
system-wide shortcuts such as terminal launchers or task switching. If the
student keyboard must be locked down against intentional OS access, configure
those shortcuts separately in the Raspberry Pi desktop/window-manager kiosk
profile. Do not rely on Python GUI code as the security boundary.
