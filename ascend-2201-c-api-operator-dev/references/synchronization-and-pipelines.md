# dav-2201 synchronization and pipelines

## Execution model

2201 operations are asynchronous within their hardware queues. Queue depths from the architecture guide are:

| Queue | AIC depth | AIV depth |
|---|---:|---:|
| Vector | 0 | 64 |
| Cube/M | 16 | 0 |
| MTE1 | 32 | 0 |
| MTE2 | 32 | 16 |
| MTE3 | 32 | 16 |
| FIX | 32 | 0 |

Same-pipeline issue order does not establish a dependency across different pipelines. Classify every edge as RAW, WAR, or WAW and protect both initial production and later buffer reuse.

## Public 2201 controls

- `asc_sync_notify(src_pipe, dst_pipe, event_id)` and `asc_sync_wait(...)`: same-core pipeline events.
- `asc_sync_pipe(pipe)` and `asc_sync()`: drain a selected pipeline or broader work when required.
- `_sync` API variants: issue the operation and apply architecture post-processing; convenient but unsuitable when explicit overlap is intended.
- `asc_sync_block_arrive/wait`, `asc_sync_subblock_arrive/wait`, and `asc_sync_inter_arrive/wait`: cross-core/cross-block flag mechanisms supported by the selected 2201 implementation.
- `asc_sync_data_barrier`: data barrier for its documented scope.

Do not use 3510-only `asc_lock/asc_unlock` mutexes or `asc_sync_intra_arrive/wait` on 2201. In the shared header those APIs are conditionally exposed only for `__NPU_ARCH__ == 3510`.

## Standard dependency chains

Vector:

```text
GM --MTE2--> UB --V--> UB --MTE3--> GM
ready: MTE2→V, V→MTE3
reuse: V→MTE2 for source overwrite; MTE3→V or MTE3→MTE2 for destination/source reuse
```

Cube:

```text
GM --MTE2--> L1 --MTE1--> L0A/L0B --M--> L0C --FIX--> GM/L1
ready: MTE2→MTE1, MTE1→M, M→FIX
reuse: MTE1→MTE2 for L1, M→MTE1 for L0A/B, FIX→M for L0C
```

For L0C→L1 followed by another L1→L0 load, add FIX→MTE1. The 2201 `asc_copy_l12gm` page assigns L1→GM to MTE1, so order its L1 producer/reuse against MTE1 rather than assuming the UB→GM MTE3 rule. Do not invent direct FIX→UB or UB→L1 paths on 2201.

## Ping/pong ownership

For each buffer instance, prove the full cycle:

```text
free -> producer writes -> ready -> consumer reads -> free
```

Double buffering needs two independent event lifetimes or a rigorously ordered reuse scheme. Seed only free ownership; do not pre-signal data-ready events. Ensure every set has exactly one matching wait before the same event is set again.

Event resources are limited. Do not manually use IDs known to be reserved by the framework, and do not assume the pipe pair creates a free namespace unless the API contract says so. Avoid consecutive sets without an intervening wait.

## AIC/AIV coordination

AIC and AIV are separate cores on 2201 and exchange ordinary intermediate data through GM. A typical handoff is:

```text
AIC: MMAD -> FIXPIPE L0C→GM -> cross-core arrive
AIV: cross-core wait -> MTE2 GM→UB -> Vector -> MTE3 UB→GM
```

The reverse direction similarly writes GM from AIV before AIC reads it. Match arrive/wait mode, flag ID, and participant count; mismatches usually surface as timeout rather than a compile error. A flag counter supports at most 15 outstanding arrivals, so long pipelines must consume tokens rather than accumulating indefinitely.

## Timing rules

- Drain or fence the measured producer before taking an end timestamp.
- Keep event setup and waits outside the target interval for isolated-engine throughput, but include them for end-to-end pipeline makespan.
- For overlapping engines, measure one common start-to-all-done interval and report aggregate bytes/operations.
- Disable trace prints and broad `PIPE_ALL` barriers during the final performance run; they can hide missing dependencies and distort overlap.
- Validate output after timing. A plausible cycle count does not prove the intended async work completed.
