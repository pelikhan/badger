"""Bounded, JSON-compatible MicroCode language; independent of badge firmware."""

VERSION = 1
PAGES = 5
MAX_RULES = 8
MAX_TERMS = 8
MAX_FRAMES = 8
MAX_NUMBER = 65535
BUTTONS = ("select", "right", "left", "up", "down")
VARIABLES = ("X", "Y", "Z")
GESTURES = ("shake", "left", "right", "up", "down", "face_up", "face_down")
SENSOR_IDS = (
    "light", "light_level", "battery", "charging", "inverted",
    "ax", "ay", "az", "gx", "gy", "gz", "tilt_x", "tilt_y",
)
EVENTS = ("start", "timer", "press", "release", "touch", "untouch",
          "move", "variable", "sensor")
COMMANDS = ("image", "number", "set", "page", "lights")

# Original 5x5 designs, encoded in row-major bits. No image files are loaded.
def pixels(rows):
    return int("".join(rows), 2)


HAPPY = pixels(("00000", "01010", "00000", "10001", "01110"))
SAD = pixels(("00000", "01010", "00000", "01110", "10001"))
HEART = pixels(("01010", "11111", "11111", "01110", "00100"))
SMALL_HEART = pixels(("00000", "01010", "01110", "00100", "00000"))
DIAMOND = pixels(("00100", "01010", "10001", "01010", "00100"))
SKULL = pixels(("01110", "10101", "11111", "01110", "01010"))
PICTURES = (HAPPY, SAD, HEART, SMALL_HEART, DIAMOND, SKULL, 0)
PICTURE_NAMES = ("Happy", "Sad", "Heart", "Small heart", "Diamond", "Skull", "Blank")


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def expression(value, times=False):
    if not isinstance(value, list) or len(value) > MAX_TERMS:
        return False
    for term in value:
        if integer(term, 0 if times else -MAX_NUMBER, 60000 if times else MAX_NUMBER):
            continue
        if not isinstance(term, str):
            return False
        if times:
            if term != "random_time":
                return False
        elif term not in VARIABLES + SENSOR_IDS + ("random", "event"):
            return False
    return True


def new_rule(event="start", command="image"):
    params = {
        "start": [], "timer": [], "press": "select", "release": "select",
        "touch": "select", "untouch": "select", "move": "shake",
        "variable": {"name": "X", "equals": []},
        "sensor": {"name": "light_level", "op": "eq", "value": [1]},
    }
    args = {
        "image": {"frames": [HAPPY], "ms": 250},
        "number": [], "set": {"name": "X", "value": []},
        "page": 1, "lights": [0],
    }
    return {"event": event, "filter": params[event], "command": command,
            "arg": args[command], "repeat": [1]}


def blank():
    return {"version": VERSION, "pages": [[] for _ in range(PAGES)]}


def clone(value):
    if isinstance(value, dict):
        return {key: clone(item) for key, item in value.items()}
    if isinstance(value, list):
        return [clone(item) for item in value]
    return value


def validate(program):
    """Reject the entire document rather than silently changing its meaning."""
    if not isinstance(program, dict) or set(program) != {"version", "pages"}:
        raise ValueError("Invalid program document")
    if type(program["version"]) is not int or program["version"] != VERSION:
        raise ValueError("Unsupported program version")
    pages = program["pages"]
    if not isinstance(pages, list) or len(pages) != PAGES:
        raise ValueError("Program needs five pages")
    for page in pages:
        if not isinstance(page, list) or len(page) > MAX_RULES:
            raise ValueError("Too many rules")
        for rule in page:
            validate_rule(rule)
    return program


