# V3 General Runtime And Goal Profile Migration Plan

Status: implementation plan derived from the verified `2.8.10+codex.20260817054749` inventory. This document does not claim V3 behavior already exists.

## 目标

把 Codex Goal Supervisor 从“实现与 Goal 模式耦合的能力集合”迁移为一个单一的通用能力核心，并在其上提供可继承的运行 Profile。General Profile 是所有显式启用项目都可调用的基础策略；Goal Profile 继承 General Profile，并只对现有 Goal 语义所需的能力提高义务、自动触发或约束等级。迁移不得复制两套实现，不得把所有能力强制化，也不得回退 `2.8.10` 已验证的 Goal 行为。

本轮北极星不是扩大治理流程，而是让同一项能力在不同 Codex 模式下按证据和净执行效益被调用。Profile 只决定能力是否可用、何时调用、是否必须、以何种强度反馈；能力实现、状态事实和验证证据保持单一来源。

## 已确认的当前事实

1. 当前仓库有 54 项已盘点能力和 35 个 CLI 命令；完整事实基线见 `docs/V3_FEATURE_INVENTORY_AND_LAYERING.md`。
2. 当前 Goal Profile 已有四类不同策略：显式启用后的强制项、满足证据前提才自动触发的项、显式可选项、只提示或仅针对确定性边界限制的项。
3. `goal-set --require-detailed`、North Star、Goal Return、阶段 Goal、路线图、偏航事件和最终 Goal 认证仍依赖已确认 Goal 状态，不能在第一次迁移中假装成完全通用能力。
4. Context Continuity、procedure memory、验证债务、运行产物过滤、低噪声 observer、状态持久化等机制不必把原生 Goal 当作必需输入，适合先进入通用层。
5. Custodian、Company、Auditor、Janitor、Ticket 和 MDCP 目前是可调用工具或内部结构，不应因为 V3 重构变成默认流程。
6. 官方 Codex app-server v2 已提供线程、turn、Goal、Skill、Hook 和事件接口。`thread/goal/set|get|clear` 表明原生 Goal 是单线程适配状态；`hooks/list`、生命周期事件、`skills/list` 与 `skills/changed` 可为通用运行层提供受支持的集成边界。

## 第一性原理

### 单一实现，策略分层

能力代码只能有一个权威实现。General Profile 和 Goal Profile 只保存策略，不复制 observer、validation、context、procedure、Custodian 或 Janitor 代码。Profile 通过 capability id 引用实现，避免普通模式与 Goal 模式形成雪花分支。

### 继承只能强化，不能暗中削弱

Goal Profile 继承 General Profile 的全部必需边界。General 中 `required` 或 `targeted_block` 的能力不能在 Goal Profile 中降级；General 中 `optional` 的能力可以在 Goal Profile 中保持可选，也可以在证据明确时提升为 `conditional` 或 `required`。

### 可用不等于必用

通用核心是能力全集，Profile 决定义务。North Star、行业专家、超复杂方案、Ticket、Company、Janitor 等可以在普通模式被调用，但不因此自动运行。任何控制若预计成本高于可避免返工，保持不启用。

### 事实状态和展示状态分离

observer、验证、Goal、Ticket 和工具状态产生事实；Profile 只解释事实并选择动作。不能由 Profile 伪造 North Star、验收、进度或完成状态。默认 `status` 读取缓存摘要，详细审计显式触发。

### Goal 兼容优先于新概念

第一次迁移先把现有 Goal Profile 原样编码成数据化策略并通过回归，再迁移通用能力。不得借 V3 重写 Goal 语义，不新增审批、签名、board、HMAC、reverse signal 或常驻多 Agent 对话。

## 官方 Codex 集成依据

- app-server v2 是新增集成的权威协议边界；不为 V3 新造一套线程控制协议。
- `thread/goal/set|get|clear` 由 Goal adapter 使用，通用核心不能要求线程存在 Goal。
- 生命周期 Hook 负责低成本、确定性的事件归一化；异步 Hook 只能提供信息，不成为第二个执行线程或隐形决策者。
- `skills/list`、显式 Skill 输入和 `skills/changed` 支持能力发现及刷新；Profile 不通过提示词假装能力已经加载。
- 项目 Hook 受 Codex 的项目配置、信任和 managed-hook 规则约束。Hook 不可用时普通执行 fail-open，并给出一次可诊断状态，不能伪造监督有效。
- 不直接复制或分叉 Codex 源码。本轮复用的是官方公开接口和生命周期边界，因此不存在需要用户确认采用的第三方直接复用候选。

