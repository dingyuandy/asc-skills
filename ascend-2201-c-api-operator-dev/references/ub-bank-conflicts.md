# dav-2201 UB banks and SIMD conflict avoidance

## Physical mapping

The 2201 UB is 192 KiB: 48 physical banks, each with 128 rows of 32 bytes. The banks form 16 groups; group `g` contains banks `g`, `16+g`, and `32+g`.

For an absolute UB byte offset in `0x00000..0x2ffff`:

```text
byte_in_row   = addr[4:0]   = addr & 0x1f
bank_group    = addr[8:5]   = (addr >> 5) & 0xf
bank_row      = addr[15:9]  = (addr >> 9) & 0x7f
bank_in_group = addr[17:16] = addr >> 16       // 0, 1, 2
physical_bank = bank_group + 16 * bank_in_group
```

Address effects:

| Delta | dav-2201 effect |
|---:|---|
| `+0x20` | next bank group |
| `+0x100` | group phase +8, normally same physical-bank layer |
| `+0x200` | same group and physical bank, next row |
| `+0x4000` | same group and physical bank, row +32 |
| `+0x10000` | same group, next physical bank in the group |

This is incompatible with dav-3510: 3510 has eight groups and uses bit 8 to select one of two banks, so `+0x100` keeps the 3510 group unchanged.

## Per-repeat audit

For operand `p`, repeat `r`, and active DataBlock `b`:

```text
A_p(r,b) = base_p + 32 * (r * repeat_stride_p + b * block_stride_p)
G_p(r,b) = (A_p(r,b) >> 5) & 0xf
B_p(r,b) = G_p(r,b) + 16 * (A_p(r,b) >> 16)
```

Apply the active mask and check every repeat, especially ranges crossing `0x10000` or `0x20000`.

The 2201 conflict rules are:

1. Multiple reads to one bank group conflict, even if they target different physical banks in the group.
2. Multiple writes to one bank group conflict.
3. A read and a write conflict when they target the same physical bank. Same group plus different physical bank is usable concurrently.

For one operand, eight active blocks visit:

```text
G(b) = (base_group + b * block_stride) mod 16
```

The period is `16 / gcd(16, block_stride)`. It must be at least eight to avoid self-conflict among eight blocks:

| Block stride | Distinct groups | Minimum service cycles |
|---:|---:|---:|
| 1 | 8 | 1 |
| 2 | 8 | 1 |
| 4 | 4 | 2 |
| 8 | 2 | 4 |
| 16 | 1 | 8 |

## Continuous dual-source baseline

For two full-mask, `block_stride=1` sources, use complementary group phases:

```text
((src1 - src0) / 32) mod 16 = 8
(src1 - src0) mod 0x200 = 0x100
```

Place the destination in another 64 KiB physical-bank layer. The validated 16 KiB-per-operand layout is:

```text
src0 = 0x00000
src1 = 0x04100
dst  = 0x10000
```

With `block_stride=1` and `repeat_stride=8`, even repeats use source group halves 0..7 and 8..15; odd repeats swap them. The destination shares a group phase with src0 but uses banks 16..31 rather than banks 0..15. This removes both source/source RR and source/destination RW conflicts.

CANN 9.1.0 dav-2201 simulator controls for ADD/SUB/MAX/MIN, F16/F32/S16/S32:

| `src0/src1/dst` | Remaining conflict | cycles/repeat |
|---|---|---:|
| `0x0000/0x4000/0x8000` | RR + RW | 2.000 to 2.125 |
| `0x0000/0x4000/0x10000` | RR | 2.000 |
| `0x0000/0x4080/0x10000` | partial RR | 2.000 |
| `0x0000/0x4100/0xc000` | RW | 2.000 |
| `0x0000/0x4100/0x10000` | none | 1.000 |

Moving only the destination or only the second source is insufficient; eliminate both RR and RW contention.

The expanded CANN 9.1.0 simulator sweep covers264 bank cases: 256 cases across23 regular multi-source APIs and8 ADD self-conflict cases. Simple `VADD/VSUB/VMAX/VMIN/VAND/VOR` paths normally degrade from1 to2 cycles/repeat under a remaining RR or RW bottleneck. Compute-heavier instructions show partial masking: VDIV starts at2–4 cycles/repeat and doubles only for full/RR layouts, while VMLA/VMADD families rise from3–4 to5.5–6 under full conflict. Packed-output VCMPV and special VSEL/VREDUCE paths do not follow the full-width destination model exactly; use measured results rather than inferring from base addresses alone.

With `repeat=1, block_stride=16`, all eight active blocks of one operand remain in the same group. Both an ADD source-read self-conflict and destination-write self-conflict measure8 cycles/repeat for F16/F32/S16/S32, versus1 for the continuous conflict-free baseline. Full results are generated as `BANK_SWEEP.csv` and `BANK_SWEEP.md` beside the microbenchmark.

## Non-contiguous and irregular access

Do not mechanically add 256 bytes for every instruction:

- For `block_stride=2`, a base at group 0 visits even groups; a second source at group 1 visits odd groups, so a 32-byte shift is complementary.
- `block_stride=4` self-conflicts before another operand is considered. Change the computation order, tile layout, or leading dimension.
- For transpose and layout conversion, prefer a formulation that gives unique read groups. Padding a leading dimension by one DataBlock can break repeated-group patterns.
- For gather/scatter, enumerate addresses from the real index distribution. A conflict-free base cannot prove irregular accesses are conflict-free.
- For ping/pong, independently audit both buffer sets and any overlapping DMA traffic.

Use `scripts/audit_ub_banks.py` to enumerate regular operands. It intentionally exits nonzero when a conflict, misalignment, or out-of-range access is found.

Examples from the skill directory:

```bash
python3 scripts/audit_ub_banks.py \
  --read src0:0x0:1:8 --read src1:0x4100:1:8 \
  --write dst:0x10000:1:8 --repeats 64 --summary-only

python3 scripts/audit_ub_banks.py \
  --read src0:0x0:1:8 --read src1:0x4000:1:8 \
  --write dst:0x8000:1:8 --repeats 1
```

In addition to the bank proof, independently prove 32-byte alignment, non-overlap, total UB capacity, and API-specific overlap semantics.
