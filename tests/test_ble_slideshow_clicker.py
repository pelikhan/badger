import errno
import importlib.util
import json
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


APP = Path(__file__).resolve().parents[1] / "badge/apps/ble_slideshow_clicker"
SPEC = importlib.util.spec_from_file_location("clicker", APP / "clicker.py")
clicker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(clicker)


class BLE:
    def __init__(self):
        self.enabled = False
        self.handler = None
        self.values = {}
        self.writes = []
        self.notifications = []
        self.subscribed = set()
        self.advertisements = []
        self.disconnections = []
        self.pairs = []
        self.passkeys = []
        self.fail_passkey = False
        self.fail_report = False

    def active(self, value=None):
        if value is not None:
            self.enabled = value
        return self.enabled

    def config(self, **values):
        if not hasattr(self, "configuration"):
            self.configuration = {}
        self.configuration.update(values)

    def irq(self, handler):
        self.handler = handler

    def gatts_register_services(self, services):
        self.services = services
        result = []
        handle = 10
        for _, characteristics in services:
            handles = []
            for characteristic in characteristics:
                handles.append(handle)
                handle += 1
                if characteristic[1] & 0x10:
                    handle += 1  # Stack-owned CCCD is not a returned handle.
                for _ in characteristic[2] if len(characteristic) == 3 else ():
                    handles.append(handle)
                    handle += 1
            result.append(tuple(handles))
        return result

    def gatts_write(self, handle, value, send_update=False):
        if send_update and self.fail_report:
            raise OSError(errno.EIO)
        self.values[handle] = value
        self.writes.append((handle, value, send_update))
        if send_update and handle in self.subscribed:
            self.notifications.append((handle, value))

    def gatts_read(self, handle):
        return self.values[handle]

    def gap_advertise(self, interval, **kwargs):
        self.advertisements.append((interval, kwargs))

    def gap_pair(self, connection):
        self.pairs.append(connection)

    def gap_passkey(self, connection, action, passkey):
        if self.fail_passkey:
            raise OSError(errno.EOPNOTSUPP)
        self.passkeys.append((connection, action, passkey))

    def gap_disconnect(self, connection):
        self.disconnections.append(connection)

    def event(self, event, *data):
        return self.handler(event, data)


def module(ble):
    return types.SimpleNamespace(BLE=lambda: ble, UUID=lambda value: value)


class ClickerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "bonds.json")
        self.store = clicker.BondStore(self.path)
        self.app = clicker.Clicker(self.store)
        self.ble = BLE()
        self.app.start(module(self.ble), 75)
        self.addCleanup(self.app.close)

    def connect(self, secure=True):
        self.ble.event(1, 7, 0, b"abcdef")
        self.app.tick(0)
        if secure:
            self.ble.event(28, 7, True, True, True, 16)
        self.ble.subscribed.add(self.app.input)

    def write(self, handle, value):
        self.ble.values[handle] = value
        self.ble.event(3, 7, handle)
        self.app.tick(0)

    def test_service_definition_and_security(self):
        self.assertEqual(self.ble.configuration, {
            "gap_name": "Badger Clicker", "bond": True, "mitm": False,
            "io": 3, "le_secure": False,
        })
        hid, device, battery = self.ble.services
        self.assertEqual(hid[0], 0x1812)
        self.assertEqual(device[0], 0x180A)
        self.assertEqual(battery[0], 0x180F)
        self.assertEqual([char[0] for char in hid[1]], [
            0x2A4A, 0x2A4B, 0x2A4C, 0x2A4E, 0x2A4D, 0x2A4D,
            0x2A22, 0x2A32,
        ])
        self.assertFalse(any(char[1] & (0x0400 | 0x2000 | 0x0200 | 0x1000)
                             for char in hid[1]))
        self.assertEqual(hid[1][4][2][0][0], 0x2908)
        self.assertEqual(self.ble.values[self.app.input + 2], b"\x01\x01")
        self.assertIn(clicker.REPORT_MAP, self.ble.values.values())
        self.assertIn(b"\x01\x02", self.ble.values.values())

    def test_advertisement_has_hid_appearance_and_name(self):
        data = clicker.ADVERTISEMENT
        self.assertLessEqual(len(data), 31)
        fields = {}
        while data:
            length = data[0]
            fields[data[1]] = data[2:length + 1]
            data = data[length + 1:]
        self.assertEqual(fields[3], b"\x12\x18")
        self.assertEqual(fields[0x19], b"\xc1\x03")
        self.assertEqual(fields[9], b"Badger Clicker")

    def test_report_descriptor_matches_input_and_output_payload_sizes(self):
        sizes = {}
        report_id, count, size, offset = 0, 0, 0, 0
        while offset < len(clicker.REPORT_MAP):
            prefix = clicker.REPORT_MAP[offset]
            length = (0, 1, 2, 4)[prefix & 3]
            data = clicker.REPORT_MAP[offset + 1:offset + 1 + length]
            tag = prefix & 0xFC
            value = int.from_bytes(data, "little")
            if tag == 0x74:
                size = value
            elif tag == 0x94:
                count = value
            elif tag == 0x84:
                report_id = value
            elif tag in (0x80, 0x90):
                key = (report_id, tag)
                sizes[key] = sizes.get(key, 0) + count * size
            offset += 1 + length
        self.assertEqual(offset, len(clicker.REPORT_MAP))
        self.assertEqual(sizes, {(1, 0x80): 64, (1, 0x90): 8})

    def test_battery_service_uses_validated_runtime_percentage(self):
        self.assertEqual(self.ble.values[self.app.battery], b"\x4b")
        self.connect()
        self.ble.subscribed.add(self.app.battery)
        self.app.set_battery(60)
        self.assertEqual(self.ble.notifications[-1], (self.app.battery, b"\x3c"))
        for value in (True, -1, 101, None, "60"):
            with self.assertRaises(ValueError):
                self.app.set_battery(value)

    def test_just_works_accepts_unauthenticated_unbonded_encryption(self):
        self.assertFalse(self.app.tap("next"))
        self.connect(False)
        self.ble.event(28, 7, False, False, False, 0)
        self.assertFalse(self.app.tap("next"))
        self.assertFalse(self.ble.notifications)
        self.ble.event(28, 7, True, False, False, 7)
        self.assertTrue(self.app.ready)
        self.assertIn("no bond", self.app.status)
        self.assertTrue(self.app.tap("next"))

    def test_report_shape_timing_and_all_controls(self):
        self.connect()
        for action, usage in clicker.KEYS.items():
            self.assertTrue(self.app.tap(action))
            self.app.tick(40)
            self.assertEqual(self.ble.notifications[-1], (
                self.app.input, bytes((0, 0, usage, 0, 0, 0, 0, 0))))
            self.assertEqual(len(self.ble.notifications[-1][1]), 8)
            count = len(self.ble.notifications)
            self.app.tick(39)
            self.assertEqual(len(self.ble.notifications), count)
            self.app.tick(1)
            self.assertEqual(self.ble.notifications[-1][1], bytes(8))
            self.assertFalse(self.app.key_down)
        count = len(self.ble.notifications)
        self.app.tick(10000)
        self.assertEqual(len(self.ble.notifications), count)

    def test_same_key_has_release_and_gap_before_repeat(self):
        self.connect()
        self.app.tap("next")
        self.app.tap("next")
        self.app.tick(0)
        self.app.tick(40)
        self.app.tick(39)
        self.assertEqual(len(self.ble.notifications), 2)
        self.app.tick(1)
        self.assertEqual(self.ble.notifications[-1][1][2], 0x4F)

    def test_unsubscribed_host_gets_no_notifications(self):
        self.connect()
        self.ble.subscribed.clear()
        self.app.tap("next")
        self.app.tick(0)
        self.app.tick(40)
        self.assertEqual(self.ble.notifications, [])
        self.assertEqual(self.ble.values[self.app.input], bytes(8))

    def test_queue_is_bounded(self):
        self.connect()
        for _ in range(4):
            self.assertTrue(self.app.tap("next"))
        self.assertFalse(self.app.tap("next"))
        self.assertEqual(len(self.app.queue), 4)
        self.assertIn("Busy", self.app.last_action)

    def test_pairing_timeout(self):
        self.connect(False)
        self.assertEqual(self.ble.pairs, [7])
        self.app.tick(60000)
        self.assertEqual(self.ble.disconnections, [7])
        self.assertFalse(self.app.tap("next"))

    def test_host_initiated_encryption_does_not_restart_the_exchange(self):
        self.ble.event(1, 7, 0, b"abcdef")
        self.ble.event(28, 7, True, False, True, 16)
        self.app.tick(0)
        self.assertEqual(self.ble.pairs, [])
        self.assertTrue(self.app.ready)

    def test_host_requiring_passkey_is_reported_as_real_blocker(self):
        self.connect(False)
        self.ble.event(31, 7, 4, 123456)
        with self.assertRaisesRegex(RuntimeError, "Host requires passkey"):
            self.app.tick(0)
        self.assertEqual(self.ble.passkeys, [])

    def test_suspend_releases_key_and_discards_queue(self):
        self.connect()
        self.app.tap("next")
        self.app.tick(0)
        self.app.tap("previous")
        self.write(self.app.control, b"\x00")
        self.assertTrue(self.app.suspended)
        self.assertFalse(self.app.tap("next"))
        self.assertEqual(self.app.queue, [])
        self.assertEqual(self.ble.notifications[-1][1], bytes(8))
        self.write(self.app.control, b"\x01")
        self.assertTrue(self.app.tap("next"))

    def test_protocol_switch_releases_old_handle_and_uses_boot_report(self):
        self.connect()
        self.app.tap("next")
        self.app.tick(0)
        self.write(self.app.mode, b"\x00")
        self.assertEqual(self.ble.notifications[-1], (self.app.input, bytes(8)))
        self.ble.subscribed.add(self.app.boot_input)
        self.app.tap("previous")
        self.app.tick(40)
        self.assertEqual(self.ble.notifications[-1][0], self.app.boot_input)
        self.assertEqual(self.ble.notifications[-1][1][2], 0x50)
        self.app.tick(40)
        self.assertEqual(self.ble.notifications[-1], (self.app.boot_input, bytes(8)))

    def test_disconnect_reconnect_resets_protocol_and_drops_stale_keys(self):
        self.connect()
        self.write(self.app.mode, b"\x00")
        self.app.tap("next")
        self.app.tick(0)
        self.app.tap("previous")
        self.ble.event(2, 7, 0, b"abcdef")
        self.assertFalse(self.app.tap("next"))
        self.app.tick(0)
        self.assertEqual(self.app.queue, [])
        self.assertFalse(self.app.key_down)
        self.assertEqual(self.ble.values[self.app.boot_input], bytes(8))
        self.assertEqual(self.ble.advertisements[-1][0], 100000)
        self.connect()
        self.assertEqual(self.app.protocol, 1)
        self.assertEqual(self.ble.values[self.app.mode], b"\x01")

    def test_fast_host_protocol_write_is_not_overwritten_on_connect(self):
        self.ble.event(1, 7, 0, b"abcdef")
        self.ble.event(28, 7, True, True, True, 16)
        self.write(self.app.mode, b"\x00")
        self.assertEqual(self.app.protocol, 0)
        self.assertEqual(self.ble.values[self.app.mode], b"\x00")

    def test_stale_connection_events_are_ignored(self):
        self.connect(False)
        self.ble.event(28, 99, True, True, True, 16)
        self.ble.event(31, 99, 3, 0)
        self.ble.event(2, 99, 0, b"abcdef")
        self.assertFalse(self.app.ready)
        self.assertEqual(self.app.connection, 7)

    def test_security_loss_stops_further_reports(self):
        self.connect()
        self.ble.event(28, 7, False, False, False, 0)
        with self.assertRaisesRegex(RuntimeError, "Link encryption lost"):
            self.app.tick(0)
        self.assertFalse(self.app.tap("next"))

    def test_irq_bond_buffers_are_copied_and_saved_outside_callback(self):
        key, value = bytearray(b"key"), bytearray(b"value")
        self.assertTrue(self.ble.event(30, 1, memoryview(key), memoryview(value)))
        key[0], value[0] = 0, 0
        self.assertFalse(Path(self.path).exists())
        self.assertEqual(self.ble.event(29, 1, 0, b"key"), b"value")
        self.app.tick(0)
        self.assertEqual(clicker.BondStore(self.path).get(1, 0, None), b"value")

    def test_close_releases_key_and_cleans_up_once(self):
        self.connect()
        self.app.tap("next")
        self.app.tick(0)
        self.app.close()
        self.assertEqual(self.ble.notifications[-1][1], bytes(8))
        self.assertEqual(self.ble.disconnections, [7])
        self.assertIsNone(self.ble.handler)
        self.assertFalse(self.ble.enabled)
        self.assertFalse(self.app.tap("next"))
        self.app.close()
        self.assertEqual(self.ble.disconnections, [7])

    def test_close_deactivates_radio_even_if_release_fails(self):
        self.connect()
        self.ble.fail_report = True
        with self.assertRaises(OSError):
            self.app.close()
        self.assertFalse(self.ble.enabled)
        self.assertIsNone(self.ble.handler)
        self.assertEqual(self.ble.disconnections, [7])

    def test_already_active_radio_is_not_taken_over(self):
        other = BLE()
        other.active(True)
        app = clicker.Clicker(self.store)
        with self.assertRaisesRegex(RuntimeError, "already active"):
            app.start(module(other), 75)
        app.close()
        self.assertTrue(other.enabled)
        self.assertEqual(other.advertisements, [])

    def test_missing_pairing_api_allows_unverified_link(self):
        other = BLE()
        other.gap_pair = None
        app = clicker.Clicker(self.store)
        app.start(module(other), 75)
        self.addCleanup(app.close)
        other.subscribed.add(app.input)
        other.event(1, 8, 0, b"abcdef")
        app.tick(0)
        self.assertTrue(app.ready)
        self.assertFalse(app.encrypted)
        self.assertIn("plaintext", app.warning)
        self.assertTrue(app.tap("next"))
        app.tick(0)
        self.assertEqual(other.notifications[-1][1][2], 0x4F)

    def test_just_works_does_not_require_passkey_api(self):
        other = BLE()
        other.gap_passkey = None
        app = clicker.Clicker(self.store)
        app.start(module(other), 75)
        self.addCleanup(app.close)
        other.event(1, 8, 0, b"abcdef")
        app.tick(0)
        other.event(28, 8, True, False, True, 16)
        self.assertTrue(app.ready)
        self.assertTrue(app.encrypted)
        self.assertTrue(app.tap("next"))
        self.assertEqual(other.pairs, [8])

    def test_unsupported_security_config_allows_unverified_link(self):
        other = BLE()
        configure = other.config

        def config(**kwargs):
            if "io" in kwargs:
                raise ValueError("unknown config param")
            configure(**kwargs)

        other.config = config
        app = clicker.Clicker(self.store)
        app.start(module(other), 75)
        self.addCleanup(app.close)
        other.event(1, 8, 0, b"abcdef")
        app.tick(0)
        self.assertTrue(app.ready)
        self.assertEqual(other.pairs, [])
        self.assertIn("UNVERIFIED", app.warning)

    def test_pairing_not_implemented_downgrades_explicitly(self):
        self.ble.gap_pair = lambda handle: (_ for _ in ()).throw(OSError(errno.EOPNOTSUPP))
        self.connect(False)
        self.assertTrue(self.app.ready)
        self.assertFalse(self.app.pairing_available)
        self.assertIn("plaintext", self.app.warning)
        self.assertTrue(self.app.tap("next"))

    def test_unexpected_pairing_failure_does_not_silently_downgrade(self):
        self.ble.gap_pair = lambda handle: (_ for _ in ()).throw(OSError(errno.EIO))
        self.ble.event(1, 7, 0, b"abcdef")
        with self.assertRaises(OSError):
            self.app.tick(0)
        self.assertFalse(self.app.ready)

    def test_close_deactivates_radio_even_if_irq_detach_fails(self):
        def detach(handler):
            raise OSError(errno.EIO)

        self.ble.irq = detach
        with self.assertRaises(OSError):
            self.app.close()
        self.assertFalse(self.ble.enabled)


class BondStoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "bonds.json"
        self.store = clicker.BondStore(str(self.path))

    def test_index_update_delete_and_round_trip(self):
        self.store.set(1, b"a", b"first")
        self.store.set(2, b"b", b"second")
        self.store.set(1, b"c", b"third")
        self.assertEqual(self.store.get(1, 1, None), b"third")
        self.assertIsNone(self.store.get(1, 2, None))
        self.store.set(1, b"a", b"updated")
        self.store.set(2, b"b", None)
        self.store.save()
        self.assertFalse(self.store.dirty)
        self.assertFalse(Path(str(self.path) + ".tmp").exists())
        reopened = clicker.BondStore(str(self.path))
        self.assertEqual(reopened.get(1, 0, b"a"), b"updated")
        self.assertIsNone(reopened.get(2, 0, b"b"))

    def test_bounded_store_rejects_excess_and_invalid_records(self):
        for index in range(clicker.MAX_SECRETS):
            self.assertTrue(self.store.set(1, bytes((index,)), b"x" * 128))
        self.assertFalse(self.store.set(1, b"new", b"value"))
        self.assertFalse(self.store.set(1, b"", b"value"))
        self.assertFalse(self.store.set(1, b"key", b"x" * 129))
        self.store.save()
        self.assertLess(self.path.stat().st_size, clicker.MAX_STORE_BYTES)
        self.assertEqual(len(clicker.BondStore(str(self.path)).secrets), clicker.MAX_SECRETS)

    def test_corrupt_storage_is_not_silently_discarded(self):
        for value in ("bad json", "null", "{}", '[[1,"not hex","61"]]',
                      '[[true,"61","62"]]', '[[1,"","62"]]',
                      '[[1,"61","62"],[1,"61","63"]]', " " * 8193):
            self.path.write_text(value)
            with self.assertRaisesRegex(ValueError, "Invalid BLE bond store"):
                clicker.BondStore(str(self.path))

    def test_permission_and_save_failures_propagate(self):
        with patch("builtins.open", side_effect=OSError(errno.EACCES)):
            with self.assertRaises(OSError):
                clicker.BondStore(str(self.path))
        self.store.set(1, b"a", b"b")
        with patch.object(clicker.os, "rename", side_effect=OSError(errno.EIO)):
            with self.assertRaises(OSError):
                self.store.save()
        self.assertTrue(self.store.dirty)

    def test_secret_change_during_save_is_not_lost(self):
        self.store.set(1, b"a", b"b")
        rename = clicker.os.rename

        def interrupted(source, target):
            self.store.set(1, b"c", b"d")
            rename(source, target)

        with patch.object(clicker.os, "rename", side_effect=interrupted):
            self.store.save()
        self.assertTrue(self.store.dirty)
        self.store.save()
        self.assertEqual(clicker.BondStore(str(self.path)).get(1, 0, b"c"), b"d")


