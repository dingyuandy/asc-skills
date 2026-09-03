# dav-3510 UB banks and SIMD/FIXPIPE bandwidth

Use this reference for UB allocation, register-SIMD load/store placement, and concurrent FIXPIPE-write/SIMD-read/write experiments on Ascend 950 (`dav-3510`). Do not reuse the 2201/A2/A3 UB topology or its `+0x100`/`+0x10000` placement rules. For `dav-2201`, stop and use the dedicated `ascend-2201-c-api-operator-dev` skill.

## Physical mapping

The public 3510 optimization guide defines:

```text
UB_ADDR = {BANK_DEPTH[8:0], BANK[0], BG[2:0], BANK_WIDTH[4:0]}
```

For a byte address:

```text
byte_in_row  = addr[4:0]   = addr & 0x1f
bank_group   = addr[7:5]   = (addr >> 5) & 0x7
bank_in_group= addr[8]     = (addr >> 8) & 0x1
bank_depth   = addr[17:9]  = (addr >> 9) & 0x1ff
physical_bank= bank_group + 8 * bank_in_group
```

The physical UB is 256 KiB: 8 bank groups x 2 banks/group; each bank is 16 KiB = 512 rows x 32 B. Consecutive 32 B blocks rotate through BG0..BG7. Adding `0x100` keeps the group and toggles the paired bank. Adding `0x200` keeps group and bank and advances depth.

### First 512-byte address map

The first 512-byte UB row visits every physical bank exactly once. Each physical bank owns one contiguous 32-byte segment in that row:

| Decimal byte address | Hex byte address | `bank_group` | `bank_in_group` | Physical bank | `depth` |
|---:|---:|---:|---:|---:|---:|
| 0-31 | `0x000-0x01f` | 0 | 0 | 0 | 0 |
| 32-63 | `0x020-0x03f` | 1 | 0 | 1 | 0 |
| 64-95 | `0x040-0x05f` | 2 | 0 | 2 | 0 |
| 96-127 | `0x060-0x07f` | 3 | 0 | 3 | 0 |
| 128-159 | `0x080-0x09f` | 4 | 0 | 4 | 0 |
| 160-191 | `0x0a0-0x0bf` | 5 | 0 | 5 | 0 |
| 192-223 | `0x0c0-0x0df` | 6 | 0 | 6 | 0 |
| 224-255 | `0x0e0-0x0ff` | 7 | 0 | 7 | 0 |
| 256-287 | `0x100-0x11f` | 0 | 1 | 8 | 0 |
| 288-319 | `0x120-0x13f` | 1 | 1 | 9 | 0 |
| 320-351 | `0x140-0x15f` | 2 | 1 | 10 | 0 |
| 352-383 | `0x160-0x17f` | 3 | 1 | 11 | 0 |
| 384-415 | `0x180-0x19f` | 4 | 1 | 12 | 0 |
| 416-447 | `0x1a0-0x1bf` | 5 | 1 | 13 | 0 |
| 448-479 | `0x1c0-0x1df` | 6 | 1 | 14 | 0 |
| 480-511 | `0x1e0-0x1ff` | 7 | 1 | 15 | 0 |

The direct formulas are:

```text
physical_bank(addr) = ((addr >> 5) & 0x7) + 8 * ((addr >> 8) & 0x1)
depth(addr)         = (addr >> 9) & 0x1ff
```

Thus `0x000-0x0ff` selects physical banks 0-7, and `0x100-0x1ff` selects physical banks 8-15. Adding `0x200` advances to the next depth without changing the bank selected by the low nine address bits. For example, byte address 512 (`0x200`) maps back to `bank_group=0`, `bank_in_group=0`, physical bank 0, at `depth=1`.

Continuous loads/stores perceive banks. The four 8-byte subbanks in each row matter for gather/scatter, not ordinary continuous access.

The default CANN 9.1.0 3510 compilation budget is 248 KiB, not the full physical 256 KiB. The public architecture guide defines:

```text
ASC_UB_SIZE = 248 KiB + BISHENG_VF_STACK + ASC_UB_RESERVE
```

Enable the full physical 256 KiB with both target compilation options:

```text
--cce-disable-vf-stack-reserved-ubuf  # release the 6 KiB VF spill reservation
--cce-disable-asc-reserved-ubuf       # release the 2 KiB ASC API reservation
```

Then `ASC_UB_SIZE = 248 + 6 + 2 = 256 KiB`. A verified CMake form is:

