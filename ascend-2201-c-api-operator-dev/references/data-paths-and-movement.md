# dav-2201 data paths and movement

## Contents

- Supported path map
- Storage alignment and capacity
- Vector-side transfers
- Cube-side transfers
- Stride and unit rules
- Architecture exclusions
- Validation workflow

## Supported path map

| Source | Destination | Pipeline | Public C API family | dav-2201 notes |
|---|---|---|---|---|
| GM | UB | MTE2 | `asc_copy_gm2ub`, `_align` | ordinary and non-32B-aligned forms |
| UB | GM | MTE3 | `asc_copy_ub2gm`, `_align` | aligned form discards dummy pad on GM write |
| UB | UB | MTE3 | `asc_copy_ub2ub` | confirm selected overload and units |
| GM | L1 | MTE2 | `asc_copy_gm2l1`, pad1..pad8 | raw, 2D fractal, pad modes |
| GM ND | L1 NZ | MTE2 | `asc_copy_gm2l1_nd2nz` | explicit ND/NZ stride fields |
| GM | L0A/L0B | MTE2 | `asc_copy_gm2l0a/b` | 2201-only direct 2D-fractal path; no transpose |
| L1 | L0A/L0B | MTE1 | `asc_copy_l12l0a/b`, `_trans`, 3D | 512B fractal loads and layout conversion |
| L1 | L0C | MTE1 | `asc_copy_l12l0c` | initializes/copies accumulator storage |
| L1 | BiasTable | MTE1 | `asc_copy_l12bt` | BiasTable address is passed in API-defined form |
| L1 | Fixpipe Buffer | MTE1 | `asc_copy_l12fb` | stores vector quant/ReLU parameters |
| L1 | GM | MTE1 | `asc_copy_l12gm` | supported on 2201, removed on 3510 |
| L0C | GM | FIX | `asc_copy_l0c2gm` | FIXPIPE transformations and output |
| L0C | L1 | FIX | `asc_copy_l0c2l1` | FIXPIPE transformations and output |

There is no 2201 UB-to-L1, L1-to-UB, or L0C-to-UB C API path. AIC and AIV exchange intermediate data through GM. Do not call a generic declaration merely because it exists in a shared header; verify `impl/c_api/instr_impl/npu_arch_2201`.

The architecture guide and C API pipeline overview identify L0C output as `PIPE_FIX`. The per-API `asc_copy_l0c2gm_arch_2201.md` currently labels it `PIPE_MTE1`; treat that label as a documentation discrepancy because the implementation lowers to `copy_matrix_cc_to_gm`, the FIXPIPE operation, and normal Cube flow is M-to-FIX.

## Storage alignment and capacity

| Storage | Capacity | Alignment |
|---|---:|---:|
| UB | 192 KiB physical | 32 B |
| L1 | 512 KiB physical | 32 B |
| L0A/L0B | 64 KiB each | 512 B |
| L0C | 128 KiB | 64 B |
| BiasTable | 1 KiB | 64 B |
| Fixpipe Buffer | 2 KiB | 64 B |

Account for framework-reserved UB/L1 space as described in the architecture reference. Alignment is not a length or layout proof: also check the entire recurrence and final address.

## Vector-side transfers

Ordinary GM/UB forms:

```cpp
asc_copy_gm2ub(dst_ub, src_gm, size_bytes);
asc_copy_ub2gm(dst_gm, src_ub, size_bytes);

asc_copy_gm2ub(dst_ub, src_gm, n_burst, len_burst, src_gap, dst_gap);
asc_copy_ub2gm(dst_gm, src_ub, n_burst, len_burst, src_gap, dst_gap);
```

For high-dimensional ordinary forms, `len_burst`, `src_gap`, and `dst_gap` are 32-byte DataBlocks. Gaps are from the end of one burst to the start of the next:

```text
next_start = current_start + (len_burst + gap) * 32
```

The `_align` forms use mixed units:

| Direction | `len_burst` | GM gap | UB gap | Padding |
|---|---|---|---|---|
| GM→UB align | bytes | `src_gap` bytes | `dst_gap` DataBlocks | left/right element counts, each padded byte span <=32B |
| UB→GM align | bytes | `dst_gap` bytes | `src_gap` DataBlocks | left/right parameters are reserved and must be zero |

