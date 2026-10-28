"""Universe 2026 adapters. No internal I2C probing or assumed physical units."""

import math

from model import BUTTONS

# name, label, unit, editor increment, default threshold
SENSORS = (
    ("light_level", "Light level", "1..5 relative", 1, 3),
    ("light", "Light ADC", "raw 0..65535", 500, 8192),
    ("battery", "Battery", "% estimate", 5, 25),
    ("charging", "Charging", "0=no 1=yes", 1, 1),
    ("inverted", "Inverted", "0=no 1=yes", 1, 1),
    ("tilt_x", "Tilt X", "% of vector", 10, 50),
    ("tilt_y", "Tilt Y", "% of vector", 10, 50),
    ("ax", "Accel X", "raw", 1000, 8000),
    ("ay", "Accel Y", "raw", 1000, 8000),
    ("az", "Accel Z", "raw", 1000, 8000),
    ("gx", "Gyro X", "raw", 1000, 8000),
    ("gy", "Gyro Y", "raw", 1000, 8000),
    ("gz", "Gyro Z", "raw", 1000, 8000),
)
LIGHT_EDGES = (512, 2048, 8192, 32768)
UNSUPPORTED = (
    ("Microphone / melodies", "No onboard mic or speaker"),
    ("Temperature / compass", "No verified firmware API"),
    ("micro:bit radio / robot", "Different radio protocol"),
    ("Jacdac / touch pins", "No Jacdac or micro:bit pins"),
    ("Qw/ST extra sensors", "No installed external sensor"),
    ("IR / Wi-Fi / Bluetooth", "Not ambient sensors / not mapped"),
)


def spec(name):
    for item in SENSORS:
        if item[0] == name:
            return item
    raise ValueError("Unknown sensor")


