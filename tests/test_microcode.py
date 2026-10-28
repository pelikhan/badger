import ast
import errno
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1] / "badge/apps/microcode"
sys.path.insert(0, str(APP))

from editor import App
from engine import Engine, MAX_EVENTS
from icons import GLYPHS, draw, matrix
from model import (EXAMPLES, HAPPY, HEART, MAX_RULES, PICTURES, SMALL_HEART,
                   blank, clone, example, new_rule, validate)
from sensors import Sensors, SENSORS
from storage import MAX_BYTES, Store
from view import View


class Badge:
    def __init__(self):
        self.down = set()
        self.up = set()
        self.touch = set()
        self.reading = (0, 0, 16384, 0, 0, 0)
        self.battery = 75
        self.charging = False
        self.flipped = False
        self.leds = []
        self.ticks_delta = 16

    def pressed(self, name):
        return name in self.down

    def released(self, name):
        return name in self.up

    def touched(self, name):
        return name in self.touch

    def imu(self):
        return self.reading

    def battery_level(self):
        return self.battery

    def is_charging(self):
        return self.charging

    def upside_down(self):
        return self.flipped

    def caselights(self, brightness):
        self.leds.append(brightness)

    def mode(self, flags):
        self.flags = flags


class ADC:
    def __init__(self, value=1000):
        self.value = value
        self.error = None

    def read_u16(self):
        if self.error:
            raise self.error
        return self.value


class Screen:
    width, height = 320, 240

    def __init__(self):
        self.calls = []
        self.pen = None

    def rectangle(self, x, y, w, h):
        self.calls.append(("rect", x, y, w, h))
        if w < 0 or h < 0:
            raise ValueError("Negative rectangle")

    def line(self, *args):
        self.calls.append(("line",) + args)

    def measure_text(self, text):
        return (len(text) * 6, 10)

    def text(self, text, x, y, scale=1):
        self.calls.append(("text", text, x, y, scale))


class Color:
    @staticmethod
    def rgb(*args):
        return args


class FirmwareTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.badge = Badge()
        self.adc = ADC()
        self.logs = []
        self.sensors = Sensors(self.badge, {name: name for name in
                              ("up", "down", "left", "right", "select", "back", "menu")},
                               self.adc, self.logs.append)
        self.sensors.poll(0)
        self.store = Store(self.temp.name)
        self.app = App(self.sensors, self.store)

    def press(self, action):
        self.app.update(16, [action], ())

    def engine(self, rules, available=None):
        program = blank()
        program["pages"][0] = rules
        output = []
        engine = Engine(program, lambda *args: output.append(args), available)
        engine.start(self.sensors.values)
        return engine, output


class ModelTests(unittest.TestCase):
    def test_examples_are_valid_independent_and_no_png(self):
        for name in EXAMPLES:
            first = example(name)
            self.assertIs(validate(first), first)
            other = clone(first)
            other["pages"][0].clear()
            self.assertTrue(first["pages"][0])
        self.assertFalse(list(APP.rglob("*.png")))
        for path in APP.glob("*.py"):
            tree = ast.parse(path.read_text())
            self.assertFalse(any(isinstance(node, ast.Call) and
                                 isinstance(node.func, ast.Attribute) and
                                 node.func.attr in ("load", "blit") and
                                 isinstance(node.func.value, ast.Name) and
                                 node.func.value.id in ("image", "screen") for node in ast.walk(tree)))

    def test_rejects_malformed_documents_and_types(self):
        for bad in (None, [], {}, {"version": True, "pages": [[]] * 5},
                    {"version": 2, "pages": [[]] * 5}, {"version": 1, "pages": [[]] * 6}):
            with self.assertRaises(ValueError):
                validate(bad)
        fields = {"event": [None, [], {}, "radio"],
                  "filter": [None, True, 0, [True], ["X"]],
                  "command": [None, [], {}, "sound"],
                  "arg": [None, [], {"frames": [True], "ms": 250},
                          {"frames": [1 << 25], "ms": 250}, {"frames": [0], "ms": 0}],
                  "repeat": [None, [True], list(range(9)), ["fake"]]}
        for field, values in fields.items():
            for value in values:
                program = blank()
                rule = new_rule()
                rule[field] = value
                program["pages"][0] = [rule]
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    validate(program)

    def test_limits_and_sensor_constants(self):
        program = blank()
        program["pages"][0] = [new_rule() for _ in range(MAX_RULES + 1)]
        with self.assertRaises(ValueError):
            validate(program)
        rule = new_rule("sensor", "number")
        rule["filter"] = {"name": "light", "op": "ge", "value": [65535]}
        rule["arg"] = ["light", "X", -65535, "random"]
        program["pages"][0] = [rule]
        validate(program)


