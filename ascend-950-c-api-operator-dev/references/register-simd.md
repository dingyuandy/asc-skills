# dav-3510 register-based SIMD

## Contents

- Register widths and masks
- Verified mask encodings and FP32 lane order
- Canonical tail loop
- Binary, unary, and exp-difference compute
- Conversion
- Interleave and deinterleave
- Aligned and unaligned load/store
- Pack/unpack and structured memory modes
- Address registers
- Gather and scatter
- Histograms
- Verified simulator harness
- ISA latency, throughput, and dual-issue measurement
- Review checklist

## Register widths and masks

dav-3510 register SIMD has a 256-byte Vector Length (VL).

| Element width | Elements per vector register | Mask constructor/update |
|---|---:|---|
| 8 bit | 256 | `asc_create_mask_b8`, `asc_update_mask_b8` |
| 16 bit | 128 | `asc_create_mask_b16`, `asc_update_mask_b16` |
| 32 bit | 64 | `asc_create_mask_b32`, `asc_update_mask_b32` |
| 64 bit | 32 | Check the exact API: many operations use a grouped b32 mask contract. |

There are 32 architectural vector registers available to ordinary code; excessive live values spill to reserved UB space. Keep lifetimes short and load close to use.

`vector_bool` is 256 bits. For b8 each element uses one bit. For b16/b32 each logical lane occupies a 2-bit/4-bit group and only the low bit of the group is meaningful. Always create/update a width-matched mask instead of manually reusing a b8 bit pattern.

Common patterns accepted by `asc_create_mask_b8/b16/b32` include `PAT_ALL`, `PAT_VL1`, `PAT_VL2`, `PAT_VL3`, `PAT_VL4`, `PAT_VL8`, `PAT_VL16`, `PAT_VL32`, `PAT_VL64`, `PAT_VL128`, and grouped patterns such as `PAT_H`, `PAT_Q`, `PAT_M3`, `PAT_M4`, `PAT_ALLF`; availability depends on element width and overload.

Masked arithmetic sets inactive destination lanes to zero unless the individual API documents a different merge behavior. Masked stores do not write inactive elements.

## Verified mask encodings and FP32 lane order

The Ascend950PR_9589 simulator stores a `vector_bool` as these eight low-to-high `uint32_t` words:

```text
INT8_ALL       ffffffff ffffffff ffffffff ffffffff ffffffff ffffffff ffffffff ffffffff
INT8_HALF      ffffffff ffffffff ffffffff ffffffff 00000000 00000000 00000000 00000000
INT8_QUARTER   ffffffff ffffffff 00000000 00000000 00000000 00000000 00000000 00000000
INT8_TAIL13    00001fff 00000000 00000000 00000000 00000000 00000000 00000000 00000000

BF16_ALL       55555555 55555555 55555555 55555555 55555555 55555555 55555555 55555555
BF16_HALF      55555555 55555555 55555555 55555555 00000000 00000000 00000000 00000000
BF16_QUARTER   55555555 55555555 00000000 00000000 00000000 00000000 00000000 00000000
BF16_TAIL13    01555555 00000000 00000000 00000000 00000000 00000000 00000000 00000000

FP32_ALL       11111111 11111111 11111111 11111111 11111111 11111111 11111111 11111111
FP32_HALF      11111111 11111111 11111111 11111111 00000000 00000000 00000000 00000000
FP32_QUARTER   11111111 11111111 00000000 00000000 00000000 00000000 00000000 00000000
FP32_TAIL13    11111111 00011111 00000000 00000000 00000000 00000000 00000000 00000000
```

This confirms that `PAT_H` and `PAT_Q` operate on logical lanes. Never reuse an all-ones b8 predicate for b16 or b32 data.

A 256-byte VREG holds 64 FP32 values, not four. It is eight 32-byte DataBlocks, each containing eight FP32 values. An aligned load/store round trip preserves low-address-to-low-lane order. The observed first lanes are:

```text
1.0  -> 00 00 80 3f
2.0  -> 00 00 00 40
-0.0 -> 00 00 00 80
-2.5 -> 00 00 20 c0
```

Describe this as little-endian when observed through UB/GM load/store. Do not claim that VREG bytes are independently addressable.

## Canonical tail loop

The update-mask APIs both create a low-lane tail mask and decrement `remaining` by one VL, saturating at zero:

