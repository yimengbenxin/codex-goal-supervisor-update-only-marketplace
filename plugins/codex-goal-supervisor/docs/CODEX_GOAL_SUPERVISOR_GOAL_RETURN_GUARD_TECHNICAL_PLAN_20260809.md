# Codex Goal Supervisor: Goal Return Guard 技术方案

日期：2026-08-09

状态：设计定稿，尚未实现

## 1. 问题定义

Codex Goal 模式存在一类已被多个用户复现的问题：运行中的 Goal 收到一次性用户补充后，该补充即使已经完成，仍可能在自动 compaction、恢复或后续 continuation 中再次成为当前任务。连续压缩会进一步强化这个局部任务，导致 Agent 重复执行已经结束的需求，长期偏离原始 Goal。

这个问题不是普通的关键词漂移，也不等于“模型忘了目标”。当前 Codex compaction 会优先保留最近的用户消息，并把它们重新写为普通 `user` 消息；系统没有为临时 steer 保存 `OPEN/CLOSED` 生命周期，也没有保存完成后的返回游标。因此聊天历史承担了不适合它承担的任务调度职责。

## 2. 第一性原理

1. 对话历史不是任务状态。
2. Compaction 是有损转换，不能成为“当前应该做什么”的权威来源。
3. 长期 Goal、临时分支、持久约束和 Goal 替换必须是不同类型。
4. 临时分支必须有退出条件、完成状态和返回点。
5. 已完成分支不能因为消息仍存在于历史中而重新变成活跃任务。
6. 插件介入必须比可能避免的返工更轻，不能在每条消息上增加 LLM 调用、票据或用户确认。
7. Codex Goal Supervisor 只能补偿 Codex 当前行为，不能宣称从插件层修复 Codex 内部 replacement history。

## 2.1 产品身份与升级前置条件

- 对外插件名称和唯一身份始终是 `Codex Goal Supervisor` / `codex-goal-supervisor`。
- `2.x` 只表示内部和语义版本，不进入插件名称、市场 ID、安装目录身份或用户命令。
- 下一次实现必须从旧 V1 的同一个插件身份发布，使正常升级覆盖旧运行代码、hook、skills 和文档。
- 升级必须保留项目拥有的 North Star、Goal contract、backlog、用户 validation catalog 和反馈隐私选择。
- 如果设备曾安装临时的 `codex-goal-supervisor-v2` 独立身份，迁移器必须移除它的重复 hook/缓存入口，确保同一项目只有一套 observer 运行。
- 发布验收必须验证从 V1 原地升级，而不只是全新安装。

## 3. 初版独立方案

在查阅外部工具前，初版方案定义为一个轻量 `Goal Return Guard`：

- `UserPromptSubmit` 记录用户输入相对于当前 Goal 的作用域。
- 临时输入形成有生命周期的 interrupt，而不是直接成为新的 Goal。
- `Stop` 在存在临时 interrupt 时判断其是否已经满足退出条件。
- `PreCompact` 固化当前 Goal 返回点。
- Compaction 后恢复当前 Goal、当前阶段和下一动作，并明确标记已经关闭的 interrupt。
- 确定性判断优先，仅在真正模糊且将影响后续写入时调用稀疏 LLM Judge。
- 第一次发现旧分支复活时只修正上下文；只有重复且高置信的错误方向写入才进入定向限制。

该方案不新增用户命令，不要求 ticket，不创建多 Agent 编排器，不上传数据。

## 4. 外部方案调研

### 4.1 LangGraph

参考：<https://docs.langchain.com/oss/python/langgraph/interrupts>

可借鉴：

- Checkpointer 保存 thread-scoped graph state。
- `thread_id` 是恢复指定 checkpoint 的游标。
- Interrupt 与 resume 是显式状态，不依赖聊天叙事。
- 恢复时必须考虑幂等性，不能重复执行 interrupt 之前的副作用。

不采用：

- 不把 Codex Goal Supervisor 改造成 LangGraph 应用。
- 不引入数据库 checkpointer、图节点和新的 Agent runtime。

结论：吸收“明确 checkpoint、interrupt、resume cursor 和幂等恢复”，不引入框架。

### 4.2 Microsoft Magentic-One / Agent Framework

参考：