class EngineTests(FirmwareTest):
    def test_start_defaults_once_and_number_clamp(self):
        rule = new_rule("start", "number")
        rule["arg"] = [1000]
        engine, out = self.engine([rule])
        engine.tick(0)
        engine.tick(1000)
        self.assertEqual(out, [("number", 99)])

    def test_delayed_start_is_once_and_timer_repeats(self):
        first = new_rule("start", "set")
        first["filter"], first["arg"]["value"] = [250, 1000], ["X", 1]
        timer = new_rule("timer", "set")
        timer["filter"], timer["arg"] = [1000], {"name": "Y", "value": ["Y", 1]}
        engine, _ = self.engine([first, timer])
        engine.tick(999)
        self.assertEqual(engine.variables, {"X": 0, "Y": 0, "Z": 0})
        engine.tick(1)
        self.assertEqual(engine.variables["Y"], 1)
        engine.tick(250)
        self.assertEqual(engine.variables["X"], 1)
        engine.tick(750)
        self.assertEqual(engine.variables["Y"], 2)
        self.assertEqual(engine.variables["X"], 1)

    def test_timer_default_and_random_sum(self):
        rule = new_rule("timer", "set")
        rule["arg"]["value"] = ["X", 1]
        engine, _ = self.engine([rule])
        engine.tick(249)
        self.assertEqual(engine.variables["X"], 0)
        engine.tick(1)
        self.assertEqual(engine.variables["X"], 1)
        rule["filter"] = [5000, "random_time"]
        engine, _ = self.engine([rule])
        engine.randint = lambda low, high: high
        engine.start()
        engine.tick(5999)
        self.assertEqual(engine.variables["X"], 0)
        engine.tick(1)
        self.assertEqual(engine.variables["X"], 1)

    def test_events_are_ordered_and_rules_read_prior_writes(self):
        a = new_rule("press", "set")
        a["arg"]["value"] = [1]
        b = new_rule("press", "set")
        b["arg"] = {"name": "Y", "value": ["X", 2]}
        c = new_rule("variable", "number")
        c["filter"] = {"name": "X", "equals": [1]}
        c["arg"] = ["event", "Y"]
        engine, out = self.engine([a, b, c])
        engine.tick(16, [("press", "select", None)])
        self.assertEqual(engine.variables["Y"], 3)
        self.assertEqual(out, [("number", 4)])

    def test_equal_assignment_is_not_change(self):
        rule = new_rule("press", "set")
        observe = new_rule("variable", "number")
        engine, out = self.engine([rule, observe])
        engine.tick(0, [("press", "select", None)])
        self.assertEqual(out, [])

    def test_counter_sample(self):
        out = []
        engine = Engine(example("Counter"), lambda *item: out.append(item))
        engine.start()
        engine.tick(0, [("press", "select", None), ("press", "select", None)])
        self.assertEqual(engine.variables["X"], 2)
        self.assertEqual(out, [("number", 2), ("number", 2)])

    def test_dice_bounds_and_variable_default(self):
        engine, _ = self.engine([])
        calls = []
        engine.randint = lambda a, b: calls.append((a, b)) or b
        self.assertEqual(engine.evaluate(["X", "Y", "Z", "random", 1]), 6)
        self.assertEqual(calls, [(1, 5)])

    def test_page_switch_cancels_jobs_events_and_old_rules(self):
        program = example("Hot potato")
        out = []
        engine = Engine(program, lambda *item: out.append(item), randint=lambda a, b: 0)
        engine.start()
        engine.tick(250)
        self.assertTrue(engine.jobs)
        engine.tick(4750)
        self.assertEqual(engine.page, 1)
        self.assertEqual(engine.jobs, {})
        self.assertEqual(engine.queue, [])
        before = len(out)
        engine.tick(1)
        self.assertEqual(out[-1], ("image", PICTURES[5]))
        self.assertEqual(len(out), before + 1)

    def test_same_page_switch_restarts_page(self):
        rule = new_rule("press", "page")
        start = new_rule("start", "set")
        start["arg"]["value"] = ["X", 1]
        engine, _ = self.engine([start, rule])
        engine.tick(0)
        engine.tick(0, [("press", "select", None)])
        self.assertEqual(engine.variables["X"], 1)
        engine.tick(0)
        self.assertEqual(engine.variables["X"], 2)

    def test_image_order_finite_and_infinite_repeat(self):
        rule = new_rule()
        rule["arg"] = {"frames": [HEART, SMALL_HEART], "ms": 100}
        rule["repeat"] = [2]
        engine, out = self.engine([rule])
        for dt in (0, 100, 100, 100, 100):
            engine.tick(dt)
        self.assertEqual(out, [("image", HEART), ("image", SMALL_HEART)] * 2)
        self.assertFalse(engine.jobs)
        rule["repeat"] = []
        engine, out = self.engine([rule])
        for dt in (0, 100, 100, 100, 100):
            engine.tick(dt)
        self.assertEqual(len(out), 5)
        self.assertTrue(engine.jobs)

    def test_new_display_cancels_old_sequence(self):
        animate = new_rule()
        animate["repeat"] = []
        number = new_rule("press", "number")
        number["arg"] = [7]
        engine, out = self.engine([animate, number])
        engine.tick(0)
        engine.tick(1, [("press", "select", None)])
        self.assertFalse(engine.jobs)
        engine.tick(5000)
        self.assertEqual(out[-1], ("number", 7))

    def test_independent_lights_and_display_and_stop(self):
        light = new_rule("start", "lights")
        light["arg"], light["repeat"] = [50], [2]
        image = new_rule()
        image["repeat"] = []
        engine, out = self.engine([light, image])
        engine.tick(0)
        self.assertEqual(set(engine.jobs), {"display", "lights"})
        engine.tick(250)
        self.assertIn(("lights", 0), out)
        engine.tick(250)
        engine.tick(250)
        self.assertNotIn("lights", engine.jobs)
        self.assertIn("display", engine.jobs)
        engine.stop()
        self.assertEqual(out[-1], ("lights", 0))
        self.assertFalse(engine.running)

    def test_zero_repeat_is_noop(self):
        for command in ("image", "lights"):
            rule = new_rule(command=command)
            rule["repeat"] = [0]
            engine, out = self.engine([rule])
            engine.tick(0)
            self.assertEqual(out, [])
            self.assertFalse(engine.jobs)

    def test_sensor_condition_on_entry_and_rearm(self):
        rule = new_rule("sensor", "set")
        rule["filter"] = {"name": "battery", "op": "le", "value": [25]}
        rule["arg"]["value"] = ["X", 1]
        engine, _ = self.engine([rule])
        for value in (50, 25, 20, 20, 26, 25):
            engine.tick(50, [("sensor", "battery", value)])
        self.assertEqual(engine.variables["X"], 2)

    def test_sensor_change_ignores_initial(self):
        rule = new_rule("sensor", "set")
        rule["filter"] = {"name": "gx", "op": "change", "value": []}
        rule["arg"]["value"] = ["X", 1]
        engine, _ = self.engine([rule])
        for value in (1, 1, 2, 2, 3):
            engine.tick(50, [("sensor", "gx", value)])
        self.assertEqual(engine.variables["X"], 2)

    def test_missing_sensor_stops_visibly_and_preflight(self):
        rule = new_rule("press", "number")
        rule["arg"] = ["gx"]
        engine, out = self.engine([rule], lambda feature: False)
        self.assertEqual(engine.missing(), ["gx"])
        engine.tick(0, [("press", "select", None)], {})
        self.assertFalse(engine.running)
        self.assertIn("unavailable", engine.error)
        self.assertFalse(any(item[0] == "number" for item in out))

    def test_feedback_bounded_and_queue_overflow_explicit(self):
        rule = new_rule("variable", "set")
        rule["arg"]["value"] = ["X", 1]
        engine, _ = self.engine([rule])
        engine.variables["X"] = 1
        engine.tick(16, [("variable", "X", 1)])
        self.assertEqual(engine.fired, 16)
        self.assertEqual(len(engine.queue), 1)
        engine.queue = [("variable", "X", 1)] * MAX_EVENTS
        engine.emit("variable", "X", 2)
        self.assertFalse(engine.running)
        self.assertIn("queue full", engine.error)

    def test_large_delta_no_catchup_storm_and_program_is_copied(self):
        rule = new_rule("timer", "set")
        rule["arg"]["value"] = ["X", 1]
        engine, _ = self.engine([rule])
        rule["arg"]["value"] = [99]
        engine.tick(10000000)
        self.assertEqual(engine.variables["X"], 1)
        self.assertLess(engine.fired, 3)

    def test_numeric_saturation(self):
        rule = new_rule("start", "set")
        rule["arg"]["value"] = [65535, 65535]
        engine, _ = self.engine([rule])
        engine.tick(0)
        self.assertEqual(engine.variables["X"], 65535)


