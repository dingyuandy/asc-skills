# dav-3510 kernel 同步方法

## 目录

- 同步层级与接口选择
- 核内流水同步
- 常见 pipe 依赖图
- AIC/AIV 核间同步
- 正向与反向依赖
- Double buffer 标准协议
- 初始化、收尾与 ID 复用
- 调试检查表

## 同步层级与接口选择

本文只使用 kernel 中采用的底层接口：

| 范围 | Kernel API | 用途 |
|---|---|---|
| 同一核、同一流水 | `pipe_barrier(PIPE_*)` | 等待该流水此前指令完成 |
| 同一核、不同流水 | `set_flag(src_pipe, dst_pipe, id)` / `wait_flag(...)` | 在 MTE2、V、MTE3、MTE1、M、FIX 之间建立依赖 |
| 同一 mixed block 内 AIC↔AIV | `set_intra_block(pipe, id)` / `wait_intra_block(pipe, id)` | 数据 ready 与 buffer release 握手 |
| 同一 AIV 内 VEC store→load | `LocalMemBar<store_type, load_type>()` | UB 上的寄存器访存可见性 |

这里的“核间同步”指一个 mixed block 内 AIC 与 AIV0/AIV1 的同步，正是
`set_intra_block/wait_intra_block` 覆盖的范围。它不能同步不同
`get_block_idx()` 的 mixed block。不同 block 的 FFTS 同步需要另一套消息
与 `wait_flag_dev` 协议；如果当前 kernel 没有使用，不要用
`set_intra_block` 代替或臆造目标消息。

`pipe_barrier(PIPE_ALL)` 只排空调用它的 AIC 或 AIV 的本地流水，不是 AIC/AIV
barrier，更不是全 block barrier。优先使用精确的 pipe event；`PIPE_ALL`
会破坏流水重叠。

## 核内流水同步

### 常见 pipe 依赖图

先按数据实际经过的存储与执行流水建立事件，不要在不共享数据生命周期的 pipe
之间添加“保险同步”。常见计算流水是：

```text
AIC: MTE2 ⇄ MTE1 ⇄ M
                    M ⇄ FIX

AIV: MTE2 ⇄ V ⇄ MTE3
```

双向箭头不是一对重复 barrier，而是两个相反的所有权事件：正向箭头传递
`ready`，反向箭头归还 `free/release`。

### AIC 常见 pipe 对

| Pipe 对 | 正向依赖 | 反向依赖 | 典型 buffer |
|---|---|---|---|
| `MTE2 ⇄ MTE1` | `MTE2→MTE1`：GM→L1 完成后才能 L1→L0 | `MTE1→MTE2`：L1 数据已装入 L0 后才能覆写对应 L1 bank | `q_l1`、`kv_l1`、其他 L1 ping-pong |
| `MTE1 ⇄ M` | `MTE1→M`：L0A/L0B 装载完成后才能 MMAD | `M→MTE1`：MMAD 已读完 L0A/L0B 后才能重装对应 L0 bank | `a0[]`、`b0[]` |
| `M ⇄ FIX` | `M→FIX`：MMAD 写完 L0C 后 FIX 才能搬出 | `FIX→M`：FIX 读完 L0C 后 M 才能覆写对应 L0C bank | `c0[]` |

对应的 double-buffer 骨架如下：

