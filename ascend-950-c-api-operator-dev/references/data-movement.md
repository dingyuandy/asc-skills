# dav-3510 data movement C APIs

## Contents

- Unit and stride rules
- GM to L1
- GM to L1 ND2NZ and DN2NZ
- Verified GM2L1 parameter harness
- GM to UB
- GM to UB ND-DMA
- Verified GM-to-UB ND-DMA parameter harness
- L1 to L0A and L0B
- Verified L1 to L0A/L0B parameter harness
- L1 to UB
- UB to L1
- Verified GM2UB/UB2L1/L12UB parameter harness
- Typical shape recipes
- Common failures

## Unit and stride rules

Never infer a stride convention from its type or name. On 3510 the important conventions are:

| API family | Length unit | Stride meaning | Stride unit |
|---|---|---|---|
| GM→L1 ordinary, no padding | 32-byte blocks | burst start→next burst start | 32-byte blocks |
| GM→L1 insertion/removal | mode-specific source/destination amount | relevant side is start→start | 32-byte blocks |
| GM→UB high-dimensional | bytes | burst start→next burst start | bytes |
| GM→UB aligned | bytes | burst start→next burst start | bytes |
| GM→UB ND-DMA | elements | coordinate start→adjacent coordinate start | elements of `T` |
| GM→L1 ND/DN→NZ source | N/D loop address step | start→start | bytes |
| ND/DN→NZ destination config | address step | start→start | 32-byte C0 blocks |
| L1→UB | 32-byte data blocks | previous burst end→next burst start (`gap`) | 32-byte blocks |
| UB→L1 | 32-byte data blocks | previous burst end→next burst start (`gap`) | 32-byte blocks |
| L1→L0A/B 2D | position/step varies | adjacent fractal start→start | 512 bytes |
| FIXPIPE source stride | adjacent NZ-Z start→start | start→start | `C0_Size = 16*sizeof(src)` |
| FIXPIPE destination stride | adjacent NZ-Z or ND row | start→start | elements |

For a gap-style L1↔UB copy:

```text
next_src = current_src + (len_burst + src_gap) * 32
next_dst = current_dst + (len_burst + dst_gap) * 32
```

For a start-to-start byte-stride external copy:

```text
next_src = base_src + burst * src_stride
next_dst = base_dst + burst * dst_stride
```

## GM to L1

### Public and bottom interfaces

```cpp
__aicore__ inline void asc_copy_gm2l1(
    __cbuf__ void* dst, __gm__ void* src,
    uint32_t n_burst, uint32_t len_burst, uint8_t pad_func_mode,
    uint64_t src_stride, uint32_t dst_stride);
```

The current 3510 implementation forwards to:

```cpp
copy_gm_to_cbuf_v2(dst, src, /*sid=*/0, n_burst, len_burst,
                   pad_func_mode, /*l2_cache_ctl=*/0,
                   src_stride, dst_stride);
```

Use the public API. `copy_gm_to_cbuf_v2` is an internal compiler builtin. If this legacy lowering fails to compile in the selected toolchain, keep the operator callsite on a public API and use the aligned 3510 path backed by `copy_gm_to_cbuf_align_v2`; do not replace it with another `copy_gm_to_cbuf_v2` call. `copy_gm_to_cbuf_align_v2` has a different contract, so a bare textual rename is invalid. The public aligned entry is `asc_copy_gm2l1_align`.

### Parameters

| Parameter | Meaning |
|---|---|
| `dst` | L1 base address. Check L1 allocation and architecture alignment. |
| `src` | GM base address. |
| `n_burst` | Number of continuous transfer bursts. |
| `len_burst` | Length of each burst in 32-byte blocks. |
| `pad_func_mode` | `0`: no padding; `1..5`: insert padding after 1/2/4/8/16 source bytes to form 32 bytes; `6..8`: from each 32-byte source block keep the low 4/8/16 bytes. |
| `src_stride` | Source burst-start→next-burst-start distance in 32-byte blocks for mode 0 and removal modes 6–8. Compact mode-0 transfer uses `src_stride = len_burst`; compact removal uses `src_stride = 1`. In insertion modes 1–5 the source is consumed continuously and this field is unused/zero. |
| `dst_stride` | Destination burst-start→next-burst-start distance in 32-byte blocks for mode 0 and insertion modes 1–5. Compact mode-0 transfer uses `dst_stride = len_burst`; compact insertion uses `dst_stride = 1`. In removal modes 6–8 the destination is written continuously and this field is unused/zero. |

