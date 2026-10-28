# Badger

Apps for the GitHub Universe 2026 badge.

## Apps

| App | Description |
| --- | --- |
| [Factory Mindset](badge/apps/factory_mindset/) | A fifteen-scene animated text/vector story about programming with a phone and a cloud agent. |
| [MicroCode](badge/apps/microcode/) | An offline, icon-based WHEN/DO programming editor with five pages, saved programs, animations, and badge sensors. |
| [Quantum Diffusion](badge/apps/quantum_diffusion/) | Motion/touch waves and no-pairing group firefly synchronization of rhythm and GitHub brand colors. |
| [Tiny Inference](badge/apps/tiny_inference/) | Endless offline poems from a tiny trained int8 recurrent model, with native Cortex-M33 kernels and correctness/performance benchmarks. |

Factory Mindset was migrated from the Home skills session's Factory mindset
slideshow worktree. Quantum Diffusion was migrated from the Agent skills
session's Quantum performance worktree (`pelikhan/fresh-repo`, commit `c2b2e76`), preserving its
source, documentation, tests, and benchmark. MicroCode was migrated from the
Skill library session's MicroCode worktree (`pelikhan/microcode-badge-app`),
preserving its eight Python modules, documentation, tests, and simulator tooling.
Each app is self-contained and can be installed independently; this repository
does not include firmware or a launcher fork.

## Install

