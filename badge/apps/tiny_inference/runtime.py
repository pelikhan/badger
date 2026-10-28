"""Bounded integer decoder primitives, with explicit quantization scales."""

from array import array
import gc


APP_BUDGET = 4 * 1024 * 1024
HEAP_RESERVE = 512 * 1024
OBJECT_ALLOWANCE = 64 * 1024

_EXP_Q15 = (
    32768, 30783, 28918, 27166, 25520, 23974, 22521, 21157, 19875, 18671, 17539, 16477,
    15479, 14541, 13660, 12832, 12055, 11324, 10638, 9994, 9388, 8819, 8285, 7783,
    7312, 6869, 6452, 6061, 5694, 5349, 5025, 4721, 4435, 4166, 3914, 3676,
    3454, 3244, 3048, 2863, 2690, 2527, 2374, 2230, 2095, 1968, 1849, 1737,
    1631, 1533, 1440, 1352, 1271, 1194, 1121, 1053, 990, 930, 873, 820,
    771, 724, 680, 639, 600, 564, 530, 498, 467, 439, 412, 387,
    364, 342, 321, 302, 283, 266, 250, 235, 221, 207, 195, 183,
    172, 162, 152, 143, 134, 126, 118, 111, 104, 98, 92, 86,
    81, 76, 72, 67, 63, 59, 56, 52, 49, 46, 43, 41,
    38, 36, 34, 32, 30, 28, 26, 25, 23, 22, 21, 19,
    18, 17, 16, 15, 14, 13, 12, 12, 11,
)


