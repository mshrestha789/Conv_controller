#!/bin/bash
set -euo pipefail

EXPECTED_REPO="$HOME/Conv_controller"
SERVICE_SOURCE="$EXPECTED_REPO/systemd/stem-conveyor.service"
AUTOSTART_SOURCE="$EXPECTED_REPO/systemd/stem-conveyor-autostart.desktop"
START_SOURCE="$EXPECTED_REPO/systemd/start-kiosk.sh"

if [[ ! -f "$EXPECTED_REPO/main.py" ]]; then
    echo "Expected project at: $EXPECTED_REPO"
    echo "Move/copy the updated files into that folder before running this installer."
    exit 1
fi

mkdir -p "$HOME/.config/systemd/user"
mkdir -p "$HOME/.config/autostart"

cp "$SERVICE_SOURCE" "$HOME/.config/systemd/user/stem-conveyor.service"
cp "$AUTOSTART_SOURCE" "$HOME/.config/autostart/stem-conveyor.desktop"
chmod +x "$START_SOURCE"

systemctl --user daemon-reload

# Do not enable the service directly at user-manager startup. The desktop
# autostart entry starts it after the graphical session exists, then systemd
# provides Restart=on-failure and WatchdogSec supervision.
systemctl --user disable stem-conveyor.service >/dev/null 2>&1 || true

SUDOERS_FILE="/etc/sudoers.d/stem-conveyor-poweroff"
TEMP_FILE="$(mktemp)"
printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl poweroff\n' "$USER" > "$TEMP_FILE"
chmod 0440 "$TEMP_FILE"

if ! sudo /usr/sbin/visudo -cf "$TEMP_FILE"; then
    echo "Generated sudoers rule failed validation. Nothing was installed there."
    rm -f "$TEMP_FILE"
    exit 1
fi

sudo cp "$TEMP_FILE" "$SUDOERS_FILE"
sudo chmod 0440 "$SUDOERS_FILE"
rm -f "$TEMP_FILE"

echo
echo "Kiosk installation completed."
echo "  User service: ~/.config/systemd/user/stem-conveyor.service"
echo "  Desktop autostart: ~/.config/autostart/stem-conveyor.desktop"
echo "  Power-off permission: $SUDOERS_FILE"
echo
echo "Reboot to test automatic startup."