```cmake
target_compile_options(kernel PRIVATE
    $<$<COMPILE_LANGUAGE:ASC>:--cce-disable-vf-stack-reserved-ubuf>
    $<$<COMPILE_LANGUAGE:ASC>:--cce-disable-asc-reserved-ubuf>)
```

This is an opt-in with real constraints, not a free capacity increase:

- the compiler cannot spill VREGs into the released 6 KiB, so keep the VF live set below the register limit and verify there is no spill;
- APIs marked as needing the ASC-reserved UB become unavailable when the 2 KiB option is enabled; raw C APIs used by the verified benchmark compile and run, but do not generalize that to every high-level API;
- SIMD+SIMT hybrid programming still has a smaller limit because of Data Cache; the 256 KiB result applies to the tested SIMD / Cube+Vector programming path.

The benchmark declares `__ubuf__ uint8_t ub[256*1024]` in both kernels and successfully compiles and runs in simulator mode with these options. The local official source is `docs/zh/guide/编程指南/高级编程/硬件实现/架构规格/NPU架构版本3510.md`; installed `sys_constants.h` independently expands the same `248 KiB + 6 KiB + 2 KiB` formula.

## Per-group port rules

Each bank accepts at most one read or one write per cycle. Each group accepts either:

```text
2 reads + 0 writes
1 read  + 1 write, provided they use different banks
```

Consequences:

- two reads to different banks in one group are conflict-free;
- read and write to the same bank conflict;
- read and write to paired banks in one group are conflict-free;
- two writes to one group conflict;
- more than two reads in one group conflict.

## Verified simulator slopes

CANN 9.1.0, `dav-3510`, Ascend950PR_9589 simulator, system-cycle slope over large payloads:

| Register-SIMD stream | Read B/cycle | Write B/cycle | Aggregate B/cycle |
|---|---:|---:|---:|
| aligned read only | 512 | 0 | 512 |
| aligned write only | 0 | 256 | 256 |
| one 256 B read + one 256 B write, paired banks | 256 | 256 | 512 |
| one 256 B read + one 256 B write, same bank | 128 | 128 | 256 |

These values match the port rules: an aligned 256 B vector spans all eight groups. Pure read can issue two such vectors per cycle using both banks; pure write can issue one; conflict-free mixed access consumes one read bank and one write bank per group.

The reproducible case and generated spec are:

```text
examples/02_simd_c_api/03_c_api/00_data_movement/
  ub_bank_simd_fixpipe_microbenchmark/
```

Treat simulator slopes as architectural specifications to validate on silicon with PMU/profiling when available.

## SIMD mixed-access placement

For same-rate aligned 256 B streams, use one 512 B physical row per logical step:

```cpp
read_addr(i)  = base + i * 0x200;
write_addr(i) = base + i * 0x200 + 0x100;
```

Both operations visit the same group at corresponding byte positions but opposite `bank_in_group` values. The ranges do not overlap.

A negative control must preserve group/bank while changing depth so it does not alias data:

```cpp
read_addr(i)  = base0 + i * 0x200;
write_addr(i) = base1 + i * 0x200;  // base1 differs in depth bits only
```

## FP32 FIXPIPE write plus SIMD read: eight-bank partition

The corrected concurrency probe keeps FIXPIPE FP32 end-to-end:

1. FP16 MMAD produces `__cc__ float` L0C.
2. `asc_copy_l0c2ub` receives `__ubuf__ float*` and `__cc__ float*`.
3. `QuantMode_t::NoQuant` is selected, `enable_nz2nd=true`, and no pre/post quantization or ReLU is enabled.
4. The complete FP32 UB result is compared with a mathematical golden.

This type check matters: INT32 L0C and FP32 L0C are both four bytes per element, so a cycle-only benchmark can look plausible while testing the wrong path.

Restrict FIXPIPE to the first eight physical banks with NZ2ND geometry:

```text
n_size     = 64 FP32 = 256 B
dst_stride = 128 FP32 = 512 B
FIX(i)     = ub + i*0x200 + 0x000   // bank_in_group=0
```

Each FIXPIPE row writes all eight groups in bank half 0, skips the 256-byte bank-half-1 hole, and starts the next row 512 bytes later. The observable SIMD read uses one of two non-aliasing layouts:

```text
other-bank: ub + i*0x200 + 0x100          // bank_in_group=1
same-bank:  ub + 0x20000 + i*0x200        // bank_in_group=0, different depth
```

