# Tiny Inference

Tiny Inference writes endless offline poems with a tiny trained integer model on the Badger.

## Controls and usage

Open **Tiny Inference** to start writing poems automatically. Words appear as
the native integer model generates them. After each two-line poem, the app
waits six seconds, resets its recurrent state, and writes another indefinitely.
It retains only the current poem, not an ever-growing history. Poems can repeat;
"endless" means an ongoing stream, not infinitely many unique poems.

SELECT pauses/resumes writing and the reading timer. BACK discards the current
poem and starts a fresh one. MENU switches between poems and the benchmark
dashboard; HOME returns to the launcher. If a smaller viewport needs multiple
pages, the reading interval cycles through them. Controls remain logical in
either badge orientation.

On the benchmark dashboard, SELECT runs the correctness/timing suite and BACK
cancels it. MENU returns to the poem without retaining a partially executed
word or benchmark. Results stay in RAM until the next benchmark run or exit.
The dashboard's `Plan` is the **prospective transformer layout** described below,
not the small poetry model's allocation. `Dot256` is the median time for a
batch of 16 dot products, not one dot. Detailed JSON-prefixed benchmark results
and failures are printed to the USB console.

An unsupported target shows `RP2350 ARM required` instead of running a fallback.
Missing, truncated, incompatible or checksum-invalid model assets fail visibly
with a USB traceback; copy the entire app directory, including `assets`.

## Requirements and limits

This app targets the Universe 2026 Badger's RP2350B **Cortex-M33**, with its
scalar DSP instructions, 520 KiB SRAM, 8 MiB PSRAM and 16 MiB flash shared with
firmware. It requires MicroPython with `micropython.asm_thumb`, raw `data`
directives, `uctypes.addressof`, and an ARM native architecture reported by
`sys.implementation._mpy`. RP2040, RP2350 RISC-V firmware, desktop Python and
the WebAssembly badge simulator cannot run these kernels. There is no NEON,
Helium, external accelerator, native-module installation or firmware patch.

No network, secrets or extra hardware are required. The trained poetry weights
and 68-token vocabulary are bundled with the app; nothing is downloaded at
runtime. This is a specialized toy poet, **not a general-purpose LLM or
SmolLM implementation**. It cannot follow prompts, answer questions, or produce
words outside its 64-word bank. Its language is deliberately narrow and may
be repetitive, surreal or awkward. Benchmark decoder weights remain synthetic.

## Trained poetry model and device fit

The bundled model is a genuine, quantization-aware-trained Elman word RNN with
64 hard-tanh hidden units and 68 tokens: 64 words plus line-end, poem-end,
start and padding markers. Neural weights learn the word order and affinities;
runtime generation does **not** select words from a per-position grammar table.
Only start/padding suppression, minimum/maximum line lengths, a small repetition
penalty, and two-line framing constrain sampling.

| Model payload | Size |
| --- | ---: |
| Learned parameters, including learned biases | 12,932 |
| Int8 weights, including alignment padding | 13,328 bytes |
| Complete `assets/poet.bin`, including its header | 13,336 bytes |
| Persistent numeric weights/shifts/state/logit/probability buffers | 14,268 bytes |
| Pre-allocation guard, including estimated objects and loading overhead | 64 KiB |

This is well below the 4 MiB app planning cap and the RP2350B's 8 MiB PSRAM,
but actual firmware heap availability is still checked: creation must leave
512 KiB free. Generation checks the reserve each active frame, collects
between poems or when less than 16 KiB of extra headroom remains, and fails
visibly if collection cannot recover enough free heap.
No floating-point training checkpoint, PyTorch, NumPy, tokenizer
library or firmware extension is installed on the badge. Only the two small native
projections, integer state updates and Q15 sampling run there.

The recurrent projection has 136 columns: a 68-token Q6 one-hot vector, 64
Q6 hidden values, a Q6 constant bias input (64), and three padding zeros.
Its weights are Q6 and its requantization shift is 6. Clamp the result to
[-64, 64] for hard-tanh. The readout has 68 columns: the 64 hidden values,
the constant bias and three zeros. Its weights are Q5 and its shift is 7,
producing Q4 logits. Both use `Linear` and the **same native ARM kernels**
as the benchmarks; there is no Python inference fallback.

Multiply logits by 24 for Q8 softmax scores at temperature 2/3, subtract 128
from scores for previously used words, and sample the exact-sum Q15
distribution with an unsigned 15-bit random draw. Lines contain 3..8 words.
If the model does not end a line, the eight-word safety limit forces a boundary.
After two lines, generation stops until the next poem reset. The hidden state
is fixed at 64 bytes; no growing KV cache is needed. The display adds sentence
capitalization and periods.

