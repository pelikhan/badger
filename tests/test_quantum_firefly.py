import importlib.util
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import create_autospec, patch


APP = Path(__file__).resolve().parents[1] / "badge/apps/quantum_diffusion"


class RadioAPI:
    def open(self, handler):
        pass

    def advertise(self, owner, interval_us, *, adv_data, connectable):
        pass

    def scan(self, owner, duration_ms, interval_us, window_us):
        pass

    def close(self):
        pass


def load_firefly(clock, identity=b"LOCALBAD", radio=None):
    radio = radio or create_autospec(RadioAPI, instance=True)
    link = SimpleNamespace(APP="app", radio=radio)
    spec = importlib.util.spec_from_file_location("quantum_firefly", APP / "firefly.py")
    module = importlib.util.module_from_spec(spec)
    with patch.object(sys, "path", [str(APP)] + sys.path), patch.dict(sys.modules, {
        "badgeware": SimpleNamespace(link=link),
        "machine": SimpleNamespace(unique_id=lambda: identity),
        "time": SimpleNamespace(
            ticks_ms=lambda: clock[0],
            ticks_diff=lambda end, start: ((end - start + (1 << 29)) % (1 << 30)) - (1 << 29),
        ),
    }):
        spec.loader.exec_module(module)
    return module


class FireflyTests(unittest.TestCase):
    def setUp(self):
        self.clock = [0]
        self.module = load_firefly(self.clock)
        self.firefly = self.module.Fireflies()
        self.firefly.start()
        self.firefly.phase = 0
        self.firefly.since_flash = 0

    def receive(self, identity=b"OTHERBAD", sequence=1, phase=1500,
                color_phase=0, color_reference=None, color_hops=0):
        payload = self.module.advertisement(
            identity, sequence, phase, color_phase, color_reference, color_hops,
        )
        self.firefly.receive(5, (0, b"abcdef", 0, -40, payload))

    def test_wire_format_and_malformed_advertisements(self):
        module = self.module
        payload = module.advertisement(b"OTHERBAD", 65535, 1999, 19999)
        self.assertLessEqual(len(payload), 31)
        self.assertEqual(module.read_beacon(payload), (b"OTHERBAD", 65535, 1999, 19999, b"OTHERBAD", 0))
        for length in range(len(payload)):
            self.assertIsNone(module.read_beacon(payload[:length]))
        for payload in (
            b"", b"\x00", b"\xff", b"\x02\xff\xff",
            payload.replace(module.MAGIC, b"QF\x01"),
            module.advertisement(b"OTHERBAD", 1, 2000, 0),
            module.advertisement(b"OTHERBAD", 1, 0, 20000),
            module.advertisement(b"OTHERBAD", 1, 0, 0, b"FIRSTBAD", 0),
            module.advertisement(b"OTHERBAD", 1, 0, 0, b"OTHERBAD", 1),
        ):
            self.assertIsNone(module.read_beacon(payload))
        for byte in range(256):
            module.read_beacon(bytes((byte,)) * 31)

    def test_shared_radio_is_nonconnectable_without_link_or_pairing(self):
        radio = self.module.link.radio
        radio.open.assert_called_once_with(self.firefly.receive)
        radio.scan.assert_called_once_with(
            owner="app", duration_ms=0, interval_us=100000, window_us=30000,
        )
        self.assertFalse(radio.advertise.call_args.kwargs["connectable"])
        self.assertFalse(hasattr(self.module.link, "Link"))
        self.firefly.stop()
        self.firefly.stop()
        radio.close.assert_called_once()

    def test_standalone_period_is_wall_time_based(self):
        flashes = []
        for elapsed in range(25, 6025, 25):
            self.clock[0] = elapsed
            if self.firefly.update():
                flashes.append(elapsed)
            self.assertEqual(self.firefly.color_phase_ms, elapsed % self.module.CYCLE_MS)
        self.assertEqual(flashes, [2000, 4000, 6000])

    def test_stall_and_tick_wrap_do_not_emit_a_backlog(self):
        self.firefly.last_tick = (1 << 30) - 100
        self.clock[0] = 50
        self.assertFalse(self.firefly.update())
        self.assertEqual(self.firefly.phase, 150)
        self.clock[0] += 60000
        self.assertTrue(self.firefly.update())
        self.assertFalse(self.firefly.update())
        self.assertEqual(self.firefly.phase, 150)

    def test_coupling_advances_or_delays_with_a_bounded_correction(self):
        for phase, target, expected in ((800, 1400, 900), (800, 200, 700), (1950, 100, 1987)):
            with self.subTest(phase=phase, target=target):
                self.firefly.peers.clear()
                self.firefly.phase = phase
                self.firefly.since_flash = phase
                self.receive(phase=target)
                self.assertFalse(self.firefly.update())
                self.assertEqual(self.firefly.phase, expected)

    def test_refractory_period_prevents_echo_flashes(self):
        self.firefly.phase = 1980
        self.firefly.since_flash = 1980
        self.receive(phase=400)
        self.assertTrue(self.firefly.update())
        self.assertEqual(self.firefly.phase, 80)
        self.receive(sequence=2, phase=1900)
        self.assertFalse(self.firefly.update())
        self.assertEqual(self.firefly.phase, 80)

    def test_duplicate_out_of_order_self_and_foreign_beacons_are_ignored(self):
        self.firefly.phase = self.firefly.since_flash = 800
        self.receive(sequence=65535, phase=1400)
        self.firefly.update()
        self.assertEqual(self.firefly.phase, 900)
        for sequence in (65535, 65534):
            self.receive(sequence=sequence, phase=1400)
            self.firefly.update()
            self.assertEqual(self.firefly.phase, 900)
        self.receive(sequence=0, phase=1400)
        self.firefly.update()
        self.assertEqual(self.firefly.phase, 1000)
        self.receive(identity=self.firefly.identity, phase=1500)
        self.firefly.receive(5, (0, b"abcdef", 0, -40, b"unrelated BLE data"))
        self.firefly.update()
        self.assertEqual(self.firefly.phase, 1000)
        self.assertEqual(len(self.firefly.peers), 1)

    def test_old_queued_beacons_do_not_pull_phase(self):
        self.firefly.phase = self.firefly.since_flash = 800
        self.receive(phase=1400)
        self.clock[0] = 500
        self.firefly.update()
        self.assertEqual(self.firefly.phase, 1300)

    def test_fast_incoming_corrections_never_make_phase_negative(self):
        self.firefly.phase = self.firefly.since_flash = 300
        for sequence in range(1, 30):
            self.receive(sequence=sequence, phase=(self.firefly.phase - 500) % 2000)
            self.firefly.update()
            self.assertGreaterEqual(self.firefly.phase, 0)
        self.firefly.broadcast()

    def test_callback_copies_borrowed_data_and_filters_unrelated_traffic(self):
        for _ in range(100):
            self.firefly.receive(5, (0, b"abcdef", 0, -40, b"unrelated"))
        self.assertEqual(self.firefly.pending, [])
        payload = bytearray(self.module.advertisement(b"OTHERBAD", 1, 1400, 8000))
        self.firefly.receive(5, (0, b"abcdef", 0, -40, memoryview(payload)))
        payload[:] = b"\x00" * len(payload)
        self.assertEqual(self.firefly.pending[0][0], (b"OTHERBAD", 1, 1400, 8000, b"OTHERBAD", 0))

    def test_color_phase_slews_toward_reference_without_waiting_for_a_flash(self):
        self.receive(identity=b"FIRSTBAD", phase=0, color_phase=8000)
        self.assertFalse(self.firefly.update())
        self.assertEqual(self.firefly.color_phase_ms, self.module.CORRECTION_MS)
        self.assertEqual(self.firefly.color_reference, b"FIRSTBAD")
        for _ in range(79):
            self.firefly.update()
        self.assertEqual(self.firefly.color_phase_ms, 8000)
        self.assertEqual(self.firefly.phase, 0)

    def test_color_wrap_takes_shortest_path_and_survives_missing_reference(self):
        self.firefly.color_phase_ms = self.firefly.color_clock_ms = 19950
        self.receive(identity=b"FIRSTBAD", color_phase=50)
        self.firefly.update()
        self.assertEqual(self.firefly.color_phase_ms, 50)
        self.clock[0] = 10000
        self.firefly.update()
        self.assertEqual(self.firefly.color_phase_ms, 10050)
        self.assertEqual(self.firefly.color_clock_ms, 10050)
        self.assertEqual(self.firefly.peers, {})

    def test_reference_choice_is_deterministic_and_does_not_depend_on_receive_order(self):
        for order in ((b"FIRSTBAD", b"ABADGE00"), (b"ABADGE00", b"FIRSTBAD")):
            firefly = self.module.Fireflies()
            firefly.start()
            for identity in order:
                packet = self.module.advertisement(identity, 1, 0, 8000)
                firefly.receive(5, (0, b"abcdef", 0, -40, packet))
            firefly.update()
            self.assertEqual(firefly.color_reference, b"ABADGE00")
            packet = self.module.advertisement(b"FIRSTBAD", 2, 0, 12000)
            firefly.receive(5, (0, b"abcdef", 0, -40, packet))
            firefly.update()
            self.assertEqual(firefly.color_clock_ms, 8000)

    def test_relays_forward_reference_clock_not_their_still_converging_palette(self):
        self.receive(
            identity=b"ZRELAY00", color_phase=8000,
            color_reference=b"FIRSTBAD", color_hops=1,
        )
        self.firefly.update()
        self.assertEqual(self.firefly.color_phase_ms, 100)
        self.assertEqual(self.firefly.color_clock_ms, 8000)
        self.assertEqual(self.firefly.color_hops, 2)
        self.firefly.broadcast()
        packet = self.module.link.radio.advertise.call_args.kwargs["adv_data"]
        self.assertEqual(self.module.read_beacon(packet)[3:], (8000, b"FIRSTBAD", 2))
        self.receive(
            identity=b"ARELAY00", color_phase=12000,
            color_reference=b"FIRSTBAD", color_hops=2,
        )
        self.firefly.update()
        self.assertEqual(self.firefly.color_clock_ms, 8000)

    def test_memory_and_group_correction_are_bounded(self):
        self.firefly.phase = self.firefly.since_flash = 800
        for number in range(100):
            self.receive(identity=struct.pack("<Q", number), phase=1400)
        self.assertEqual(len(self.firefly.pending), self.module.MAX_MESSAGES)
        self.firefly.update()
        self.assertEqual(len(self.firefly.peers), self.module.MAX_PEERS)
        self.assertEqual(self.firefly.phase, 900)
        for number in range(100, 200):
            self.receive(identity=struct.pack("<Q", number), phase=1400)
            self.firefly.update()
        self.assertEqual(len(self.firefly.peers), self.module.MAX_PEERS)
        self.clock[0] = self.module.PEER_TIMEOUT_MS
        self.firefly.update()
        self.assertEqual(self.firefly.peers, {})

    def test_radio_failure_is_explicit_and_closes_resources(self):
        firefly = self.module.Fireflies()
        self.module.link.radio.scan.side_effect = OSError("radio unavailable")
        with self.assertRaisesRegex(OSError, "radio unavailable"):
            firefly.start()
        self.assertFalse(firefly.running)
        self.module.link.radio.close.assert_called_once()

    def test_groups_converge_without_pairing_including_antiphase(self):
        for phases, lossy in (
            ((0, 1000), False), ((50, 550, 1050, 1550), False),
            ((100, 350, 800, 1250, 1750), False),
            ((100, 350, 800, 1250, 1750), True),
        ):
            with self.subTest(phases=phases, lossy=lossy):
                clock = [0]
                group = []
                packets = []
                for index, phase in enumerate(phases):
                    radio = create_autospec(RadioAPI, instance=True)
                    module = load_firefly(clock, struct.pack("<Q", index), radio)
                    firefly = module.Fireflies()
                    firefly.phase = firefly.since_flash = phase
                    firefly.color_phase_ms = firefly.color_clock_ms = index * 4000
                    firefly.start()
                    if lossy:
                        firefly.beacon_elapsed = index * self.module.BEACON_MS // len(phases)
                    group.append(firefly)
                    packets.append(radio.advertise.call_args.kwargs["adv_data"])
                for tick in range(25, 60025, 25):
                    clock[0] = tick
                    for index, firefly in enumerate(group):
                        for sender, packet in enumerate(packets):
                            audible = not lossy or (
                                abs(sender - index) == 1
                                and (tick // 25 + index + sender) % 3 != 0
                            )
                            if sender != index and audible:
                                firefly.receive(5, (0, b"abcdef", 0, -40, packet))
                    for index, firefly in enumerate(group):
                        firefly.update()
                        packets[index] = firefly.broadcast.__globals__["link"].radio.advertise.call_args.kwargs["adv_data"]
                offsets = [
                    (firefly.phase - group[0].phase + 1000) % 2000 - 1000
                    for firefly in group
                ]
                self.assertLessEqual(max(offsets) - min(offsets), 100)
                color_offsets = [
                    (firefly.color_phase_ms - group[0].color_phase_ms + 10000) % 20000 - 10000
                    for firefly in group
                ]
                self.assertLessEqual(max(color_offsets) - min(color_offsets), self.module.BEACON_MS)
                self.assertTrue(all(firefly.color_reference == group[0].identity for firefly in group))
                with patch.object(sys, "path", [str(APP)] + sys.path):
                    from palette import BrandPalette
                colors = []
                for firefly in group:
                    palette = BrandPalette()
                    palette.set_phase(firefly.color_phase_ms)
                    colors.append(palette.rgb)
                self.assertLessEqual(max(
                    abs(channel - colors[0][index])
                    for rgb in colors for index, channel in enumerate(rgb)
                ), 12)


if __name__ == "__main__":
    unittest.main()
