"""Host references and optional execution of the actual mpy-cross Thumb output."""

from array import array
import gc
import importlib.util
import io
import itertools
import json
import math
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout


APP = Path(__file__).resolve().parents[1] / "badge/apps/tiny_inference"


def load(name, file, modules=None):
    spec = importlib.util.spec_from_file_location(name, APP / file)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules or {}):
        spec.loader.exec_module(module)
    return module


runtime = load("tiny_inference_runtime", "runtime.py")
benchmark = load("tiny_inference_benchmark", "benchmark.py", {"runtime": runtime})
poetry = load("tiny_inference_poet", "poet.py", {"runtime": runtime})


def decode(buffer):
    return [int.from_bytes(bytes([value]), "little", signed=True) for value in buffer]


def encode(values):
    return bytearray(value & 255 for value in values)


def saturate(value):
    return max(-128, min(127, value))


def rounded(value, divisor):
    quotient, remainder = divmod(abs(value), divisor)
    return (-1 if value < 0 else 1) * (quotient + (2 * remainder >= divisor))


class ReferenceKernel:
    """Test oracle only; the app never imports or selects this implementation."""

    @staticmethod
    def dot(left, right, count, left_offset=0, right_offset=0):
        return sum((a if a < 128 else a - 256) * (b if b < 128 else b - 256)
                   for a, b in zip(left[left_offset:left_offset + count],
                                   right[right_offset:right_offset + count]))

    @staticmethod
    def requantize(value, shift):
        return saturate(rounded(value, 1 << shift))


