# dav-2201 architecture and API model

## Contents

- Architecture identity
- SIMD programming model
- Storage and execution map
- Boundary from dav-3510
- Source-of-truth order
- Repository source map
- Coding and validation rules

## Architecture identity

Use this skill only for `npu_arch_2201`, Atlas A2/A3, and 910B/C. A 2201 Vector kernel operates on data staged in Unified Buffer and normally calls public APIs through:

```cpp
#include "c_api/asc_simd.h"
```

The public SIMD overloads accept `__ubuf__` operands plus mask, DataBlock stride, repeat count, and repeat stride. They lower to VEC instructions that internally perform the requested repeats.

The AIC and AIV are separate cores with their own Scalar front ends; the product ratio is one AIC to two AIVs. AIC owns Cube-side L1/L0A/L0B/L0C/BT/FB work, while AIV owns UB-based Vector work. On 2201, AIC and AIV exchange data through GM rather than the 3510 direct L1/UB paths.

## SIMD programming model

A normal full-mask repeat covers eight 32-byte DataBlocks, or 256 bytes:

```text
16-bit type: 128 elements/repeat
32-bit type:  64 elements/repeat
```

For a normal operand, repeat `r` and DataBlock `b` have the recurrence:

```text
address(r,b) = base + 32 * (r * repeat_stride + b * block_stride)
b = 0..7
```

Block and repeat strides default to DataBlock units in the high-dimensional SIMD API family. Check the selected API documentation for exceptions. Use `asc_set_vector_mask` or the required state setter before issuing the operation. Async APIs execute on `PIPE_V`; preserve MTE2-to-V, V-to-MTE3, and buffer-reuse dependencies.

## Storage and execution map

| Storage | Capacity | Required base alignment | Primary role |
|---|---:|---:|---|
| UB | 192 KiB physical | 32 B | AIV Vector operands |
| L1 / `__cbuf__` | 512 KiB physical | 32 B | AIC matrix staging |
| L0A / `__ca__` | 64 KiB | 512 B | Cube left operand |
| L0B / `__cb__` | 64 KiB | 512 B | Cube right operand |
| L0C / `__cc__` | 128 KiB | 64 B | Cube accumulator/result |
| BiasTable | 1 KiB | 64 B | MMAD bias/initial C |
| Fixpipe Buffer / `__fbuf__` | 2 KiB | 64 B | Vector quant/ReLU parameters |

In fused Cube/Vector builds, framework reservations reduce usable UB and L1. The 2201 architecture guide gives a maximum UB user area of `192 KiB - 256 B` when the ASC-reserved-UB option releases the optional 8 KiB API reservation; without that option, subtract the additional 8 KiB. L1 similarly reserves 256 B. Check the actual compile mode rather than allocating the physical maximum blindly.

The normal pipelines are:

```text
AIV: GM --MTE2--> UB --V--> UB --MTE3--> GM
AIC: GM --MTE2--> L1 --MTE1--> L0A/L0B --M--> L0C --FIX--> GM or L1
```

## Boundary from dav-3510

Do not translate behavior by product generation:

| Property | dav-2201 / A2/A3 / 910B/C | dav-3510 / Ascend 950 |
|---|---|---|
| Compute operands | UB pointers | VREGs after explicit register loads |
| Compute structure | one UB API carries mask/strides/repeat | load VREG, compute, store VREG |
| Physical UB | 192 KiB | 256 KiB physical; commonly 248 KiB static budget |
| Bank topology | 16 groups x 3 banks | 8 groups x 2 banks |
| Group bits | `addr[8:5]` | `addr[7:5]` |
| Bank selector | `addr[17:16]` | `addr[8]` |
| `+0x100` effect | group phase +8 | same group, paired bank |
| Same-group read rule | multiple source reads conflict | two reads can use paired banks |
| GM to L0A/L0B direct | supported | removed |
| L1 to GM direct | supported | removed |
| UB to/from L1 | unsupported | supported |
| L0C to UB | unsupported | supported |
| L0A transpose load | supported | not supported by the corresponding 3510 path |
| Cube S4 and 4:2 sparse | supported | unsupported |
| MX load/MMAD, NZ2DN | unsupported | supported |
| AIC/AIV local exchange | through GM | direct L1/UB paths available |

Consequences:

- Do not add `vld/vst` around a 2201 UB-pointer API just because a 3510 implementation uses VREGs.
- Do not use 3510 dual-issue or load/store bandwidth as a 2201 compute result.
- Do not use the 3510 `addr ^ 0x100` bank-pair prescription on 2201. On 2201, `+0x100` changes the group phase and `+0x10000` changes the physical-bank layer.
- Do not call 3510-only `asc_set_l0c2gm_nz2nd` on 2201. Configure 2201 FIXPIPE NZ2ND with `asc_set_l0c_copy_params`.
- Do not infer a path from a generic declaration alone. Confirm that the overload is implemented under `npu_arch_2201` and documented for A2/A3.

## Source-of-truth order

When sources disagree, use:

1. The selected public declaration in `include/c_api` for callable signatures and types.
2. `impl/c_api/instr_impl/npu_arch_2201` for the emitted builtin and architecture-specific forwarding.
3. Documentation explicitly scoped to architecture 2201 for constraints and parameter units.
4. `tests` and dav-2201 simulator behavior for exact lowering and address recurrence.
5. Examples for representative configurations, not as proof of the complete contract.

Do not call internal compiler builtins directly from production operator code.

## Repository source map

All paths are relative to the `asc-devkit` repository root:

```text
include/c_api/asc_simd.h
include/c_api/vector_compute/vector_compute.h
include/c_api/vector_datamove/vector_datamove.h
include/c_api/cube_compute/cube_compute.h
include/c_api/cube_datamove/cube_datamove.h
include/c_api/sync/sync.h
impl/c_api/instr_impl/npu_arch_2201/
docs/zh/api/SIMD-API/c_api/
docs/zh/api/SIMD-API/c_api/general_description_and_constraints.md
docs/zh/guide/算子实践参考/SIMD算子性能优化/内存访问/避免UB的bank冲突/
tests/api/c_api/
tests/api/c_api/npu_arch_2201/cube_compute/
tests/api/c_api/npu_arch_2201/cube_datamove/
tests/api/c_api/npu_arch_2201/sync/
examples/02_simd_c_api/03_c_api/01_ub_vector_compute/
```

The primary throughput reproducer is:

```text
examples/02_simd_c_api/03_c_api/01_ub_vector_compute/
  simd_isa_microbenchmark/
```

## Coding and validation rules

- Use public `asc_*` APIs and the correct `__ubuf__`, `__gm__`, or other address-space qualifier.
- State every non-obvious length and stride unit beside its value.
- Check repeat limits, integer narrowing, alignment, overlap constraints, and mask tails.
- Initialize instruction state such as vector masks, compare masks, dequantization parameters, or VA registers immediately before the dependent operation when practical.
- Make timed work observable. Output validation must cover all instruction-defined bytes; do not require holes or untouched padding to be zero unless the API defines that result.
- Separate compute throughput from GM/UB copies, synchronization, scalar address generation, and launch time.
- Compile with `CMAKE_ASC_ARCHITECTURES=dav-2201`; reject a benchmark silently built for another architecture.
- Several neighboring examples in the checkout are explicitly dav-3510-only. Do not use their successful build or golden result as 2201 evidence; start from the 2201 implementation/tests and create an architecture-pinned simulator case.
