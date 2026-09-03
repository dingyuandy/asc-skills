# dav-3510 L1 to L0A/L0B matrix loads

## Contents

- Public interfaces and lowering
- Parameter meanings and address formulas
- NZ and ZN destination layouts
- Choosing normal versus transpose
- Parameter-test design
- Verified harness
- Pitfalls

## Public interfaces and lowering

Use the public 2D APIs:

```cpp
template <typename T>
__aicore__ inline void asc_copy_l12l0a(
    __ca__ T* dst, __cbuf__ T* src,
    uint16_t m_start_position, uint16_t k_start_position,
    uint8_t m_step, uint8_t k_step,
    int16_t src_stride, uint16_t dst_stride);

template <typename T>
__aicore__ inline void asc_copy_l12l0a_transpose(
    __ca__ T* dst, __cbuf__ T* src,
    uint16_t m_start_position, uint16_t k_start_position,
    uint8_t m_step, uint8_t k_step,
    int16_t src_stride, uint16_t dst_stride);

template <typename T>
__aicore__ inline void asc_copy_l12l0b(
    __cb__ T* dst, __cbuf__ T* src,
    uint16_t m_start_position, uint16_t k_start_position,
    uint8_t m_step, uint8_t k_step,
    int16_t src_stride, uint16_t dst_stride);

template <typename T>
__aicore__ inline void asc_copy_l12l0b_transpose(
    __cb__ T* dst, __cbuf__ T* src,
    uint16_t m_start_position, uint16_t k_start_position,
    uint8_t m_step, uint8_t k_step,
    int16_t src_stride, uint16_t dst_stride);
```

On dav-3510 these select `load_cbuf_to_ca` or `load_cbuf_to_cb`; the transpose overload sets the builtin transpose control to 1. Treat those builtins as implementation details and do not call them from operator code.

These instructions execute on `PIPE_MTE1`. Synchronize an MTE2 producer before the load and synchronize MTE1 before `PIPE_M` consumes L0A/L0B.

The similarly named legacy `asc_copy_l12l0a_trans` is documented as unsupported on Ascend 950. For dav-3510 2D transpose loads, use `asc_copy_l12l0a_transpose`. Also distinguish the full 2D `asc_copy_l12l0b_transpose` API from repeat-style compatibility overloads.

### MX scale loads

MXFP4/MXFP8 matrix data uses the ordinary L1→L0 load above; its E8M0 scale matrix is loaded
separately with:

```cpp
__aicore__ inline void asc_copy_l12l0a_mx(
    uint64_t dst, __cbuf__ fp8_e8m0_t* src,
    uint16_t x_start_pos, uint16_t y_start_pos,
    uint8_t x_step, uint8_t y_step,
    uint16_t src_stride, uint16_t dst_stride);

__aicore__ inline void asc_copy_l12l0b_mx(
    uint64_t dst, __cbuf__ fp8_e8m0_t* src,
    uint16_t x_start_pos, uint16_t y_start_pos,
    uint8_t x_step, uint8_t y_step,
    uint16_t src_stride, uint16_t dst_stride);
```

| Parameter | Unit | Meaning |
|---|---|---|
| `dst` | L0 byte address divided by 16 | MX scale destination associated with an L0A/L0B data operand. Build it as `uint64_t(reinterpret_cast<uintptr_t>(l0_data)) / 16`. Do not pass the typed pointer directly. |
| `src` | address | L1 E8M0 scale matrix base. |
| `x_start_pos` | 32-byte scale fractals | Start along M for A or N for B. |
| `y_start_pos` | 32-byte units | Start along the scale-K direction. |
| `x_step` | count, `uint8_t` | Number of scale fractals along M/N. Range 0..255 in the CANN 9.1 documentation. |
| `y_step` | count, `uint8_t` | Number of 32-byte units along scale-K. Range 0..255. |
| `src_stride` | 32 bytes | Start-to-start pitch between adjacent source X fractals. |
| `dst_stride` | 32 bytes | Start-to-start pitch between adjacent destination X fractals. |

