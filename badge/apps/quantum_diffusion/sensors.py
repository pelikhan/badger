"""Soft-timer sensor service; rendering only consumes the latest impulse."""

from machine import Timer
from time import ticks_diff, ticks_ms

from field import MotionImpulse


class Sensors:
    def __init__(self, device):
        self.device = device
        self.poll_imu = device._poll_imu
        self.motion = MotionImpulse()
        self.timer = Timer(-1)
        self.pending = (0, 0, 0, 0, "SHAKE")
        self.consumed = 0
        self.samples = 0
        self.light = None
        self.gain = 4
        self.error = None
        self.running = False
        self.last_tick = ticks_ms()
        self.light_elapsed = 100

    def start(self):
        if self.running:
            return
        self.last_tick = ticks_ms()
        self.running = True
        # The timer owns IMU polling while active. The runtime can still poll
        # buttons/orientation without racing us for read-to-clear IMU events.
        self.device._poll_imu = self.idle
        try:
            self.timer.init(period=10, mode=Timer.PERIODIC, callback=self.poll, hard=False)
        except (OSError, RuntimeError, ValueError):
            self.stop()
            raise

    def idle(self):
        pass

    def stop(self):
        if self.running:
            self.device._poll_imu = self.poll_imu
        self.running = False
        self.timer.deinit()

    def poll(self, timer):
        if not self.running:
            return
        try:
            now = ticks_ms()
            elapsed = max(0, ticks_diff(now, self.last_tick))
            self.last_tick = now
            # Refresh only the IMU, not badge.poll(): consuming logical button
            # edges here would lose presses before the next rendered frame.
            self.poll_imu()
            ax, ay, az, gx, gy, gz = self.device.imu()
            strength = self.motion.sample(
                ax, ay, az, elapsed, self.device.imu_event(), (gx, gy, gz)
            )
            self.samples += 1
            if strength:
                self.pending = (
                    self.pending[0] + 1, strength,
                    self.motion.dx, self.motion.dy, self.motion.source,
                )
            self.light_elapsed += elapsed
            if self.light_elapsed >= 100:
                self.light_elapsed = 0
                level = max(0, min(65535, self.device.light_level()))
                self.light = level if self.light is None else (7 * self.light + level) // 8
                self.gain = 4 + min(3, self.light // 1024)
        except (OSError, RuntimeError) as error:
            self.error = error
            self.stop()
            print("Quantum Diffusion sensor error:", error)

    def take(self):
        if self.error is not None:
            raise RuntimeError("Quantum Diffusion sensor service failed: %s" % self.error)
        pending = self.pending
        if pending[0] == self.consumed:
            return None
        self.consumed = pending[0]
        return pending[1:]
