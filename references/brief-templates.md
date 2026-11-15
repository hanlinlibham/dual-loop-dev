# Executor 与 Reviewer 任务书

从同一任务定义和当前回执生成任务书；不要每轮另写设计书、检查摘要和相同内容的反馈长文。手工宿主与CLI遵守同一规则。完整命令入口见 [AGENTS.md](../AGENTS.md)。

## 执行者

````markdown
你是 <task_id> 第 <round> 轮的executor。
目标：<objective>
工作树：<repo>，HEAD：<head>
可修改：<allowed_paths>；保护路径：<protected_paths>
冻结验收：<criteria>；正式checks：<checks>

只在范围内实现。不得修改合同/政策/控制目录、读取其他角色密钥或执行Git提交、重置、切分支、合并、推送。
开发中按改动做针对性自查，不要求先完整复跑正式checks。
准备好后仅运行一次：
```sh
python3 "<controller_script>" execute --root "<control_root>" --token "<executor_token>"
```
无论成功或失败，随后停止修改候选。
结束时只补充设计取舍、存疑点和未覆盖边界；文件、退出码、检查数由回执提供。不要自行宣布验收通过。
````

## 独立审阅者

````markdown
你是 <task_id> 第 <round> 轮的独立reviewer，未参与编写候选。
输入：<repo>、基线<head>、<case_root>、<round_directory>。
审阅类型：<首次冷审 | 一次定向跟进>。
上轮阻断项及本轮增量：<如有，仅列相关反馈>。

不得修改被审工作树、调用execute/recycle或读取其他角色密钥。
1. 核对diff/status的修改范围及not_applicable依据。
2. 核验candidate、execution和日志对应当前候选；读相关diff。
3. 有效封存回执无需默认全量重跑。证据缺失、环境不清、候选不一致、历史不稳定或新风险出现时，复跑相关检查并记录原因。
4. 独立验证本轮风险：UI走真实宿主关键动作；数据/文件结构构造边界反例；模型可见行为核对实际返回和说明。关键守卫须有能发现退化的证据；可用既有反例或/tmp副本变异，不凑数量。
5. 定向跟进只看局部patch、原反例及关联回归。目标/范围或公共契约、并发、安全假设变化，同一问题再失败或发现先前漏检时，要求全新冷审。
6. 每个阻断项写明验收ID、触发条件、影响和复验要求。非阻断润色单独列出。

将证据文件和一份observations写入case的outputs目录，真实填写各项结果。执行：
```sh
python3 "<controller_script>" review --root "<control_root>" --token "<reviewer_token>" \
  --verdict <accept|reject> --observations outputs/observations.json
```
生成器负责兼容feedback/comparison，不自动判定PASS。返回裁决、review_hash和关键阻断项即可。
````

## Observations主结果

保留合同引擎原有rows/evidence结构，新增review字段。以下是**尚未完成验证**的合法状态示意，不能原样当成通过证据：

```json
{
  "baseline_sha256": "<冻结基线文件的SHA-256>",
  "observed_at": "<冻结后的UTC ISO-8601时间>",
  "review": {
    "summary": "尚未完成行为验证",
    "attribution": "environment",
    "verification_only": false,
    "blockers": ["behavior-verified：运行环境不可用，需恢复环境后复验"],
    "non_blocking": []
  },
  "rows": [
    {
      "id": "behavior-verified",
      "status": "NOT_RUN",
      "detail": "环境未就绪，不能判断行为",
      "evidence_level": "independent-review",
      "evidence": []
    }
  ]
}
```

- review.summary必填；拒绝时attribution必须为code、contract或environment。blockers/non_blocking为字符串列表；blockers记录尚未解决的代码或产品缺陷。
- rows中的PASS/FAIL必须有实际证据路径；对未运行项如实写NOT_RUN，不能默认填PASS。
- accept要求控制器检查成功、所有required通过且没有阻断项。非阻断建议不阻止accept。
- `verification_only: true` 仅用于已确认的纯探针/夹具问题：归因contract，blockers为空，将验证工具错误及其证据写入summary；仍须解释为何原业务标准不变。
- CLI每次是新review进程；定向brief不等于恢复同一个reviewer。缺少独立审阅能力的宿主必须保留待审状态。
- 旧手动接口仍可先用contract.py compare，再以 `review --feedback <文件> --report <case相对路径>` 提交；无需迁移已有归档。
