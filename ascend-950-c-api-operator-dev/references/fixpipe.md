# dav-3510 FIXPIPE: L0C output, layout, quantization, and dual UB

## Contents

- L0C to GM interface
- L0C to UB interface and dual mode
- Dual mode device portability rule
- Shared geometry parameters
- Pre-stage side functions
- Post-stage quantization: contract versus observed behavior
- Quantization parameter registers
- Validity rules
- Typical calls

## L0C to GM interface

Representative public signature (typed overloads restrict valid source/destination pairs):

```cpp
__aicore__ inline void asc_copy_l0c2gm(
    __gm__ DstT* dst, __cc__ SrcT* src,
    uint16_t n_size, uint16_t m_size,
    uint32_t dst_stride, uint16_t src_stride,
    uint8_t l2_cache_mode, uint8_t enable_clip_relu_pre,
    uint8_t unit_flag_mode, uint64_t quant_pre_mode,
    uint8_t relu_pre_mode, bool enable_channel_split,
    bool enable_nz2nd, uint64_t quant_post,
    uint8_t relu_post, bool clip_relu_post,
    uint8_t eltwise_op, bool eltwise_antq_en,
    bool c0_pad_en, bool broadcast_en, bool enable_nz2dn);
```

The 3510 implementation lowers to the L0C→GM FIXPIPE builtin. This API has no `dual_dst_ctrl`: GM is one destination address space.

## L0C to UB interface and dual mode

```cpp
__aicore__ inline void asc_copy_l0c2ub(
    __ubuf__ DstT* dst, __cc__ SrcT* src,
    uint16_t n_size, uint16_t m_size,
    uint32_t dst_stride, uint16_t src_stride,
    uint8_t dual_dst_ctrl, bool sub_blockid,
    uint8_t enable_clip_relu_pre, uint8_t unit_flag_mode,
    uint64_t quant_pre_mode, uint8_t relu_pre_mode,
    bool enable_channel_split, bool enable_nz2nd,
    uint64_t quant_post, uint8_t relu_post,
    bool clip_relu_post, uint8_t eltwise_op,
    bool eltwise_antq_en, bool c0_pad_en,
    bool broadcast_en, bool enable_nz2dn);
```

`dual_dst_ctrl` values:

| Value | Behavior | Constraint |
|---|---|---|
| `0` | Send the whole matrix to the UB selected by `sub_blockid`. | `sub_blockid` is 0 or 1. |
| `1` | Split M in half and write `M/2 × N` to each AIV UB. | M must be even. |
| `2` | Split N in half and write `M × N/2` to each AIV UB. | N must be a multiple of 32. |

The device contract supports dual mode only for ordinary NZ→NZ or NZ→ND
movement and excludes quantization, ReLU, channel split/merge, and every other
side function. In dual mode both AIVs must have compatible UB allocation and
synchronization.

The task description sometimes calls this “L0C→GM dual mode”; that is a naming error. The parameter exists on `asc_copy_l0c2ub`, not `asc_copy_l0c2gm`.

## Dual mode device portability rule

CANN 9.1.0 has a dangerous simulator false negative on dav-3510. The public
float-to-float overload accepts `dual_dst_ctrl` and `relu_pre_mode`, its
implementation forwards both fields, and the Ascend950PR_9589 simulator can
produce numerically correct output for dual-N + NZ2ND + Normal ReLU. On-device
execution nevertheless rejects or misbehaves for this unsupported
combination. Treat device evidence and the documented exclusion as
authoritative: when `dual_dst_ctrl != 0`, set all side-function controls to
neutral values. Apply ReLU on each AIV after waiting for PIPE_FIX readiness,
for example with register `asc_max_scalar(score, score, 0.0f, mask)`.

Do not use implementation-field forwarding, a clean simulator exception log,
or a passing simulator Golden comparison as proof that a dual-mode side
function is supported on hardware.

## Shared geometry parameters

| Parameter | Meaning |
|---|---|
| `dst` | GM or UB output. GM ND may be byte aligned; NZ/DN and UB destinations require 32-byte alignment according to their format/API. |
| `src` | L0C NZ base, 64-byte aligned. |
| `n_size` | Logical N size, range `[1,4095]`. |
| `m_size` | Logical M size; `[1,65535]` without NZ2ND and `[1,8192]` with NZ2ND. |
| `dst_stride` | Without NZ2ND: start stride between adjacent destination NZ-Z units, in destination elements. With NZ2ND/NZ2DN: destination ND/DN row length, in elements. Must be nonzero. |
| `src_stride` | Start stride between adjacent source NZ-Z units, in `C0_Size` units. For L0C `SrcT` float/int32, one unit is `16*sizeof(SrcT)=64` bytes. |
| `l2_cache_mode` | L0C→GM only: `0` normal, `4` disable; LAST/persistent modes are not currently supported. |
| `enable_nz2nd` | Convert source NZ to destination ND. |
| `enable_nz2dn` | Convert source NZ to destination DN. Select only a supported combination. |

