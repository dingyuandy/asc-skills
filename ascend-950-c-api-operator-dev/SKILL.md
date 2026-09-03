---
name: ascend-950-c-api-operator-dev
description: Develop, review, debug, benchmark, or explain Ascend 950 (dav-3510) C API operators. Use for 3510 GM/L1/L0A/L0B/L0C/UB/SSBuffer data movement, L1 and UB bank mapping, register-SIMD load/compute/store, FIXPIPE, matrix operations, synchronization, double buffering, and simulator analysis. Do not use for dav-2201/A2/A3/910B/C UB-pointer SIMD; use the dedicated ascend-2201 skill instead.
---

# Ascend 950 C API Operator Development

## Scope

Target only Ascend 950 / `npu_arch_3510`. If the user requests `npu_arch_2201`, Atlas A2/A3, or 910B/C, use `ascend-2201-c-api-operator-dev` instead. Resolve the architecture before applying any bank formula: 3510 and 2201 have incompatible UB topology, port rules, address bits, and useful offset transformations. Prefer public C APIs from `include/c_api`; use `impl/c_api/instr_impl/npu_arch_3510` to establish emitted builtins and hardware semantics, and examples/tests to validate shapes and address formulas.

Treat repository paths in this skill and its references as relative to the `asc-devkit` repository root. Discover that root from the current workspace, for example with `git rev-parse --show-toplevel` when already inside the repository or by locating `include/c_api/asc_simd.h`; do not assume a username, home directory, mount point, or fixed checkout location.

## Workflow

1. Resolve the requested product and `npu_arch` before reading architecture references or applying address formulas. For 3510 operator or pipeline work, read [architecture-and-workflow.md](references/architecture-and-workflow.md); it is not a 2201 architecture description.
2. For `set_flag`/`wait_flag`, `set_intra_block`/`wait_intra_block`, `pipe_barrier`, double buffering, or RAW/WAR/WAW dependencies, read [synchronization.md](references/synchronization.md).
3. For DMA/layout work, read [data-movement.md](references/data-movement.md). It contains the API signatures, every parameter, stride conventions, ND2NZ/DN2NZ address formulas, and shape examples.
4. For L1→L0A/L0B loads, MMAD operand orientation, NZ/ZN layout, or normal-versus-transpose selection, also read [l1-l0-matrix-load.md](references/l1-l0-matrix-load.md).
5. For L1 bank layout, bank conflicts, upper/lower-half allocation, or concurrent UB2L1 and L12L0 bandwidth, read [l1-bank-and-parallel-bandwidth.md](references/l1-bank-and-parallel-bandwidth.md).
6. For L0C output, quantization, ReLU, NZ conversion, or dual UB destinations, read [fixpipe.md](references/fixpipe.md).
7. For 3510 register-vector arithmetic, conversion, masks, tails, interleave, loads/stores, gather/scatter, or histograms, read [register-simd.md](references/register-simd.md).
8. For 3510 UB placement, 256 KiB opt-in, register-SIMD load/store bandwidth, or concurrent FIXPIPE-write/SIMD-read/write work, read [ub-bank-and-bandwidth.md](references/ub-bank-and-bandwidth.md). Never extrapolate its topology to 2201.
9. Before editing code, locate the exact overload in `include/c_api`, then the corresponding implementation for the selected architecture. Do not call an internal builtin directly from production operator code.
10. Compute every transfer in bytes on paper first, then convert once to the API's declared unit. Write the unit beside non-obvious constants.
11. Compile and run the closest example in simulator mode. Compare the whole logical output, including padded/tail regions where defined.

## Source-of-truth order

When sources disagree, use this order and state the discrepancy:

1. Public signature in `include/c_api` for callable overloads and types.
2. `impl/c_api/instr_impl/npu_arch_3510` for the selected builtin, fixed arguments, and architecture behavior.
3. Documentation explicitly scoped to 3510 for supported modes, constraints, and parameter ranges.
4. Tests and simulator models built for 3510 for exact address recurrence and tail behavior.
5. Examples for realistic configurations, never as proof of the full contract.

Do not silently generalize 2201/220x semantics to 3510 or translate 3510 bank offsets to 2201. Treat deprecated APIs as compatibility-only and select a non-deprecated public API when available.

## Review checklist

