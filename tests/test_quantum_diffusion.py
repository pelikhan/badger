import importlib.util
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from test_quantum_firefly import load_firefly


APP = Path(__file__).resolve().parents[1] / "badge/apps/quantum_diffusion"
SPEC = importlib.util.spec_from_file_location("quantum_field", APP / "field.py")
physics = importlib.util.module_from_spec(SPEC)
with patch.object(sys, "path", [str(APP)] + sys.path):
    SPEC.loader.exec_module(physics)
PALETTE_SPEC = importlib.util.spec_from_file_location("quantum_palette", APP / "palette.py")
palettes = importlib.util.module_from_spec(PALETTE_SPEC)
PALETTE_SPEC.loader.exec_module(palettes)


class FakeTimer:
    PERIODIC = 1

    def __init__(self, number):
        self.active = False

    def init(self, **kwargs):
        self.settings = kwargs
        self.active = True

    def deinit(self):
        self.active = False


def load_sensors(clock):
    spec = importlib.util.spec_from_file_location("quantum_sensors", APP / "sensors.py")
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {
        "field": physics,
        "machine": SimpleNamespace(Timer=FakeTimer),
        "time": SimpleNamespace(
            ticks_ms=lambda: clock[0],
            ticks_diff=lambda end, start: end - start,
        ),
    }):
        spec.loader.exec_module(module)
    return module


class WaveTests(unittest.TestCase):
    def test_rest_is_stationary(self):
        field = physics.WaveField()
        for _ in range(100):
            field.step()
        self.assertTrue(all(value == 0 for value in field.current))

    def test_impulse_propagates_symmetrically(self):
        field = physics.WaveField(41, 25)
        field.impulse(20, 12)
        for _ in range(12):
            field.step()
        self.assertNotEqual(field.current[12 * 41 + 27], 0)
        for offset in range(1, 9):
            self.assertAlmostEqual(
                field.current[12 * 41 + 20 - offset],
                field.current[12 * 41 + 20 + offset],
                places=6,
            )
        for _ in range(24):
            field.step()
        self.assertTrue(any(value < 0 for value in field.current))

    def test_opposite_impulses_cancel(self):
        field = physics.WaveField()
        field.impulse(15, 12, 4 * physics.SCALE)
        field.impulse(15, 12, -4 * physics.SCALE)
        for _ in range(20):
            field.step()
        self.assertLess(max(abs(v) for v in field.current), 0.00001)

    def test_long_run_is_bounded_and_decays(self):
        field = physics.WaveField()
        buffers = {id(field.current), id(field.previous), id(field.next)}
        for step in range(1500):
            if step < 300 and step % 3 == 0:
                field.impulse(3 + step % 30, 3 + step % 17, 8 * physics.SCALE)
            field.step()
            self.assertTrue(all(isinstance(v, int) and abs(v) <= physics.LIMIT for v in field.current))
        self.assertLessEqual(max(abs(v) for v in field.current), 2)
        self.assertEqual(buffers, {id(field.current), id(field.previous), id(field.next)})
        self.assertTrue(all(v == 0 for v in field.current[:field.width]))
        self.assertTrue(all(v == 0 for v in field.current[-field.width:]))
        self.assertTrue(all(field.current[y * field.width] == 0 for y in range(field.height)))

    def test_edges_and_clear(self):
        field = physics.WaveField()
        field.impulse(-999, 999, 999 * physics.SCALE)
        self.assertLessEqual(max(field.current), 8 * physics.SCALE)
        for _ in range(30):
            field.step()
        field.clear()
        for buffer in (field.current, field.previous, field.next):
            self.assertTrue(all(v == 0 for v in buffer))

    def test_signed_stencil_and_all_boundaries(self):
        positive = physics.WaveField()
        negative = physics.WaveField()
        positive.impulse(13, 8)
        negative.impulse(13, 8, -physics.PULSE)
        for _ in range(150):
            positive.step()
            negative.step()
            self.assertEqual(list(positive.current), [-v for v in negative.current])
        for buffer in (positive.current, positive.previous, positive.next):
            self.assertTrue(all(buffer[y * positive.width + positive.width - 1] == 0
                                for y in range(positive.height)))

    def test_small_grid_rejected(self):
        with self.assertRaises(ValueError):
            physics.WaveField(7, 16)

    def test_signed_stencil_matches_measured_arm_fingerprint(self):
        field = physics.WaveField(26, 16)
        for i in range(120):
            if i % 7 == 0:
                field.impulse(3 + i % 19, 3 + i % 9,
                              (-1 if i % 2 else 1) * 8 * physics.SCALE)
            field.step()
        self.assertEqual(
            (sum(field.current), sum(abs(v) for v in field.current),
             min(field.current), max(field.current),
             sum((i + 1) * v for i, v in enumerate(field.current))),
            (221877, 996915, -12288, 10840, 66361619),
        )