The transferred scale payload is `x_step * y_step * 32` bytes. For a dense matrix with
`M=N=128,K=1024`, there are `K/32=32` E8M0 scales per row. The verified full load uses
`x_step=128/16=8`, `y_step=(K/32)/2=16`, and `src_stride=dst_stride=16`, hence 4096 bytes.
The scale L1 input is prepared with DN2NZ using `height=(K/32)/2`; follow the official MX
example rather than treating the E8M0 byte matrix as an ordinary row-major copy.

Both APIs execute on PIPE_MTE1 and lower to the dedicated `load_cbuf_to_ca_mx`/
`load_cbuf_to_cb_mx` instructions on dav-3510. In the Ascend950PR_9589 simulator, independently
timed 2/3/4 KiB scale loads fit exactly to **32 B/system-cycle** for both L0A and L0B. This was
validated by a real MXFP4 `128x1024x128` MMAD with data=1, A scale=2, B scale=1, producing 2048.

## Parameter meanings and address formulas

For these APIs a fractal is 512 bytes. For half, it is a 16-row by 16-column tile because one C0 is 32 bytes or 16 half elements.

| Parameter | Unit | Meaning |
|---|---|---|
| `dst` | address | L0A (`__ca__`) or L0B (`__cb__`) base. |
| `src` | address | L1 (`__cbuf__`) NZ base. |
| `m_start_position` | 16 elements | Source starting position on the M axis. For half, 1 selects row block 16. |
| `k_start_position` | 32-byte C0 | Source starting position on the K axis. For half, 1 selects column block 16. |
| `m_step` | count of 16-element blocks | Number of M blocks copied. |
| `k_step` | count of 32-byte C0 blocks | Number of K blocks copied. |
| `src_stride` | 512 bytes | Source start-to-start distance between adjacent K-direction fractal groups. It is signed in the public signature. |
| `dst_stride` | 512 bytes | Destination start-to-start distance along the destination K/fractal direction. |

Do not interpret either stride as bytes, 32-byte DataBlocks, or an end-to-next-start gap.

For local loop indices `m` and `k`, the L1 source fractal number is:

```text
src_fractal(k, m) =
    (k_start_position + k) * src_stride
  + (m_start_position + m)
```

For a dense half matrix whose logical M dimension is `M`, a common source stride is `ceil(M/16)`. A dense 32x32 source therefore uses `src_stride=2`; a dense 64x64 source uses `src_stride=4`.

## NZ and ZN destination layouts

Let `Fmk` denote source fractal at M block `m`, K block `k`. A dense 2x2 fractal grid has these linear orders:

```text
L1 NZ / L0A normal        L0B transpose / ZN
0: F00                    0: F00
1: F10                    1: F01
2: F01                    2: F10
3: F11                    3: F11
```

The normal destination recurrence is:

```text
dst_fractal(k, m) = k * dst_stride + m
```

The verified L0B transpose/ZN recurrence is:

```text
dst_fractal(m, k) = m * dst_stride + k
```

This distinction is observable when `dst_stride` is not dense. With `dst_stride=3`, a 2x2 load writes linear destinations 0, 1, 3, 4 in both cases, but indices 0..3 consumed by MMAD represent different grids:

```text
L0A normal, dense NZ view       L0B transpose, dense ZN view

F00   zero                      F00   F01
F10   F01                       zero  F10
```

Do not reuse an L0A/NZ golden model for L0B transpose stride tests.

## Choosing normal versus transpose

The decision depends on what logical matrix L1 currently represents, not just the GM file's declared storage order.

### When GM2L1 normalizes physical storage

Prefer ND2NZ for a row-major physical matrix and DN2NZ for a column-major physical matrix when the intent is to produce the same logical L1 NZ layout. Once L1 is normalized:

| Mathematical operand | L0A load | L0B load |
|---|---|---|
| original matrix | normal | transpose |
| transposed matrix | transpose | normal |

The L0B baseline is opposite to L0A because a conventional `C=AB` MMAD expects A in the L0A NZ orientation and B in the L0B ZN orientation.

### When both physical layouts are ingested with ND2NZ

If a column-major byte stream is deliberately passed through ND2NZ as though it were row-major, L1 represents the physical transpose. Let `storage_col` describe the GM byte stream and `math_transpose` describe the requested mathematical operand. The simulator-verified rules are:

