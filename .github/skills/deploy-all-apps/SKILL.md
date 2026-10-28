---
name: deploy-all-apps
description: Preview and safely deploy every app in this Badger collection to a physical badge, using the HYB26 USB Disk Mode tools.
---

# Deploy all apps

Use this skill when asked to install or update the whole collection on a badge.
Do not deploy merely because the user asks to change this script or skill.

1. Load `hyb26-skill` first. Read its USB serial reference and the current
   upstream `hardware/USB_SERIAL.md` before connecting. Follow this repository's
   `AGENTS.md`: never add or deploy `icon.png`, patch the menu, or copy firmware.
   The upstream skill recommends icons and menu patches; those recommendations
   do not apply to this collection. If validation requires an icon, report the
   conflict instead of adding one or bypassing validation.
2. Locate that skill's actual `scripts` directory. The bulk script discovers
   `.github/skills/hyb26-skill/scripts` or
   `~/.copilot/skills/hyb26-skill/scripts`; use `--skill-scripts` for another
   installation. Use Python 3.10+ and run commands from the repository root.
3. If BADGER is not already mounted, discover the device with
   `python3 "$scripts/badge_serial.py" port`, then enter USB Disk Mode with
   `python3 "$scripts/badge_serial.py" disk-mode`. Install `mpremote` only if
   missing. Never hard-code a serial port or run serial operations in parallel;
   connecting interrupts the running app. Alternatively ask the user to
   double-tap RESET or open Mass Storage. Never target the RP2350 drive.
4. Preview the entire batch:

   ```sh
   python3 tools/deploy_all.py --replace
   ```

   `--replace` is needed only for existing apps; it preserves timestamped
   backups. Add `--mount /path/to/BADGER` if mount discovery is ambiguous.
   Review every selected app, destination, validation warning, and file list.
   Any preflight failure prevents the entire batch from starting.
5. Only when deployment is requested and the preview is acceptable, repeat
   the same command with `--write`:

   ```sh
   python3 tools/deploy_all.py --replace --write
   ```

   Deployment is sequential, not a collection-wide transaction. A write failure
   stops the batch; earlier apps may already have been updated. Report the
   failing app, preserve backups/staging for inspection, and do not claim that
   the whole collection was installed. Do not retry blindly or remove backups.
6. After success, flush/eject with `python3 "$scripts/badge_serial.py" eject`
   and wait for the badge to return. Discover its serial port again if needed.
   Verify installed files, then reset out of the REPL for normal menu startup.
   Keep the default launcher icons and leave unrelated device apps and `/state`
   untouched.
7. Report copying separately from physical verification. Exercise each app's
   README checklist: Factory Mindset playback/pause/navigation, MicroCode
   editing/run-stop/save/reopen/sensors, and Quantum Diffusion gestures/light/
   radio synchronization. Check both orientations, HOME/reopen, persistence,
   and relevant failures for all apps. If those checks were not performed,
   explicitly say physical verification is outstanding.

On Windows use `python` and PowerShell's `$scripts` path syntax instead of the
shell examples. The bulk script itself is cross-platform and never connects,
changes device mode, or ejects automatically.
