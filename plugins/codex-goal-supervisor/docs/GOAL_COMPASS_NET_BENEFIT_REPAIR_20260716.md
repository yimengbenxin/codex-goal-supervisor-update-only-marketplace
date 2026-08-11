# Goal Compass 净收益重构与真实反馈修复

日期：2026-07-16

## 至高规则

> 每步流程必须产生净收益，不要让流程成为整个项目推进时的噪音和阻拦。

这里的净收益按整个有效生命周期判断，不按单次 patch 大小判断。能够持续减少
运行时间、重复验证、协调步骤、误报返工或维护耦合的重构，即使初始改动较大，
仍符合至高规则。只有无法说明后续节省、只是搬代码或新增术语的重构才不应做。

## 本轮根因

旧设计默认假设“进入 Goal 模式就应该启用完整票据治理”，又把状态统计、Hook、
验证、公司角色、Janitor 和 CLI 集中在一个主脚本中。结果是控制项容易被同时触发，
高频命令也会间接调用昂贵评估。真实项目中的主要问题因此不是缺少规则，而是：

1. 缺少一个先判断“是否值得管”的廉价决策器。
2. `status`、Hook、验证目录和 Janitor 的高频路径承担了不必要的扫描或解析。
3. 执行失败、上游证据失效、环境运行产物、目标漂移混用状态。
4. 公司回执、重复验证和每票清理形成仪式性工作。
5. 运行时代码边界不清，修一个热路径容易触碰无关逻辑。

## 已修问题

### 1. 未显式使用插件时仍全局介入

**原问题**：小任务和普通线程被 Goal Supervisor 自动捕获，流程成本大于风险。

**修改**：插件级 `hooks/hooks.json` 保持空；只有项目显式安装并存在 ACTIVE ticket
时，仓库本地 Hook 才执行。没有 ACTIVE ticket 时 Hook 被动放行。

**原因**：工具的存在不等于用户授权当前项目进入治理。显式接入消除了无收益全局税。

### 2. 每个任务都被迫建票

**原问题**：只读查询、一个字面量、一个断言也进入完整 ticket 生命周期。

**修改**：净收益判断器输出 `NONE / LIGHT / STANDARD / DEEP`：

- `NONE`：只读，或 20 分钟、3 文件、180 行以内且无高后果/质量门/独立角色需求的
  微型变更。直接执行普通项目验证，不创建 ticket。
- `LIGHT`：范围小但机器验收能明显降低返工；只启用范围、验收、close validation。
- `STANDARD`：有真实实现或跨职能风险；按独立产出选角色，不默认启动 Janitor。
- `DEEP`：高后果、并行集成、产品质量证据或超过四个部门的任务。

输出新增简短 `net_benefit.decision` 和依据。没有独立风险收益的控制项不启用。

### 3. Janitor 每票运行且误报成本高

**原问题**：普通实现也做清理扫描；弱关键词会让合法文件进入噪音候选。

**修改**：

- 权限继续固定为 `MARK_ONLY`，不能移动或删除项目文件。
- `STANDARD` 只有明确清理意图或宽变更面才预先启用 Janitor。
- 运行中使用廉价 changed-path trigger；只有出现 `stale`、`marketplace`、`RBAC`、
  越界路径、anti-pattern 或 backlog-domain 信号才进入有界分类。
- current-ticket Janitor 只构建 ticket、acceptance、validation 和 changed paths 的
  参考上下文，不先全仓扫描。
- 用户显式运行 `prune-check` 时，非 `NONE` 票可执行检查；`NONE` 微任务仍返回
  `NOT_REQUIRED`，避免手工命令重新制造仪式。

**原因**：先用廉价信号判断是否值得扫描，再用强证据分类，兼顾低成本与不漏明显噪音。

### 4. `status` 会扫描仓库并阻塞

**原问题**：旧版 `status -> evaluate -> snapshot`，非 Git 大仓库中逐文件再次解析
validation catalog，出现 35 秒以上无输出。

**修改**：`status` 只读取持久化的 `last_evaluation`、预算摘要、Hook 计数和最近票据；
不调用 `evaluate()`，不做仓库扫描。完整诊断由显式 `check` 承担。

