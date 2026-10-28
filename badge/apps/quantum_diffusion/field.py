"""Allocation-bounded fixed-point waves and raw-count 2026 motion input."""

from array import array
import sys

from kernel import step


SCALE = 1024
LIMIT = 12 * SCALE
PULSE = 11 * SCALE // 2
STEP_MS = 25
BACKEND = "ARM Viper" if sys.implementation.name == "micropython" else "Python reference"


class WaveField:
    def __init__(self, width=64, height=48):
        if width < 8 or height < 8:
            raise ValueError("Wave field dimensions must be at least 8")
        self.width = width
        self.height = height
        size = width * height
        self.current = array("i", [0] * size)
        self.previous = array("i", [0] * size)
        self.next = array("i", [0] * size)

    def clear(self):
        for i in range(len(self.current)):
            self.current[i] = 0
            self.previous[i] = 0
            self.next[i] = 0

    def impulse(self, x, y, strength=PULSE):
        x = max(3, min(self.width - 4, int(x)))
        y = max(3, min(self.height - 4, int(y)))
        strength = max(-8 * SCALE, min(8 * SCALE, int(strength)))
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                distance = dx * dx + dy * dy
                if distance > 5:
                    continue
                i = (y + dy) * self.width + x + dx
                amplitude = abs(strength) * (6 - distance) // 6
                if strength < 0:
                    amplitude = -amplitude
                self.current[i] = max(-LIMIT, min(LIMIT, self.current[i] + amplitude))

    def step(self):
        step(self.current, self.previous, self.next, self.width, self.height)
        self.previous, self.current, self.next = self.current, self.next, self.previous


class MotionImpulse:
    """16384 raw counts/g; Q10 high-pass and jerk reject gravity and slow tilt."""

    def __init__(self):
        self.gravity = None
        self.last = None
        self.warmup = 0
        self.cooldown = 0
        self.armed = False
        self.dx = 0
        self.dy = 0
        self.last_gyro = (0, 0, 0)
        self.source = "SHAKE"

    def sample(self, ax, ay, az, elapsed_ms, events=(), gyro=(0, 0, 0)):
        dt = max(0, min(100, elapsed_ms))
        sample = (ax >> 4, ay >> 4, az >> 4)
        if self.gravity is None:
            # Firmware returns zeros until its first sensor poll.
            if sample != (0, 0, 0):
                self.gravity = list(sample)
                self.last = sample
                self.last_gyro = gyro
            return 0
        self.warmup += dt
        self.cooldown = max(0, self.cooldown - dt)
        self.armed = self.warmup >= 200 and not self.cooldown
        gravity = self.gravity
        dx = sample[0] - gravity[0]
        dy = sample[1] - gravity[1]
        dz = sample[2] - gravity[2]
        self.dx, self.dy = dx, dy
        jerk = max(abs(sample[i] - self.last[i]) for i in range(3))
        self.last = sample
        for axis in range(3):
            gravity[axis] += (sample[axis] - gravity[axis]) * dt // (350 + dt)
        # imu_event() is a tuple of integer IDs, not strings or a bit mask.
        gesture = 1 in events or 2 in events or 16 in events
        moving = dx * dx + dy * dy + dz * dz >= 358 * 358 and jerk >= 184
        rotation = max(abs(value) for value in gyro)
        angular_jerk = max(abs(gyro[i] - self.last_gyro[i]) for i in range(3))
        self.last_gyro = gyro
        # Stock CTRL2_G=0x40: +/-250 deg/s, 0.00875 deg/s per count.
        twisting = rotation >= 7000 and angular_jerk >= 2200
        if self.armed and (gesture or moving or twisting):
            self.armed = False
            self.cooldown = 300
            self.source = "TWIST" if twisting and not (moving or gesture) else "SHAKE"
            if self.source == "TWIST":
                self.dx = max(-SCALE, min(SCALE, gyro[1] // 8))
                self.dy = max(-SCALE, min(SCALE, gyro[0] // 8))
                return min(8 * SCALE, 3584 + rotation // 4)
            return min(8 * SCALE, 3584 + 2 * max(abs(dx), abs(dy), abs(dz)))
        return 0
