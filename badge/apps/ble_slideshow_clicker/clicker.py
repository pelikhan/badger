"""Frame-driven HID keyboard; no asyncio loop or badge globals required."""

import binascii
import errno
import json
import os

NAME = "Badger Clicker"
ATT_MTU = 23
# pid.codes 1209:0001 is allowed for private testing only, not product shipment.
PNP_ID = b"\x02\x09\x12\x01\x00\x00\x01"
EMPTY_REPORT = bytes(8)
KEYS = {"next": 0x4F, "previous": 0x50, "escape": 0x29, "blank": 0x05}
MAX_SECRETS = 16
MAX_STORE_BYTES = 8192
BOND_PATH = "/state/ble_slideshow_clicker_bonds.json"

# Keyboard application, report ID 1, modifiers, reserved byte, six keys,
# and the standard five keyboard LED output bits.
REPORT_MAP = bytes((
    0x05, 0x01, 0x09, 0x06, 0xA1, 0x01, 0x85, 0x01,
    0x05, 0x07, 0x19, 0xE0, 0x29, 0xE7, 0x15, 0x00,
    0x25, 0x01, 0x75, 0x01, 0x95, 0x08, 0x81, 0x02,
    0x95, 0x01, 0x75, 0x08, 0x81, 0x01,
    0x95, 0x05, 0x75, 0x01, 0x05, 0x08, 0x19, 0x01,
    0x29, 0x05, 0x91, 0x02,
    0x95, 0x01, 0x75, 0x03, 0x91, 0x01,
    0x95, 0x06, 0x75, 0x08, 0x15, 0x00, 0x25, 0x65,
    0x05, 0x07, 0x19, 0x00, 0x29, 0x65, 0x81, 0x00, 0xC0,
))

# Flags, complete HID service UUID list, keyboard appearance, complete name.
ADVERTISEMENT = (
    b"\x02\x01\x06\x03\x03\x12\x18\x03\x19\xc1\x03"
    + bytes((len(NAME) + 1, 0x09)) + NAME.encode()
)


class BondStore:
    """Bounded device-only secrets; IRQ callbacks never perform file I/O."""

    def __init__(self, path=BOND_PATH):
        self.path = path
        self.secrets = []
        self.dirty = False
        self.revision = 0
        try:
            with open(path, "r") as source:
                text = source.read(MAX_STORE_BYTES + 1)
        except OSError as error:
            if error.args[0] != errno.ENOENT:
                raise
            return
        try:
            rows = json.loads(text)
            if len(text) > MAX_STORE_BYTES or not isinstance(rows, list):
                raise ValueError()
            if len(rows) > MAX_SECRETS:
                raise ValueError()
            for row in rows:
                if not isinstance(row, list) or len(row) != 3:
                    raise ValueError()
                kind, key_hex, value_hex = row
                if type(kind) is not int or not 0 <= kind <= 255:
                    raise ValueError()
                if not isinstance(key_hex, str) or not isinstance(value_hex, str):
                    raise ValueError()
                key = binascii.unhexlify(key_hex)
                value = binascii.unhexlify(value_hex)
                if not 1 <= len(key) <= 64 or not 1 <= len(value) <= 128:
                    raise ValueError()
                if any(item[:2] == (kind, key) for item in self.secrets):
                    raise ValueError()
                self.secrets.append((kind, key, value))
        except (ValueError, TypeError):
            raise ValueError("Invalid BLE bond store; see README recovery")

    def get(self, kind, index, key):
        if key is not None:
            for entry_kind, entry_key, value in self.secrets:
                if entry_kind == kind and entry_key == key:
                    return value
        else:
            for entry_kind, _, value in self.secrets:
                if entry_kind == kind:
                    if index == 0:
                        return value
                    index -= 1
        return None

    def set(self, kind, key, value):
        key = bytes(key)
        if not 1 <= len(key) <= 64:
            return False
        for index, item in enumerate(self.secrets):
            if item[:2] == (kind, key):
                if value is None:
                    del self.secrets[index]
                else:
                    value = bytes(value)
                    if not 1 <= len(value) <= 128:
                        return False
                    if item[2] == value:
                        return True
                    self.secrets[index] = (kind, key, value)
                self.dirty = True
                self.revision += 1
                return True
        if value is None:
            return True
        value = bytes(value)
        if len(self.secrets) >= MAX_SECRETS or not 1 <= len(value) <= 128:
            return False
        self.secrets.append((kind, key, value))
        self.dirty = True
        self.revision += 1
        return True

    def save(self):
        if not self.dirty:
            return
        revision = self.revision
        rows = [
            [kind, binascii.hexlify(key).decode(), binascii.hexlify(value).decode()]
            for kind, key, value in self.secrets
        ]
        with open(self.path + ".tmp", "w") as target:
            json.dump(rows, target)
        os.rename(self.path + ".tmp", self.path)
        self.dirty = self.revision != revision