```cpp
__simd_vf__ inline void add_half(
    __ubuf__ half* out, __ubuf__ half* x, __ubuf__ half* y,
    uint32_t count)
{
    uint32_t remaining = count;
    uint32_t offset = 0;
    while (remaining != 0) {
        vector_bool mask = asc_update_mask_b16(remaining); // up to 128 half
        vector_half vx, vy, vz;
        asc_loadalign(vx, x, offset);
        asc_loadalign(vy, y, offset);
        asc_add(vz, vx, vy, mask);
        asc_storealign(out + offset, vz, mask);
        offset += 128;
    }
}
```

For aligned access, ensure every iteration's effective address is 32-byte aligned. If the final base is unaligned, use the unaligned load/store protocol; a mask does not relax address alignment.

The `remaining` reference is modified in place. Preserve a separate logical count if later code needs the original value. A 70-element FP32 loop produces one 64-lane all mask followed by a six-lane tail mask.

## Binary, unary, and exp-difference compute

Typical binary form:

```cpp
asc_add(dst, src0, src1, mask);
```

The type-specific overloads cover supported b8/b16/b32 integer and floating combinations. Similar families exist for subtract, multiply, min/max, compare/select, logical, shift, and fused operations. Confirm the exact overload in `include/c_api/vector_compute` or `reg_compute/reg_vector.h`; do not assume mixed types are implicitly converted.

Typical unary exponential:

```cpp
asc_exp(dst, src, mask); // half or float overloads on 3510
```

`asc_exp_sub` computes an exponential of a lane-selected difference and has two source-lane variants (regular/even and v2/odd behavior in the implementation). It is deprecated in current 3510 documentation. For new code, compose the supported subtract and exponential APIs unless the legacy even/odd lane contract is specifically required and tested.

For all arithmetic:

- match the mask to the destination lane width;
- initialize/read every active source lane;
- account for inactive result lanes becoming zero;
- inspect saturation/rounding on integer and narrow operations;
- avoid relying on a deprecated overload when a current primitive composition exists.

## Conversion

Conversion APIs are named by source and destination, for example:

```cpp
asc_float2half(dst_half, src_float, round_mode, mask);
asc_half2float(dst_float, src_half, mask);
asc_float2int32(dst_i32, src_float, round_mode, mask);
asc_int322float(dst_float, src_i32, mask);
```

Exact signatures vary: some conversions require rounding/saturation controls and some widening/narrowing forms use multiple registers or half-lane placement. Read the individual page under `reg/data_type_convert` and the public prototype before coding.

Rounding families include round-to-nearest variants, floor, ceil, truncation, and rint as exposed by the selected conversion. Saturating integer conversion clamps; non-saturating behavior may wrap or be undefined for out-of-range values according to the overload. Never substitute a C++ cast as a model of hardware conversion.

Width changes alter useful lane count:

- b16→b32: one b16 register has twice as many source lanes as one b32 result; process/route halves as required by the exact overload.
- b32→b16: packing or lane selection determines where narrowed values land.
- int4/fp4 logical elements are packed; use the documented `vector_*x2_t` types and pack/unpack APIs.

## Interleave and deinterleave

Register operations:

```cpp
asc_intlv(dst0, dst1, src0, src1);        // or mask overload
asc_deintlv(dst0, dst1, src0, src1);      // or mask overload
```

They support same-width/same-type pairs across the documented integer and floating vector types. `dst0` and `dst1` must not be the same register. Source registers may alias one another, and documented in-place source/destination forms are allowed. With a mask, use b8/b16/b32 grouping matching the data width.

Memory-fused forms avoid a separate shuffle when the UB layout is interleaved:

```cpp
asc_loadalign_deintlv(dst0, dst1, src);
asc_storealign_intlv(dst, src0, src1);
```

They access `2*VL` and require a 32-byte aligned effective UB address. Scalar offsets are in elements; `addr_reg` overloads use the address-register value.

## Aligned and unaligned load/store

### Aligned

```cpp
asc_loadalign(reg, ub_ptr);                    // reads one VL
asc_loadalign(reg, ub_ptr, element_offset);
asc_storealign(ub_ptr, reg, mask);             // writes active lanes
```

Use a 32-byte aligned effective address. Post-update variants update the pointer/offset by their explicit step and are useful in tight loops; check whether that step is elements for the selected typed overload.

### Unaligned load