Insertion requires `len_burst == 1`. PIPE_MTE2. The `_sync` overload performs the copy followed by architecture synchronization.

For `n_burst > 1`, zero is not the compact stride on a side whose stride is active. It repeats that side's start address and can overwrite the same L1 block or reread the same GM block. Zero is harmless for `n_burst == 1` because no next burst exists.

Use these mode-specific recurrences:

| `pad_func_mode` | Per-burst source | Per-burst destination | Compact active stride |
|---|---|---|---|
| `0` | `len_burst * 32` bytes | `len_burst * 32` bytes | `src_stride = dst_stride = len_burst` |
| `1..5` | 1/2/4/8/16 bytes, consumed continuously | one 32-byte block; remaining bytes are zero | `src_stride = 0`, `dst_stride = 1`, `len_burst = 1` |
| `6..8` | one 32-byte block | low 4/8/16 bytes, written continuously | `src_stride = 1`, `dst_stride = 0` |

Example: with mode 0, `n_burst=3`, and `len_burst=1`, compact starts are blocks 0, 1, 2, so both strides are 1. A stride of 2 uses starts 0, 2, 4 and leaves a one-block hole between bursts.

### 2D overload

```cpp
asc_copy_gm2l1(dst, src,
    uint32_t m_start_position, uint32_t k_start_position,
    uint16_t dst_stride, uint16_t m_step, uint16_t k_step,
    uint8_t decomp_mode, uint8_t l2_cache_ctl);
```

`dst_stride` is in 512-byte units. The current compatibility constraint requires `m_step == 1`. `l2_cache_ctl`: `0` normal, `4` disable; `1` LAST is unsupported and `2` persistent is not currently available. Use typed overloads for the intended data type.

## GM to L1 ND2NZ and DN2NZ

### Lowest-level interface chain

Public APIs:

```cpp
__aicore__ inline void asc_copy_gm2l1_nd2nz(
    __cbuf__ T* dst, __gm__ T* src,
    uint64_t loop1_src_stride, uint8_t l2_cache_ctl,
    uint16_t n_value, uint32_t d_value,
    uint64_t loop4_src_stride, bool smallc0_en);

__aicore__ inline void asc_copy_gm2l1_dn2nz(/* same parameters */);

__aicore__ inline void asc_set_gm2l1_nz_para(uint64_t config);
```

Implementation and builtins:

```cpp
asc_copy_gm2l1_nd2nz_impl(...) ->
  copy_gm_to_cbuf_multi_nd2nz(dst, src, /*sid=*/0, ...);

asc_copy_gm2l1_dn2nz_impl(...) ->
  copy_gm_to_cbuf_multi_dn2nz(dst, src, /*sid=*/0, ...);

asc_set_gm2l1_nz_para(config) -> set_mte2_nz_para(config);
```

These APIs use the ND-DMA path, not `copy_gm_to_cbuf_v2` or `copy_gm_to_cbuf_align_v2`. They are PIPE_MTE2.

### Required destination configuration

Program immediately before the matching ND2NZ/DN2NZ copy:

```cpp
uint64_t config =
    (uint64_t(nd_num)                  ) |
    (uint64_t(loop2_dst_stride) << 16 ) |
    (uint64_t(loop3_dst_stride) << 32 ) |
    (uint64_t(loop4_dst_stride) << 48 );
asc_set_gm2l1_nz_para(config);
```