class Sensors:
    def __init__(self, badge, buttons, adc=None, logger=print):
        self.badge = badge
        self.buttons = buttons
        self.log = logger
        self.values = {}
        self.capabilities = {}
        self.errors = {}
        self.time = 0
        self.sample_elapsed = 50
        self.last_touch = {}
        self.tilts = set()
        self.previous_accel = None
        self.gravity = None
        self.shake_hit = None
        self.shake_after = 0
        self.light_band = None
        self.adc = adc
        for feature, method in (("imu", "imu"), ("touch", "touched"),
                                ("battery", "battery_level"), ("charging", "is_charging"),
                                ("inverted", "upside_down"), ("lights", "caselights")):
            self.capabilities[feature] = callable(getattr(badge, method, None))
            if not self.capabilities[feature]:
                self.errors[feature] = "Firmware API missing"
        if adc is None:
            try:
                from machine import ADC, Pin
                # Verified U26 LIGHT_SENSE pin; never touch GPIO41/power or I2C.
                pin = getattr(Pin.board, "LIGHT_SENSE", None) if hasattr(Pin, "board") else None
                self.adc = ADC(pin if pin is not None else Pin(43))
            except ImportError:
                self.errors["light"] = "machine.ADC unavailable"
            except (OSError, ValueError, TypeError) as error:
                self.errors["light"] = str(error)
        self.capabilities["light"] = self.adc is not None
        for feature, reason in self.errors.items():
            self.log("MicroCode unavailable %s: %s" % (feature, reason))

    def available(self, feature):
        if feature in ("light", "light_level"):
            return self.capabilities.get("light", False)
        if feature in ("ax", "ay", "az", "gx", "gy", "gz", "tilt_x", "tilt_y"):
            return self.capabilities.get("imu", False)
        return self.capabilities.get(feature, False)

    def disable(self, feature, error):
        self.capabilities[feature] = False
        self.errors[feature] = str(error)
        for key in tuple(self.values):
            if key == feature or (feature == "light" and key == "light_level") or (
                    feature == "imu" and key in ("ax", "ay", "az", "gx", "gy", "gz", "tilt_x", "tilt_y")):
                del self.values[key]
        self.log("MicroCode sensor failed %s: %s" % (feature, error))

    def poll(self, dt):
        self.time += max(0, int(dt))
        events = []
        for name in BUTTONS:
            button = self.buttons[name]
            if self.badge.pressed(button):
                events.append(("press", name, None))
            if self.badge.released(button):
                events.append(("release", name, None))
            if self.available("touch"):
                try:
                    touched = bool(self.badge.touched(button))
                    before = self.last_touch.get(name, False)
                    if touched != before:
                        events.append(("touch" if touched else "untouch", name, None))
                    self.last_touch[name] = touched
                except (OSError, ValueError, TypeError) as error:
                    self.disable("touch", error)
        self.sample_elapsed += max(0, int(dt))
        if self.sample_elapsed < 50:
            return events
        self.sample_elapsed = 0
        if self.available("imu"):
            try:
                reading = self.badge.imu()
                if len(reading) != 6 or not all(isinstance(value, (int, float)) and math.isfinite(value)
                                               for value in reading):
                    raise ValueError("IMU requires six finite raw values")
                for key, value in zip(("ax", "ay", "az", "gx", "gy", "gz"), reading):
                    self.values[key] = int(value)
                self.motion(reading[:3], events)
            except (OSError, ValueError, TypeError) as error:
                self.disable("imu", error)
        if self.available("light"):
            try:
                value = self.adc.read_u16()
                if type(value) is not int or not 0 <= value <= 65535:
                    raise ValueError("Invalid ADC reading")
                self.values["light"] = value
                band = sum(value >= edge for edge in LIGHT_EDGES)
                # 64 counts of hysteresis avoids chatter at band boundaries.
                if self.light_band is None:
                    self.light_band = band
                elif band > self.light_band:
                    if value >= LIGHT_EDGES[self.light_band] + 64:
                        self.light_band = band
                elif band < self.light_band:
                    if value < LIGHT_EDGES[self.light_band - 1] - 64:
                        self.light_band = band
                self.values["light_level"] = self.light_band + 1
            except (OSError, ValueError, TypeError) as error:
                self.disable("light", error)
        for feature, method in (("battery", "battery_level"), ("charging", "is_charging"),
                                ("inverted", "upside_down")):
            if not self.available(feature):
                continue
            try:
                raw = getattr(self.badge, method)()
                if feature == "battery":
                    if not isinstance(raw, (int, float)) or not math.isfinite(raw) or not 0 <= raw <= 100:
                        raise ValueError("Battery must be 0..100 percent")
                    self.values[feature] = int(raw)
                else:
                    if raw not in (True, False, 0, 1):
                        raise ValueError("Expected boolean state")
                    self.values[feature] = int(raw)
            except (OSError, ValueError, TypeError) as error:
                self.disable(feature, error)
        for name, value in self.values.items():
            events.append(("sensor", name, value))
        return events

    def motion(self, raw, events):
        magnitude = math.sqrt(sum(value * value for value in raw))
        if magnitude == 0:
            self.values["tilt_x"] = 0
            self.values["tilt_y"] = 0
            self.previous_accel = tuple(raw)
            return
        x, y, z = (value / magnitude for value in raw)
        if self.values.get("inverted", 0):
            x, y = -x, -y
        self.values["tilt_x"], self.values["tilt_y"] = round(x * 100), round(y * 100)
        active = set()
        for name, component in (("left", -x), ("right", x), ("up", -y), ("down", y),
                                ("face_up", z), ("face_down", -z)):
            if component >= (0.40 if name in self.tilts else 0.60):
                active.add(name)
                if name not in self.tilts:
                    events.append(("move", name, None))
        self.tilts = active
        if self.gravity is None:
            self.gravity = magnitude
        if self.previous_accel is not None and self.time >= self.shake_after:
            delta = math.sqrt(sum((a - b) ** 2 for a, b in zip(raw, self.previous_accel)))
            if delta > self.gravity * 0.75:
                if self.shake_hit is not None and self.time - self.shake_hit <= 300:
                    events.append(("move", "shake", None))
                    self.shake_after = self.time + 600
                    self.shake_hit = None
                else:
                    self.shake_hit = self.time
        self.previous_accel = tuple(raw)
        # A slowly moving magnitude reference avoids claiming an unknown G scale.
        self.gravity += (magnitude - self.gravity) * 0.01