- <https://microsoft.github.io/autogen/dev/user-guide/agentchat-user-guide/magentic-one.html>
- <https://learn.microsoft.com/en-us/python/api/agent-framework-core/agent_framework.standardmagenticmanager>

可借鉴：

- Task Ledger 保存整体事实和计划。
- Progress Ledger 判断任务是否完成、是否停滞、是否循环。
- 多次停滞后重新规划，而不是继续重复相同动作。

不采用：

- 不为每一轮生成 LLM Progress Ledger。
- 不新增 orchestrator 或公司审批流。

结论：只吸收“任务状态与对话历史分离”和“重复复活计数”。

### 4.3 OpenAI Agents SDK Sessions

参考：

- <https://openai.github.io/openai-agents-python/sessions/>
- <https://openai.github.io/openai-agents-js/guides/sessions/>

可借鉴：

- Session history 与 resumable `RunState` 分离。
- 恢复未完成 run 时传回原 `RunState`，而不是追加一条新的用户消息。
- 可通过 session input callback 控制新输入与历史如何合并。
- Compaction 可以在 turn 之间或 idle 时显式运行，避免和正在执行的 turn 混在一起。

不采用：

- Codex Goal Supervisor 不接管 Codex 的 Responses session。
- 不新建第二套模型会话代理 Codex Desktop。

结论：吸收“恢复运行状态，不用新 user message 模拟恢复”的原则；当前 Codex hook API 做不到完全等价，只能近似补偿。

### 4.4 Vercel eve

参考：<https://github.com/vercel/eve/blob/main/docs/concepts/default-harness.md>

可借鉴：

- Compaction 明确区分已完成工作和剩余工作。
- 上一个 checkpoint 与普通 transcript 分开保存。
- Compaction 后重新注入 active todo，而不是让摘要自行推断当前任务。
- 工具状态由 harness 独立保存。

结论：这是最接近本问题的已实现做法。V2 应重新注入“当前 Goal checkpoint + 未完成动作”，不能重新注入完整历史。

### 4.5 Claude Code hooks 与社区 handoff 工具

参考：

- <https://code.claude.com/docs/en/hooks>
- <https://github.com/Sonovore/claude-code-handoff>
- <https://github.com/Haustorium12/continuity-v2>

可借鉴：

- `PreCompact` 前保存状态。
- Compaction 后重新加载短状态文件。
- 当前任务文件面向“下一窗口要做什么”，而不是记录完整历史。

不采用：

- 不在每次 `UserPromptSubmit` 中注入“请更新状态文件”的指令。
- 不要求模型持续维护 60 至 120 行 handoff 文档。
- 不把小型社区项目直接作为生产依赖。

原因：逐消息注入会成为新的上下文和流程税，并可能进一步放大本项目要解决的问题。

### 4.6 Codex Deterministic Session Checkpoint RFC

参考：<https://github.com/openai/codex/issues/8573>

可借鉴：

- 使用 rollout 事件生成确定性 checkpoint，而不是依赖叙事摘要。
- Checkpoint 有固定容量、稳定排序和 evidence hash。
- 事实依赖的文件变化后标记为 `SUSPECT`。
- Compaction 后只注入稳定的 checkpoint view。

必须拒绝的一点：

- RFC Phase 0 提议从“最后一条用户消息”生成 task pointer。对于本问题，这正是错误来源。Codex Goal Supervisor 的 task pointer 必须来自已确认 Goal contract 和显式生命周期事件，不能来自最后一条普通消息。

### 4.7 Temporal / CrewAI Flows

它们提供 durable workflow、持久状态和恢复能力，但需要引入新的运行时、工作流定义和存储层。对一个项目内 Codex 插件而言，收益不足以抵消复杂度，因此不作为依赖，只保留“事件日志 + 投影 + 幂等恢复”的工程思想。

## 5. 调研后的最终架构

### 5.1 定位

Goal Return Guard 是 Codex Goal Supervisor 的一个后台补偿器，只在以下条件同时成立时启用：

1. 项目已显式安装并启用 Codex Goal Supervisor。
2. 项目存在 confirmed North Star 和详细 Goal contract。
3. 当前 Codex session 处于 active Goal 模式或已有 Goal continuation 证据。

普通未启用项目、无 Goal 对话和短任务完全不触发。

### 5.2 状态文件

新增两个 project-local runtime 文件：

```text
.agent/runtime/goal_return/events.jsonl
.agent/runtime/goal_return/state.json
```

