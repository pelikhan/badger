"""Firmware-independent controller for the on-device structured editor."""

from engine import Engine
from model import (
    BUTTONS, COMMANDS, EVENTS, EXAMPLES, GESTURES, MAX_FRAMES, MAX_RULES,
    MAX_TERMS, PAGES, PICTURES, PICTURE_NAMES, VARIABLES,
    blank, clone, example, new_rule, validate,
)
from sensors import SENSORS, UNSUPPORTED, spec

EVENT_LABELS = {
    "start": "Page start", "timer": "Timer", "press": "Button press",
    "release": "Button release", "touch": "Touch", "untouch": "Touch release",
    "move": "Move", "variable": "Variable changed", "sensor": "Sensor condition",
}
COMMAND_LABELS = {"image": "Show images", "number": "Show number", "set": "Set variable",
                  "page": "Switch page", "lights": "Case LEDs"}
HOME = (("Continue editing", "edit"), ("Run program", "start"), ("New program", "new"),
        ("Load slot", "load"), ("Examples", "samples"), ("Sensors & support", "sensor"),
        ("Help / controls", "help"))
TOOLBAR = (("Run", "start"), ("Save", "save"), ("Prev", "left"),
           ("Next", "right"), ("Home", "back"), ("Help", "help"))
HELP = (
    ("Build a rule", "WHEN event + condition. DO command + value. SELECT edits a tile."),
    ("Navigate", "Arrows move. Left/right wrap on a row. Up above page 1 goes Home."),
    ("Pages", "Five pages; only one runs. Down past the last rule visits the next page."),
    ("Run / stop", "Run always starts at page 1. BACK stops. MENU inspects live sensors."),
    ("Save / load", "Editor Save writes one of 3 slots. Home Load restores a slot. Edits autosave a draft."),
    ("Numbers", "Value tiles add together. X/Y/Z start at 0. Dice gives 1..5. Empty set means 0."),
    ("Sequences", "Images play in order. Append copies the last frame. Edit all 25 pixels with arrows + SELECT."),
    ("Repeat", "Choose once, a count, or forever for images / LED blinks. A new display rule cancels the old one."),
    ("Buttons", "SELECT is sample A; RIGHT is sample B. BACK stops, MENU inspects, HOME stays launcher-owned."),
    ("Sensor events", "Conditions fire on entry, not every frame. Change ignores the initial sample. Values can be summed."),
    ("Motion", "Tilt uses relative acceleration with hysteresis. Shake needs 2 changes. Raw axes are not g or degrees/sec."),
    ("Unavailable", "No mic, speaker, compass, micro:bit radio or Jacdac driver. Sensors screen shows API faults."),
)


def option(label, glyph, value, enabled=True):
    return (label, glyph, value, enabled)


def terms_label(terms, times=False):
    if not terms:
        return "default"
    names = {"random_time": "?s", "random": "dice", "event": "event"}
    return "+".join((("%g" % (term / 1000) + "s") if times and type(term) is int
                     else names.get(str(term), str(term))) for term in terms)


