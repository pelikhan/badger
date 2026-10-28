# Quantum Diffusion

Quantum Diffusion turns motion and capacitive touches into pixelated shockwaves that cycle through GitHub brand colors.

## Usage

Open **Quantum Diffusion** from the badge menu. An opening pulse lights the
field, then the app responds to motion and capacitive touches. The entire
display is the wave grid: no title, footer, hints, status labels, borders
or cursor overlay.

| Input | Action |
| --- | --- |
| Shake / tap the badge | Inject a shockwave; motion direction offsets its origin |
| Twist the badge quickly | Gyro-triggered shockwave, offset in the twist direction |
| Capacitive directions | Shockwave in the corresponding quarter of the grid |
| Capacitive SELECT | Shockwave at the center |
| Capacitive MENU / BACK | Shockwave in the upper-right / lower-right quarter |
| HOME | Return to the launcher |

Each newly touched pad produces one wave; holding it does not continuously
fire. Release and touch again to repeat. Multiple pads can trigger together.
Touch actions follow the firmware's logical orientation, so UP stays at the
top of the displayed grid when inverted. Physical button presses have no app
action, and HOME remains launcher-owned. There is no automatic demonstration.

Wait about 0.2 seconds after opening for motion calibration. Resting gravity
is filtered out; a shake needs about 0.35 g of changing acceleration and a
0.18 g sample-to-sample change. The firmware's latched single-tap, double-tap
and significant-motion events also trigger waves, catching short gestures
between samples. Its event API returns a tuple of integer IDs, not event
names or a bit mask. Tilt/freefall events alone do not trigger waves.

The gyro also detects sudden twists (about 61 degrees/second with a changing
angular velocity), rejecting resting sensor bias and steady rotation.
A 300 ms cooldown limits repeated impulses without requiring the badge to
be held still between shakes. Held acceleration does not continuously fire.

An independent 100 Hz **soft timer** polls accelerometer, gyro and latched
events; rendering only takes queued impulses. Light is sampled at 10 Hz and
smoothed to adjust the glow: gentler in darkness, brighter in strong
light. This uses the raw 0-65535 light API; it never changes the user's
backlight settings. Timer callbacks execute safely in the MicroPython VM,
not a hard interrupt or a second core, so a long blocking native call can
still delay sampling. A bounded latest-impulse mailbox avoids an event backlog.
Capacitive input uses the runtime's cached `badge.touched(action)` state, not
extra I2C reads or button polling in the renderer; IMU/light polling remains
in the independent sensor service.

## Brand-color cycle