class MotionTests(unittest.TestCase):
    def rest(self, detector, count=40, axis=(0, 0, 16384)):
        return [detector.sample(*axis, 25) for _ in range(count)]

    def test_gravity_at_any_orientation_is_not_an_impulse(self):
        for axis in ((0, 0, 16384), (16384, 0, 0), (0, -16384, 0)):
            detector = physics.MotionImpulse()
            self.assertFalse(any(self.rest(detector, 200, axis)))
            self.assertTrue(detector.armed)

    def test_impulse_and_cooldown(self):
        detector = physics.MotionImpulse()
        self.rest(detector)
        self.assertGreater(detector.sample(16384, 0, 16384, 25), 0)
        self.assertEqual(detector.sample(-16384, 0, 16384, 25), 0)
        self.assertFalse(any(self.rest(detector, 8)))
        self.assertFalse(detector.armed)
        self.rest(detector, 40)
        self.assertGreater(detector.sample(-16384, 0, 16384, 25), 0)

    def test_held_acceleration_does_not_repeat(self):
        detector = physics.MotionImpulse()
        self.rest(detector)
        results = self.rest(detector, 200, (16384, 0, 16384))
        self.assertEqual(sum(value > 0 for value in results), 1)

    def test_slow_rotation_and_sensor_noise_are_ignored(self):
        detector = physics.MotionImpulse()
        self.rest(detector)
        for step in range(400):
            angle = step * math.pi / 800
            self.assertEqual(
                detector.sample(
                    int(math.sin(angle) * 16384) + step % 101,
                    step % 61,
                    int(math.cos(angle) * 16384),
                    25,
                ),
                0,
            )

    def test_sustained_shaking_repeats_at_bounded_rate_without_quiet(self):
        detector = physics.MotionImpulse()
        self.rest(detector)
        results = [
            detector.sample(22000 if i % 2 else -22000, 0, 16384, 25)
            for i in range(200)
        ]
        self.assertGreater(sum(value > 0 for value in results), 10)
        self.assertLessEqual(sum(value > 0 for value in results), 17)

    def test_latched_firmware_events_survive_short_taps_between_samples(self):
        for event in (1, 2, 16):
            detector = physics.MotionImpulse()
            self.rest(detector)
            self.assertGreater(detector.sample(0, 0, 16384, 10, (event,)), 0)
            self.assertEqual(detector.sample(0, 0, 16384, 10, (event,)), 0)
        for event in (4, 8):
            detector = physics.MotionImpulse()
            self.rest(detector)
            self.assertEqual(detector.sample(0, 0, 16384, 10, (event,)), 0)

    def test_latched_event_in_same_millisecond_is_not_lost_after_warmup(self):
        detector = physics.MotionImpulse()
        self.rest(detector)
        self.assertGreater(detector.sample(0, 0, 16384, 0, (1,)), 0)
        self.assertEqual(detector.sample(0, 0, 16384, 0, (1,)), 0)

    def test_gyro_twist_fires_but_bias_and_steady_rotation_do_not_repeat(self):
        detector = physics.MotionImpulse()
        self.rest(detector)
        self.assertEqual(detector.sample(0, 0, 16384, 10, gyro=(240, -440, 130)), 0)
        self.assertGreater(detector.sample(0, 0, 16384, 10, gyro=(0, 9000, 0)), 0)
        self.assertEqual(detector.source, "TWIST")
        self.assertEqual(detector.dx, physics.SCALE)
        for _ in range(100):
            self.assertEqual(detector.sample(0, 0, 16384, 10, gyro=(0, 9000, 0)), 0)

    def test_initial_zero_cache_does_not_calibrate_gravity(self):
        detector = physics.MotionImpulse()
        for _ in range(100):
            self.assertEqual(detector.sample(0, 0, 0, 10), 0)
        self.assertIsNone(detector.gravity)
        self.assertFalse(any(self.rest(detector)))

    def test_startup_is_quiet_and_zero_time_does_not_arm(self):
        detector = physics.MotionImpulse()
        for _ in range(100):
            self.assertEqual(detector.sample(0, 0, 16384, 0), 0)
        self.assertFalse(detector.armed)
        self.assertEqual(detector.sample(32768, 0, 16384, 25), 0)


