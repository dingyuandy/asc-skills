# dav-3510 architecture and operator workflow

## Contents

- Architecture model
- Memory and pipeline map
- Code-side rules
- Synchronization workflow
- Simulator workflow
- Source map

## Architecture model

An Ascend 950 AI Core contains one Cube execution side (AIC) and two Vector execution sides (AIV/SUB BLOCK 0 and 1). L1 is shared by the AIC and its two AIVs; each AIV has its own UB. This is why L0C→UB can select one UB or split to both UBs, and why cross-side L1↔UB operations require attention to sub-block selection.

Relevant storage:

| Storage | Typical role | Owner/access |
|---|---|---|
| GM | Inputs, outputs, large tensors | External memory |
| L1 / `__cbuf__` | Cube tiles, cross-side staging | Shared within AI Core |
| L0A / `__ca__` | Left matrix operand | Cube side |
| L0B / `__cb__` | Right matrix operand | Cube side |
| L0C / `__cc__` | Matrix accumulator | Cube/FIXPIPE source |
| UB / `__ubuf__` | Vector working set | Per Vector side |
| SSBuffer | 3510 scalar/special staging | Architecture-specific |
| Fixpipe Buffer (`__fbuf__`) | Vector quant/ReLU parameters | FIXPIPE parameter staging |
| BT/FP | Bias/table or special matrix operands | Cube-related |

## Memory and pipeline map

| Path | Pipe | Main public API |
|---|---|---|
| GM→L1 | MTE2 | `asc_copy_gm2l1`, `_nd2nz`, `_dn2nz`, `_align` |
| GM→UB | MTE2 | `asc_copy_gm2ub`, `_align` |
| L1→L0A/L0B | MTE1 | `asc_copy_l12l0a`, `asc_copy_l12l0b`, transpose variants |
| L1→UB | MTE1 | `asc_copy_l12ub` |
| UB→L1 | MTE3 | `asc_copy_ub2l1` |
| UB→GM | MTE3 | `asc_copy_ub2gm`, `_align` |
| L0C→GM | FIXPIPE | `asc_copy_l0c2gm` |
| L0C→UB | FIXPIPE | `asc_copy_l0c2ub` |
| L0C→L1 | FIXPIPE | `asc_copy_l0c2l1` |
| Register SIMD | V | `asc_*` register APIs |

3510 adds direct L0C→UB, L1→UB, UB→L1, and SSBuffer-related capabilities. Do not assume an older architecture supports them or uses the same units.

## Code-side rules

- Use `__aicore__` for functions that may dispatch by side. Internal implementations commonly guard Cube work with `ASC_IS_AIC` and Vector work with `ASC_IS_AIV`.
- Register vector functions use `__simd_vf__`/`__simd_callee__` as shown by the public API and examples.
- L1↔UB crosses AIC/AIV resources. On 3510, `asc_copy_l12ub` is a PIPE_MTE1 instruction whose implementation is guarded by `ASC_IS_AIC`: issue it in the AIC branch even though its destination is an AIV UB. Calling it inside the AIV branch can silently do nothing. Use a mixed kernel such as `__mix__(1,1)` and select the destination AIV with `sub_blockid`.
- In a `__mix__(1,2)` kernel, an AIC-side `asc_sync_block_wait` for an AIV→AIC dependency covers both AIV sub-blocks. Both AIVs must execute `asc_sync_block_arrive`, including an idle AIV that does not move data. Guard the DMA by `asc_get_sub_block_id()`, not the handshake.
- Use public address-space types. A pointer cast does not make the underlying allocation belong to another buffer.

## Synchronization workflow

1. Identify producer and consumer pipes for every buffer lifetime.
2. Place an event after the producer command and wait before the consumer reads or overwrites that region.
3. Reuse an event ID only after its preceding wait completes.
4. Use `_sync` variants only when the implicit architecture post-process is acceptable. Explicit events allow overlap and are preferred in pipelined kernels.
5. For shared L1 and dual AIVs, reason about both data readiness and which sub-block owns the UB address.

Typical Cube flow:

```text
GM --MTE2--> L1 --MTE1--> L0A/L0B --M--> L0C --FIXPIPE--> GM or UB
```

Typical Vector flow:

```text
GM --MTE2--> UB --V(register load/compute/store)--> UB --MTE3--> GM
```

For simulator-only direct L1 readback, the verified cross-side sequence is:

```text
AIC: GM --MTE2--> L1
     event MTE2→MTE1
AIC: L1 --MTE1/asc_copy_l12ub(sub_blockid)--> selected AIV UB
     cross-core block-arrive on PIPE_MTE1
AIV: wait on PIPE_MTE3
     UB --MTE3/asc_copy_ub2gm--> GM
```

Do not move `asc_copy_l12ub` into `if ASC_IS_AIV` merely because the pointer type is `__ubuf__`. Also keep the event pipe pair consistent; a mismatched pipe can surface as a simulator synchronization error rather than a compile error.