## 目标架构

```text
Codex lifecycle / app-server v2 / project hooks
                    |
          shared event normalization
                    |
        universal capability registry
                    |
             policy evaluator
             /              \
    General Profile      Goal Profile
       baseline       inherits + promotions
             \              /
        one state/evidence model
                    |
      compact status + explicit tools
```

每个 capability descriptor 至少包含：

```json
{
  "id": "verification_debt",
  "implementation": "observer.verification_debt",
  "preconditions": [],
  "inputs": ["normalized_event", "project_state"],
  "outputs": ["fact", "advisory"],
  "side_effects": ["bounded_state_write"],
  "default_cost": "low",
  "failure_mode": "fail_open"
}
```

每个 Profile 只覆盖策略维度：

```json
{
  "availability": "available",
  "obligation": "optional",
  "invocation": "explicit",
  "enforcement": "none",
  "preconditions": []
}
```

Profile 合并时保留来源、最终值和提升原因，便于测试和诊断，但默认输出只显示已触发能力、原因和下一动作。

## 执行步骤

### 节点 A：冻结兼容基线

**输入：** `2.8.10` 源码、Skill、35 个命令、现有 verification、自测和 `docs/V3_FEATURE_INVENTORY_AND_LAYERING.md`。

**动作：** 把现有 Goal Profile 的强制、条件触发、可选、提醒和硬边界转换成机器可读矩阵；为每项策略关联现有测试或补充只读行为证据；记录当前入口和状态依赖。

**产出：** `goal-2.8.10` 兼容 Profile fixture、能力目录 fixture、回归映射和缺口清单。

**消费者：** Profile loader、兼容回归、后续迁移节点。

**对总目标的贡献：** 让迁移以真实行为为基线，防止凭记忆重新解释 Goal 模式。

**验收：** 54 项能力和 35 个命令全部有 owner；当前强制项与可选项没有互换；现有 suite 在零运行时代码改动时保持通过。

### 节点 B：建立单一能力注册表与 Profile 合并器

**依赖：** 节点 A 串行完成。

**输入：** 兼容矩阵、能力实现入口、状态 schema。

**动作：** 新增最小 capability descriptor、Profile schema、单调继承合并和 explain 投影；实现 `General -> Goal` 继承；拒绝未知 capability、非法降级和缺少前提却伪造结果的配置。

**产出：** 单一注册表、General baseline、Goal compatibility Profile、纯函数测试。

**消费者：** Hook dispatcher、CLI dispatcher、status 投影、Goal adapter。

**对总目标的贡献：** 用数据策略替代散落条件，同时避免复制业务实现。

**验收：** 同一 capability id 只有一个实现；General required 无法被 Goal 降级；Goal promotion 有来源；无 Profile 时保持现行兼容行为。

### 节点 C：迁移第一批模式无关能力

**依赖：** 节点 B。

**输入：** 注册表、当前 observer、context、procedure、state、validation 模块。

**动作：** 先注册低噪声事件观察、工具结果归一化、验证债务、运行产物过滤、Context Continuity、procedure memory、协作有效性、compact status 和 sparse Judge 服务。移除这些机制对 confirmed Goal 的强制读取，但允许 Goal adapter 提供额外上下文。

**产出：** 可在无原生 Goal 线程调用的 General 能力；Goal 模式继续调用同一实现。

**消费者：** 普通 Codex 线程、Goal 线程、显式工具调用。

**对总目标的贡献：** 证明通用层不是文档概念，而是一套真实共享实现。

**验收：** 非 Goal 黑盒能显式启用插件并完成普通产品任务；普通对齐工作无额外输出；Hook/Judge/状态缺失 fail-open；Goal 回归无变化。

### 节点 D：封装 Goal 专用适配器

**依赖：** 节点 B，可与节点 C 的非冲突测试并行，但接口 schema 必须先冻结。

**输入：** Goal Profile fixture、North Star、详细 Goal、阶段、Goal Return、路线和认证模块。

