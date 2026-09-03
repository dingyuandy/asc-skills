# dav-3510 L1 bank mapping and parallel bandwidth

## Architecture mapping

The public 3510 memory-structure documentation defines a 512 KiB L1 with 16 banks. Each bank is
32 KiB, arranged as 1024 rows of 32 bytes. Eight bank groups each contain two banks.

```text
L1_ADDR[18:0] = {bank_in_group[18], depth[17:8], BG[7:5], byte[4:0]}

byte          = addr & 0x1f
bg            = (addr >> 5)  & 0x7
depth         = (addr >> 8)  & 0x3ff
bank_in_group = (addr >> 18) & 0x1
physical_bank = bg + 8*bank_in_group
```

Consecutive 32-byte blocks rotate through BG0..BG7. After 256 bytes, `depth` increments. Toggling
bit 18, equivalently XORing `0x40000`, selects the paired bank while preserving byte, BG, and
depth. Calculate this from the absolute local address (`allocation base + tensor offset`), not a
tensor-relative offset unless the L1 allocation base is known to be zero.

One physical bank supports at most one read or one write at a time. The two banks in one bank
group support one read on one bank plus one write on the other; they do not support two reads or
two writes together.

## Allocation rule for concurrent streams

For equal-shape, equal-phase continuous streams, allocate the reader and writer at paired offsets:

```cpp
constexpr uint32_t L1_BANK_PAIR_DELTA = 256 * 1024;
uint32_t write_base = read_base ^ L1_BANK_PAIR_DELTA;
```

This is the reason the advanced Matmul L1-bank-conflict optimization separates concurrently used
buffers into the upper and lower 256 KiB halves. A 32-byte BG phase shift is harmless for full
256-byte-wide continuous streams because each cycle still covers every BG, but XORing bit 18 is
the deterministic layout rule.

## Verified concurrent bandwidth

The runnable dav-3510 simulator harness is:

```text
examples/02_simd_c_api/03_c_api/00_data_movement/
  l1_bank_parallel_microbenchmark
```

It times one common makespan for two AIV UB2L1 writers and AIC L12L0A+L12L0B readers. Five sizes
are each measured three times; the three largest points determine the slope. A real MMAD validates
the read path. Before L1 readback, both AIV UBs are overwritten with zero, so exact raw output
validates that UB2L1 actually reached L1.

Ascend950PR_9589 simulator results:

| Valid path | read B/system-cycle | write B/system-cycle | aggregate B/system-cycle |
|---|---:|---:|---:|
| L12L0A + L12L0B only | 256 | 0 | 256 |
| dual-AIV UB2L1 only | 0 | 256 | 256 |
| paired-bank concurrent | 256 | 256 | **512** |

All eight BG start phases and the inverse upper/lower-half direction produce correct results. The
measured paired-bank slopes range from 500.275 to 512 B/system-cycle because of approximately 1%
simulator counter-phase variation; the maximum and bank-structure limit are 512.

## Same-bank negative result

Do not treat a same-bank conflict as a safe serialized fallback. The simulator negative cases use
read/write bases `0x00000/0x10000`, `0x00000/0x20000`, `0x00000/0x30000`, and
`0x40000/0x60000`. They change depth but keep bit 18 equal. The MMAD read result remains correct,
but the entire concurrent UB2L1 write is lost and L1 readback is zero. The apparent 256 B/cycle
cycle slope is therefore invalid as usable bandwidth.

Treat this data-loss behavior as a simulator-verified correctness constraint. The address mapping
and port rules are documented architecture contracts; repeat the performance and corruption test
on physical hardware or a changed simulator before making a cross-version silicon claim.
