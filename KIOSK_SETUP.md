# Kiosk / Developer Mode Update

This update is designed as a small overlay on the currently working
`mshrestha789/Conv_controller` code. It intentionally leaves `gui.py`,
`camera.py`, `camera_worker.py`, `hardware.py`, `storage.py`, and `watchdog.py`
unchanged.

## New operating model

- Raspberry Pi boots to the desktop session and automatically starts the
  systemd-supervised conveyor application.
- Application opens full screen.
- The visible `Exit` button becomes `SHUT DOWN` and powers off the Pi only
  after commanding the conveyor OFF.
- Normal window close / Alt+F4 is ignored by the application.
- Hidden developer shortcut: `Ctrl+Alt+Shift+D`.
- First use of the developer shortcut asks you to create a 4–12 digit PIN.
  Only a salted PBKDF2 hash is stored.
- After authentication, the Developer Menu offers:
  - **Configuration**
  - **Exit to Raspberry Pi OS**
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
- direction-change dead time
- camera capture timeout

Hardware-critical values are deliberately **not** editable in the GUI:

- sensor GPIO 17
- forward GPIO 22
- reverse GPIO 27
- active-low relay polarity
- camera type / resolution

## Files to copy into `~/Conv_controller`

Replace:

- `config.py`
- `main.py`
- `systemd/stem-conveyor.service`

Add:

- `kiosk_gui.py`
- `runtime_settings.py`
- `developer_auth.py`
- `systemd/start-kiosk.sh`
- `systemd/stem-conveyor-autostart.desktop`
- `install_kiosk.sh`

Leave your currently working versions of these files unchanged:

- `gui.py`
- `camera.py`
- `camera_worker.py`
- `hardware.py`
- `storage.py`
- `watchdog.py`

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

While the full-screen conveyor application has focus, press:

```text
Ctrl + Alt + Shift + D
```

On the first use, create your developer PIN. On later uses, enter that PIN to
open the Developer Menu.

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