```cpp
vector_load_unalign ureg;
asc_loadunalign_pre(ureg, src_unaligned);
asc_loadunalign(dst, ureg, src_unaligned);
```

`vector_load_unalign` holds 32 bytes of alignment state. The load address need not be 32-byte aligned, but the pre-step is mandatory. Up to four unaligned state registers are available, so do not create unbounded live instances.

### Unaligned store

```cpp
vector_store_unalign ureg;
asc_storeunalign(dst_unaligned, ureg, src, count);
asc_storeunalign_post(dst_unaligned, ureg, tail_offset);
```

The main operation buffers boundary data and the post operation commits the final partial block. `count` and the post argument follow the typed API's element-count/offset contract. Up to four unaligned store state registers are available. Skipping `_post` loses or corrupts the tail.

## Pack/unpack and structured memory modes

Use these when memory layout, not arithmetic, is the transformation:

| Operation | Purpose |
|---|---|
| `asc_loadalign_unpack`, `_unpack4` | Expand packed/narrow memory lanes into register arrangement. |
| `asc_storealign_pack`, `_pack_quarter` | Compress selected/active register lanes into contiguous UB output. |
| `asc_loadalign_deintlv` | Load two VLs and split alternating elements. |
| `asc_storealign_intlv` | Interleave two registers into two VLs of UB. |
| `asc_loadalign_upsample` / `_downsample` | Structured lane replication or selection while loading. |
| broadcast load variants | Broadcast an element or data block across register lanes. |

Pack stores compact lanes according to the documented mask rather than leaving holes. Plain `asc_storealign` preserves lane positions and skips inactive writes. Choose deliberately.

The current checkout names the low-8-bit b32 store `asc_storealign_pack_quarter`. The installed CANN 9.1 simulator headers still expose the same operation as deprecated `asc_storealign_pack_v2`. Prefer `asc_storealign_pack_quarter` for current source; use the legacy name only when compiling against that older installed header and record the toolchain discrepancy.

## Address registers

`addr_reg` supports indexed UB access and post-update loops. Hardware provides up to eight address registers. A generated offset follows a sum of indices times strides, with up to four hardware loop dimensions:

```text
offset = index0*stride0 + index1*stride1 + ...
```

Initialize/update with the width-specific `asc_update_addr_reg_*` API before use. Confirm whether the resulting offset is interpreted in elements or bytes by the consumer overload; aligned load/store scalar offsets are commonly typed element offsets.

## Gather and scatter

### Gather from UB

```cpp
asc_gather(dst_reg, src_ub, index_reg, mask);
```

Each active lane reads:

```text
address = src_ub + index[lane] * sizeof(source_element)
```

The UB base is 32-byte aligned. Masked-off lanes do not access memory and become zero. All active indices must address valid UB. Some b8→b16 overloads zero-extend the 8-bit bit pattern, including signed b8 input; do not assume sign extension. When data/index lane widths differ, documented overloads may place results only in even lanes.

### Gather within a register

```cpp
asc_gather(dst_reg, src_reg, index_reg);
```

Out-of-range indices wrap modulo the number of elements in one VL.

The CANN 9.1 installed register-to-register gather overloads do not include `vector_float`; use a documented integer/half/BF16 overload. If the intent is lane permutation of FP32 bit patterns, an explicitly documented `vector_uint32_t` reinterpretation preserves the 32-bit payload, but it is not floating-point arithmetic.

### Scatter to UB

```cpp
asc_scatter(dst_ub, src_reg, index_reg, mask);
```

The UB base must be 32-byte aligned. Indices are element indices. Active index values must be unique; duplicate indices have nondeterministic winner selection. For b8 destinations only even source-register positions are effective on the documented 3510 overload.

## Histograms

Input is `vector_uint8_t`; accumulator is `vector_uint16_t` with 128 bins per VL:

```cpp
asc_frequency_histogram_bin0(dst_lo, src, mask);  // values 0..127
asc_frequency_histogram_bin1(dst_hi, src, mask);  // values 128..255

asc_cumulative_histogram_bin0(dst_lo, src, mask);
asc_cumulative_histogram_bin1(dst_hi, src, mask);
```

Frequency operations add counts to the existing destination. Cumulative operations add prefix-frequency results. Initialize accumulators when starting a new histogram. `uint16_t` wraps/overflows after 65535, so chunk or widen/merge for large populations. The b8 mask selects source samples; it does not mask individual destination bins.