| Bits | Field | Meaning and unit |
|---|---|---|
| `[15:0]` | `nd_num` | Number of independent matrices/outer iterations. |
| `[31:16]` | `loop2_dst_stride` | Destination step between adjacent N positions, in 32-byte C0 blocks. |
| `[47:32]` | `loop3_dst_stride` | Destination step between adjacent D/C0 blocks, in 32-byte C0 blocks. |
| `[63:48]` | `loop4_dst_stride` | Destination step between matrices, in 32-byte C0 blocks. |

Changing `loop4_src_stride` does not create extra matrices; `nd_num` controls the outer count.

### Common parameters

| Parameter | Meaning |
|---|---|
| `dst` | L1 output base. Normal output is NZ with a 32-byte innermost C0 block. D tails are zero-padded. |
| `src` | GM input base. ND2NZ reads logical `[N,D]`; DN2NZ reads a transposed/strided logical `[D,N]`. |
| `loop1_src_stride` | Source byte step for the innermost hardware loop. For ND2NZ it steps from one N row to the next. For DN2NZ it steps from one D element/row to the next while N elements are contiguous. |
| `l2_cache_ctl` | `0` normal or `4` disable. `1` LAST is unsupported; `2` persistent is not currently available. |
| `n_value` | Logical N element count. It is not bytes and not rounded to C0. |
| `d_value` | Logical D element count. Hardware rounds it up to C0 for the L1 result and writes zero to the tail. |
| `loop4_src_stride` | Source byte step between the `nd_num` outer matrices. May be zero when `nd_num == 1`. |
| `smallc0_en` | Enable the 4-channel SmallC0 path; valid only for `D <= 4`. Despite a reversed sentence in one generated doc table, the 3510 simulator and test model show `true` selects SmallC0/4 channels and `false` selects normal 32-byte C0. For half with `N=3,D=3,true`, each N writes `[d0,d1,d2,0]`, an 8-byte four-channel group; do not assume the rest of a 32-byte area was written. |

For normal C0, `C0_ELEMS = 32 / sizeof(T)`.

### ND2NZ exact address mapping

Let `h` select an outer matrix, `i` a D/C0 block, `j` N, and `k` an element inside C0:

```text
src_byte = h*loop4_src_stride
         + j*loop1_src_stride
         + i*32 + k*sizeof(T)

dst_byte = (h*loop4_dst_stride
          + i*loop3_dst_stride
          + j*loop2_dst_stride) * 32
         + k*sizeof(T)
```

Copy only when `i*C0_ELEMS+k < d_value`; otherwise write zero. For a row-major `[N,D]` input, `loop1_src_stride = D*sizeof(T)`.

### DN2NZ exact address mapping

DN2NZ reads D with `loop1_src_stride` and N contiguously:

```text
src_byte = h*loop4_src_stride
         + (i*C0_ELEMS+k)*loop1_src_stride
         + j*sizeof(T)

dst_byte = (h*loop4_dst_stride
          + i*loop3_dst_stride
          + j*loop2_dst_stride) * 32
         + k*sizeof(T)
```

The same D-tail zero fill applies. For a row-major physical `[D,N]` input, `loop1_src_stride = N*sizeof(T)`.

### Dense NZ recipe

For one dense matrix, set:

```text
nd_num = 1
loop2_dst_stride = 1
loop3_dst_stride = N
loop4_dst_stride = 0
```

This produces consecutive C0 blocks for each N position and consecutive D/C0 groups separated by `N*32` bytes.

### Verified GM2L1 parameter harness

The local simulator harness is:

```text
examples/02_simd_c_api/03_c_api/00_data_movement/
  data_copy_gm2l1/parameter_cases
```

It covers 34 cases: 14 ordinary GM2L1 cases, 10 ND2NZ cases, and 10 DN2NZ cases. The tested target is `dav-3510`; the recorded Ascend950PR_9589 simulator run passed 34/34. Use `README.md` for parameter matrices, `TEST_RESULTS.md` for the recorded result, and `run_all.sh` to rebuild and run. The golden model compares only bytes defined by the instruction: stride-created L1 holes and unwritten bytes after a SmallC0 group are not required to be zero.

