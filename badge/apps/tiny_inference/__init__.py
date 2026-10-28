"""Hardware-only quantized inference correctness and benchmark dashboard."""

import gc
import os
import sys
import time

APP_DIR = "/system/apps/tiny_inference"
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

from benchmark import Benchmark
from runtime import memory_plan

plan_kib = memory_plan()["total"] // 1024
screen.font = font.nope
background = color.rgb(13, 17, 23)
foreground = color.rgb(230, 237, 243)
accent = color.rgb(163, 230, 53)
failure = color.rgb(248, 81, 73)
worker = None
benchmark = None
status = "SELECT to run"
error = False
native = None

try:
    import native
except (ImportError, SyntaxError) as exception:
    status = "RP2350 ARM required"
    error = True
    print("tiny_inference native backend unavailable")
    sys.print_exception(exception)


def cancel():
    global worker
    if worker is not None:
        worker.close()
        worker = None
    gc.collect()


def on_exit():
    cancel()


def update():
    global worker, benchmark, status, error

    if badge.pressed(BUTTON_BACK):
        cancel()
        if native is not None:
            status = "Stopped; SELECT runs"
    elif native is not None and worker is None and badge.pressed(BUTTON_SELECT):
        benchmark = None
        gc.collect()
        benchmark = Benchmark(native, time, gc.mem_free)
        worker = benchmark.run()
        error = False
    if worker is not None:
        try:
            status = benchmark.advance(worker)
        except StopIteration:
            cancel()
            status = "Complete; SELECT runs"
        except (AssertionError, MemoryError, RuntimeError, ValueError) as exception:
            cancel()
            status = "FAILED: see USB log"
            error = True
            print("tiny_inference benchmark FAILED")
            sys.print_exception(exception)

    screen.pen = background
    screen.rectangle(screen.clip)
    screen.pen = failure if error else accent
    screen.text("TINY INFERENCE / W8A8", 4, 4)
    screen.pen = foreground
    lines = [
        status, "M33 DSP + int32 sums", "No trained model loaded",
        "Free: %d KiB" % (gc.mem_free() // 1024),
        "Plan: %d KiB / 4 MiB" % plan_kib,
    ]
    if benchmark is not None and "dot_256" in benchmark.results:
        result = benchmark.results["dot_256"]
        lines.append("Dot256: %d us/batch" % result["median_batch_us"])
    lines.extend(("SELECT run / BACK stop", "HOME exits; logs via USB"))
    for index, line in enumerate(lines):
        if 19 + index * 11 < screen.height - 6:
            screen.text(line, 4, 19 + index * 11)


run(update)
