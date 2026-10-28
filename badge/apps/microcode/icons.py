"""Original code-drawn tile glyphs. Seven rows of seven bits per glyph."""

GLYPHS = {
    "start": (16, 24, 28, 30, 28, 24, 16),
    "timer": (28, 8, 62, 73, 89, 65, 62),
    "press": (8, 8, 42, 42, 62, 28, 28),
    "release": (8, 28, 42, 8, 62, 28, 28),
    "touch": (8, 42, 8, 28, 62, 62, 28),
    "untouch": (42, 8, 42, 28, 62, 62, 28),
    "move": (8, 28, 42, 127, 42, 28, 8),
    "sensor": (28, 34, 73, 85, 73, 34, 28),
    "variable": (65, 34, 20, 8, 20, 34, 65),
    "set": (8, 8, 42, 28, 8, 0, 127),
    "image": (127, 65, 85, 65, 99, 93, 127),
    "number": (20, 127, 20, 20, 127, 20, 0),
    "page": (62, 34, 47, 41, 47, 34, 62),
    "lights": (42, 28, 62, 62, 28, 28, 28),
    "save": (127, 73, 73, 127, 65, 93, 127),
    "load": (8, 8, 42, 28, 8, 65, 127),
    "new": (8, 8, 8, 127, 8, 8, 8),
    "samples": (127, 65, 127, 0, 127, 65, 127),
    "edit": (3, 7, 14, 28, 56, 112, 64),
    "delete": (28, 127, 34, 42, 42, 34, 62),
    "repeat": (62, 65, 66, 0, 33, 65, 62),
    "help": (62, 65, 2, 12, 8, 0, 8),
    "back": (16, 32, 127, 65, 65, 1, 62),
    "stop": (0, 62, 62, 62, 62, 62, 0),
    "warning": (8, 28, 28, 62, 54, 127, 8),
    "random": (127, 97, 81, 73, 69, 67, 127),
    "up": (8, 28, 42, 8, 8, 8, 8),
    "down": (8, 8, 8, 8, 42, 28, 8),
    "left": (8, 16, 32, 127, 32, 16, 8),
    "right": (8, 4, 2, 127, 2, 4, 8),
    "select": (0, 1, 2, 68, 40, 16, 0),
}


def draw(screen, glyph, x, y, scale=2):
    rows = GLYPHS.get(glyph, GLYPHS["sensor"])
    for row, bits in enumerate(rows):
        for column in range(7):
            if bits & (1 << (6 - column)):
                screen.rectangle(x + column * scale, y + row * scale, scale, scale)


def matrix(screen, mask, x, y, cell=8, gap=2, off_pen=None, on_pen=None):
    for index in range(25):
        on = bool(mask & (1 << (24 - index)))
        if on_pen is not None and on:
            screen.pen = on_pen
        elif off_pen is not None and not on:
            screen.pen = off_pen
        elif not on:
            continue
        screen.rectangle(x + (index % 5) * cell, y + (index // 5) * cell,
                         cell - gap, cell - gap)
