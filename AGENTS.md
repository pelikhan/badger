# Badger app collection

This repository is a collection of independent MicroPython apps for the
GitHub Universe 2026 Badger device. It is not a firmware or launcher fork.
Keep each app self-contained and deployable without installing the rest of
the collection. Do not add starter apps or infrastructure unless requested.

## Load the badge skill first

Before building, porting, debugging, validating, or deploying an app, load
`hyb26-skill` using the client's skill loader. The canonical skill, including
its installation instructions, scripts, and references, lives at
<https://github.com/badger/home/tree/main/.github/skills/hyb26-skill>.
If the skill is unavailable, read its `SKILL.md` and the relevant references
there before proceeding; do not assume its scripts exist in this repository.

Use these sources of truth in order, and report disagreements:

1. The connected badge's actual firmware and files.
2. The current [upstream app contract](https://github.com/badger/home/blob/main/badge/AGENTS.md),
   [hardware reference](https://github.com/badger/home/blob/main/hardware/README.md),
   [USB serial guide](https://github.com/badger/home/blob/main/hardware/USB_SERIAL.md),
   and [2026 reference apps](https://github.com/badger/home/tree/main/badge/apps).
3. The [browser simulator](https://badger.github.io/editor) and its documentation.

Read the hardware reference before raw GPIO, I2C, ADC, IR, power, wireless,
or interrupt work, and the USB guide before connecting or copying files.
Never use Universe 2025 `badge25/` APIs or assume generic Tufty 2350 examples
match the custom Badger wiring.

## Collection layout

Create apps under this layout as needed:

```text
badge/
└── apps/
    └── my_app/
        ├── __init__.py
        ├── README.md
        └── assets/       # optional app-owned images, fonts, and data
```

- Use lowercase path-safe app names, preferably `snake_case`. Do not use the
  reserved names `menu` or `startup`.
- Each app needs its own entry point, documentation, and assets. Avoid
  dependencies on sibling apps or new shared runtime libraries.
- Never create, include, load, or deploy `icon.png` in apps. Remove any
  scaffold-generated `icon.png`; custom app icons are not required.
- The README's first paragraph after its title must be one plain-text
  description sentence, 10-200 characters long, ending in punctuation.
  Follow it with controls, usage, hardware/network requirements, configuration,
  and relevant limitations. Document any required secret names, never values.
- Repository `badge/` maps to device `/system/`; an app runs from
  `/system/apps/<name>`. This collection does not need copies of upstream
  `main.py`, system apps, firmware, or shared system assets.
- Keep changes scoped to the requested apps and their documentation. Do not
  modify unrelated apps or device system files.

## Firmware and app conventions

Use the 2026 runtime-provided globals: `badge`, `screen`, `image`, `font`,
`color`, `shape`, `brush`, `vec2`, `rect`, `mat3`, `run`, `BUTTON_*`, `HIRES`,
and `VSYNC`. Do not `import badge` or import legacy graphics APIs from
`badgeware`. Standard MicroPython modules may be imported normally;
persistence uses `from badgeware import State`.

- Set the app directory with `os.chdir("/system/apps/<name>")`; add that
  directory to `sys.path` before importing app-local modules when needed.
  Keep asset paths app-relative and avoid host-specific paths.
- Define `update()` and call `run(update)` at module level, not behind an
  `if __name__ == "__main__"` guard. Ordinary apps should not launch other
  apps. Use a module-level `on_exit()` when resources need cleanup.
- Use logical `BUTTON_UP`, `BUTTON_DOWN`, `BUTTON_LEFT`, `BUTTON_RIGHT`,
  `BUTTON_SELECT`, `BUTTON_BACK`, and `BUTTON_MENU` through
  `badge.pressed/held/released/touched`. HOME belongs to the launcher.
  Do not remap controls based on physical orientation or old A/B/C labels.
- Keep frames short and nonblocking. Use `badge.ticks` and
  `badge.ticks_delta` for timing and frame-rate-independent movement.
  Bound caches, histories, queues, and other growing collections.
- Size layouts with `screen.width` and `screen.height`: default 160x120,
  or 320x240 with `badge.mode(HIRES | VSYNC)`. Check both orientations.
- Load images, fonts, and static graphics once rather than every frame.
  Prefer optimized paletted PNGs and sprite sheets; memory is constrained.
- Give each app a unique `State` key, defaults, and validation of loaded
  values. State belongs in `/state`, not the read-only `/system` tree.
  Save after meaningful changes, not every frame.
- Never commit or deploy credentials, a filled `secrets.py`, generated state,
  device backups, or host caches. Keep configuration out of app source.
  Surface unexpected failures; handle expected missing files narrowly.

## Scaffold and validate

Use Python 3.10+ and the skill's existing tools rather than inventing a
desktop runtime or new build system. Run them from this repository's root.
For a personal skill installation on macOS/Linux:

```bash
scripts="$HOME/.copilot/skills/hyb26-skill/scripts"
python3 "$scripts/scaffold_app.py" my_app --title "My App" --description "My App displays a friendly greeting."
python3 "$scripts/validate_app.py" badge/apps/my_app
python3 "$scripts/validate_app.py" badge/apps/my_app --target emulator
```

If the skill is installed elsewhere, use its actual scripts directory
(for example `.github/skills/hyb26-skill/scripts` only if present).
On Windows, use the corresponding PowerShell path and `python`.
Remove any scaffold-generated `icon.png` before validation. If upstream
tools require it, report the conflict rather than adding the file.
Validate every changed app; static validation does not execute the app.
Add focused tests for app logic where practical without requiring firmware
globals to exist on desktop Python.

## Simulator and physical deployment

The skill prefers physical testing and treats the simulator as optional;
upstream `badge/AGENTS.md` asks Copilot clients to preview in a Badge Simulator
canvas first. Use that canvas when the client actually exposes it, or use
the browser editor for a convenient preview. Do not invent unavailable canvas
IDs or treat simulation as hardware proof. The simulator cannot verify
sockets, Bluetooth, IR, camera, or `.mpy` behavior.

Deploy only when requested. Use the skill's USB workflow and discovery tools:

- Install `mpremote` if needed and discover the port rather than hard-coding
  it. Only one program may own the serial port; never parallelize serial
  operations. Connecting can interrupt or soft-reset the running app.
- Preview `deploy_app.py badge/apps/<name>` before adding `--write`.
  For an existing app, use the documented `--replace` backup workflow.
- `/system` is normally read-only over serial. Enter USB Disk Mode and copy
  only the requested app to `BADGER/apps` (or `BADGER/system/apps` on some
  firmware). Never copy apps to the `RP2350` bootloader drive.
- Do not deploy `icon.png` or patch the system menu to support custom app
  icons. Leave launcher icon behavior unchanged.
- Flush and eject with `badge_serial.py eject`, wait for the badge to return,
  then check normal launch, every screen and control, both orientations,
  HOME/re-open, persistence, and relevant failure cases.

For app delivery, distinguish static validation, simulator checks, deployment,
and on-badge checks actually performed. If hardware is unavailable, explicitly
state that physical verification is outstanding and provide an app-specific
hardware checklist. Never claim hardware verification from static or
simulator results alone.