## GM to UB

```cpp
asc_copy_gm2ub(__ubuf__ T* dst, __gm__ T* src, uint32_t size);

asc_copy_gm2ub(__ubuf__ T* dst, __gm__ T* src,
               uint16_t n_burst, uint16_t len_burst,
               uint16_t src_stride, uint16_t dst_stride);
```

On 3510 the simple `size` is bytes and must be 32-byte aligned. In the high-dimensional overload, `len_burst`, `src_stride`, and `dst_stride` are bytes; strides are burst-start-to-next-burst-start. Thus a compact copy uses both strides equal to `len_burst`, not zero.

For burst `b`:

```text
src_start(b) = src + b*src_stride
dst_start(b) = dst + b*dst_stride
copy len_burst bytes
```

With `n_burst=1`, both strides may be zero because no next burst exists. With `n_burst>1`, zero repeats the corresponding start address. For example, `n_burst=3,len_burst=32,src_stride=64,dst_stride=32` reads GM blocks 0, 2, 4 and writes three compact UB blocks.

Aligned/non-aligned rows:

```cpp
asc_copy_gm2ub_align(dst, src, n_burst, len_burst,
    left_pad_elements, right_pad_elements, enable_constant_pad,
    asc_load_l2_cache_mode, src_stride, dst_stride);
```

Lengths and strides are bytes and start-to-start. GM may be byte aligned; UB base is 32-byte aligned. When constant padding is disabled, hardware uses its automatic dummy value; use `asc_set_copy_pad_val` for explicit constant padding. PIPE_MTE2.

## GM to UB ND-DMA

### Public and bottom interfaces

Use the public state setters and typed copy:

```cpp
asc_set_ndim_loop0_stride(uint64_t dst_stride, uint64_t src_stride);
// loop1 through loop4 have the same parameter order
asc_set_ndim_pad_count(asc_ndim_pad_count_config& config);
asc_set_ndim_pad_value(T pad_value);
asc_ndim_copy_dci();
asc_ndim_copy_gm2ub(__ubuf__ T* dst, __gm__ T* src,
    uint32_t loop0_size, uint32_t loop1_size, uint32_t loop2_size,
    uint32_t loop3_size, uint32_t loop4_size,
    uint8_t loop0_lp_count, uint8_t loop0_rp_count,
    bool padding_mode, cache_mode);
```

The 3510 implementation selects `nddma_out_to_ub_b8`, `nddma_out_to_ub_b16`, or `nddma_out_to_ub_b32` by `T` width and fixes `sid=0`. A stride setter packs `(src_stride << 20) | (dst_stride & 0xfffff)` into `set_loopi_stride_nddma`; the public limits are 40 source bits and 20 destination bits. Pad counts use `set_pad_cnt_nddma`, the typed pad bit pattern uses `set_pad_val_nddma`, and DCI uses `nd_dma_dci`. Never call these builtins directly.

The C API documentation prefers an `asc_load_l2_cache_mode` final argument, but the installed CANN 9.1 public header used by the verified harness exposes `uint8_t`. Select the overload from the actual installed `include/c_api` first. Numeric modes are `0,1,2,4,5,6`; migrate to enum names when that overload exists.

### Address recurrence and padding

Every size, stride, pointer offset, and pad count is in **elements of `T`**, not bytes or 32-byte blocks. For each dimension `i`:

```text
extent_i = lp_i + size_i + rp_i
p_i in [0, extent_i)                 # destination/padded coordinate
q_i = p_i - lp_i                     # logical source coordinate

dst_element = dst_base + sum(p_i * dst_stride_i)
```

If all `q_i` are in `[0,size_i)`, use:

```text
src_element = src_base + sum(q_i * src_stride_i)
```

If any dimension is outside:

- `padding_mode == true`: write the typed constant set by `asc_set_ndim_pad_value`.
- `padding_mode == false`: clamp every `q_i` independently to `[0,size_i-1]`, then evaluate the source recurrence. This is nearest-value padding.

The C API boolean direction is therefore the inverse of the C++ configuration name `isNearestValueMode`: C API `true` means constant, not nearest. Left padding shifts the destination coordinate; do not add `lp_i` to a valid source coordinate.

Set an unused higher dimension to `size=1,stride=0`; `size=0` is outside `[1,2^20)`. A source stride of zero is a legal broadcast. Avoid destination stride zero or inter-layer overlap unless overwrite behavior is explicitly intended. When destination strides are ascending, reserve enough pitch for every lower-dimensional padded extent.

Initialize `asc_ndim_pad_count_config` with `config=0` before assigning loop1 through loop4 fields. Loop0 pad counts are direct copy arguments; loop1 through loop4 counts are packed in the config union. Uninitialized fields can enable unintended padding.

### Required ordering and synchronization

Program all five strides, all higher pad counts, and the typed pad value before issuing the copy. Call `asc_ndim_copy_dci()` before the NDDMA read; the path has a separate 32KB cache and stale lines matter when cores read and write the same GM address. The state setters are `PIPE_S`; the copy is `PIPE_MTE2`.

Before consuming UB from MTE3, use an MTE2→MTE3 event. If successive GM→UB/NDDMA operations write overlapping UB destinations, serialize them explicitly; this also applies to a normal GM→UB initialization followed by an overlapping NDDMA.

Cache mode changes L2 allocation/replacement behavior, not copy values. Validate these modes with output equivalence and execution success rather than expecting a numeric delta.

### Verified GM-to-UB ND-DMA parameter harness

The local dav-3510 simulator harness is:

```text
examples/02_simd_c_api/03_c_api/00_data_movement/
  data_copy_nddma_parameter_cases
```

The recorded Ascend950PR_9589 run passes 45/45 cases. Coverage includes both pointers, five sizes, ten source/destination strides, ten per-dimension left/right pad counts, typed pad value, constant/nearest mode, all six cache policies, broadcast/transpose/slice, 1D/2D/3D, and 8/16/32-bit element widths. Every case compares all 512 UB bytes after seeding holes with `0xcd`; data-semantic cases must differ from their reference, while cache policies must be byte-identical. Read its `README.md` for formulas and diagrams, `TEST_RESULTS.md` for the recorded run, `build/output/REPORT.md` for per-coordinate mappings, and `run_all.sh` to rerun.

## L1 to L0A and L0B

```cpp
asc_copy_l12l0a(__ca__ T* dst, __cbuf__ T* src,
    uint16_t m_start_position, uint16_t k_start_position,
    uint8_t m_step, uint8_t k_step,
    int16_t src_stride, uint16_t dst_stride);

asc_copy_l12l0b(__cb__ T* dst, __cbuf__ T* src, /* same controls */);

asc_copy_l12l0a_transpose(__ca__ T* dst, __cbuf__ T* src, /* same controls */);
asc_copy_l12l0b_transpose(__cb__ T* dst, __cbuf__ T* src, /* same controls */);
```

| Parameter | Meaning |
|---|---|
| `m_start_position` | Start in M direction, in 16 elements. |
| `k_start_position` | Start in K direction, in 32-byte blocks. |
| `m_step` | M tile count, each step 16 elements. |
| `k_step` | K tile count, each step 32 bytes. |
| `src_stride` | L1 adjacent K-direction fractal start step, in 512 bytes. |
| `dst_stride` | L0 adjacent K-direction fractal start step, in 512 bytes. |

PIPE_MTE1. For half, one M unit is 16 rows and one K unit is a 32-byte/16-element C0. Both strides are adjacent-fractal start-to-start distances in 512-byte units.

After GM2L1 has normalized A and B to logical L1 NZ, conventional `C=AB` uses L0A normal and L0B transpose (NZ→ZN). `A^T` uses L0A transpose; `B^T` uses L0B normal. Do not emulate a transpose by swapping strides. On 950 use the supported 2D `asc_copy_l12l0a_transpose`, not the legacy unsupported `asc_copy_l12l0a_trans` name.

