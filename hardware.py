from config import (
    PROXIMITY_PIN,
    FORWARD_RELAY_PIN,
    REVERSE_RELAY_PIN,
    RELAY_ACTIVE_HIGH,
    SENSOR_ACTIVE_HIGH,
    SENSOR_PULL_UP,
    SENSOR_BOUNCE_TIME_SEC,
    DEFAULT_DIRECTION,
    ALLOW_GPIO_SIMULATION,
)


class Hardware:
    """Raspberry Pi GPIO interface.

    The Raspberry Pi only provides low-voltage control signals. The motor
    must be switched by properly rated and interlocked motor-control hardware.

    The forward relay is intentionally active-low in the current system.
    Logical ``off()`` therefore drives the physical GPIO HIGH.
    """

    VALID_DIRECTIONS = ("forward", "reverse")

    def __init__(self):
        self.gpio_available = False
        self.forward_relay = None
        self.reverse_relay = None
        self.sensor = None
        self.direction = DEFAULT_DIRECTION
        self.running = False

        # GPIO is initialized before camera/storage/UI work so the conveyor
        # is commanded OFF as early as possible during application startup.
        self._initialize_gpio()

    def _initialize_gpio(self):
        try:
            from gpiozero import OutputDevice, DigitalInputDevice

            self.forward_relay = OutputDevice(
                FORWARD_RELAY_PIN,
                active_high=RELAY_ACTIVE_HIGH,
                initial_value=False,  # logical OFF; HIGH for active-low relay
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
            self._all_outputs_off()
            self.running = False
            print("GPIO initialized. Conveyor outputs forced OFF.")

        except Exception as error:
            self.gpio_available = False
            self.running = False

            if ALLOW_GPIO_SIMULATION:
                print("GPIO unavailable. Simulation mode is enabled.")
            else:
                print("GPIO unavailable. Conveyor start is LOCKED OUT.")

            print(error)

    @property
    def reverse_configured(self):
        return REVERSE_RELAY_PIN is not None

    def _all_outputs_off(self):
        if not self.gpio_available:
            return

        if self.forward_relay is not None:
            self.forward_relay.off()

        if self.reverse_relay is not None:
            self.reverse_relay.off()

    def conveyor_start(self, direction=None):
        """Start the conveyor in the requested direction.

        Returns False if GPIO control is unavailable or reverse is not set up.
        """
        if not self.gpio_available and not ALLOW_GPIO_SIMULATION:
            print("Conveyor start refused: GPIO control is unavailable.")
            self.running = False
            return False

        if direction is None:
            direction = self.direction

        direction = direction.lower()

        if direction not in self.VALID_DIRECTIONS:
            raise ValueError(
                f"Direction must be one of {self.VALID_DIRECTIONS}."
            )

        if direction == "reverse" and not self.reverse_configured:
            print(
                "Reverse requested, but REVERSE_RELAY_PIN is not configured."
            )
            self.running = False
            return False

        # Software interlock: de-energize all direction outputs before
        # energizing the requested direction.
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
        # For the active-low relay, off() means physical GPIO HIGH.
        self._all_outputs_off()
        self.running = False
        print("Conveyor STOPPED")

    def stem_detected(self):
        if not self.gpio_available or self.sensor is None:
            return False

        value = bool(self.sensor.value)
        return value if SENSOR_ACTIVE_HIGH else not value

    def cleanup(self):
        # Always command OFF before releasing GPIO handles.
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
