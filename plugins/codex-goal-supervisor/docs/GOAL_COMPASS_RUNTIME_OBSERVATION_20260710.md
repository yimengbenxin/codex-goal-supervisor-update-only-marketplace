# Goal Compass 真实任务运行观察

日期：2026-07-10

本文件只记录运行表现，不把修复说明和产品任务结果混在一起。

## 观察样本

- Windows GLB/产品建模长任务。
- Windows AI 视频生成长任务。
- macOS 量化 AI 长任务。
- macOS Agent Registry / Skill Hub 长任务。

## 观察到的插件行为

### 产品建模任务

- 项目原始目标是“产品几何操作系统”，自动检测却缩成“GLB 生成器”。
- 清理员按错误摘要重扫后，把大量主线文档列为 `BACKLOG_CANDIDATE`。
- 清理 ticket 同时要求清理 `scripts` 缓存、又禁止 `scripts/**`，ACTIVE 后才暴露矛盾。
- GBK 控制台打印字符失败，清理逻辑本身未损坏，但执行被无关编码问题打断。
- agent 开始设计移动/冷档方案，说明旧权限边界仍可能把误判转成文件动作。

### AI 视频任务

- 已确认目标包含赚钱优先、官方梯队和当前 P0 阶段，检测器只产出“AI 自动视频生成系统”。
- 两者被判 `MISMATCH`，导致 ticket 启动前产生错误阻断。
- 修正后 Janitor 未删除文件，并继续围绕 P0 adapter 契约生成 bounded ticket；这证明硬 acceptance 有价值，但目标检测误差会先制造额外流程。

### 量化任务

- 旧版把金融“多市场”归一化成 marketplace。
- 父目录任务没有加载子项目 repo hook，`tool_calls` 长期为 0。
- 后台行情/日志变化污染非 Git diff，验收 37/37 通过的 ticket 仍被打成 DRIFT。
- 这些问题已在前一轮修复，详见 `QUANT_GOAL_COMPASS_RUNTIME_OBSERVATION_20260710.md`。

### Agent Registry 任务

- 插件能推动 bounded ticket、validation 和 UI 交付。
- plugin cache 更新后，正在运行的旧任务仍引用已删除的旧 hook 绝对路径，连续报 `goal_hook.py` 不存在。
- 源码更新与 Codex 实际加载的 cache 副本不是同一个状态，必须 cachebuster 重装并在新任务验证。

## 运行结论

Goal Compass 的 hard acceptance、ticket budget 和 request gate 已能约束单票执行；当前主要风险集中在目标摘要覆盖、Janitor 证据过弱、跨平台输出以及旧任务热更新。

因此本轮策略是：

1. Janitor 固定 `MARK_ONLY`；
2. 先修目标原文优先和证据分级；
3. 用跨行业盲测记录误伤/误保；
4. 未达到统计门槛前不恢复移动或删除权限。

本观察不把执行项目中的产品缺陷算作 Goal Compass 缺陷，也不向执行任务提前透露噪音文件位置。

## 2026-07-10 通用性与准确率复测

### 样本

- ChatGPT Deep Research 生成一个独立的 18 行业、378 artifact 盲测包；
- Goal Compass 只读取 blind case、North Star 合同和仓库内容；
- 隐藏 ground truth 在预测完成后评分；
- 可逆隔离试验只在临时副本中执行，正式插件保持 `MARK_ONLY`。

### 观察到的问题

1. 运行时核心仍残留 Agent Registry 专用 `compile` 分支，以及视频 permission guard 专用 `request` 文案。它们会让通用插件根据少量关键词生成某个历史产品的路径、验收和实现函数。
2. 插件自身的 `assets/governor-harness` 会被旧缓存 dispatcher 当成普通用户仓库；无 ACTIVE ticket 时，插件维护被自己的 hook 拦截。
3. cachebuster 重装会移除旧缓存目录，而已经打开的旧任务仍可能引用旧 `goal_hook.py`。缺少存在性 wrapper 时，所有后续工具调用都会报路径不存在。
4. 外部盲测的 command runner 写死 `python`，本机只有 `python3`；临时兼容 wrapper 后 18 个 command validation 全部通过。这是基准 portability 问题，不是插件判定失败。
5. `tmp/**` 被有意排除，导致 1 个 blind artifact 没进入 inventory。扩大扫描可提高测试分数，但会重新引入运行产物和性能噪音。

### 对应改动

- 删除运行时核心的所有产品专用 compile/request 分支，`compile` 只生成 task-specific DRAFT 和空 hard acceptance；
- `goal-set`、`goal-detect`、lens 和 MDCP 全部从用户目标/rough task 提取，不注入视频、GLB、量化或 Agent Registry 默认叙事；
- dispatcher 明确忽略插件模板根；plugin hook 先检查 `${PLUGIN_ROOT}/scripts/goal_hook.py` 是否存在，避免旧缓存路径消失后锁死任务；
- Janitor 采用 validation/acceptance/manifest/reference/path 的证据优先级，负向词和 North Star 词重叠不能覆盖强证据；
- `prune-apply --confirm` 只写 `.agent/quarantine_manifest.jsonl`；`--delete` 硬拒绝；
- 保留 `tmp/**` 扫描排除，并在报告中明确这一可见盲区。

### 复测结果

- 378 个 artifact：micro accuracy 96.56%，macro F1 96.65%；
- cleanup precision 100%，recall 96.15%；
- quarantine precision 100%，recall 93.42%；
- 核心误操作 0，核心误隔离 0；
- 可逆隔离副本验证 18/18 PASS；
- 恢复 18/18 PASS；
- 正式插件权限仍为 `MARK_ONLY`，没有因为该结果自动升级。