PIPE_FIX.

## Pre-stage side functions

### `quant_pre_mode`

This enum chooses both the type path and scalar/tensor parameter mode. Important families:

| Source→destination | Scalar | Tensor/vector |
|---|---|---|
| no quantization | `NoQuant` | — |
| int32→half | `DEQF16` | `VDEQF16` |
| int32→int4 | `REQ4` | `VREQ4` |
| int32→int8/uint8 | `REQ8` | `VREQ8` |
| int32→bfloat16 | `QS322BF16_PRE` | `VQS322BF16_PRE` |
| float→half | `QF322F16_PRE` | `VQF322F16_PRE` |
| float→bfloat16 | `QF322BF16_PRE` | `VQF322BF16_PRE` |
| float cast | `F322F16` | `F322BF16` |
| float→int4 | `QF322S4_PRE` | `VQF322S4_PRE` |
| float→int8/uint8 | `QF322B8_PRE` | `VQF322B8_PRE` |
| float→FP8 E4M3 | `QF322FP8_PRE` | `VQF322FP8_PRE` |
| float→HiFloat8 | `QF322HIF8_PRE` / hybrid | `VQF322HIF8_PRE` / hybrid |
| float→float scaled | `QF322F32_PRE` | `VQF322F32_PRE` |

Do not choose only by destination type. Check the exact typed overload and the combination diagram in the local 3510 docs.

### ReLU and unit flag

| Parameter | Values |
|---|---|
| `enable_clip_relu_pre` | `0` off, `1` scalar Clip ReLU. Requires normal ReLU and quantization. |
| `relu_pre_mode` | `0` off, `1` normal, `2` scalar, `3` vector/tensor. |
| `unit_flag_mode` | `0` off; `2` on and do not reset; `3` on and reset after command. The matching MMAD and output command must use compatible unit flags. |
| `enable_channel_split` | Only when source and destination are float; incompatible with NZ2ND. |

## Post-stage quantization: contract versus observed behavior

There is a real source discrepancy on CANN 9.1.0:

- The public 3510 `asc_copy_l0c2gm` and `asc_copy_l0c2ub` pages call these invalid placeholders and prescribe neutral values:

```cpp
quant_post       = 0;
relu_post        = 0;
clip_relu_post   = false;
eltwise_op       = 0;
eltwise_antq_en  = false;
c0_pad_en        = false;
broadcast_en     = false;
```

- The public 950 API also exposes `asc_set_l0c2gm_quant_post(uint64_t)` and `asc_set_l0c2gm_relu_alpha(uint64_t)`.
- The raw 3510 C API implementation forwards `quant_post` and `relu_post` to the FIXPIPE builtin.
- CANN 9.1.0 dav-3510 simulator tests in `data_copy_fixpipe_parameter_cases`, cases 24–32, prove observable post-stage behavior for both L0C→GM and L0C→UB.

Treat this as **version-locked implementation behavior, not a portable public copy-API contract**. For documentation-compatible production code, keep neutral values. If a project intentionally depends on the raw post stage, pin the CANN/compiler version and rerun output-difference tests after every upgrade. High-level FIXPIPE wrappers currently force `QuantMode_post::NoConv`; use the full raw public C API signature for such controlled experiments.

### Empirically verified two-stage path

The following scalar chain passed whole-matrix comparison in CANN 9.1.0 sim:

```text
int32 L0C
  -- quant_pre=DEQF16, pre scale P --> half intermediate
  -- quant_post=QF162B8_POST, post scale Q --> signed int8

result = round/saturate(C * P * Q)
```

Verified scales and effects:

| Path | P | Q | ReLU | Result |
|---|---:|---:|---|---|
| GM | 1 | 1 | off | `C` |
| GM | 2 | 1 | off | `2C` |
| GM | 2 | 3 | off | `6C` |
| GM | 2 | 3 | pre Normal | `max(6C,0)` |
| GM | 2 | 3 | post Normal | `max(6C,0)` |
| UB | 1 | 1 | off | `C` |
| UB | 1 | 3 | off | `3C` |
| UB | 1 | 3 | pre/post Normal, tested separately | `max(3C,0)` |

Therefore quant pre can combine with quant post and either Normal pre-ReLU or Normal post-ReLU in this exact tested chain. Mode compatibility is determined by the intermediate type: `DEQF16` supplies F16 semantics, which matches `QF162B8_POST`. Do not combine arbitrary pre/post enum names without checking the intermediate type and typed destination overload.

With positive scales, pre- and post-ReLU yield the same final values, so their outputs prove that each switch is effective but do not prove stage ordering. Use negative scale, offsets, saturation, or asymmetric quantization if ordering itself must be distinguished.

## Quantization parameter registers

Scalar modes use a packed scalar parameter configured through the matching public setter, commonly:

```cpp
asc_set_l0c_copy_prequant(uint64_t config);
```

For the empirically verified post B8 path:

```cpp
// IEEE float bits are suitable here because the documented M3 field is bits 31:13.
constexpr uint64_t post_scale_1_signed = 0x3f800000ULL | (1ULL << 9);
constexpr uint64_t post_scale_3_signed = 0x40400000ULL | (1ULL << 9);
asc_set_l0c2gm_quant_post(post_scale_3_signed);
```

For `QF162B8_POST`/`QS162B8_POST`, QUANT_POST bits 31:13 hold scalar M3, bit 9 selects signed B8, and bits 8:0 hold the offset. M3 must not be INF/NAN. The setter name contains `l0c2gm`, but CANN 9.1.0 sim shows that it also supplies the raw L0C→UB post stage; regard that sharing as version-dependent.

`asc_set_l0c2gm_relu_alpha` packs scalar pre-ReLU M2 in bits 31:13 and scalar post-ReLU M2 in bits 63:45. It is unnecessary for Normal ReLU mode 1.

Tensor/vector quantization and vector ReLU use Fixpipe Buffer parameters. A common flow is:

```text
GM --asc_copy_gm2l1--> L1 --asc_copy_l12fb--> Fixpipe Buffer
                                      |
                            configure address/enable
                                      |
                       asc_copy_l0c2gm / asc_copy_l0c2ub
```

Configure the parameter addresses with the public 3510 setter such as `asc_set_l0c2gm_config`. The hardware address encoding uses 128-byte units; therefore an FBUF byte address must be 128-byte aligned and encoded only after that check. Preserve the exact setter signature from the installed header because its fields depend on the destination path.

## Validity rules

- Validate `SrcT`, `DstT`, `quant_pre_mode`, and ReLU mode as a tuple.
- Also validate the pre intermediate type against `quant_post`; for example, tested `DEQF16 → QF162B8_POST`, not an arbitrary pair.
- Unless intentionally running a version-pinned post-stage test, neutralize every documentation-invalid placeholder explicitly; never pass uninitialized values.
- Channel split and NZ2ND cannot both be enabled.
- Dual UB mode cannot be combined with side functions. Keep every side control
  neutral even if the implementation forwards it and the simulator produces a
  correct Golden result; on-device behavior is authoritative.
- Clip ReLU requires normal ReLU plus quantization.
- Unit flag must agree with the MMAD producer; otherwise results can be moved before/after the intended fractal boundary.
- Keep L0C source 64-byte aligned and the destination aligned for its selected format.
- For vector quantization, reserve enough FBUF parameter data and synchronize GM→L1→FBUF before FIXPIPE reads it.
- A test is evidence that a side parameter works only if changing that parameter alone changes output. Assert both the mathematical golden and bytewise inequality against the baseline.

## Typical calls

### Plain float L0C→GM NZ

```cpp
asc_copy_l0c2gm(dst_gm, src_l0c,
    N, M, dst_nz_stride_elements, src_nz_stride_c0,
    0, 0, 0, QuantMode_t::NoQuant, 0,
    false, false,
    0, 0, false, 0, false, false, false, false);
```

### Plain L0C→one UB

```cpp
asc_copy_l0c2ub(dst_ub, src_l0c,
    N, M, dst_nz_stride_elements, src_nz_stride_c0,
    0, /*sub_blockid=*/false,
    0, 0, QuantMode_t::NoQuant, 0,
    false, false,
    0, 0, false, 0, false, false, false, false);
```

### Split M across two UBs

```cpp
// M must be even; all side functions off.
asc_copy_l0c2ub(dst_ub_base, src_l0c,
    N, M, dst_stride, src_stride,
    /*dual_dst_ctrl=*/1, /*sub_blockid ignored in dual mode=*/false,
    0, 0, QuantMode_t::NoQuant, 0,
    false, enable_nz2nd,
    0, 0, false, 0, false, false, false, false);
```

### Version-pinned scalar pre + scalar post experiment

```cpp
asc_set_l0c_copy_prequant(0x40000000ULL);                   // pre scale 2.0
asc_set_l0c2gm_quant_post(0x40400000ULL | (1ULL << 9));     // post scale 3.0, signed B8
asc_copy_l0c2gm(dst_i8, src_i32,
    N, M, N, M,
    0, 0, 0,
    static_cast<uint64_t>(QuantMode_t::DEQF16),
    /*relu_pre_mode=*/0, false, true,
    static_cast<uint64_t>(QuantMode_post::QF162B8_POST),
    /*relu_post=*/0, false, 0, false, false, false, false);
// Expected for small exact inputs: round/saturate(C * 2 * 3).
```

This example deliberately uses implementation behavior that conflicts with the copy API page. Keep a neutral-value path available when portability is required.

Use named constants or a wrapper configuration struct around these long signatures. Add compile-time/runtime assertions for geometry and mode constraints before expanding into the raw call.
