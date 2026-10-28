"""Package the image-free badge app for installation and the real simulator."""

import argparse
import json
from base64 import b64encode
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

APP = Path(__file__).resolve().parents[1] / "badge/apps/factory_mindset"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    files = []
    for path in sorted(APP.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        if path.suffix not in (".py", ".md"):
            raise ValueError("Only code and documentation may ship: " + path.name)
        files.append({"path": path.relative_to(APP).as_posix(),
                      "base64": b64encode(path.read_bytes()).decode("ascii"),
                      "text": path.read_text()})
    (args.output / "project.json").write_text(json.dumps({
        "name": "factory_mindset", "sourceName": "factory_mindset",
        "origin": "new", "activePath": "__init__.py", "files": files}))
    package = args.output / "factory-mindset-badge.zip"
    with ZipFile(package, "w", ZIP_DEFLATED) as archive:
        for file in files:
            archive.write(APP / file["path"], "apps/factory_mindset/" + file["path"])
    with ZipFile(package) as archive:
        if archive.testzip() is not None or len(archive.namelist()) != len(files):
            raise ValueError("Install archive verification failed")
    print("Packaged %d code/documentation files; no images or icon." % len(files))


if __name__ == "__main__":
    main()
