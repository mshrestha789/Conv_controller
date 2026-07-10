# Stem Conveyor Imaging System

This project is a Raspberry Pi-based conveyor control and imaging system. The system detects a stem/object using a proximity sensor, stops the conveyor, captures an image using a camera, saves the image locally, and allows the user to view or copy images to a USB drive using a touchscreen GUI.

## System Workflow

```text
Proximity sensor detects stem
        ↓
Raspberry Pi stops conveyor through relay/contactor
        ↓
Camera captures image
        ↓
Image is saved locally on Raspberry Pi
        ↓
User views images on touchscreen
        ↓
User copies selected/all images to USB drive
```

## Main Features

- Touchscreen GUI using PySide6
- Conveyor start/stop control
- Proximity sensor detection
- Automatic image capture when stem is detected
- Manual image capture button
- Local image storage
- Image preview/gallery
- Copy current image to USB drive
- Copy all images to USB drive
- Delete saved images
- Status display for system, conveyor, sensor, USB, and image count

## Hardware Used

| Component | Purpose |
|---|---|
| Raspberry Pi | Main controller and GUI system |
| Camera | Captures stem/object image |
| Proximity sensor | Detects stem/object |
| Relay module | Raspberry Pi low-voltage relay control |
| Contactor | Switches conveyor AC power |
| Conveyor belt | Moves sample/stem |
| Touchscreen display | User interface |
| USB-A panel-mount extension | External USB drive access |
| Emergency stop button | Manual safety stop |
| Fuse and terminal blocks | Electrical protection and clean wiring |
| Enclosure | Holds electrical/control components |

## Recommended Control Architecture

```text
Proximity sensor → isolated input → Raspberry Pi GPIO

Raspberry Pi GPIO → relay module → contactor coil → conveyor power

Camera → Raspberry Pi → local image folder

Touchscreen → Raspberry Pi GUI

USB drive → panel-mount USB extension → Raspberry Pi
```

## Safety Notes

This system controls a 120 VAC conveyor. Follow proper electrical safety procedures.

Important safety points:

- Do not connect Raspberry Pi GPIO directly to 120 VAC.
- Use an opto-isolated relay module.
- Use a contactor or properly rated relay to switch conveyor power.
- Switch the hot/live wire, not the ground.
- Never cut or switch the earth ground wire.
- Use a fuse or breaker.
- Use a hardwired emergency stop.
- Keep AC wiring and low-voltage Raspberry Pi wiring separated.
- Place wiring inside an enclosure with strain relief/cable glands.
- Test GPIO and relay behavior before connecting conveyor power.

## Software Stack

| Function | Library/Tool |
|---|---|
| GUI | PySide6 |
| GPIO input/output | gpiozero |
| Raspberry Pi CSI camera | Picamera2 |
| USB camera | OpenCV |
| Image handling | pathlib, shutil |
| USB drive copy | Linux media mount folder |
| Auto-start | systemd service |

## Installation

Update Raspberry Pi OS:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

Install required packages:

```bash
sudo apt install -y \
  python3-pip \
  python3-venv \
  python3-gpiozero \
  python3-picamera2 \
  python3-opencv \
  python3-pil \
  python3-pil.imagetk \
  udisks2 \
  exfatprogs \
  ntfs-3g \
  git
```

Create project folder:

```bash
mkdir -p ~/stem_conveyor
cd ~/stem_conveyor
```

Create Python virtual environment:

```bash
python3 -m venv --system-site-packages venv
source venv/bin/activate
```

Install PySide6:

```bash
pip install --upgrade pip
pip install PySide6
```

## Running the GUI

Activate the virtual environment:

```bash
cd ~/stem_conveyor
source venv/bin/activate
```

Run the GUI:

```bash
python stem_gui.py
```

## GPIO Configuration

The current default GPIO pins are:

```python
PROXIMITY_PIN = 17
RELAY_PIN = 27
```

These use BCM numbering.

Update these values in `stem_gui.py` according to the final wiring.

## Relay Logic

Different relay modules behave differently.

In `stem_gui.py`, update:

```python
RELAY_ACTIVE_HIGH = True
```

Use `True` if the relay turns ON when GPIO is HIGH.

Use `False` if the relay turns ON when GPIO is LOW.

## Sensor Logic

Different proximity sensor circuits may output HIGH or LOW when an object is detected.

In `stem_gui.py`, update:

```python
SENSOR_ACTIVE_HIGH = True
```

Use `True` if the sensor output is HIGH when a stem is detected.

Use `False` if the sensor output is LOW when a stem is detected.

## Camera Configuration

The GUI supports either USB camera or Raspberry Pi CSI camera.

For USB camera:

```python
CAMERA_TYPE = "usb"
USB_CAMERA_INDEX = 0
```

For Raspberry Pi CSI camera:

```python
CAMERA_TYPE = "picamera2"
```

## Image Storage

Captured images are saved in:

```text
~/stem_conveyor/images
```

Image filenames use timestamps:

```text
stem_YYYYMMDD_HHMMSS.jpg
```

Example:

```text
stem_20260710_143205.jpg
```

## USB Copy

When a USB flash drive is inserted, the GUI can copy images into:

```text
USB_DRIVE/stem_images
```

The user can copy:

- Current selected image
- All saved images

Images are saved locally first before copying to USB. This prevents data loss if the USB drive is removed during capture.

## GUI Buttons

| Button | Function |
|---|---|
| START SYSTEM | Starts monitoring and runs conveyor |
| STOP SYSTEM | Stops monitoring and turns conveyor off |
| RESUME CONVEYOR | Restarts conveyor after image capture |
| MANUAL CAPTURE | Captures image manually |
| PREVIOUS | Shows previous image |
| NEXT | Shows next image |
| COPY CURRENT TO USB | Copies selected image to USB |
| COPY ALL TO USB | Copies all images to USB |
| DELETE IMAGE | Deletes selected image |
| EXIT | Closes the GUI and turns conveyor off |

## Testing Procedure

Before connecting conveyor power:

1. Run the GUI.
2. Confirm the touchscreen works.
3. Confirm the camera captures images.
4. Confirm proximity sensor status changes on the GUI.
5. Confirm relay LED changes when conveyor ON/OFF is commanded.
6. Confirm USB copy works.
7. Only after low-voltage testing, connect relay/contactor to conveyor control circuit.

## Suggested Final Deployment

For touchscreen deployment, change this line in `stem_gui.py`:

```python
gui.show()
```

to:

```python
gui.showFullScreen()
```

This will launch the application in full-screen mode.

## Future Improvements

Possible future updates:

- Add operator ID
- Add image classification
- Add automatic USB detection and eject button
- Add settings page for delay time and GPIO pins
- Add event log file
- Add system health monitoring
- Add automatic backup
- Add systemd auto-start service

EOF