```cpp
// L1 bank b：MTE1→MTE2 是初始 free。
set_flag(PIPE_MTE1, PIPE_MTE2, b);
wait_flag(PIPE_MTE1, PIPE_MTE2, b);
gm_to_l1(l1[b], ...);                         // MTE2
set_flag(PIPE_MTE2, PIPE_MTE1, b);
wait_flag(PIPE_MTE2, PIPE_MTE1, b);
l1_to_l0(l0[b], l1[b], ...);                  // MTE1
set_flag(PIPE_MTE1, PIPE_MTE2, b);             // 归还 L1

// L0A/B bank b：M→MTE1 是初始 free。
set_flag(PIPE_M, PIPE_MTE1, b);
wait_flag(PIPE_M, PIPE_MTE1, b);
l1_to_l0(l0ab[b], ...);                        // MTE1
set_flag(PIPE_MTE1, PIPE_M, b);
wait_flag(PIPE_MTE1, PIPE_M, b);
mmad(l0c[b], l0ab[b], ...);                    // M
set_flag(PIPE_M, PIPE_MTE1, b);                // 归还 L0A/B

// L0C bank b：FIX→M 是初始 free。
set_flag(PIPE_FIX, PIPE_M, b);
wait_flag(PIPE_FIX, PIPE_M, b);
mmad(l0c[b], ...);                             // M
set_flag(PIPE_M, PIPE_FIX, b);
wait_flag(PIPE_M, PIPE_FIX, b);
fixpipe_out(dst, l0c[b], ...);                 // FIX
set_flag(PIPE_FIX, PIPE_M, b);                 // 归还 L0C
```

同一段真实代码中，prologue 的 free token 应在循环外只预置一次；上面分段展示的
目的是强调每个 buffer 的闭环。

`FIX` 与 `MTE2` 在这条 AIC 数据链上没有直接生产者/消费者关系，不要创建
`set_flag(PIPE_FIX, PIPE_MTE2, ...)` 或反向事件。若观察到二者似乎相关，应沿
实际 buffer 追踪中间的 `MTE1`、`M` 或 GM/L1 生命周期，而不是跨级补一条事件。

### AIV 常见 pipe 对

| Pipe 对 | 正向依赖 | 反向依赖 | 典型 buffer |
|---|---|---|---|
| `MTE2 ⇄ V` | `MTE2→V`：GM→UB 完成后 V 才能计算 | `V→MTE2`：V 最后一次访问结束后 MTE2 才能覆写 UB | 输入/中间 UB ping-pong |
| `V ⇄ MTE3` | `V→MTE3`：V 写完 UB 后 MTE3 才能 UB→GM/L1 | `MTE3→V`：MTE3 读完 UB 后 V 才能覆写或复用 | 输出 UB ping-pong |

标准三阶段环是：

```text
MTE2 写 UB → set MTE2→V ready
V 等 ready、计算 → set V→MTE3 ready
MTE3 等 ready、搬出 → set MTE3→MTE2 free
下一轮 MTE2 等 free 后覆写同一 bank
```

如果 V 在 MTE3 搬出完成后还会继续覆写同一 bank，则用 `MTE3→V` 归还；如果
下一位 owner 是 MTE2，则应把最终 free 直接返回 `MTE3→MTE2`。反向事件的目标
由“谁下一次写这个 bank”决定，而不是机械地总返回给前一条 pipe。

### 同一流水

同一异步流水对重叠地址进行连续操作时，用 `pipe_barrier` 保证前一操作结束：

```cpp
copy_gm_to_ubuf(ub, gm0, ...);  // PIPE_MTE2
pipe_barrier(PIPE_MTE2);
copy_gm_to_ubuf(ub, gm1, ...);  // 覆写相同 UB
```

仅当确实需要排空所有本地流水时才使用：

```cpp
pipe_barrier(PIPE_ALL);
```

### 不同流水

`set_flag(A, B, id)` 表示 A 流水完成此前工作后通知 B；
`wait_flag(A, B, id)` 阻塞 B，直到对应事件到达：

```cpp
copy_gm_to_ubuf(ub, gm, ...);       // MTE2 写 UB
set_flag(PIPE_MTE2, PIPE_V, 0);
wait_flag(PIPE_MTE2, PIPE_V, 0);    // V 等待数据 ready
vector_compute(ub);

set_flag(PIPE_V, PIPE_MTE3, 0);
wait_flag(PIPE_V, PIPE_MTE3, 0);    // MTE3 等待 V 写完 UB
copy_ubuf_to_gm(out, ub, ...);
```

dav-3510 的本地 event ID 范围按 `[0,7]` 管理。相同
`(src_pipe, dst_pipe, id)` 的 `set_flag` 与 `wait_flag` 必须配对；前一个
事件尚未被 wait 消耗时，不要再次 set 同一事件。