- Confirm execution side: Cube/AIC, Vector/AIV, or cross-core path.
- Classify each dependency as same-pipe ordering, a same-core `set_flag` event, an AIC/AIV `set_intra_block` counter, or a true different-block dependency not covered by those APIs.
- Confirm pipe and add the required event/pipe synchronization before a consumer reuses the buffer.
- For common AIC pipelines, synchronize `MTE2⇄MTE1`, `MTE1⇄M`, and `M⇄FIX`; do not invent a `FIX⇄MTE2` dependency. For AIV, synchronize `MTE2⇄V` and `V⇄MTE3`, returning free ownership to the next writer.
- For common AIC/AIV paths, make AIV V wait for AIC FIX ready and return release to FIX; make AIC MTE1 wait for AIV MTE3 UB→L1 ready and return free to MTE3.
- Check address space qualifiers and base alignment.
- State whether each stride is start-to-start or previous-burst-end-to-next-start.
- State whether each length/stride is bytes, elements, 32-byte data blocks, `C0_Size`, or 512-byte fractals.
- For ordinary GM2L1 multi-burst, never default an active stride to zero: compact mode 0 uses both strides equal to `len_burst`, compact insertion uses `dst_stride=1`, and compact removal uses `src_stride=1`.
- For GM2UB high-dimensional copies, keep `len_burst` and both start-to-start strides in bytes; compact multi-burst uses both strides equal to `len_burst`.
- For UB2L1/L12UB, treat `len_burst` and both end gaps as 32-byte DataBlocks; derive the next start with `(len_burst + gap) * 32`.
- For every double-buffer bank, prove both the forward ready dependency and the reverse free dependency. Seed only free tokens, never ready tokens.
- In `__mix__(1,2)`, make both AIV sub-blocks participate in group handshakes even when only one AIV moves data. For pairwise intra synchronization, map AIV local ID `x` to AIC IDs `x` and `16+x`.
- Treat synchronization IDs as scarce live resources. Do not use the pipe argument or transfer direction as an assumed extra namespace; reuse an ID only after its preceding lifetime is fully consumed.
- For dual-AIV bandwidth, use disjoint L1/UB regions, count aggregate bytes, and measure one common
  ready-to-both-done makespan. Fence PIPE_S counter reads through the pipe carrying the block wait.
- Check count ranges and integer-width overflow before narrowing to `uint8_t`/`uint16_t`.
- For GM→L1 ND2NZ/DN2NZ, program `asc_set_gm2l1_nz_para` immediately before the matching copy.
- For GM→UB ND-DMA, keep all five sizes/strides in elements, pass stride setters as `(dst, src)`, zero-initialize higher pad counts, call `asc_ndim_copy_dci()`, and interpret C API `padding_mode=true` as constant padding.
- For L1→L0A/L0B, derive the actual logical matrix already present in L1 before choosing normal or transpose; conventional `AB` uses L0A normal and L0B NZ→ZN transpose after normalized L1 input.
- For MX L1→L0 scale loads, pass the L0 data address divided by 16 and count the payload as
  `x_step*y_step*32` bytes; do not mix scale bytes with packed MX matrix-data bytes.
- For concurrent L1 reads and writes, derive the bank from the absolute local L1 address. Pair
  corresponding streams by toggling bit 18 (`addr ^ 0x40000`); do not overlap a read and write in
  the same physical bank and assume the hardware will merely serialize them.
- For non-dense L0 loads, distinguish the L0A/NZ destination recurrence from the L0B transpose/ZN recurrence, and initialize any fractals that MMAD will read but the load does not write.
- For register SIMD, derive VL elements from element width and generate the tail mask from remaining elements.
- For 3510 UB concurrency, decode the absolute address as group=`addr[7:5]`, bank-in-group=`addr[8]`, depth=`addr[17:9]`; use `addr^0x100` to pair banks only when both streams advance at the same 256-byte phase rate.
- The default 3510 static UB budget is 248 KiB. Opt into the full 256 KiB only by compiling with both `--cce-disable-vf-stack-reserved-ubuf` and `--cce-disable-asc-reserved-ubuf`; then prevent VF spills and avoid APIs that require the released 2 KiB ASC-reserved area.
- Keep every timed SIMD load observable. Repeatedly overwriting one VREG lets the compiler delete all but the last load and produces a false read-bandwidth result.
- For FP32 FIXPIPE-write/SIMD-read concurrency, make FIXPIPE write only bank half 0 with NZ2ND `n_size=64` FP32 and `dst_stride=128` FP32; read bank half 1 at `row*0x200+0x100`, and use `+0x20000+row*0x200` as the same-bank/different-depth control. Use `asc_loadalign_postupdate(..., 512)` for the fixed-half read. The verified simulator gives 384 B/cycle separated versus 192 B/cycle same-bank.
- When the requirement says FIXPIPE writes FP32 UB, require `__cc__ float` source, `__ubuf__ float` destination, `QuantMode_t::NoQuant`, and no post conversion. An INT32 L0C has the same byte width and can hide a wrong test if only cycles are checked.
- For FIXPIPE, validate the complete source/destination type and side-function combination; do not configure a mode from its name alone.
- Treat dual-destination FIXPIPE plus every side function, including ReLU, as unsupported. A CANN 9.1 simulator may execute dual-N + NZ2ND + Normal ReLU without reporting an error even though the same binary is unsupported on device; implementation forwarding and simulator Golden checks do not override the device contract. Keep side-function controls neutral and perform ReLU on AIV after the FIXPIPE-ready event. See [fixpipe.md](references/fixpipe.md).
- Prefer `_sync` only when its full post-process synchronization is intended; otherwise use explicit events to overlap pipelines.

## Safe implementation guidance

Use `#include "c_api/asc_simd.h"`. Never include files under `impl/` directly. If a normal GM→L1 build fails because the compiler no longer accepts the legacy `copy_gm_to_cbuf_v2` lowering, keep operator code on a public API and inspect the installed 3510 implementation; use the architecture-aligned path backed by `copy_gm_to_cbuf_align_v2`, not another `copy_gm_to_cbuf_v2` call. Its parameters differ, so do not mechanically rename the builtin. Internal builtins are not a stable user interface.

For claims not covered by the references, inspect the local header/implementation instead of guessing. Quote the path and overload used in the final explanation or code review.