The simulator verifies cumulative BIN1 continues the global prefix rather than restarting at value 128:

```text
BIN0[i] = count(value <= i)       for i=0..127
BIN1[i] = count(value <= 128+i)   for i=0..127
```

Thus `BIN1[0]` includes every sample with value 0 through 128. A high-half-only `cumsum(counts[128:])` is an incorrect golden.

## Verified simulator harness

Use this no-NPU test suite:

```text
examples/02_simd_c_api/03_c_api/02_reg_vector_compute/
  reg_simd_parameter_cases
```

It passed 32/32 checks on the dav-3510 Ascend950PR_9589 simulator: 12 raw mask encodings plus FP32 lane bytes, a 70-element ADD tail, masked EXP, EXP-SUB compatibility, BF16/FP32 conversions, register and fused-memory interleave/deinterleave, aligned offsets, unaligned pre/post, low16/low8 pack, UB gather/scatter, register gather modulo wrap, and frequency/cumulative histograms. Each output section is seeded with `0xA5`, so verification detects writes outside active lanes and packed output ranges.

## ISA latency, throughput, and dual-issue measurement

Use the generated measurement suite when performance depends on the emitted register-SIMD instruction rather than only API correctness:

```text
examples/02_simd_c_api/03_c_api/02_reg_vector_compute/
  simd_isa_microbenchmark
```

It covers the CANN 9.1 installed dav-3510 compute API/type matrix, including distinct source-to-destination conversion pairs. The completed run has 507/507 passing cases, 509 emitted-opcode rows, and no missing cases. Build and raw simulator logs live under the work directory configured by the harness; generated cases, analyzer, `RESULTS.csv`, `RESULTS.md`, and the family-level `ANALYSIS.md` live with the example.

Measure from simulator instruction logs, not host elapsed time:

- pair `core0.veccore0.instr_popped_log.dump` issue entries with `core0.veccore0.instr_log.dump` completions by dynamic instruction ID and opcode;
- latency is completion raw cycle minus issue raw cycle, reported as median/min/max;
- for one target opcode, let `n(c)` be its count at raw issue cycle `c`, let ordered occupied cycles be `u[0] ... u[k-1]`, and let `N = sum(n(c))`;
- `issue interval = mode(u[i] - u[i-1])` is the modal gap between nonempty issue groups; same-cycle instructions form one group, so this is not a per-instruction spacing;
- `max/cycle = max(n(c))` is the largest observed burst width. A value of two demonstrates that the opcode used both issue ports at least once, but does not imply sustained 2 IPC; never infer dual issue only from the API family;
- `peak IPC = max/cycle / issue interval` is a nominal estimate for regular steady schedules. For irregular or multimodal schedules the maximum burst and modal gap need not describe one stable pattern, so this value is not a strict throughput bound;
- `scheduled IPC = N / (u[k-1] - u[0] + 1)` is the inclusive first-to-last finite-window average. It can include dependency, finite-register, PREG recurrence, register-pressure, and compiler-scheduling gaps, while excluding prologue and epilogue outside the target opcode's issue window;
- finite-window boundary effects can make scheduled IPC differ slightly from the steady-state limit. For dependency-heavy irregular cases, inspect the gap distribution and scheduled IPC rather than treating peak IPC as authoritative.

Calibration observations on CANN 9.1 Ascend950PR_9589 are:

| API/type | ISA | latency | raw issue interval | max/cycle | peak IPC | dual |
|---|---|---:|---:|---:|---:|:---:|
| `asc_add` F32 | `RV_VADD` | 7 | 1 | 2 | 2.0 | yes |
| `asc_exp` F32 | `RV_VEXP` | 16 | 4 | 2 | 0.5 | yes |
| `asc_eq` F32 | `RV_VCMP_EQ` | 6 | 1 | 2 | 2.0 | yes |
| `asc_reduce_sum` F32 | `RV_VCADD` | 22 | 1 | 1 | 1.0 | no |
| `asc_intlv` U16 | `RV_VINTLV` | 11 | 2 | 1 | 0.5 | no |
| `asc_half2float` F16→F32 | `RV_VCVT_F2F` | 7 | 1 | 2 | 2.0 | yes |

These are simulator/compiler observations, not a cross-version silicon guarantee. Quote raw-cycle units explicitly. In particular, do not rewrite the measured U16 `VINTLV` interval of two raw cycles as “throughput one” without defining a different clock normalization.

