"""Cooperative event interpreter. All work per frame is bounded."""

import random

from model import clone, validate, MAX_NUMBER

MAX_EVENTS = 64
EVENTS_PER_FRAME = 16


class Unavailable(ValueError):
    pass


class Engine:
    def __init__(self, program, output, available=None, randint=None):
        self.program = clone(validate(program))
        self.output = output
        self.available = available
        self.randint = randint or random.randint
        self.variables = {"X": 0, "Y": 0, "Z": 0}
        self.values = {}
        self.page = 0
        self.now = 0
        self.queue = []
        self.jobs = {}
        self.due = {}
        self.latches = {}
        self.sensor_before = {}
        self.running = False
        self.error = ""
        self.fired = 0
        self.last_rule = None
        self.generation = 0

    def start(self, values=None):
        self.values = dict(values or {})
        self.variables = {"X": 0, "Y": 0, "Z": 0}
        self.now = 0
        self.fired = 0
        self.error = ""
        self.running = True
        self.switch(0)

    def stop(self):
        self.running = False
        self.queue = []
        self.jobs = {}
        self.due = {}
        try:
            self.output("lights", 0)
        except (OSError, ValueError) as error:
            self.error = "LED cleanup: " + str(error)

    def fail(self, message):
        self.stop()
        self.error = message

    def emit(self, kind, value=None, number=None):
        if len(self.queue) >= MAX_EVENTS:
            self.fail("Event queue full; simplify feedback rules")
            return
        self.queue.append((kind, value, number))

    def evaluate(self, terms, default=0, event_value=None, times=False):
        if not terms:
            return default
        total = 0
        for term in terms:
            if type(term) is int:
                total += term
            elif term in self.variables:
                total += self.variables[term]
            elif term == "random":
                total += self.randint(1, 5)
            elif term == "random_time" and times:
                total += self.randint(0, 1000)
            elif term == "event":
                if event_value is None:
                    raise Unavailable("This event has no numeric value")
                total += event_value
            elif term in self.values:
                total += self.values[term]
            else:
                raise Unavailable("Sensor unavailable: " + str(term))
        return int(total)

    def duration(self, terms, default):
        return max(1, self.evaluate(terms, default, times=True))

    def switch(self, page):
        self.page = page
        self.generation += 1
        self.queue = []
        self.jobs = {}
        self.due = {}
        self.latches = {}
        self.sensor_before = {}
        for index, rule in enumerate(self.program["pages"][page]):
            if rule["event"] in ("start", "timer"):
                delay = self.duration(rule["filter"], 0 if rule["event"] == "start" else 250)
                self.due[index] = self.now + (0 if rule["event"] == "start" and not rule["filter"] else delay)

    def tick(self, dt, events=(), values=None):
        if not self.running:
            return
        if values is not None:
            self.values = dict(values)
        self.now += max(0, int(dt))
        try:
            self.advance_jobs()
            for kind, value, number in events:
                self.emit(kind, value, number)
            # A timer expiry is one event for that timer rule, not all timers.
            for index in sorted(self.due):
                if self.now >= self.due[index]:
                    self.emit("due", index)
                    rule = self.program["pages"][self.page][index]
                    self.due[index] = (self.now + self.duration(rule["filter"], 250)
                                       if rule["event"] == "timer" else float("inf"))
            for _ in range(EVENTS_PER_FRAME):
                if not self.queue or not self.running:
                    break
                self.dispatch(self.queue.pop(0))
        except (Unavailable, OSError, ValueError) as error:
            self.fail(str(error))

    def matches(self, index, rule, event):
        kind, value, number = event
        when, param = rule["event"], rule["filter"]
        if kind == "due":
            return index == value
        if when == "variable" and kind == "variable":
            return (param["name"] == value and
                    (not param["equals"] or number == self.evaluate(param["equals"], event_value=number)))
        if when == "sensor" and kind == "sensor" and param["name"] == value:
            if param["op"] == "change":
                old = self.sensor_before.get(index)
                self.sensor_before[index] = number
                return old is not None and old != number
            threshold = self.evaluate(param["value"], event_value=number)
            test = {"eq": number == threshold, "ge": number >= threshold,
                    "le": number <= threshold}[param["op"]]
            previous = self.latches.get(index, False)
            self.latches[index] = test
            return test and not previous
        return kind == when and value == param

    def dispatch(self, event):
        generation = self.generation
        for index, rule in enumerate(self.program["pages"][self.page]):
            if self.matches(index, rule, event):
                self.execute(index, rule, event[2])
            if generation != self.generation or not self.running:
                break

    def require(self, feature):
        if self.available is not None and not self.available(feature):
            raise Unavailable("Unavailable: " + feature)

    def execute(self, index, rule, event_value):
        self.last_rule = index
        self.fired += 1
        command, arg = rule["command"], rule["arg"]
        if command in ("image", "number"):
            self.jobs.pop("display", None)
        if command == "image":
            repeats = (None if not rule["repeat"] else
                       max(0, min(999, self.evaluate(rule["repeat"], event_value=event_value))))
            if repeats == 0:
                return
            self.output("image", arg["frames"][0])
            self.jobs["display"] = {
                "frames": arg["frames"], "index": 0, "ms": arg["ms"],
                "next": self.now + arg["ms"], "left": repeats,
            }
        elif command == "number":
            value = self.evaluate(arg, event_value=event_value)
            self.output("number", max(0, min(99, value)))
        elif command == "set":
            value = max(-MAX_NUMBER, min(MAX_NUMBER, self.evaluate(arg["value"], event_value=event_value)))
            if value != self.variables[arg["name"]]:
                self.variables[arg["name"]] = value
                self.emit("variable", arg["name"], value)
        elif command == "page":
            self.switch(arg - 1)
        elif command == "lights":
            self.require("lights")
            repeats = (None if not rule["repeat"] else
                       max(0, min(999, self.evaluate(rule["repeat"], event_value=event_value))))
            self.jobs.pop("lights", None)
            if repeats == 0:
                return
            value = max(0, min(100, self.evaluate(arg, event_value=event_value)))
            self.output("lights", value)
            if repeats != 1:
                self.jobs["lights"] = {"on": True, "value": value,
                                       "next": self.now + 250, "left": repeats}

    def advance_jobs(self):
        for resource in tuple(self.jobs):
            job = self.jobs[resource]
            if self.now < job["next"]:
                continue
            if resource == "display":
                job["index"] += 1
                if job["index"] == len(job["frames"]):
                    job["index"] = 0
                    if job["left"] is not None:
                        job["left"] -= 1
                        if job["left"] <= 0:
                            del self.jobs[resource]
                            continue
                self.output("image", job["frames"][job["index"]])
                job["next"] = self.now + job["ms"]
            else:
                job["on"] = not job["on"]
                self.output("lights", job["value"] if job["on"] else 0)
                if not job["on"] and job["left"] is not None:
                    job["left"] -= 1
                    if job["left"] <= 0:
                        del self.jobs[resource]
                        continue
                job["next"] = self.now + 250

    def missing(self):
        missing = []
        for page in self.program["pages"]:
            for rule in page:
                features = []
                if rule["event"] == "move":
                    features.append("imu")
                elif rule["event"] in ("touch", "untouch"):
                    features.append("touch")
                elif rule["event"] == "sensor":
                    features.append(rule["filter"]["name"])
                if rule["command"] == "lights":
                    features.append("lights")
                terms = []
                if rule["command"] in ("number", "lights"):
                    terms += rule["arg"]
                elif rule["command"] == "set":
                    terms += rule["arg"]["value"]
                terms += rule["repeat"]
                if rule["event"] == "variable":
                    terms += rule["filter"]["equals"]
                elif rule["event"] == "sensor":
                    terms += rule["filter"]["value"]
                features += [term for term in terms if isinstance(term, str)
                             and term not in ("X", "Y", "Z", "random", "event")]
                for feature in features:
                    if self.available is not None and not self.available(feature) and feature not in missing:
                        missing.append(feature)
        return missing
