# dav-2201 Cube MMAD and matrix layouts

## Storage structures

L1 is 512 KiB with 16 physical banks arranged as eight groups of two banks. Each bank has 1024 rows of 32 bytes:

```text
L1_ADDR[18:0] = {BANK[0], DEPTH[9:0], BG[2:0], WIDTH[4:0]}
```

One physical bank permits one read or one write per cycle. A group can use one bank for read and the paired bank for write, but does not support two reads or two writes in that group. The high-level `EnableL1BankConflictOptimise` switch is unsupported on A2/A3; if concurrent L1 streams matter, analyze their absolute addresses and validate a manual layout.

L0A and L0B are each 64 KiB, one bank with 128 rows of 512 bytes:

```text
L0A/L0B_ADDR[15:0] = {DEPTH[6:0], WIDTH[8:0]}
```

LoadData may write while MMAD reads a different address, but not the same address. Ping/pong L0 buffers therefore need disjoint ranges plus forward-ready and reverse-free dependencies.

L0C is 128 KiB with 16 banks, each 128 rows x 64 bytes:

```text
L0C_ADDR[16:0] = {DEPTH[6:0], BANK[3:0], WIDTH[5:0]}
```

L0C contains the accumulator and is consumed by FIXPIPE or can be reloaded/accumulated according to MMAD initialization controls.

## ND, NZ, ZZ, and ZN

For a logical matrix `[M,N]`, ND is ordinary row-major storage. Cube paths use 512-byte input fractals and a 1024-byte L0C output fractal for 32-bit accumulators.

L1 and L0C use FRACTAL_NZ at different fractal sizes. The generic transformation is:

```text
[M,N]
 -> pad to [M1*M0, N1*N0]
 -> reshape [M1,M0,N1,N0]
 -> transpose [N1,M1,M0,N0]
```

For L1 input NZ, `M0=16`, `N0=C0=32B/sizeof(T)`, so one fractal is 512 B. For L0C, the accumulator fractal is normally `16x16` elements and 1024 B for float/int32.

L0A expects FRACTAL_ZZ for logical A `[M,K]`:

```text
[M,K] -> [M1,K1,M0,K0]
M0=16, K0=32B/sizeof(T)
```

L0B expects FRACTAL_ZN for logical B `[K,N]`:

```text
[K,N] -> [K1,N1,N0,K0]
N0=16, K0=32B/sizeof(T)
```

Input-fractal shapes by element width:

| Width | L0A `M0xK0` | L0B `K0xN0` | Bytes/fractal |
|---:|---:|---:|---:|
| 4 bit | 16x64 | 64x16 | 512 |
| 8 bit | 16x32 | 32x16 | 512 |
| 16 bit | 16x16 | 16x16 | 512 |
| 32 bit | 16x8 | 8x16 | 512 |

For the common path with A and B supplied as ordinary logical matrices:

```text
A GM ND [M,K] -> L1 NZ [K1,M1,M0,K0]
              -> normal L1→L0A reorder -> L0A ZZ [M1,K1,M0,K0]

B GM ND [K,N] -> L1 NZ [N1,K1,K0,N0]
              -> transpose L1→L0B -> L0B ZN [K1,N1,N0,K0]
```

If B is already physically supplied as `[N,K]`, its L1 NZ organization can already match the ZN operand expected by MMAD; use the normal L0B load only after proving that physical equivalence. Choose normal/transpose from the layout actually present, not from the symbolic expression name alone.

## MMAD API and types

`asc_mmad` computes `C = A*B + C` on `PIPE_M`. For 2201, the principal public combinations are:

| A/B | L0C | API |
|---|---|---|
| half | float | `asc_mmad` |
| bfloat16 | float | `asc_mmad` |
| float/HF32 mode | float | `asc_mmad` plus mode setters |
| int8 | int32 | `asc_mmad` |
| int4 | int32 | `asc_mmad_s4` |
| int8 4:2 sparse path | int32 | `asc_mmad_sparse` plus sparse L0B load/index |

Do not use the 3510-only FP8/HiFloat8/MX overloads on 2201 merely because shared declarations are visible.

The primary dimensions are:

```text
left_height = M
n_dim       = K
right_width = N
```

Important controls:

- `c_matrix_init_val=true`: initialize C to zero before accumulation.
- `c_matrix_init_val=false, c_matrix_source=false`: accumulate from the existing L0C region.
- `c_matrix_init_val=false, c_matrix_source=true`: initialize from BiasTable.
- `unit_flag=2/3`: allow MMAD/FIXPIPE fractal-level overlap; use 3 for the last unit. It is incompatible with L0C accumulation.
- `k_direction_align`: controls architecture-specific K/N-aligned read behavior; retain it only after deriving the selected type's padded fractals.
- offset overloads expose feature/weight offset controls. Treat reserved fields as documented and test nonzero modes separately.

When any M/K/N dimension is zero, MMAD is a no-op. Compute buffer sizes from padded physical dimensions, not logical dimensions. Invalid padded A/B elements must be initialized to zero because MMAD can read complete aligned fractals even when FIXPIPE later suppresses invalid C output.

## Sparse 4:2

2201 supports the path removed on 3510. The sparse B data contains at most two nonzero values per group of four; extra nonzeros are not preserved. The generated two-bit indices must be in `{0,1,2}` and are used to select corresponding A elements. Load B and its index with `asc_copy_l12l0b_sparse`, then call `asc_mmad_sparse`.

Sparse requirements include 512-byte L0A/L0B alignment and 1024-byte L0C alignment for the documented path. Validate the compressed data and index independently before interpreting an MMAD mismatch.

## Cube pipeline checklist

1. Derive logical A/B/C and the physical layout at GM, L1, L0A, L0B, and L0C.
2. Derive `M0/K0/N0`, padded M/K/N, fractal counts, and exact bytes.
3. Select direct GM→L0 or staged GM→L1→L0 intentionally. Direct GM→L0 does not accept arbitrary ND input.
4. Select normal versus `_trans` separately for A and B from their physical L1 organization.
5. Initialize padded K data and all L0 fractals MMAD may read.
6. Select zero, BiasTable, or existing-L0C initialization explicitly.
7. Prove MTE2→MTE1→M→FIX ready dependencies and the reverse reuse dependencies.
8. Compare logical output and padded/tail behavior after FIXPIPE; use asymmetric, nonconstant A/B to expose accidental transposes.
9. Build and logs go under `/mnt/e`; do not use dav-3510-only examples as proof of dav-2201 behavior.
