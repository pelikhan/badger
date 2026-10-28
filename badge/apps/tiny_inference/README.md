# Tiny Inference

Tiny Inference checks and benchmarks bounded integer inference kernels on the Badger.

## Controls and usage

Open **Tiny Inference**, then press SELECT to run correctness checks, native dot
and matrix benchmarks, and a three-step synthetic decoder. BACK cancels the
current run; SELECT starts a fresh run. HOME returns to the launcher. Results
stay in RAM until the next run or exit; nothing is saved or downloaded.

The dashboard shows progress, free heap, and a **prospective** model memory
plan. The `Dot256` result is the median time for a batch of 16 dot products,
not the time for one dot product. Detailed JSON-prefixed results and failures
are printed to the USB console; the dashboard directs you there on failure.
An unsupported target shows `RP2350 ARM required` instead of running a fallback.

## Requirements and limits

This app targets the Universe 2026 Badger's RP2350B **Cortex-M33**, with its
scalar DSP instructions, 520 KiB SRAM, 8 MiB PSRAM and 16 MiB flash shared with
firmware. It requires MicroPython with `micropython.asm_thumb`, raw `data`
directives, `uctypes.addressof`, and an ARM native architecture reported by
`sys.implementation._mpy`. RP2040, RP2350 RISC-V firmware, desktop Python and
the WebAssembly badge simulator cannot run these kernels. There is no NEON,
Helium, external accelerator, native-module installation or firmware patch.

No network, secrets, extra hardware, trained weights or tokenizer are required.
This is a runtime-and-benchmark foundation, **not text generation or a
pretrained-model-compatible language model**. It has no model loader, embedding
executor, sampling loop, RoPE, gated FFN or multi-layer model executor. All
benchmark weights are deterministic synthetic data; checksums are not model
quality scores.

## Native arithmetic contract

`native.py` is the checked boundary; do not call `thumb.py` directly. It refuses
unsupported targets before importing assembly and never substitutes Python
math. The Python dot reference in `benchmark.py` is only a correctness oracle,
outside timed native regions.

| Primitive | Contract |
| --- | --- |
| `native.dot(left, right, count, left_offset=0, right_offset=0)` | Signed two's-complement int8 values in bytearrays; word-aligned starting addresses; count and nonnegative offsets multiples of four; count 0..1024; both buffers bounds-checked. |
| Accumulation | Exact signed int32 sum. `SXTB16` selects even/odd byte pairs, including rotated variants; two `SMLAD` instructions accumulate four products per loop. The largest legal dot magnitude is at most 16,777,216, so no overflow occurs. |
| `native.requantize(value, shift)` | Integer accumulator strictly between -2^30 and 2^30; shift 0..30. Divide by 2^shift, round halfway **away from zero**, then saturate to [-128, 127] using `SSAT`. The accumulator limit leaves room for the rounding increment without signed overflow. |

Buffers are deliberately bytearrays, not views, to make alias and alignment
checks unambiguous. There are no tails, arbitrary strides, zero-points, floating
point scales or biases. Callers must encode -128..-1 as bytes 128..255.

## Integer runtime

`runtime.py` provides independent, app-local primitives:

- `Linear(weights, columns, rows, shifts, kernel)`: row-major W8A8 projection.
  Columns are a multiple of four, 4..1024; rows are 1..1024. Per-row shifts are
  unsigned bytes in 0..30. `run(inputs, output)` yields after every output row;
  `row(...)` executes one row. Output cannot alias input, weights or shifts.
- `rms_norm(inputs, gains, output, kernel)`: input/output widths are multiples
  of four, 4..512. Gains are signed int16 Q7; output is Q6. It computes
  `d = floor_sqrt(floor(sum(x*x) * 65536 / width) + 65536)` and
  `y = sat_i8(round_away(x * gain * 128 / d))`. The additive term corresponds
  to epsilon 1 in input integer-square units. All intermediate arithmetic is
  integer; division and square root use Python/MicroPython integers, not DSP.
  In-place normalization is supported.
- `softmax_q15(scores, count, probabilities)`: int32 Q8 natural-log scores,
  uint16 Q15 outputs, count 1..128. Subtract the maximum, interpolate an
  exp(-x) table at 1/16 intervals and truncate differences >=8 to zero.
  Floor-normalize and add the remainder to the **last** maximum, giving an
  exact sum of 32768. This is an approximation, not floating-point softmax.