class App:
    def __init__(self, sensors, store):
        self.sensors = sensors
        self.store = store
        self.mode = "home"
        self.home_index = 0
        self.page = 0
        self.row = -1
        self.column = 0
        self.program = example("First program")
        self.message = ""
        self.error = ""
        self.dirty = False
        self.engine = None
        self.display_kind = "image"
        self.display_value = PICTURES[0]
        self.sensor_index = 0
        self.help_index = 0
        self.inspect_run = False
        try:
            saved = store.load("draft")
            if saved is not None:
                self.program = saved
        except (OSError, ValueError) as error:
            self.report("Draft not loaded: " + str(error))
        self.pick_title = ""
        self.options = []
        self.pick_index = 0
        self.pick_back = "editor"
        self.on_pick = None
        self.confirm_text = ""
        self.confirm_action = None
        self.confirm_index = 0
        self.confirm_back = "home"

    def report(self, text):
        self.error = text
        print("MicroCode: " + text)

    def commit(self):
        validate(self.program)
        self.dirty = True
        try:
            self.store.save("draft", self.program)
            self.dirty = False
            self.error = ""
            self.message = "Draft saved"
        except (OSError, ValueError) as error:
            self.report("Not saved: " + str(error))

    def pick(self, title, options, callback, back="editor"):
        self.mode = "picker"
        self.pick_title = title
        self.options = options
        self.pick_index = 0
        self.on_pick = callback
        self.pick_back = back

    def confirm(self, text, callback, back):
        self.mode = "confirm"
        self.confirm_text = text
        self.confirm_action = callback
        self.confirm_index = 0
        self.confirm_back = back

    def replace(self, program):
        self.program = clone(program)
        self.page, self.row, self.column = 0, -1, 0
        self.mode = "editor"
        self.commit()

    def rule(self):
        return self.program["pages"][self.page][self.row]

    def output(self, command, value):
        if command in ("image", "number"):
            self.display_kind, self.display_value = command, value
        elif command == "lights" and self.sensors.available("lights"):
            self.sensors.badge.caselights(value / 100)

    def run_program(self):
        if self.dirty:
            self.commit()
            if self.dirty:
                return
        self.engine = Engine(self.program, self.output, self.sensors.available)
        missing = self.engine.missing()
        if missing:
            self.report("Cannot run; unavailable: " + ", ".join(missing))
            return
        self.display_kind, self.display_value = "image", 0
        self.inspect_run = False
        self.engine.start(self.sensors.values)
        self.mode = "run"

    def stop_program(self):
        if self.engine is not None:
            self.engine.stop()
            if self.engine.error:
                self.report(self.engine.error)
        self.mode = "editor"

    def update(self, dt, actions, events):
        if self.mode == "run":
            if "back" in actions:
                self.stop_program()
                return
            if "menu" in actions:
                self.inspect_run = not self.inspect_run
            missing = self.engine.missing()
            if missing:
                self.engine.fail("Sensor lost: " + ", ".join(missing))
            self.engine.tick(dt, events, self.sensors.values)
            if self.engine.error:
                self.report(self.engine.error)
                self.mode = "editor"
            return
        if not actions:
            return
        # One editor transition per frame prevents SELECT leaking into a new screen.
        for action in ("back", "menu", "select", "up", "down", "left", "right"):
            if action in actions:
                self.handle(action)
                break

    def handle(self, action):
        if self.mode == "home":
            if action in ("up", "down", "left", "right"):
                step = -1 if action in ("up", "left") else 1
                self.home_index = (self.home_index + step) % len(HOME)
            elif action == "select":
                self.home_activate()
        elif self.mode == "editor":
            self.editor_action(action)
        elif self.mode == "picker":
            if action in ("up", "down", "left", "right"):
                self.pick_index = (self.pick_index + (-1 if action in ("up", "left") else 1)) % len(self.options)
            elif action in ("back", "menu"):
                self.mode = self.pick_back
            elif action == "select":
                chosen = self.options[self.pick_index]
                if chosen[3]:
                    callback = self.on_pick
                    self.mode = self.pick_back
                    callback(chosen[2])
                else:
                    self.report("Unavailable: " + chosen[0])
        elif self.mode == "confirm":
            if action in ("left", "right", "up", "down"):
                self.confirm_index = 1 - self.confirm_index
            elif action in ("back", "menu"):
                self.mode = self.confirm_back
            elif action == "select":
                self.mode = self.confirm_back
                if self.confirm_index:
                    self.confirm_action()
        elif self.mode == "expression":
            self.expression_action(action)
        elif self.mode == "numeric":
            self.numeric_action(action)
        elif self.mode == "pixels":
            self.pixel_action(action)
        elif self.mode == "sensors":
            count = len(SENSORS) + len(UNSUPPORTED) + 2
            if action in ("down", "right"):
                self.sensor_index = (self.sensor_index + 1) % count
            elif action in ("up", "left"):
                if action == "up" and self.sensor_index == 0:
                    self.mode = "home"
                else:
                    self.sensor_index = (self.sensor_index - 1) % count
            elif action in ("back", "menu"):
                self.mode = "home"
        elif self.mode == "help":
            if action in ("down", "right", "select"):
                self.help_index = (self.help_index + 1) % len(HELP)
            elif action in ("up", "left"):
                if action == "up" and self.help_index == 0:
                    self.mode = self.help_back
                else:
                    self.help_index = (self.help_index - 1) % len(HELP)
            elif action in ("back", "menu"):
                self.mode = self.help_back

    def home_activate(self):
        choice = HOME[self.home_index][1]
        if choice == "edit":
            self.mode = "editor"
        elif choice == "start":
            self.run_program()
        elif choice == "new":
            self.confirm("Replace draft with an empty program?", lambda: self.replace(blank()), "home")
        elif choice == "load":
            self.slots(False)
        elif choice == "samples":
            self.pick("Examples (replace draft)", [option(name, "samples", name) for name in EXAMPLES],
                      lambda name: self.confirm("Replace draft with " + name + "?",
                                                lambda: self.replace(example(name)), "home"), "home")
        elif choice == "sensor":
            self.mode, self.sensor_index = "sensors", 0
        else:
            self.open_help("home")

    def open_help(self, back):
        self.help_back = back
        self.help_index = 0
        self.mode = "help"

    def slots(self, saving):
        options = []
        for slot in ("1", "2", "3"):
            try:
                saved = self.store.load(slot)
                state = "empty" if saved is None else "%d rules" % sum(len(page) for page in saved["pages"])
            except (OSError, ValueError) as error:
                state = "unreadable"
                print("MicroCode slot %s: %s" % (slot, error))
            options.append(option("Slot " + slot + " - " + state, "save" if saving else "load", slot))
        self.pick("Save to slot" if saving else "Load from slot", options,
                  self.save_slot if saving else self.load_slot, "editor" if saving else "home")

    def save_slot(self, slot):
        self.confirm("Overwrite slot " + slot + "?", lambda: self.write_slot(slot), "editor")

    def write_slot(self, slot):
        try:
            self.store.save(slot, self.program)
            self.message = "Saved slot " + slot
            self.error = ""
        except (OSError, ValueError) as error:
            self.report("Slot not saved: " + str(error))

    def load_slot(self, slot):
        try:
            saved = self.store.load(slot)
            if saved is None:
                self.report("Slot " + slot + " is empty")
                return
        except (OSError, ValueError) as error:
            self.report("Slot not loaded: " + str(error))
            return
        self.confirm("Replace draft with slot " + slot + "?", lambda: self.replace(saved), "home")

    def editor_action(self, action):
        rules = self.program["pages"][self.page]
        last = min(len(rules), MAX_RULES - 1)
        if action in ("left", "right"):
            self.column = (self.column + (1 if action == "right" else -1)) % 6
        elif action == "up":
            if self.row >= 0:
                self.row -= 1
            elif self.page > 0:
                self.page -= 1
            else:
                self.mode = "home"
        elif action == "down":
            if self.row < last:
                self.row += 1
            else:
                self.page = (self.page + 1) % PAGES
                self.row = -1
        elif action == "back":
            if self.row >= 0:
                self.row = -1
            else:
                self.mode = "home"
        elif action == "menu":
            self.pick("Program", [option("Run", "start", "run"), option("Save slot", "save", "save"),
                                   option("Retry draft save", "save", "retry"),
                                   option("Home / load / examples", "back", "home")],
                      self.program_menu)
        elif action == "select":
            if self.row == -1:
                if self.column == 0:
                    self.run_program()
                elif self.column == 1:
                    self.slots(True)
                elif self.column in (2, 3):
                    self.page = (self.page + (-1 if self.column == 2 else 1)) % PAGES
                elif self.column == 4:
                    self.mode = "home"
                else:
                    self.open_help("editor")
            elif self.row == len(rules):
                rules.append(new_rule())
                self.column = 0
                self.commit()
                self.edit_event()
            else:
                (self.edit_event, self.edit_filter, self.edit_command,
                 self.edit_argument, self.edit_repeat, self.row_menu)[self.column]()

    def program_menu(self, choice):
        if choice == "run":
            self.run_program()
        elif choice == "save":
            self.slots(True)
        elif choice == "retry":
            self.commit()
        else:
            self.mode = "home"

    def event_enabled(self, event):
        return ((event != "move" or self.sensors.available("imu")) and
                (event not in ("touch", "untouch") or self.sensors.available("touch")))

    def edit_event(self):
        choices = [option(EVENT_LABELS[event], event, event, self.event_enabled(event)) for event in EVENTS]
        self.pick("WHEN: event", choices, self.set_event)

    def set_event(self, event):
        rule = self.rule()
        rule["event"] = event
        rule["filter"] = new_rule(event)["filter"]
        self.commit()

    def edit_command(self):
        self.pick("DO: command",
                  [option(COMMAND_LABELS[command], command, command,
                          command != "lights" or self.sensors.available("lights")) for command in COMMANDS],
                  self.set_command)

    def set_command(self, command):
        rule = self.rule()
        rule["command"] = command
        rule["arg"] = new_rule(command=command)["arg"]
        rule["repeat"] = [1]
        self.commit()

    def edit_filter(self):
        rule = self.rule()
        event = rule["event"]
        if event in ("start", "timer"):
            self.edit_expression(rule["filter"], lambda value: self.assign("filter", value),
                                 "Delay / timer sum", times=True)
        elif event in ("press", "release", "touch", "untouch"):
            self.pick("Which logical button?", [option(name.upper(), name, name) for name in BUTTONS],
                      lambda value: self.assign("filter", value))
        elif event == "move":
            self.pick("Which movement?", [option(name.replace("_", " "), "move", name) for name in GESTURES],
                      lambda value: self.assign("filter", value))
        elif event == "variable":
            self.pick("Variable event", [option("Variable " + name, "variable", name) for name in VARIABLES],
                      self.variable_filter)
        else:
            self.pick("Sensor condition",
                      [option(label + (" [unavailable]" if not self.sensors.available(name) else ""),
                              "sensor", name, self.sensors.available(name)) for name, label, _, _, _ in SENSORS],
                      self.sensor_filter)

    def assign(self, field, value):
        self.rule()[field] = value
        self.mode = "editor"
        self.commit()

    def variable_filter(self, name):
        self.edit_expression(self.rule()["filter"]["equals"],
                             lambda terms: self.assign("filter", {"name": name, "equals": terms}),
                             name + " equals sum (empty=any)")

    def sensor_filter(self, name):
        self.pick("Condition: " + spec(name)[1],
                  [option("Equals", "sensor", "eq"), option("At least", "up", "ge"),
                   option("At most", "down", "le"), option("Changes", "variable", "change")],
                  lambda op: self.sensor_threshold(name, op))

    def sensor_threshold(self, name, op):
        if op == "change":
            self.assign("filter", {"name": name, "op": op, "value": []})
        else:
            current = self.rule()["filter"]
            values = current["value"] if current["name"] == name else [spec(name)[4]]
            self.edit_expression(values, lambda terms: self.assign(
                "filter", {"name": name, "op": op, "value": terms}), spec(name)[1] + " threshold")

    def edit_argument(self):
        rule = self.rule()
        command = rule["command"]
        if command == "image":
            self.frames_menu()
        elif command == "page":
            self.pick("Switch to page", [option("Page " + str(n), "page", n) for n in range(1, PAGES + 1)],
                      lambda value: self.assign("arg", value))
        elif command == "set":
            self.pick("Set which variable?", [option("Set " + name, "set", name) for name in VARIABLES],
                      self.set_variable)
        else:
            self.edit_expression(rule["arg"], lambda value: self.assign("arg", value),
                                 "Number sum" if command == "number" else "LED brightness 0..100")

    def set_variable(self, name):
        self.edit_expression(self.rule()["arg"]["value"],
                             lambda value: self.assign("arg", {"name": name, "value": value}),
                             "Set " + name + " to sum")

    def edit_repeat(self):
        if self.rule()["command"] not in ("image", "lights"):
            self.report("Repeat applies to images / LEDs only")
            return
        self.pick("Repeat entire sequence",
                  [option("Once", "repeat", [1]), option("Forever", "repeat", [])] +
                  [option(str(n) + " times", "repeat", [n]) for n in range(2, 6)] +
                  [option("Count expression", "number", None)], self.set_repeat)

    def set_repeat(self, value):
        if value is None:
            self.edit_expression(self.rule()["repeat"], lambda terms: self.assign("repeat", terms),
                                 "Repeat count (empty=forever)")
        else:
            self.assign("repeat", value)

    def row_menu(self):
        self.pick("Rule " + str(self.row + 1),
                  [option("Duplicate", "new", "copy", len(self.program["pages"][self.page]) < MAX_RULES),
                   option("Move up", "up", "up", self.row > 0),
                   option("Move down", "down", "down", self.row < len(self.program["pages"][self.page]) - 1),
                   option("Delete", "delete", "delete")], self.change_row)

    def change_row(self, choice):
        rules = self.program["pages"][self.page]
        if choice == "copy":
            rules.insert(self.row + 1, clone(self.rule()))
            self.row += 1
        elif choice == "delete":
            self.confirm("Delete rule " + str(self.row + 1) + "?",
                         self.delete_row, "editor")
            return
        else:
            target = self.row + (-1 if choice == "up" else 1)
            rules[self.row], rules[target] = rules[target], rules[self.row]
            self.row = target
        self.commit()

    def delete_row(self):
        del self.program["pages"][self.page][self.row]
        self.row = min(self.row, len(self.program["pages"][self.page]))
        if self.row == MAX_RULES:
            self.row -= 1
        self.commit()

    def edit_expression(self, terms, callback, title, times=False):
        self.mode = "expression"
        self.expr = list(terms)
        self.expr_done = callback
        self.expr_title = title
        self.expr_times = times
        self.expr_index = 0
        self.expr_options = [option("Done: use sum", "select", "done")]
        if times:
            self.expr_options += [option("Add " + label, "timer", value) for label, value in
                                  (("1/4 second", 250), ("1 second", 1000), ("5 seconds", 5000),
                                   ("random 0..1 second", "random_time"))]
        else:
            self.expr_options += [option("Add number...", "number", "numeric")]
            self.expr_options += [option("Add " + str(n) + " dots", "number", n) for n in range(1, 6)]
            self.expr_options += [option("Add " + name, "variable", name) for name in VARIABLES]
            self.expr_options += [option("Add dice (1..5)", "random", "random")]
            if self.rule()["event"] in ("variable", "sensor"):
                self.expr_options += [option("Add event value", "sensor", "event")]
            self.expr_options += [option("Add " + label, "sensor", name, self.sensors.available(name))
                                  for name, label, _, _, _ in SENSORS]
        self.expr_options += [option("Remove last tile", "delete", "remove"),
                              option("Clear / use default", "back", "clear")]

    def expression_action(self, action):
        if action in ("up", "down", "left", "right"):
            self.expr_index = (self.expr_index + (-1 if action in ("up", "left") else 1)) % len(self.expr_options)
        elif action in ("back", "menu"):
            self.mode = "editor"
        elif action == "select":
            item = self.expr_options[self.expr_index]
            if not item[3]:
                self.report("Unavailable: " + item[0])
                return
            value = item[2]
            if value == "done":
                self.expr_done(list(self.expr))
            elif value == "remove":
                if self.expr:
                    self.expr.pop()
            elif value == "clear":
                self.expr = []
            elif value == "numeric":
                self.begin_number(1, self.add_term, "expression", -65535, 65535)
            else:
                self.add_term(value)

    def add_term(self, value):
        if len(self.expr) < MAX_TERMS:
            self.expr.append(value)
        else:
            self.report("Maximum 8 value tiles")
        self.mode = "expression"

    def begin_number(self, value, callback, back, low, high):
        self.numeric = value
        self.numeric_done = callback
        self.numeric_back = back
        self.numeric_low, self.numeric_high = low, high
        self.numeric_step = 0
        self.mode = "numeric"

    def numeric_action(self, action):
        steps = (1, 5, 50, 500, 5000)
        if action in ("left", "right"):
            self.numeric_step = (self.numeric_step + (-1 if action == "left" else 1)) % len(steps)
        elif action in ("up", "down"):
            self.numeric = max(self.numeric_low, min(self.numeric_high,
                               self.numeric + (1 if action == "up" else -1) * steps[self.numeric_step]))
        elif action in ("back", "menu"):
            self.mode = self.numeric_back
        elif action == "select":
            self.mode = self.numeric_back
            self.numeric_done(self.numeric)

    def frames_menu(self, _unused=None):
        frames = self.rule()["arg"]["frames"]
        self.pick("Animation: select a frame",
                  [option("Edit frame " + str(n + 1), "image", n) for n in range(len(frames))] +
                  [option("Append copy of last", "new", "append", len(frames) < MAX_FRAMES),
                   option("Frame duration (ms)", "timer", "speed"),
                   option("Delete last frame", "delete", "delete", len(frames) > 1),
                   option("Done", "select", "done")], self.frame_choice)

    def frame_choice(self, choice):
        frames = self.rule()["arg"]["frames"]
        if type(choice) is int:
            self.frame_index = choice
            self.pixel_mask = frames[choice]
            self.pixel_cursor = 0
            self.mode = "pixels"
        elif choice == "append":
            frames.append(frames[-1])
            self.commit()
            self.frames_menu()
        elif choice == "speed":
            self.begin_number(self.rule()["arg"]["ms"], self.frame_speed, "editor", 50, 5000)
        elif choice == "delete":
            frames.pop()
            self.commit()
            self.frames_menu()

    def frame_speed(self, value):
        self.rule()["arg"]["ms"] = value
        self.commit()
        self.frames_menu()

    def pixel_action(self, action):
        if action == "menu":
            self.pick("Frame presets", [option(name, "image", mask) for name, mask in
                                       zip(PICTURE_NAMES, PICTURES)], self.use_preset, "pixels")
        elif action == "back":
            self.rule()["arg"]["frames"][self.frame_index] = self.pixel_mask
            self.commit()
            self.frames_menu()
        elif action == "select":
            self.pixel_mask ^= 1 << (24 - self.pixel_cursor)
        elif action in ("left", "right"):
            row, col = divmod(self.pixel_cursor, 5)
            self.pixel_cursor = row * 5 + (col + (-1 if action == "left" else 1)) % 5
        elif action in ("up", "down"):
            if action == "up" and self.pixel_cursor < 5:
                self.pixel_action("back")
            else:
                self.pixel_cursor = (self.pixel_cursor + (-5 if action == "up" else 5)) % 25

    def use_preset(self, mask):
        self.pixel_mask = mask
        self.mode = "pixels"

    def close(self):
        if self.engine is not None:
            self.engine.stop()