For a bidirectional AIV→AIC→AIV observation path in `__mix__(1,2)`, use this low-freedom pattern:

```text
AIV0: perform UB→L1 on PIPE_MTE3
AIV0+AIV1: block-arrive(AIV_TO_AIC_FLAG, PIPE_MTE3)
AIC: block-wait(AIV_TO_AIC_FLAG, PIPE_MTE1)
AIC: perform L1→selected AIV UB on PIPE_MTE1
AIC: block-arrive(AIC_TO_AIV_FLAG, PIPE_MTE1)
AIV0+AIV1: block-wait(AIC_TO_AIV_FLAG, PIPE_MTE3)
selected AIV: consume/read back its UB
```

Use distinct flag IDs for the two directions unless the complete first arrive/wait lifetime is known to have ended before reuse. An idle AIV participates only in the handshake; it must not duplicate the DMA or write the same GM result.

### Timing two AIVs as one operation

For dual-AIV throughput, report aggregate bytes and time the common makespan. A robust UB→L1
protocol is: both AIVs arrive ready; AIC fences and reads the start counter; AIC releases both;
each AIV issues MTE3 into a disjoint L1 range and arrives done; AIC waits for both, fences, and
reads the end counter. On the current compiler, putting two bare `asc_get_system_cycle()` calls
around cross-core block waits produced an impossible 1-cycle result because PIPE_S reads were not
ordered with PIPE_MTE1 waits. Use explicit MTE1→S and S→MTE1 events around the counter reads.

This protocol measured 256 B/system-cycle aggregate for dual UB→L1 versus 128 for one AIV. The
opposite L1→dual-UB direction remained 128 B/system-cycle aggregate because both commands share
the AIC MTE1 engine. These are Ascend950PR_9589 dav-3510 simulator results; rerun on a physical
device or a changed simulator before treating them as cross-version silicon guarantees.

## Simulator workflow

Use the closest example under `examples/02_simd_c_api/03_c_api`. Inspect its `CMakeLists.txt`/README for the local build entry, select simulator mode when no NPU exists, and preserve generated input/golden comparison scripts.

Local environment pitfalls observed with the current checkout:

- `set_env.sh` can rewrite `PATH`; resolve `cmake` before sourcing it or accept an explicit `CMAKE_BIN` supplied by the caller instead of assuming a machine-specific installation path.
- A runner using `set -u` must temporarily use `set +u` while sourcing `set_env.sh`, because the script reads optional unset variables; restore `set -u` afterward.
- On the current glibc/toolchain combination, pass `CMAKE_ASC_FLAGS="-D__INTEL_LLVM_COMPILER=1 -DSYCL_LANGUAGE_VERSION=1"` if Bisheng reports `_Float128` or `__TC__` parse errors.
- Initialize the simulator once and execute a parameter matrix in one process when possible; per-case simulator startup dominates runtime.
- Compare only instruction-defined bytes. Do not require stride-created holes, untouched L1, or bytes outside a written SmallC0 group to contain zero.
- Fence `asc_get_system_cycle()` through the pipe that owns a cross-core block wait; bare PIPE_S
  counter reads do not by themselves define a makespan boundary.

Validation should include:

- compilation for the intended `dav-3510` target;
- simulator exit status and exception logs;
- exact output comparison;
- tail values and required zero padding;
- at least one non-contiguous stride case when adding/changing a strided copy;
- both SUB BLOCK targets when using L0C→UB dual mode.
- both `sub_blockid=false` and `sub_blockid=true` when using L1→UB.
- asymmetric `N` and `D` when distinguishing ND2NZ from DN2NZ.

## Source map

Public aggregators:

- `include/c_api/asc_simd.h`
- `include/c_api/cube_datamove/cube_datamove.h`
- `include/c_api/vector_datamove/vector_datamove.h`
- `include/c_api/vector_compute/vector_compute.h`
- `include/c_api/reg_compute/reg_vector.h`
- `include/c_api/reg_compute/reg_load.h`
- `include/c_api/reg_compute/reg_store.h`
- `include/c_api/reg_compute/reg_convert.h`

3510 implementations:

- `impl/c_api/instr_impl/npu_arch_3510/cube_datamove_impl/`
- `impl/c_api/instr_impl/npu_arch_3510/vector_datamove_impl/`
- `impl/c_api/instr_impl/npu_arch_3510/vector_compute_impl/`

Register APIs are also implemented through the 3510 `vector_compute_impl` tree; there is no separate `reg_compute_impl` directory in this checkout.

Documentation and tests:

- `docs/zh/api/SIMD-API/c_api/`
- `tests/api/c_api/npu_arch_3510/`
- `tests/api/tensor_api/npu_arch_3510/`
- `examples/02_simd_c_api/03_c_api/`

When explaining the “lowest-level API,” list the public `asc_*` prototype first, then its `_impl` function, then the emitted compiler builtin. The builtin is evidence, not the recommended callable interface.
