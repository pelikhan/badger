"""Synthetic correctness checks and bounded benchmarks, not a trained model."""

from array import array
import json

from runtime import (
    DecoderBlock, Linear, memory_plan, require_heap, round_div, signed,
)

STEP_BUDGET_US = 16000
EXPECTED_DECODER = (
    (16, 115, 108, 31, -12, 76, 125, -119, -67, -20, -38, -111, -78, 3, 24, 45),
    (-24, 127, 127, 12, -82, 75, 127, -128, -71, 12, -3, -128, -94, 19, 30, 5),
    (-79, 127, 127, -2, -128, 85, 127, -128, -73, 31, 36, -128, -82, 19, 38, -50),
)


def reference_dot(left, right, count, left_offset=0, right_offset=0):
    return sum(signed(left[left_offset + index]) * signed(right[right_offset + index])
               for index in range(count))


def synthetic_block(kernel, width=16, hidden=32, context=8):
    layers = []
    for seed, (columns, rows) in enumerate(
        ((width, width),) * 4 + ((width, hidden), (hidden, width))
    ):
        weights = bytearray(((index * 13 + seed * 7) % 15 - 7) & 255
                            for index in range(columns * rows))
        layers.append(Linear(weights, columns, rows, bytearray([5] * rows), kernel))
    gains = (array("h", [128] * width), array("h", [128] * width))
    return DecoderBlock(layers, gains, context, 2, 5, kernel)


def self_check(kernel):
    for count in (4, 16, 64, 256, 1024):
        for pattern in range(4):
            left = bytearray(count)
            right = bytearray(count)
            expected = 0
            for index in range(count):
                if pattern == 0:
                    a, b = -128, -128
                elif pattern == 1:
                    a, b = -128, 127
                elif pattern == 2:
                    a, b = index % 256 - 128, (index * 47) % 256 - 128
                else:
                    a, b = 127 if index % 2 else -128, 127
                left[index], right[index] = a & 255, b & 255
                expected += a * b
                if index % 32 == 31:
                    yield "Checking signed dot"
            actual = kernel.dot(left, right, count)
            if actual != expected:
                raise AssertionError("DSP dot differs from the integer reference")
            yield "Checking signed dot"
    left = bytearray((index * 47) & 255 for index in range(24))
    right = bytearray((index * 71) & 255 for index in range(24))
    if kernel.dot(left, right, 16, 4, 8) != reference_dot(left, right, 16, 4, 8):
        raise AssertionError("DSP dot offset differs from the reference")
    for shift in range(31):
        half = (1 << shift) // 2
        for value in (0, 1, -1, 127, -128, half, -half,
                      (1 << 30) - 1, -(1 << 30) + 1):
            expected = max(-128, min(127, round_div(value, 1 << shift)))
            if kernel.requantize(value, shift) != expected:
                raise AssertionError("SSAT requantization differs from the reference")
        yield "Checking rounding"