- `KVCache(width, context)`: fixed int8 K/V storage, width 4..512 in multiples
  of four, context 1..128. `attend(query, output, heads, score_shift, kernel)`
  yields between scores and output channels. Each of 1..16 heads must have a
  width divisible by four. Scores are `round_away(dot(Q,K) / 2^score_shift)`,
  interpreted as Q8. Output is the Q15-weighted V average at the V scale.
  Attention output and append inputs cannot alias cache storage. A full
  context is an explicit error, never a silent truncation or eviction.
- `DecoderBlock((q,k,v,o,up,down), (gain1,gain2), context, heads, score_shift,
  kernel)`: one pre-RMS attention block with saturated residual adds and a
  ReLU FFN. `step(input_bytes)` is a generator; fully exhaust it to commit
  `block.output`. It snapshots inputs at its first advance, rejects reentrant
  steps, and permits feedback from the previous output. Close the generator
  on cancellation: partial output is private and the logical cache length
  rolls back. Unused cache bytes are not erased. `clear()` rejects an active
  step and resets the logical context between sequences.

A future quantizer must supply consistent units: for a linear row the ratio
`input_scale * weight_scale / output_scale` must equal `2^-row_shift`.
RMSNorm emits Q6; attention scores must be scaled to Q8 including the chosen
head normalization factor; residual branches must have identical scales.
Q/K/V and the two residual branches are not automatically calibrated. The
synthetic demo uses row shift 5, score shift 5 and unity Q7 gains (128) solely
for reproducible checks. Do not equate its numbers with trained-model accuracy.

Do not resize or mutate parameters, cache storage or scratch buffers while
operations are in progress. Do not interleave direct cache operations with a
decoder step. Kernels are synchronous and not interrupt-safe.

## Allocation and cooperative scheduling

`memory_plan()` budgets a prospective width-256, hidden-512, four-layer,
four-head, context-128, vocabulary-512 layout. It reserves tied embedding/output
weights, position bytes, final normalization gains and output shifts even though
those model-level operations are not implemented. Its total is **2,616,320
bytes**, below the **4 MiB** planned app allocation cap:

| Reservation | Bytes |
| --- | ---: |
| Weights (including tied embedding/output table) | 2,228,224 |
| Position bytes | 32,768 |
| Int16 gains | 4,608 |
| Per-row shift bytes | 7,680 |
| Int8 K/V caches | 262,144 |
| Scratch vectors and attention scores/probabilities | 15,360 |
| Object/alignment/import allowance | 65,536 |

The formulas are `L*(4*D*D + 2*D*H) + V*D` weight bytes,
`2*L*C*D` cache bytes, and `L*(10*D + H + 6*C)` workspace bytes. A block
actually owns nine width-byte scratch/output vectors, one hidden-byte vector,
and six bytes per context position for scores/probabilities. The planner
reserves an extra width vector per block. Dimensions and planned totals are
checked; configurations exceeding 4 MiB are rejected.

**This is allocation planning, not measured hardware capacity or a loader.**
It excludes interpreter/firmware/display heap use and cannot stop a caller
from retaining unrelated objects or allocating multiple independent models.
Call `require_heap(plan["total"], gc.mem_free())` before future whole-model
allocation. Cache/block constructors check their incremental allocation plus
overhead against actual free heap and preserve a **512 KiB reserve**. The
benchmark requires 128 KiB plus that reserve before allocating its small
working set and checks sampled free heap between stages. Object allowances are
conservative estimates, not an exact MicroPython allocator accounting.

One benchmark work slice runs per frame. Correctness setup and matrix
initialization yield incrementally; projections yield per row, attention per
score/channel. The dashboard times every generator advance with `ticks_us()`,
records the maximum work-slice time and **stops with an explicit error if a
slice exceeds 16,000 us**. This detects an overrun after it happens; it cannot
preempt assembly, GC, console writes, or synchronous integer RMSNorm/softmax,
and excludes rendering and firmware time. No frame-rate or tokens/second
claim is made without on-badge measurements.

