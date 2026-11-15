# Dual-Loop Dev

[English](README.en.md) · [Agent执行入口](AGENTS.md) · [SKILL](SKILL.md) · [技术概览](docs/OVERVIEW.en.md) · [工程案例](references/failure-casebook.md)

dual-loop-dev为AI编码任务提供受监督的交付闭环：冻结目标和范围，执行者提交候选，独立审阅者依据代码与证据裁决，再按具体阻断问题进行预算内修复。

它解决的是验收标准漂移、自审盲区、验证工具错误导致的无效返工，以及多任务共享工作树的冲突。自动化测试是证据来源之一；框架不保证每轮更优或最终成功。

## 默认工作方式

```text
一份任务定义 → 冻结合同 → 针对性开发自查 → execute正式检查并封存
                                                ↓
             预算内定向修复 ← 独立审阅diff、回执与风险 → accept
```

- **执行检查一遍，审阅关注新信息。** 执行者开发中做针对性自查，execute运行正式冻结checks。审阅者核对有效回执，不默认再跑全量；证据或环境有疑点时复跑相关检查。
- **按风险独立验证。** UI检查真实宿主关键动作，数据处理检查边界与最终产物，模型可见行为核对返回值和说明。变异用于验证关键守卫，不设数量配额。
- **一份审阅主结果。** observations记录真实结论和证据；`review --observations`生成兼容feedback/comparison，不自动填PASS。
- **局部修复可跟进一次。** 首次冷审；原生宿主可复用原reviewer一次。目标/范围、公共契约、并发或安全假设变化，以及再失败或暴露漏检时，换新冷审。CLI每次仍启动新进程。
- **只修验证工具时不再调用编码代理。** 经独立确认的纯探针/夹具错误可进入verification轮：整个候选和验收目标不变，关联父轮，重新执行全部checks并独立裁决。仍消耗原预算，旧失败保留；当前没有跨轮检查缓存。

## 程序提供的门禁

三种角色入口、路径白名单和保护路径、单轮单次execute、检查前后候选哈希、超时进程组回收、不可静默上调的轮数预算、哈希链与自包含历史回放。Worktree工具提供隔离工作树和端口租约；合入时可将提交树与已验收候选核对。

审阅质量仍依赖人的或模型的判断。程序可以验证证据存在且未变，不能凭一个PASS字段证明业务正确；不支持独立审阅的宿主必须保留待审状态。

## 开始使用

只需要Python 3.10+和Git，不需要第三方依赖：

```sh
git clone https://github.com/hanlinlibham/dual-loop-dev.git
cd dual-loop-dev
python3 examples/minimal-project/run_demo.py
python3 -m unittest discover -s tests -p "test_*.py" -v
```

demo用模拟Agent覆盖驳回、重签、接受与合入路径；它不是实际LLM收敛能力或token节省的对照实验。

可直接运行scripts，也可安装CLI：

```sh
pip install -e .
dual-loop --help
```

需要作为Skill使用时，将仓库安装到宿主的技能目录。用于其他代码库时，把框架和control/authority/case状态放在目标工作树外。

将以下提示词交给支持独立子代理或CLI后端的编码助手即可：

> 阅读dual-loop-dev的AGENTS.md和SKILL.md，按当前宿主能力推进我的目标。维护一份任务定义，按实际风险验证，保留独立验收、固定候选和预算；避免默认重复全量检查及同义文书。先完成当前已授权范围，再汇报实际结果与开销。

完整的task/profile配置、手工宿主与自动编排命令、observations接口、verification条件、提交及合入方式统一放在 **[AGENTS.md](AGENTS.md)**。单任务使用orchestrator，多任务依赖队列使用roadmap；默认支持claude、codex或自定义command后端。

## 适用范围与兼容

复杂功能、跨模块改造、状态/并发问题及无人值守队列适合使用。只读问答和低风险微调无需启用；按实际影响选L1/L2/L3，不能仅凭文件数量或.md后缀降档。

旧 `review --feedback ... --report ...`、无新字段的配置和外部[TaskKit](https://github.com/abuttoncc/agent-taskkit)接口继续支持，历史归档不重写。正在执行的任务完成后再升级流程。

统计实际agent调用、检查和耗时；没有真实后端usage时token为unknown，不承诺未经测量的节省比例。max_rounds是停止边界，不是成功保证。

这是可信本地工作流门禁，不是同一UID下恶意代码的沙箱。安全边界见[architecture.md](references/architecture.md)；分档和审阅要求见[workflow-tiers.md](references/workflow-tiers.md)，规则改进约定见[rule-evolution.md](references/rule-evolution.md)。

[MIT License](LICENSE)