def validate_rule(rule):
    if not isinstance(rule, dict) or set(rule) != {
            "event", "filter", "command", "arg", "repeat"}:
        raise ValueError("Invalid rule")
    event, param = rule["event"], rule["filter"]
    command, arg = rule["command"], rule["arg"]
    if event not in EVENTS or command not in COMMANDS:
        raise ValueError("Unknown tile")
    valid = False
    if event in ("start", "timer"):
        valid = expression(param, times=True)
    elif event in ("press", "release", "touch", "untouch"):
        valid = isinstance(param, str) and param in BUTTONS
    elif event == "move":
        valid = isinstance(param, str) and param in GESTURES
    elif event == "variable":
        valid = (isinstance(param, dict) and set(param) == {"name", "equals"}
                 and param["name"] in VARIABLES and expression(param["equals"]))
    elif event == "sensor":
        valid = (isinstance(param, dict) and set(param) == {"name", "op", "value"}
                 and param["name"] in SENSOR_IDS and param["op"] in ("eq", "ge", "le", "change")
                 and expression(param["value"]))
    if not valid:
        raise ValueError("Invalid WHEN parameters")
    valid = False
    if command == "image":
        valid = (isinstance(arg, dict) and set(arg) == {"frames", "ms"}
                 and isinstance(arg["frames"], list) and 1 <= len(arg["frames"]) <= MAX_FRAMES
                 and all(integer(frame, 0, (1 << 25) - 1) for frame in arg["frames"])
                 and integer(arg["ms"], 50, 5000))
    elif command in ("number", "lights"):
        valid = expression(arg)
    elif command == "set":
        valid = (isinstance(arg, dict) and set(arg) == {"name", "value"}
                 and arg["name"] in VARIABLES and expression(arg["value"]))
    elif command == "page":
        valid = integer(arg, 1, PAGES)
    if not valid:
        raise ValueError("Invalid DO parameters")
    if not expression(rule["repeat"]):
        raise ValueError("Invalid repeat count")
    if command not in ("image", "lights") and rule["repeat"] != [1]:
        raise ValueError("Repeat is only for images and lights")


def example(name):
    program = blank()
    page = program["pages"][0]
    if name == "First program":
        page.append(new_rule("press"))
    elif name == "Smiley buttons":
        page.extend((new_rule("press"), new_rule("press")))
        page[1]["filter"] = "right"
        page[1]["arg"]["frames"] = [SAD]
    elif name == "Flashing heart":
        rule = new_rule("start")
        rule["arg"]["frames"] = [HEART, SMALL_HEART]
        rule["repeat"] = []
        page.append(rule)
    elif name in ("Counter", "Shake counter"):
        rule = new_rule("press" if name == "Counter" else "move", "set")
        rule["arg"]["value"] = ["X", 1]
        page.append(rule)
        rule = new_rule("variable", "number")
        rule["arg"] = ["X"]
        page.append(rule)
    elif name == "Tilt faces":
        for gesture, mask in (("left", SAD), ("right", HAPPY)):
            rule = new_rule("move")
            rule["filter"] = gesture
            rule["arg"]["frames"] = [mask]
            page.append(rule)
    elif name == "Light meter":
        rule = new_rule("timer", "number")
        rule["arg"] = ["light_level"]
        page.append(rule)
    elif name == "Dice":
        rule = new_rule("move", "set")
        rule["arg"]["value"] = ["random"]
        page.append(rule)
        rule = new_rule("variable", "number")
        rule["arg"] = ["X"]
        page.append(rule)
    elif name == "Hot potato":
        rule = new_rule("timer", "page")
        rule["filter"], rule["arg"] = [5000, "random_time"], 2
        page.append(rule)
        rule = new_rule("timer")
        rule["arg"]["frames"] = [SMALL_HEART, 0]
        page.append(rule)
        rule = new_rule()
        rule["arg"]["frames"] = [SKULL]
        program["pages"][1].append(rule)
    else:
        raise ValueError("Unknown example")
    return program


EXAMPLES = ("First program", "Smiley buttons", "Flashing heart", "Counter",
            "Shake counter", "Tilt faces", "Light meter", "Dice", "Hot potato")