class Clicker:
    def __init__(self, store):
        self.store = store
        self.ble = None
        self.owns_radio = False
        self.closed = False
        self.error = None
        self.connection = None
        self.ready = False
        self.encrypted = False
        self.pairing_available = False
        self.warning = "UNVERIFIED: may be plaintext"
        self.pair_pending = False
        self.advertise_pending = False
        self.reset_pending = False
        self.writes = set()
        self.queue = []
        self.key_down = False
        self.wait_ms = 0
        self.connection_ms = 0
        self.protocol = 1
        self.suspended = False
        self.last_action = "No keys sent"
        self.security_problem = None
        self.status = "Starting"

    def start(self, bluetooth, battery_level):
        self.ble = bluetooth.BLE()
        if self.ble.active():
            raise RuntimeError("BLE already active; close the other app first")
        self.owns_radio = True
        self.ble.active(True)
        # Eight-byte keyboard reports fit the default ATT payload. Keep
        # negotiation small; the host reads the longer Report Map in chunks.
        self.ble.config(gap_name=NAME, mtu=ATT_MTU)
        if callable(getattr(self.ble, "gap_pair", None)):
            try:
                # NoInputNoOutput requests Just Works, not passkey entry.
                self.ble.config(io=3, mitm=False, le_secure=False, bond=True)
                self.pairing_available = True
                self.warning = "Just Works: NO MITM protection"
            except ValueError as error:
                print("BLE security configuration unavailable; allowing insecure link:", error)
            except OSError as error:
                if error.args[0] not in (errno.EOPNOTSUPP, errno.ENOSYS):
                    raise
                print("BLE security configuration unsupported; allowing insecure link:", error)
        else:
            print("BLE pairing API unavailable; allowing unverified / possibly plaintext link")
        self.ble.irq(self._irq)
        self._register(bluetooth.UUID)
        self.set_battery(battery_level)
        self._advertise()

    def _register(self, uuid):
        read, write, command, notify = 0x0002, 0x0008, 0x0004, 0x0010
        # Public attributes intentionally permit the user-requested insecure
        # fallback. When pairing APIs work, reports wait for encryption.
        r = read
        w = write | command
        services = (
            (uuid(0x1812), (
                (uuid(0x2A4A), r),
                (uuid(0x2A4B), r),
                (uuid(0x2A4C), command),
                (uuid(0x2A4E), r | command),
                (uuid(0x2A4D), r | notify, ((uuid(0x2908), r),)),
                (uuid(0x2A4D), r | w, ((uuid(0x2908), r),)),
                (uuid(0x2A22), r | notify),
                (uuid(0x2A32), r | w),
            )),
            (uuid(0x180A), (
                (uuid(0x2A29), read),
                (uuid(0x2A24), read),
                (uuid(0x2A50), read),
            )),
            (uuid(0x180F), ((uuid(0x2A19), r | notify),)),
        )
        hid, device, battery = self.ble.gatts_register_services(services)
        self.battery = battery[0]
        (info, report_map, self.control, self.mode, self.input, input_reference,
         self.output, output_reference, self.boot_input, self.boot_output) = hid
        for handle, value in (
            (info, b"\x11\x01\x00\x02"),
            (report_map, REPORT_MAP),
            (self.control, b"\x01"),
            (self.mode, b"\x01"),
            (input_reference, b"\x01\x01"),
            (output_reference, b"\x01\x02"),
            (self.output, b"\x00"),
            (self.boot_output, b"\x00"),
            (device[0], b"Badger community"),
            (device[1], b"Slideshow Clicker"),
            (device[2], PNP_ID),
        ):
            self.ble.gatts_write(handle, value)
        self._reset_reports()

    def set_battery(self, level):
        if type(level) is not int or not 0 <= level <= 100:
            raise ValueError("Invalid badge battery percentage")
        self.ble.gatts_write(self.battery, bytes((level,)), self.ready)

    def _advertise(self):
        self.ble.gap_advertise(100000, adv_data=ADVERTISEMENT)
        self.status = "Pair / reconnect"
        self.advertise_pending = False

    def _reset_reports(self):
        self.queue.clear()
        self.key_down = False
        self.wait_ms = 0
        self.ble.gatts_write(self.input, EMPTY_REPORT)
        self.ble.gatts_write(self.boot_input, EMPTY_REPORT)

    def _irq(self, event, data):
        if self.closed:
            return
        if event == 29:  # GET_SECRET must return synchronously.
            return self.store.get(*data)
        if event == 30:  # SET_SECRET copies ring-buffer views before returning.
            accepted = self.store.set(*data)
            if not accepted:
                self.security_problem = "Bond storage full or invalid"
            return accepted
        if event == 1:
            self.connection = data[0]
            self.queue.clear()
            self.writes.clear()
            self.advertise_pending = False
            self.reset_pending = True
            self.encrypted = False
            self.ready = not self.pairing_available
            self.warning = ("Just Works: NO MITM protection" if self.pairing_available
                            else "UNVERIFIED: may be plaintext")
            self.protocol = 1
            self.suspended = False
            self.connection_ms = 0
            self.pair_pending = self.pairing_available
            self.status = "Pairing (Just Works)" if self.pairing_available else "Connected / unverified"
        elif event == 2 and data[0] == self.connection:
            self.connection = None
            self.ready = False
            self.encrypted = False
            self.pair_pending = False
            self.queue.clear()
            self.writes.clear()
            self.advertise_pending = True
            self.status = "Disconnected"
        elif event == 3 and data[0] == self.connection:
            if data[1] in (self.control, self.mode):
                self.writes.add(data[1])
        elif event == 28 and data[0] == self.connection:
            _, encrypted, _, bonded, key_size = data
            was_encrypted = self.encrypted
            self.encrypted = bool(encrypted and 7 <= key_size <= 16)
            self.ready = self.encrypted or not self.pairing_available
            if self.encrypted:
                self.pair_pending = False
                self.warning = "Encrypted; peer NOT verified"
                self.status = "Encrypted / bonded" if bonded else "Encrypted / no bond"
            else:
                self.queue.clear()
                self.status = "Pairing (Just Works)" if self.pairing_available else "Connected / unverified"
                if was_encrypted:
                    self.security_problem = "Link encryption lost; reconnect"
        elif event == 31 and data[0] == self.connection:
            self.pair_pending = False
            self.security_problem = "Host requires passkey; retry Just Works"

    def tap(self, action):
        if not self.ready or self.suspended or self.error or self.closed:
            return False
        if len(self.queue) >= 4:
            self.last_action = "Busy - press again"
            return False
        self.queue.append(KEYS[action])
        self.last_action = action.capitalize()
        return True

    def _report(self, value):
        handle = self.input if self.protocol else self.boot_input
        # Unlike gatts_notify, send_update respects the host's CCCD subscription.
        # Report IDs belong in Report Reference, not the eight-byte payload.
        self.ble.gatts_write(handle, value, True)

    def tick(self, dt):
        if self.closed or self.error:
            return
        if self.security_problem:
            raise RuntimeError(self.security_problem)
        self.store.save()
        if self.reset_pending:
            self._reset_reports()
            if self.mode not in self.writes:
                self.ble.gatts_write(self.mode, b"\x01")
            if self.control not in self.writes:
                self.ble.gatts_write(self.control, b"\x01")
            self.reset_pending = False
        if self.advertise_pending:
            self._reset_reports()
            self._advertise()
        if self.connection is None:
            return
        self.connection_ms += max(0, dt)
        if not self.ready and self.connection_ms >= 60000:
            self.status = "Pairing timed out"
            self.ble.gap_disconnect(self.connection)
            return
        if self.pair_pending:
            self.pair_pending = False
            try:
                self.ble.gap_pair(self.connection)
            except OSError as error:
                if error.args[0] not in (errno.EOPNOTSUPP, errno.ENOSYS):
                    raise
                self.pairing_available = False
                self.ready = True
                self.warning = "UNVERIFIED: may be plaintext"
                self.status = "Connected / unverified"
                print("BLE pairing unsupported; allowing insecure link:", error)
        for handle in tuple(self.writes):
            self.writes.remove(handle)
            value = self.ble.gatts_read(handle)
            if value not in (b"\x00", b"\x01"):
                raise ValueError("Invalid HID control or protocol value")
            if self.key_down and self.ready:
                self._report(EMPTY_REPORT)
            self._reset_reports()
            if handle == self.mode:
                self.protocol = value[0]
            else:
                self.suspended = value == b"\x00"
        if not self.ready or self.suspended:
            return
        self.wait_ms = max(0, self.wait_ms - max(0, dt))
        if self.wait_ms:
            return
        if self.key_down:
            self._report(EMPTY_REPORT)
            self.key_down = False
            self.wait_ms = 40
        elif self.queue:
            key = self.queue.pop(0)
            self._report(bytes((0, 0, key, 0, 0, 0, 0, 0)))
            self.key_down = True
            self.wait_ms = 40

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.queue.clear()
        if not self.owns_radio:
            return
        try:
            if self.connection is not None and self.ready and hasattr(self, "input"):
                self._report(EMPTY_REPORT)
        finally:
            try:
                self.ble.gap_advertise(None)
                if self.connection is not None:
                    self.ble.gap_disconnect(self.connection)
            finally:
                try:
                    self.ble.irq(None)
                finally:
                    self.ble.active(False)
        self.store.save()