The wave palette smoothly cycles through these verified
[GitHub Brand Guide colors](https://brand.github.com/foundations/color):

| Anchor | RGB hex |
| --- | --- |
| GitHub Green | `#0FBF3E` |
| Security Blue | `#3094FF` |
| Copilot Purple | `#8534F3` |
| Orange 3 | `#FE4C25` |
| Lime 2 | `#D3FA37` |

Each transition takes four seconds; the complete loop takes 20 seconds and
starts with GitHub Green. Color follows elapsed wall time, independent of
simulation steps and frame rate. A preallocated 17-entry RGB565 palette is
updated at most 20 times per second using integer interpolation, not per-cell
color generation. The ARM raster and sensor service are unchanged.

Amplitude still controls brightness: faint waves use dark shades, level 11
matches the current anchor, and the strongest crests are lightened toward
official Gray 1 (`#F2F5F3`) without dimming a channel. Interpolated hues and
amplitude tints are this app's treatment, not additional official brand
swatches. The background stays black and ambient light still controls gain.
There is no palette control or extra on-screen chrome.

## What is simulated

This is a classical damped 2D wave equation, not a quantum simulation.
The quantum/Matrix theme is visual: colored phosphor, chunky 8-bit cells,
interfering crests and troughs, and a decaying disturbance. It is wave
propagation rather than the non-oscillating heat/diffusion equation.

The full-screen 64x48 field uses 5-pixel squares on the 320x240 HIRES screen,
up from the initial optimized 26x16 field's 12-pixel squares and 416 cells.
The new grid has 3,072 cells, over seven times the detail, without reserving
space for app chrome. Three preallocated signed 32-bit arrays store Q10
fixed-point amplitudes (36,864 data bytes).
The stencil runs as ARM machine code through MicroPython's Viper emitter,
using integer multiply/shift operations and direct 32-bit buffer access.
The raster also runs through Viper: it writes the verified writable RGB565
`screen.raw` buffer directly, packing two pixels into aligned 32-bit stores
where possible. This avoids thousands of per-cell graphics API calls while
preserving the exact palette, gaps and phosphor detail. Layout/length checks
reject an incompatible framebuffer before starting the timer. Sparse dots
and 2x2 phosphor cutouts are used only when cells are large enough to fit them.
The stencil and raster use no floating-point math or per-cell heap
allocations; no square roots, assembly blobs, overclocking or new firmware
are needed.

Fixed 25 ms integration steps, a stable spatial coefficient of 204/1024
(below the 2-D CFL limit of 0.5), damping, absorbing edge bands and a
12-unit amplitude clamp preserve stability. Signed truncation towards zero
avoids a negative rounding bias. A frame catches up at most four steps after
a stall. No network, credentials, external hardware or saved state is required.
There are no menus, cursor controls or physical-button actions;
launcher-owned HOME behavior is unchanged.

The timer temporarily owns the verified 2026 firmware's `_poll_imu` hook.
It calls the original hook outside rendering and leaves cached readings
available to the runtime; regular button polling remains untouched. This
avoids concurrent I2C reads and double consumption of read-to-clear events.
`on_exit()` stops the timer and restores the hook before HOME/reopen.
Sensor I/O errors stop the service and surface an explicit app error.

## Install

Copy this entire `quantum_diffusion` directory into `apps/` on the badge's
**BADGER** USB Disk Mode volume, then safely eject it. Never copy to the
RP2350 firmware drive. No custom menu icon is included; the launcher uses its
default icon.

## Browser and validation

The physical 2026 badge is the execution target: it must support the Viper
emitter, soft `machine.Timer` callbacks and the `_poll_imu` hook. These were
probed on the connected RP2350 firmware before implementation. MicroPython
recognizes `@micropython.viper` and `@micropython.native` at compile time:
`hasattr(micropython, "viper")` misleadingly returns false on this firmware.
Unsupported firmware reports an explicit import/compile failure, not a
silent unaccelerated fallback. The CPython test path executes the identical
stencil using signed memoryviews instead of native pointers.

The browser simulator is not proof of native emitter/timer support and was
not used for hardware verification. Load all five Python files if exploring
it; simulator builds lacking those firmware facilities cannot run this app.

From the repository root:

```sh
python3 -m unittest discover -s tests -v
python3 ~/.copilot/skills/hyb26-skill/scripts/validate_app.py badge/apps/quantum_diffusion
python3 tests/benchmark_quantum_diffusion.py
mpremote connect PORT run tests/benchmark_quantum_diffusion.py
```

The skill validator reports an expected missing-icon warning for this
intentionally icon-free app. The animation does not load or depend on an icon.

The benchmark runs 100 solver steps and two 120-frame workloads with real
display/VSYNC and the real sensor timer. **Busy** injects a bounded sensor
mailbox impulse every 300 ms, alternating shake/twist labels and directions.
**Dense** additionally reseeds interior cells with high-amplitude values and
selects bright glow gain to stress full-grid rendering. Those are synthetic
benchmark inputs, not app controls, an automatic app mode, or evidence of
physical human gestures. No benchmark UI or FPS label is drawn on the badge.
The native dense seeding overhead is included in average FPS.

Serial output includes average FPS, p95/max full-frame time, the slowest
individual frame and the slowest rolling 15-frame window. It also checks
sampling during 250 ms with no rendering. Desktop results measure the Python
reference only, not the ARM emitter or display.

To reproduce the candidate sweep without replacing the installed app:

```sh
mpremote connect PORT mount badge/apps/quantum_diffusion \
  exec "BENCH_APP='/remote'; GRID_CANDIDATES=[(53,40),(64,48),(80,60),(106,80),(160,120)]" \
  run tests/benchmark_quantum_diffusion.py
```

For a longer brand-cycle check, use `BENCH_FRAMES=420` instead of
`GRID_CANDIDATES`. The phase mask in serial output is `31` when all five
brand-color segments were visited. This output is diagnostic only; it is
never drawn on the display.

Set `BENCH_TOUCH=True` in the same `exec` argument to add seven simultaneous
capacitive onsets every eight frames to both workloads. This exercises the
real firmware's logical pad cache and app pulse path, not physical touches.
The benchmark restores cached pad state after every update and never injects
HOME. Omit the option for comparison with the earlier motion-only workloads.

### Measured on the physical badge

On 28 October 2026, the connected GitHub Badger RP2350 at its existing
230 MHz clock ran MicroPython 1.29.0-preview (13 October 2026 build).
The original float/40x24 app measured 4.02 FPS and 42,043.5 us per solver
step. The first fixed-point/native 26x16 revision, still with chrome and
controls, measured 22.24 FPS and about 422 us per solver step under its
older AUTO-plus-pulses workload. Removing chrome and controls freed the whole
display. An initial 45x34 grid still fell to 13.65 dense FPS with per-cell
graphics calls. Direct native framebuffer rendering eliminated that bottleneck;
RGB565 byte order and all palette entries were verified on the badge, and
pixel-level regression checks cover odd/even cells from 2 to 7 pixels.

The packed-framebuffer resolution sweep before brand cycling used the workloads
above, 90 frames per workload:

| Full-screen grid | Square size | Busy FPS | Dense FPS | Slowest dense 15-frame FPS |
| --- | ---: | ---: | ---: | ---: |
| 53x40 | 6 px | 27.63 | 21.94 | 22.73 |
| **64x48** | **5 px** | **21.70** | **16.83** | **17.31** |
| 80x60 | 4 px | 17.15 | 13.62 | 14.28 |
| 106x80 | 3 px | 9.74 | 8.22 | 8.76 |
| 160x120 | 2 px | 5.30 | 4.52 | 4.99 |

64x48 is the finest full-screen square-cell grid in this sweep sustaining
approximately 15 FPS even in dense rendering stress; the next finer size
does not. At 64x48 the dense p95 frame time was 64.52 ms and the slowest
single frame was 76.20 ms (13.12 instantaneous FPS). Busy p95 was 56.48 ms,
with a slowest single frame of 71.70 ms (13.95 instantaneous FPS).
These isolated stalls mean **15 FPS is not a hard per-frame guarantee**;
the slowest measured 15-frame windows remained above the target. Rolling
window rates exclude benchmark-only dense seeding overhead, whereas average
FPS includes it; they are not interchangeable.

A separate 120-frame confirmation at the selected 64x48 size measured
22.76 busy FPS and 17.21 dense FPS, with a slowest dense frame of 65.37 ms
(15.30 instantaneous FPS). Timings vary between runs, so the slower sweep
results above are retained rather than claiming a hard minimum.
On the physical badge, the entire 153,600-byte native framebuffer matched
the firmware's rectangle-rendered reference exactly at both glow gains 4
and 7; the optimization does not substitute a different image.

### Capacitive confirmation

With capacitive input added, a separate 120-frame run with `BENCH_TOUCH=True`
measured **21.40 busy FPS** and **17.26 dense FPS**, including seven simultaneous
touch onsets every eight frames plus the usual motion workload. Dense p95
was 65.16 ms and its slowest frame was 73.65 ms (13.58 instantaneous FPS);
the slowest dense 15-frame window remained 17.79 FPS.
The actual firmware's cached touch actions were exercised for all eight
channels in both logical orientations: non-HOME pads fired once per contact,
held pads did not repeat, simultaneous pads worked, and HOME added no wave.
These are injected cached inputs, not a physical finger-touch observation.

### Brand-cycle confirmation

With the cycling palette enabled, 420 frames per workload measured **21.89
busy FPS** and **17.16 dense FPS** on the same physical badge. Both workloads
visited all five brand-color segments. Busy p95 frame time was 56.27 ms;
dense p95 was 65.14 ms, with a slowest dense frame of 74.50 ms (13.42
instantaneous FPS). This retains the approximate 15+ FPS target, not a hard
minimum for every frame.

The actual ARM framebuffer matched the firmware rectangle reference across
all five color anchors and all five transition midpoints, at both glow
gains 4 and 7. These are execution, numerical and performance checks;
human-observed color appearance and smoothness have not been verified.

The real soft timer produced 25 sensor samples during 250 ms with no
rendering. Ambient readings were around 51 raw counts in that environment;
the dark-room gain of 4 was selected. A signed mixed-impulse stencil
fingerprint matched CPython exactly, including negative values and clamping.
Two bounded runs through the real firmware launcher unloaded/reopened the
app and restored the original IMU hook successfully.

The original raw accelerometer scale was correct. Shake reliability was
limited by slow rendered frames and quiet-only rearming, and it ignored the
firmware's latched motion events. Higher-rate independent sampling, jerk
detection, latched events and cooldown-only rearming address those causes.

Physical verification remains necessary for human-observed shake/twist
sensitivity, light changes, both orientations, HOME and
re-opening. Automated hardware timings and injected sensor/control inputs
are distinguished from human gesture and visual checks.
