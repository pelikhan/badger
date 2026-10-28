"""MicroCode-style programming for the Universe 2026 badge."""

import os
import sys

APP_DIR = "/system/apps/microcode"
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

from editor import App
from sensors import Sensors
from storage import Store
from view import View

badge.mode(HIRES | VSYNC)
buttons = {
    "up": BUTTON_UP, "down": BUTTON_DOWN, "left": BUTTON_LEFT, "right": BUTTON_RIGHT,
    "select": BUTTON_SELECT, "back": BUTTON_BACK, "menu": BUTTON_MENU,
}
sensors = Sensors(badge, buttons)
sensors.poll(0)
app = App(sensors, Store())
view = View(screen, color, font.sins)


def update():
    dt = badge.ticks_delta
    events = sensors.poll(dt)
    actions = [name for name, button in buttons.items() if badge.pressed(button)]
    app.update(dt, actions, events)
    view.render(app)


run(update)
app.close()