## Benchmark results

Console lines start with `tiny_inference` followed by a stage and JSON or PASS:

- `plan`: the prospective layout above, not an allocated model.
- `kernels PASS`: signed extrema/mixed patterns through length 1024, offsets
  and all 31 requantization shifts match integer references.
- `dot`: five lengths (64, 128, 256, 512, 1024), nine batches of 16 calls each,
  median batch microseconds and MAC/s. Includes Python argument checks and
  native-call overhead; does not isolate peak instruction throughput.
- `matrix`: a 64x256 projection, checked row by row. Accumulated compute
  microseconds include the checked row call, excluding frame waits, reference
  computation and setup.
- `decoder`: width 16, hidden 32, two heads, context capacity 8, three tokens.
  Every output byte is compared to independent host-derived golden vectors.
  Compute time sums generator advances, excluding inter-frame waits and the
  outer golden-vector checks. Reports a checksum and minimum **sampled** free
  heap, not peak memory or full-model latency.
- `correctness PASS`: all stages including the decoder matched references.
- `scheduler`: maximum observed work-slice time, slice count and 16 ms budget.

Closing the benchmark also explicitly closes an active decoder generator.
Timer results use wrap-safe `ticks_diff`; results are not persisted.

## Host verification

From the repository root, using Python 3.10+:

```sh
python3 -m unittest discover -s tests -p test_tiny_inference.py -v
python3 "$HOME/.copilot/skills/hyb26-skill/scripts/validate_app.py" badge/apps/tiny_inference
python3 "$HOME/.copilot/skills/hyb26-skill/scripts/validate_app.py" badge/apps/tiny_inference --target emulator
```

The default test run uses independent integer/float references for math,
memory planning, aliases, full context, every decoder cancellation point,
rollback/retry, input snapshots, reentrancy, failures and scheduler overruns.
Native execution tests skip explicitly if their optional tools are absent.
To exercise native code without hardware, install `mpy-cross==1.29.0` and
`unicorn==2.1.4` into a host virtual environment and run:

```sh
MPY_CROSS=/path/to/venv/bin/mpy-cross /path/to/venv/bin/python -m unittest discover -s tests -p test_tiny_inference.py -v
```

The native tests compile the **actual** `thumb.py`, extract the two inline-ASM
functions from MPY v6.3, and execute those exact bytes on Unicorn's Cortex-M33.
They check signed extrema, random vectors, offsets, all rounding shifts/ties,
stack/callee-saved registers, and repeat the runtime tests with that backend.
They do not substitute a handwritten copy of the kernels. The deliberately
small parser rejects a changed MPY format. Generated MPY files are temporary,
not app assets; deploy source so the badge uses its own firmware compiler.

Current delivery evidence: static validator passes with the expected missing
icon warning, all app modules cross-compile, and focused host/reference plus
Cortex-M33 instruction-emulation tests pass. No Badge Simulator canvas is
exposed by this client; browser simulation was not run and cannot validate the
native kernels. No device connection, deployment or firmware flashing was
performed. **Physical verification and real throughput/memory are outstanding.**

## Physical verification checklist (not yet performed)

Use the skill's USB guide for any later, explicitly requested deployment.
Copy only this app; do not install an icon or modify firmware/system apps.

1. Confirm normal launcher entry on RP2350 ARM firmware, then SELECT and
   capture all PASS/JSON lines. Check golden decoder outputs, free-heap reserve,
   real maximum work slice and timing results; do not infer them from emulation.
2. Exercise BACK during correctness setup, matrix rows and decoder attention,
   then SELECT again. Check no partial run survives and the rerun matches.
3. Exercise HOME during each stage, reopen repeatedly, and check responsiveness
   and recovered free heap after collection. Rotate both ways; verify readable
   dashboard text and logical SELECT/BACK controls.
4. Check full-context rejection and clear/retry through a small REPL harness
   using `synthetic_block`; never overrun allocated K/V buffers. Exercise a
   low-free-heap rejection without deliberately exhausting the system.
5. Record firmware version, CPU frequency, free heap, dot/matrix/decoder times
   and maximum slice. Unsupported firmware must fail visibly, not execute a
   slow fallback. Do not connect a second serial owner or change firmware.
