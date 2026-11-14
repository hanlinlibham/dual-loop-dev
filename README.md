# Dual-Loop Dev (`dual-loop-dev`)

[English Documentation](README.en.md) · [**🤖 云端/本地 AI Agent 自引导手册 (`AGENTS.md`)**](AGENTS.md) · [`SKILL.md`](SKILL.md) · [架构与安全边界](references/architecture.md) · [L1/L2/L3 分档指南](references/workflow-tiers.md) · [真实拦截案例集](references/failure-casebook.md)

**面向 AI 编码智能体的双层循环受监督自动化开发框架。**

`dual-loop-dev` 把单层自动编程中极易失控的「边改边跑测试、红了就改断言凑绿灯、自己宣布完成」拆解为**内层单次受控实现（Executor）**与**外层冻结合同 + 独立冷审阅 + 预算内归因重签（Supervisor + Independent Reviewer）**的双层闭环，并提供**自动 Git Worktree 隔离**与**多任务 DAG 夜间无人值守守护进程**。

> **🤖 给直接读取本仓库的云端 / 远程 AI Agent（Codex Cloud · Claude Code Web · Cursor Cloud · Devin · Antigravity）**：  
> 请直接阅读根目录 [**`AGENTS.md`**](AGENTS.md)（及 [`SKILL.md`](SKILL.md)）。无需 `pip install` 或任何第三方依赖——将本仓库克隆到目标代码库**外部目录**（如 `/tmp/dual-loop-dev`）后，即可对照 `AGENTS.md` 中的完整命令行配方与最小合法 JSON 模板，零猜测跑通全套受监督双层开发闭环。

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

安装后即可在任意新代码库中通过提示词驱动完整受监督迭代闭环（见下方模板）。

---

## 在新代码库启动持续迭代优化与交付（提示词模板）

当你进入一个**全新的代码库**，希望 Agent 围绕某个目标持续拆解、迭代优化、独立验收并干净交付时，按环境直接复制对应提示词即可：

### 云端 / 临时容器免预装直连模式（无需提前安装 Skill，直接贴仓库链接即可）

适用于 Codex Cloud、Claude Code Web、Cursor Background Agent、Devin 等云端环境——云端 Agent 读取本仓库根目录 [`AGENTS.md`](AGENTS.md) 后会自动完成外部挂载与全流程执行：

```markdown
请先将 https://github.com/hanlinlibham/dual-loop-dev 克隆到当前仓库外部目录（如 `/tmp/dual-loop-dev`），仔细阅读其根目录下的 `AGENTS.md` 与 `SKILL.md`，然后严格按照里面的双层循环受监督规范，对当前仓库执行【<在此填写你的目标>】的持续迭代优化与交付：
1. 先跑只读摸底（`preflight.py`、基线测试/指标采集、检查 `.gitignore` 缓存忽略规则、建立 `profile.json`），将目标拆解为 2~5 个小步独立验证的工作包并给出明确停止条件。
2. 用 `worktree.py create` 在仓外创建隔离 Git Worktree，所有 `control / authority / cases` 状态目录一律放在目标仓库外部。
3. 对每个工作包执行完整 `contract.py` + `controller.py` 双层闭环（冻结合同 → `issue` → 单次 `execute` 封存候选源码 → 全新冷审阅与 `/tmp` 反向变异探针 → 若驳回先归因再在 `max_rounds=3` 内 `recycle` → 验收后附 `Accepted in supervised round <case> (review <hash8>)` 尾注本地提交）。
4. 全部达标后，用 `worktree.py integrate` 校验尾注合入目标分支，用 `summary.py` 实算统计并输出前后对比报告；未经明确确认不执行 `git push`。
```

### 模板 A：本机主会话架构师总控模式（本机已安装 `dual-loop-dev` 技能时最常用）

