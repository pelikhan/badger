# MicroCode for the Universe 2026 badge

MicroCode is an offline, icon-based WHEN/DO programming editor with five pages, saved programs, animations, and badge sensors.

This is a clean-room Python implementation of the concepts in the
[MicroCode Classic manual](https://microbit-apps.org/microcode-classic/docs/manual)
and [language](https://microbit-apps.org/microcode-classic/docs/language), not a
micro:bit binary or a port of its assets. No PNG files, PNG decoding, network,
credentials, or external hardware are required. All tile glyphs and 5x5 images
are integer bitmaps drawn using rectangles. The stock badge launcher will show
its generic app icon: its built-in/custom icon contracts do not accept Python
glyphs. This app deliberately does not patch the menu or install a PNG.

## Using the editor

- At Home, choose **Continue editing**, **Run**, **New**, **Load slot**,
  **Examples**, **Sensors & support**, or **Help**.
- Arrows move; **SELECT** opens a tile/picker. Left/right wraps on the same
  row. Up from the toolbar goes to the previous page, then Home on page 1.
  Down beyond the last rule visits the next page, wrapping page 5 to page 1.
- The toolbar has **Run**, **Save**, **Previous page**, **Next page**, **Home**,
  and **Help**. **MENU** also opens program commands, including retrying a failed
  draft save. **BACK** returns to the toolbar, then Home.
- A rule has six tiles: **event**, **condition**, **command**, **arguments**,
  **repeat**, and **rule operations**. Add up to eight rules per page.
  Rule operations duplicate, move, or delete, with confirmation before deletion.
- Choose event and command tiles independently. Their parameter pickers only
  offer valid tile categories; changing a tile resets its parameters to defaults.
- Numeric parameters are sums of up to eight tiles: constants, X/Y/Z, a dice
  value, the current numeric event value, or supported sensor readings.
  In a number picker, up/down changes the value; left/right selects step
  1/5/50/500/5000. Choose **Done: use sum** to commit an expression; BACK cancels.
- Timer/page-start parameters add 250 ms, 1 second, 5 seconds, and random
  0..1 second tiles. No timer parameters means 250 ms; no page-start parameters
  means immediate. Each timer restarts after expiry with a fresh random interval.
- Image arguments open an animation editor. Select a frame, move over its 25
  cells, and SELECT to toggle pixels. **MENU** opens original code-encoded presets.
  **BACK** or Up above the first pixel row commits the frame. Append copies the
  last frame; delete removes the last (except the sole remaining frame).
  Sequences have up to eight frames and a shared 50..5000 ms frame duration.
- Images and LED blinks can repeat once, a numeric count (bounded to 999), or
  forever. A count of zero does nothing. Image sequence order and repeat are
  nonblocking; logical controls remain responsive.
- **BACK** stops a running program and returns to the editor. **MENU** toggles
  a live sensor view while the program continues. **HOME remains launcher-owned**;
  no IRQs, physical input mappings, or orientation handling are overridden.
  Runtime button tiles expose SELECT/RIGHT/LEFT/UP/DOWN; BACK/MENU/HOME are
  reserved. In the adapted button examples, micro:bit A is SELECT and B is RIGHT.

## Language and scheduling

A program always starts on page 1 with X/Y/Z = 0. Only the current page is
active. Each event is processed separately and matching rules execute in
visible top-to-bottom order. Variable writes enqueue changed-value events;
writing the same value does not emit another event. An empty variable condition
matches any change; otherwise the event value must equal the expression's sum.
An empty WHEN is represented by the equivalent default **Page start** tile.

Page switches cancel all pending page timers, queued old-page events, and
animation/blink jobs, including when switching to the same page. Variables and
the last displayed image/LED setting persist across page switches. New-page
start rules run on the next cooperative frame. A new image or number command
cancels a prior image sequence; other resources remain independent.

Supported DO commands are images, number display (clamped to 0..99), X/Y/Z
assignment (default 0, saturated to -65535..65535), page switch (default page 1),
and four white case LEDs (brightness clamped to 0..100%). Image defaults to the
original happy face. Random numeric tiles yield 1..5 inclusive. Event-value tiles
are available only in variable/sensor rules. Missing sensor values never become
invented zeroes: the program refuses to start or stops with a visible error.

At most 16 events are dispatched per frame; a 64-event queue limit stops runaway
feedback visibly. Each animation advances at most one frame per update, so slow
frames slow animations rather than locking up controls. Timers coalesce missed
expiries, not unbounded catch-up bursts. These are deliberate bounded-resource
adaptations of the manual.

## Saving

Edits atomically autosave a working **draft**, independently of the three named
save slots. Editor **Save** writes a slot, after confirmation; Home **Load**
restores one, after confirmation. New/example/load operations explicitly confirm
replacement of the current draft. Slots do not change merely because you edit.

State files are `/state/microcode_draft.json` and
`/state/microcode_1.json` through `microcode_3.json`. Documents are schema-checked,
versioned, and bounded to 32 KiB before parsing. A temporary file is flushed and
renamed over its destination on LittleFS. A failed write leaves the previous
committed document intact; a partial `.tmp` is never loaded. Failed saves remain
visible as **Not saved**, retain the current program in RAM, and can be retried.
Corrupt/unknown-version slots are reported, not silently rewritten or partly
executed. Replacing the draft after an error is an explicit user edit/new/load.

This intentionally uses a small app-specific atomic store rather than the
current firmware `State`: its upstream save/load helper swallows I/O errors and
can rewrite corrupt data, preventing reliable user-visible failure reporting.
Pixel edits are persisted on leaving the frame editor, not on every pixel.
Press BACK to commit a frame before using launcher HOME; incomplete pixel and
expression dialogs are not autosaved.

The official WebAssembly runtime currently reports missing files as WASI errno 44
while its Python `errno.ENOENT` constant is 2. The store recognizes that additional
missing-file code **only on `sys.platform == "webassembly"`**; other I/O errors
are still reported. Native badge behavior is unchanged.

## Sensors and hardware evidence

Sources were reviewed on 28 October 2026:
[badge app contract](https://github.com/badger/home/blob/main/badge/AGENTS.md),
[hardware map](https://github.com/badger/home/blob/main/hardware/README.md),
[USB guide](https://github.com/badger/home/blob/main/hardware/USB_SERIAL.md),
and reference apps
[input_test](https://github.com/badger/home/tree/main/badge/apps/input_test),
[settings](https://github.com/badger/home/tree/main/badge/apps/settings),
[brattal](https://github.com/badger/home/tree/main/badge/apps/brattal), and
[sense](https://github.com/badger/home/tree/main/badge/apps/sense).
Read-only inspection of connected badge app sources confirmed
`battery_level()`, `is_charging()`, and `caselights(float)`.
The connected device identified as GitHub Badger 2350 / MicroPython 1.29 preview.
No new app has been deployed: the user requested simulator verification.

| Tile / value | Verified interface | Units / adaptation |
|---|---|---|
| Button press/release | `badge.pressed/released(logical_action)` | Edge events; front switches and logical capacitive controls |
| Touch/release | `badge.touched(logical_action)` | Separate touch edges; no raw CAP1208 counts |
| Accelerometer X/Y/Z | `badge.imu()[0:3]`, onboard LSM6DS3TR-C | Raw firmware values, **not claimed to be g** |
| Gyroscope X/Y/Z | `badge.imu()[3:6]`, same IMU | Raw firmware values, **not claimed to be deg/s** |
| Tilt X/Y, directional/face gestures | Normalized acceleration | Relative -100..100%; enter at 0.60, rearm below 0.40 |
| Shake | Two acceleration-vector changes >0.75 of a slowly tracked magnitude reference | Within 300 ms, 600 ms cooldown; physical tuning is unverified |
| Ambient light | PT19-21C, `machine.ADC(Pin.board.LIGHT_SENSE)` or documented GPIO43 | Raw 0..65535; no lux claim |
| Light levels 1..5 | Same ADC | Boundaries 512/2048/8192/32768, 64-count hysteresis, uncalibrated relative levels |
| Battery | `badge.battery_level()` | Firmware percent estimate, not a precision battery measurement |
| Charging | `badge.is_charging()` | 0/1 firmware status |
| Inverted | `badge.upside_down()` | 0/1 firmware orientation; logical controls are not remapped by the app |
| Case LEDs (output) | `badge.caselights(brightness)` | Four white LEDs together, 0..1 firmware brightness |

Analog/IMU/status sampling is limited to 20 Hz; logical input edges are checked
each frame. Sensor conditions support equality, at least, at most, and change.
Threshold conditions fire only on entering the matching region (including a
matching first available sample); change ignores its initial sample. Light
level and tilt gestures have hysteresis; raw threshold conditions do not, so
choose thresholds away from noisy boundaries. Axis signs need confirmation on
physical hardware. The runtime honors firmware inversion for relative tilt.

Capability detection checks callable firmware APIs and validates readings.
Missing or failing sensors show **N/A**, crossed-out choices, logged reasons,
and visible runtime errors rather than fabricated readings. The simulator may
provide inert/stub sensor APIs; availability there is **not proof of hardware**.
The sensor screen exposes raw live values and an explicit unsupported list.

### Deliberate gaps

- No onboard microphone/speaker: hear/clap, sound emoji, and the four-note melody
  editor are unavailable; they are not silently replaced with a different action.
- No confirmed application API for IMU die temperature, ambient temperature,
  humidity, pressure, or compass heading. The BME280/LTR559/external LSM6DS3 in
  `sense` belong to an **optional** Qw/ST stick, not the badge itself.
- No micro:bit P0/P1/P2 or touch logo: use logical badge controls instead.
- Micro:bit proprietary radio/group messaging and remote robot/line/distance/
  motor/servo commands are unsupported. Wi-Fi/Bluetooth are not compatible
  substitutions and no network connection is made.
- Jacdac accessories require a separate transport/driver; Qw/ST has no
  preinstalled devices. This app neither scans nor reconfigures internal I2C.
- IR hardware is not a distance sensor; no unsupported distance claims or
  unverified IR protocol are exposed.
- No MicroCode HEX/URL import, web sharing, multilingual speech reader, or binary
  compatibility is claimed. Examples are original badge-compatible reconstructions.

## Acceptance checklist and verification

The checklist was derived from the manual, raw language Markdown (the rendered
page drops some icon-heavy command lists), and the First program, Smiley buttons,
Flashing heart, Counter, Head or tail, Hot potato, Jacdac, and Robot documentation.

| Manual requirement | Badge implementation | Automated acceptance |
|---|---|---|
| Offline icon structured WHEN/DO | Code-drawn tiles and context pickers | Real entrypoint/render/control fakes; no asset/PNG loads |
| Directional navigation/back/wrap | Logical controls, page toolbar, row wrap | Editor navigation and page-boundary tests |
| Pages, active rules, order | Five pages, ordered event dispatch | Event ordering, start delays, same/new page cancellation |
| Timers and summed/random times | Cooperative per-rule timers | Boundary, repeat and deterministic random tests |
| Variables, sums, random | X/Y/Z, changed events, numeric filters | Counter, ordering, defaults, sums and saturation tests |
| Image animation and repeat | Editable 5x5 frames, frame copy, finite/infinite loops | Pixel controls, copy, sequence/resource cancellation |
| Three editor-save/home-load slots | Atomic slots plus independent autosave draft | Missing, malformed, oversized, bad types, interrupted writes |
| Hardware-specific tiles | Capability-aware sensors and case LEDs | Missing/failing APIs, thresholds, edge/hysteresis/shake tests |
| Samples | Nine runnable badge adaptations | All schemas validated; counter, heart and page samples executed |
| HOME/short updates | No HOME handler or blocking sleeps | Entrypoint smoke test, bounded event feedback |

From the repository root, run `python3 -m unittest discover -s tests -v`.
Run the skill's `validate_app.py badge/apps/microcode --target emulator`.
The missing `icon.png` warning is intentional; **do not add a PNG** to appease it.
See the repository README for reproducible simulator loading.

Physical checks remain separate: actual sensor values/ranges and axis signs,
shake false positives, light thresholds, touch and orientation, LED cleanup,
HOME/reopen, restart persistence, failed saves, frame rate, and memory on badge.
Neither automated fakes nor the WebAssembly simulator certify those properties.