validation catalog 按路径、mtime 和大小缓存；同一进程中未变化只解析一次。

**原因**：状态查询是控制面热路径，必须是缓存读取，而不是隐式审计。

### 5. Hook 每次写操作都做完整评估

**原问题**：`PreToolUse` 和 `PostToolUse` 调用完整 `evaluate()`，每次编辑都支付 diff、
预算和 Janitor 成本。

**修改**：

- Hook 只追加事件，并在短锁内更新紧凑 `hook_state.json` 计数。
- PreToolUse 只检查冻结合同、已知硬阻塞、实时 tool-call 上限和目标路径。
- PostToolUse 只记录计数并返回必要警告，不运行完整评估。
- sidecar 丢失或计数不一致时才从 append-only Hook 事件恢复。

**原因**：Hook 应做 O(1) 边界判断，完整语义只在显式 `check/close` 执行。

### 6. 验证失败后继续跑下游并重复执行

**原问题**：build 失败后 contracts/validate 继续执行；`check --run-validation` 通过后
`close` 又重跑相同验证。

**修改**：验证按目录顺序 fail-fast；首个失败保存为 root cause，后续命令标记 skipped，
并报告 suppressed cascade 数量。通过结果按验证命令和输入哈希缓存，输入未变化时
`close` 复用结果。

状态保持：未运行为 `NEEDS_VALIDATION`，失败为 `VALIDATION_FAILED`，不会返回
`ON_TRACK` 或 `PASS_READY`。

### 7. 失败分类错误

**原问题**：构建错误、上游证据失效和目标漂移都可能被写成 `DRIFT`。

**修改**：分别使用：

- `EXECUTION_FAILED`：实现或验证命令失败。
- `BLOCKED_BY_UPSTREAM / UPSTREAM_EVIDENCE_INVALID`：冻结的只读依赖变化。
- `GOAL_DRIFT / DRIFT`：范围或目标确实偏离。
- `ACCEPTANCE_INCOMPLETE`：机器验收未满足。
- `ARTIFACT_SPRAWL`：有强证据的产物扩散。
- `IMPLEMENTATION_PASS_ENVIRONMENT_DIRTY`：实现通过但服务运行产物变化。

默认动作随根因变化；执行失败优先 repair/retry，不再建议 prune-plan。

### 8. 运行产物被算成产品改动

**原问题**：SQLite、WAL、日志、生成缓存和 `.agent/.codex` 状态会污染 budget 或 scope。

**修改**：路径合同分为 writable、read dependency、immutable、runtime。显式 runtime
和常见 SQLite/WAL 运行产物进入环境变化；`.agent/**`、`.codex/**` 不计产品 diff。
Git 与 non-Git 都按真实内容差计算；二进制按字节与角色记录，不伪造文本行数。

### 9. 公司回执成为仪式

**原问题**：小票也要求多个角色；任意文本字段变化会使所有回执失效；提前中断与
发现产品缺陷共用 FAIL。

**修改**：

- `NONE/LIGHT` 不启用公司角色。
- `STANDARD/DEEP` 只选择有独立 deliverable 的部门，0 到 4 个自动选择；超过 4 个
  需要主线程 CEO 对精确 roster 和合同确认。
- 每个角色绑定自己消费的合同字段；只失效受变化字段影响的角色。
- COMPLETED 可自动补齐同一 agent 的 STARTED，减少重复回执。
- 失败语义区分 `PRODUCT_BLOCKER`、`REVIEW_INCOMPLETE`、`RUNTIME_FAILURE`、
  `SUPERSEDED`，调度动作不同。

回执仍是 execution claim，不是签名或独立模型运行证明。

### 10. 票据和阶段状态不同步

**原问题**：ticket PASS 后 Program Phase 仍 ACTIVE，需要人工覆盖。

**修改**：票据可绑定 `phase_completion.complete_on_pass`；通过 close 时一次性完成阶段，
并记录 completed ticket。阶段和 ticket 仍是不同层级，但退出动作由同一 close 原子完成。

