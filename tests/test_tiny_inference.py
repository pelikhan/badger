"""Host references and optional execution of the actual mpy-cross Thumb output."""

from array import array
import importlib.util
import io
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
        a = decode(left[left_offset:left_offset + count])
        b = decode(right[right_offset:right_offset + count])
        return sum(x * y for x, y in zip(a, b))

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
        self.heap = patch.object(runtime.gc, "mem_free", return_value=8 * 1024 * 1024,
                                 create=True)
        self.heap.start()
        self.addCleanup(self.heap.stop)
        self.kernel = ReferenceKernel()

    def block(self):
        return benchmark.synthetic_block(self.kernel, width=8, hidden=16, context=4)

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
        with redirect_stdout(io.StringIO()):
            while True:
                try:
                    runner.advance(worker)
                except StopIteration:
                    break
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
    def dashboard(self, native):
        source = (APP / "__init__.py").read_text()
        screen = SimpleNamespace(
            width=160, height=120, clip=None,
            text=lambda *args: None, rectangle=lambda *args: None,
        )
        buttons = set()
        ticks = iter(range(10000))
        namespace = {
            "screen": screen, "font": SimpleNamespace(nope=None),
            "color": SimpleNamespace(rgb=lambda *args: args),
            "badge": SimpleNamespace(pressed=lambda button: button in buttons),
            "BUTTON_SELECT": "SELECT", "BUTTON_BACK": "BACK",
            "run": lambda callback: None,
        }
        with patch.dict(sys.modules, {
            "os": SimpleNamespace(chdir=lambda path: None),
            "sys": SimpleNamespace(path=[], print_exception=lambda exc: None),
            "gc": SimpleNamespace(collect=lambda: None, mem_free=lambda: 8 * 1024 * 1024),
            "benchmark": benchmark, "runtime": runtime, "native": native,
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
        buttons.add("SELECT")
        with redirect_stdout(io.StringIO()):
            app["update"]()
        self.assertIsNotNone(app["worker"])
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
