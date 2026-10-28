"""The Factory Mindset: an offline, animated Universe 2026 badge story."""

import os
import sys


APP_DIR = "/system/apps/factory_mindset"
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

from factory_player import DURATIONS, REVEAL_MS, Player
from factory_slides import BACKGROUND, BLUE, FONT_PATH, GREEN, PURPLE, SLIDES

badge.mode(HIRES | VSYNC)
screen.font = font.load(FONT_PATH)
BACKGROUND_PEN = color.rgb(*BACKGROUND)
player = Player()
scale_x = screen.width / 320
scale_y = screen.height / 240
text_scale = min(scale_x, scale_y)


def fit_line(line):
    text, x, y, size, rgb, delay = line
    width, _ = screen.measure_text(text, font_size=size * text_scale)
    available = (302 - x) * scale_x
    fitted = min(size * text_scale, size * text_scale * available / max(1, width))
    if fitted < 12 * text_scale:
        raise ValueError("Slide text would be too small: " + text)
    return text, x, y, fitted, rgb, delay


LINES = tuple(tuple(fit_line(line) for line in slide["lines"]) for slide in SLIDES)


def reveal(elapsed, delay):
    return min(1, max(0, (elapsed - delay) / REVEAL_MS))


def pen(rgb, amount=1):
    screen.pen = color.rgb(
        int(BACKGROUND[0] + (rgb[0] - BACKGROUND[0]) * amount),
        int(BACKGROUND[1] + (rgb[1] - BACKGROUND[1]) * amount),
        int(BACKGROUND[2] + (rgb[2] - BACKGROUND[2]) * amount))


def outline(x, y, width, height, rgb, amount, offset, radius=12):
    pen(rgb, amount)
    screen.shape(shape.rounded_rectangle(
        x * scale_x + offset, y * scale_y, width * scale_x,
        height * scale_y, radius * text_scale).stroke(2 * text_scale))


def line(x1, y1, x2, y2, offset):
    screen.line(x1 * scale_x + offset, y1 * scale_y,
                x2 * scale_x + offset, y2 * scale_y)


def ring(x, y, offset):
    screen.shape(shape.circle(x * scale_x + offset, y * scale_y,
                              4 * text_scale).stroke(2 * text_scale))


def draw_symbol(symbol, elapsed, offset):
    amount = reveal(elapsed, 100)
    if amount <= 0:
        return
    if symbol == "phone":
        outline(40, 50, 29, 49, GREEN, amount, offset, 4)
        line(51, 57, 58, 57, offset)
        ring(54, 91, offset)
    elif symbol == "agent":
        outline(39, 61, 43, 33, PURPLE, amount, offset, 5)
        line(60, 51, 60, 61, offset)
        line(53, 51, 60, 51, offset)
        line(51, 71, 51, 82, offset)
        line(69, 71, 69, 82, offset)
    elif symbol == "pull":
        pen(BLUE, amount)
        line(48, 57, 48, 95, offset)
        line(78, 95, 78, 67, offset)
        line(78, 67, 64, 57, offset)
        line(64, 57, 72, 57, offset)
        line(64, 57, 66, 65, offset)
        for x, y in ((48, 53), (48, 99), (78, 99)):
            ring(x, y, offset)
    elif symbol == "workflow":
        for y, delay, rgb in ((79, 900, PURPLE), (152, 1700, GREEN)):
            pen(rgb, reveal(elapsed, delay))
            line(160, y, 160, y + 7, offset)
            line(156, y + 3, 160, y + 7, offset)
            line(164, y + 3, 160, y + 7, offset)


def draw_slide(index, elapsed, offset):
    slide = SLIDES[index]
    for x, y, width, height, rgb, delay in slide.get("boxes", ()):
        amount = reveal(elapsed, delay)
        if amount > 0:
            outline(x, y, width, height, rgb, amount, offset)
    if "symbol" in slide:
        draw_symbol(slide["symbol"], elapsed, offset)
    for text, x, y, size, rgb, delay in LINES[index]:
        amount = reveal(elapsed, delay)
        if amount > 0:
            pen(rgb, amount)
            rise = 10 * (1 - amount) ** 3
            screen.text(text, x * scale_x + offset, (y + rise) * scale_y,
                        font_size=size)


def draw():
    screen.pen = BACKGROUND_PEN
    screen.rectangle(screen.clip)
    offset = player.offset(screen.width)
    if offset:
        draw_slide(player.previous, DURATIONS[player.previous],
                   offset - screen.width * player.direction)
    elapsed = DURATIONS[player.scene] if player.reveal_all else player.elapsed
    draw_slide(player.scene, elapsed, offset)


def update():
    player.advance(badge.ticks_delta,
                   badge.pressed(BUTTON_LEFT),
                   badge.pressed(BUTTON_RIGHT),
                   badge.pressed(BUTTON_SELECT))
    draw()


# HOME is intentionally untouched: the launcher owns it.
run(update)