GM may be byte-aligned for `_align`; UB still requires 32-byte alignment. Validate only the requested GM bytes on UB→GM because hardware-added dummy bytes are discarded.

## Cube-side transfers

### GM to L1

`asc_copy_gm2l1(dst, src, size)` uses bytes. The high-dimensional raw form uses DataBlocks and end gaps, like ordinary GM→UB. The 2D overload processes 512-byte fractals:

```text
base_idx: source fractal ID, 512B units
repeat: number of iterations
src_stride: source start-to-start pitch, 512B units
dst_gap: gap after one destination fractal, 512B units
```

The pad1..pad8 families insert or remove fixed pieces within each 32-byte unit. Select the exact documented mode instead of emulating it with a guessed stride.

### GM ND to L1 NZ

`asc_copy_gm2l1_nd2nz` uses:

| Parameter | Unit |
|---|---|
| `nd_num`, `n_value`, `d_value` | counts/elements as named |
| `src_nd_matrix_stride`, `src_d_value` | elements |
| `dst_nz_c0_stride`, `dst_nz_n_stride` | C0 units, each 32 B |
| `dst_nz_matrix_stride` | elements |

For dense compact input, `src_d_value` is the logical/padded row pitch, not necessarily `d_value`. Write the resulting NZ recurrence before loading L0A/L0B.

### Direct GM to L0A/L0B

`asc_copy_gm2l0a/b` is a 2201 capability removed on 3510. It reads load-compatible 2D fractals directly from GM and does not provide the L1 transpose family. Its `base_idx`, `src_stride`, and `dst_gap` follow 512-byte-fractal semantics. Do not pass ordinary row-major ND data unless the selected overload/documented transformation explicitly supports it.

### L1 to L0A/L0B

The normal 2D 2201 overload is:

```text
dst, src, start_index, repeat, src_stride, dst_gap
```

Each normal iteration transfers one 512-byte fractal. `start_index` and `src_stride` are source-fractal units; `dst_gap` is a 512-byte end gap. The `_trans` family performs square-block transpose. For b16 it handles one 512-byte 16x16 block per repeat; for b8 and b32 it combines two 512-byte fractals into a 1024-byte square, transposes it, then splits it. Thus transpose `src_stride` is a square-block pitch, while destination gaps remain 512-byte-fractal based.

The same API names also have 3D/load3d overloads for convolution/im2col. Configure the L1 3D state and validate NC1HWC0 channel constraints; do not reuse 2D stride formulas.

### Auxiliary and reverse paths

`asc_copy_l12bt` and `asc_copy_l12fb` accept byte-size and high-dimensional DataBlock forms. Front-N byte copies require 32-byte size alignment. BiasTable uses a hardware address form; follow the public example's conversion instead of casting a pointer blindly.

`asc_copy_l12gm`, `asc_copy_l12l0c`, and their high-dimensional forms use DataBlock-style burst/gap parameters in the selected public overload. Confirm source/destination type conversion for L1→L0C; do not infer identical byte width from pointer types.

## Architecture exclusions

- Do not use 3510 ND-DMA setters for 2201 GM↔UB.
- Do not use 3510 GM→L1 DN2NZ, L1↔UB, L0C→UB, MX-scale loads, or NZ2DN paths on 2201.
- Do not use 3510 L1/L0 parameter meanings for a similarly named 2201 overload. Locate the implementation selected by `__NPU_ARCH__ == 2201`.
- The advanced `EnableL1BankConflictOptimise` option is documented as unsupported on A2/A3; do not enable it as a manual 2201 bank fix.

## Validation workflow

1. Record source/destination storage, pipeline, type, layout, bytes, and every parameter unit.
2. Derive each burst start and total range; validate alignment and capacity before compiling.
3. Use distinct nonconstant patterns per row/fractal. Seed destination holes so stray writes are observable.
4. Synchronize the producer to the path and the path to its consumer; add the reverse dependency before reuse.
5. Compare all defined destination bytes and ignore only explicitly undefined holes/padding.
6. For performance, sweep payload size and fit the large-size cycle slope. Keep setup, synchronization, and launch overhead outside the measured slope where possible.
7. Build and simulator artifacts belong under `/mnt/e`, not in the source or skill tree.