Adding `0x20000` changes depth bits but preserves group and bank bits, so the negative control conflicts physically without reading FIXPIPE output bytes.

The continuous SIMD read control reaches 512 B/cycle when both bank halves are used. A stream confined to one eight-bank half reaches the expected 256 B/cycle only when the hot loop uses pointer post-update:

```cpp
__ubuf__ uint8_t* src = base + bank_offset;
asc_loadalign_postupdate(v, src, 512);  // stay on the same bank half, advance depth
```

Writing `base + bank_offset + i*512` separately for every unrolled load introduced enough scalar address-generation work to mismeasure this stream as 128 B/cycle. This was a benchmark artifact, not a bank limit. Since FP32 FIXPIPE is 128 B/cycle, balance the common makespan with `SIMD_read_bytes = 2 * FIX_write_bytes`.

The completed CANN 9.1.0 simulator test gives:

| Common-makespan case | SIMD read B/cycle | FP32 FIX write B/cycle | Aggregate B/cycle |
|---|---:|---:|---:|
| FP32 FIXPIPE, bank half 0 only | 0 | 128 | 128 |
| SIMD read, bank half 0 only | 256 | 0 | 256 |
| SIMD read, bank half 1 only | 256 | 0 | 256 |
| FIX bank0 + SIMD bank1 | 256 | 128 | **384** |
| FIX bank0 + SIMD bank0, different depth | 128 | 64 | **192** |

The separated layout is 2x faster; same-bank placement loses 50% relative to it. In the separated layout the two balanced streams fully overlap: `256R + 128W = 384 B/cycle`. The same-bank result matches full serialization: for read bytes `2W`, `2W/256 + W/128` cycles yields 192 B/cycle aggregate.

Every final FIX row is validated as 64 FP32 ones, every skipped bank1 hole is validated unchanged, and every timed SIMD load contributes to an exact XOR checksum. Report these as CANN 9.1.0 dav-3510 simulator slopes, not silicon PMU results.

Use `__mix__(1,1)`, a common AIC-side timer, and this handshake:

1. AIV initializes the read window and sends READY.
2. AIC consumes READY, timestamps, sends GO from `PIPE_FIX`, and issues FIXPIPE.
3. AIV consumes GO on `PIPE_MTE3`, synchronizes MTE3-to-V, issues observable SIMD reads, then synchronizes V-to-MTE3 and sends DONE.
4. AIC waits for DONE on `PIPE_FIX`, drains FIX, and timestamps the common makespan.

Version-pinned examples in this checkout use cross-block user flags `0x8` and `0x9`. Do not assume `0xA` is available merely because the parameter type is wide; the simulator can wait indefinitely. Reuse a consumed READY flag for DONE when two tokens are sufficient.

## Measurement pitfalls

- `asc_loadalign` is eliminable when every iteration overwrites the same VREG and only the last value is used. The false benchmark has almost constant cycles and absurd bandwidth. Fold every load into several independent XOR/add accumulators and store a final checksum, or otherwise make each load observable.
- Use at least eight independent accumulator chains so arithmetic dependency latency does not become the slope. Confirm the result against the port-rule prediction and inspect R².
- Count an aligned register load/store as 256 bytes regardless of element type; the VREG length is 256 B.
- Derive bandwidth from the large-payload slope, not `bytes / one elapsed point`; fixed event, timer, checksum, and launch overhead otherwise biases the result.
- Use absolute local addresses. Compiler placement of separate UB arrays can change low bank bits; one large UB allocation plus explicit byte offsets is easier to audit.
- Keep runtime case dispatch and scalar address mapping outside the per-load hot path. For fixed-bank stride, use `asc_loadalign_postupdate(..., 512)`; explicit per-load `i*512` address expressions falsely measured 128 B/cycle instead of 256 B/cycle.
- Fit standalone controls in the same `__mix__` kernel. Measure both fixed bank halves separately and require the FP32 FIX control to recover 128 B/cycle before interpreting concurrency.
- Keep FIXPIPE output and SIMD read ranges disjoint. The same-bank negative control must change depth while preserving group/bank bits; otherwise aliasing can corrupt the test.
- Balance the read and write bytes from the restricted-stream baselines, not from the 512 B/cycle whole-UB read ceiling.
- Validate complete output/checksum after the final timed trial. A timing curve alone does not prove that the intended instructions executed.
