from config import (
    PROXIMITY_PIN,
    FORWARD_RELAY_PIN,
    REVERSE_RELAY_PIN,
    RELAY_ACTIVE_HIGH,
    SENSOR_ACTIVE_HIGH,
    SENSOR_PULL_UP,
    SENSOR_BOUNCE_TIME_SEC,
    DEFAULT_DIRECTION,
)


class Hardware:
    """
    Raspberry Pi GPIO interface.

    This class only handles low-voltage GPIO control signals.
    The Raspberry Pi must NOT drive the conveyor motor directly.

    Forward/reverse outputs are intended to control properly
    rated and interlocked motor-control hardware.
    """

    VALID_DIRECTIONS = ("forward", "reverse")

    def __init__(self):
        self.gpio_available = False

        self.forward_relay = None
        self.reverse_relay = None
        self.sensor = None

        self.direction = DEFAULT_DIRECTION
        self.running = False

        self._initialize_gpio()

    # ========================================================
    # INITIALIZATION
    # ========================================================

    def _initialize_gpio(self):
        try:
            from gpiozero import OutputDevice, DigitalInputDevice

            self.forward_relay = OutputDevice(
                FORWARD_RELAY_PIN,
                active_high=RELAY_ACTIVE_HIGH,
                initial_value=False,
            )

            if REVERSE_RELAY_PIN is not None:
                self.reverse_relay = OutputDevice(
                    REVERSE_RELAY_PIN,
                    active_high=RELAY_ACTIVE_HIGH,
                    initial_value=False,
                )

            self.sensor = DigitalInputDevice(
                PROXIMITY_PIN,
                pull_up=SENSOR_PULL_UP,
                bounce_time=SENSOR_BOUNCE_TIME_SEC,
            )

            self.gpio_available = True
            print("GPIO initialized successfully.")

        except Exception as error:
            self.gpio_available = False
            print("GPIO unavailable. Running in simulation mode.")
            print(error)

    # ========================================================
    # CAPABILITIES
    # ========================================================

    @property
    def reverse_configured(self):
        return REVERSE_RELAY_PIN is not None

    # ========================================================
    # CONVEYOR CONTROL
    # ========================================================

    def _all_outputs_off(self):
        if self.gpio_available:
            if self.forward_relay is not None:
                self.forward_relay.off()

            if self.reverse_relay is not None:
                self.reverse_relay.off()

    def conveyor_start(self, direction=None):
        """
        Start the conveyor in the requested direction.

        Returns True if the command is accepted.
        Returns False if reverse is requested but not configured.
        """
        if direction is None:
            direction = self.direction

        direction = direction.lower()

        if direction not in self.VALID_DIRECTIONS:
            raise ValueError(
                f"Direction must be one of {self.VALID_DIRECTIONS}."
            )

        if direction == "reverse" and not self.reverse_configured:
            print("Reverse direction requested, but REVERSE_RELAY_PIN is not configured.")
            return False

        # Software interlock: never intentionally leave both
        # direction outputs active at the same time.
        self._all_outputs_off()

        if self.gpio_available:
            if direction == "forward":
                self.forward_relay.on()
            else:
                self.reverse_relay.on()

        self.direction = direction
        self.running = True

        print(f"Conveyor started: {direction.upper()}")
        return True

    def conveyor_stop(self):
        self._all_outputs_off()
        self.running = False
        print("Conveyor STOPPED")

    # ========================================================
    # SENSOR
    # ========================================================

    def stem_detected(self):
        if not self.gpio_available or self.sensor is None:
            return False

        value = bool(self.sensor.value)

        if SENSOR_ACTIVE_HIGH:
            return value

        return not value

    # ========================================================
    # CLEANUP
    # ========================================================

    def cleanup(self):
        self.conveyor_stop()

        for device in (
            self.forward_relay,
            self.reverse_relay,
            self.sensor,
        ):
            if device is not None:
                try:
                    device.close()
                except Exception:
                    pass

        print("GPIO cleanup complete.")