Measured family patterns worth using during design review:

- FP16/BF16/F32 ADD/SUB have latency 7 and sustain two instructions per raw cycle; integer ADD/SUB have the same latency and dual-issue width but issue pairs every two raw cycles, averaging one instruction per raw cycle.
- MUL follows the same type split with latency 8. MIN/MAX, compare, shift, and ordinary vector logic are generally latency 6 and sustain two per raw cycle.
- F32 EXP/DIV/SQRT/LN have latency 16/17/17/18 and issue a pair every four raw cycles. Their F16 forms are slower at 21/22/22/23 and issue a pair every eight raw cycles.
- Reduce instructions are single-lane. Full-vector sum latency is 16 for integer, 22 for F32, and 24 for F16; DataBlock sum is 13/16/18 respectively. Pair-reduce sum is latency 12 and single issue.
- VREG INTLV/DEINTLV are latency 11, single issue, with a two-raw-cycle interval. PACK/UNPACK are latency 11, single issue, interval one; SQUEEZE/UNSQUEEZE are latency 18/15 with interval 5/4.
- All 68 measured conversion cases, covering 56 source-to-destination base-type pairs, show dual-issue evidence. F16↔F32 and F32↔BF16 use latency 7, interval one, and sustain two per raw cycle. Other F2F/F2I/I2F conversions are latency 8 with interval two; I2I is latency 7 with interval two.
- Do not treat a two-port burst as continuous throughput. Carry-in/out ADDC/SUBC expand to arithmetic plus predicate work and are limited by PREG recurrence in the measured dependency construction; consult the scheduled IPC row.

Microbenchmark construction pitfalls:

- Load independent chains from distinct UB addresses. Loading every chain from one address lets Bisheng prove them equal, coalesce VREGs, and reduce hundreds of source calls to one chain.
- Store every final chain result. A source-level call count is not evidence that target instructions survived DCE; confirm the dynamic target-opcode count in the popped log.
- A compare writes a predicate. Using that result as the next compare mask creates a predicate recurrence and measures dependency stalls. Use distinct VREG inputs, all-active masks, and store each predicate result before reusing the PREG.
- Predicate overloads and numeric overloads can share one public API name. Give their generated cases distinct identities so manifest deduplication cannot silently discard one.
- `vector_int4x2_t` is a wrapper, not a freely reinterpret-castable native VREG. Keep real typed source/destination registers and cast to the supported storage vector only at the final store.
- Vector shift counts use a signed vector of the same lane width (`vector_int8_t`, `vector_int16_t`, or `vector_int32_t`), including when the shifted data is unsigned.
- Report an API that expands to multiple opcodes as multiple ISA rows. Do not assign the aggregate expansion one invented latency.
- Check the installed headers used by the compiler, not only repository HEAD. In this measured CANN 9.1 environment the newer checkout declarations for integer `asc_div`, `asc_pack_to_low/high`, `asc_squeeze_with_status`, `asc_copy(s64)`, `asc_min_scalar(bf16)`, and vector `asc_ne(bf16)` are unavailable.
- Distinct-constant `asc_duplicate_scalar` FP8 microbenchmarks crash the Bisheng 9.1 frontend with stack-smashing/segfault. The vector-source FP8 overloads compile and measure normally; treat scalar FP8 throughput as unavailable for this toolchain rather than copying the vector result.

## Review checklist

- VL element count matches the data type.
- Mask constructor/update matches b8/b16/b32 grouping.
- Tail mask is generated from remaining logical elements, not bytes.
- Effective UB address satisfies the load/store alignment contract.
- Unaligned load has `_pre`; unaligned store has `_post`.
- Pack versus sparse masked store behavior is intentional.
- Conversion rounding and saturation are explicit.
- Interleave destinations do not alias each other.
- Gather indices are valid; scatter indices are unique.
- Histogram accumulators are initialized and cannot overflow for the tile size.
- Cumulative BIN1 golden continues the global 0..255 prefix.
- Installed-header API names are checked before using `pack_quarter`; CANN 9.1 may require legacy `pack_v2`.
- Deprecated operations are isolated and justified by a test.
- Performance claims identify API, types, emitted opcode/encoding, simulator version, and raw cycle unit.
- Dual-issue claims come from same-cycle dynamic log entries; throughput claims distinguish peak IPC from scheduled IPC.
