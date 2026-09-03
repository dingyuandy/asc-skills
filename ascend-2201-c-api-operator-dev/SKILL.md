---
name: ascend-2201-c-api-operator-dev
description: Develop, review, debug, benchmark, or explain dav-2201 C API operators for Atlas A2/A3 and 910B/C. Use for UB-pointer SIMD, Cube MMAD, GM/L1/L0A/L0B/L0C/BT/FB data paths, ND/NZ/ZZ/ZN layouts, FIXPIPE output and quantization, synchronization, bank conflicts, generated case matrices, and simulator throughput analysis. Do not use its architecture rules for Ascend 950 / dav-3510.
---

# Ascend dav-2201 C API Operator Development

## Scope and architecture gate

Target `npu_arch_2201` only: Atlas A2/A3 and 910B/C. Confirm the requested architecture before inspecting APIs or proposing layouts. If the target is Ascend 950 / `dav-3510`, stop using this skill's UB formulas: 3510 uses VREG load/compute/store and a different 8-group x 2-bank UB organization.

Treat paths in this skill as relative to the `asc-devkit` repository root unless explicitly stated otherwise. Locate the root by finding `include/c_api/asc_simd.h`; do not assume a checkout path. Keep source, generated cases, manifests, and final reports with the example. Put CMake output, executables, run logs, and simulator dumps under `/mnt/e`; the standard work directory is `/mnt/e/asc_2201_ub_simd_microbenchmark`.

## Required routing

1. Read [references/architecture-and-api-model.md](references/architecture-and-api-model.md) before implementing, reviewing, or explaining a 2201 operator.
2. Read [references/data-paths-and-movement.md](references/data-paths-and-movement.md) for any GM/UB/L1/L0/BT/FB transfer, padding, stride, or path-availability question.
3. Read [references/cube-mmad-and-layouts.md](references/cube-mmad-and-layouts.md) for Cube computation, MMAD types and initialization, L1/L0A/L0B/L0C layout, transpose, direct GM-to-L0, or sparse 4:2 work.
4. Read [references/fixpipe.md](references/fixpipe.md) for L0C output, quantization/dequantization, ReLU/Leaky ReLU, channel split/merge, NZ2ND, unit flag, or parameter-buffer handling.
5. Read [references/synchronization-and-pipelines.md](references/synchronization-and-pipelines.md) for async pipeline dependencies, ping/pong reuse, or AIC/AIV coordination.
6. Read [references/ub-bank-conflicts.md](references/ub-bank-conflicts.md) for UB allocation, block/repeat stride, bank conflicts, gather/scatter addresses, or ping/pong placement. Use `scripts/audit_ub_banks.py` for deterministic address checks.
7. Read [references/microbenchmark-method.md](references/microbenchmark-method.md) when adding API/type/path cases, compiling a matrix, parsing simulator logs, or reporting latency/throughput. Use `scripts/run_microbenchmark.sh` for the existing Vector suite and keep all intermediate artifacts under `/mnt/e`.

## Implementation workflow

1. Locate the exact public overload under `include/c_api`; confirm types, address spaces, mask behavior, stride units, repeat range, overlap constraints, and architecture guards.
2. Inspect the matching `impl/c_api/instr_impl/npu_arch_2201` implementation to identify the emitted builtin and fixed arguments. Use it as evidence, not as the production interface.
3. Express every transfer in bytes once, then convert to the selected API's unit. Units vary among bytes, elements, 32-byte DataBlocks, 512-byte input fractals, 1024-byte L0C fractals, and end gaps; never transfer a 3510 parameter interpretation to 2201.
4. Derive the logical matrix and physical ND/NZ/ZZ/ZN layout already present in each storage before choosing normal or transpose loads. Prove capacity, alignment, padding, non-overlap, and every address recurrence.
5. Preserve pipeline correctness around `PIPE_MTE2`, `PIPE_MTE1`, `PIPE_M`, `PIPE_FIX`, `PIPE_V`, and `PIPE_MTE3`. Prove both forward ready and reverse free dependencies for buffer reuse. Keep synchronization outside timed instruction windows when measuring isolated throughput.
6. Compile explicitly for `dav-2201`, run in simulator mode, validate the complete defined output, and inspect empty stderr/exception logs. Treat simulator measurements as toolchain-version observations until confirmed on hardware.

## Microbenchmark acceptance

- Generate independent public-C-API cases with metadata for API, form, type, repeat, elements/repeat, and UB offsets.
- For Cube and transfers, additionally record path, pipeline, source/destination layouts, shapes, alignment, byte count, every stride unit, MMAD initialization mode, and FIXPIPE side functions.
- Keep the target opcode observable and issue enough identical calls to expose steady state.
- Include conflict-free baselines and intentional RR/RW/WW controls when bank behavior affects the result.
- Derive throughput from instruction issue/completion logs. Do not use host elapsed time and do not equate one dynamic opcode with one internal repeat.
- Report `issue interval`, `cycles/repeat`, `elements/cycle`, first unqueued latency, `scheduled IPC`, and `max/cycle`, with their definitions and limitations.
- Require every selected case to compile, run successfully, produce analyzable target opcodes, and pass output validation where the instruction semantics are under test.
- Treat the current 551-case snapshot as Vector-only coverage. Do not claim Cube, data-movement, layout, or FIXPIPE coverage until their separate matrices have been generated and run for dav-2201.
