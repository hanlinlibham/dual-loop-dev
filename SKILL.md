---
name: dual-loop-dev
description: 双层循环受监督自动化开发框架与技能：覆盖并发占用预检、证据诊断与分期、轻中重三档(L1/L2/L3)、冻结合同、内层单次执行、外层冷审阅与反向变异验证、被拒三分类归因、Git Worktree隔离编排与无人值守多任务队列。用于需要高可靠性推进复杂代码重构、多模块功能迭代或夜间无人值守路线图开发时；一次性简单问答无需启用。
---

# Dual-Loop Supervised Automated Development (`dual-loop-dev`)

将 AI 自动编程拆分为**内层单次受控实现（Executor）**与**外层冻结合同 + 独立对抗审阅 + 预算内重签发（Supervisor + Cold Reviewer）**两层闭环。任何一层在失败后均不得自行静默修补重跑。

本目录既是零第三方依赖的 Python CLI 框架（`scripts/`），又是可直接挂载到 Claude Code、Cursor、Codex、Antigravity 的通用开发技能。设 `DL` 为本目录绝对路径。

---

## 0. 何时启用与如何选档

- **不启用**：只读排查、单文件几行文案微调或一次性问答，直接完成即可。
- **启用并选档**（拿不准时上调一档，详见 [references/workflow-tiers.md](references/workflow-tiers.md)）：
  - **L1 轻量**：$\le 3$ 个文件、无外部行为或协议变化 → 冻结合同 + 单次 `execute` + 审阅者核对白名单 Diff 并复跑检查。
  - **L2 标准**：常规功能开发、Bug 修复或局部重构 → 完整双层闭环 + 隔离环境现场验证 + 反向验证。
  - **L3 重型**：跨模块重构、公共契约/模型可见面变化、并发或状态机改动 → L2 基础上增加**逐字节行为等价探针** + **全新冷审阅者** + **`/tmp` 副本变异测试**（删守卫确认测试变红）。

---

## 1. 动手前：并发占用预检与 Worktree 隔离

在共享仓库做任何 Git 写操作或重启共享开发端口前，先运行只读预检：

```sh
python3 "$DL/scripts/preflight.py" --repo <仓库根> --workspace <工作区根>
```

- 若返回 `OCCUPIED`（存在进行中的 `issued/running/awaiting_review` 轮次或 `LOCK`）：严禁在主工作树移动分支或重启共享服务，切换至独立 Git Worktree + 独立端口：

```sh
python3 "$DL/scripts/worktree.py" create --repo <仓库根> --task-id <任务ID> --port-range 3200-3299
```

---

## 2. 项目解耦配置（`profile.json`）

每个被管仓库通过一份只读 `profile.json`（Schema 见 [schemas/profile.schema.json](schemas/profile.schema.json)）声明受保护路径与预检端口，控制器不硬编码任何业务路径：

```json
{
  "schema_version": 1,
  "name": "my-service",
  "protected_paths": ["core/", "migrations/"],
  "require_clean_worktree": true,
  "preflight": { "port": 3000 },
  "docs": ["ARCHITECTURE.md"]
}
```

---

## 3. 三种运行方式

### 方式 A：宿主 Agent 驱动子代理（在当前会话内调度 Executor 与 Reviewer）

1. **初始化控制目录与三角色令牌**（必须在被测代码仓外部）：
   ```sh
   python3 "$DL/scripts/controller.py" init --root "$CONTROL" --authority "$AUTH"
   ```
2. **创建并冻结本轮证据合同**（默认使用内置 `contract.py`，亦兼容外部 TaskKit `contract.py`）：
   ```sh
   python3 "$DL/scripts/contract.py" init-case --root "$CASE" --topic <kebab-id> \
     --question '<本轮单一目标>' --criteria-json criteria.json
   python3 "$DL/scripts/contract.py" freeze --root "$CASE"
   ```
   > 冻结前每一条验收项必须是 `required` 或附明确依据的 `not_applicable`；严禁残留 `investigate`。
