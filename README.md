# Stem Conveyor Imaging System

A Raspberry Pi-based embedded control application for automatically capturing images of plant stems moving on a conveyor belt.

The system uses a proximity sensor to detect an incoming stem, waits for the stem to travel from the sensor to the camera, and automatically captures an image while the conveyor continues moving.

The application provides a touchscreen-friendly graphical interface designed for student use.

## Features

- Raspberry Pi conveyor control
- PySide6 touchscreen GUI
- Proximity sensor stem detection
- Automatic delayed image capture
- Continuous conveyor movement during capture
- Manual image capture
- Forward and reverse conveyor control support
- Safe direction-change delay
- Image preview and browsing
- Image deletion
- USB drive detection
- Copy individual or all images to USB
- USB webcam support
- Raspberry Pi CSI camera support
- Modular Python code structure

## How It Works

```text
START BELT
     ↓
Conveyor runs
     ↓
Proximity sensor detects a stem
     ↓
Conveyor continues moving
     ↓
Wait for sensor-to-camera travel time
     ↓
Camera captures one image
     ↓
Conveyor continues running
     ↓
Next stem
```

The software detects only the transition from sensor clear to stem detected. This prevents a single stem from producing multiple image triggers while it remains in front of the sensor.

## Project Structure

```text
Conv_controller/
│
├── main.py
├── config.py
├── hardware.py
├── camera.py
├── storage.py
├── gui.py
├── README.md
└── stem_gui.py
```

### `main.py`

Starts the PySide6 application and opens the graphical interface.

### `config.py`

Contains adjustable settings including:

- GPIO pins
- relay polarity
- sensor polarity
- sensor-to-camera delay
- direction-change delay
- camera type
- camera index
- image storage location
- automatic capture settings

### `hardware.py`

Handles Raspberry Pi GPIO operations including:

- proximity sensor input
- forward conveyor output
- reverse conveyor output
- conveyor start
- conveyor stop
- direction control
- GPIO cleanup

### `camera.py`

Handles image capture using:

- USB cameras through OpenCV
- Raspberry Pi CSI cameras through Picamera2

### `storage.py`

Handles:

- local image storage
- image deletion
- USB detection
- copying one image to USB
- copying all images to USB

### `gui.py`

Contains the touchscreen user interface and coordinates the hardware, camera, and storage modules.

### `stem_gui.py`

Original single-file version of the application.

Keep this file temporarily as a backup until the modular version is fully tested on the Raspberry Pi.

## GPIO Configuration

The application uses BCM GPIO numbering.

Current configuration:

```python
PROXIMITY_PIN = 17
FORWARD_RELAY_PIN = 27
REVERSE_RELAY_PIN = None
```

The reverse GPIO is intentionally left undefined until the actual reverse-control wiring is confirmed.

After the hardware is connected, update:

```python
REVERSE_RELAY_PIN = YOUR_GPIO_PIN
```

Do not assign a GPIO pin based only on an example.

## Sensor-to-Camera Delay

The proximity sensor is positioned before the camera.

When the sensor detects a stem, the conveyor continues moving while the software waits for the stem to reach the camera.

The delay is configured in `config.py`:

```python
SENSOR_TO_CAMERA_DELAY_SEC = 2.0
```

This value should be calibrated experimentally.

- If the picture is taken too early, increase the delay.
- If the picture is taken too late, decrease the delay.

The approximate relationship is:

```text
Delay = Sensor-to-camera distance / Conveyor speed
```

## Conveyor Direction

The GUI contains a direction-change button.

When direction is changed while the conveyor is running:

```text
Stop conveyor
     ↓
Wait for motor dead time
     ↓
Change direction
     ↓
Restart conveyor
```

The delay is configured with:

```python
DIRECTION_CHANGE_DEAD_TIME_MS = 800
```

Pending automatic image captures are canceled when direction changes because the original sensor-to-camera timing is no longer valid.

By default:

```python
AUTO_CAPTURE_FORWARD = True
AUTO_CAPTURE_REVERSE = False
```

Automatic capture in reverse should only be enabled if the sensor and camera arrangement supports it.

## Motor-Control Safety

The Raspberry Pi must not directly power the conveyor motor.

GPIO outputs should only control appropriately designed motor-control hardware such as relays, contactors, or motor controllers.

Forward and reverse contactors must not be energized at the same time.

The software delay between direction changes is an additional safeguard and does not replace proper electrical or mechanical interlocking.

## Requirements

Recommended software:

- Raspberry Pi OS
- Python 3
- PySide6
- gpiozero
- OpenCV

Install common Python dependencies with:

```bash
pip install PySide6 gpiozero opencv-python
```

For Raspberry Pi CSI cameras, install Picamera2 using the appropriate Raspberry Pi OS package.

## Clone the Repository

```bash
git clone https://github.com/mshrestha789/Conv_controller.git
```

Then:

```bash
cd Conv_controller
```

## Run the Application

```bash
python3 main.py
```

## Fullscreen Mode

For development:

```python
FULLSCREEN = False
```

For final Raspberry Pi touchscreen operation:

```python
FULLSCREEN = True
```

## Student Operation

1. Make sure the conveyor area is clear.
2. Start the application.
3. Press **START BELT**.
4. Place stems on the conveyor.
5. The proximity sensor detects each stem automatically.
6. The program waits for the configured travel time.
7. The camera automatically captures an image.
8. The image appears in the saved-photo list.
9. Press **STOP BELT** when finished.
10. Insert a USB drive if images need to be exported.
11. Use **Save This to USB** or **Save All to USB**.

The **TAKE PHOTO** button can also be used for manual image capture.

## Image Storage

Images are stored by default in:

```text
~/stem_conveyor/images/
```

Automatic image filenames look like:

```text
stem_auto_20260812_142530_421.jpg
```

Manual image filenames look like:

```text
stem_manual_20260812_142545_118.jpg
```

## Testing Before Student Use

Before operating the system with students, verify:

- START BELT correctly starts the conveyor.
- STOP BELT correctly stops the conveyor.
- One stem produces only one trigger.
- Sensor-to-camera timing positions the stem correctly.
- Multiple stems produce separate images.
- Manual capture works.
- USB copying works.
- STOP cancels pending automatic captures.
- The conveyor stops before changing direction.
- Forward and reverse outputs cannot activate simultaneously.
- Physical motor-safety controls operate independently of the Raspberry Pi.

## Software Architecture

```text
main.py
   │
   ▼
gui.py
   │
   ├── hardware.py
   ├── camera.py
   └── storage.py

config.py
   └── shared configuration
```

This modular structure makes the controller easier to understand, test, maintain, and extend.

## Repository

https://github.com/mshrestha789/Conv_controller
