# Dual-Loop Dev (`dual-loop-dev`)

[English Documentation](README.en.md) · [架构与安全边界](references/architecture.md) · [L1/L2/L3 分档指南](references/workflow-tiers.md) · [真实拦截案例集](references/failure-casebook.md)

**面向 AI 编码智能体的双层循环受监督自动化开发框架。**

`dual-loop-dev` 把单层自动编程中极易失控的「边改边跑测试、红了就改断言凑绿灯、自己宣布完成」拆解为**内层单次受控实现（Executor）**与**外层冻结合同 + 独立冷审阅 + 预算内归因重签（Supervisor + Independent Reviewer）**的双层闭环，并提供**自动 Git Worktree 隔离**与**多任务 DAG 夜间无人值守守护进程**。

A zero-dependency Python 3.10+ framework and universal Agent Skill (`SKILL.md`) for high-reliability automated software engineering. It separates **single-attempt inner execution** from **outer contract freezing, cold adversarial review, budgeted recycling, and isolated Git worktree roadmap orchestration**.

---

## 核心特性

- **两层闭环 · 三权分立**：`supervisor`（冻结合同/签发/归因重签）、`executor`（仅限白名单单次实现与提交）、`reviewer`（独立复跑/冷读 Diff/`/tmp` 反向变异验证/裁决）使用独立 `0600` 角色令牌，物理阻断自审自批与静默重试。
- **单次 `execute` 与检查前后源码封存**：每轮仅允许一次提交；第 1 项检查前封存源码树 SHA-256 快照 `candidate.json`，每项检查前后重验指纹，严防检查命令充当自动改代码的后门；POSIX 进程组隔离确保超时（`124`）或信号取消时干净回收全部子孙进程。
- **防篡改哈希链与自包含离线回放**：所有事件写入哈希链 `events.jsonl`；每轮 `rounds/NNN/` 完整封存合同、政策、`profile.json`、检查日志、审阅证据与合同脚本字节，即使外部目录删除仍可随时离线 `replay`。
- **内置零依赖合同引擎 + 兼容外部 TaskKit**：内置标准库 `contract.py`（`init-case / freeze / check / compare / replay`）开箱即用，同时无缝兼容外部已安装的 [TaskKit](https://github.com/abuttoncc/agent-taskkit)（`taskkit_script`）。
- **实战容错与三分类归因**：自动处理「执行者漏调 `execute` 补交」「审阅者 API 额度中断只读重派」，以及被拒后的 `code`（代码缺陷）/ `contract`（合同缺陷，候选代码零重做）/ `environment`（并发漂移）精准归因。
- **无人值守多任务 DAG 队列与 Worktree 隔离**：`roadmap.py` 自动在仓外创建隔离 Git Worktree 与空闲端口租约，按依赖拓扑推进任务包，实时刷新 `progress.md` 进度板，待全部通过后经审阅尾注校验一键合入主分支。
- **零第三方依赖 · 双形态即插即用**：仅依赖 Python 3.10+ 标准库与 Git；既可作为 CLI 工具（`dual-loop`）无人值守运行，也可直接克隆进 `.agents/skills/` 或 `.claude/skills/` 作为通用 Agent Skill 使用。

---

## 架构总览

```text
┌────────────────────────────────────────────────────────────────────────────┐
│ 外层治理闭环 (Outer Governance & Adversarial Review Loop)                   │
│                                                                            │
│  [Supervisor] ──1. freeze 合同 + issue 签发──▶ [Controller 状态机 & 哈希链]   │
│       ▲                                              │        ▲            │
│       │ 6. 归因(code/contract/env)                   │        │            │
│       │    + 预算内 recycle (凭 review_hash)          │        │ 5. accept  │
│       │                                              ▼        │    / reject│
│  [Rule Evolution] ◀──暴露规范缺口── [Cold Reviewer (独立复跑 + /tmp 反向变异)] │
└──────────────────────────────────────────────────────┼────────▲────────────┘
                                                       │ 2.     │ 4. 封存
                                                       ▼ 下发   │ awaiting_review
┌──────────────────────────────────────────────────────┴────────┴────────────┐
│ 内层执行闭环 (Bounded Single-Attempt Execution Loop)                        │
│  [Executor] 仅在 allowed_paths 编码 ──▶ 本地自查 ──▶ 3. 仅调用一次 execute   │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 30 秒快速体验

无需配置任何 LLM Key，运行内置端到端双任务路线图演示（含第 1 轮单测全绿但被冷审阅对抗探针驳回 → 第 2 轮自动重签修复 → Worktree 尾注校验合入全过程）：

```bash
git clone https://github.com/hanlinlibham/dual-loop-dev.git
cd dual-loop-dev

# 运行端到端演示
python3 examples/minimal-project/run_demo.py

# 运行全量状态机反例与编排测试套件
python3 -m unittest discover -s tests -p "test_*.py" -v
```

---

## 安装方式

### 1. 安装为命令行工具（CLI）

```bash
# 使用 pip / uv / pipx 安装（零第三方依赖）
pip install -e .
dual-loop --help
```

### 2. 安装为通用编码助手技能（Claude Code / Cursor / Codex / Antigravity）

```bash
# 安装到用户级通用技能目录
mkdir -p ~/.agents/skills
git clone https://github.com/hanlinlibham/dual-loop-dev.git ~/.agents/skills/dual-loop-dev

# 若使用 Claude Code，同样可放入 ~/.claude/skills/dual-loop-dev
```

安装后可直接对你的 Agent 说：

> 请使用 `dual-loop-dev` 技能，先对当前仓库跑一遍占用预检和基线诊断，按风险分期后在隔离 worktree 里用双层循环推进这项重构。

---

## 使用指南

### 1. 在目标仓库放置 `profile.json`

将仓库专属保护路径与端口配置解耦到 `profile.json`（Schema 见 [`schemas/profile.schema.json`](schemas/profile.schema.json)）：

```json
{
  "schema_version": 1,
  "name": "my-project",
  "protected_paths": ["core/", "migrations/", "profile.json"],
  "require_clean_worktree": true,
  "preflight": { "port": 3000 },
  "docs": ["ARCHITECTURE.md"]
}
```

### 2. 模式一：单任务自动双层编排（`dual-loop orchestrate`）

支持对接 `"kind": "claude"`（Claude Code CLI）、`"kind": "codex"`（Codex CLI）或 `"kind": "command"`（任意自定义 Agent 脚本）：

```bash
dual-loop orchestrate --task-config task.json --state-dir ../task-state/auth-guard
```

### 3. 模式二：多任务 DAG 路线图与夜间无人值守守护进程（`dual-loop roadmap`）

编写 `roadmap.json`（Schema 见 [`schemas/roadmap.schema.json`](schemas/roadmap.schema.json)）：

```bash
# 单次按拓扑顺序跑完所有可推进任务（自动创建仓外隔离 Git Worktree）
dual-loop roadmap run --roadmap roadmap.json --state-dir ../roadmap-run

# 守护进程模式：后台持续监听 roadmap.json 新增任务，带 flock 防重入与心跳文件
dual-loop roadmap daemon --roadmap roadmap.json --state-dir ../roadmap-run --poll-interval 15

# 查看实时状态与生成的 progress.md 看板
dual-loop roadmap status --state-dir ../roadmap-run

# 全部验收后，校验每笔提交的审阅尾注并快进合入主分支
dual-loop worktree integrate --repo . --worktree <execution_repo_path>
```

### 4. 模式三：底层状态机逐步精确控制（`dual-loop controller`）

适合宿主 Agent 在同一会话内精细派发执行子代理与审阅子代理：

```bash
dual-loop preflight --repo . --workspace ..
dual-loop controller init --root "$CTRL" --authority "$AUTH"
dual-loop contract init-case --root "$CASE" --topic fix-parser --question "Fix edge case" --criteria-json criteria.json
dual-loop contract freeze --root "$CASE"
dual-loop controller issue --root "$CTRL" --token "$AUTH/supervisor.key" --manifest manifest.json
dual-loop controller execute --root "$CTRL" --token "$AUTH/executor.key"
dual-loop contract compare --root "$CASE" --observations outputs/obs.json --out outputs/review-comparison.json
dual-loop controller review --root "$CTRL" --token "$AUTH/reviewer.key" --verdict accept --feedback feedback.md --report outputs/review-comparison.json
```

---

## 仓库目录结构

| 路径 | 说明 |
|---|---|
| [`SKILL.md`](SKILL.md) | 编码助手技能主入口（选档、预检、三角色工作流） |
| [`dual_loop/cli.py`](dual_loop/cli.py) | 统一 `dual-loop` CLI 入口分发器 |
| [`scripts/contract.py`](scripts/contract.py) | 内置零依赖证据合同引擎（兼容 TaskKit v0.5 协议） |
| [`scripts/controller.py`](scripts/controller.py) | 单次执行状态机、候选源码封存、进程组回收与哈希链归档 |
| [`scripts/worktree.py`](scripts/worktree.py) | Git Worktree 创建、空闲端口租约、`LOCK` 与审阅尾注校验合入 |
| [`scripts/orchestrator.py`](scripts/orchestrator.py) | 单任务外层自动调度器（密钥隔离、漏交补提、审阅重试、被拒归因） |
| [`scripts/roadmap.py`](scripts/roadmap.py) | 多任务 DAG 队列与无人值守守护进程（实时生成 `progress.md`） |
| [`scripts/preflight.py`](scripts/preflight.py) | 活跃轮次、`LOCK`、近期 Agent 会话与端口只读占用检查 |
| [`scripts/summary.py`](scripts/summary.py) | 基于 Git 审阅尾注的确定性完成度汇总统计 |
| [`schemas/`](schemas/) | `profile`、`manifest`、`roadmap`、`rule-proposal` JSON Schemas |
| [`references/`](references/) | 架构边界、三档门禁、任务书模板、真实失败案例集与规则自进化手册 |
| [`examples/minimal-project/`](examples/minimal-project/) | 3 秒可跑通的双任务双层循环完整演示 |
| [`tests/`](tests/) | 覆盖越权、篡改、超时、信号取消、离线回放与无人值守编排的反例测试集 |

---

## 安全与能力边界声明

`dual-loop-dev` 是一个**可信本地工作流门禁（Trusted-Local Workflow Guard）**，用于阻止自动化编码工作流中的角色混淆、静默重试、范围越界与历史账本篡改；它**不是**对抗同一操作系统 UID 下恶意代码的内核级沙箱。

若需运行完全不可信的第三方代码或高危无人值守 Worker，请将 `control/`、`authority/` 与政策文件保存在容器/VM 宿主机外层，仅向 Worker 容器挂载只读任务单与受限工作树。详见 [`references/architecture.md`](references/architecture.md)。

---

## License

[MIT](LICENSE)