3. **签发本轮清单（Supervisor）**：
   ```sh
   python3 "$DL/scripts/controller.py" issue --root "$CONTROL" \
     --token "$AUTH/supervisor.key" --manifest "$MANIFEST"
   ```
4. **派发内层执行子代理（Executor）**：按 [references/brief-templates.md](references/brief-templates.md) 下发任务书与仅限 `executor.key` 的临时拷贝。执行者完成编码与自查后**仅调用一次**：
   ```sh
   python3 "$DL/scripts/controller.py" execute --root "$CONTROL" --token "$EXEC_KEY"
   ```
5. **派发全新独立审阅子代理（Reviewer）**：不复用执行者上下文。审阅者用 `git diff <基线HEAD>` 核对范围、复跑检查、在 `/tmp` 副本做反向变异验证，写入 `outputs/observations.json` 后对账并裁决：
   ```sh
   python3 "$DL/scripts/contract.py" compare --root "$CASE" \
     --observations outputs/observations.json --out outputs/review-comparison.json
   python3 "$DL/scripts/controller.py" review --root "$CONTROL" --token "$REV_KEY" \
     --verdict accept --feedback feedback.md --report outputs/review-comparison.json
   ```
6. **被拒后先归因再重签（Supervisor）**：
   - **代码错（`ATTRIBUTION: code`）**：保持标准不变，建新 case，凭 `review_hash` 执行 `recycle`；
   - **合同错（`ATTRIBUTION: contract`）**：候选源码逐字节不动，仅修合同/白名单后重签；
   - **环境/并发漂移（`ATTRIBUTION: environment`）**：切换隔离 Worktree 在新基线重签；
   - `max_rounds`（默认 3）耗尽即停止并向用户汇报。

### 方式 B：单任务自动编排器（CLI 自动拉起 `claude` / `codex` / 自定义后端）

```sh
python3 "$DL/scripts/orchestrator.py" --task-config task.json --state-dir "$STATE_DIR"
```

### 方式 C：多任务 DAG 路线图与夜间无人值守守护进程

```sh
# 单次跑通整个 DAG 路线图（默认自动创建隔离 Git Worktree）
python3 "$DL/scripts/roadmap.py" run --roadmap roadmap.json --state-dir "$STATE_DIR"

# 守护进程模式：持续监听 roadmap.json 新增任务，带 PID 锁、心跳与优雅停机
python3 "$DL/scripts/roadmap.py" daemon --roadmap roadmap.json --state-dir "$STATE_DIR" --poll-interval 15
```

---

## 4. 本地提交、合入与收尾

- `accept` 后在本地（或隔离 Worktree）提交，提交消息末行固定附审计尾注：
  `Accepted in supervised round <case> (review <review_hash_8>)`
- 将隔离 Worktree 合入主分支（自动校验每个提交均带合法审阅尾注）：
  ```sh
  python3 "$DL/scripts/worktree.py" integrate --repo <主仓库> --worktree <隔离Worktree>
  ```
- 收尾汇总数字一律用脚本从 Git 实算，禁止凭记忆手写：
  ```sh
  python3 "$DL/scripts/summary.py" --repo <仓库> --from <起点提交> --to HEAD
  ```
- `git push`、制品打包、线上部署与服务重启属于独立授权边界，须经用户明确同意。

---

## 5. 按需阅读参考资料

| 场景 | 文档 |
|---|---|
| 状态机、六条铁律与可信本地门禁 vs 容器隔离边界 | [references/architecture.md](references/architecture.md) |
| 诊断分期、L1/L2/L3 选档、逐字节行为探针与反向变异测试 | [references/workflow-tiers.md](references/workflow-tiers.md) |
| Executor 与独立 Reviewer 标准任务书模板 | [references/brief-templates.md](references/brief-templates.md) |
| 真实工程失败案例集（合同错、漏调 execute、探针盲区、异步竞态等） | [references/failure-casebook.md](references/failure-casebook.md) |
| 外部规则自进化提案与跨任务晋升机制 | [references/rule-evolution.md](references/rule-evolution.md) |
