"""A standalone BLE HID slideshow keyboard."""

import os
import sys

APP_DIR = "/system/apps/ble_slideshow_clicker"
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

from clicker import BondStore, Clicker

badge.mode(HIRES | VSYNC)
screen.font = font.sins
clicker = None
error_text = None
battery_ms = 0


def fail(error):
    global error_text
    print("BLE slideshow clicker:", type(error).__name__, str(error))
    error_text = str(error)
    if clicker is not None:
        clicker.error = error_text
        try:
            clicker.close()
        except (OSError, RuntimeError, ValueError) as cleanup_error:
            print("BLE cleanup failed:", cleanup_error)


try:
    import bluetooth
    clicker = Clicker(BondStore())
    clicker.start(bluetooth, badge.battery_level())
except (ImportError, OSError, RuntimeError, ValueError, AttributeError) as error:
    fail(error)


def text(value, y, rgb=(230, 238, 250)):
    screen.pen = color.rgb(*rgb)
    screen.text(value, 10, y)


def update():
    global battery_ms
    if error_text is None:
        try:
            battery_ms += max(0, badge.ticks_delta)
            if battery_ms >= 60000:
                clicker.set_battery(badge.battery_level())
                battery_ms = 0
            clicker.tick(badge.ticks_delta)
            if badge.pressed(BUTTON_RIGHT) or badge.pressed(BUTTON_SELECT):
                clicker.tap("next")
            elif badge.pressed(BUTTON_LEFT):
                clicker.tap("previous")
            elif badge.pressed(BUTTON_BACK):
                clicker.tap("escape")
            elif badge.pressed(BUTTON_DOWN):
                clicker.tap("blank")
        except (OSError, RuntimeError, ValueError, AttributeError) as error:
            fail(error)
    screen.pen = color.rgb(13, 20, 36)
    screen.rectangle(screen.clip)
    text("BLE SLIDESHOW", 12, (83, 200, 255))
    if error_text is not None:
        text("BLE unavailable / stopped", 40, (255, 170, 110))
        # Wrap by measured width, including firmware messages without spaces.
        y, line = 68, ""
        for character in error_text[:200]:
            if screen.measure_text(line + character).x > screen.width - 20:
                text(line, y)
                y += 14
                line = ""
                if y > screen.height - 65:
                    break
            line += character
        text(line, y)
        text("See app README for firmware needs.", screen.height - 44)
        text("HOME: exit", screen.height - 24)
        return
    text("Suspended" if clicker.suspended else clicker.status, 40)
    text(clicker.warning, 64, (255, 190, 100))
    text("Bluetooth name: Badger Clicker", 84)
    text("RIGHT / SELECT   Next slide", 108)
    text("LEFT             Previous", 128)
    text("BACK             Escape", 148)
    text("DOWN             B / blank", 168)
    text(clicker.last_action, 192, (150, 195, 220))
    text("HOME: exit and disconnect", screen.height - 24)


def on_exit():
    if clicker is not None:
        clicker.close()


run(update)
on_exit()
