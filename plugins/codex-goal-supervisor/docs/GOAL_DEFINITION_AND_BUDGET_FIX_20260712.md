# Goal Compass 目标结构与预算修复说明

## 1. 发现的问题

### 1.1 自动进入 Goal 模式后，目标文本过于松散

旧逻辑主要把 `goal-set --text` 的整段文本保存为 `goal`，再按逗号、句号和换行拆成 `main_path`。这只能完成文本切分，不能证明系统真正识别了：

- 用户到底要解决什么问题；
- 哪些是不可被局部需求推翻的第一性原理；
- 需要执行哪些产品动作；
- 最终要交付什么；
- 什么证据才算真正完成；
- 哪些只是工具、监控方式或非目标。

因此，自动生成的 Goal 容易把对话摘要、工具要求和产品目标混在一起。

### 1.2 已有 North Star 可能被初始化逻辑“修复”

旧 `init` 会调用兼容性刷新逻辑。在某些旧合同命中特定标记时，它会重建 `main_path`、`allowed_subgoals` 等字段。即使原始 `goal` 文本没变，项目已有的 Goal 合同仍可能被工具自动改写。

这与“已有目标由项目所有，工具只能读取”的原则冲突。

### 1.3 所有 compile ticket 默认使用同一个 300 行预算

旧 `compile` 固定生成：

```json
{
  "max_minutes": 30,
  "max_tool_calls": 40,
  "max_changed_files": 5,
  "max_diff_lines": 300
}
```

这会把单文件修复和 adapter / pipeline / integration 初稿当成同一种任务。新适配器初稿超过 300 行时，即使没有功能错误、没有越界、没有 drift，也会触发 `DIFF_BUDGET_EXCEEDED_CLEAN`。

## 2. 新的 Goal 生成规则

普通的长周期、多步骤、持续性交付任务仍然允许自动进入 Goal Compass。变化不在于“是否自动进入”，而在于“进入时必须写出什么”。

### 2.1 没有 confirmed North Star 时

主线程先把当前对话展开成可执行的 Goal 蓝图，而不是压缩成摘要：

```text
source_requirements
precise_goal
problem_statement
current_state
desired_state
stakeholders
first_principles
concrete_actions
process.entry_conditions
process.nodes[].inputs/actions/outputs/exit_criteria/dependencies
process.completion_conditions
deliverables
final_acceptance[].criterion/evidence/validation_method
constraints
non_goals
assumptions
open_questions
```

然后一次性写入 `.agent/north_star_goal.json` 的 `goal_definition`。

其中：

- `goal` 只写产品结果，不写 Goal Compass、插件、监控、subagent 或 ticket 流程；
- `first_principles` 至少两条，每条包含原理、理由和落地影响；
- `process.nodes` 至少两个，每个节点明确输入、动作、产出和退出条件；
- `deliverables` 明确名称、格式、消费者和单项验收；
- `final_acceptance` 明确最终标准、证据和验证方法；
- 工具和执行方法只进入执行层，不进入 North Star。

### 2.2 已经存在 confirmed North Star 时

默认行为是：

```text
读取
校验
报告缺项
继续复用
```

禁止自动：

```text
覆盖
补写
规范化
重排 main_path
添加 first_principles
以“修复旧格式”为名重建 Goal
```

`init` 和重复 `goal-set` 都不会修改已有 confirmed Goal。重复 `goal-set` 返回：

```text
EXISTING_GOAL_PRESERVED
required_action = reuse_existing_goal
```

只有用户明确要求替换 North Star 时，才允许使用 `--replace-existing`。

## 3. 结构化 goal-set 示例

```bash
python3 .agent/goal_compass.py goal-set \
  --text "精确产品目标" \
  --definition-file /path/to/goal-definition.json \
  --require-detailed
```

`--require-detailed` 会拒绝缺少节点产出、节点退出条件、交付物消费者或最终验收证据的短目标。为了兼容旧脚本，只传 `--text` 仍可保存，但会标记：

```text
goal_definition.quality = TEXT_ONLY
```

并列出 `missing_fields`。自动启用流程不得使用这种弱格式。

## 4. 新的 ticket 预算逻辑

`compile` 不再统一写死 300 行，而是根据 rough task 的长度、显式路径数量和任务形态选择一个 bounded 档位：

| 档位 | 典型任务 | 分钟 | 工具调用 | 文件 | diff 行 |
|---|---|---:|---:|---:|---:|
| `MICRO_BOUNDED` | 单文件、小断言、重命名、小修复 | 20 | 25 | 3 | 180 |
| `STANDARD_BOUNDED` | 普通 bounded ticket | 40 | 50 | 6 | 500 |
| `INTEGRATION_BOUNDED` | adapter、pipeline、interface、schema、integration | 45 | 60 | 8 | 800 |
| `BROAD_BOUNDED` | 明确跨模块但仍可作为一个 bounded ticket 的任务 | 60 | 80 | 12 | 1500 |

每张 DRAFT ticket 新增 `budget_basis`，记录：

- 选择的档位；
- 触发信号；
- 建议范围；
- 实际机器执行上限；
- “只能在 DRAFT 阶段调整，ACTIVE 后不能静默扩容”的规则。

因此，“当前票据变更预算触发上限，主要是新适配器初稿过长，不是功能错误”的准确含义是：

1. 代码没有命中 forbidden path 或 drift；
2. 机器验收不一定失败；
3. 只是产品 diff 超过当前 ticket 的 `max_diff_lines`；
4. 正确动作是压缩或拆票，或者在 `ready` 前依据 `budget_basis` 修正 DRAFT 预算；
5. 不能在 ACTIVE 阶段为了让结果通过而临时抬高上限。

## 5. 可观察输出

`status` 现在会显示：

- North Star 是否 confirmed；
- `goal_definition` 的结构质量和缺失字段；
- 当前 ticket 的 `budget_limits`；
- 当前 ticket 的 `budget_basis`；
- 已有目标的 preservation policy。

MDCP Layer 1 也会读取：

- `goal_definition_quality`；
- `problem_statement`；
- `first_principles`；
- `goal_deliverables`；
- `goal_success_criteria`。

这些是结构化判断字段，不是审批流。

## 6. 回归保护

新增测试覆盖：

- `init` 不改已有 confirmed Goal；
- 重复 `goal-set` 默认保留旧 Goal；
- 新 Goal 能保存第一性原理、动作、交付物和成功标准；
- 只传文本时明确标记为 `TEXT_ONLY`；
- adapter 初稿获得 integration bounded budget；
- 单文件小修复仍保持小预算；
- `status` 输出目标结构和保留策略；
- skill 允许自动进入 Goal Compass，但要求结构化新目标并保留旧目标。