`events.jsonl` 是有界 append-only 事件源，最多保留最近 512 条结构化事件。`state.json` 是可重建投影，用文件锁和原子替换写入。两者默认不上传。

不保存：

- 完整聊天正文
- 隐藏推理
- 文件内容
- 用户长期画像
- 跨项目记忆

### 5.3 Goal generation

每次显式创建或替换 Goal 时生成新的 `goal_generation_id`：

```json
{
  "goal_generation_id": "sha256(north_star + goal_contract + revision)",
  "north_star_hash": "...",
  "goal_contract_hash": "...",
  "current_stage": "...",
  "next_action": "...",
  "open_acceptance_ids": [],
  "checkpoint_revision": 12
}
```

旧 generation 的 interrupt 永远不能约束新 Goal。这个规则专门处理旧 steer 跨 `/goal clear` 或新 Goal 继续污染的问题。

### 5.4 Interrupt 类型

```text
QUESTION_ONLY
TEMPORARY_BRANCH
GOAL_CONSTRAINT_UPDATE
GOAL_REPLACEMENT_REQUEST
UNSCOPED
```

状态：

```text
OPEN
CLOSE_CANDIDATE
CLOSED
PROMOTED_TO_CONSTRAINT
SUPERSEDED
```

`GOAL_REPLACEMENT_REQUEST` 不能自动改写项目 North Star。它只提醒主线程使用 Codex Goal UI 或显式 `goal-set` 完成替换。

### 5.5 Interrupt 记录

```json
{
  "interrupt_id": "int_...",
  "goal_generation_id": "...",
  "prompt_hash": "...",
  "intent_type": "TEMPORARY_BRANCH",
  "state": "OPEN",
  "sanitized_summary": "检查当前导出错误",
  "scope": "until_stop_or_exit_condition",
  "exit_condition": "回答问题或完成指定局部修复",
  "return_checkpoint_revision": 12,
  "affected_paths": [],
  "created_turn_id": "...",
  "closed_turn_id": null,
  "replay_count": 0,
  "evidence_refs": []
}
```

`sanitized_summary` 最多 240 字符，仅保存在本项目；raw prompt 不写入该状态文件。

## 6. Hook 接入

### 6.1 UserPromptSubmit

作用：记录新输入相对于当前 Goal 的候选类型。

要求：

- 确定性规则执行，目标开销低于 20 ms。
- 不在每条消息调用 LLM。
- 不默认阻止用户输入。
- 问号、一次性措辞、显式“临时/先/本轮”等只能作为信号，不作为唯一结论。
- 显式“从现在开始/以后都/替换总目标”标记为持久候选，但仍不能静默改写 North Star。
- `UNSCOPED` 默认不做阻断，等待 Stop 和后续行为提供证据。

### 6.2 Stop

仅在存在 `OPEN` interrupt 时工作。

- `QUESTION_ONLY` 在一次正常回答后直接进入 `CLOSED`。
- 有明确 exit condition 且已有对应工具/验证证据时进入 `CLOSED`。
- 无法确定时进入 `CLOSE_CANDIDATE`。
- 只有 `CLOSE_CANDIDATE` 将影响下一轮写入时，才允许调用一次稀疏 LLM Judge。
- Judge 失败、超时或不可用时 fail-open，不阻断工作。

Stop hook 不反复创建 continuation prompt。只有确认旧分支复活且即将继续写入时，才允许创建一次短的 Goal-return continuation。

### 6.3 PreCompact

- 原子写入当前 root checkpoint。
- 固化所有 `OPEN/CLOSED` interrupt 状态。
- 记录 `compaction_seq`。
- 不阻止正常 compaction。
- 不调用 LLM。

### 6.4 PostCompact

Codex 当前 `PostCompact` 不提供模型可见的 `additionalContext` 通道，因此这里只记录 compaction 完成和状态，不承担恢复注入。

### 6.5 SessionStart(source=compact)

这是 Codex 当前真正可用的恢复注入点。官方 hook 行为保证 root session compaction 后、下一次模型请求前执行。

注入内容必须低于 300 tokens：

```text
[GOAL RETURN CHECKPOINT]
Active goal generation: <id>
Current stage: <stage>
Next unfinished action: <action>
Closed temporary branches: <bounded summaries>
Do not resume a CLOSED branch unless the user explicitly reopens it.
Continue from the current stage and validate against the stored Goal contract.
```

