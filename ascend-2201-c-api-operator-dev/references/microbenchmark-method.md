# dav-2201 microbenchmark method

## Reproducer and artifact policy

Use this repository-relative example:

```text
examples/02_simd_c_api/03_c_api/01_ub_vector_compute/
  simd_isa_microbenchmark/
```

Keep generated `.asc` sources, `cases.json`, analyzer code, `RESULTS.csv`, `RESULTS.md`, and explanatory Markdown with the example. Put intermediate artifacts under:

```text
/mnt/e/asc_2201_ub_simd_microbenchmark/
  build/          CMake output and case executables
  runs/<case>/    stdout, stderr, and simulator dumps
```

Use another `/mnt/e` child only when isolation from an existing run is needed. Do not put large simulator dumps into the source tree or skill folder.

Run through the skill wrapper from the skill directory:

```bash
scripts/run_microbenchmark.sh --repo <asc-devkit-root> --profile smoke
scripts/run_microbenchmark.sh --repo <asc-devkit-root> --profile full \
  --build-jobs 12 --run-jobs 4
# Incremental rerun only: the work directory must already contain the other full-profile logs.
scripts/run_microbenchmark.sh --repo <asc-devkit-root> --profile full \
  --case-regex '^(compute_add|bank_add)'
```

Pass `--cmake <path>` when `cmake` is not already on `PATH`. The repository runner initializes CANN and must explicitly configure `CMAKE_ASC_ARCHITECTURES=dav-2201`. On a clean work directory, run the complete selected profile without `--case-regex`; the analyzer requires logs for every case in the generated manifest. Use filtering only to refresh cases in an existing complete run set.

## Case construction

One case should isolate one public API overload and one type/layout combination. Record at least:

```text
name, api, form, category
dst/src types
repeat and elements_per_repeat
src0/src1/dst absolute UB offsets
expected target opcode or opcode family
```

Use a large byte-addressed UB arena and explicit offsets so placement is auditable. The existing ordinary baseline is:

```text
arena = 96 KiB
src0/src1/dst = 0x00000/0x04100/0x10000
block strides = 1/1/1 DataBlocks
repeat strides = 8/8/8 DataBlocks
repeat = 64
```

This provides 64 repeats x 256 bytes = 16 KiB per ordinary operand while avoiding dual-source RR and source/destination RW conflicts. Fixed-shape operations such as transpose and special VA-list operations require their documented geometry rather than this generic call shape.

Issue at least 16 identical long-repeat calls so execution backpressure becomes visible. For a fixed `repeat=1` operation, issue enough calls to expose a stable interval. Keep setup, copies, and synchronization outside the target call sequence.

Generated cases must remain observable. Copy a defined result out or otherwise establish a side effect, and inspect emitted logs to ensure the compiler retained the intended opcode. A successful process with no target opcode is a failed measurement.

## Coverage strategy

Build the matrix from public 2201 declarations and explicit overloads. Cover arithmetic, scalar, compare/select, conversion and rounding modes, reductions, rearrangement, quantization, gather, and sort operations when supported. Treat these separately:

- `_sync` wrappers add synchronization and should not define raw compute-opcode throughput.
- State setters/getters are setup operations, not independent data-throughput cases.
- Count convenience overloads can mix mask setup with compute; use explicit repeat/stride overloads for ISA throughput.
- One API can emit multiple opcodes. Preserve one result row per target opcode and define API throughput by its limiting component.

When changing bank-sensitive cases, include explicit controls that retain RR, RW, partial group overlap, and self-conflicting stride patterns. Do not infer a compute-unit limit from a conflicted baseline.

## Log analysis

Use Vector Core issue and completion logs. Pair events by dynamic instruction ID and opcode:

```text
core0.veccore0.instr_popped_log.dump   issue
core0.veccore0.instr_log.dump          completion
```

For target issue cycles `c[i]` and per-cycle count `n(c)`:

- `latency`: first target completion cycle minus its issue cycle. The first call approximates an unqueued latency.
- `queued latency median`: median completion-minus-issue for later queued calls; it is diagnostic, not standalone hardware latency.
- `issue interval`: mode of gaps between ordered occupied issue cycles. It represents the steady command interval after queue backpressure.
- `max/cycle`: maximum `n(c)`, showing whether same-cycle multiple issue was observed.
- `peak IPC = max/cycle / issue interval`: nominal steady opcode issue estimate.
- `scheduled IPC = N / (last_issue - first_issue + 1)`: finite-window average, including startup bursts and scheduling gaps.
- `cycles/repeat = issue_interval / repeat`.
- `elements/cycle = elements_per_repeat * repeat / issue_interval`.

One `repeat=64` VADD dynamic opcode contains 64 internal repeats. An issue interval of 64 cycles is `1 cycle/repeat`, not 64 cycles per repeat. Early commands can enter a queue in a burst, so scheduled IPC can exceed the sustainable execution rate; use issue interval and its repeat-normalized derivatives as the primary throughput result.

## Acceptance checks

Require all of the following before publishing results:

1. The generated manifest count matches generated source count and selected run count.
2. Every selected case builds for dav-2201 and exits successfully.
3. `stderr.log` and simulator exception/error logs are empty.
4. Every case yields its intended target opcode; multi-opcode expansions are documented.
5. Output or checksum validation proves the intended work executed when semantic validation is part of the case.
6. CSV columns preserve raw latency, interval, scheduled metrics, repeat-normalized metrics, offsets, bank group, physical bank, and encoding.
7. Bank controls reproduce the expected relative behavior; conflict-free ADD/SUB/MAX/MIN should reach 1 cycle/repeat in the validated CANN 9.1.0 simulator setup.
8. Reports state CANN/simulator version, architecture, mask, stride, layout, type, and that simulator numbers are not a silicon guarantee.

The current full reproducer contains 551 **Vector UB-SIMD** API/type/layout/stride cases covering167 public async API names and produces554 opcode rows. It includes287 conflict-free main cases, 256 regular multi-source bank-layout cases across23 APIs, and8 ADD source/destination self-conflict cases. It does not cover Cube, Cube-side data movement, matrix layouts, or FIXPIPE. Treat these counts as a Vector regression snapshot, not a permanent API specification.

The bank sweep writes `BANK_SWEEP.csv` with each layout's slowdown against its conflict-free main case and `BANK_SWEEP.md` with per-opcode ranges. Preserve both when changing the generator or analyzer.

## Extending the method to Cube and movement

Build separate architecture-pinned suites rather than mixing incompatible metrics into the Vector CSV:

| Suite | Case axes | Primary normalization |
|---|---|---|
| data movement | path, type, aligned/unaligned, burst, stride, layout conversion, payload size | bytes/cycle from large-payload slope |
| L1/L0 layout | source physical layout, normal/transpose, type, start, repeat, stride/gap | fractals/cycle plus exact physical golden |
| MMAD | type, M/K/N, padded shape, init source, unit flag, sparse/dense | cycles and operations/cycle |
| FIXPIPE | path, source/destination type, quant, activation, layout, channel mode, unit flag | output bytes/cycle plus semantic golden |

Use `tests/api/c_api/npu_arch_2201` to discover supported overloads and emitted builtins, but do not count forwarding unit tests as simulator performance evidence. For every performance case, create a runnable dav-2201 kernel, inspect the emitted target instruction, and validate numerical/layout output.

Store their intermediate trees under distinct `/mnt/e` children, for example:

```text
/mnt/e/asc_2201_data_movement_microbenchmark/
/mnt/e/asc_2201_cube_microbenchmark/
/mnt/e/asc_2201_fixpipe_microbenchmark/
```

Keep generators, manifests, CSV/Markdown reports, and small golden scripts with the repository examples. Never place simulator dumps in the skill directory.
