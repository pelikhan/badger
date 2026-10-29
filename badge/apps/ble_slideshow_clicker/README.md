# BLE Slideshow Clicker

BLE Slideshow Clicker turns a compatible Badger into a wireless keyboard for navigating presentation slides.

**Current hardware status:** deployment and launch succeeded, but connection
to the tested MacBook Pro failed. macOS Bluetooth daemon crashes were observed.
Do not retry on that Mac until the incompatibility is understood; see the
physical test findings below. Advertising was stopped after testing.

## MacBook Pro setup

1. Open this app on the badge. On the Mac, open **System Settings > Bluetooth**
   and connect **Badger Clicker** under nearby devices. The app requests
   unauthenticated **Just Works** pairing: no badge passkey or custom Mac
   receiver is required. If macOS displays an unexpected code-entry request,
   cancel, forget the device, reopen the app, and retry.
2. Wait until macOS finishes connecting its standard Bluetooth keyboard
   driver. If Keyboard Setup Assistant appears, it can be closed: the clicker
   has no letter keys for its keyboard-layout identification exercise.
3. Focus Keynote or PowerPoint for Mac and start the presentation normally.
   The badge does not start the slideshow or select the foreground window.
4. Right/Select advances; Left goes back; Back sends Escape; Down sends B to
   toggle a black screen in Keynote/PowerPoint. B depends on the application's
   keyboard shortcuts and current keyboard layout.

No Wi-Fi, network, `secrets.py`, app permissions, desktop receiver, or companion
Mac application is needed for standard Bluetooth HID. **The tested MacBook Pro
did not successfully pair, and key delivery remains unverified.** Hardware or macOS security
policy can still reject the badge's firmware or an unencrypted connection.

## Controls and operation

| Badge action | Keyboard event |
| --- | --- |
| Right or Select | Right arrow: next slide |
| Left | Left arrow: previous slide |
| Back | Escape: exit slideshow |
| Down | B: toggle blank screen |
| Home | Launcher exit; release keys and disconnect |

Up and Menu have no app action. Inputs are single taps, not hold-to-repeat.
One action is accepted per frame; simultaneous presses prioritize Next,
Previous, Escape, then Blank. At most four taps wait for transmission. A full
queue shows "Busy - press again". Queued taps are discarded on disconnect,
suspend, protocol changes, or exit. A supported pairing attempt times out
after 60 seconds. Both logical orientations use the same controls. The app
requests 320x240 and sizes its background/error wrapping from the screen.

The app advertises while open and disconnected. It requests bonding when the
firmware supports it, permitting reconnect while the app is open. It does not
wake a suspended computer. No app-specific icon is included, as requested;
the launcher may supply its own default tile.

## Deliberately reduced security

**This app allows insecure pairing/connections at the user's request.**

When pairing configuration and `gap_pair` work, the app requests
`io=3` (NoInputNoOutput), `mitm=False`, `le_secure=False`, and `bond=True`.
This permits legacy or Secure Connections Just Works pairing without a
passkey; it does not force legacy pairing or disable encryption. Reports wait
for an encryption event, but do not require authentication/MITM protection,
bonding success, or a full 16-byte key. The screen says
**Just Works: NO MITM protection**.

If pairing APIs/security configuration are absent, or pairing returns
`EOPNOTSUPP`/`ENOSYS`, the app permits reports after connection without claiming
to know whether the link is encrypted. The screen explicitly warns
**UNVERIFIED: may be plaintext**, and the reason is printed to the runtime
console. GATT attributes intentionally do not require encryption or
authentication, allowing this compatibility fallback. Notifications are still
sent only to subscribed hosts. Unexpected errors are displayed, not silently
converted to an insecure fallback.

Neither mode verifies who connected. Nearby devices may connect first,
observe navigation keys, interfere with pairing, or impersonate this device.
Use it only in a trusted environment, turn it off when not presenting, and
never use it for typing passwords or other sensitive information. Exiting via
HOME stops advertising and disconnects. macOS can require encryption even
though the app itself permits plaintext: an app cannot override host policy.

## Firmware requirements and limitations