class PaletteTests(unittest.TestCase):
    def test_exact_official_anchors_and_cycle_wrap(self):
        palette = palettes.BrandPalette()
        for index, rgb in enumerate(palettes.BRAND_COLORS):
            if index:
                palette.update(palettes.TRANSITION_MS)
            self.assertEqual(palette.rgb, rgb)
            self.assertEqual(palette.colors[11], palettes.rgb565(*rgb))
        palette.update(palettes.TRANSITION_MS)
        self.assertEqual(palette.phase_ms, 0)
        self.assertEqual(palette.rgb, palettes.BRAND_COLORS[0])

    def test_every_transition_is_smooth_including_wrap(self):
        palette = palettes.BrandPalette()
        previous = palette.rgb
        for _ in range(palettes.CYCLE_MS // palettes.UPDATE_MS):
            palette.update(palettes.UPDATE_MS)
            self.assertLessEqual(max(abs(a - b) for a, b in zip(previous, palette.rgb)), 4)
            previous = palette.rgb
        self.assertEqual(palette.rgb, palettes.BRAND_COLORS[0])

    def test_fixed_size_palette_preserves_amplitude_contrast(self):
        palette = palettes.BrandPalette()
        buffer = palette.colors
        for _ in range(palettes.CYCLE_MS // palettes.UPDATE_MS):
            palette.update(palettes.UPDATE_MS)
            self.assertIs(palette.colors, buffer)
            self.assertEqual(len(buffer), 17)
            components = [
                (value >> 11, (value >> 5) & 63, value & 31)
                for value in buffer[:16]
            ]
            for first, second in zip(components, components[1:]):
                self.assertTrue(all(b >= a for a, b in zip(first, second)))
            self.assertNotEqual(buffer[1], buffer[11])
            self.assertNotEqual(buffer[11], buffer[15])
            self.assertLess(buffer[16], buffer[11])
            dim = sum(channel * weight for channel, weight in
                      zip(components[1], (54 * 8, 183 * 4, 19 * 8)))
            peak = sum(channel * weight for channel, weight in
                       zip(components[15], (54 * 8, 183 * 4, 19 * 8)))
            self.assertGreaterEqual(peak - dim, 20000)
            self.assertGreaterEqual(peak, 4 * dim)

    def test_updates_are_bounded_to_twenty_per_second(self):
        palette = palettes.BrandPalette()
        self.assertFalse(palette.update(0))
        self.assertEqual(sum(palette.update(1) for _ in range(1000)), 20)
        self.assertEqual(palette.phase_ms, 1000)

    def test_elapsed_time_and_long_stalls_do_not_change_cycle_rate(self):
        slow = palettes.BrandPalette()
        fast = palettes.BrandPalette()
        for _ in range(40):
            slow.update(25)
        for _ in range(100):
            fast.update(10)
        self.assertEqual(list(slow.colors), list(fast.colors))
        fast.update(palettes.CYCLE_MS * 123)
        self.assertEqual(list(slow.colors), list(fast.colors))
        self.assertEqual(fast.phase_ms, slow.phase_ms)

    def test_absolute_phase_can_move_backwards_and_wrap_without_reallocating(self):
        palette = palettes.BrandPalette()
        buffer = palette.colors
        for phase in (8000, 7900, -50, palettes.CYCLE_MS + 100):
            expected = palettes.BrandPalette()
            expected.update(phase % palettes.CYCLE_MS)
            palette.set_phase(phase)
            self.assertEqual(palette.phase_ms, phase % palettes.CYCLE_MS)
            self.assertEqual(list(palette.colors), list(expected.colors))
            self.assertIs(palette.colors, buffer)


class RuntimeTests(unittest.TestCase):
    def app(self, framebuffer_stride=640, width=320, height=240):
        pressed = set()
        touches = set()
        badge = SimpleNamespace(
            ticks_delta=0,
            mode=lambda mode: None,
            imu=lambda: (0, 0, 16384, 0, 0, 0),
            imu_event=lambda: (),
            _poll_imu=lambda: None,
            light_level=lambda: 64,
            upside_down=lambda: False,
            pressed=lambda key: key in pressed,
            touched=lambda key: key in touches,
        )
        screen = SimpleNamespace(
            width=width, height=height, clip=(0, 0, width, height),
            stride=framebuffer_stride, raw=bytearray(framebuffer_stride * height),
            rectangle=lambda *args: None,
            line=lambda *args: None,
            text=lambda *args: None,
            measure_text=lambda text: (len(text) * 6, 8),
        )
        context = {
            "badge": badge, "screen": screen,
            "font": SimpleNamespace(nope=object()),
            "color": SimpleNamespace(rgb=lambda *args: args),
            "HIRES": 1, "VSYNC": 2, "run": lambda callback: None,
        }
        for action in ("UP", "DOWN", "LEFT", "RIGHT", "SELECT", "MENU", "BACK", "HOME"):
            context["BUTTON_" + action] = action
        clock = [0]
        sensors = load_sensors(clock)
        firefly = load_firefly(clock)
        with patch("os.chdir"), patch.dict("sys.modules", {
            "field": physics, "sensors": sensors, "palette": palettes, "firefly": firefly,
        }), patch("sys.path", []):
            exec(compile((APP / "__init__.py").read_text(), "__init__.py", "exec"), context)
        context["motion"].start()
        context["fireflies"].start()
        context["clock"] = clock
        context["touches"] = touches
        return context, badge, pressed

    def test_fixed_step_is_frame_rate_independent(self):
        first, badge, _ = self.app()
        badge.ticks_delta = 25
        for _ in range(40):
            first["clock"][0] += 25
            first["update"]()
        second, badge, _ = self.app()
        badge.ticks_delta = 10
        for _ in range(100):
            second["clock"][0] += 10
            second["update"]()
        self.assertEqual(list(first["field"].current), list(second["field"].current))
        self.assertEqual(list(first["PALETTE"]), list(second["PALETTE"]))
        self.assertEqual(first["brand_palette"].phase_ms, 1000)

    def test_firefly_rhythm_adds_center_waves_without_resetting_color(self):
        app, badge, _ = self.app()
        app["fireflies"].phase = 0
        app["fireflies"].since_flash = 0
        badge.ticks_delta = 2000
        app["clock"][0] = 2000
        with patch.object(app["field"], "impulse", wraps=app["field"].impulse) as pulse:
            app["update"]()
        pulse.assert_called_once_with(32, 24, physics.PULSE)
        self.assertEqual(app["source"], "FIREFLY")
        self.assertEqual(app["brand_palette"].phase_ms, 2000)

    def test_palette_uses_shared_color_phase_instead_of_local_frame_delta(self):
        app, badge, _ = self.app()
        badge.ticks_delta = 25
        app["fireflies"].color_phase_ms = 8000
        app["update"]()
        self.assertEqual(app["brand_palette"].phase_ms, 8000)
        self.assertEqual(app["brand_palette"].rgb, palettes.BRAND_COLORS[2])

    def test_received_color_clock_drives_the_actual_palette_in_both_directions(self):
        app, badge, _ = self.app()
        service = app["fireflies"]
        packet = service.broadcast.__globals__["advertisement"](b"\x00" * 8, 1, service.phase, 8000)
        service.receive(5, (0, b"abcdef", 0, -40, packet))
        badge.ticks_delta = 25
        for _ in range(90):
            app["clock"][0] += 25
            app["update"]()
        self.assertEqual(app["brand_palette"].phase_ms, service.color_clock_ms)
        expected = palettes.BrandPalette()
        expected.set_phase(service.color_clock_ms)
        self.assertEqual(list(app["PALETTE"]), list(expected.colors))
        before = app["brand_palette"].phase_ms
        packet = service.broadcast.__globals__["advertisement"](b"\x00" * 8, 2, service.phase, 1000)
        service.receive(5, (0, b"abcdef", 0, -40, packet))
        app["update"]()
        self.assertEqual(app["brand_palette"].phase_ms, before - 100)

    def test_stalled_frame_has_bounded_catchup(self):
        app, badge, _ = self.app()
        badge.ticks_delta = 60000
        with patch.object(app["field"], "step", wraps=app["field"].step) as step:
            app["update"]()
            self.assertEqual(step.call_count, 4)

    def test_physical_buttons_have_no_effect(self):
        app, badge, pressed = self.app()
        before = list(app["field"].current)
        pressed.update(("LEFT", "RIGHT", "UP", "DOWN", "SELECT", "MENU", "BACK", "HOME"))
        with patch.object(badge, "pressed") as buttons:
            for _ in range(20):
                app["update"]()
        buttons.assert_not_called()
        self.assertEqual(list(app["field"].current), before)
        self.assertEqual(app["pulses"], 1)
        self.assertNotIn("auto", app)
        self.assertNotIn("cursor_x", app)

    def test_capacitive_actions_pulse_at_logical_grid_positions(self):
        points = {
            "UP": (32, 12), "DOWN": (32, 36),
            "LEFT": (16, 24), "RIGHT": (48, 24),
            "SELECT": (32, 24), "MENU": (48, 12), "BACK": (48, 36),
        }
        for inverted in (False, True):
            for action, point in points.items():
                with self.subTest(action=action, inverted=inverted):
                    app, badge, _ = self.app()
                    badge.upside_down = lambda: inverted
                    app["touches"].add(action)
                    with patch.object(app["field"], "impulse", wraps=app["field"].impulse) as pulse:
                        app["update"]()
                    pulse.assert_called_once_with(*point, physics.PULSE)
                    self.assertEqual(app["source"], "TOUCH")
                    self.assertEqual(app["pulses"], 2)

    def test_held_touch_fires_once_and_rearms_after_release(self):
        app, _, _ = self.app()
        app["touches"].add("SELECT")
        for _ in range(30):
            app["update"]()
        self.assertEqual(app["pulses"], 2)
        app["touches"].clear()
        app["update"]()
        app["touches"].add("SELECT")
        app["update"]()
        self.assertEqual(app["pulses"], 3)

    def test_simultaneous_touches_have_independent_edges_and_keep_motion(self):
        app, _, _ = self.app()
        app["touches"].update(("LEFT", "RIGHT"))
        app["motion"].pending = (1, physics.PULSE, 0, 0, "SHAKE")
        app["update"]()
        self.assertEqual(app["pulses"], 4)
        self.assertIsNone(app["motion"].take())
        app["touches"].remove("LEFT")
        app["update"]()
        self.assertEqual(app["pulses"], 4)
        app["touches"].add("LEFT")
        app["update"]()
        self.assertEqual(app["pulses"], 5)

    def test_capacitive_home_remains_launcher_owned(self):
        app, badge, _ = self.app()
        app["touches"].add("HOME")
        with patch.object(badge, "touched", wraps=badge.touched) as touched:
            app["update"]()
        self.assertNotIn(call("HOME"), touched.call_args_list)
        self.assertEqual(app["pulses"], 1)
        self.assertEqual(app["touch_mask"], 0)

    def test_grid_fills_display_with_source_credit(self):
        app, _, _ = self.app()
        screen = app["screen"]
        screen.text = Mock()
        screen.line = Mock()
        screen.rectangle = Mock()
        app["update"]()
        screen.text.assert_called_once_with("pelikhan/badger", 115, 228)
        screen.line.assert_not_called()
        self.assertEqual((app["field"].width, app["field"].height), (64, 48))
        self.assertEqual((app["CELL"], app["LEFT"], app["TOP"]), (5, 0, 0))
        screen.rectangle.assert_called_once_with(111, 226, 98, 12)
        self.assertTrue(any(screen.raw))

    def test_source_credit_fits_both_orientations_and_survives_redraws(self):
        for width, height in ((320, 240), (240, 320)):
            with self.subTest(size=(width, height)):
                app, _, _ = self.app(
                    framebuffer_stride=width * 2, width=width, height=height)
                screen = app["screen"]
                draws = []
                screen.text = lambda *args: draws.append((screen.pen, args))
                screen.rectangle = Mock()
                for _ in range(2):
                    app["update"]()
                self.assertEqual(draws, [
                    ((128, 128, 128), ("pelikhan/badger", (width - 90) // 2, height - 12))
                ] * 2)
                for draw in screen.rectangle.call_args_list:
                    x, y, w, h = draw.args
                    self.assertGreaterEqual(x, 0)
                    self.assertGreaterEqual(y, 0)
                    self.assertLessEqual(x + w, width)
                    self.assertLessEqual(y + h, height)

    def test_unsupported_framebuffer_layout_fails_explicitly(self):
        with self.assertRaisesRegex(RuntimeError, "RGB565 framebuffer"):
            self.app(framebuffer_stride=960)

    def test_native_raster_matches_signed_rectangle_reference(self):
        for dimensions in ((45, 34), (53, 40), (64, 48), (80, 60), (106, 80), (160, 120)):
            with self.subTest(grid=dimensions):
                self.assert_raster_matches_reference(*dimensions)

    def test_native_raster_handles_high_bit_brand_colors_and_transitions(self):
        for phase in range(0, palettes.CYCLE_MS, palettes.TRANSITION_MS // 2):
            with self.subTest(phase=phase):
                self.assert_raster_matches_reference(64, 48, phase)

    def assert_raster_matches_reference(self, width, height, phase=0):
        app, _, _ = self.app()
        screen = app["screen"]
        field = physics.WaveField(width, height)
        app["field"] = field
        app["CELL"] = min(screen.width // width, screen.height // height)
        app["LEFT"] = (screen.width - width * app["CELL"]) // 2
        app["TOP"] = (screen.height - height * app["CELL"]) // 2
        cell = app["CELL"]
        for i in range(len(field.current)):
            field.current[i] = (i * 31) % (2 * physics.LIMIT + 1) - physics.LIMIT
        buffer = screen.raw
        app["brand_palette"].update(phase)
        for gain in (4, 7):
            app["motion"].gain = gain
            app["draw"]()
            expected = bytearray(len(buffer))
            pixels = memoryview(expected).cast("H")

            def rectangle(x, y, width, height, pen):
                for dy in range(height):
                    for dx in range(width):
                        pixels[(y + dy) * screen.width + x + dx] = pen

            for y in range(1, field.height - 1):
                for x in range(1, field.width - 1):
                    value = field.current[y * field.width + x]
                    level = min(15, abs(value) * gain >> 10)
                    if value < 0 and level:
                        level = max(1, level - 3)
                    px = app["LEFT"] + x * cell
                    py = app["TOP"] + y * cell
                    if level:
                        rectangle(px, py, cell - 1, cell - 1, app["PALETTE"][level])
                        if level > 7 and (x + y) % 3 == 0 and cell >= 5:
                            rectangle(px + 2, py + 2, 2, 2, 0)
                    elif x % 4 == 0 and y % 4 == 0 and cell >= 5:
                        rectangle(px + 3, py + 3, 1, 1, app["PALETTE"][16])
            self.assertEqual(buffer, expected)
        self.assertIs(screen.raw, buffer)

    def test_motion_uses_same_pulse_path_and_orientation(self):
        for inverted in (False, True):
            app, badge, _ = self.app()
            badge.ticks_delta = 25
            badge.upside_down = lambda: inverted
            for _ in range(40):
                app["clock"][0] += 25
                app["motion"].poll(None)
            badge.imu = lambda: (16384, 0, 16384, 0, 0, 0)
            app["clock"][0] += 25
            app["motion"].poll(None)
            with patch.object(app["field"], "impulse", wraps=app["field"].impulse) as impulse:
                app["update"]()
                self.assertEqual(app["source"], "SHAKE")
                self.assertEqual(app["pulses"], 2)
                self.assertEqual(
                    impulse.call_args.args[0],
                    app["field"].width // 2 + (-6 if inverted else 6),
                )

    def test_render_does_not_poll_sensors(self):
        app, badge, _ = self.app()
        with patch.object(badge, "imu") as imu, patch.object(badge, "_poll_imu") as poll, \
                patch.object(badge, "light_level") as light:
            for _ in range(20):
                app["update"]()
            imu.assert_not_called()
            poll.assert_not_called()
            light.assert_not_called()

    def test_back_does_not_discard_queued_sensor_impulse(self):
        app, _, pressed = self.app()
        app["motion"].pending = (1, physics.PULSE, 0, 0, "SHAKE")
        pressed.add("BACK")
        app["update"]()
        self.assertGreater(max(app["field"].current), 0)
        self.assertEqual(app["source"], "SHAKE")
        self.assertIsNone(app["motion"].take())

    def test_timer_services_motion_without_rendering_and_stops_on_exit(self):
        app, badge, _ = self.app()
        service = app["motion"]
        self.assertEqual(service.timer.settings["period"], 10)
        self.assertFalse(service.timer.settings["hard"])
        for _ in range(40):
            app["clock"][0] += 10
            service.poll(None)
        badge.imu = lambda: (0, 0, 16384, 0, 9000, 0)
        app["clock"][0] += 10
        service.poll(None)
        self.assertEqual(app["source"], "BOOT")
        app["update"]()
        self.assertEqual(app["source"], "TWIST")
        self.assertIsNone(service.take())
        app["on_exit"]()
        app["on_exit"]()
        self.assertFalse(service.timer.active)
        self.assertFalse(service.running)
        self.assertEqual(badge._poll_imu, service.poll_imu)
        self.assertFalse(app["fireflies"].running)

    def test_timer_owns_imu_poll_without_consuming_button_edges(self):
        app, badge, pressed = self.app()
        service = app["motion"]
        pressed.add("SELECT")
        with patch.object(service, "poll_imu") as poll:
            for _ in range(10):
                app["clock"][0] += 10
                badge._poll_imu()
                service.poll(None)
            self.assertEqual(poll.call_count, 10)
        app["update"]()
        self.assertEqual(app["source"], "BOOT")
        self.assertEqual(pressed, {"SELECT"})

    def test_reopening_uses_a_fresh_timer_and_calibration(self):
        first, _, _ = self.app()
        first["brand_palette"].update(12000)
        first["on_exit"]()
        second, _, _ = self.app()
        self.assertFalse(first["motion"].timer.active)
        self.assertTrue(second["motion"].timer.active)
        self.assertIsNone(second["motion"].motion.gravity)
        self.assertEqual(second["brand_palette"].phase_ms, 0)
        self.assertEqual(second["brand_palette"].rgb, palettes.BRAND_COLORS[0])
        self.assertEqual(second["touch_mask"], 0)

    def test_light_is_filtered_and_bounded_outside_rendering(self):
        app, badge, _ = self.app()
        service = app["motion"]
        for raw, expected in ((0, 4), (65535, 7)):
            badge.light_level = lambda: raw
            for _ in range(100):
                app["clock"][0] += 10
                service.poll(None)
            self.assertEqual(service.gain, expected)
        prior = service.light
        badge.light_level = lambda: 0
        for _ in range(10):
            app["clock"][0] += 10
            service.poll(None)
        self.assertGreater(service.light, 0)
        self.assertLess(service.light, prior)

    def test_sensor_error_is_reported_and_timer_stopped(self):
        app, badge, _ = self.app()
        service = app["motion"]
        with patch.object(service, "poll_imu", side_effect=OSError("I2C unavailable")), patch("builtins.print") as log:
            app["clock"][0] += 10
            service.poll(None)
        log.assert_called_once()
        self.assertFalse(service.running)
        with self.assertRaisesRegex(RuntimeError, "I2C unavailable"):
            app["update"]()


if __name__ == "__main__":
    unittest.main()
