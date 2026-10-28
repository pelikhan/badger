#!/usr/bin/env python3
"""Package only this app for the official badge simulator; never access USB."""

import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "badge/apps/microcode"


def sources():
    return {path.name: path.read_text() for path in sorted(APP.glob("*.py"))}


def project():
    return {
        "name": "microcode", "sourceName": "microcode", "origin": "new",
        "activePath": "__init__.py",
        "files": [{"path": name, "text": text} for name, text in sources().items()],
    }


def bundle():
    files = sources()
    entry = files.pop("__init__.py")
    return (
        '"""Generated single-file loader for the official U26 simulator.\n'
        'Set the simulator app name to microcode before running.\n'
        'The embedded files are unchanged copies of the repository app.\n'
        '"""\nimport os\n'
        '_root = "/system/apps/microcode"\n'
        'os.chdir(_root)\n'
        "_sources = " + repr(files) + "\n"
        "for _name, _text in _sources.items():\n"
        '    with open(_name, "w") as _file:\n'
        "        _file.write(_text)\n"
        "del _sources, _name, _text, _file\n"
        "exec(compile(" + repr(entry) + ', _root + "/__init__.py", "exec"))\n'
    )


def simulation_request():
    files = sources()
    # Match the official editor's newer BACK/MENU and browser-motion adapter.
    # This shim is simulator-only; the installed badge app never sees it.
    bootstrap = '''
import editor_support, builtins, js, json, os, sys, errno
print("SIMULATOR_ENV", sys.platform, "ENOENT", errno.ENOENT)
class _Action:
    def __init__(self, name):
        self.name = name
builtins.BUTTON_BACK = _Action("BACK")
builtins.BUTTON_MENU = _Action("MENU")
class _Motion:
    def get_readings(self):
        reading = js.sim_motion()
        if reading is None:
            return (0, 16384 if badge._upside_down else -16384, 0, 0, 0, 0)
        return tuple(int(reading[i]) for i in range(6))
    def double_tap_detected(self):
        return False
badge._imu = _Motion()
badge._imu_readings = badge._imu.get_readings()
_native_run = run
_last_state = None
def _sim_run(update):
    def observed():
        global _last_state
        update()
        state = {
            "mode": app.mode, "page": app.page, "row": app.row,
            "column": app.column, "home_index": app.home_index,
            "pick_index": app.pick_index, "pick_title": app.pick_title,
            "options": [item[0] for item in app.options],
            "error": app.error, "dirty": app.dirty,
            "display_kind": app.display_kind, "display": app.display_value,
            "variables": app.engine.variables if app.engine else {},
            "active_page": app.engine.page if app.engine else None,
            "rules": [len(page) for page in app.program["pages"]],
            "values": sensors.values, "capabilities": sensors.capabilities,
        }
        text = json.dumps(state)
        if text != _last_state:
            print("MICROCODE_STATE " + text)
            _last_state = text
    return _native_run(observed)
run = _sim_run
os.chdir("/system/apps/microcode")
sys.path.insert(0, "/system/apps/microcode")
'''
    entry = files["__init__.py"]
    return {
        "program": bootstrap + "\nexec(compile(" + repr(entry) +
                   ', "/system/apps/microcode/__init__.py", "exec"))\n',
        "files": [{"name": "/system/apps/microcode/" + name, "content": text}
                  for name, text in files.items()],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, help="Write a generated single-file Python loader")
    parser.add_argument("--serve", action="store_true", help="Serve only app source JSON on loopback")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    if args.bundle:
        args.bundle.write_text(bundle())
        print(args.bundle.resolve(), flush=True)
    if not args.serve:
        if not args.bundle:
            parser.error("Choose --bundle or --serve")
        return
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/project.json":
                content, kind = json.dumps(project()).encode(), "application/json"
            elif self.path == "/request.json":
                content, kind = json.dumps(simulation_request()).encode(), "application/json"
            elif self.path == "/bundle.py":
                content, kind = bundle().encode(), "text/x-python"
            elif self.path == "/":
                content = (Path(__file__).parent / "simulator.html").read_bytes()
                kind = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Access-Control-Allow-Origin", "https://badger.github.io")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "https://badger.github.io")
            self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
            self.send_header("Access-Control-Allow-Private-Network", "true")
            self.end_headers()

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print("http://127.0.0.1:%d/" % server.server_port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
