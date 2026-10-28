"""Small vector UI for the 320x240 badge display."""

from editor import COMMAND_LABELS, EVENT_LABELS, HELP, HOME, TOOLBAR, terms_label
from icons import draw, matrix
from sensors import SENSORS, UNSUPPORTED


class View:
    def __init__(self, screen, color, font):
        self.screen = screen
        self.font = font
        self.colors = {name: color.rgb(*rgb) for name, rgb in (
            ("bg", (12, 19, 30)), ("panel", (29, 42, 60)), ("ink", (238, 245, 255)),
            ("muted", (144, 163, 187)), ("when", (65, 192, 228)), ("do", (116, 222, 150)),
            ("focus", (255, 204, 80)), ("error", (255, 129, 133)), ("off", (46, 61, 80)),
        )}
        self.w = screen.width
        self.h = screen.height

    def pen(self, name):
        self.screen.pen = self.colors[name]

    def box(self, x, y, w, h, color):
        self.pen(color)
        self.screen.rectangle(x, y, w, h)

    def text(self, text, x, y, pen="ink", width=None):
        self.pen(pen)
        text = str(text)
        if width is not None:
            original = text
            while text and self.screen.measure_text(text)[0] > width:
                text = text[:-1]
            if text != original and len(text) > 2:
                text = text[:-2] + ".."
        self.screen.text(text, x, y)

    def wrap(self, text, x, y, width, pen="ink", line=14):
        current = ""
        for word in text.split():
            trial = current + (" " if current else "") + word
            if current and self.screen.measure_text(trial)[0] > width:
                self.text(current, x, y, pen, width)
                y += line
                current = word
            else:
                current = trial
        if current:
            self.text(current, x, y, pen, width)
            y += line
        return y

    def tile(self, x, y, w, h, glyph, label, selected=False, tint="ink", enabled=True):
        self.box(x, y, w, h, "focus" if selected else "off")
        self.box(x + 1, y + 1, w - 2, h - 2, "panel")
        self.pen(tint if enabled else "muted")
        draw(self.screen, glyph, int(x + (w - 14) / 2), y + 4)
        self.text(label, x + 3, y + h - 13, tint if enabled else "muted", w - 5)

    def header(self, title, sub=""):
        self.text(title, 8, 5, "ink", self.w - 85)
        self.text(sub, self.w - 80, 5, "muted", 76)
        self.box(8, 22, self.w - 16, 1, "off")

    def footer(self, text):
        self.box(0, self.h - 20, self.w, 20, "bg")
        self.text(text, 7, self.h - 16, "muted", self.w - 14)

    def list(self, options, selected, top=40, bottom=None):
        bottom = self.h - 44 if bottom is None else bottom
        pitch = 28
        visible = max(1, (bottom - top) // pitch)
        first = max(0, min(selected - visible // 2, len(options) - visible))
        for index in range(first, min(len(options), first + visible)):
            label, glyph, _, enabled = options[index]
            y = top + (index - first) * pitch
            self.box(8, y, self.w - 16, pitch - 3, "focus" if index == selected else "off")
            self.box(9, y + 1, self.w - 18, pitch - 5, "panel")
            self.pen("ink" if enabled else "muted")
            draw(self.screen, glyph, 16, y + 5)
            if not enabled:
                self.screen.line(15, y + 20, 32, y + 3)
            self.text(label, 41, y + 7, "ink" if enabled else "muted", self.w - 54)
        self.text("%d / %d" % (selected + 1, len(options)), self.w - 70, bottom + 2, "muted", 65)

    def render(self, app):
        self.screen.font = self.font
        self.box(0, 0, self.w, self.h, "bg")
        mode = app.mode
        if mode == "home":
            self.header("MICROCODE", "BADGE")
            self.list([(label, glyph, glyph, True) for label, glyph in HOME], app.home_index, 30)
            self.footer("Arrows: move   SELECT: open")
        elif mode == "editor":
            self.draw_editor(app)
        elif mode == "picker":
            self.header(app.pick_title)
            self.list(app.options, app.pick_index, 34)
            self.footer("SELECT: choose   BACK: cancel")
        elif mode == "confirm":
            self.header("Confirm")
            self.wrap(app.confirm_text, 16, 48, self.w - 32)
            self.tile(30, 134, 110, 40, "back", "Cancel", app.confirm_index == 0)
            self.tile(self.w - 140, 134, 110, 40, "select", "Confirm", app.confirm_index == 1)
            self.footer("Left/right: choose   SELECT: confirm")
        elif mode == "expression":
            self.header(app.expr_title)
            self.wrap(terms_label(app.expr, app.expr_times), 10, 29, self.w - 20, "focus")
            self.list(app.expr_options, app.expr_index, 64)
            self.footer("SELECT: add/use   BACK: discard")
        elif mode == "numeric":
            self.header("Number tile")
            self.text(str(app.numeric), 32, 70, "focus")
            self.wrap("UP + / DOWN -", 32, 110, self.w - 64)
            self.wrap("LEFT / RIGHT: step %d" % (1, 5, 50, 500, 5000)[app.numeric_step],
                      32, 136, self.w - 64, "when")
            self.footer("SELECT: use number   BACK: cancel")
        elif mode == "pixels":
            self.header("Draw frame %d" % (app.frame_index + 1))
            cell = 26
            x, y = (self.w - 5 * cell) // 2, 44
            matrix(self.screen, app.pixel_mask, x, y, cell, 3,
                   self.colors["off"], self.colors["do"])
            px, py = app.pixel_cursor % 5, app.pixel_cursor // 5
            self.pen("focus")
            self.screen.rectangle(x + px * cell - 1, y + py * cell - 1, cell, 2)
            self.screen.rectangle(x + px * cell - 1, y + (py + 1) * cell - 3, cell, 2)
            self.screen.rectangle(x + px * cell - 1, y + py * cell, 2, cell - 2)
            self.screen.rectangle(x + (px + 1) * cell - 3, y + py * cell, 2, cell - 2)
            self.footer("SELECT: pixel  MENU: presets  BACK: done")
        elif mode == "run":
            self.draw_run(app)
        elif mode == "sensors":
            self.draw_sensors(app)
        elif mode == "help":
            title, text = HELP[app.help_index]
            self.header("Help", "%d/%d" % (app.help_index + 1, len(HELP)))
            self.text(title, 12, 44, "when", self.w - 24)
            self.wrap(text, 12, 74, self.w - 24, line=18)
            self.footer("Arrows: next/prev   BACK: close")
        if app.error:
            self.box(0, self.h - 43, self.w, 22, "panel")
            self.text(app.error, 6, self.h - 39, "error", self.w - 12)

    def draw_editor(self, app):
        self.header("WHEN / DO", "PAGE %d/5" % (app.page + 1))
        pitch = (self.w - 16) // 6
        for column, (label, glyph) in enumerate(TOOLBAR):
            self.tile(8 + column * pitch, 26, pitch - 2, 34, glyph, label,
                      app.row == -1 and app.column == column)
        self.text("WHEN", 18, 63, "when")
        self.text("DO", 18 + 2 * pitch, 63, "do")
        rules = app.program["pages"][app.page]
        total = min(8, len(rules) + 1)
        visible = 3
        first = max(0, min(max(0, app.row) - 1, total - visible))
        for index in range(first, min(total, first + visible)):
            y = 81 + (index - first) * 41
            if index == len(rules):
                self.tile(16, y, self.w - 26, 36, "new", "Add rule", app.row == index)
                continue
            rule = rules[index]
            self.text(str(index + 1), 2, y + 13, "muted")
            labels = (EVENT_LABELS[rule["event"]].split()[0], self.filter_label(rule),
                      COMMAND_LABELS[rule["command"]].split()[0], self.argument_label(rule),
                      "forever" if not rule["repeat"] else "x" + terms_label(rule["repeat"]), "Rule")
            glyphs = (rule["event"], "sensor", rule["command"], "image" if rule["command"] == "image" else "number",
                      "repeat", "edit")
            for col in range(6):
                self.tile(16 + col * (pitch - 1), y, pitch - 3, 37, glyphs[col], labels[col],
                          app.row == index and app.column == col, "when" if col < 2 else "do")
        if app.row >= 0 and app.row < len(rules):
            rule = rules[app.row]
            summaries = (EVENT_LABELS[rule["event"]], self.filter_label(rule),
                         COMMAND_LABELS[rule["command"]], self.argument_label(rule),
                         "Repeat (empty means forever)", "Duplicate / move / delete")
            self.footer(summaries[app.column] + " | SELECT edits")
        else:
            self.footer("SELECT: open   BACK: Home   " + ("NOT SAVED" if app.dirty else app.message))

    def filter_label(self, rule):
        event, arg = rule["event"], rule["filter"]
        if event in ("timer", "start"):
            return terms_label(arg, True)
        if event == "variable":
            return arg["name"] + "=" + (terms_label(arg["equals"]) if arg["equals"] else "any")
        if event == "sensor":
            return arg["name"] + {"eq": "=", "ge": ">=", "le": "<=", "change": "~"}[arg["op"]] + terms_label(arg["value"])
        return arg

    def argument_label(self, rule):
        command, arg = rule["command"], rule["arg"]
        if command == "image":
            return "%d frames" % len(arg["frames"])
        if command == "page":
            return "Page " + str(arg)
        if command == "set":
            return arg["name"] + "=" + terms_label(arg["value"])
        return terms_label(arg)

    def draw_run(self, app):
        engine = app.engine
        self.header("RUNNING", "PAGE %d" % (engine.page + 1))
        if app.inspect_run:
            self.text("LIVE VALUES (raw unless marked)", 10, 30, "when", self.w - 20)
            for index, (name, label, unit, _, _) in enumerate(SENSORS):
                x = 10 + (index // 7) * (self.w // 2)
                y = 52 + (index % 7) * 20
                self.text(name + ": " + str(app.sensors.values.get(name, "N/A")), x, y,
                          "ink" if app.sensors.available(name) else "error", self.w // 2 - 12)
        else:
            if app.display_kind == "image":
                cell = 26
                matrix(self.screen, app.display_value, (self.w - 5 * cell) // 2, 44, cell, 4,
                       self.colors["off"], self.colors["do"])
            else:
                # The built-in font is scaled, not a loaded image.
                self.pen("do")
                self.screen.text(str(app.display_value), self.w // 2 - 45, 70, 5)
            self.text("X:%d  Y:%d  Z:%d" % tuple(engine.variables[name] for name in ("X", "Y", "Z")),
                      12, self.h - 53, "muted", self.w - 24)
            self.text("Rules fired: %d" % engine.fired, 12, self.h - 36, "muted", self.w - 24)
        self.footer("BACK: stop / edit   MENU: live sensors")

    def draw_sensors(self, app):
        self.header("Sensors & support")
        options = []
        for name, label, unit, _, _ in SENSORS:
            available = app.sensors.available(name)
            value = str(app.sensors.values.get(name, "-")) if available else "N/A"
            options.append((label + ": " + value, "sensor", name, available))
        options += [("Touch controls", "touch", "touch", app.sensors.available("touch")),
                    ("Case LEDs (output)", "lights", "lights", app.sensors.available("lights"))]
        options += [(label, "warning", label, False) for label, _ in UNSUPPORTED]
        self.list(options, app.sensor_index, 32, self.h - 76)
        if app.sensor_index < len(SENSORS):
            name, _, unit, _, _ = SENSORS[app.sensor_index]
            reason = unit if app.sensors.available(name) else app.sensors.errors.get(
                "imu" if name in ("ax", "ay", "az", "gx", "gy", "gz", "tilt_x", "tilt_y") else
                "light" if name == "light_level" else name, "API unavailable")
        elif app.sensor_index < len(SENSORS) + 2:
            reason = "Logical touch events" if app.sensor_index == len(SENSORS) else "Four white LEDs, 0..100%"
        else:
            reason = UNSUPPORTED[app.sensor_index - len(SENSORS) - 2][1]
        self.wrap(reason, 10, self.h - 61, self.w - 20, "when")
        self.footer("Arrows: inspect   BACK: Home")
