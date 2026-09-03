#!/usr/bin/env python3
"""Audit regular dav-2201 UB SIMD reads and writes for bank conflicts."""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass


UB_BYTES = 192 * 1024
DATA_BLOCK_BYTES = 32


@dataclass(frozen=True)
class Operand:
    name: str
    base: int
    block_stride: int
    repeat_stride: int
    access: str


def number(value: str) -> int:
    return int(value, 0)


def operand(value: str, access: str) -> Operand:
    parts = value.split(":")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError(
            "operand must be NAME:BASE:BLOCK_STRIDE:REPEAT_STRIDE")
    name, base, block_stride, repeat_stride = parts
    try:
        parsed = Operand(name, number(base), number(block_stride), number(repeat_stride), access)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error)) from error
    if not name:
        raise argparse.ArgumentTypeError("operand name must not be empty")
    if parsed.base < 0 or parsed.block_stride < 0 or parsed.repeat_stride < 0:
        raise argparse.ArgumentTypeError("base and strides must be nonnegative")
    return parsed


def address(op: Operand, repeat: int, block: int) -> int:
    return op.base + DATA_BLOCK_BYTES * (
        repeat * op.repeat_stride + block * op.block_stride)


def group(addr: int) -> int:
    return (addr >> 5) & 0xF


def physical_bank(addr: int) -> int:
    return group(addr) + 16 * (addr >> 16)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enumerate regular dav-2201 UB DataBlocks and report RR/WW/RW conflicts.")
    parser.add_argument("--read", action="append", default=[], metavar="NAME:BASE:BS:RS")
    parser.add_argument("--write", action="append", default=[], metavar="NAME:BASE:BS:RS")
    parser.add_argument("--repeats", type=number, default=1)
    parser.add_argument("--blocks", type=number, default=8,
                        help="active DataBlocks per repeat; default: 8")
    parser.add_argument("--summary-only", action="store_true")
    args = parser.parse_args()

    if args.repeats <= 0 or args.blocks <= 0 or args.blocks > 8:
        parser.error("repeats must be positive and blocks must be in 1..8")
    try:
        reads = [operand(item, "R") for item in args.read]
        writes = [operand(item, "W") for item in args.write]
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))
    if not reads and not writes:
        parser.error("provide at least one --read or --write operand")
    names = [op.name for op in reads + writes]
    if len(names) != len(set(names)):
        parser.error("operand names must be unique")

    invalid: list[str] = []
    conflicts: list[str] = []
    for op in reads + writes:
        if op.base % DATA_BLOCK_BYTES:
            invalid.append(f"{op.name}: base 0x{op.base:x} is not 32-byte aligned")

    for repeat in range(args.repeats):
        r_groups: dict[int, list[str]] = defaultdict(list)
        w_groups: dict[int, list[str]] = defaultdict(list)
        r_banks: dict[int, list[str]] = defaultdict(list)
        w_banks: dict[int, list[str]] = defaultdict(list)
        rows: list[str] = []
        for op in reads + writes:
            for block in range(args.blocks):
                addr = address(op, repeat, block)
                label = f"{op.name}[{block}]"
                if not 0 <= addr <= UB_BYTES - DATA_BLOCK_BYTES:
                    invalid.append(
                        f"repeat {repeat} {label}: 0x{addr:x} outside 192 KiB UB")
                    continue
                bank_group = group(addr)
                bank = physical_bank(addr)
                rows.append(
                    f"r={repeat:3d} {op.access} {label:16s} addr=0x{addr:05x} "
                    f"group={bank_group:2d} bank={bank:2d}")
                target_groups = r_groups if op.access == "R" else w_groups
                target_banks = r_banks if op.access == "R" else w_banks
                target_groups[bank_group].append(label)
                target_banks[bank].append(label)

        for bank_group, labels in sorted(r_groups.items()):
            if len(labels) > 1:
                conflicts.append(
                    f"repeat {repeat} RR group {bank_group}: {', '.join(labels)}")
        for bank_group, labels in sorted(w_groups.items()):
            if len(labels) > 1:
                conflicts.append(
                    f"repeat {repeat} WW group {bank_group}: {', '.join(labels)}")
        for bank in sorted(r_banks.keys() & w_banks.keys()):
            conflicts.append(
                f"repeat {repeat} RW bank {bank}: "
                f"reads={','.join(r_banks[bank])} writes={','.join(w_banks[bank])}")
        if not args.summary_only:
            print("\n".join(rows))

    unique_invalid = list(dict.fromkeys(invalid))
    unique_conflicts = list(dict.fromkeys(conflicts))
    print(f"operands={len(reads) + len(writes)} repeats={args.repeats} "
          f"blocks/repeat={args.blocks}")
    if unique_invalid:
        print("INVALID:")
        print("\n".join(f"  {item}" for item in unique_invalid))
    if unique_conflicts:
        print("CONFLICTS:")
        print("\n".join(f"  {item}" for item in unique_conflicts))
    if not unique_invalid and not unique_conflicts:
        print("conflict-free")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