class Benchmark:
    def __init__(self, kernel, clock, free_heap):
        self.kernel = kernel
        self.clock = clock
        self.free_heap = free_heap
        self.results = {}
        self.minimum_free = free_heap()
        self.maximum_quantum_us = 0
        self.quantums = 0

    def advance(self, worker):
        started = self.clock.ticks_us()
        finished = False
        try:
            status = next(worker)
        except StopIteration:
            finished = True
        elapsed = self.clock.ticks_diff(self.clock.ticks_us(), started)
        self.maximum_quantum_us = max(self.maximum_quantum_us, elapsed)
        self.quantums += 1
        if elapsed > STEP_BUDGET_US:
            worker.close()
            raise RuntimeError("work slice exceeded 16 ms: %d us" % elapsed)
        if finished:
            self.sample_heap()
            if "decoder" in self.results:
                self.results["decoder"]["minimum_sampled_free"] = self.minimum_free
                print("tiny_inference decoder " + json.dumps(self.results["decoder"]))
                print("tiny_inference correctness PASS")
            self.results["scheduler"] = {
                "maximum_quantum_us": self.maximum_quantum_us,
                "quantums": self.quantums, "budget_us": STEP_BUDGET_US,
            }
            print("tiny_inference scheduler " + json.dumps(self.results["scheduler"]))
            raise StopIteration
        return status

    def sample_heap(self):
        free = self.free_heap()
        self.minimum_free = min(self.minimum_free, free)
        require_heap(0, free)

    def run(self):
        require_heap(128 * 1024, self.free_heap())
        block = synthetic_block(self.kernel)
        return self._run(block)

    def _run(self, block):
        print("tiny_inference plan " + json.dumps(memory_plan()))
        yield from self_check(self.kernel)
        print("tiny_inference kernels PASS")
        for count in (64, 128, 256, 512, 1024):
            left = bytearray(count)
            right = bytearray(count)
            expected = 0
            for offset in range(0, count, 32):
                for index in range(offset, offset + 32):
                    left[index] = (index * 29) & 255
                    right[index] = (index * 17 + 3) & 255
                expected += reference_dot(left, right, 32, offset, offset)
                yield "Preparing dot %d" % count
            samples = []
            repetitions = 16
            for _ in range(9):
                started = self.clock.ticks_us()
                for _ in range(repetitions):
                    actual = self.kernel.dot(left, right, count)
                elapsed = self.clock.ticks_diff(self.clock.ticks_us(), started)
                if actual != expected:
                    raise AssertionError("timed DSP dot result changed")
                if elapsed <= 0:
                    raise RuntimeError("benchmark timer did not advance")
                samples.append(elapsed)
                yield "Dot %d x %d" % (count, repetitions)
            median = sorted(samples)[len(samples) // 2]
            result = {
                "count": count, "repetitions": repetitions,
                "median_batch_us": median,
                "mac_per_second": count * repetitions * 1000000 // median,
                "includes_python_boundary": True,
            }
            self.results["dot_%d" % count] = result
            print("tiny_inference dot " + json.dumps(result))

        columns, rows = 256, 64
        weights = bytearray(columns * rows)
        for index in range(len(weights)):
            weights[index] = (index * 11) & 255
            if index % 64 == 63:
                yield "Preparing matrix"
        inputs = bytearray((index * 31 + 1) & 255 for index in range(columns))
        output = bytearray(rows)
        linear = Linear(weights, columns, rows, bytearray([9] * rows), self.kernel)
        kernel_us = 0
        for row in range(rows):
            started = self.clock.ticks_us()
            linear.row(inputs, output, row)
            kernel_us += self.clock.ticks_diff(self.clock.ticks_us(), started)
            expected = reference_dot(weights, inputs, columns, row * columns)
            expected = max(-128, min(127, round_div(expected, 512)))
            if signed(output[row]) != expected:
                raise AssertionError("matrix row differs from the reference")
            yield "Matrix row %d/%d" % (row + 1, rows)
        self.results["matrix"] = {
            "rows": rows, "columns": columns, "compute_us": kernel_us,
            "checksum": sum(signed(value) for value in output),
            "excludes_frame_waits_and_reference": True,
        }
        print("tiny_inference matrix " + json.dumps(self.results["matrix"]))
        del linear, weights, inputs, output

        inputs = bytearray((index * 19 + 7) & 255 for index in range(16))
        compute_us = 0
        for token in range(3):
            steps = block.step(inputs)
            try:
                while True:
                    started = self.clock.ticks_us()
                    try:
                        next(steps)
                    except StopIteration:
                        compute_us += self.clock.ticks_diff(self.clock.ticks_us(), started)
                        break
                    compute_us += self.clock.ticks_diff(self.clock.ticks_us(), started)
                    yield "Decoder token %d/3" % (token + 1)
            finally:
                steps.close()
            if tuple(signed(value) for value in block.output) != EXPECTED_DECODER[token]:
                raise AssertionError("synthetic decoder differs from the reference")
            inputs[:] = block.output
        self.results["decoder"] = {
            "width": 16, "hidden": 32, "heads": 2, "tokens": 3,
            "compute_us": compute_us, "minimum_sampled_free": self.minimum_free,
            "checksum": sum(signed(value) for value in block.output),
            "synthetic_weights_not_text_generation": True,
        }
        yield "Complete"
