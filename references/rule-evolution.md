# 外层规则自进化机制（Rule Evolution）

在双层循环体系中，不仅业务源码受监督，**开发规范与门禁政策本身也受同构的「提议 → 独立审查 → 跨任务晋升」闭环约束**。

---

## 一、核心原则

1. **规则作者不得自批**：提出新门禁或删减冗余门禁的代理/作者，不能自己批准该提案生效。
2. **禁止事后降标追认**：新政策只绑定未来新签发的轮次；历史已失败轮次保存在各自封存的 `rounds/NNN/policies/` 中，永远不得用新规则将历史 `rejected` 追认为 `accepted`。
3. **两例验证晋升铁律（Two-Case Promotion Rule）**：
   - 第一次在某个任务中暴露的教训，先记为**案例级经验**（或技能层推荐做法）；
   - 只有当该规则在**至少两个独立任务**中得到真实检验，并附带可重现的反例测试后，经独立规则审查方可晋升为仓库默认政策文件。

---

## 二、规则提案标准结构（`schemas/rule-proposal.schema.json`）

```json
{
  "proposal_id": "require-clean-worktree-at-issue",
  "feedback_ids": [
    "abd5eec79f01...",
    "70c9c3e241ab..."
  ],
  "previous_policy_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
  "problem": "在带未提交改动的脏工作树上签发轮次时，增量边界差集无法反映相对 HEAD 的真实修改面，需审阅者手工补查。",
  "change": "在 profile.json 支持 require_clean_worktree=true，并在 execution.json 中同时记录 changed_paths 与 head_changed_paths。",
  "scope": "所有启用 dual-loop-dev 的 Git 仓库开发轮次；对显式声明分步草稿的轮次允许关闭开关但保留 head_changed_paths 审计。",
  "counterexample": "在工作树存在未提交文件时调用 issue（开启 require_clean_worktree），控制器必须以退出码 2 拒绝且不生成半轮目录。",
  "validation_cases": [
    "task-alpha-round-01",
    "task-beta-round-02"
  ],
  "rollback": "在未来轮次绑定回旧版 policy 文件 SHA-256；已归档历史轮次不受影响。"
}
```
