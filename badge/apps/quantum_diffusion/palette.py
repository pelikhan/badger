"""Integer color cycling from GitHub's official primary and secondary palettes."""

from array import array


# https://brand.github.com/foundations/color
BRAND_COLORS = (
    (15, 191, 62),    # GitHub Green #0FBF3E
    (48, 148, 255),   # Security Blue #3094FF
    (133, 52, 243),   # Copilot Purple #8534F3
    (254, 76, 37),    # Orange 3 #FE4C25
    (211, 250, 55),   # Lime 2 #D3FA37
)
HIGHLIGHT = (242, 245, 243)  # GitHub Gray 1 #F2F5F3
TRANSITION_MS = 4000
UPDATE_MS = 50
CYCLE_MS = len(BRAND_COLORS) * TRANSITION_MS


def rgb565(red, green, blue):
    return ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)


class BrandPalette:
    def __init__(self):
        self.colors = array("H", [0] * 17)
        self.phase_ms = 0
        self.last_tick = -1
        self.rgb = BRAND_COLORS[0]
        self.update(0)

    def update(self, elapsed_ms):
        self.phase_ms = (self.phase_ms + max(0, elapsed_ms)) % CYCLE_MS
        tick = self.phase_ms // UPDATE_MS
        if tick == self.last_tick:
            return False
        self.last_tick = tick
        phase = tick * UPDATE_MS
        index = phase // TRANSITION_MS
        blend = phase % TRANSITION_MS
        first = BRAND_COLORS[index]
        second = BRAND_COLORS[(index + 1) % len(BRAND_COLORS)]
        red = (first[0] * (TRANSITION_MS - blend) + second[0] * blend) // TRANSITION_MS
        green = (first[1] * (TRANSITION_MS - blend) + second[1] * blend) // TRANSITION_MS
        blue = (first[2] * (TRANSITION_MS - blend) + second[2] * blend) // TRANSITION_MS
        self.rgb = (red, green, blue)
        for level in range(16):
            if level <= 11:
                r = red * (level + 1) // 12
                g = green * (level + 1) // 12
                b = blue * (level + 1) // 12
            else:
                # Lighten crests toward Gray 1 without dimming any channel.
                tint = (level - 11) * 24
                r = red + ((max(red, HIGHLIGHT[0]) - red) * tint >> 8)
                g = green + ((max(green, HIGHLIGHT[1]) - green) * tint >> 8)
                b = blue + ((max(blue, HIGHLIGHT[2]) - blue) * tint >> 8)
            self.colors[level] = rgb565(r, g, b)
        self.colors[16] = rgb565(red // 8, green // 8, blue // 8)
        return True