def normalized(values, gains):
    divisor = math.isqrt(sum(x * x for x in values) * 65536 // len(values) + 65536)
    return [saturate(rounded(x * gain * 128, divisor))
            for x, gain in zip(values, gains)]


def projected(layer, values):
    weights = decode(layer.weights)
    return [saturate(rounded(sum(
        values[i] * weights[row * layer.columns + i] for i in range(layer.columns)
    ), 1 << layer.shifts[row])) for row in range(layer.rows)]


def block_reference(block, inputs, keys, values):
    q, k, v, o, up, down = block.projections
    norm = normalized(inputs, block.gains[0])
    query = projected(q, norm)
    keys.append(projected(k, norm))
    values.append(projected(v, norm))
    attended = []
    head_width = len(inputs) // block.heads
    lut = [int(math.exp(-i / 16) * 32768 + 0.5) for i in range(129)]
    for head in range(block.heads):
        channels = range(head * head_width, (head + 1) * head_width)
        scores = [rounded(sum(query[i] * key[i] for i in channels),
                          1 << block.score_shift) for key in keys]
        peak = max(scores)
        masses = []
        for score in scores:
            delta = peak - score
            if delta >= 2048:
                masses.append(0)
            else:
                index, fraction = divmod(delta, 16)
                masses.append((lut[index] * (16 - fraction)
                               + lut[index + 1] * fraction + 8) // 16)
        probabilities = [mass * 32768 // sum(masses) for mass in masses]
        last_peak = len(scores) - 1 - scores[::-1].index(peak)
        probabilities[last_peak] += 32768 - sum(probabilities)
        attended.extend(saturate(rounded(sum(
            probability * value[i] for probability, value in zip(probabilities, values)
        ), 32768)) for i in channels)
    residual = [saturate(x + y) for x, y in zip(inputs, projected(o, attended))]
    hidden = [max(0, x) for x in projected(up, normalized(residual, block.gains[1]))]
    return [saturate(x + y) for x, y in zip(residual, projected(down, hidden))]


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.heap = patch.object(gc, "mem_free", return_value=8 * 1024 * 1024,
                                 create=True)
        self.heap.start()
        self.addCleanup(self.heap.stop)
        self.kernel = ReferenceKernel()

    def block(self):
        return benchmark.synthetic_block(self.kernel, width=8, hidden=16, context=4)

    def test_integer_array_formats_without_typecode_and_without_mutation(self):
        for itemsize, sentinel, expected in ((2, -1, "h"), (2, 65535, "H"), (4, -1, "i")):
            for kind in ("b", "B", "h", "H", "i", "I", "f", "d"):
                for values in ([], [1, 2, 3]):
                    buffer = array(kind, values)
                    original = buffer.tobytes()
                    self.assertEqual(runtime._integer_array(buffer, itemsize, sentinel),
                                     kind == expected, (itemsize, sentinel, kind))
                    self.assertEqual(buffer.tobytes(), original)
        self.assertFalse(runtime._integer_array(bytearray(4), 4, -1))

    def poet(self, seed=349):
        return poetry.Poet(self.kernel, random.Random(seed).getrandbits,
                           assets=str(APP / "assets"))

    def test_poet_model_integrity_and_allocation(self):
        poet = self.poet()
        list(poet.prepare())
        self.assertTrue(poet.ready)
        self.assertEqual(len(poet.recurrent.weights) + len(poet.readout.weights), 13328)
        buffers = (poet.recurrent.weights, poet.recurrent.shifts, poet.readout.weights,
                   poet.readout.shifts, poet.inputs, poet.hidden, poet.next_hidden,
                   poet.output_inputs, poet.logits, poet.scores, poet.probabilities)
        payload = sum(len(b) * (b.itemsize if isinstance(b, array) else 1) for b in buffers)
        self.assertEqual(payload, 14268)
        with patch.object(runtime.gc, "mem_free", return_value=runtime.HEAP_RESERVE):
            with self.assertRaises(MemoryError):
                self.poet()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            valid_manifest = (APP / "assets/poet.json").read_bytes()
            path.joinpath("poet.json").write_bytes(valid_manifest)
            valid = (APP / "assets/poet.bin").read_bytes()
            for corrupted in (valid[:-1], valid + b"x", b"xxxx" + valid[4:],
                              valid[:100] + bytes([valid[100] ^ 1]) + valid[101:]):
                path.joinpath("poet.bin").write_bytes(corrupted)
                broken = poetry.Poet(self.kernel, random.Random(1).getrandbits, assets=directory)
                with self.assertRaises(ValueError):
                    list(broken.prepare())
                self.assertFalse(broken.busy)
                self.assertFalse(broken.ready)
            path.joinpath("poet.bin").write_bytes(valid)
            wrong_vocabulary = json.loads(valid_manifest)
            wrong_vocabulary["vocabulary_sha256"] = "0" * 64
            for invalid in ("[]", "{", " " * 4097, json.dumps(wrong_vocabulary)):
                path.joinpath("poet.json").write_text(invalid)
                broken = poetry.Poet(self.kernel, random.Random(1).getrandbits, assets=directory)
                with self.assertRaises(ValueError):
                    list(broken.prepare())
                self.assertFalse(broken.busy)
            path.joinpath("poet.json").unlink()
            broken = poetry.Poet(self.kernel, random.Random(1).getrandbits, assets=directory)
            with self.assertRaises(OSError):
                list(broken.prepare())

    def test_poet_native_projection_matches_independent_integer_equations(self):
        poet = self.poet()
        list(poet.prepare())
        for _ in range(14):
            expected_hidden = [max(-64, min(64, value)) for value in
                               projected(poet.recurrent, decode(poet.inputs))]
            expected_inputs = expected_hidden + [64, 0, 0, 0]
            expected_logits = projected(poet.readout, expected_inputs)
            list(poet.step())
            self.assertEqual(decode(poet.hidden), expected_hidden)
            self.assertEqual(decode(poet.logits), expected_logits)
            self.assertEqual(decode(poet.inputs[poetry.VOCABULARY:
                                               poetry.VOCABULARY + poetry.HIDDEN]),
                             expected_hidden)
            if poet.complete:
                break

    def test_poet_cancellation_every_yield_and_reentrancy(self):
        baseline = self.poet()
        list(baseline.prepare())
        count = len(list(baseline.step()))
        expected = (bytes(baseline.hidden), baseline.text)
        self.assertEqual(count, poetry.HIDDEN + poetry.VOCABULARY)
        poet = self.poet()
        list(poet.prepare())
        for stop in range(count + 1):
            poet.restart()
            poet.random_bits = random.Random(349).getrandbits
            before = bytes(poet.inputs)
            task = poet.step()
            for _ in range(stop):
                next(task)
            if stop:
                with self.assertRaises(RuntimeError):
                    next(poet.step())
                with self.assertRaises(RuntimeError):
                    poet.restart()
            task.close()
            self.assertFalse(poet.busy)
            self.assertEqual(bytes(poet.inputs), before)
            self.assertEqual(poet.hidden, bytearray(poetry.HIDDEN))
            self.assertEqual(poet.text, ())
            list(poet.step())
            self.assertEqual((bytes(poet.hidden), poet.text), expected)
        partial = self.poet()
        task = partial.prepare()
        next(task)
        task.close()
        self.assertFalse(partial.busy)
        self.assertFalse(partial.ready)
        list(partial.prepare())
        self.assertTrue(partial.ready)

    def test_poet_endless_generation_is_bounded_and_learned(self):
        poet = self.poet()
        list(poet.prepare())
        buffers = tuple(id(b) for b in (poet.inputs, poet.hidden, poet.next_hidden,
                                       poet.recurrent.weights, poet.readout.weights,
                                       poet.scores, poet.probabilities, poet.logits))
        grammar = (poetry.ADJECTIVES, poetry.NOUNS, poetry.VERBS, poetry.PREPOSITIONS,
                   poetry.ADJECTIVES, poetry.NOUNS)
        valid_lines, total_lines = 0, 0
        examples = set()
        poems = 200 if isinstance(self.kernel, ReferenceKernel) else 4
        for _ in range(poems):
            tokens = 0
            while not poet.complete:
                list(poet.step())
                tokens += 1
                self.assertLessEqual(tokens, 2 * (poetry.MAX_WORDS + 1))
                self.assertLessEqual(len(poet.words), poetry.MAX_WORDS)
                self.assertLessEqual(len(poet.selected), 2 * poetry.MAX_WORDS)
                self.assertLessEqual(len(poet.lines), 2)
                self.assertLessEqual(len(poet.text), 2)
            for line in poet.text:
                words = line[:-1].lower().split()
                self.assertTrue(3 <= len(words) <= poetry.MAX_WORDS)
                valid_lines += len(words) == 6 and all(w in group for w, group in zip(words, grammar))
                total_lines += 1
            if len(examples) < 20:
                examples.add(poet.text)
            self.assertEqual(tuple(id(b) for b in (poet.inputs, poet.hidden, poet.next_hidden,
                                                  poet.recurrent.weights, poet.readout.weights,
                                                  poet.scores, poet.probabilities, poet.logits)), buffers)
            poet.restart()
        self.assertGreaterEqual(valid_lines / total_lines, 0.9)
        self.assertGreater(len(examples), min(10, poems - 1))
        poet.number = 999999
        poet.restart()
        self.assertEqual(poet.number, 1)

    def test_poet_sampling_failure_rolls_back(self):
        poet = self.poet()
        list(poet.prepare())
        before = bytes(poet.inputs)
        poet.random_bits = lambda bits: -1
        with self.assertRaises(ValueError):
            list(poet.step())
        self.assertFalse(poet.busy)
        self.assertEqual(bytes(poet.inputs), before)
        self.assertEqual(poet.text, ())

    def test_poet_forced_boundaries_prevent_unbounded_lines(self):
        poet = self.poet()
        list(poet.prepare())
        poet.readout.weights[:] = bytearray(len(poet.readout.weights))
        poet.random_bits = lambda bits: 0
        count = 0
        while not poet.complete:
            list(poet.step())
            count += 1
            self.assertLessEqual(count, 2 * (poetry.MAX_WORDS + 1))
        self.assertEqual(poet.forced_boundaries, 2)
        self.assertEqual([len(line.split()) for line in poet.text], [8, 8])

    def test_poem_wrap_uses_measured_width(self):
        measure = lambda text: (len(text) * 6, 8)
        paragraphs = ("Drifting tides dream beneath distant harbors.",) * 2
        wrapped = poetry.wrap_lines(paragraphs, 152, measure)
        self.assertIn("", wrapped)
        self.assertTrue(all(measure(line)[0] <= 152 for line in wrapped))
        with self.assertRaises(ValueError):
            poetry.wrap_lines(paragraphs, 0, measure)
        with self.assertRaises(ValueError):
            poetry.wrap_lines(paragraphs, 1, measure)

    def test_rounding_and_integer_square_root(self):
        for value in range(-257, 258):
            for divisor in (1, 2, 3, 5, 16, 255):
                self.assertEqual(runtime.round_div(value, divisor), rounded(value, divisor))
        for value in list(range(1000)) + [2**48 - 1, 2**48, 2**48 + 1]:
            self.assertEqual(runtime.isqrt(value), math.isqrt(value))
        with self.assertRaises(ValueError):
            runtime.round_div(1, 0)
        with self.assertRaises(ValueError):
            runtime.isqrt(-1)

    def test_norm_quantization(self):
        rng = random.Random(138)
        for width in (4, 16, 512):
            for pattern in ([-128] * width, [0] * width,
                            [rng.randrange(-128, 128) for _ in range(width)]):
                gains = array("h", [rng.randrange(-32768, 32768) for _ in range(width)])
                inputs = encode(pattern)
                output = bytearray(width)
                runtime.rms_norm(inputs, gains, output, self.kernel)
                self.assertEqual(decode(output), normalized(pattern, gains))
                runtime.rms_norm(inputs, gains, inputs, self.kernel)
                self.assertEqual(inputs, output)
        unity = encode([1, 1, -1, -1])
        runtime.rms_norm(unity, array("h", [128] * 4), unity, self.kernel)
        self.assertEqual(decode(unity), [45, 45, -45, -45])

    def test_linear_and_alias_guards(self):
        weights = encode([-128, 127, 3, -9] * 4)
        layer = runtime.Linear(weights, 4, 4, bytearray([0, 5, 15, 30]), self.kernel)
        inputs, output = encode([-7, 42, -128, 127]), bytearray(4)
        list(layer.run(inputs, output))
        self.assertEqual(decode(output), projected(layer, decode(inputs)))
        for target in (inputs, layer.shifts):
            with self.assertRaises(ValueError):
                layer.row(inputs, target, 0)
        with self.assertRaises(ValueError):
            runtime.Linear(weights, 4, 4, bytearray([31] * 4), self.kernel)
        with self.assertRaises(ValueError):
            layer.row(inputs, output, -1)

    def test_softmax_shape_accuracy_and_exact_sum(self):
        rng = random.Random(984)
        self.assertEqual(tuple(runtime._EXP_Q15),
                         tuple(int(math.exp(-i / 16) * 32768 + 0.5) for i in range(129)))
        cases = [[0], [1] * 128, [2047, 0], [2048, 0], [2**31 - 1, -2**31]]
        cases += [[rng.randrange(-1200, 1200) for _ in range(128)] for _ in range(10)]
        for scores in cases:
            result = array("H", [0] * len(scores))
            runtime.softmax_q15(array("i", scores), len(scores), result)
            self.assertEqual(sum(result), 32768)
            masses = [math.exp((s - max(scores)) / 256) for s in scores]
            self.assertLess(max(abs(p / 32768 - m / sum(masses))
                                for p, m in zip(result, masses)), 0.004)
        for scores, count, probabilities in (
            (array("i", [0]), 0, array("H", [0])),
            (array("i", [0]), 2, array("H", [0])),
            ([0], 1, array("H", [0])),
            (array("i", [0]), 1, bytearray(2)),
        ):
            with self.assertRaises(ValueError):
                runtime.softmax_q15(scores, count, probabilities)

    def test_cache_attention_and_capacity(self):
        cache = runtime.KVCache(8, 2)
        cache.append(bytearray(8), encode([-128, 127, 5, 6, 10, 11, 12, 13]))
        output = bytearray(8)
        list(cache.attend(bytearray(8), output, 2, 5, self.kernel))
        self.assertEqual(output, cache.values[:8])
        cache.append(bytearray(8), encode([126, -127, -5, -6, -10, -11, -12, -13]))
        list(cache.attend(bytearray(8), output, 2, 5, self.kernel))
        self.assertEqual(decode(output), [-1, 0, 0, 0, 0, 0, 0, 0])
        with self.assertRaises(ValueError):
            cache.append(bytearray(8), bytearray(8))
        cache.clear()
        with self.assertRaises(ValueError):
            list(cache.attend(bytearray(8), output, 2, 5, self.kernel))
        singleton = runtime.KVCache(8, 1)
        singleton.append(bytearray(8), bytearray(8))
        for target in (singleton.keys, singleton.values):
            with self.assertRaises(ValueError):
                list(singleton.attend(bytearray(8), target, 2, 5, self.kernel))
        singleton.clear()
        for key, value in ((singleton.keys, bytearray(8)),
                           (bytearray(8), singleton.keys),
                           (singleton.values, singleton.keys)):
            with self.assertRaises(ValueError):
                singleton.append(key, value)

    def test_decoder_matches_independent_reference_and_full_context(self):
        block = self.block()
        keys, values = [], []
        inputs = [-128, 127, -54, 2, 50, 67, -76, 80]
        for _ in range(4):
            expected = block_reference(block, inputs, keys, values)
            list(block.step(encode(inputs)))
            self.assertEqual(decode(block.output), expected)
            inputs = expected
        with self.assertRaises(ValueError):
            list(block.step(block.output))
        block.clear()
        self.assertEqual(block.cache.length, 0)
        list(block.step(block.output))
        self.assertEqual(block.cache.length, 1)

    def test_on_device_golden_vectors_have_independent_provenance(self):
        block = benchmark.synthetic_block(self.kernel)
        inputs = decode(bytearray((i * 19 + 7) & 255 for i in range(16)))
        keys, values = [], []
        for expected in benchmark.EXPECTED_DECODER:
            inputs = block_reference(block, inputs, keys, values)
            self.assertEqual(tuple(inputs), expected)

    def test_cancellation_at_every_yield_preserves_committed_state_and_retries(self):
        inputs = encode([-128, 127, -5, 2, 50, 67, -76, 80])
        baseline = self.block()
        list(baseline.step(inputs))
        count = len(list(baseline.step(baseline.output)))
        expected = bytes(baseline.output)
        for stop in range(count + 1):
            block = self.block()
            list(block.step(inputs))
            committed = bytes(block.output)
            previous_keys = bytes(block.cache.keys[:8])
            previous_values = bytes(block.cache.values[:8])
            task = block.step(block.output)
            for _ in range(stop):
                next(task)
            task.close()
            self.assertFalse(block.busy, stop)
            self.assertEqual(block.cache.length, 1, stop)
            self.assertEqual(bytes(block.output), committed, stop)
            self.assertEqual(bytes(block.cache.keys[:8]), previous_keys, stop)
            self.assertEqual(bytes(block.cache.values[:8]), previous_values, stop)
            list(block.step(block.output))
            self.assertEqual(bytes(block.output), expected, stop)

    def test_snapshot_reentrancy_and_failure_rollback(self):
        inputs = encode([20] * 8)
        expected = self.block()
        list(expected.step(inputs))
        block = self.block()
        task = block.step(inputs)
        next(task)
        inputs[:] = bytearray(8)
        with self.assertRaises(RuntimeError):
            next(block.step(inputs))
        with self.assertRaises(RuntimeError):
            block.clear()
        list(task)
        self.assertEqual(block.output, expected.output)
        prior = bytes(block.output)
        with patch.object(self.kernel, "dot", side_effect=ValueError("injected")):
            with self.assertRaisesRegex(ValueError, "injected"):
                list(block.step(inputs))
        self.assertFalse(block.busy)
        self.assertEqual(block.cache.length, 1)
        self.assertEqual(bytes(block.output), prior)
        task = block.step(inputs)
        while block.cache.length == 1:
            next(task)
        with patch.object(self.kernel, "dot", side_effect=ValueError("after append")):
            with self.assertRaisesRegex(ValueError, "after append"):
                next(task)
        self.assertFalse(block.busy)
        self.assertEqual(block.cache.length, 1)
        self.assertEqual(bytes(block.output), prior)

    def test_memory_payload_and_heap_limits(self):
        plan = runtime.memory_plan()
        self.assertEqual(plan["total"], sum(v for k, v in plan.items() if k != "total"))
        self.assertLessEqual(plan["total"], 4 * 1024 * 1024)
        for dimensions in ({"width": 512, "hidden": 1024}, {"width": 7},
                           {"width": True}, {"heads": 3}, {"context": 129}):
            with self.assertRaises(ValueError):
                runtime.memory_plan(**dimensions)
        runtime.require_heap(1024, runtime.HEAP_RESERVE + 1024)
        with self.assertRaises(MemoryError):
            runtime.require_heap(1024, runtime.HEAP_RESERVE + 1023)
        block = self.block()
        plan = runtime.memory_plan(8, 16, 1, 2, 4, 4)
        buffers = (block.inputs, block.norm, block.query, block.key, block.value,
                   block.attention, block.projected, block.output, block.next_output,
                   block.hidden, block.cache.scores, block.cache.probabilities)
        payload = sum(len(b) * (b.itemsize if isinstance(b, array) else 1) for b in buffers)
        self.assertLessEqual(payload, plan["workspace"])
        self.assertEqual(len(block.cache.keys) + len(block.cache.values), plan["cache"])
        weights = sum(len(layer.weights) for layer in block.projections)
        self.assertEqual(weights + 4 * 8, plan["weights"])
        with patch.object(runtime.gc, "mem_free", return_value=runtime.HEAP_RESERVE):
            with self.assertRaises(MemoryError):
                runtime.KVCache(8, 4)
            with self.assertRaises(MemoryError):
                self.block()

    def test_synthetic_benchmark_and_quantum_limit(self):
        class Clock:
            now = 0

            def ticks_us(self):
                self.now += 1
                return self.now

            @staticmethod
            def ticks_diff(end, start):
                return end - start

        runner = benchmark.Benchmark(self.kernel, Clock(), lambda: 8 * 1024 * 1024)
        worker = runner.run()
        with redirect_stdout(io.StringIO()), patch.object(
            benchmark, "reference_dot", wraps=benchmark.reference_dot
        ) as reference:
            while True:
                try:
                    runner.advance(worker)
                except StopIteration:
                    break
        self.assertTrue(all(call.args[2] <= 256 for call in reference.call_args_list))
        self.assertTrue(any(call.args[2] == 32 for call in reference.call_args_list))
        self.assertEqual(len(runner.results), 8)
        self.assertEqual(runner.results["decoder"]["tokens"], 3)
        closed = []

        def exact_budget():
            runner.clock.now += benchmark.STEP_BUDGET_US - 1
            yield "within budget"

        bounded = exact_budget()
        self.assertEqual(runner.advance(bounded), "within budget")
        bounded.close()

        def too_slow():
            try:
                runner.clock.now += benchmark.STEP_BUDGET_US
                yield "slow"
            finally:
                closed.append(True)

        with self.assertRaisesRegex(RuntimeError, "exceeded 16 ms"):
            runner.advance(too_slow())
        self.assertEqual(closed, [True])

    def test_slow_heap_scans_stay_outside_benchmark_work_slices(self):
        class Clock:
            now = 0

            def ticks_us(self):
                self.now += 1
                return self.now

            @staticmethod
            def ticks_diff(end, start):
                return end - start

        clock = Clock()
        scans = []

        def slow_heap():
            clock.now += 55000
            scans.append(True)
            return 8 * 1024 * 1024

        with patch.object(runtime.gc, "mem_free", side_effect=slow_heap):
            runner = benchmark.Benchmark(self.kernel, clock, slow_heap)
            worker = runner.run()
            with redirect_stdout(io.StringIO()):
                while True:
                    try:
                        runner.advance(worker)
                    except StopIteration:
                        break
        self.assertEqual(len(scans), 5)
        self.assertLess(runner.maximum_quantum_us, benchmark.STEP_BUDGET_US)


def native_module(thumb, address, platform="rp2", machine="Badger with RP2350",
                  arch=7):
    return load("tiny_inference_native", "native.py", {
        "sys": SimpleNamespace(platform=platform, implementation=SimpleNamespace(_mpy=arch << 10)),
        "os": SimpleNamespace(uname=lambda: SimpleNamespace(machine=machine)),
        "uctypes": SimpleNamespace(addressof=address),
        "thumb": thumb,
    })


class NativeBoundaryTests(unittest.TestCase):
    def test_platform_gate_and_no_fallback(self):
        for platform, machine, arch in (("linux", "RP2350", 7), ("rp2", "RP2040", 4),
                                        ("rp2", "RP2350", 11), ("rp2", "RP2350", 0)):
            with self.assertRaises(ImportError):
                native_module(None, None, platform, machine, arch)
        for arch in (4, 5, 6, 7, 8):
            native_module(SimpleNamespace(), None, arch=arch)

    def test_native_guards_precede_execution(self):
        calls = []
        thumb = SimpleNamespace(
            dot_i8=lambda *args: calls.append(args) or 42,
            requantize_i8=lambda *args: calls.append(args) or 12,
        )
        native = native_module(thumb, lambda buffer: 4096)
        for left, right, count, lo, ro in (
            (bytearray(4), bytearray(4), -4, 0, 0),
            (bytearray(4), bytearray(4), 3, 0, 0),
            (bytearray(2048), bytearray(2048), 1028, 0, 0),
            (bytearray(4), bytearray(4), True, 0, 0),
            (bytes(4), bytearray(4), 4, 0, 0),
            (bytearray(4), bytearray(4), 4, -4, 0),
            (bytearray(4), bytearray(4), 4, 1, 0),
            (bytearray(4), bytearray(4), 4, 4, 0),
            (bytearray(4), bytearray(4), 4, 0, False),
        ):
            with self.assertRaises((ValueError, TypeError)):
                native.dot(left, right, count, lo, ro)
        unaligned = native_module(thumb, lambda buffer: 4097)
        with self.assertRaises(ValueError):
            unaligned.dot(bytearray(4), bytearray(4), 4)
        for value, shift in ((1 << 30, 0), (-(1 << 30), 0), (True, 0),
                             (1, -1), (1, 31), (1, True)):
            with self.assertRaises(ValueError):
                native.requantize(value, shift)
        self.assertEqual(calls, [])
        self.assertEqual(native.dot(bytearray(), bytearray(), 0), 42)
        self.assertEqual(native.requantize(1, 0), 12)


class DashboardTests(unittest.TestCase):
    def setUp(self):
        guard = patch.object(gc, "mem_free", return_value=8 * 1024 * 1024,
                             create=True)
        guard.start()
        self.addCleanup(guard.stop)

    def dashboard(self, native):
        source = (APP / "__init__.py").read_text()
        screen = SimpleNamespace(
            width=160, height=120, clip=None,
            text=lambda *args: None, rectangle=lambda *args: None,
            measure_text=lambda text: (len(text) * 6, 8),
        )
        buttons = set()
        ticks = itertools.count()
        namespace = {
            "screen": screen, "font": SimpleNamespace(nope=None),
            "color": SimpleNamespace(rgb=lambda *args: args),
            "badge": SimpleNamespace(pressed=lambda button: button in buttons, ticks_delta=16),
            "BUTTON_SELECT": "SELECT", "BUTTON_BACK": "BACK", "BUTTON_MENU": "MENU",
            "run": lambda callback: None,
        }
        with patch.dict(sys.modules, {
            "os": SimpleNamespace(chdir=lambda path: None),
            "sys": SimpleNamespace(path=[], print_exception=lambda exc: None),
            "gc": SimpleNamespace(collect=lambda: None, mem_free=lambda: 8 * 1024 * 1024),
            "benchmark": benchmark, "runtime": runtime, "native": native,
            "poet": SimpleNamespace(
                Poet=lambda kernel, bits: poetry.Poet(kernel, bits, assets=str(APP / "assets")),
                wrap_lines=poetry.wrap_lines,
            ),
            "random": random.Random(389),
            "time": SimpleNamespace(ticks_us=lambda: next(ticks),
                                    ticks_diff=lambda end, start: end - start),
        }), redirect_stdout(io.StringIO()):
            exec(compile(source, str(APP / "__init__.py"), "exec"), namespace)
        return namespace, buttons

    def test_unsupported_target_does_not_run(self):
        app, buttons = self.dashboard(None)
        self.assertTrue(app["error"])
        self.assertIsNone(app["native"])
        buttons.add("SELECT")
        app["update"]()
        self.assertIsNone(app["worker"])

    def test_cancel_exit_and_visible_failure(self):
        app, buttons = self.dashboard(ReferenceKernel())
        app["update"]()
        self.assertIsNotNone(app["worker"])
        buttons.add("SELECT")
        app["update"]()
        self.assertTrue(app["paused"])
        self.assertIsNotNone(app["worker"])
        buttons.clear()
        buttons.add("BACK")
        app["update"]()
        self.assertEqual(app["poet"].number, 2)
        self.assertFalse(app["paused"])
        buttons.clear()
        buttons.add("MENU")
        app["update"]()
        self.assertEqual(app["mode"], "benchmarks")
        self.assertIsNone(app["worker"])
        buttons.clear()
        buttons.add("SELECT")
        with redirect_stdout(io.StringIO()):
            app["update"]()
        self.assertIsNotNone(app["worker"])
        buttons.clear()
        buttons.add("BACK")
        app["update"]()
        self.assertIsNone(app["worker"])
        buttons.clear()
        closed = []

        def faulty():
            try:
                raise ValueError("injected dashboard error")
                yield
            finally:
                closed.append(True)

        app["worker"] = faulty()
        with redirect_stdout(io.StringIO()):
            app["update"]()
        self.assertTrue(app["error"])
        self.assertEqual(app["status"], "FAILED: see USB log")
        self.assertEqual(closed, [True])
        app["on_exit"]()

    def test_auto_generation_hold_pause_and_responsive_layout(self):
        app, buttons = self.dashboard(ReferenceKernel())
        drawn = []
        app["screen"].text = lambda text, x, y: drawn.append((text, x, y))
        for _ in range(2500):
            drawn.clear()
            app["update"]()
            self.assertFalse(app["error"])
            for text, x, y in drawn:
                self.assertLessEqual(x + len(text) * 6, app["screen"].width)
                self.assertLessEqual(y + 8, app["screen"].height)
            if app["poet"].complete:
                break
        else:
            self.fail("first neural poem did not finish")
        number = app["poet"].number
        buttons.add("SELECT")
        app["update"]()
        buttons.clear()
        for _ in range(400):
            app["update"]()
        self.assertEqual(app["poet"].number, number)
        self.assertEqual(app["hold_ms"], 0)
        buttons.add("SELECT")
        app["update"]()
        buttons.clear()
        for _ in range(400):
            app["update"]()
        self.assertEqual(app["poet"].number, number + 1)
        for width, height in ((320, 240), (160, 120)):
            app["screen"].width, app["screen"].height = width, height
            app["poet"].text = ("Circuits circuits circuits circuits circuits circuits circuits circuits.",) * 2
            app["poet"].complete = True
            drawn.clear()
            app["draw_poem"]()
            for text, x, y in drawn:
                self.assertLessEqual(x + len(text) * 6, width)
                self.assertLessEqual(y + 8, height)
        app["on_exit"]()

    def test_poetry_frame_budget_and_explicit_overrun(self):
        app, _ = self.dashboard(ReferenceKernel())
        app["update"]()
        app["cancel"]()
        now = [0]
        app["time"] = SimpleNamespace(ticks_us=lambda: now[0],
                                      ticks_diff=lambda end, start: end - start)
        advances = []
        closed = []

        def paced(duration):
            try:
                for _ in range(20):
                    now[0] += duration
                    advances.append(duration)
                    yield
            finally:
                closed.append(True)

        app["worker"] = paced(1000)
        app["advance_poem"]()
        self.assertEqual(advances, [1000] * 4)
        app["cancel"]()
        self.assertEqual(closed, [True])
        app["worker"] = paced(16001)
        with redirect_stdout(io.StringIO()):
            app["update"]()
        self.assertTrue(app["error"])
        self.assertIsNone(app["worker"])
        self.assertEqual(closed, [True, True])

    def test_fractional_font_measurements_keep_integer_row_indices(self):
        app, _ = self.dashboard(ReferenceKernel())
        app["update"]()
        app["screen"].measure_text = lambda text: (len(text) * 6.25, 8.25)
        app["poet"].text = ("Mossy leaves breathe beside green seeds.",) * 2
        app["poet"].complete = True
        drawn = []
        app["screen"].text = lambda text, x, y: drawn.append((text, x, y))
        app["draw_poem"]()
        self.assertTrue(drawn)
        self.assertTrue(all(type(y) is int for _, _, y in drawn))
        self.assertTrue(all(x + len(text) * 6.25 <= app["screen"].width
                            for text, x, _ in drawn))
        app["on_exit"]()

    def test_heap_checks_happen_at_transitions_not_every_frame(self):
        app, buttons = self.dashboard(ReferenceKernel())
        app["update"]()
        scans = []
        app["gc"].mem_free = lambda: scans.append(True) or 8 * 1024 * 1024
        for _ in range(20):
            app["update"]()
        self.assertEqual(scans, [])
        buttons.add("MENU")
        app["update"]()
        buttons.clear()
        for _ in range(20):
            app["update"]()
        self.assertEqual(scans, [True])
        app["on_exit"]()

    def test_frame_collection_bounds_garbage_without_full_heap_scans(self):
        app, _ = self.dashboard(ReferenceKernel())
        app["update"]()
        collections = []
        app["gc"].collect = lambda: collections.append(True)
        app["gc"].mem_free = lambda: self.fail("ordinary frame scanned the heap")
        for _ in range(24):
            app["update"]()
        self.assertEqual(len(collections), 3)
        self.assertLess(app["collection_frames"], app["COLLECT_FRAMES"])
        app["on_exit"]()

    def test_poetry_collects_between_poems_and_fails_visibly_if_exhausted(self):
        app, _ = self.dashboard(ReferenceKernel())
        app["update"]()
        app["cancel"]()
        app["poet"].complete = True
        app["hold_ms"] = app["HOLD_MS"]
        free = [runtime.HEAP_RESERVE - 1]
        collections = []

        def collect():
            collections.append(True)
            free[0] = 8 * 1024 * 1024

        app["gc"] = SimpleNamespace(collect=collect, mem_free=lambda: free[0])
        app["update"]()
        self.assertEqual(collections, [True])
        self.assertFalse(app["error"])
        app["cancel"]()
        app["poet"].complete = True
        app["hold_ms"] = app["HOLD_MS"]
        app["gc"] = SimpleNamespace(collect=lambda: None,
                                    mem_free=lambda: runtime.HEAP_RESERVE - 1)
        with redirect_stdout(io.StringIO()):
            app["update"]()
        self.assertTrue(app["error"])
        self.assertIsNone(app["worker"])


def compiled_kernels(path):
    """Read only the constant-free module/inline-ASM subset of MPY v6.3."""
    data = path.read_bytes()
    stream = io.BytesIO(data)
    if stream.read(4) != b"M\x06\x1b\x1f":
        raise AssertionError("expected mpy-cross -march=armv7em MPY v6.3")

    def uint():
        value = 0
        while True:
            byte = stream.read(1)
            if not byte:
                raise AssertionError("truncated MPY integer")
            value = (value << 7) | (byte[0] & 127)
            if byte[0] < 128:
                return value

    strings, objects = uint(), uint()
    if objects:
        raise AssertionError("kernel module unexpectedly contains constant objects")
    for _ in range(strings):
        descriptor = uint()
        if not descriptor & 1:
            stream.read((descriptor >> 1) + 1)
    kernels = []

    def raw_code():
        descriptor = uint()
        kind = descriptor & 3
        code = stream.read(descriptor >> 3)
        if kind == 3:
            if uint() != 0:
                raise AssertionError("unexpected inline-ASM scope flags")
            arguments, signature = uint(), uint()
            kernels.append((code, arguments, signature))
        elif kind != 0:
            raise AssertionError("unexpected native code kind")
        if descriptor & 4:
            for _ in range(uint()):
                raw_code()

    raw_code()
    if stream.tell() != len(data) or len(kernels) != 2:
        raise AssertionError("expected exactly two inline-ASM kernels")
    if [arguments for _, arguments, _ in kernels] != [3, 2]:
        raise AssertionError("kernel argument signatures changed")
    if any(signature != 2 for _, _, signature in kernels):
        raise AssertionError("kernels must declare signed integer return values")
    return [code for code, _, _ in kernels]


class NativeExecutionTests(RuntimeTests):
    """Repeat runtime tests using actual cross-compiled instructions on M33."""

    @classmethod
    def setUpClass(cls):
        compiler = os.environ.get("MPY_CROSS") or shutil.which("mpy-cross")
        try:
            import unicorn
            from unicorn import arm_const
        except ImportError:
            raise unittest.SkipTest("optional native tests need unicorn and mpy-cross")
        if not compiler:
            raise unittest.SkipTest("set MPY_CROSS to run optional native tests")
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "thumb.mpy"
            subprocess.run([compiler, "-march=armv7em", "-o", str(destination),
                            str(APP / "thumb.py")], check=True, capture_output=True)
            cls.code = compiled_kernels(destination)
        cls.unicorn = unicorn
        cls.arm = arm_const

    def setUp(self):
        super().setUp()
        uc, arm = self.unicorn, self.arm
        self.cpu = uc.Uc(uc.UC_ARCH_ARM, uc.UC_MODE_THUMB | uc.UC_MODE_MCLASS)
        self.cpu.ctl_set_cpu_model(arm.UC_CPU_ARM_CORTEX_M33)
        self.cpu.mem_map(0x10000, 0x10000)
        self.cpu.mem_map(0x20000000, 0x400000)
        self.cpu.mem_map(0x21000000, 0x10000)
        self.code_addresses = {code: 0x10000 + index * 0x1000
                               for index, code in enumerate(self.code)}
        for code, address in self.code_addresses.items():
            self.cpu.mem_write(address, code)
        self.buffers = {}
        self.next_address = 0x20000020
        self.kernel = native_module(SimpleNamespace(
            dot_i8=lambda *args: self.execute(self.code[0], args),
            requantize_i8=lambda *args: self.execute(self.code[1], args),
        ), self.address)

    def address(self, buffer):
        key = id(buffer)
        if key not in self.buffers:
            self.buffers[key] = (self.next_address, buffer)
            self.next_address += (len(buffer) + 63) & ~3
        address, _ = self.buffers[key]
        if buffer:
            self.cpu.mem_write(address, bytes(buffer))
        return address

    def execute(self, code, arguments):
        cpu, arm = self.cpu, self.arm
        preserved = [getattr(arm, "UC_ARM_REG_R%d" % index) for index in range(4, 12)]
        for index, register in enumerate(preserved):
            cpu.reg_write(register, 0xA5000000 + index)
        cpu.reg_write(arm.UC_ARM_REG_SP, 0x2100FFF0)
        cpu.reg_write(arm.UC_ARM_REG_LR, 0x1F001)
        for index, value in enumerate(arguments):
            cpu.reg_write(getattr(arm, "UC_ARM_REG_R%d" % index), value & 0xFFFFFFFF)
        cpu.emu_start(self.code_addresses[code] | 1, 0x1F000,
                      timeout=1000000, count=100000)
        self.assertEqual(cpu.reg_read(arm.UC_ARM_REG_PC), 0x1F000)
        self.assertEqual(cpu.reg_read(arm.UC_ARM_REG_SP), 0x2100FFF0)
        for index, register in enumerate(preserved):
            self.assertEqual(cpu.reg_read(register), 0xA5000000 + index)
        value = cpu.reg_read(arm.UC_ARM_REG_R0)
        return value if value < 2**31 else value - 2**32

    def test_emitted_dot_extrema_random_offsets_and_zero(self):
        rng = random.Random(879)
        for count in (0, 4, 8, 12, 64, 256, 512, 1024):
            for a, b in ((-128, -128), (-128, 127), (127, 127), (None, None)):
                left = encode([rng.randrange(-128, 128) if a is None else a
                               for _ in range(count + 12)])
                right = encode([rng.randrange(-128, 128) if b is None else b
                                for _ in range(count + 12)])
                actual = self.kernel.dot(left, right, count, 4, 8)
                self.assertEqual(actual, ReferenceKernel.dot(left, right, count, 4, 8))

    def test_emitted_requantization_all_shifts_and_ties(self):
        rng = random.Random(23)
        for shift in range(31):
            values = [0, 1, -1, 127, -128, 2**30 - 1, -2**30 + 1]
            if shift:
                half = 1 << (shift - 1)
                values += [half - 1, half, half + 1, -half + 1, -half, -half - 1]
            values += [rng.randrange(-2**30 + 1, 2**30) for _ in range(40)]
            for value in values:
                self.assertEqual(self.kernel.requantize(value, shift),
                                 ReferenceKernel.requantize(value, shift), (value, shift))


if __name__ == "__main__":
    unittest.main()