**动作：** 将 confirmed North Star、详细 Goal 同步、Goal replacement、阶段 Goal、Goal Return、路线事件、偏航事件和最终认证放入 Goal adapter；Profile 负责前提和提升，不改业务算法。

**产出：** Goal adapter 与兼容 Profile；原生 Goal 状态仍由官方 `thread/goal/*` 接口承载。

**消费者：** Goal 模式线程和 Goal 专用 CLI。

**对总目标的贡献：** 保住现行 Goal 能力，同时让它成为通用核心上的强化 Profile。

**验收：** 详细 Goal 文本与原生 Goal 字节、长度、SHA-256 一致；替换记录为 superseded 而非完成；阶段失败不能推进；最终认证规则不回退。

### 节点 E：处理两类通用收敛候选

**状态：已实现并晋升为 General.required。** 确定性 Hook 回归证明两类能力可在普通模式低成本运行；Goal Profile 通过继承获得相同义务，不保留第二套实现。

**依赖：** 节点 C 的共享状态模型稳定后再做，不能与底层 schema 设计并行抢跑。

**输入：** 已完成/被替换请求的证据、验证后的负向元素移除证据、compaction/session 事件。

**动作：** 分别实现“已结束请求回放抑制”和“已解决负向残留抑制”。前者只注入主任务和 tombstone 事实，不重复临时请求原文；后者仅在明确删除或模型确认反问式删除后生效，只拦标题、说明、叙述文件和后续完成摘要中的残留，不阻断普通产品代码，并允许用户显式恢复。

**产出：** 独立 capability、误报样例、黑盒证据和 Profile 晋升结论。

**消费者：** 普通模式与 Goal 模式；Goal 自动继承 General 的最终义务等级。

**对总目标的贡献：** 解决上下文压缩后重复旧请求，以及“删除东坡肉却持续留下无东坡肉注释/标题”的噪音问题，而不把每次否定都变成流程。

**验收：** 已证实完成或替换的请求不会因 recency/compaction 被重启；负向元素移除后不在代码、注释、PR 文案或说明中反复残留；真正回归或用户显式询问仍可重新呈现约束。

### 节点 F：发布前双模式真实验证

**依赖：** A-E 中实际进入本版本的节点全部完成。

**输入：** 源码候选、三种发行形态、固定真实测试线程、解压包。

**动作：** 先运行确定性测试、性能和 Windows Hook 回归；再在固定 `插件专用测试线程` 中分别运行普通模式和 Goal 模式真实黑盒；核对产物、日志和 Goal 等价性；全部通过后才允许发布。

**产出：** commit-bound 黑盒证据、回归摘要、安装包和发布决定。

**消费者：** 维护者、GitHub 使用者、自动更新客户端。

**对总目标的贡献：** 证明通用化提高可用范围且没有破坏 Goal 稳定性。

**验收：** 失败测试时发布器拒绝写网络；普通模式无强制 Goal；Goal 模式完整继承强制项；源码与解压包一致；无个人信息和生成缓存进入发行包。

## 串行、并行与依赖关系

核心串行链为 `A -> B -> C/D -> E -> F`。节点 C 与 D 只能在 capability id、Profile schema、状态 owner 和输入输出合同于 B 冻结后并行；二者不得修改同一实现入口。节点 E 依赖 C 的共享状态和证据模型，不能提前做成关键词补丁。节点 F 必须最后执行，任何失败都会回到首个可行动根因，不能先发布再补验证。

并行工作的接口合同至少冻结 capability id、输入事件 schema、事实输出 schema、状态 ownership、错误语义、Profile 合并规则和测试 fixture。不同工作区可以并行，单一工作区仍保持一个写 owner。

## 开源复用决定

本轮调研了 OpenAI Codex 官方 app-server v2、Hook 配置和仓库开发规范。结论是复用官方协议，不复制官方实现：

- 使用 `thread/goal/set|get|clear` 作为 Goal adapter 的原生状态接口。
- 使用生命周期 Hook 和 `hooks/list` 作为项目观察边界。
- 使用 `skills/list`、显式 Skill 输入和 `skills/changed` 作为能力发现边界。
- 使用官方 thread/turn 事件作为后续通用 observer 的事件来源。

