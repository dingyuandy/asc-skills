# dav-2201 FIXPIPE

## Paths and role

FIXPIPE consumes L0C on the AIC and writes either GM or L1:

```text
L0C --asc_copy_l0c2gm--> GM
L0C --asc_copy_l0c2l1--> L1
```

Both paths can combine data-type conversion/quantization, activation, NZ2ND, channel transformation, and MMAD unit-flag coordination. There is no 2201 L0C→UB path and no NZ2DN feature.

Use `PIPE_FIX` for dependency reasoning. The per-API 2201 `asc_copy_l0c2gm` page currently says `PIPE_MTE1`, but the architecture guide, C API pipeline overview, implementation (`copy_matrix_cc_to_gm`), and MMAD-to-FIX flow identify it as FIXPIPE.

## Public output combinations

The documented `asc_copy_l0c2gm` type pairs include:

| L0C source | GM destination | Relevant mode family |
|---|---|---|
| float | float | NoQuant |
| float | half | F322F16 |
| float | bfloat16 | F322BF16 |
| float | int8/uint8 | scalar/vector QF322B8_PRE |
| int32 | int32 | NoQuant |
| int32 | half | scalar/vector DEQF16 |
| int32 | int16 | documented conversion/requant path |
| int32 | int8 | scalar/vector REQ8 |

The documented `asc_copy_l0c2l1` pairs include float→half/bfloat16/int8 and int32→half/int16/int8/uint8, plus `void*` destination overloads used by B4 modes. Select the exact overload and `QuantMode_t` supported by the current public header and 2201 implementation; do not infer a mode from equal byte widths.

The nine named modes in the 2201 pages are:

```text
NoQuant, F322F16, F322BF16,
DEQF16, VDEQF16,
QF322B8_PRE, VQF322B8_PRE,
REQ8, VREQ8
```

Scalar and vector quantization have different parameter sources and must be tested independently.

## Parameter setters

- `asc_set_l0c_copy_prequant(config)`: scalar quant/dequant parameter, `PIPE_S`.
- `asc_copy_l12fb`: move vector quant/ReLU parameters from L1 to the 2 KiB Fixpipe Buffer, `PIPE_MTE1`.
- `asc_set_l0c2gm_config(relu_pre, quant_pre, enable_unit_flag)`: select vector parameter addresses and unit-flag behavior, `PIPE_S`.
- `asc_set_l0c_copy_params(nd_num, src_nd_stride, dst_nd_stride)`: 2201 NZ2ND configuration. `src_nd_stride` is in 1024-byte L0C fractals and `dst_nd_stride` is in destination elements.
- `asc_set_l0c2gm_lrelu_alpha(half_or_float)`: configure Leaky ReLU alpha, `PIPE_S`.

Do not use `asc_set_l0c2gm_nz2nd` on A2/A3: its own support table marks 2201 products unsupported. That similarly named setter belongs to the newer path. Use `asc_set_l0c_copy_params` for 2201.

Setters are stateful. Program them immediately before the dependent FIXPIPE command where possible, order the L1→FB copy before parameter consumption, and avoid interleaving unrelated commands that mutate the same state.

## Geometry and layout

Core arguments:

```text
n_size: valid N extent of the source L0C NZ matrix
m_size: valid M extent
src_stride: adjacent source Z pitch, in C0_SIZE of source type
dst_stride:
  NZ output  -> adjacent destination Z pitch, destination elements
  ND output  -> destination row pitch, destination elements
```

Without NZ2ND, ordinary N is normally a multiple of 16; FP32 channel split uses an 8-element N granularity. NZ2ND allows FIXPIPE to drop padded M/N results and produce row-major ND. Use valid logical extents for output while ensuring MMAD input/L0C allocation covers the padded physical computation.

Channel transformations:

- S8/U8 channel merge combines adjacent 16-channel fractals into 32-channel output fractals.
- S4/U4 channel merge combines four 16-channel fractals into a 64-channel output fractal.
- FP32 channel split turns one 16x16 result fractal into two 16x8 fractals.
- FP32 channel split cannot be combined with NZ2ND in the documented C API.

ReLU and Leaky ReLU are side functions of the same output command. Validate each type/mode combination rather than assuming all side functions compose with all quant and layout modes.

## Unit flag

Unit flag pipelines MMAD and FIXPIPE at fractal granularity:

```text
MMAD unit_flag 2: keep unit mode enabled
MMAD unit_flag 3: final unit and close mode
FIXPIPE unit_flag_mode 2/3: matching output behavior
```

Do not use unit flag when accumulating multiple MMADs into the same L0C result. Establish a normal M→FIX dependency when unit mode is disabled. When enabled, test multi-fractal ordering and the final close explicitly; a functionally correct single-fractal case does not validate the protocol.

## Validation matrix

For each destination path, generate cases across:

```text
source type x destination type x quant mode
x scalar/vector parameter source
x ReLU/Leaky ReLU
x NZ/NZ2ND
x channel split/merge
x unit flag off/on
x aligned and tail M/N
```

Only include combinations supported by the 2201 public contract. Use nontrivial negative/positive L0C values to expose activation, distinct per-channel vector parameters to distinguish scalar from vector quantization, and asymmetric M/N to validate NZ2ND. Compare every defined output element and verify padding/hole behavior separately.

Do not treat a dav-3510 FIXPIPE example or simulator result as 2201 evidence. The repository's 2201 forwarding tests under `tests/api/c_api/npu_arch_2201/cube_datamove` prove selected builtins and type overloads, but an end-to-end simulator matrix is still required for numerical semantics and throughput.