The tested badge reports MicroPython 1.29.0-preview, build `badger`,
`GitHub Badger 2350 with RP2350`. Its BLE object does not expose `gap_pair`
or `gap_passkey`, so this build uses the app's unverified-security fallback.
This observation does not establish compatibility of other firmware builds.
The app uses MicroPython's low-level `bluetooth.BLE` in the badge frame loop,
not a competing asyncio loop, `aioble`, or `badgeware.link`.

The minimum app API is a BLE peripheral with GATT registration/descriptors,
long Report Map reads, advertising, connection/write events, and
subscription-aware `gatts_write(handle, value, True)`. A successful Mac
pairing additionally requires a functional firmware Security Manager. Merely
bundling `bluetooth` or aioble is not proof of this.

The Tufty upstream board selects **BTstack**. Current upstream MicroPython
BTstack cannot supply a passkey response (`mp_bluetooth_gap_passkey` returns
`EOPNOTSUPP`); this app no longer uses passkey entry. Pairing configuration and
encryption/secret events are compile-time gated. Current upstream event
handling also does not expose a Python Just Works confirmation API. If an installed build's Security Manager cannot finish the host's pairing exchange,
removing authentication requirements in this app does **not** repair that
firmware limitation. A timeout/rejection on the Mac remains a genuine
firmware/host blocker, not evidence that plaintext fallback will work.
Installing this app does not patch firmware or modify system files.

Other BLE apps must release the singleton radio before launch. This app
refuses to overwrite an already active radio's services or IRQ handler.
Cleanup releases the last key when possible, stops advertising, disconnects,
unregisters this app's handler, and disables its radio. Pairing failures,
missing modules, and storage errors are visible on screen and in the console.

### Offline firmware investigation