class SensorTests(FirmwareTest):
    def test_all_actual_value_surfaces(self):
        self.assertEqual(set(self.sensors.values), {item[0] for item in SENSORS})
        self.assertEqual(self.sensors.values["battery"], 75)
        self.assertEqual(self.sensors.values["light"], 1000)
        self.assertEqual(self.sensors.values["az"], 16384)
        self.assertTrue(self.sensors.available("gx"))
        self.assertFalse(self.sensors.available("temperature"))

    def test_analog_sampling_20hz_but_buttons_every_frame(self):
        self.adc.value = 6000
        self.badge.down = {"select"}
        events = self.sensors.poll(16)
        self.assertIn(("press", "select", None), events)
        self.assertEqual(self.sensors.values["light"], 1000)
        self.sensors.poll(34)
        self.assertEqual(self.sensors.values["light"], 6000)

    def test_touch_edges_and_release(self):
        self.badge.touch = {"left"}
        self.assertIn(("touch", "left", None), self.sensors.poll(1))
        self.assertNotIn(("touch", "left", None), self.sensors.poll(1))
        self.badge.touch.clear()
        self.assertIn(("untouch", "left", None), self.sensors.poll(1))

    def test_light_band_hysteresis_and_limits(self):
        self.adc.value = 2048
        self.sensors.poll(50)
        self.assertEqual(self.sensors.values["light_level"], 2)
        self.adc.value = 2112
        self.sensors.poll(50)
        self.assertEqual(self.sensors.values["light_level"], 3)
        self.adc.value = 2000
        self.sensors.poll(50)
        self.assertEqual(self.sensors.values["light_level"], 3)
        self.adc.value = 1983
        self.sensors.poll(50)
        self.assertEqual(self.sensors.values["light_level"], 2)
        self.adc.value = 65535
        self.sensors.poll(50)
        self.assertEqual(self.sensors.values["light_level"], 5)

    def test_tilt_enter_hysteresis_rearm_and_raw_gyro(self):
        self.badge.reading = (16000, 0, 12000, 123, 456, 789)
        events = self.sensors.poll(50)
        self.assertIn(("move", "right", None), events)
        self.assertEqual(self.sensors.values["gx"], 123)
        self.assertNotIn(("move", "right", None), self.sensors.poll(50))
        self.badge.reading = (0, 0, 16384, 0, 0, 0)
        self.sensors.poll(50)
        self.badge.reading = (16000, 0, 12000, 0, 0, 0)
        self.assertIn(("move", "right", None), self.sensors.poll(50))

    def test_shake_two_hits_and_cooldown(self):
        self.badge.reading = (20000, 0, 16384, 0, 0, 0)
        self.assertNotIn(("move", "shake", None), self.sensors.poll(50))
        self.badge.reading = (-20000, 0, 16384, 0, 0, 0)
        self.assertIn(("move", "shake", None), self.sensors.poll(50))
        self.badge.reading = (20000, 0, 16384, 0, 0, 0)
        self.assertNotIn(("move", "shake", None), self.sensors.poll(50))

    def test_missing_firmware_and_broken_readings(self):
        self.badge.imu = None
        missing = Sensors(self.badge, self.sensors.buttons, self.adc, self.logs.append)
        self.assertFalse(missing.available("ax"))
        self.assertIn("imu", missing.errors)
        self.badge.imu = lambda: (1, 2)
        broken = Sensors(self.badge, self.sensors.buttons, self.adc, self.logs.append)
        broken.poll(0)
        self.assertFalse(broken.available("imu"))
        self.assertNotIn("ax", broken.values)
        self.assertTrue(self.logs)

    def test_sensor_error_removes_stale_values_and_does_not_invent_zero(self):
        self.adc.error = OSError(errno.EIO, "ADC failure")
        self.sensors.poll(50)
        self.assertFalse(self.sensors.available("light_level"))
        self.assertNotIn("light", self.sensors.values)
        self.assertNotIn("light_level", self.sensors.values)
        self.assertIn("ADC failure", self.sensors.errors["light"])

    def test_nonfinite_or_out_of_range_status_is_unavailable(self):
        self.badge.battery = float("nan")
        self.sensors.poll(50)
        self.assertFalse(self.sensors.available("battery"))
        self.assertNotIn("battery", self.sensors.values)