L0B transpose has a ZN destination recurrence. For local block indices, its destination is `m*dst_stride+k`, while an NZ/non-transpose destination is `k*dst_stride+m`. Read [l1-l0-matrix-load.md](l1-l0-matrix-load.md) for the complete address formulas, physical-storage decision table, and diagrams.

### Verified L1 to L0A/L0B parameter harness

The local dav-3510 simulator harness is:

```text
examples/02_simd_c_api/03_c_api/00_data_movement/
  data_copy_l12l0ab_parameter_cases
```

It passes 32/32 cases: 16 row/column physical-storage and mathematical-transpose combinations, plus baseline and seven one-parameter-at-a-time cases for each of L0A and L0B. Every changed parameter is asserted to produce an observable result change.

## L1 to UB

```cpp
asc_copy_l12ub(__ubuf__ T* dst, __cbuf__ T* src,
               bool sub_blockid, uint16_t n_burst,
               uint16_t len_burst, uint16_t src_gap,
               uint16_t dst_gap);
```

`sub_blockid` selects Vector SUB BLOCK 0 or 1. `len_burst` is in 32-byte blocks. `src_gap` and `dst_gap` are gaps from the end of one burst to the start of the next, also in 32-byte blocks. PIPE_MTE1.

The 3510 simulator verifies `sub_blockid=false` targets AIV0 UB and `sub_blockid=true` targets AIV1 UB. The instruction must still be issued by AIC; see [architecture-and-workflow.md](architecture-and-workflow.md) for the mixed-kernel handshake.

Two calls targeting UB0 and UB1 do not imply two independent producer pipes. Both are issued by
the single AIC PIPE_MTE1. The Ascend950PR_9589 simulator measures 128 B/system-cycle for either
single target and also 128 B/system-cycle aggregate when equal-size disjoint L1 regions are sent
to both UBs. Count the dual case by the sum of both outputs, but do not claim 128 B/cycle per UB.

## UB to L1

```cpp
asc_copy_ub2l1(__cbuf__ T* dst, __ubuf__ T* src, uint32_t size);

asc_copy_ub2l1(__cbuf__ T* dst, __ubuf__ T* src,
               uint16_t n_burst, uint16_t len_burst,
               uint16_t src_gap, uint16_t dst_gap);
```

The simple `size` is bytes. Its 3510 implementation converts it to `size/32` DataBlocks, so use a 32-byte multiple unless a later implementation explicitly defines tail handling. The high-dimensional `len_burst` and both gaps are 32-byte blocks; gaps are previous-burst-end-to-next-start. PIPE_MTE3.

Each AIV has its own PIPE_MTE3. When AIV0 and AIV1 concurrently copy equal-size UB regions to
disjoint regions of shared L1, the Ascend950PR_9589 simulator measures 256 B/system-cycle
aggregate versus 128 B/system-cycle for one AIV. Use a common AIC-side makespan from simultaneous
release until both AIVs report completion; summing two separately timed single-AIV results is not
a concurrency measurement.

For UB2L1 and L12UB:

```text
next_src = current_src + (len_burst + src_gap) * 32
next_dst = current_dst + (len_burst + dst_gap) * 32
```

A gap of zero is compact. With `len_burst=1,gap=1`, burst starts are blocks 0, 2, 4; do not pass a start-to-start pitch directly as `gap`.

## Verified GM2UB/UB2L1/L12UB parameter harness

The local simulator harness is:

```text
examples/02_simd_c_api/03_c_api/00_data_movement/
  data_copy_pipe_parameter_cases
```

It runs one `__mix__(1,2)` process and covers 24 cases: 8 GM2UB, 8 UB2L1, and 8 L12UB. The recorded `dav-3510` Ascend950PR_9589 simulator run passed 24/24 with exact comparison of all defined bytes and empty exception logs. Use `README.md` for parameter diagrams, `TEST_RESULTS.md` for the recorded result, `build/output/REPORT.md` for per-case source/destination mappings, and `run_all.sh` to rebuild and run.