The public Tufty
[build recipe](https://github.com/pimoroni/tufty2350/blob/ee84772fb7f7d5424e9593029caba815c6f89c80/ci/micropython.sh)
selects `pimoroni/micropython` branch `bw-1.29.0-gc`, inspected at
`41d1e007537f9b15cf13a9436228dd59d6485eca`, with BTstack dependency
`77e752abd6a0992334047a48038a5a3960e5c6bc`. This is a reference build,
**not established provenance for the installed custom Badger firmware**.
Its backend has the same read-only Service Changed stub. It also registers
an HCI handler but no Security Manager handler, and never handles
`SM_EVENT_JUST_WORKS_REQUEST`. The pinned BTstack emits that event and
requires `sm_just_works_confirm()` to continue. Enabling the pairing compile
flag alone would therefore not complete this implementation.

A targeted firmware repair must provide a real Service Changed indication
characteristic and working CCCD storage/callbacks, plus the indication and
cache-invalidation lifecycle. Merely adding the INDICATE property is
insufficient: BTstack generates a dynamic CCCD whose value the MicroPython
backend must manage. The security path also needs policy-aware Just Works
confirmation, correctly routed encryption events, and real device-specific
key storage before claiming persistent bonds; the reference backend
currently initializes ER/IR to zero and links an in-memory device database.

Switching to NimBLE is not a verified configuration-only solution. The
reference RP2 NimBLE port uses the UART HCI interface; the CYW43 transport
used here is wired through the BTstack port. A replacement needs transport
integration as well as compatible badge runtime/radio modules.

At the user's direction, public Tufty compatibility was assumed for a separate
**offline experimental build**, not established for the physical badge.
The candidate replaces the Service Changed stub with an indication-only
characteristic and per-connection CCCDs, schedules full-range invalidation,
cleans up disconnected sessions, and prevents database replacement while
connected. It registers a separate Security Manager handler, confirms Just
Works only after explicit NoInputNoOutput configuration without MITM, and
rejects unsupported passkey/numeric-comparison requests. ER/IR roots are
random per BLE activation; the device database remains memory-only, so
**persistent bonding is not implemented**.

The patched Tufty target cross-compiles, and native ATT/pairing-policy
regressions exercise the firmware helper and real ATT database engine.
The patches, source revisions, checksums, and code-only UF2 are maintained
separately from this app collection. This is not a custom Badger release:
the UF2 has no bundled ROMFS/filesystem, and replacing the runtime can break
existing apps or badge-specific APIs. It is not a deployment-ready update.
The upstream UF2 also includes an RP2350-E10 compatibility block targeting
the last flash page; preservation of existing device data is unverified.
Neither build success nor the compatibility assumption establishes that
the patch prevents the Mac crash. No firmware has been flashed and no
additional host connection attempt has been made. An exact-source Badger
build remains preferable; flashing and another crash-risk Mac test require
separate informed approval.

## HID and bonding implementation

The HID service (0x1812) exposes HID Information, Report Map, Control Point,
Protocol Mode, input/output Reports with Report Reference descriptors, and
boot keyboard input/output. Its report map describes keyboard report ID 1:
modifiers, reserved byte, six key usages, and LED output. The eight-byte input
payload excludes the report ID. Key-down and all-zero key-release are separated
by at least 40 ms, with a 40 ms gap before the next tap. Boot/report selection
and suspend/resume are honored; LED output is accepted but not displayed.
Battery Service exposes the runtime's percentage, refreshed once per minute.
The app requests ATT MTU 23: its eight-byte reports fit in the default
20-byte payload, while hosts must use long reads for the Report Map.
This diagnostic interoperability change did not prevent the observed Mac
failure in the controlled test below.

Device Information includes a descriptive manufacturer/model and the
[pid.codes private test identity 1209:0001](https://pid.codes/1209/0001/).
This identity is **not universally unique** and is permitted only for private,
in-house testing, not devices redistributed, sold, or manufactured. Obtain
an appropriate assigned identity before any such distribution. This is a
community prototype, not a Bluetooth-qualified product
or a claim of full HOGP conformance. The intentionally public attributes and
possible plaintext fallback deviate from HOGP's security requirements.

No sibling apps, shared libraries, or additional MicroPython packages are
required. The advertised name and controls are constants in `clicker.py`.
When supported, bonding secrets are stored only at
`/state/ble_slideshow_clicker_bonds.json`, never inside the deployable app.
The cache holds at most 16 stack secret records (not 16 computers), validates
loaded data, copies IRQ buffers, and writes changes outside IRQ callbacks by
temporary file/rename. Persistent bonding is not claimed when the backend
does not supply secret callbacks; reconnection may require re-pairing.

The bond file is not encrypted at rest. Do not commit, share, or package it
or its `.tmp` file. To clear stale/corrupt bonds or a full store, exit the app,
forget **Badger Clicker** on the Mac, and remove only that device-local JSON
file and matching `.tmp` using the badge's documented USB/REPL workflow.
Reopen and pair again. Storage errors stop the app rather than silently
discarding existing keys.

## Checks and outstanding hardware acceptance

From the repository root:

```sh
python3 -m unittest discover -s tests -p 'test_ble_slideshow_clicker.py'
python3 "$HOME/.copilot/skills/hyb26-skill/scripts/validate_app.py" badge/apps/ble_slideshow_clicker
python3 "$HOME/.copilot/skills/hyb26-skill/scripts/validate_app.py" badge/apps/ble_slideshow_clicker --target emulator
```

Desktop tests use fake BLE events to check the actual service definition,
report descriptor/packets, pairing gates and insecure fallbacks, persistence,
timing, protocol changes, disconnect/reconnect, failures and lifecycle.
They cannot establish radio or macOS compatibility. The browser simulator
cannot implement BLE HID. Static validation's missing-icon warning is
intentional. No simulator pairing or successful keyboard delivery is claimed.

### Physical findings, 2026-10-28

The three app files were copied through USB Disk Mode, compared byte-for-byte,
and safely ejected. The installed app launched and advertised; macOS displayed
**Badger Clicker** as a keyboard. The Mac connected briefly and negotiated
ATT MTU 527, then disconnected. No successful keyboard registration, report
subscription, navigation keys, or bonded reconnection was established.

The Mac's `bluetoothd` crash reports recorded `EXC_BAD_ACCESS` / `SIGSEGV`
during connection attempts. The logs reached Device Information reads just
before the crashes. The original zero vendor/product identity was replaced
with the permitted private-test identity, but the single explicitly approved
retest still failed. The identity change is **not a confirmed fix or root
cause**. Firmware Security Manager limitations and a macOS HID interoperability
bug remain possibilities; the exact cause is unresolved.

Further log analysis found an earlier error: macOS attempted to enable
Service Changed indications on the firmware's built-in GATT service and
reported that the characteristic did not support them.
[Upstream MicroPython's BTstack backend](https://github.com/micropython/micropython/blob/d5f2fc239af2d69407170fa290ba6752c8f1790c/extmod/btstack/modbluetooth_btstack.c#L889-L906)
registers 0x2A05 as read-only, without
indications or a CCCD. The public Python registration API always preserves
that built-in service, so changing app Device Information fields cannot
repair it. This is a concrete interoperability defect, but its causal role
in the macOS crash remains unconfirmed.

The MTU-23 diagnostic build passed 35 desktop tests and both static validators,
then was deployed with backup and installed-byte verification. During one
separately approved connection attempt, the badge confirmed ATT MTU 23, but
pairing still failed. macOS again recorded the same Service Changed error
before Device Information reads, and `bluetoothd` crashed with
`EXC_BAD_ACCESS` / `SIGSEGV` at address 0x1. No app-visible report/protocol
writes or native HID keyboard registration occurred. BTstack suppresses
Python CCCD-write events, so the trace alone does not establish whether the
host subscribed. Reducing the MTU is therefore
**not a solution**. There have been five observed daemon crashes in total;
do not make additional connection attempts without fresh informed permission.

The controlled retests each stopped after their first disconnect and disabled
BLE. After the MTU-23 attempt, a separate read verified the radio inactive
before the badge was reset to the menu and serial access released.
Do not treat successful advertising, a transient BLE connection, or desktop
tests as a working Mac slideshow clicker.

Physical verification remains outstanding:

1. Resolve the host crash and establish a compatible firmware/host combination
   before another pairing attempt. Do not repeatedly retry a crashing host.
2. With explicit permission for any further host testing, connect without
   a custom receiver, check the badge's honest security status, then check
   each shortcut in Keynote and PowerPoint with the slideshow focused.
3. Check rapid taps, long holds, both orientations, suspend/resume, and
   disconnect/HOME during key-down; confirm no stuck keys.
4. Check out-of-range reconnect, Mac restart, badge restart and repeated
   reopen. Determine whether bonding survives on this installed firmware;
   forget/re-pair if it does not.
5. Check full/malformed bond storage, missing APIs, Security Manager timeout,
   unavailable BLE, memory usage and battery-only operation.

## Sources

- [2026 app contract](https://github.com/badger/home/blob/main/badge/AGENTS.md)
  and [hardware](https://github.com/badger/home/blob/main/hardware/README.md).
- [MicroPython Bluetooth API](https://docs.micropython.org/en/latest/library/bluetooth.html),
  including security flags, pairing events, and notification subscriptions.
- [MicroPython BTstack backend](https://github.com/micropython/micropython/blob/master/extmod/btstack/modbluetooth_btstack.c)
  and [pairing compile gate](https://github.com/micropython/micropython/blob/master/extmod/modbluetooth.h).
- [Vendor-pinned backend](https://github.com/pimoroni/micropython/blob/41d1e007537f9b15cf13a9436228dd59d6485eca/extmod/btstack/modbluetooth_btstack.c)
  and [pinned BTstack Just Works state machine](https://github.com/bluekitchen/btstack/blob/77e752abd6a0992334047a48038a5a3960e5c6bc/src/ble/sm.c#L1025-L1070).
- [Tufty Bluetooth configuration](https://github.com/pimoroni/tufty2350/blob/main/board/mpconfigboard.cmake)
  and [manifest](https://github.com/pimoroni/tufty2350/blob/main/board/manifest.py).
- [Bluetooth SIG HID over GATT Profile 1.0](https://www.bluetooth.com/specifications/specs/hid-over-gatt-profile-1-0/)
  for report routing, boot protocol, suspend, and security.
- [USB HID usage tables](https://usb.org/document-library/hid-usage-tables-16):
  Right Arrow 0x4F, Left Arrow 0x50, Escape 0x29, and B 0x05.