Use [Deploy the whole collection](#deploy-the-whole-collection) to install
or update every app with validation and backups. To install just one app,
copy its directory from `badge/apps/` into `apps/` on the badge's **BADGER**
USB Disk Mode volume, then safely eject it. Do not copy `icon.png`,
`secrets.py`, hidden files, caches, or bytecode. Never copy apps to the
RP2350 firmware drive. Keep directory names unchanged:
`factory_mindset`, `microcode`, `quantum_diffusion`, and `tiny_inference` run from their respective
`/system/apps/<name>` paths.

Read each app's README for controls and firmware requirements. Factory Mindset
uses the stock `MonaSans-Medium.af` font and native HIRES text/vector drawing.
Quantum Diffusion requires 2026 RP2350 firmware with Viper, soft timer callbacks,
a writable RGB565 framebuffer, the firmware's `_poll_imu` hook, and
`badgeware.link.radio` for connectionless Bluetooth firefly synchronization. It
does not have a portable simulator fallback. Both apps intentionally use the
launcher's default icon. MicroCode uses the stock `font.sins` font, draws its
icons in code without PNG assets, and also intentionally uses the launcher's
default icon. It saves drafts and three program slots under `/state`; sensor
features require the corresponding firmware APIs. No app needs credentials,
Wi-Fi or internet access.

### Deploy the whole collection

The [bulk deployment script](tools/deploy_all.py) requires Python 3.10+ and
an installed [hyb26-skill](https://github.com/badger/home/tree/main/.github/skills/hyb26-skill).
Run the following commands from this repository root.

**Connect the badge.** Close other serial clients and connect with a
data-capable USB-C cable. Enter USB Disk Mode using the badge's Mass Storage
app or by double-tapping RESET, then wait for the **BADGER** volume.
Alternatively, use the skill's serial tools:

```sh
scripts="$HOME/.copilot/skills/hyb26-skill/scripts"
python3 "$scripts/badge_serial.py" port
python3 "$scripts/badge_serial.py" disk-mode
```

Adjust `scripts` if the skill is installed elsewhere. Serial access requires
`mpremote`; install it with `python3 -m pip install mpremote` if missing.
Connecting can interrupt the running app. Never run serial operations in parallel.

**Preview, then deploy.** Review the selected apps, destination, warnings, and
file lists before running the second command:

```sh
# Dry run: validate all apps without writing to BADGER.
python3 tools/deploy_all.py --replace

# Copy all apps, backing up any installed versions.
python3 tools/deploy_all.py --replace --write
```

| Option | Behavior |
| --- | --- |
| `--write` | Copy files; without this flag, only preview the deployment. |
| `--replace` | Preserve timestamped backups of existing apps before replacing them; omit to refuse overwrites. |
| `--mount /path/to/BADGER` | Select a mounted volume when automatic discovery is ambiguous. |
| `--skill-scripts /path/to/hyb26-skill/scripts` | Use a skill installation outside the default locations. |

The script discovers app directories under `badge/apps/`, validates and
preflights the entire batch before any writes, then delegates sequential
copies to the skill's `deploy_app.py`. Both `BADGER/apps` and
`BADGER/system/apps` are supported. The skill is discovered under
`.github/skills/hyb26-skill/scripts` or `~/.copilot/skills/hyb26-skill/scripts`.

Unrelated installed apps and saved state are left untouched. Icons and
app-local `secrets.py` are rejected; hidden files, caches, and bytecode are
excluded by the skill. No menu patches or firmware changes are performed.
A write failure stops the batch but does not roll back earlier apps; inspect
the reported failure before retrying. Backups remain in `.badge-app-backups`
on BADGER, consume device space, and are never automatically pruned.

**Eject and check the apps.** The script never switches device modes or ejects
automatically. Set `scripts` to the installed skill's scripts directory as
above, then flush and eject:

```sh
python3 "$scripts/badge_serial.py" eject
```

Wait for normal badge startup, then follow each app's README physical
acceptance checklist. Static validation and copying do not establish
on-badge behavior.

On Windows, use `python` instead of `python3`, PowerShell's
`$scripts = "$HOME\.copilot\skills\hyb26-skill\scripts"` assignment, and
`python "$scripts\badge_serial.py" eject` for the eject command. Pass the
BADGER drive path to `--mount` if needed.

To have Copilot perform this workflow, ask **"Deploy all apps to my badge."**
The repository's [deploy-all-apps skill](.github/skills/deploy-all-apps/SKILL.md)
guides discovery, preview, deployment, ejection, and physical checks.

Tiny Inference requires RP2350 ARM MicroPython with inline Thumb assembly and
uses the launcher's default icon. It maps int8 dot products to M33 DSP
instructions without a slow fallback and bundles a 13 KiB trained recurrent
poetry model. It writes ongoing, bounded-memory couplets; MENU opens the
benchmarks. Its README documents training provenance, limited vocabulary,
prospective transformer budgets, native instruction emulation, and outstanding
physical throughput/memory checks. It is not SmolLM or a general-purpose LLM.

## Development

Run the host-side regression suite from the repository root:

```sh
python3 -m unittest discover -s tests -v
```

Package Factory Mindset for badge installation and the MicroPython simulator:

```sh
python3 tools/package_factory.py --output /tmp/factory-mindset-package
```

The output contains an installation ZIP and a simulator `project.json`, without
bundling the firmware's stock font.

Preview MicroCode using the official U26 MicroPython WebAssembly runtime:

```sh
python3 tools/simulator_project.py --serve --port 8000
```

Open <http://127.0.0.1:8000/>. The host preloads the unchanged app into the
official simulator worker; it is not a JavaScript reimplementation. Initial
loading requires internet access. It never connects to USB. Arrow keys move,
Enter/Space selects, K/Backspace goes back, and U opens Menu. Simulated sensor
readings do not verify physical behavior. Drafts and slots persist in this
browser origin's IndexedDB; the host restores only MicroCode's saved documents.

Alternatively, generate a one-file loader:

```sh
python3 tools/simulator_project.py --bundle /tmp/microcode_simulator.py
```

At <https://badger.github.io/simulator/>, choose **New app**, name the app folder
**microcode**, open the generated file with **Open Python file**, then Run.
The loader is for simulation only; install the original app directory on a badge.
Do not select Connect badge or Upload for simulator-only use.

Run the desktop solver benchmark:

```sh
python3 tests/benchmark_quantum_diffusion.py
```

For Tiny Inference, the host suite runs independent math and lifecycle
references. Optional `mpy-cross`/Unicorn tests execute the app's actual compiled
Thumb kernels on an emulated Cortex-M33; see its
[verification instructions](badge/apps/tiny_inference/README.md#host-verification).
Native throughput measurements run on the badge, not in the browser simulator.

See [AGENTS.md](AGENTS.md) for collection conventions and the HYB26 skill's
scaffolding, validation, and deployment workflow.

Host-side checks do not prove badge behavior or performance. The
[Factory Mindset README](badge/apps/factory_mindset/README.md) documents playback,
simulator support, and its physical acceptance checklist. The
[MicroCode README](badge/apps/microcode/README.md) documents controls, language
semantics, persistence, sensor limitations, and its physical acceptance checklist.
The [Quantum Diffusion README](badge/apps/quantum_diffusion/README.md) includes static
validation commands, physical-badge benchmarks, and the remaining gesture,
light, orientation, HOME/reopen, and visual checks. Factory Mindset and Quantum
Diffusion's historical simulator and hardware results were recorded in their
source sessions, not rerun by those migrations. MicroCode's migration passed
host regression tests and static validation, and its migrated source was checked
in the official WebAssembly runtime for launch, editor entry, run/stop, and
rotation. No physical deployment or on-badge verification was performed.