没有发现一个可直接替换本插件 capability registry/Profile engine 的现成官方组件，因此该最小策略层需要在现有代码内实现。实现不得重新包装 app-server，不得私自修改 Codex 源码。

参考：

- https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md
- https://github.com/openai/codex/blob/main/docs/config.md
- https://github.com/openai/codex/blob/main/AGENTS.md

连续执行每满 24 小时，只基于北极星、当前阶段和剩余动作刷新一次复用调研；发现合适工具必须进入相应模块并通过验证，不能只增加调研文档。

## 最终验收

1. **架构真实性：** 机器检查证明每个 capability id 只解析到一个实现；General 与 Goal 只保存策略和适配器。
2. **继承正确性：** 参数化测试覆盖 optional、conditional、required 与 none、advisory、targeted_block 的单调合并，禁止 Goal 降级 General 硬边界。
3. **普通模式可用：** 无 native Goal 的隔离项目可显式启用 General Profile，完成产品写入与验证，普通对齐过程保持静默。
4. **Goal 模式不回退：** 现有 North Star、详细 Goal、Goal Return、阶段、路线、偏航、最终认证和 optional tool 语义全部通过现有及新增回归。
5. **低流程成本：** 默认 status 读取缓存摘要；普通动作不自动创建 Ticket、角色回执、Janitor 或 MDCP 输出；控制失败不阻断普通产品工作。
6. **通用候选准确：** 回放抑制和负向残留抑制必须用真实黑盒证明减少噪音，且不吞掉新指令、真实回归或用户询问。
7. **发布顺序：** py_compile、verification、selftest、性能、三发行形态和固定真实线程全部通过后，发布脚本才可执行网络写入。

## 本轮非目标

- 不做 HMAC、签名、board、reverse signal、审批流、security governor 或常驻多 Agent 聊天。
- 不把所有能力设为默认或必选。
- 不支持其他 coding 平台；先使用 Codex 官方边界完成 V3。
- 不推翻现有 Goal 算法，不在迁移时重写 Janitor、Custodian、Company 或 Ticket 产品语义。
- 不把文档、测试数量或 Profile 字段存在当成业务完成；必须由双模式真实黑盒和产品验收证明。

## 实施中发现并修复的问题

1. **Goal 替换残留旧 convergence 代际。** 旧目标的活动段、进度、检查点和提醒会与新目标同时存在。修复后以详细 Goal 指纹识别代际变化，旧状态以 `SUPERSEDED_BY_GOAL_CHANGE` 摘要归档，新 Goal 从干净的 goal-scoped 状态开始；同一 Goal 刷新不清状态。
2. **项目运行时可能落后于已加载插件。** 仅加载最新版 Skill 不等于项目 `.agent` 已更新。新增 `scripts/ensure_project_runtime.py`，显式启用时幂等比较版本和受安装器管理的整棵不可变文件树；不一致时执行保留项目状态的 `--force --no-init` 更新。
3. **单文件哈希无法证明完整运行时。** 只比较 `.agent/goal_compass.py` 会漏掉缺失的新模块、contracts、protocols 或 Hook。安装 provenance 现在记录 `managed_runtime_sha256`，文件缺失也进入树哈希并触发恢复。
4. **Goal onboarding 复用处置无法持久化。** 详细 Goal 已完成研究并有证据拒绝直接复用时，onboarding probe 仍可能留下 `choose_reuse_disposition`，而 direct-action 决策不进入项目状态。修复后 `goal-set` 把已确认的研究拒绝写入项目 reuse contract；采用或扩展候选仍必须走机器验证的集成合同。
5. **普通模式 status 会错误要求 North Star。** General Profile 项目没有 Goal 时应正常空闲，不应显示 `NEEDS_CONFIRMATION`。修复后 compact status 返回 `IDLE` 与 `general-initial`，Goal 项目在 verbose 状态展示完整兼容 Profile。
6. **Profile 摘要可能反过来制造 status 噪音。** 默认状态曾因完整复合策略摘要超过既有紧凑输出上限。修复后普通模式只显示一个 Profile id，完整 54 项统计仅在 `--verbose` 展开。

这些修复都配有确定性回归；完整双模式黑盒结果在发布前单独记录。历史完整 suite 的 60 秒目标仍是既有测试基础设施债务，不能通过删除核心场景或放宽超时伪造达标。