class PersistenceTests(FirmwareTest):
    def test_three_slots_independent_draft_and_round_trip(self):
        for index, name in enumerate(EXAMPLES[:3], 1):
            self.store.save(str(index), example(name))
        self.store.save("draft", blank())
        self.assertEqual(self.store.load("draft"), blank())
        for index, name in enumerate(EXAMPLES[:3], 1):
            self.assertEqual(self.store.load(str(index)), example(name))

    def test_missing_and_corrupt_slots_not_overwritten(self):
        self.assertIsNone(self.store.load("1"))
        path = Path(self.store.path("1"))
        for text in ("{", '{"version": 200}', "null", "[]", "x" * (MAX_BYTES + 1)):
            path.write_text(text)
            with self.assertRaises(ValueError):
                self.store.load("1")
            self.assertEqual(path.read_text(), text)

    def test_bad_slot_and_path_traversal_rejected(self):
        for slot in ("4", "../other", "", None):
            with self.assertRaises(ValueError):
                self.store.path(slot)

    def test_rename_failure_preserves_last_good_document(self):
        self.store.save("1", example("Counter"))
        with patch("storage.os.rename", side_effect=OSError(errno.EIO, "rename")):
            with self.assertRaises(OSError):
                self.store.save("1", blank())
        self.assertEqual(self.store.load("1"), example("Counter"))
        self.assertTrue(Path(self.store.path("1") + ".tmp").exists())

    def test_permission_error_is_not_treated_as_missing(self):
        with patch("builtins.open", side_effect=OSError(errno.EACCES, "permission")):
            with self.assertRaises(OSError):
                self.store.load("1")

    def test_wasi_missing_file_code_is_scoped_to_webassembly(self):
        with patch("builtins.open", side_effect=OSError(44)), \
                patch("storage.sys.platform", "webassembly"):
            self.assertIsNone(self.store.load("1"))
        with patch("builtins.open", side_effect=OSError(44)), \
                patch("storage.sys.platform", "darwin"):
            with self.assertRaises(OSError):
                self.store.load("1")

    def test_failed_autosave_is_visible_retains_ram_and_retries(self):
        with patch.object(self.store, "save", side_effect=OSError(errno.ENOSPC, "full")):
            self.app.replace(example("Dice"))
        self.assertTrue(self.app.dirty)
        self.assertIn("Not saved", self.app.error)
        self.assertEqual(self.app.program, example("Dice"))
        self.app.commit()
        self.assertFalse(self.app.dirty)
        self.assertEqual(self.store.load("draft"), example("Dice"))

    def test_corrupt_startup_preserves_source_until_user_edit(self):
        path = Path(self.store.path("draft"))
        path.write_text("{bad")
        app = App(self.sensors, self.store)
        self.assertIn("Draft not loaded", app.error)
        self.assertEqual(path.read_text(), "{bad")

    def test_draft_reopen(self):
        self.app.replace(example("Counter"))
        reopened = App(self.sensors, self.store)
        self.assertEqual(reopened.program, example("Counter"))


