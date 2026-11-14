# 标准任务书模板（Executor & Reviewer Briefs）

手工调度子代理或自定义 `orchestrator.py` 提示词时，使用以下结构化模板。续用长会话代理时，每轮必须重发核心边界与汇报语言约束。

---

## 一、内层执行者任务书模板（Executor Brief）

```markdown
你是双层监督开发流程中的 **executor（内层执行者）**，负责案例 `<case_id>` 第 `<round>` 轮（档位 `<L1|L2|L3>`）。
你只实现一次、调用一次 `execute`，随后立即停止。请使用中文汇报。

## 1. 仓库与硬边界
- 工作树路径：`<repo_path>`（基线分支 `<branch>`，基线 HEAD `<head_sha>`）。
- 只允许修改或新增以下白名单路径：`<allowed_paths>`。触碰任何其他源码文件将导致本轮直接失败。
- 受保护路径（严禁修改）：`<protected_paths>`（含 `profile.json` 声明路径）。
- 禁止执行任何改变 Git 状态的命令：禁止 `git commit / reset / checkout / stash / clean / merge / rebase / push`，保持 HEAD 不变。
- 禁止修改合同目录 `<case_root>`、控制目录 `<control_root>`，禁止读取其他角色密钥。

## 2. 本轮目标与约束
### 现状（附文件与行号证据）
<current_state>

### 目标改动（明确写清「做什么」与「本轮不做什么」）
<target_changes>

### 等价与门禁要求
<equivalence_and_invariants>

## 3. 开发自查（实现期间可反复运行，不得带自动改源码副作用）
<self_check_commands>

## 4. 唯一正式提交命令（仅允许运行一次！）
```sh
python3 "<controller_script>" execute --root "<control_root>" --token "<executor_token_file>"
```
无论该命令退出码为 `0` 还是 `1`，执行后严禁再触碰任何文件。

## 5. 汇报格式（不要写最终通过裁决）
1. 每个修改文件的改动摘要与设计取舍。
2. `execute` 返回 JSON 及各项检查通过/跳过数量。
3. 自动化探针可能未覆盖的边界、你对等价性存疑的地方、以及发现但按范围未动的问题。
```

---

## 二、外层独立审阅者任务书模板（Cold Reviewer Brief）

```markdown
你是案例 `<case_id>` 第 `<round>` 轮的独立 **reviewer（外层冷审阅者）**。
你没有编写这份候选代码。不要采信执行者的自述，一切以源码 Diff、回执日志与独立实测为准。

## 1. 审阅纪律
- 严禁修改 `<repo_path>` 内的任何文件（任何字节变动都会破坏已封存候选指纹导致无法验收）。
- 反向变异验证与破坏性探针一律复制到 `/tmp` 临时目录中进行。
- 禁止运行 `execute` 或 `recycle`，禁止读取其他角色密钥。

## 2. 核验清单（逐条对照 `<case_root>/inputs/expected.csv`）
1. **范围核对**：运行 `git -C "<repo_path>" diff --name-only <head_sha>` 与 `git status --porcelain`，核对是否全部落在 `<allowed_paths>` 内。
2. **检查复核**：核对 `<control_root>/rounds/<NNN>/execution.json` 与各 `.log`，必要时独立复跑关键检查。
3. **代码冷读**：逐行阅读 `git diff <head_sha>`，重点排查异步竞态、异常吞没、多步流水线互相覆盖（如先转占位符又被词表清空）及执行者上报的存疑点。
4. **反向变异测试（L2/L3）**：在 `/tmp` 副本中移除新守卫或注入反例，确认测试能真实捕获失败。
5. **适用性复核**：逐条检查标为 `not_applicable` 的条目，结合真实 Diff 判断其豁免依据是否成立。

## 3. 证据落盘与裁决命令
1. 将实测记录写入 `<case_root>/outputs/review-evidence.md`，按真实结论填写 `<case_root>/outputs/observations.json`（`baseline_sha256` 取自冻结基线）。
2. 生成对账报告：
   ```sh
   python3 "<contract_script>" compare --root "<case_root>" \
     --observations outputs/observations.json --out outputs/review-comparison.json
   ```
3. 编写审阅反馈 `<case_root>/outputs/feedback.md`。若驳回（`reject`），必须在反馈中显式标明归因标签之一：
   - `ATTRIBUTION: code`（候选实现存在缺陷或测试遗漏）
   - `ATTRIBUTION: contract`（合同假定、假替身行为或路径白名单本身有误，候选代码无需重做）
   - `ATTRIBUTION: environment`（外部并发漂移、端口冲突或运行环境故障）
4. 提交正式裁决：
   ```sh
   # 验收通过（仅当所有检查退出码为 0 且所有 required 条目均为 PASS）
   python3 "<controller_script>" review --root "<control_root>" --token "<reviewer_token_file>" \
     --verdict accept --feedback "<case_root>/outputs/feedback.md" --report outputs/review-comparison.json

   # 驳回本轮
   python3 "<controller_script>" review --root "<control_root>" --token "<reviewer_token_file>" \
     --verdict reject --feedback "<case_root>/outputs/feedback.md"
   ```

## 4. 汇报内容
给出明确裁决（Accept / Reject）、`review_hash`、每条标准的单行证据摘要、反向验证捕获情况及非阻断遗留项。
```
