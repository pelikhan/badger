"""Continuous offline poetry with native integer benchmarks behind MENU."""

import gc
import os
import random
import sys
import time

APP_DIR = "/system/apps/tiny_inference"
os.chdir(APP_DIR)
sys.path.insert(0, APP_DIR)

from benchmark import Benchmark, STEP_BUDGET_US
from poet import Poet, wrap_lines
from runtime import memory_plan, require_heap

HOLD_MS = 6000
FRAME_WORK_US = 4000
ROWS_PER_FRAME = 8
COLLECT_FRAMES = 8
plan_kib = memory_plan()["total"] // 1024
screen.font = font.nope
background = color.rgb(13, 17, 23)
foreground = color.rgb(230, 237, 243)
accent = color.rgb(163, 230, 53)
failure = color.rgb(248, 81, 73)
worker = None
benchmark = None
poet = None
mode = "poems"
paused = False
hold_ms = 0
layout_key = None
poem_rows = []
maximum_slice_us = 0
last_free_heap = None
collection_frames = 0
status = "Preparing word model"
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


def check_heap():
    global last_free_heap
    gc.collect()
    last_free_heap = gc.mem_free()
    require_heap(16 * 1024, last_free_heap)


def advance_poem():
    global poet, worker, hold_ms, maximum_slice_us
    if paused:
        return
    if poet is None:
        gc.collect()
        poet = Poet(native, random.getrandbits)
    if poet.complete:
        hold_ms = min(HOLD_MS, hold_ms + max(0, badge.ticks_delta))
        if hold_ms < HOLD_MS:
            return
        check_heap()
        poet.restart()
        hold_ms = 0
    if worker is None:
        worker = poet.step() if poet.ready else poet.prepare()
    frame_start = time.ticks_us()
    for _ in range(ROWS_PER_FRAME):
        started = time.ticks_us()
        finished = False
        try:
            next(worker)
        except StopIteration:
            finished = True
        elapsed = time.ticks_diff(time.ticks_us(), started)
        maximum_slice_us = max(maximum_slice_us, elapsed)
        if elapsed > STEP_BUDGET_US:
            raise RuntimeError("poetry work slice exceeded 16 ms: %d us" % elapsed)
        if finished:
            worker = None
            break
        if time.ticks_diff(time.ticks_us(), frame_start) >= FRAME_WORK_US:
            break


def draw_poem():
    global layout_key, poem_rows
    screen.pen = accent
    title = "POEM %d" % poet.number if poet is not None else "INFINITE POEMS"
    screen.text(title + (" / PAUSED" if paused else ""), 4, 4)
    screen.pen = foreground
    if poet is not None and poet.text:
        key = (poet.text, screen.width, screen.height)
        if key != layout_key:
            poem_rows = wrap_lines(poet.text, screen.width - 8, screen.measure_text)
            layout_key = key
        line_height = max(1, int(screen.measure_text("Ag")[1]) + 2)
        available = max(1, (screen.height - 30) // line_height)
        pages = max(1, (len(poem_rows) + available - 1) // available)
        page = min(pages - 1, hold_ms * pages // HOLD_MS) if poet.complete else pages - 1
        for index, line in enumerate(poem_rows[page * available:(page + 1) * available]):
            screen.text(line, 4, 17 + index * line_height)
    else:
        message = "Preparing word model" if poet is None or not poet.ready else "Writing first word"
        screen.text(message, 4, 24)
    screen.pen = accent
    help_text = "SEL pause  BACK new  MENU"
    if screen.measure_text(help_text)[0] > screen.width - 8:
        help_text = "SEL / BACK / MENU"
    screen.text(help_text, 4, screen.height - 11)


def draw_dashboard():
    screen.pen = failure if error else accent
    screen.text("TINY INFERENCE / W8A8", 4, 4)
    screen.pen = foreground
    lines = [
        status, "M33 DSP + int32 sums", "64-word neural poet",
        "Heap: %d KiB (last)" % (last_free_heap // 1024)
        if last_free_heap is not None else "Heap: checked on start",
        "Plan: %d KiB / 4 MiB" % plan_kib,
    ]
    if benchmark is not None and "dot_256" in benchmark.results:
        result = benchmark.results["dot_256"]
        lines.append("Dot256: %d us/batch" % result["median_batch_us"])
    lines.extend(("SELECT run / BACK stop", "MENU poems / HOME exit"))
    for index, line in enumerate(lines):
        if 19 + index * 11 < screen.height - 6:
            screen.text(line, 4, 19 + index * 11)


def update():
    global worker, benchmark, status, error, mode, paused, hold_ms, last_free_heap
    global collection_frames
    try:
        collection_frames = (collection_frames + 1) % COLLECT_FRAMES
        if collection_frames == 0:
            gc.collect()
        if native is not None:
            if badge.pressed(BUTTON_MENU):
                cancel()
                mode = "benchmarks" if mode == "poems" else "poems"
                benchmark = None
                check_heap()
                status = "SELECT to run"
                error = False
            elif badge.pressed(BUTTON_BACK):
                cancel()
                if mode == "poems":
                    if poet is not None:
                        check_heap()
                        poet.restart()
                    hold_ms = 0
                    paused = False
                else:
                    status = "Stopped; SELECT runs"
                error = False
            elif badge.pressed(BUTTON_SELECT):
                if mode == "poems":
                    paused = not paused
                elif worker is None:
                    benchmark = None
                    gc.collect()
                    benchmark = Benchmark(native, time, gc.mem_free)
                    worker = benchmark.run()
                    last_free_heap = benchmark.minimum_free
                    error = False
            if not error:
                if mode == "poems":
                    advance_poem()
                elif worker is not None:
                    try:
                        status = benchmark.advance(worker)
                    except StopIteration:
                        cancel()
                        last_free_heap = benchmark.minimum_free
                        status = "Complete; SELECT runs"
    except (AssertionError, MemoryError, OSError, RuntimeError, ValueError) as exception:
        cancel()
        status = "FAILED: see USB log"
        error = True
        print("tiny_inference " + mode + " FAILED")
        sys.print_exception(exception)

    screen.pen = background
    screen.rectangle(screen.clip)
    if mode == "poems" and not error:
        draw_poem()
    else:
        draw_dashboard()


run(update)
