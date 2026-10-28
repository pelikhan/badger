"""Q10 stencil and RGB565 raster compiled to ARM by MicroPython Viper."""

try:
    import micropython
except ImportError:
    # Execute the identical stencil on CPython for regression tests.
    class micropython:
        @staticmethod
        def viper(function):
            return function

    def ptr32(buffer):
        return memoryview(buffer).cast("B").cast("i")

    def ptr16(buffer):
        return memoryview(buffer).cast("B").cast("H")


@micropython.viper
def step(current, previous, target, width: int, height: int):
    src = ptr32(current)
    old = ptr32(previous)
    dst = ptr32(target)
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            i = y * width + x
            value = int(src[i])
            laplacian = (
                int(src[i - 1]) + int(src[i + 1])
                + int(src[i - width]) + int(src[i + width]) - 4 * value
            )
            # Combined Q10 coefficients: c^2=204/1024, momentum=1002/1024.
            value = 2024 * value - 1002 * int(old[i]) + 204 * laplacian
            # Truncate towards zero, not -infinity: no negative DC bias.
            if value < 0:
                value = -((-value) >> 10)
            else:
                value = value >> 10
            if x < 3 or x >= width - 3 or y < 3 or y >= height - 3:
                if value < 0:
                    value = -(((-value) * 900) >> 10)
                else:
                    value = (value * 900) >> 10
            if value > 12288:
                value = 12288
            elif value < -12288:
                value = -12288
            dst[i] = value


@micropython.viper
def render(current, palette, framebuffer, width: int, height: int,
           cell: int, left: int, top: int, stride: int, gain: int):
    src = ptr32(current)
    colors = ptr16(palette)
    pixels = ptr16(framebuffer)
    words = ptr32(framebuffer)
    sign = 1
    sign = sign << 31
    for i in range(int(len(framebuffer)) >> 2):
        words[i] = 0
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            value = int(src[y * width + x])
            magnitude = -value if value < 0 else value
            level = (magnitude * gain) >> 10
            if level > 15:
                level = 15
            if value < 0 and level:
                level = level - 3 if level > 3 else 1
            start = (top + y * cell) * stride + left + x * cell
            if level:
                pen = int(colors[level])
                # Signed representation also supports red/high-bit RGB565 colors.
                pair = ((pen | (pen << 16)) ^ sign) - sign
                for dy in range(cell - 1):
                    offset = start + dy * stride
                    end = offset + cell - 1
                    # Keep packed two-pixel stores aligned on odd cell sizes.
                    if offset & 1:
                        pixels[offset] = pen
                        offset += 1
                    while offset + 1 < end:
                        words[offset >> 1] = pair
                        offset += 2
                    if offset < end:
                        pixels[offset] = pen
                if level > 7 and (x + y) % 3 == 0 and cell >= 5:
                    offset = start + 2 * stride + 2
                    pixels[offset] = 0
                    pixels[offset + 1] = 0
                    pixels[offset + stride] = 0
                    pixels[offset + stride + 1] = 0
            elif x % 4 == 0 and y % 4 == 0 and cell >= 5:
                pixels[start + 3 * stride + 3] = colors[16]
