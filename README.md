# Badger

Apps for the GitHub Universe 2026 badge.

## Apps

| App | Description |
| --- | --- |
| [Quantum Diffusion](badge/apps/quantum_diffusion/) | Full-screen waves triggered by motion and capacitive touches, with a cycling GitHub brand-color palette. |

Quantum Diffusion was migrated from the Agent skills session's Quantum
performance worktree (`pelikhan/fresh-repo`, commit `c2b2e76`), preserving its
source, documentation, tests, and benchmark.

## Install

Copy an app's complete directory from `badge/apps/` into `apps/` on the
badge's **BADGER** USB Disk Mode volume, then safely eject it. Never copy
apps to the RP2350 firmware drive. Keep the directory name unchanged:
Quantum Diffusion expects `/system/apps/quantum_diffusion`.

Read each app's README for controls and firmware requirements. Quantum
Diffusion requires 2026 RP2350 firmware with Viper, soft timer callbacks,
a writable RGB565 framebuffer, and the firmware's `_poll_imu` hook. It
intentionally uses the launcher's default icon. No credentials or network
access are needed.

## Development

Run the host-side regression suite from the repository root:

```sh
python3 -m unittest discover -s tests -v
```

Run the desktop solver benchmark:

```sh
python3 tests/benchmark_quantum_diffusion.py
```

Host-side checks do not prove badge behavior or performance. The
[app README](badge/apps/quantum_diffusion/README.md) includes static validation
commands, physical-badge benchmarks, and the remaining gesture, light,
orientation, HOME/reopen, and visual checks. Its historical hardware
measurements were recorded in the source session, not rerun by this migration.