### VEC store→load

当 VEC 刚写完一个 UB 地址又立即从同一地址读取时，DMA event 或 unaligned
store flush 不等价于 VEC 内存依赖。显式使用对应的 `LocalMemBar`：

```cpp
AscendC::MicroAPI::LocalMemBar<
    AscendC::MicroAPI::MemType::VEC_STORE,
    AscendC::MicroAPI::MemType::VEC_LOAD>();
```

## AIC/AIV 核间同步

`set_intra_block` 是 arrive：其他 Core 对应 ID 的计数器加一。
`wait_intra_block` 在计数为零时阻塞；计数非零时减一并继续。必须按 token
计数，而不能把它当成一次性布尔 flag。

### `__mix__(1,2)` 的 ID 映射

每个 AIV 使用本地 ID `[0,15]`，AIC 使用 `[0,31]`。对逻辑 ID `x`：

| 方向 | 调用方式 |
|---|---|
| AIV→AIC | AIV0、AIV1 各执行 `set_intra_block(pipe, x)`；AIC 分别执行 `wait_intra_block(pipe, x)` 和 `wait_intra_block(pipe, 16+x)` |
| AIC→AIV | AIC 分别执行 `set_intra_block(pipe, x)` 和 `set_intra_block(pipe, 16+x)`；AIV0、AIV1 各执行 `wait_intra_block(pipe, x)` |

AIV0 的本地 `x` 映射到 AIC 的 `x`，AIV1 的本地 `x` 映射到 AIC 的
`16+x`。因此 AIC 上的两次 wait/arrive 是面向两个 AIV 的独立握手，不是
“两道全局屏障”。

`pipe` 参数只决定 arrive/wait 与哪条流水的先后关系，不给 ID 增加命名空间。
不要认为 `(pipe, direction, id)` 各自独立。保守规则是：所有同时存活的
语义依赖，即使方向相反，也分配不同逻辑 ID。

计数器是 4 bit；每个目标 Core 上 arrive 与 wait 的数量必须守恒，任何 ID
都不能累计超过 15。缺少 arrive 会死锁，多余 arrive 会残留到下一段生命周期，
导致 wait 被错误提前满足。

### 常见跨核 pipe 对

#### AIC FIX 生产，AIV V 消费

FIX 将 L0C 结果写到 AIV UB 后，由 AIV 的 V 流水消费。正向 ready 和反向
release 必须分开：

```text
AIC FIX：写 AIV UB
AIC FIX：set_intra_block(PIPE_FIX, ready)
         set_intra_block(PIPE_FIX, 16 + ready)

AIV V： wait_intra_block(PIPE_V, ready)
AIV V： 消费 UB
AIV V： set_intra_block(PIPE_V, release)

AIC FIX 下次覆写前：
         wait_intra_block(PIPE_FIX, release)
         wait_intra_block(PIPE_FIX, 16 + release)
```

因此常用关系是 `AIV PIPE_V waits AIC PIPE_FIX`；反方向由 V 向 FIX 归还 UB
所有权。`ready` 与 `release` 默认使用不同逻辑 ID。

#### AIV MTE3 生产，AIC MTE1 消费

AIV 通过 UB→L1 把数据写入共享 L1，AIC 的 MTE1 从 L1 装入 L0：

```text
AIV MTE3：等待 free，执行 UB→L1
AIV MTE3：set_intra_block(PIPE_MTE3, ready)

AIC MTE1：wait_intra_block(PIPE_MTE1, ready)
          wait_intra_block(PIPE_MTE1, 16 + ready)
AIC MTE1：从完整 L1 bank 装入 L0
AIC MTE1：set_intra_block(PIPE_MTE1, free)
          set_intra_block(PIPE_MTE1, 16 + free)

AIV MTE3：wait_intra_block(PIPE_MTE3, free)，然后才能覆写该 L1 bank
```

因此常用关系是 `AIC PIPE_MTE1 waits AIV PIPE_MTE3`；反方向由 MTE1 向
MTE3 归还 L1 所有权。若两个 AIV 各生产一半数据，AIC 必须同时等待 `ready`
和 `16+ready`，不能只等其中一个。