只注入当前 Goal、当前阶段、下一动作和最多 3 个最近关闭分支。不得注入完整事件账本、MDCP、票据历史或全文 Goal。

### 6.6 PreToolUse / PostToolUse

只用于检测“已关闭分支是否重新主导真实动作”：

- 路径和命令与 CLOSED interrupt 的 affected paths 精确重合，同时不服务当前 next action，记一次 replay。
- 只有自然语言相似而无路径或行为证据时，不得阻断。
- 第一次：静默追加 Goal-return context。
- 第二次：一条强提醒。
- 第三次且 LLM Judge 高置信确认：只限制该错误分支的写入面；读取、测试、修复和对齐工作继续。

## 7. 稀疏 LLM Judge

LLM Judge 不是常驻监控器，只允许在以下组合条件触发：

1. 当前存在 `CLOSE_CANDIDATE` 或已关闭分支疑似复活。
2. 确定性证据无法确认。
3. 下一动作将修改产品文件或消耗明显资源。

输入只包含：

- North Star 和 Goal contract 的哈希及短摘要
- 当前 stage / next action
- interrupt 的 sanitized summary、状态和退出条件
- 即将执行的动作摘要和路径
- 已有机器证据

输出：

```text
RETURN_TO_GOAL
CONTINUE_BRANCH
PROMOTE_CONSTRAINT_CANDIDATE
INSUFFICIENT_EVIDENCE
```

Judge 无权替换 Goal，无权修改 North Star，无权批准删除。

## 8. 纠偏策略

这不是全局三次失败阻断，而是按同一个 `interrupt_id + goal_generation_id` 计数：

1. 第一次复活：恢复上下文，不显示流程。
2. 第二次复活：显示一条简短强提醒，要求返回当前 stage。
3. 第三次复活：若存在行为证据且 Judge 高置信确认，只阻止错误分支对应的写入；继续允许对齐工作。

当用户明确重新打开该分支时，生成新的 interrupt revision，不把合理新指令当成复发。

## 9. 与现有 Codex Goal Supervisor 的关系

复用现有能力：

- `state_store.py` 的文件锁和原子写入。
- `llm_judge.py` 的超时、只读沙箱、缓存和 fail-open。
- `context_continuity.py` 的 `PreCompact` seal 与 `SessionStart(source=compact)` 恢复入口。
- North Star 和详细 Goal contract 的双层目标结构。
- 后台 advisory-first 和定向 rail 语义。

必须修改：

- 安装 hooks 增加 `UserPromptSubmit` 和 `Stop`。
- 新增独立 `goal_return.py`，不要继续把逻辑堆进 `goal_compass.py`。
- `project_hook.py` 仅负责路由事件。
- `SessionStart(source=compact)` 合并 read-continuity 和 Goal-return 两种短上下文，设统一 token 上限。

不修改：

- Ticket、Janitor、Custodian 和 Company roles 的默认触发逻辑。
- 用户项目 North Star。
- Codex 本身的 compaction 历史。

## 10. 已知限制

1. 插件不能删除 Codex replacement history 中已经复活的 user message。
2. Codex `PostCompact` 当前不能直接注入模型上下文，恢复依赖 `SessionStart(source=compact)`。
3. Hook 事件没有稳定暴露 Codex Goal UI 的完整 objective 和 revision。插件只能以项目内 confirmed North Star + Goal contract 作为权威目标；用户直接在 UI 改 Goal 后，需要有稳定同步事件才能完全自动识别。
4. Stop 的 `last_assistant_message` 不能单独证明任务完成，所以重要分支关闭必须结合工具或验收证据。
5. 语义相似度永远不能成为定向阻断的唯一依据。

建议向 Codex 上游提出：

- 为 hook 输入增加 `active_goal_id`、`goal_revision`、`goal_hash`。
- 为 steer 增加 scope 和 consumed/closed 状态。
- Compaction 排除已消费 steer，或保留其 lifecycle metadata。
- 允许 `PostCompact` 返回模型可见的 bounded `additionalContext`。

## 11. 测试计划

### 11.1 Codex 行为复现矩阵

```text
queue / steer
× 无 compaction / 单次 compaction / 连续三次 compaction
× question / temporary branch / persistent constraint / goal replacement
× branch completed / branch incomplete
```

