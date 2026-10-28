"""Small atomic documents; unlike firmware State, failures are not swallowed."""

import json
import os
import errno
import sys

from model import validate

MAX_BYTES = 32768


def missing(error):
    code = error.args[0] if error.args else None
    # The U26 WASM filesystem leaks WASI errno 44; its errno module still says 2.
    return code == errno.ENOENT or (sys.platform == "webassembly" and code == 44)


class Store:
    def __init__(self, root="/state"):
        self.root = root

    def path(self, slot):
        if slot not in ("draft", "1", "2", "3"):
            raise ValueError("Unknown save slot")
        return self.root + "/microcode_" + slot + ".json"

    def load(self, slot):
        path = self.path(slot)
        try:
            with open(path, "r") as source:
                text = source.read(MAX_BYTES + 1)
        except OSError as error:
            if missing(error):
                return None
            raise
        if len(text) > MAX_BYTES:
            raise ValueError("Saved program too large")
        return validate(json.loads(text))

    def save(self, slot, program):
        text = json.dumps(validate(program))
        if len(text) > MAX_BYTES:
            raise ValueError("Program too large to save")
        path = self.path(slot)
        try:
            os.stat(self.root)
        except OSError as error:
            if not missing(error):
                raise
            os.mkdir(self.root)
        # LittleFS rename is atomic. Until rename succeeds, the last save survives.
        with open(path + ".tmp", "w") as target:
            target.write(text)
            target.flush()
        os.rename(path + ".tmp", path)