## 正向与反向依赖

一个可复用 buffer 必须同时解决两个方向：

| 依赖 | 含义 | 同步边 |
|---|---|---|
| RAW，正向 ready | 消费者不能在生产者写完前读取 | producer write → ready → consumer read |
| WAR，反向 free/release | 下一次生产不能在上一次消费结束前覆写 | consumer last read → free → next producer write |
| WAW | 后一个写者不能在前一个重叠写完成前启动 | previous write done/free → next write |

只有正向 ready 事件并不构成完整 double buffer。它只能证明“可以读”，不能证明
“可以再次写”。反向 free 事件才是生产者的背压。

如果一个 bank 有多个异步消费者，生产者必须等所有消费者释放。最安全的做法是
为独立消费者分配不同 release ID；也可以等待精确数量的 token，但必须证明 token
不会被另一个 buffer 生命周期消费。

## Double buffer 标准协议

每个 bank `b` 保持同一所有权不变量：

```text
初始：free[b] = 1，ready[b] = 0

producer：wait free[b] → 写 bank b → set ready[b]
consumer：wait ready[b] → 读 bank b → set free[b]
```

只预置 free，不预置 ready。预置 ready 会允许消费者读取未初始化数据。

### 同一核：MTE2→V double buffer

本地 event 的 source/target pipe 已经区分正向和反向，可为两个 bank 使用 ID 0/1：

```cpp
// Prologue：两个 bank 初始都可由 MTE2 写入。
set_flag(PIPE_V, PIPE_MTE2, 0);
set_flag(PIPE_V, PIPE_MTE2, 1);

for (uint64_t i = 0; i < tile_count; ++i) {
    uint64_t b = i & 1;

    // 反向 WAR：V 已释放 bank，MTE2 才能覆写。
    wait_flag(PIPE_V, PIPE_MTE2, b);
    copy_gm_to_ubuf(ub[b], src(i), ...);

    // 正向 RAW：MTE2 写完，V 才能读取。
    set_flag(PIPE_MTE2, PIPE_V, b);
    wait_flag(PIPE_MTE2, PIPE_V, b);
    vector_compute(ub[b]);

    // V 最后一次读/写完成后归还 bank。
    set_flag(PIPE_V, PIPE_MTE2, b);
}

// Epilogue：消费最后的 free token，也处理从未使用的预置 bank。
wait_flag(PIPE_V, PIPE_MTE2, 0);
wait_flag(PIPE_V, PIPE_MTE2, 1);
```

如果 MTE3 是 bank 的最后消费者，free 必须从 MTE3 返回，而不是 V：

```text
MTE2 写 → MTE2→V ready
V 计算 → V→MTE3 ready
MTE3 读/搬出 → MTE3→MTE2 free
```

下一轮 MTE2 覆写前等待 `wait_flag(PIPE_MTE3, PIPE_MTE2, b)`。若 V 和 MTE3
是并行且互不排序的读者，则必须分别等两者释放。

### AIV 生产、AIC 消费的跨核 double buffer

为每个 bank 分配不同的 `ready[b]` 和 `free[b]` ID。下面假设两个 AIV 各写
共享 L1 bank 的一半，AIC 读取完整 bank：

```text
Prologue，对每个 bank b：
  AIC set free[b] 和 16+free[b]，把 bank 归给 AIV0/AIV1

每个 AIV，第 i 个 tile 使用 bank b：
  wait_intra_block(PIPE_MTE3, free[b])
  MTE3 写本 AIV 对应的 L1 区域
  set_intra_block(PIPE_MTE3, ready[b])

AIC，第 i 个 tile 使用 bank b：
  wait_intra_block(PIPE_MTE1, ready[b])
  wait_intra_block(PIPE_MTE1, 16 + ready[b])
  MTE1 从 L1 读入 L0
  set_intra_block(PIPE_MTE1, free[b])
  set_intra_block(PIPE_MTE1, 16 + free[b])
```

这里有两套独立握手：