```text
L0A_transpose = A_storage_col XOR A_math_transpose
L0B_transpose = NOT(B_storage_col XOR B_math_transpose)
```

Equivalent decision table:

| Operand | GM storage | Math operand | Load |
|---|---|---|---|
| A | row | A | L0A normal |
| A | row | A^T | L0A transpose |
| A | column | A | L0A transpose |
| A | column | A^T | L0A normal |
| B | row | B | L0B transpose |
| B | row | B^T | L0B normal |
| B | column | B | L0B normal |
| B | column | B^T | L0B transpose |

Use this XOR/XNOR rule only after confirming that GM2L1 preserved the physical interpretation this way. If DN2NZ already normalized column-major storage, use the simpler normalized-L1 table instead.

## Parameter-test design

Make each parameter case observable and isolate one field at a time:

1. Use a source larger than the target tile, such as a non-symmetric 64x64 half matrix for a 32x32 output. This makes start positions and source stride select different data.
2. Establish a baseline, for example `m_start=0`, `k_start=0`, `m_step=2`, `k_step=2`, `src_stride=4`, and `dst_stride=2`.
3. Change exactly one value per case: each start, each step, each stride, then normal versus transpose.
4. Preinitialize the entire L0 operand when a reduced step or non-dense destination stride leaves a fractal unwritten. Otherwise MMAD consumes undefined data and a zero-based golden is invalid.
5. Multiply by an identity matrix so the copied operand is exposed at the output. Still run MMAD and FIXPIPE to validate that the physical L0 layout is consumable.
6. Compare the complete output to an independent block-level golden.
7. Assert that every changed case differs from baseline; a passing comparison alone does not prove the parameter had an effect if the input is symmetric or repetitive.
8. For transpose combinations, use non-symmetric A and B. Check that all physical-storage variants of one mathematical expression agree, and that toggling each mathematical transpose changes the output.

Use square matrices when one suite must execute all of `AB`, `AB^T`, `A^T B`, and `A^T B^T` without changing dimensions. State this constraint explicitly; it is a test-shape choice, not an API restriction.

## Verified harness

The runnable simulator harness is:

```text
examples/02_simd_c_api/03_c_api/00_data_movement/
  data_copy_l12l0ab_parameter_cases
```

It targets `dav-3510` in `sim` mode and contains 32 cases:

- 16 cases for two A storage layouts times two B storage layouts times four mathematical transpose expressions;
- 8 L0A cases: baseline plus one case for each of the seven controls;
- 8 L0B cases: baseline plus one case for each of the seven controls.

The recorded Ascend950PR_9589 run passed 32/32 exact float comparisons. All seven parameter changes for both L0A and L0B produced an output different from baseline. See the harness `README.md` for diagrams and `TEST_RESULTS.md` for recorded difference counts.

## Pitfalls

- `m_start_position=1` does not mean one element. It means 16 elements.
- `k_start_position=1` does not mean one element. It means one 32-byte C0; for half that is 16 elements.
- `src_stride=0` or `dst_stride=0` is not a generic compact default when multiple fractal groups are active. Derive the dense stride from the layout.
- L0B transpose produces ZN; its non-dense `dst_stride` golden needs `m*stride+k`, not `k*stride+m`.
- Smaller `m_step`/`k_step` and larger `dst_stride` leave holes. They are not guaranteed to contain zero unless initialized.
- GM row/column-major storage and mathematical transpose are independent. First determine what matrix GM2L1 placed in L1.
- Normal L0B is not automatically correct for normal `B`. Conventional `AB` uses an L0B transpose load from an L1 NZ B operand.
- Do not infer dav-3510 support from a similarly named legacy `*_trans` API; locate the exact public overload and architecture implementation.
- Keep the pipeline chain explicit: MTE2 produces L1, MTE1 produces L0A/L0B, PIPE_M consumes them, and PIPE_FIX exports L0C.
- MX scale `dst` is the associated L0 data address divided by 16, not a normal `__ca__`/`__cb__`
  pointer argument. Count scale bytes as `x_step*y_step*32`, independently of packed matrix bytes.
