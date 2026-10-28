"""Bounded, connectionless phase coupling through Badge Link's shared radio."""

import struct
from machine import unique_id
from time import ticks_diff, ticks_ms

from badgeware import link
from palette import CYCLE_MS


PERIOD_MS = 2000
BEACON_MS = 200
REFRACTORY_MS = 250
CORRECTION_MS = 100
PEER_TIMEOUT_MS = 6000
MAX_PEERS = 16
MAX_MESSAGES = 16
MAGIC = b"QF\x02"


def advertisement(identity, sequence, phase, color_phase, color_reference=None, color_hops=0):
    if color_reference is None:
        color_reference = identity
    data = (b"\xff\xff" + MAGIC + identity
            + struct.pack("<HHH", sequence, phase, color_phase)
            + color_reference + bytes((color_hops,)))
    return bytes((len(data) + 1, 0xff)) + data


def read_beacon(payload):
    offset = 0
    while offset < len(payload):
        length = payload[offset]
        end = offset + length + 1
        if not length or end > len(payload):
            return None
        if (length == 29 and payload[offset + 1] == 0xff
                and payload[offset + 2:offset + 7] == b"\xff\xff" + MAGIC):
            identity = payload[offset + 7:offset + 15]
            sequence, phase, color_phase = struct.unpack("<HHH", payload[offset + 15:offset + 21])
            if phase < PERIOD_MS and color_phase < CYCLE_MS:
                reference = payload[offset + 21:offset + 29]
                hops = payload[offset + 29]
                if (reference == identity) == (hops == 0):
                    return identity, sequence, phase, color_phase, reference, hops
            return None
        offset = end
    return None


class Fireflies:
    def __init__(self):
        self.identity = unique_id()
        if len(self.identity) != 8:
            raise RuntimeError("Quantum firefly sync requires an eight-byte badge ID")
        self.phase = sum((index + 1) * byte for index, byte in enumerate(self.identity)) % PERIOD_MS
        self.since_flash = self.phase
        self.color_phase_ms = 0
        self.color_clock_ms = 0
        self.color_reference = self.identity
        self.color_hops = 0
        self.sequence = 0
        self.peers = {}
        self.pending = []
        self.last_tick = ticks_ms()
        self.beacon_elapsed = BEACON_MS
        self.running = False

    def start(self):
        if self.running:
            return
        self.last_tick = ticks_ms()
        self.running = True
        try:
            link.radio.open(self.receive)
            self.broadcast()
            link.radio.scan(
                owner=link.APP, duration_ms=0, interval_us=100000, window_us=30000,
            )
        except (OSError, RuntimeError, TypeError, ValueError):
            self.stop()
            raise

    def stop(self):
        if self.running:
            self.running = False
            link.radio.close()
        self.pending.clear()
        self.peers.clear()

    def receive(self, event, data):
        if self.running and event == 5 and len(self.pending) < MAX_MESSAGES:
            payload = data[4]
            if len(payload) <= 31:
                # BLE callback data is borrowed; copy before returning to firmware.
                beacon = read_beacon(bytes(payload))
                if beacon is not None:
                    self.pending.append((beacon, ticks_ms()))

    def broadcast(self):
        payload = advertisement(
            self.identity, self.sequence, self.phase,
            self.color_clock_ms, self.color_reference, self.color_hops,
        )
        link.radio.advertise(
            owner=link.APP, interval_us=100000, adv_data=payload, connectable=False,
        )
        self.sequence = (self.sequence + 1) & 0xffff
        self.beacon_elapsed = 0

    def update(self):
        now = ticks_ms()
        elapsed = max(0, ticks_diff(now, self.last_tick))
        self.last_tick = now
        phase = self.phase + elapsed
        fired = phase >= PERIOD_MS
        self.phase = phase % PERIOD_MS
        self.color_phase_ms = (self.color_phase_ms + elapsed) % CYCLE_MS
        self.color_clock_ms = (self.color_clock_ms + elapsed) % CYCLE_MS
        self.since_flash = self.phase if fired else min(PERIOD_MS, self.since_flash + elapsed)
        for identity in tuple(self.peers):
            if ticks_diff(now, self.peers[identity][1]) >= PEER_TIMEOUT_MS:
                del self.peers[identity]

        pending = self.pending
        self.pending = []
        errors = {}
        color_candidate = None
        for beacon, received_at in pending:
            identity, sequence, phase, color_phase, color_reference, color_hops = beacon
            if identity == self.identity:
                continue
            previous = self.peers.get(identity)
            if previous is not None:
                distance = (sequence - previous[0]) & 0xffff
                if not 0 < distance < 0x8000:
                    continue
            elif len(self.peers) >= MAX_PEERS:
                continue
            self.peers[identity] = (sequence, now)
            age = max(0, ticks_diff(now, received_at))
            if age < BEACON_MS * 2:
                target = (phase + age) % PERIOD_MS
                error = (target - self.phase + PERIOD_MS // 2) % PERIOD_MS - PERIOD_MS // 2
                if error == -PERIOD_MS // 2 and identity > self.identity:
                    error = PERIOD_MS // 2
                errors[identity] = error
                # Follow a shared reference through strictly closer relays,
                # rather than feeding delayed color clocks back around a loop.
                if (color_hops < 255 and color_reference != self.identity
                        and (color_reference < self.color_reference
                             or (color_reference == self.color_reference
                                 and color_hops < self.color_hops))):
                    candidate = (color_reference, color_hops, identity, (color_phase + age) % CYCLE_MS)
                    if color_candidate is None or candidate[:3] < color_candidate[:3]:
                        color_candidate = candidate

        if color_candidate is not None:
            self.color_reference = color_candidate[0]
            self.color_hops = color_candidate[1] + 1
            self.color_clock_ms = color_candidate[3]
        if self.color_reference != self.identity:
            error = (self.color_clock_ms - self.color_phase_ms + CYCLE_MS // 2) % CYCLE_MS - CYCLE_MS // 2
            correction = max(-CORRECTION_MS, min(CORRECTION_MS, error))
            self.color_phase_ms = (self.color_phase_ms + correction) % CYCLE_MS

        if errors and self.since_flash >= REFRACTORY_MS:
            correction = sum(errors.values()) // (4 * len(errors))
            correction = max(-CORRECTION_MS, min(CORRECTION_MS, correction))
            self.phase = max(0, self.phase + correction)
            if self.phase >= PERIOD_MS:
                self.phase -= PERIOD_MS
                self.since_flash = self.phase
                fired = True

        self.beacon_elapsed += elapsed
        if self.running and (fired or self.beacon_elapsed >= BEACON_MS):
            self.broadcast()
        return fired