- 正向：AIV `PIPE_MTE3` → ready → AIC `PIPE_MTE1`；
- 反向：AIC `PIPE_MTE1` → free → AIV `PIPE_MTE3`。

如果改为 AIC/FIX 生产、AIV/V 消费，交换生产者/消费者角色，但仍保持同一不变量：
AIC 覆写前等两个 AIV 的 release；写完后对 `x`、`16+x` 各 set ready；每个
AIV 等本地 `x`，消费结束后 set release。

ready/free 分方向、分 bank 使用不同 ID 是默认安全方案。只有在证明前一 ID 的
最后一个 wait 已完成、计数器已归零、生命周期完全不重叠后，才能复用 ID。

## 初始化、收尾与 ID 复用

### Prologue

- 每个初始空闲 bank 预置一个 free token。
- `__mix__(1,2)` 中 AIC→AIV 的 free 要分别 set `x` 和 `16+x`。
- 不预置 ready。
- 如果第一次写本来就是无条件的，不要额外预置 release；多余 token 可能残留并
  提前放行后续覆写。

### Steady state

- bank index、ready ID、free ID 必须从同一个 ring index 推导。
- set 必须挂在真正的最后写/最后读流水上。
- 不依赖连续 `set_intra_block` 的源码顺序；硬件不保证连续 arrive 的执行顺序。
  对顺序敏感的独立依赖使用不同 ID。

### Drain

- 停止生产后，消费所有已经 ready 的 bank。
- 消耗或回收所有预置和未决 free token。
- 最后一次 MTE3/FIX 等异步访问完成前，不得宣布 UB/L1 bank free。
- `pipe_barrier(PIPE_ALL)` 只能补足本核 drain 的可见性，不能代替缺失的
  `set_intra_block/wait_intra_block`。

### ID live range

为每个同步 ID 建表：语义、方向、producer pipe、consumer pipe、bank、首次 set、
最后 wait、每轮 token 数。只有 live range 不重叠且旧计数器确定为零时才能复用。
把逻辑 `[0,15]` 当成稀缺资源；AIC 的 `16+x` 只用于映射 AIV1，不是额外的
独立逻辑 ID。

## 调试检查表

- 为每个复用 bank 画出 `free → write → ready → read → free` 闭环。
- AIC 只建立实际存在的 `MTE2⇄MTE1`、`MTE1⇄M`、`M⇄FIX` 常见依赖；不要添加 `FIX⇄MTE2` 事件。
- AIV 检查 `MTE2⇄V` 与 `V⇄MTE3`，并把最终 free 返回给下一位 writer。
- 跨核检查 `FIX→AIV V` ready/release 和 `AIV MTE3→AIC MTE1` ready/free 两套闭环。
- 检查正向 RAW 和反向 WAR 是否都存在。
- 检查 set 是否位于真正的最后生产/消费 pipe。
- 分别统计 prologue、steady state、drain 的 set/wait 数量。
- `__mix__(1,2)` 中检查 AIC 的 `x/16+x` 与两个 AIV 的本地 `x` 映射。
- 检查 4-bit intra 计数器不会超过 15。
- 检查同一 `(src pipe, dst pipe, event id)` 不会连续 set 两次而未 wait。
- 关闭 trace 再测；打印与 `PIPE_ALL` 调试屏障会掩盖竞争。
- 每个 bank/tile 使用不同随机值；常量数据会掩盖 stale read。
- 覆盖 1 tile、2 tile、首次 wraparound、长循环、两个 AIV 和缩短 drain。
- 全量比较逻辑输出，并检查 simulator 的同步异常、NaN、Inf 与死锁。

## 参考实现位置

- Kernel 内实际用法：若当前工作区包含 `dsa` 项目，查看其中的 `aic.cce` 和 `aiv.cce`。
- dav-3510 intra 映射测试：`tests/api/basic_api/ascendc_case_ascend950pr_9599/ascendc_case_ascend950pr_9599_aiv_framework/test_intra_block.cpp`
- 编译器通道映射：`cmake/asc/legacy_modules/util/channel.py`