每个场景记录：

- Goal continuation 是否恢复。
- 已完成分支是否被重新执行。
- 重复执行发生在哪次 compaction。
- Hook 事件顺序。
- 额外上下文 token 和延迟。

### 11.2 必须新增的验证

```text
test_question_closes_after_one_stop
test_temporary_branch_closes_with_exit_evidence
test_closed_branch_survives_three_compactions_without_replay
test_persistent_constraint_is_not_auto_closed
test_goal_replacement_requires_explicit_goal_change
test_old_generation_interrupt_does_not_affect_new_goal
test_session_start_compact_injects_bounded_return_checkpoint
test_postcompact_does_not_claim_context_injection
test_ambiguous_prompt_does_not_call_judge_without_write_risk
test_third_replay_blocks_only_affected_wrong_direction_paths
test_hook_failure_is_fail_open
test_goal_return_state_is_atomic_under_concurrent_hooks
test_goal_return_context_stays_under_300_tokens
```

### 11.3 成功标准

- 临时需求完成后连续三次 compaction 不再执行该需求。
- 正式持久约束在 compaction 后仍然有效。
- 新 Goal 不继承旧 Goal 的临时 steer。
- 普通 follow-up 不增加可感知流程。
- 无 interrupt 时 hook 只做一次小文件读取或直接返回。
- `UserPromptSubmit` 确定性路径 P95 小于 20 ms。
- `PreCompact` / `SessionStart(compact)` P95 小于 100 ms。
- 默认注入小于 300 tokens。
- Judge 调用率低于 active Goal 用户消息的 5%，并通过真实回放继续下调。

## 12. 分阶段实现

### 发布前置：统一插件身份并完成原地升级

- 将 manifest、marketplace 条目、skill 命名空间、安装器和公开文档统一为 `codex-goal-supervisor`。
- 使用 `2.x` 语义版本发布，但不把 `v2` 写入插件身份或产品名称。
- 从已安装 V1 原地升级时替换旧 runtime、hooks、skills 和工具文档。
- 保留项目拥有的 North Star、Goal contract、backlog、validation catalog 和反馈隐私选择。
- 检测并移除临时 `codex-goal-supervisor-v2` 身份造成的重复 hook/observer；迁移后只允许一个插件身份生效。
- 在解压包和真实缓存安装路径中分别验证 V1 升级、临时双身份去重和全新安装。

### Phase 0：只做真实复现

- 在当前 Codex 版本运行行为矩阵。
- 保存最小可复现 rollout 和 hook 顺序。
- 不改当前运行逻辑。

### Phase 1：被动事件账本

- 新增 `goal_return.py`、事件日志和投影。
- 记录 Goal generation 和 interrupt 生命周期。
- 不注入、不提醒、不阻断。

### Phase 2：Compaction 恢复

- `PreCompact` seal。
- `SessionStart(source=compact)` 注入短 checkpoint。
- 对 CLOSED interrupt 添加 tombstone。
- 仍不阻断。

### Phase 3：稀疏完成判断

- Stop 自动关闭 `QUESTION_ONLY`。
- 有证据的 TEMPORARY_BRANCH 自动关闭。
- 模糊且高成本的情况调用 Judge。

### Phase 4：重复复活定向限制

- 只有真实回放证明两次轻量恢复仍无效后才启用。
- 第三次只限制错误分支写入面。

每个 Phase 都必须独立 A/B 测量额外延迟、token、误判和减少的重复工作。如果某一阶段未产生净收益，不进入下一阶段。

## 13. 最终采用决定

不直接引入 LangGraph、AutoGen、Agent Framework、CrewAI、Temporal、eve 或社区 handoff 工具。

采用的组合思想：

- LangGraph：显式 interrupt + checkpoint + resume cursor。
- Magentic-One：任务账本与重复停滞计数。
- OpenAI Agents SDK：run state 与 session history 分离。
- eve：compaction 后重新注入 active todo。
- Codex DSC RFC：确定性、有界、证据化 checkpoint。
- Claude Code hooks：PreCompact 保存、compact-session 恢复。

最终实现保持为一个本地、显式启用、低延迟、无新用户命令的 Goal Return Guard。它解决的是“已完成临时分支被 compaction 复活”，不是扩建通用 Agent OS，也不是接管 Codex 的任务执行。
