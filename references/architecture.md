# 双层架构、状态机与安全边界

## 一、为什么需要双层循环

单层编码 Agent 循环（「改代码 → 跑测试 → 红了就继续改或弱化断言 → 全绿即宣布完成」）在工程复杂度上升时存在四类系统性失效：

1. **目标迎合与测试弱化**：当实现遇到阻力时，单层循环倾向于放宽测试预期、跳过边界条件或静默格式化无关文件。
2. **检查盲区自满**：静态类型与单元测试全绿并不代表真实数据流正确（例如多步清洗流水线互相抵消、UI 异步选区竞态）。
3. **合同错与代码错混淆**：测试夹具假设错误时，执行者为了凑绿灯往往在生产代码里塞入破坏架构的特判。
4. **多会话并发踩踏**：多个 Agent 同时操作同一分支与端口，导致 `HEAD` 漂移与验证结果失真。

`dual-loop-dev` 将「单次受控实现」与「决定是否通过及如何迭代」彻底拆成内外两层、三种独立身份。

---

## 二、两层与三角色职责矩阵

| 角色 | 所在层 | 允许执行的操作 | 严禁执行的操作 |
|---|---|---|---|
| **Supervisor（监督者）** | 外层 | 诊断分期、冻结合同、`issue` 签发、被拒归因、预算内 `recycle`、提出规则修订草案 | 自审自己的规则提案、替 Executor 偷偷改源码补救 |
| **Reviewer（独立审阅者）** | 外层 | 独立复跑检查、冷读 Diff、`/tmp` 副本反向变异测试、填写 `observations.json`、执行 `accept / reject` | 修改被审仓库源码、调用 `execute / recycle`、直接采信 Executor 自述 |
| **Executor（执行者）** | 内层 | 在 `allowed_paths` 内完成一次实现、反复运行本地非破坏性自查、调用**恰好一次** `execute` 并如实上报取舍与疑点 | 二次调用 `execute`、修改合同/政策/探针、执行 `git commit / reset / checkout`、自封通过 |

---

## 三、单轮确定性状态机

```text
idle ──(supervisor: issue)──▶ issued ──(executor: execute once)──▶ running
                                ▲                                     │
                                │                                     ▼
                                │                              awaiting_review
                                │                                 │       │
        (supervisor: recycle    └────── rejected ◀──(reviewer)────┘       │
         + rejecting review_hash                                          │
         + explicit reason)                        accepted ◀─(reviewer)──┘
```

### 状态转移不变量

- **原子签发（Atomic Issue）**：`issue` 与 `recycle` 在临时目录完成合同、政策、`profile.json` 与合同引擎脚本字节的完整复制和离线自检后才原子重命名为 `rounds/NNN/`；校验失败不产生半轮残留、不消耗预算。
- **单次尝试消耗（Single-Attempt Consumption）**：只要进入 `execute`，无论越界预检失败、检查命令返回非零、超时（退出码 `124`）还是收到 `SIGINT/SIGTERM` 取消（退出码 `128+sig`），本轮尝试均已消耗并进入 `awaiting_review`。
- **候选源码封存（Candidate Sealing）**：第 1 项检查运行前生成 `candidate.json`（覆盖 Git 跟踪文件、子模块状态及未被 `.gitignore` 忽略的新增文件指纹；忽略构建产物如 `dist/` 允许生成，但已被 Git 跟踪的 vendor 文件仍受指纹锁定）。每项检查前后均重验源码快照必须恒等于 `candidate.json`。
- **进程组回收（Process-Group Isolation）**：每项检查在独立 POSIX 会话（`start_new_session=True`）中以 `shell=False` 运行，超时或取消时向整个进程组发送 `SIGTERM → SIGKILL`，防止残留孙进程延迟写盘污染工作树。
- **哈希链与自包含离线回放（Self-Contained Replay）**：`events.jsonl` 的每条事件包含前一事件 SHA-256；`rounds/NNN/` 封存当轮合同、政策、回执、反馈、证据与合同脚本副本。即便日后删除外部 `case_root` 或升级全局策略，`controller.py status` 仍可独立重放历史每一轮判决。

---

## 四、安全边界声明（必读）

> [!WARNING]
> **可信本地工作流门禁（Trusted-Local Workflow Guard）vs. 恶意进程沙箱**
>
> `dual-loop-dev` 的角色密钥分离、源码快照与哈希链旨在防止编码 Agent 在自动化工作流中发生**角色串用、静默自修、偷改测试或账本漂移**。
>
> 如果执行进程与控制器运行在**同一操作系统 UID** 下且拥有任意代码执行权限，恶意或失控脚本理论上仍可在文件系统层面直接读取仓外目录或绕过 CLI。因此：
> - 在本机半监督/可信 Agent 场景下，`orchestrator.py` 通过将单轮 `executor.key` 临时拷贝至独立 `worker-tokens/` 目录并在返回后立即销毁，避免工作代理误触 `authority/`；
> - 若面向**完全不可信的执行器或高危无人值守生产环境**，必须将 `control/`、`authority/` 与政策目录置于容器/VM 宿主机外层，仅向执行容器挂载只读任务单与受限可写代码工作树，并由宿主机外层在容器退出后对候选快照执行 `execute` 与独立审阅。