def _integer(value, name, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(name + " is outside its supported integer range")


def _vector(buffer, length, name):
    if not isinstance(buffer, bytearray) or len(buffer) != length:
        raise ValueError(name + " must be a bytearray of length " + str(length))


def _integer_array(buffer, itemsize, sentinel):
    if not isinstance(buffer, array) or memoryview(buffer).itemsize != itemsize:
        return False
    # MicroPython lacks array.typecode; an empty slice retains the element format.
    probe = buffer[:0]
    try:
        probe.append(sentinel)
    except OverflowError:
        return False
    return type(probe[0]) is int and probe[0] == sentinel


def signed(value):
    return value if value < 128 else value - 256


def clip_i8(value):
    return max(-128, min(127, value))


def round_div(value, divisor):
    if divisor <= 0:
        raise ValueError("divisor must be positive")
    if value < 0:
        return -((-value + divisor // 2) // divisor)
    return (value + divisor // 2) // divisor


def isqrt(value):
    if value < 0:
        raise ValueError("square root of negative integer")
    if value < 2:
        return value
    root = value
    while True:
        candidate = (root + value // root) // 2
        if candidate >= root:
            return root
        root = candidate


def memory_plan(width=256, hidden=512, layers=4, heads=4, context=128, vocabulary=512):
    """Conservative prospective payload, not a model loader or free-heap claim."""
    for value, name, low, high in (
        (width, "width", 4, 512), (hidden, "hidden", 4, 1024),
        (layers, "layers", 1, 4), (heads, "heads", 1, 16),
        (context, "context", 1, 128), (vocabulary, "vocabulary", 4, 1024),
    ):
        _integer(value, name, low, high)
    if width % 4 or hidden % 4 or width % heads or (width // heads) % 4:
        raise ValueError("width, hidden, and each attention head must be multiples of four")
    weights = layers * (4 * width * width + 2 * width * hidden)
    weights += vocabulary * width
    positions = context * width
    gains = (2 * layers + 1) * width * 2
    shifts = layers * (5 * width + hidden) + vocabulary
    cache = 2 * layers * context * width
    # Scratch is per layer, even if a future executor can reuse it across layers.
    workspace = layers * (10 * width + hidden + 6 * context)
    payload = weights + positions + gains + shifts + cache + workspace
    total = payload + OBJECT_ALLOWANCE
    if total > APP_BUDGET:
        raise ValueError("model exceeds the 4 MiB app allocation budget")
    return {
        "weights": weights, "positions": positions, "gains": gains,
        "shifts": shifts, "cache": cache, "workspace": workspace,
        "objects": OBJECT_ALLOWANCE, "total": total,
    }


def require_heap(required, available):
    _integer(required, "required bytes", 0, APP_BUDGET)
    _integer(available, "available bytes", 0, 1 << 30)
    if required + HEAP_RESERVE > available:
        raise MemoryError("not enough free heap to preserve the 512 KiB reserve")


class Linear:
    """Bias-free W8A8 projection with a power-of-two scale per output row."""

    def __init__(self, weights, columns, rows, shifts, kernel):
        _integer(columns, "columns", 4, 1024)
        _integer(rows, "rows", 1, 1024)
        if columns % 4:
            raise ValueError("linear columns must be a multiple of four")
        _vector(weights, columns * rows, "weights")
        _vector(shifts, rows, "shifts")
        if any(shift > 30 for shift in shifts):
            raise ValueError("linear shifts must be in 0..30")
        self.weights = weights
        self.columns = columns
        self.rows = rows
        self.shifts = shifts
        self.kernel = kernel

    def row(self, inputs, output, index):
        _vector(inputs, self.columns, "inputs")
        _vector(output, self.rows, "output")
        _integer(index, "row index", 0, self.rows - 1)
        if output is inputs or output is self.weights or output is self.shifts:
            raise ValueError("linear output must not alias an input or parameter")
        value = self.kernel.dot(self.weights, inputs, self.columns, index * self.columns)
        output[index] = self.kernel.requantize(value, self.shifts[index]) & 255

    def run(self, inputs, output):
        for index in range(self.rows):
            self.row(inputs, output, index)
            yield


def rms_norm(inputs, gains, output, kernel):
    """RMSNorm to Q6 activations; signed int16 gains are Q7, epsilon is one."""
    length = len(inputs)
    _vector(inputs, length, "inputs")
    _vector(output, length, "output")
    if not length or length > 512 or length % 4:
        raise ValueError("RMSNorm width must be a multiple of four up to 512")
    if not _integer_array(gains, 2, -1) or len(gains) != length:
        raise ValueError("RMSNorm gains must be an int16 array matching its width")
    square_sum = kernel.dot(inputs, inputs, length)
    divisor = isqrt(((square_sum << 16) // length) + 65536)
    for index in range(length):
        value = signed(inputs[index]) * gains[index] * 128
        output[index] = clip_i8(round_div(value, divisor)) & 255


def softmax_q15(scores, count, probabilities):
    """Q8 natural-log scores to Q15 probabilities with exact sum 32768."""
    _integer(count, "softmax count", 1, 128)
    if not _integer_array(scores, 4, -1):
        raise ValueError("softmax scores must be an int32 array")
    if not _integer_array(probabilities, 2, 65535):
        raise ValueError("softmax probabilities must be a uint16 array")
    if len(scores) < count or len(probabilities) < count:
        raise ValueError("softmax buffers are too small")
    peak = max(scores[index] for index in range(count))
    peak_index = 0
    total = 0
    for index in range(count):
        delta = peak - scores[index]
        if delta >= 2048:
            value = 0
        else:
            position, fraction = delta >> 4, delta & 15
            value = (_EXP_Q15[position] * (16 - fraction)
                     + _EXP_Q15[position + 1] * fraction + 8) >> 4
        probabilities[index] = value
        total += value
        if scores[index] == peak:
            peak_index = index
    used = 0
    for index in range(count):
        value = probabilities[index] * 32768 // total
        probabilities[index] = value
        used += value
    probabilities[peak_index] += 32768 - used


class KVCache:
    """Fixed-capacity int8 K/V storage; a full context is an explicit error."""

    def __init__(self, width, context):
        _integer(width, "cache width", 4, 512)
        _integer(context, "cache context", 1, 128)
        if width % 4:
            raise ValueError("cache width must be a multiple of four")
        require_heap(2 * width * context + 6 * context + 2048, gc.mem_free())
        self.width = width
        self.context = context
        self.keys = bytearray(width * context)
        self.values = bytearray(width * context)
        self.scores = array("i", [0] * context)
        self.probabilities = array("H", [0] * context)
        self.length = 0

    def clear(self):
        self.length = 0

    def append(self, key, value):
        _vector(key, self.width, "key")
        _vector(value, self.width, "value")
        if (key is self.keys or key is self.values
                or value is self.keys or value is self.values):
            raise ValueError("append inputs must not alias cache storage")
        if self.length == self.context:
            raise ValueError("context full; clear the cache before a new sequence")
        offset = self.length * self.width
        self.keys[offset:offset + self.width] = key
        self.values[offset:offset + self.width] = value
        self.length += 1

    def attend(self, query, output, heads, score_shift, kernel):
        _vector(query, self.width, "query")
        _vector(output, self.width, "attention output")
        _integer(heads, "heads", 1, 16)
        _integer(score_shift, "score shift", 0, 30)
        if not self.length:
            raise ValueError("attention requires at least one cached token")
        if self.width % heads or (self.width // heads) % 4:
            raise ValueError("each attention head must be a multiple of four")
        if output is query or output is self.keys or output is self.values:
            raise ValueError("attention output must not alias the query or cache")
        head_width = self.width // heads
        for head in range(heads):
            offset = head * head_width
            for token in range(self.length):
                score = kernel.dot(query, self.keys, head_width, offset,
                                   token * self.width + offset)
                self.scores[token] = round_div(score, 1 << score_shift)
                yield
            softmax_q15(self.scores, self.length, self.probabilities)
            yield
            for channel in range(offset, offset + head_width):
                value = 0
                for token in range(self.length):
                    value += self.probabilities[token] * signed(
                        self.values[token * self.width + channel])
                output[channel] = clip_i8(round_div(value, 32768)) & 255
                yield


class DecoderBlock:
    """Pre-RMS causal attention and ReLU FFN; no tokenizer or trained weights."""

    def __init__(self, projections, gains, context, heads, score_shift, kernel):
        _integer(context, "context", 1, 128)
        if len(projections) != 6:
            raise ValueError("six projections required: Q, K, V, O, up, down")
        q, k, v, o, up, down = projections
        width = q.columns
        if width > 512 or any(
            layer.columns != width or layer.rows != width for layer in (q, k, v, o)
        ) or up.columns != width or down.columns != up.rows or down.rows != width:
            raise ValueError("inconsistent decoder projection dimensions")
        if any(layer.kernel is not kernel for layer in projections):
            raise ValueError("decoder projections must share the selected kernel")
        _integer(heads, "heads", 1, 16)
        _integer(score_shift, "score shift", 0, 30)
        if width % heads or (width // heads) % 4:
            raise ValueError("each decoder head must be a multiple of four")
        if len(gains) != 2 or any(
            not _integer_array(gain, 2, -1) or len(gain) != width
            for gain in gains
        ):
            raise ValueError("two matching int16 RMSNorm gain arrays required")
        self.projections = projections
        self.gains = gains
        self.heads = heads
        self.score_shift = score_shift
        self.kernel = kernel
        require_heap(
            2 * width * context + 6 * context + 9 * width + up.rows + 4096,
            gc.mem_free(),
        )
        self.cache = KVCache(width, context)
        self.inputs = bytearray(width)
        self.norm = bytearray(width)
        self.query = bytearray(width)
        self.key = bytearray(width)
        self.value = bytearray(width)
        self.attention = bytearray(width)
        self.projected = bytearray(width)
        self.hidden = bytearray(up.rows)
        self.output = bytearray(width)
        self.next_output = bytearray(width)
        self.busy = False

    def clear(self):
        if self.busy:
            raise RuntimeError("cannot clear a decoder step in progress")
        self.cache.clear()

    def step(self, inputs):
        """Snapshot inputs; roll back cache on close and publish output on success."""
        _vector(inputs, self.cache.width, "decoder inputs")
        if inputs is self.inputs:
            raise ValueError("decoder inputs must not alias its private snapshot")
        if self.busy:
            raise RuntimeError("decoder step already in progress")
        if self.cache.length == self.cache.context:
            raise ValueError("decoder context is full")
        self.busy = True
        old_length = self.cache.length
        completed = False
        try:
            self.inputs[:] = inputs
            q, k, v, o, up, down = self.projections
            rms_norm(self.inputs, self.gains[0], self.norm, self.kernel)
            yield
            for layer, output in ((q, self.query), (k, self.key), (v, self.value)):
                yield from layer.run(self.norm, output)
            self.cache.append(self.key, self.value)
            yield from self.cache.attend(
                self.query, self.attention, self.heads, self.score_shift, self.kernel)
            yield from o.run(self.attention, self.projected)
            for index in range(self.cache.width):
                self.projected[index] = clip_i8(
                    signed(self.inputs[index]) + signed(self.projected[index])) & 255
            rms_norm(self.projected, self.gains[1], self.norm, self.kernel)
            yield
            yield from up.run(self.norm, self.hidden)
            for index in range(len(self.hidden)):
                if self.hidden[index] >= 128:
                    self.hidden[index] = 0
            yield
            yield from down.run(self.hidden, self.next_output)
            for index in range(self.cache.width):
                self.next_output[index] = clip_i8(
                    signed(self.projected[index]) + signed(self.next_output[index])) & 255
            self.output[:] = self.next_output
            completed = True
        finally:
            if not completed:
                self.cache.length = old_length
            self.busy = False
