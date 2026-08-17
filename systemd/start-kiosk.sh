#!/bin/bash
set -u

# This helper is launched by the Raspberry Pi desktop autostart mechanism,
# so the graphical-session environment already exists. Import it into the
# user's systemd manager before starting the supervised conveyor service.
/usr/bin/systemctl --user import-environment \
    DISPLAY WAYLAND_DISPLAY XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS 2>/dev/null || true

/usr/bin/systemctl --user start stem-conveyor.service