class EntryPointTests(unittest.TestCase):
    def test_runtime_controls_and_lifecycle_cleanup(self):
        ble = BLE()
        badge = types.SimpleNamespace(
            ticks_delta=40, mode=lambda mode: None, battery_level=lambda: 75)
        badge.pressed = lambda name: name in badge.down
        screen = types.SimpleNamespace(
            width=320, height=240, clip=None,
            rectangle=lambda clip: None, text=lambda *args: None,
            measure_text=lambda text: types.SimpleNamespace(x=len(text) * 6),
        )
        colors = types.SimpleNamespace(rgb=lambda *args: args)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = clicker.BondStore(str(Path(directory.name) / "bonds.json"))
        real_bond_store = clicker.BondStore
        namespace = {
            "badge": badge, "screen": screen, "color": colors,
            "font": types.SimpleNamespace(sins=None), "HIRES": 1, "VSYNC": 2,
        }
        for name in ("RIGHT", "SELECT", "LEFT", "BACK", "DOWN", "UP", "MENU"):
            namespace["BUTTON_" + name] = name
        captured = []

        def run(update):
            ble.event(1, 7, 0, b"abcdef")
            ble.event(28, 7, True, True, True, 16)
            ble.subscribed.add(update.__globals__["clicker"].input)
            for button in ("RIGHT", "SELECT", "LEFT", "BACK", "DOWN", "UP", "MENU"):
                badge.down = {button}
                update()
                badge.down = set()
                update()
                update()
                update()
            captured.extend(report[2] for _, report in ble.notifications if report != bytes(8))
        namespace["run"] = run
        with patch.dict(sys.modules, {"bluetooth": module(ble), "clicker": clicker}):
            with patch.object(clicker, "BondStore", return_value=store):
                with patch("os.chdir"):
                    old_path = sys.path[:]
                    try:
                        runpy.run_path(str(APP / "__init__.py"), init_globals=namespace)
                    finally:
                        sys.path[:] = old_path
        self.assertIs(clicker.BondStore, real_bond_store)
        self.assertEqual(captured, [0x4F, 0x4F, 0x50, 0x29, 0x05])
        self.assertFalse(ble.enabled)


if __name__ == "__main__":
    unittest.main()
