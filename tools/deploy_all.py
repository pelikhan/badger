#!/usr/bin/env python3
"""Preview or deploy every collection app to a mounted BADGER volume."""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def find_skill_scripts(explicit: Path | None, repo_root: Path) -> Path:
    candidates = (
        [explicit.expanduser()]
        if explicit is not None
        else [
            repo_root / ".github/skills/hyb26-skill/scripts",
            Path.home() / ".copilot/skills/hyb26-skill/scripts",
        ]
    )
    for candidate in candidates:
        if all(
            (candidate / name).is_file()
            for name in ("deploy_app.py", "validate_app.py", "skill_paths.py")
        ):
            return candidate.resolve()
    raise FileNotFoundError(
        "HYB26 deployment tools not found. Install hyb26-skill or pass "
        "--skill-scripts /path/to/hyb26-skill/scripts."
    )


def load_deployer(scripts: Path):
    spec = importlib.util.spec_from_file_location(
        "collection_badge_deployer", scripts / "deploy_app.py"
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load HYB26 deploy_app.py")
    module = importlib.util.module_from_spec(spec)
    # The upstream tools import sibling helpers, including during validation.
    sys.path.insert(0, str(scripts))
    try:
        spec.loader.exec_module(module)
        module.load_validator()
    finally:
        sys.path.pop(0)
    return module


def discover_apps(repo_root: Path) -> list[Path]:
    apps_dir = repo_root / "badge/apps"
    if apps_dir.is_symlink():
        raise ValueError(f"Refusing symlinked apps directory: {apps_dir}")
    apps = []
    for app in sorted(apps_dir.iterdir()):
        if app.name.startswith(".") or app.name == "__pycache__":
            continue
        if app.is_symlink():
            raise ValueError(f"Refusing symlinked app: {app}")
        if not app.is_dir():
            continue
        if (
            not re.fullmatch(r"[a-z][a-z0-9_-]*", app.name)
            or app.name in {"menu", "startup"}
        ):
            raise ValueError(f"Refusing unsafe or reserved app name: {app.name}")
        if not (app / "__init__.py").is_file():
            raise ValueError(f"Missing app entry point: {app / '__init__.py'}")
        for path in app.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"Refusing symlink in app: {path}")
            if path.name.casefold() == "icon.png":
                raise ValueError(f"Remove forbidden icon.png before deploying: {path}")
        apps.append(app)
    if not apps:
        raise ValueError(f"No apps found in {apps_dir}")
    return apps


def check_destination(mount: Path, apps: list[Path], deployer) -> None:
    if (
        any(part.upper() == "RP2350" for part in mount.parts)
        or (mount / "INFO_UF2.TXT").exists()
    ):
        raise ValueError("RP2350 is the firmware bootloader, not a BADGER app volume.")
    for path in (
        mount / "system",
        mount / "apps",
        mount / "system/apps",
        mount / ".badge-app-staging",
        mount / ".badge-app-backups",
    ):
        if path.is_symlink():
            raise ValueError(f"Refusing symlink on badge volume: {path}")
    destination = deployer.find_apps_dir(mount)
    for app in apps:
        target = destination / app.name
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise ValueError(f"App destination is not a regular directory: {target}")
        staging = mount / ".badge-app-staging" / app.name
        if staging.exists() or staging.is_symlink():
            raise ValueError(
                f"Previous deployment staging exists; inspect it before retrying: {staging}"
            )


def deploy_all(
    repo_root: Path,
    deployer,
    mount: Path,
    *,
    write: bool = False,
    replace: bool = False,
) -> None:
    apps = discover_apps(repo_root)
    check_destination(mount, apps, deployer)
    print(f"Preflighting {len(apps)} apps (no files written).", flush=True)
    for app in apps:
        deployer.deploy(app, mount, False, replace, repo_root)
    if not write:
        print("All apps passed preflight. Re-run with --write to deploy.")
        return
    print("Preflight passed for every app; starting sequential deployment.", flush=True)
    for app in apps:
        try:
            deployer.deploy(app, mount, True, replace, repo_root)
        except (RuntimeError, OSError, ValueError) as error:
            raise RuntimeError(
                f"Deployment stopped at {app.name}; earlier apps may already be "
                f"updated. Keep BADGER mounted and inspect before retrying: {error}"
            ) from error
    print(
        f"Deployed {len(apps)} apps. Safely eject with the HYB26 "
        "badge_serial.py eject tool before opening them."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mount", type=Path, help="BADGER volume (auto-detected if omitted)")
    parser.add_argument("--write", action="store_true", help="Copy files after all preflights pass")
    parser.add_argument("--replace", action="store_true", help="Back up and replace existing apps")
    parser.add_argument("--skill-scripts", type=Path, help="HYB26 skill scripts directory")
    args = parser.parse_args(argv)
    try:
        deployer = load_deployer(find_skill_scripts(args.skill_scripts, ROOT))
        mount = deployer.find_mount(args.mount)
        deploy_all(ROOT, deployer, mount, write=args.write, replace=args.replace)
    except (RuntimeError, OSError, ValueError, ImportError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