```markdown
请启用 `dual-loop-dev` 技能，作为外层 Supervisor 对当前仓库执行【<在此填写你的目标，例如：核心解析链路性能优化 / 模块解耦重构 / 补齐鉴权与边界防御>】的持续迭代优化与交付，严格遵守以下流程：

1. **只读摸底与基线建档**：
   - 先运行 `preflight.py` 检查仓库与工作区占用；只读梳理模块边界、现有测试/构建命令与 `.gitignore`（确保构建缓存与 `__pycache__/` 已被忽略，避免检查命令污染源码快照）。
   - 若仓库尚无 `profile.json`，先识别不可误碰的核心目录/配置并生成 `profile.json`（设置 `"require_clean_worktree": true`）。
   - 先实测并记录当前基线数据（现有测试通过率、性能指标或缺陷复现证据），禁止未诊断直接改代码。

2. **分期拆解路线图**：
   - 将目标拆解为 2~5 个可独立验证、小步快跑的迭代工作包，明确每个包的档位（`L1/L2/L3`）、白名单 `allowed_paths`、自动化检查命令与可量化验收标准（`expected.csv`），给出整件事情的明确停止条件（Stop Rule）。

3. **隔离 Worktree 与双层循环逐包迭代**：
   - 使用 `worktree.py create` 在仓外创建独立 Git Worktree（及隔离端口），严禁在主分支直接裸改。
   - 对每个工作包严格执行：冻结证据合同（无 `investigate` 悬而未决项）→ 签发 `issue` → 派发内层 Executor 子代理在白名单内单次实现并只跑一次 `execute` → 派发**全新上下文**的冷 Reviewer 子代理独立复跑检查、冷读 Diff 并在 `/tmp` 副本上做反向变异探针验证。
   - 若某轮被 `reject`，先明确归因（`ATTRIBUTION: code | contract | environment`），在单包 `max_rounds=3` 预算内凭 `review_hash` 执行 `recycle` 迭代修复；预算耗尽仍未过则停机汇报，禁止静默改弱测试标准。

4. **尾注合流与实算交付**：
   - 每个工作包 `accept` 后，在隔离 Worktree 内创建带 `Accepted in supervised round <case> (review <hash8>)` 审计尾注的本地提交，进入下一包迭代直至满足总停止条件。
   - 全部完成后，用 `worktree.py integrate` 校验尾注合入目标分支（或保留隔离分支待检），运行 `summary.py` 实算提交数与改动统计，输出基线对比与交付总结。未经我明确确认，不要执行 `git push` 或重启生产服务。
```

### 模板 B：夜间/后台无人值守守护模式（适合长任务队列自动挂机）

```markdown
请启用 `dual-loop-dev` 技能，先只读摸底当前仓库并生成 `profile.json`；然后将【<在此填写复杂需求或路线图文档路径>】拆解为一份符合 `schemas/roadmap.schema.json` 的多任务 DAG 配置文件 `../roadmap-state/roadmap.json`（开启 `"isolate_worktree": true`，配置好每项任务的 `allowed_paths`、`checks`、`criteria` 与 `depends_on` 依赖关系，后端使用 `<claude 或 codex>`）。

生成并校验通过后，运行 `dual-loop roadmap run --roadmap ../roadmap-state/roadmap.json --state-dir ../roadmap-state`（或 `daemon` 模式）自动推进队列，完成后向我汇报 `progress.md` 看板结果与待合入分支状态。
```

---

## 与普通 Skill 及相关工具的区别与联动

| 维度 | 普通编码 Skill（纯提示词规范） | `dual-loop-dev`（本框架） | 宏观任务管理（如 [TaskKit / `task-harness`](https://github.com/abuttoncc/agent-taskkit)） |
|---|---|---|---|
| **约束强度** | **软约束**：依赖大模型自觉遵守 Prompt，长对话易遗忘、易改弱测试或自审自批 | **硬门禁**：Python 状态机 + `0600` 三角色密钥 + 检查前后源码 SHA-256 封存 + 哈希链账本 | **交接门禁**：聚焦跨会话/跨天任务建档、`handoffs/` 快照与已交付文档防原位覆写 |
| **运行形态** | 仅在对话内被动提供参考指令 | **双模合一**：既可当会话内 Skill，也可由 CLI（`orchestrate` / `roadmap daemon`）在宿主机主动调度多个子 Agent | 工作区级任务目录与里程碑索引管理 |
| **协同方式** | 可作为领域知识（如框架开发规范、UI 规范）写入 `profile.json` 的 `docs` 与每轮 `policy_files`，由本框架冻结归档并交冷 Reviewer 逐条核验 | 负责「代码修改这一步」的微观受监督闭环、Worktree 隔离与防回归对抗审阅 | 与本框架共享完全一致的 `spec.json` + `expected.csv` 证据合同协议：可用 TaskKit 管理顶层项目需求与交接，用 `dual-loop-dev` 驱动底层代码迭代并将 `review-comparison.json` 挂入最终交付证据 |

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

# 全部验收后，交叉校验提交尾注、哈希链账本与封存 candidate.json 后快进合入主分支
dual-loop worktree integrate --repo . --worktree <execution_repo_path> --state-dir ../roadmap-run
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
| [`AGENTS.md`](AGENTS.md) / [`CLAUDE.md`](CLAUDE.md) | 云端与本地 AI Agent 免预装自引导手册（含完整 CLI 配方与最小合法 JSON 模板） |
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
