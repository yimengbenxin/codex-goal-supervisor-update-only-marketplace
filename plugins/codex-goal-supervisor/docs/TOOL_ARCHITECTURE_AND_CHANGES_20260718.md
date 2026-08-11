# Codex Goal Supervisor: 工具化架构与改动说明

## 结论

V1 已冻结。工具版从“默认绑定执行的 harness”改为“项目显式安装、后台隐性观察、前台能力可选调用”的工具。

至高规则：

> 每步流程必须产生净收益，不要让流程成为项目推进时的噪音和阻拦。

这次不是删除 Goal Compass 的能力，而是解除能力与普通执行之间的强绑定。

## 为什么要改

真实项目反馈集中暴露了同一个根因：V1 把本应按风险调用的能力做成了每张票的固定手续。

主要后果：

1. 普通修复也被票据、回执、状态查询和重复验证拖慢。
2. 语义不确定时直接阻断，误判成本由产品执行承担。
3. 公司角色、Custodian、Auditor、Janitor 从“可减少返工的能力”变成“必须证明已走过的流程”。
4. `status`、全仓扫描和逐文件重复解析可能让监督器本身成为性能瓶颈。
5. 运行产物、批量文件、CAD/BOM 等非代码交付容易被静态代码预算误判。
6. 清理准确率不足时，删除或移动权限会把误报放大成不可逆损失。

工具版的判断不是“默认静默”或“默认管控”，而是：后台只做低成本观察；具体能力仅在其预期收益明确大于流程成本时调用。

## 两层结构

### 隐性后台层

启用边界：只有用户明确为项目安装插件 后才启用。不会因为任务很大、线程很长或附近项目已经安装而全局触发。

隐性层持续做：

- 观察 PreToolUse/PostToolUse 的小型元数据；
- 记录读写、验证、失败次数和最多 100 个候选路径；
- 识别连续三次工具失败；
- 识别达到 50 个路径的宽写入面，并提示确认是否为合法批处理；
- 识别项目自己写入的 North Star anti-goal 或 ACTIVE ticket drift signal；
- 保存本地、可恢复、低体积状态。

实现上，项目 Hook 先进入独立的 `project_hook.py`。没有 ACTIVE ticket
时只加载 observer 与状态存储两个小模块；只有 AI 显式启动了 bounded
ticket，才委托完整 `goal_compass.py hook`。因此“后台一直观察”不等于
“每个工具调用都运行整套治理引擎”。

隐性层不做：

- 不要求 ACTIVE ticket；
- 不要求角色回执；
- 不自动跑 onboard-scan、prune-plan 或深度审计；
- 不把通用关键词猜测变成权限决定；
- 不修改 North Star、acceptance 或产品文件；
- 不把 hook 故障当作产品失败。

### 显性能力层

以下能力全部保留，由 AI 在认为能减少返工或提高交付质量时自行调用：

- North Star / `goal-set`
- Goal Custodian / `request`
- Company subagents
- Auditor / `check`、`close`
- Janitor / `prune-check`、`prune-plan`
- MDCP
- bounded ticket / `compile`、`ready`、`start`、`close`
- onboard full scan
- reuse discovery

“可选”不等于能力变弱。显式调用 bounded ticket 后，空 acceptance 仍不能 start 或 PASS，validation 失败仍不能认证为 PASS，acceptance 仍被冻结。

## 干预矩阵

| 事件 | 默认动作 | 是否中断执行 |
| --- | --- | --- |
| 普通读取、编辑、测试 | 静默记录 | 否 |
| 连续三次工具失败 | 一次强提醒，先查首个根因 | 否 |
| 宽写入面或批量产物 | 一次强提醒，确认批处理/产物声明 | 否 |
| 项目自定义 anti-goal/drift phrase | 一次强提醒 | 否 |
| 普通 ticket writable scope 外变更 | 提醒 | 否 |
| 预算或变更规模压力 | 提醒、压缩或拆分建议 | 否 |
| destructive `git reset` / `git clean` | 阻断当前动作 | 是 |
| 直接编辑 Goal Supervisor 控制状态 | 阻断当前动作 | 是 |
| ACTIVE ticket exact forbidden path | 阻断当前动作 | 是 |
| ACTIVE ticket immutable evidence path | 阻断当前动作 | 是 |
| hook 超时、异常或非零退出 | fail-open，紧凑提示一次 | 否 |

语义不确定时绝不隐藏阻断。只有可机器确定、不可逆或明确写入合同的边界才硬阻断。

## 关键改动

### 1. 普通工作不再需要 ticket

`status` 在没有 ACTIVE ticket 时返回 `continue_normal_execution`。普通编辑在 PreToolUse 中不输出文本，但会进入紧凑 observer state。

### 2. Custodian 分成隐性和显性

- 隐性：仅根据项目自己写入的 anti-goal/drift signal 给低频提醒。
- 显性：AI 可调用 `request --text ...` 获取结构化建议。

显性 Custodian 可以推荐 ticket，但 `requires_new_ticket` 为 false，不再把建议变成强制手续。

### 3. 公司角色可为零

公司模式按任务选岗。0 到 4 个角色不需要 CEO 扩编；超过 4 个仍需要主线程谨慎确认。普通实现和 PASS 不再依赖可选角色回执。专门的跨职能或高后果任务仍可显式使用全部角色能力。

### 4. Auditor 区分“报告事实”和“认证结果”

`check` 非绑定，负责报告真实状态；validation 失败仍显示 `VALIDATION_FAILED`，但不会把执行线程锁死。`close` 是显式认证动作；失败时返回 `NOT_CERTIFIED`，保留 ACTIVE ticket 供原地修复。

### 5. Janitor 降为 MARK_ONLY

Janitor 只能 KEEP、REVIEW、BACKLOG、SIMPLIFY 或标记 quarantine candidate。它不移动、不删除产品文件。全仓扫描只能显式触发，后台 observer 不调用。

### 6. Observer 状态有硬上限

Observer 只保存计数、时间、分类和最多 100 个候选路径，不保存源码内容。连续失败和宽写入提醒各只触发一次，避免监督日志反复刷屏。

项目本地 Hook 与插件分发 Hook 都 fail-open。旧项目尚未包含轻量 Hook
文件时，会兼容回退到旧入口；更新后的项目则默认走轻量入口。

### 7. 全仓扫描去除重复解析

显式 onboard-scan 仍保留 1600 个文本候选和最多 800 个 metadata-only artifact，但一次扫描只加载一次 North Star，并在分类链中复用。负向证据、主链路和 ticket 映射不再对同一文件重复计算多轮。

### 8. 隐私默认本地

反馈上传默认关闭。仅项目级明确同意后可上传脱敏诊断；只配置 endpoint 不能打开上传。网络失败永远不阻断产品执行。

## V1 冻结与 V2 隔离

- V1 源目录：`<plugin-root>/codex-goal-supervisor`
- V2 源目录：`<plugin-root>/codex-goal-supervisor`
- 插件名称：`codex-goal-supervisor`
- 主版本：`2.0.0`

V2 不覆盖 V1 源码、缓存或插件名。两个版本可以并存，但同一个项目只应安装其中一个项目级 hook。

## 明确非目标

V2 不加入 HMAC、签名账本、board approval、reverse signal、role signoff、security governor 或 MCP firewall。它不是安全 sandbox，也不假装是可靠发布控制平面。