### 11. 只看文件存在，不看成品质量

**原问题**：MP4/GLB/页面存在即可 PASS，无法表达视觉、音频、产品或市场质量。

**修改**：ticket 可声明 technical、artifact、product、market quality gate 及其证据类型；
缺证据返回 `NEEDS_QUALITY_EVIDENCE`。普通代码票不强制质量门，避免把所有任务变重。

### 12. 多票只能串行

**原问题**：一个项目只能一张票，前后端等独立工作也无法并行。

**修改**：同一 worktree 保持单票；不同 Git worktree 可并行不相交票据。并行前检查
依赖边、writable overlap、共享接口/数据/命名/错误/版本合同和预计净节省。收益不足或
契约不确定时保持串行。没有为了并行增加常驻审批流。

### 13. Windows Hook 引用脆弱

**原问题**：`cmd /c` 内联 Python 在嵌套、空格或中文路径中出现引号截断。

**修改**：Windows command 指向固定 `goal_compass_runtime/windows_hook.py`，参数由
`subprocess.list2cmdline` 生成，并启用 `-X utf8`；不再使用 `python -c`。

### 14. 状态写入与单体耦合

**原问题**：主脚本同时负责状态覆盖、目录解析、Hook 启动、监督决策和业务命令。

**修改**：开始按稳定边界增量拆分：

- `goal_compass_runtime/state_store.py`：原子写、独占创建、JSONL、短锁。
- `goal_compass_runtime/validation_catalog.py`：目录缓存与命令解析。
- `goal_compass_runtime/supervision.py`：纯净收益决策。
- `goal_compass_runtime/windows_hook.py`：跨平台固定启动入口。

主脚本仍然偏大，这是尚未清零的维护债务。本轮没有做一次性重写，而是先拆最常被
修改、最容易引起回归、且能立即降低运行成本的边界。后续 scanner、company catalog、
ticket contract、CLI 只有在对应行为再次修改时继续迁出，避免复制逻辑的假重构。

## 没有采纳或没有当作本轮缺陷的建议

### 1. 不新增 HMAC、签名、board 或 security control plane

Goal Compass 是执行纪律工具，不是安全沙箱。加入这些会增加流程与虚假权威感，
不能解决当前交付效率问题。

### 2. 不授予 Janitor 删除或移动权限

准确率提升不等于可以自我提权。所有候选仍由人或后续明确票据处理。

### 3. 不把 CAD、GPU、SolidWorks 资源锁硬编码进通用内核

保留通用 runtime checkpoint、PID/port/prompt/checkpoint/hash 字段和“无 kill 权限”原则。
具体资源租约需要宿主运行时提供真实所有权，不能由文本票据伪造。

### 4. 不加入 11,000 文件的重复 `status` 回归

旧故障根因已经由 `status` 不调用 evaluate 的直接测试锁定。再次构造大型目录只增加
测试时间，不能提供新的失效模式，违背净收益规则。

### 5. 中文源码乱码不是插件缺陷

原始 UTF-8 码点正确；观察到的问题来自 PowerShell 5 显示解码。本轮只在 Windows
固定启动器使用 `-X utf8`，没有改写正确源文本。

### 6. 暂不声称是可靠安全控制平面

当前有原子写、revision 检查、状态锁、append-only Hook 事件和并发回归，但还不是
完整事件溯源数据库。产品定位继续是 Goal Orchestration Harness；execution claim
不能被描述成密码学 evidence。

## 当前推荐行为

1. 未显式接入：不运行 Goal Compass。
2. 只读或微型低后果改动：`NONE`，直接改并跑普通验证。
3. 小而有返工风险：`LIGHT`，只保留范围与机器验收。
4. 有真实跨职能风险：`STANDARD`，角色和 Janitor 都按信号启用。
5. 高后果、并行集成或产品质量任务：`DEEP`。
6. `status` 只读缓存；`check` 和 `prune-check` 按需运行；`close` 是最终验收权威。

这套行为的目标不是“少做所有流程”，而是让每个被启用的流程都能明确说明它减少了
哪一种返工、误判或交付风险。