`assets/poet.json` records architecture, quantization, vocabulary/model SHA-256,
training settings and held-out metrics. The loader verifies its format and
dimensions, the binary header, exact length, vocabulary digest and weight
checksum. Loading yields after at most 512 bytes; both projections yield per
row. Closing a word operation leaves its published recurrent state and text
unchanged. Reopening or switching screens closes open model files/generators.

### Training and provenance

The model was trained locally on **102,400 procedurally generated original
couplets**, from the word banks in `poet.py`. No existing poems, pretrained
weights or external text dataset were downloaded or copied. Training examples
use adjective/noun/verb/preposition/adjective/noun lines with sky, woodland,
circuit and sea affinities. That grammar creates training examples; it is not
a runtime word-selection rule.

Training used 1,600 batches of 64, seed 20261028, CPU PyTorch 2.7.1 and NumPy
2.2.6. Fake-quantized weights, hidden state and logits match the on-device
round-away/clamp operations. On 512 separate synthetic held-out couplets,
cross-entropy was 1.803731 nats and argmax grammatical-category accuracy
99.9581%. These describe this constrained synthetic task, **not general
language ability or human-rated poetry quality**.

To reproduce training on a host with compatible Python 3.10+ and those two
packages installed (not on the badge):

```sh
python3 tools/train_tiny_poet.py
```

The tool checks held-out loss/category accuracy and exact integer-export
equivalence to its quantized training graph before replacing the bundled
binary/manifest. Its defaults match the shipped model; a second training run
reproduced the binary and manifest byte for byte. It uses no remote APIs
and writes no training checkpoints. Host checks also sample 200 complete
integer-generated poems, verify bounded state and varied output, and require
at least 90% of lines to follow the learned training grammar.

SmolLM itself cannot meet this hardware envelope: the smallest
[SmolLM2 model has 135 million parameters](https://huggingface.co/HuggingFaceTB/SmolLM2-135M).
Even ideal 4-bit weight packing alone is about 64 MiB, before scales,
activations and cache, exceeding both the badge's PSRAM and its shared 16 MiB
flash. This much smaller custom RNN is not a renamed/truncated SmolLM.

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
  ReLU FFN (separate from the trained recurrent poet). `step(input_bytes)` is a generator; fully exhaust it to commit
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

**This is allocation planning, not measured hardware capacity or a transformer loader.**
It excludes interpreter/firmware/display heap use and cannot stop a caller
from retaining unrelated objects or allocating multiple independent models.
Call `require_heap(plan["total"], gc.mem_free())` before future whole-model
allocation. Cache/block constructors check their incremental allocation plus
overhead against actual free heap and preserve a **512 KiB reserve**. The
benchmark requires 128 KiB plus that reserve before allocating its small
working set and checks sampled free heap between stages. Object allowances are
conservative estimates, not an exact MicroPython allocator accounting.

One benchmark work slice runs per frame. Poetry runs at most eight native rows
per frame, stopping early after 4,000 us of accumulated inference/loading work;
an individual slice over 16,000 us fails visibly. Initial allocations, drawing,
firmware time and garbage collection are not included in that soft work budget.
The reading timer uses bounded `badge.ticks_delta` accumulation and stops while
paused or on the benchmark screen.

Correctness setup and matrix
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
memory planning, aliases, full context, every decoder/poet cancellation point,
rollback/retry, input snapshots, reentrancy, model corruption, learned poem
quality, bounded long-run generation, timer/pause/skip/menu behavior,
word wrapping/paging and scheduler overruns.
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
This includes actual trained-poet inference and integer-state/logit equivalence,
not just synthetic weights.
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

1. Confirm normal launcher entry on RP2350 ARM firmware, then MENU, SELECT and
   capture all PASS/JSON lines. Check golden decoder outputs, free-heap reserve,
   real maximum work slice and timing results; do not infer them from emulation.
2. Confirm continuous neural poems, readable wrapping and the six-second
   reading interval. SELECT must pause both typing and the interval; BACK
   starts a new poem. Let many poems finish and verify free heap remains stable.
   MENU must enter/leave benchmarks without retaining a partial word.
3. Exercise BACK during benchmark correctness setup, matrix rows and decoder
   attention, then SELECT again. Check no partial run survives and the rerun matches.
4. Exercise HOME during loading, poems and benchmarks, reopen repeatedly, and check responsiveness
   and recovered free heap after collection. Rotate both ways; verify readable
   dashboard text and logical SELECT/BACK controls.
5. Check full-context rejection and clear/retry through a small REPL harness
   using `synthetic_block`; never overrun allocated K/V buffers. Exercise a
   low-free-heap rejection without deliberately exhausting the system.
6. Record firmware version, CPU frequency, free heap, word generation latency,
   dot/matrix/decoder times and maximum slice. Unsupported firmware or damaged
   model assets must fail visibly, not execute a
   slow fallback. Do not connect a second serial owner or change firmware.
