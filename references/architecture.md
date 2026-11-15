# 双层架构、状态机与边界

框架把候选实现和验收决定分开，减少目标漂移、自审遗漏以及合同错误引起的无效代码修改。它不替代业务测试，不保证有限轮次必然成功。

## 两层与三角色

| 角色 | 职责 | 边界 |
|---|---|---|
| Supervisor | 冻结任务、签发、归因、预算内recycle | 不偷偷修候选或改写历史失败 |
| Executor | 在allowed_paths实现、针对性自查、单次execute | 不改合同/政策，不自封accept，提交后不改源码 |
| Reviewer | 核对diff和回执、独立风险验证、填写observations并裁决 | 不修改候选或调用execute/recycle，不批准自己写的代码 |

首次审阅使用独立冷上下文；同一reviewer可对其指出的局部修复跟进一次。目标/范围、公共契约、并发或安全假设变化，以及再次失败或暴露漏检时，重新冷审。CLI每次仍启动新review进程；宿主不具备独立审阅能力时不得报告独立accept。

## 状态机

```text
idle → issue → issued → execute once → running → awaiting_review
                 ↑                                  │
                 └── recycle + rejecting hash ← rejected
                                                    │
                                    reviewer → accepted
```

development与verification共用此状态机和max_rounds。verification只跳过LLM编码代理，控制器仍执行完整冻结checks；没有检查缓存或第二套预算。

## 程序强制的不变量

- **原子签发**：临时目录中封存合同、政策、profile与合同引擎并自检，成功后改名为rounds/NNN；失败不留下半轮或消耗预算。
- **单次execute**：进入execute就消耗本轮尝试，包括检查失败、超时与信号取消。失败后必须经独立驳回和监督者recycle才能重试。
- **固定候选**：首项检查前生成candidate.json，覆盖Git跟踪文件、子模块和非ignored新增文件。每项检查前后以及accept前核验快照；构建产物应在.gitignore中。
- **进程回收**：检查使用独立POSIX进程组和shell=False，超时/取消时回收子孙进程。
- **封存与回放**：events.jsonl形成哈希链，每轮保存合同、政策、检查回执、审阅证据及合同脚本；外部目录删除后仍能离线回放。合入时提供state-dir可校验提交树与已验收候选。
- **仅验证修正**：父反馈明确contract与VERIFICATION_ONLY:true，没有代码阻断项；签发、execute开始、accept前核对父候选和HEAD。目标/criteria/scope保持不变，checks标识保留，探针修订另行封存；不改写父轮失败。

## 程序无法替代的判断

控制器校验身份入口、状态、文件哈希、检查退出码和验收结果完整性；不能从证据文件的存在推断业务结论正确。独立审阅者仍须判断反例是否有效、探针是否继续验证原标准、哪些缺陷真正阻断验收。

审阅者维护一份observations；review --observations生成兼容feedback/comparison，不自动填写PASS。旧feedback/report接口仍支持。单会话持有多个角色密钥不代表真的完成独立审阅。

审阅先核对封存回执，不默认重复全量checks；按风险补独立验证。只有证据缺失、环境不明、候选不一致、检查不稳定或新风险时重跑相关检查。候选不变的verification轮仍重新执行全部冻结checks，不继承旧PASS。

## 安全与成本边界

这是可信本地工作流门禁，不是针对同一UID下恶意进程的操作系统沙箱。角色密钥、源码快照和哈希链用于发现误操作和证据漂移；同一用户的任意代码执行权限可以绕过CLI。对不可信执行器，应将control/authority/政策留在容器或VM外，只挂载受限工作树与只读任务书。

max_rounds是重试边界，不是token预算或收敛证明。只统计实际agent调用、检查和耗时；没有后端usage时token未知。模拟demo证明编排路径，不证明真实模型审阅质量或节省比例。