class EditorTests(FirmwareTest):
    def test_navigation_wrap_pages_and_home(self):
        self.press("select")
        self.assertEqual(self.app.mode, "editor")
        self.press("left")
        self.assertEqual(self.app.column, 5)
        self.press("right")
        self.assertEqual(self.app.column, 0)
        self.press("down")
        self.press("down")
        self.press("down")
        self.assertEqual((self.app.page, self.app.row), (1, -1))
        self.press("up")
        self.assertEqual(self.app.page, 0)
        self.press("up")
        self.assertEqual(self.app.mode, "home")

    def test_add_and_edit_when_do_via_controls(self):
        self.app.replace(blank())
        self.press("down")
        self.press("select")
        self.assertEqual(len(self.app.program["pages"][0]), 1)
        self.assertEqual(self.app.mode, "picker")
        self.press("down")  # timer
        self.press("select")
        self.assertEqual(self.app.rule()["event"], "timer")
        self.app.column = 2
        self.press("select")
        self.press("down")  # number
        self.press("select")
        self.assertEqual(self.app.rule()["command"], "number")
        self.assertEqual(self.store.load("draft"), self.app.program)

    def test_no_select_leaks_to_picker(self):
        self.app.replace(blank())
        self.press("down")
        self.app.update(16, ["select", "down"], ())
        self.assertEqual(self.app.mode, "picker")
        self.assertEqual(self.app.pick_index, 0)

    def test_expression_commit_cancel_and_sum(self):
        self.app.replace(example("Counter"))
        self.app.row = 0
        original = clone(self.app.rule())
        self.app.set_variable("X")
        self.app.add_term(5)
        self.press("back")
        self.assertEqual(self.app.rule(), original)
        self.app.set_variable("X")
        self.app.add_term(5)
        self.app.expr_index = 0
        self.press("select")
        self.assertEqual(self.app.rule()["arg"]["value"], ["X", 1, 5])

    def test_pixel_edit_commit_preset_and_frame_copy(self):
        self.app.replace(example("First program"))
        self.app.row = 0
        self.app.frames_menu()
        self.press("select")
        self.assertEqual(self.app.mode, "pixels")
        self.press("select")
        expected = HAPPY ^ (1 << 24)
        self.press("back")
        self.assertEqual(self.app.rule()["arg"]["frames"], [expected])
        self.app.frame_choice("append")
        self.assertEqual(self.app.rule()["arg"]["frames"], [expected, expected])
        self.app.frame_choice(1)
        self.press("menu")
        self.app.pick_index = 2  # heart
        self.press("select")
        self.assertEqual(self.app.mode, "pixels")
        self.press("back")
        self.assertEqual(self.app.rule()["arg"]["frames"], [expected, HEART])

    def test_rule_duplicate_reorder_delete_confirm(self):
        self.app.replace(example("First program"))
        self.app.row = 0
        self.app.change_row("copy")
        self.assertEqual(len(self.app.program["pages"][0]), 2)
        self.app.rule()["filter"] = "right"
        self.app.change_row("up")
        self.assertEqual(self.app.row, 0)
        self.app.change_row("delete")
        self.press("select")  # default cancel
        self.assertEqual(len(self.app.program["pages"][0]), 2)
        self.app.change_row("delete")
        self.press("right")
        self.press("select")
        self.assertEqual(len(self.app.program["pages"][0]), 1)

    def test_slot_save_and_load_confirmation(self):
        self.app.replace(example("Counter"))
        self.app.save_slot("1")
        self.press("right")
        self.press("select")
        self.app.replace(blank())
        self.app.mode = "home"
        self.app.load_slot("1")
        self.assertEqual(self.app.program, blank())
        self.press("right")
        self.press("select")
        self.assertEqual(self.app.program, example("Counter"))

    def test_run_stop_and_sensor_loss(self):
        self.app.replace(example("Counter"))
        self.app.run_program()
        self.assertEqual(self.app.mode, "run")
        self.app.update(16, [], [("press", "select", None)])
        self.assertEqual(self.app.display_value, 1)
        self.press("menu")
        self.assertTrue(self.app.inspect_run)
        self.press("back")
        self.assertEqual(self.app.mode, "editor")
        self.app.replace(example("Light meter"))
        self.app.run_program()
        self.sensors.disable("light", OSError("gone"))
        self.app.update(16, [], ())
        self.assertIn("Sensor lost", self.app.error)
        self.assertEqual(self.app.mode, "editor")

    def test_unavailable_tiles_crossed_and_execution_blocked(self):
        self.sensors.disable("imu", OSError("missing"))
        self.app.replace(example("Shake counter"))
        self.app.run_program()
        self.assertNotEqual(self.app.mode, "run")
        self.assertIn("unavailable", self.app.error)
        self.app.row = 0
        self.app.edit_event()
        move = next(item for item in self.app.options if item[2] == "move")
        self.assertFalse(move[3])

    def test_help_up_back_sensors_and_numeric_controls(self):
        self.app.open_help("editor")
        self.press("up")
        self.assertEqual(self.app.mode, "editor")
        self.app.mode = "sensors"
        self.press("up")
        self.assertEqual(self.app.mode, "home")
        result = []
        self.app.begin_number(1, result.append, "editor", -65535, 65535)
        self.press("right")
        self.press("up")
        self.press("select")
        self.assertEqual(result, [6])

    def test_render_all_screens_and_glyph_bounds(self):
        screen = Screen()
        view = View(screen, Color, "font")
        for mode in ("home", "editor", "sensors"):
            self.app.mode = mode
            view.render(self.app)
        self.app.open_help("editor")
        view.render(self.app)
        self.app.replace(example("Counter"))
        self.app.row = 0
        self.app.set_variable("X")
        view.render(self.app)
        self.app.begin_number(1, lambda _: None, "editor", 0, 50)
        view.render(self.app)
        self.app.confirm("Replace program?", lambda: None, "editor")
        view.render(self.app)
        self.app.slots(True)
        view.render(self.app)
        self.app.replace(example("First program"))
        self.app.row = 0
        self.app.frame_choice(0)
        view.render(self.app)
        self.app.run_program()
        view.render(self.app)
        self.app.inspect_run = True
        view.render(self.app)
        for glyph, rows in GLYPHS.items():
            self.assertEqual(len(rows), 7)
            self.assertTrue(all(0 <= bits < 128 for bits in rows))
            draw(screen, glyph, 0, 0)
        for mask in PICTURES:
            matrix(screen, mask, 0, 0)
        self.assertTrue(screen.calls)

    def test_actual_entrypoint_runs_with_runtime_globals_no_home_handler(self):
        screen = Screen()
        frames = []
        buttons = {name: name.lower().replace("button_", "") for name in
                   ("BUTTON_UP", "BUTTON_DOWN", "BUTTON_LEFT", "BUTTON_RIGHT",
                    "BUTTON_SELECT", "BUTTON_BACK", "BUTTON_MENU")}

        def run(update):
            for _ in range(3):
                update()
                frames.append(True)
        globals_ = dict(buttons, badge=self.badge, screen=screen, color=Color,
                        font=type("Font", (), {"sins": "font"})(), HIRES=1, VSYNC=2, run=run)
        with patch("os.chdir") as chdir, patch("sensors.Sensors", return_value=self.sensors), \
                patch("storage.Store", return_value=self.store):
            loaded = runpy.run_path(str(APP / "__init__.py"), init_globals=globals_)
        chdir.assert_called_once_with("/system/apps/microcode")
        self.assertEqual(len(frames), 3)
        self.assertEqual(loaded["app"].mode, "home")
        self.assertTrue(screen.calls)
        self.assertNotIn("BUTTON_HOME", (APP / "__init__.py").read_text())


if __name__ == "__main__":
    unittest.main()
