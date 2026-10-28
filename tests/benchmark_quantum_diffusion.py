"""Run with mpremote run, or CPython for solver-only desktop measurements."""

import gc
import sys
import time


HARDWARE = sys.implementation.name == "micropython"
if HARDWARE:
    APP = globals().get("BENCH_APP", "/system/apps/quantum_diffusion")
else:
    from pathlib import Path
    APP = str(Path(__file__).resolve().parents[1] / "badge/apps/quantum_diffusion")
sys.path.insert(0, APP)
import field
from kernel import micropython
from palette import TRANSITION_MS

if not HARDWARE:
    ptr32 = memoryview


@micropython.viper
def seed_dense(current, previous, width: int, height: int, frame: int):
    dst = ptr32(current)
    old = ptr32(previous)
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            i = y * width + x
            value = ((i * 73 + frame * 19) % 16385) - 8192
            dst[i] = value
            old[i] = value


def clock():
    return time.ticks_us() if HARDWARE else time.perf_counter_ns() // 1000


def difference(end, start):
    return time.ticks_diff(end, start) if HARDWARE else end - start


waves = field.WaveField()
waves.impulse(waves.width // 2, waves.height // 2)
gc.collect()
start = clock()
for _ in range(100):
    waves.step()
step_us = difference(clock(), start) / 100
print("target:", "RP2350 badge" if HARDWARE else "desktop CPython")
print("grid:", waves.width, waves.height)
print("backend:", getattr(field, "BACKEND", "float bytecode"))
print("step_us:", step_us)

def frame_benchmark(width, height, workload, frames):
    # Keep real graphics, sensor polling and VSYNC, but bound the app loop.
    context = {"run": lambda callback: None}
    exec(open(APP + "/__init__.py").read(), context)
    if width is not None:
        grid = field.WaveField(width, height)
        context["field"] = grid
        context["CELL"] = min(screen.width // width, screen.height // height)
        context["LEFT"] = (screen.width - width * context["CELL"]) // 2
        context["TOP"] = (screen.height - height * context["CELL"]) // 2
        context["pulse"](width // 2, height // 2)
    sensors = context.get("motion")
    if hasattr(sensors, "start"):
        sensors.start()
    update = context["update"]
    badge.poll()
    costs = []
    full_costs = []
    start = clock()
    last_shake = start
    palette_segments = 0
    touch_actions = (
        BUTTON_UP, BUTTON_DOWN, BUTTON_LEFT, BUTTON_RIGHT,
        BUTTON_SELECT, BUTTON_MENU, BUTTON_BACK,
    ) if globals().get("BENCH_TOUCH", False) else ()
    try:
        for frame in range(frames):
            now = clock()
            if difference(now, last_shake) >= 300000:
                last_shake = now
                sensors.pending = (
                    sensors.pending[0] + 1, 8 * field.SCALE,
                    field.SCALE if frame % 2 else -field.SCALE,
                    field.SCALE if frame % 3 else -field.SCALE,
                    "SHAKE" if frame % 2 else "TWIST",
                )
            if workload == "dense":
                grid = context["field"]
                seed_dense(grid.current, grid.previous, grid.width, grid.height, frame)
                sensors.gain = 7
            frame_start = clock()
            held = badge.pad._held
            if touch_actions:
                badge.pad._held = touch_actions if frame % 8 == 0 else ()
            try:
                update()
            finally:
                badge.pad._held = held
            if "brand_palette" in context:
                palette_segments |= 1 << (context["brand_palette"].phase_ms // TRANSITION_MS)
            costs.append(difference(clock(), frame_start))
            badge.update()
            full_costs.append(difference(clock(), frame_start))
        wall_us = difference(clock(), start)
        costs.sort()
        full_sorted = sorted(full_costs)
        print("workload:", workload, "grid:", context["field"].width, context["field"].height)
        print("update_us_median:", costs[len(costs) // 2])
        print("update_us_p95:", costs[len(costs) * 95 // 100])
        print("full_frame_fps:", frames * 1000000 / wall_us)
        print("frame_p95_ms:", full_sorted[len(full_sorted) * 95 // 100] / 1000)
        print("frame_max_ms:", max(full_costs) / 1000)
        print("slowest_frame_fps:", 1000000 / max(full_costs))
        window = min(15, frames)
        print("slowest_15_frame_fps:", window * 1000000 / max(
            sum(full_costs[i:i + window]) for i in range(len(full_costs) - window + 1)
        ))
        print("motion_pulses:", context["pulses"])
        print("capacitive_stress_enabled:", bool(touch_actions))
        if "brand_palette" in context:
            print("brand_palette_phase_ms:", context["brand_palette"].phase_ms)
            print("brand_palette_rgb:", context["brand_palette"].rgb)
            print("brand_palette_segments_seen_mask:", palette_segments)
        if hasattr(sensors, "samples"):
            before = sensors.samples
            time.sleep_ms(250)
            print("samples_during_250ms_without_render:", sensors.samples - before)
            print("sensor_samples_total:", sensors.samples)
            print("ambient_raw_filtered:", sensors.light, "gain:", sensors.gain)
    finally:
        if hasattr(sensors, "stop"):
            sensors.stop()


if HARDWARE:
    candidates = globals().get("GRID_CANDIDATES")
    if candidates:
        for width, height in candidates:
            for workload in ("busy", "dense"):
                gc.collect()
                frame_benchmark(width, height, workload, 90)
    else:
        for workload in ("busy", "dense"):
            gc.collect()
            frame_benchmark(None, None, workload, globals().get("BENCH_FRAMES", 120))
    print("normal launcher resumes after reset")
