# The Factory Mindset

The Factory Mindset turns a laptop failure into an animated story about programming with a phone and a cloud agent.

## Usage

Open **Factory Mindset** from the badge menu. The fifteen scenes autoplay and loop
in about 93 seconds, with staged text reveals and sliding transitions. Each scene
is rendered directly from code for the 320x240 HIRES display. There is no frame,
toolbar, counter, timeline, control footer, or custom launcher icon.

| Control | Action |
| --- | --- |
| LEFT / RIGHT | Previous / next scene, wrapping at either end |
| SELECT | Pause / resume the animation and autoplay timer |
| HOME | Return to the launcher, handled by firmware |

Pausing freezes reveals, transitions, and the timeline. Navigating while paused
opens the selected scene fully revealed; resuming gives it a full reading interval.
Reopening the app starts from the opening scene. BACK, MENU, UP, and DOWN have no
app-specific action. No settings or personal data are saved.

## Story and artwork

The five supplied **The Factory Mindset** slides become fourteen story scenes
plus a talk advertisement. They cover the laptop failure, summer 2025 travel,
phone-to-agent-to-PR workflow, the AW factory's output and speed, the 50/50
human/AW split, engineering trusted processes, Markdown-to-Actions-to-sandboxed
agents, and building a human-agent process that works for you.

Factory metrics are transcribed from the user's slide: approximately 23k+
pull requests in `github/gh-aw`, 700+ customer issues merged, 6h time to merge,
100 LoC, and 50% started by humans / 50% AW. These are supplied-slide figures,
not live measurements or independently reconciled GitHub statistics.
The requested dark mode uses a near-black green background and high-contrast
light text while retaining the original green identity; purple marks the cloud
agent and blue marks the pull request. Text fades and moves into place; the phone,
agent, pull-request diagram, workflow arrows, and card outlines are native vector
primitives, not pictures.

The [hardware documentation](https://github.com/badger/home/blob/main/hardware/README.md#lcd)
identifies the badge display as a backlit IPS LCD, not OLED. Dark pixels do not
turn down its backlight, so this visual change is not a verified battery-saving
measure. Display brightness and playback controls are intentionally unchanged;
the additional scenes extend the loop.

The supplied [Universe session](https://reg.githubuniverse.com/flow/github/universe26/attendee-portal/page/sessioncatalog/session/1785161525956001tBix)
lists the session as **The factory mindset: Agentic workflows in production** on
**Wednesday, October 28, 2026, 12:50-1:20 PM PDT**, at
**Main Stage @ Festival Pavilion**. Per the user's title correction, the app and
final scene use **The Factory Mindset** as the displayed title. The final scene
advertises that date, time, timezone, and venue. The schedule was read from the fully loaded official session
page, not inferred from search results. The bundled schedule is static; rebuild
the advertisement if the event organizer changes it.

No images are shipped or loaded. `factory_slides.py` contains every line of text,
position, color, size, reveal delay, and vector-card layout; `__init__.py` draws
them with `screen.text`, `screen.line`, and stroked `shape` primitives.
The stock vector font `/system/assets/fonts/MonaSans-Medium.af` is loaded once.
Its presence was checked on the connected badge before selecting it. Text is
measured and fitted once at startup, with a minimum 12-pixel size; missing fonts
or illegible layouts raise an explicit error instead of silently substituting.
There is no network, extra hardware, Wi-Fi, `secrets.py`, audio, or desktop Python
dependency on the badge. Large
frame-time stalls are capped to 100 ms so the reader does not miss entire scenes.

## Install

Copy this **entire folder**, including `factory_player.py` and `factory_slides.py`,
into `apps/factory_mindset/` on the BADGER USB Disk Mode volume.
Some firmware exposes `system/apps/` instead. Never copy to the RP2350
bootloader drive. Safely eject BADGER and open **Factory Mindset** in the menu.

No custom launcher icon is included, per the user's request. The firmware may
show its default generic tile. The menu is intentionally left unchanged; no
menu-icon patch is needed. The HYB26 static validator's missing-icon warning is
expected for this explicitly icon-free app.

## Development and checks

This app was migrated from the **Home skills** project's Factory mindset
slideshow worktree into `pelikhan/badger`. The current fifteen-scene native
text/vector implementation, playback tests, and packaging tool are preserved;
older image-based packages and temporary preview artifacts are not included.
The source project is unchanged.

The app follows the authoritative [2026 app contract](https://github.com/badger/home/blob/main/badge/AGENTS.md)
and [gallery graphics example](https://github.com/badger/home/blob/main/badge/apps/gallery/__init__.py):
runtime globals, logical buttons, `badge.ticks_delta`, HIRES/VSYNC, module-level
`run(update)`, explicit app-relative paths, and no HOME interception. Native font
loading and `screen.text(..., font_size=...)` follow the
[2026 vector-text example](https://github.com/badger/home/blob/main/badge/apps/demos/demos/vector_text.py).

From the repository root, using the installed HYB26 skill:

```sh
python3 "$HOME/.copilot/skills/hyb26-skill/scripts/validate_app.py" badge/apps/factory_mindset
python3 "$HOME/.copilot/skills/hyb26-skill/scripts/validate_app.py" badge/apps/factory_mindset --target emulator
python3 -m unittest discover -s tests -v
```

To package the current code for installation and the actual MicroPython simulator:

```sh
python3 tools/package_factory.py --output /path/to/preview-folder
```

This writes a code-only ZIP and a simulator project bundle. There is no image
builder or Pillow dependency. Firmware's stock font is used at runtime, not
redistributed. The browser preview uses the actual MicroPython simulator, not
a slideshow implemented in JavaScript.

Migration checks cover the Python playback/contract tests, both HYB26 static
targets, and packaging integrity. The simulator and physical deployment checks
below describe the original implementation, not a new deployment from this
repository. No simulator or physical badge verification was repeated for the
migration; the physical acceptance checklist still applies.

Validation performed during implementation: both HYB26 static targets passed
with only the expected missing-icon warning and all 16 playback/contract tests
passed, including source content/talk-time fidelity, absence of image assets,
native drawing/layout coverage, staged reveals, and minimum 4.5:1 text/accent
contrast. The app
also ran in the public Badger MicroPython browser simulator through a temporary
read-only localhost mirror. Pixel comparisons confirmed pause stability,
paused navigation, resume, all fifteen scenes, forward wrapping, and reverse
navigation.

With the owner's authorization, the image-free app was installed on the connected
BADGER volume, all four files were read-back hash verified, and the volume was
safely ejected. The installed app then rendered all fifteen scenes through the
physical firmware's real frame loop (45 frames), with native font measurements
confirming text bounds. No custom icon, menu patch, or brightness change was made.
These checks do not establish how the screen looked to a person.

## Physical acceptance checklist

Manual screen, launcher, and physical-control verification remain outstanding.
Visually check the launcher tile, which uses the firmware's existing default icon.
Read all fifteen scenes in both orientations; let the last scene loop to the first;
test LEFT/RIGHT wrapping, SELECT during a reveal and slide, paused navigation,
resume timing, and repeated HOME/reopen. Confirm HOME returns promptly, the
story restarts on reopen, there are no tracebacks, and memory/performance remain
stable over multiple loops.
