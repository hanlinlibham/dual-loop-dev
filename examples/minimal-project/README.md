# Minimal End-to-End Demo (`run_demo.py`)

本示例无需配置任何外部大模型 API Key，仅用 Python 3.10+ 标准库与本地 Git，即可在 3 秒内完整演示 `dual-loop-dev` 的核心闭环：

```sh
python3 examples/minimal-project/run_demo.py
```

## 演示全流程

1. **自动创建隔离 Git Worktree 与端口租约**：原仓库主分支保持干净不动，所有轮次在仓外隔离工作树中推进。
2. **任务 1（`sanitize-labels`，L3 档）演示「全绿单测下的冷审阅驳回与自动重签」**：
   - **第 1 轮**：Executor 实现通过全部单元测试（`execute` 返回 `0`），但独立 Reviewer 运行对抗探针发现纯数字标签被清成了空串 `""`，出具 `reject`（`ATTRIBUTION: code`）并生成不可变 `review_hash`。
   - **第 2 轮**：`orchestrator.py` 自动凭 `review_hash` 执行 `recycle` 并将审阅反馈注入第 2 轮任务书；Executor 修复边界后，全新 Reviewer 对账通过并出具 `accept`，自动在隔离分支生成带 `Accepted in supervised round ...` 尾注的本地提交。
3. **任务 2（`batch-export`，依赖任务 1）自动接续推进**：DAG 队列识别前置任务已 `accepted`，自动启动第 2 个任务并一轮通过。
4. **审阅尾注校验合入与离线回放**：`worktree.py integrate` 校验每一笔提交均具备合法审阅尾注后快进合入主仓，`summary.py` 输出统计，`controller.py status` 验证封存归档链完整性。
