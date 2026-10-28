"""Quantum-themed classical waves for the Universe 2026 badge."""

import os
import sys

APP_DIR = "/system/apps/quantum_diffusion"
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

from field import PULSE, SCALE, STEP_MS, WaveField
from kernel import render
from palette import BrandPalette
from sensors import Sensors

badge.mode(HIRES | VSYNC)
field = WaveField()
motion = Sensors(badge)
CELL = min(screen.width // field.width, screen.height // field.height)
LEFT = (screen.width - field.width * CELL) // 2
TOP = (screen.height - field.height * CELL) // 2


brand_palette = BrandPalette()
PALETTE = brand_palette.colors
if screen.stride != screen.width * 2 or len(screen.raw) != screen.stride * screen.height:
    raise RuntimeError("Quantum Diffusion requires the 2026 RGB565 framebuffer")
accumulator = 0
pulses = 0
source = "BOOT"
touch_mask = 0
TOUCH_POINTS = (
    (BUTTON_UP, 1, 2, 1),
    (BUTTON_DOWN, 2, 2, 3),
    (BUTTON_LEFT, 4, 1, 2),
    (BUTTON_RIGHT, 8, 3, 2),
    (BUTTON_SELECT, 16, 2, 2),
    (BUTTON_MENU, 32, 3, 1),
    (BUTTON_BACK, 64, 3, 3),
)


def pulse(x, y, strength=PULSE, label="PULSE"):
    global pulses, source
    field.impulse(x, y, strength)
    pulses = (pulses + 1) % 1000
    source = label


def draw():
    render(
        field.current, PALETTE, screen.raw, field.width, field.height,
        CELL, LEFT, TOP, screen.stride // 2, motion.gain,
    )


def update():
    global accumulator, touch_mask
    brand_palette.update(max(0, badge.ticks_delta))
    elapsed = max(0, min(100, badge.ticks_delta))
    impulse = motion.take()

    if impulse is not None:
        strength, dx, dy, label = impulse
        direction = -1 if badge.upside_down() else 1
        x = field.width // 2 + dx * 6 * direction // SCALE
        y = field.height // 2 - dy * 6 * direction // SCALE
        pulse(x, y, strength, label)

    touching = 0
    for action, bit, x, y in TOUCH_POINTS:
        # touched() reads the runtime's cached, orientation-aware pad state.
        if badge.touched(action):
            touching |= bit
            if not touch_mask & bit:
                pulse(field.width * x // 4, field.height * y // 4, label="TOUCH")
    touch_mask = touching

    # Drop excess wall time after a stall instead of doing unbounded catch-up.
    accumulator = min(100, accumulator + elapsed)
    while accumulator >= STEP_MS:
        field.step()
        accumulator -= STEP_MS
    draw()


def on_exit():
    motion.stop()


pulse(field.width // 2, field.height // 2, label="BOOT")
motion.start()
run(update)
on_exit()