The harness establishes two non-obvious architecture facts: L12UB is emitted by AIC even though it targets UB, and both AIVs must participate in a `__mix__(1,2)` AIV→AIC block handshake. Golden masks exclude stride/gap holes because untouched UB/L1 bytes are not defined as zero.

## Typical shape recipes

### Half `[128,128]` ND to dense NZ

```cpp
constexpr uint64_t nz = uint64_t(1) | (uint64_t(1) << 16) |
                        (uint64_t(128) << 32);
asc_set_gm2l1_nz_para(nz);
asc_copy_gm2l1_nd2nz(l1, gm,
    128 * sizeof(half), 0, 128, 128, 0, false);
```

There are eight D/C0 blocks because half C0 contains 16 elements. Output size is `8*128*32 = 32768` bytes.

### Half physical `[D,N]=[128,128]` via DN2NZ

Use the same destination config and:

```cpp
asc_copy_gm2l1_dn2nz(l1, gm,
    128 * sizeof(half), 0, 128, 128, 0, false);
```

The equal dimensions hide the orientation difference; test an asymmetric shape such as `[D,N]=[48,96]` to validate DN semantics.

### L1↔UB two rows of 128 bytes with a 64-byte hole

Each row has `len_burst=4` blocks and the hole is `gap=2` blocks:

```cpp
asc_copy_l12ub(ub, l1, sub_blockid, 2, 4, 2, 0);
asc_copy_ub2l1(l1_out, ub, 2, 4, 0, 2);
```

The source row starts are 192 bytes apart for the first call: `(4+2)*32`.

## Common failures

- Passing zero stride to ordinary GM→L1 mode 0 for a compact multi-burst copy. On 3510 the fields are start-to-start; compact mode 0 uses `src_stride = dst_stride = len_burst`.
- Passing zero for the active insertion/removal stride. Compact insertion uses `dst_stride=1`; compact removal uses `src_stride=1`.
- Passing zero stride to GM→UB high-dimensional mode expecting compact packing. On 3510 start-to-start stride must equal row length.
- Passing a row pitch directly as L1↔UB `gap`. Convert with `gap = pitch/32 - len_burst`.
- Calling `asc_copy_l12ub` from the AIV branch. The 3510 implementation is guarded by `ASC_IS_AIC` and can silently do nothing on AIV.
- Letting only the active AIV arrive at an AIV→AIC block handshake in `__mix__(1,2)`. The idle AIV must arrive too or the AIC wait can deadlock.
- Reusing one cross-core flag for opposite directions before the first arrive/wait lifetime has completed. Prefer separate AIV→AIC and AIC→AIV flag IDs.
- Letting concurrent UB2L1 copies overlap in L1. The two AIV MTE3 engines can run together, but
  each must own a disjoint destination range unless an overwrite/race is explicitly intended.
- Expecting stride/gap-created holes to be zero. Compare only the bytes each instruction defines as written.
- Omitting `asc_set_gm2l1_nz_para`, or letting an intervening copy overwrite the shared configuration.
- Using ND2NZ for a physical `[D,N]` source. ND2NZ assumes N rows; DN2NZ walks D by `loop1_src_stride` and N contiguously.
- Treating D-tail bytes as uninitialized. ND/DN→NZ defines zero padding.
- Reversing SmallC0 because of the generated doc wording. Enforce `smallc0_en == true` only for `D<=4`.
- Comparing stride-created holes or bytes beyond the defined SmallC0 group against zero. Mask unwritten bytes in the golden comparison.
- Testing ND2NZ and DN2NZ only with square shapes. Use an asymmetric case such as `N=3,D=18`; equal dimensions can hide a reversed physical orientation.
- Mixing 32-byte, 512-byte, byte, and element units in the same expression. Convert from a named byte quantity once.
