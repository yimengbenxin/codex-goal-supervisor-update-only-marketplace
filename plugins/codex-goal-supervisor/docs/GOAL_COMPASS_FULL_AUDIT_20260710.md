# Goal Compass 全流程审计、修复原因与权限策略

日期：2026-07-10

## 结论

真实长任务截图证明，旧版 Goal Janitor 的判断准确率不足以拥有移动或删除权限。本轮先把能力固定为 `MARK_ONLY`，再改目标识别和证据层级，并建立跨行业盲测。当前版本不会移动、覆盖或删除被清理员标记的项目文件。

这不是新增安全 Governor，也没有加入 board、HMAC、签名、reverse signal 或审批流。

## 真实运行中发现的问题

### 1. 确认过的富目标被自动摘要覆盖

GLB 项目的原目标是“产品几何操作系统”，视频项目的原目标还包含赚钱优先、官方梯队和当前 P0 阶段。旧检测器把它们分别压缩成“GLB 生成器”和“AI 自动视频生成系统”，再把摘要与原目标判成 `MISMATCH`。

影响：清理员基于错误摘要重新扫描，正确的长期设计、历史证据和阶段文档会被当成偏离。

### 2. 通用插件仍内置少数产品模板

旧 `goal-set` 会按视频、GLB、Agent Registry、量化等领域补写固定 `main_path`、`allowed_subgoals` 和 backlog。即使示例 ticket 已从安装包删除，这些内置产品假设仍会污染用户目标。医疗、法律、供应链等未知行业只能返回 `UNKNOWN`。

### 3. Janitor 同时存在误杀和误保

- 单个词或两个普通领域词重叠，就可能把主线文档标成未来范围。
- 噪音文件只要抄写 North Star、acceptance 或 `must_do` 的几个词，又可能被误判为 `PROTECTED/KEEP`。
- `files_not_changed`、文件名、注释文本和真实依赖关系没有清楚区分证据强度。

第一轮 18 行业盲测确实复现了第二类问题：历史文件抄写 North Star 后，因为与 `must_do` 有两个普通词重叠，被错误判成 `KEEP`。

### 4. 清理动作的风险高于判断能力

旧接口保留 `--delete`，真实任务也开始设计移动/归档脚本。即使带 `--confirm`，模型仍可能基于自己的误判执行破坏性动作。

### 5. ticket 路径合同会自相矛盾

真实清理 ticket 要处理 `scripts/__pycache__`，同时又把 `scripts/**` 整体放入 forbidden。ACTIVE 后 acceptance 冻结，只能失败重建，产生额外流程噪音。

### 6. Windows 输出与运行命令不稳定

Windows/GBK 控制台打印私用区字符失败；Windows hook 没有强制 UTF-8。插件热更新时，旧任务还可能继续调用已被 cachebuster 删除的绝对 hook 路径。

### 7. 插件源码里的嵌入式 harness 会被全局 hook 当成用户项目

在插件源码目录维护 `assets/governor-harness` 时，旧 dispatcher 会找到其中的 `.agent/goal_compass.py`，然后以“没有 ACTIVE ticket”为由阻止插件自身更新。

## 本轮改成了什么

### A. North Star 保留用户原文

- `goal-set` 不再按任何行业自动填视频、GLB、量化或 Agent Registry 模板。
- `main_path` 只保存用户确认原文及其明确分句。
- `allowed_subgoals`、`anti_goals`、`backlog_domains` 默认为空，只有用户或项目合同明确提供时才存在。
- `GOAL.md` 是最高优先级目标源并永远受保护。
- 已确认的富目标与同领域简略检测结果只会得到 `PARTIAL/continue_with_confirmed_north_star`，不会让摘要推翻原目标。
- 两个明确不同的已知领域仍会返回 `MISMATCH`。

### B. goal-detect 改为文档驱动的通用检测

- 优先读取 `GOAL.md`、项目 README 和 `product/**` 中用户实际写下的目标句。
- 原文候选优先于领域摘要，不再替用户重新定义 North Star。
- 医疗、法律、教育、供应链等没有硬编码模板的项目也能从项目文档识别目标。
- 没有可靠目标句时仍返回 `UNKNOWN`，不编造目标。
- RBAC、provider marketplace、security gateway 等未来范围文件只能进入 noise/backlog evidence，不能成为目标 supporting evidence。

### C. Janitor 改成证据分级

强保护只来自：

1. `GOAL.md` 等确认目标源；
2. 精确 validation / acceptance 文件；
3. 当前 ticket 的完整强任务锚点；
4. existing core flow；
5. North Star core path；
6. 项目锚点文档加 North Star 映射；
7. North Star 映射和真实引用链同时存在。

负向判断分三档：

- 单一负向信号：`REVIEW_REQUIRED`；
- 负向词与真实引用链冲突：`REVIEW_REQUIRED`；
- 多个独立负向类别或明确负向路径：`QUARANTINE_CANDIDATE`。

抄写 North Star 文本但没有合同、核心路径或引用链，只能得到 `REVIEW_REQUIRED`，不能 `PROTECTED`。

每个扫描项现在附带 `evidence_tier` 和 `janitor_action_limit=MARK_ONLY`，便于后续按真实标签统计，而不是让模型自报准确率。

### D. 清理员权限固定为 MARK_ONLY

- `prune-apply --confirm` 只向 `.agent/quarantine_manifest.jsonl` 写入原路径、SHA256、大小、理由、信号和时间。
- 文件留在原路径，`file_moved=false`、`file_deleted=false`。
- `prune-apply --confirm --delete` 硬拒绝并返回非零状态。
- Janitor 代码不存在项目文件删除路径；安装器只保留迁移旧官方示例的定向清理。

### E. ticket、Windows 与 hook 修复

- `ready/start` 会拒绝 allowed path 被 forbidden path 完整覆盖的 ticket。
- Windows CLI、validation 和 dispatcher 使用 UTF-8/replace 输出；repo hook 使用 `py -3 -X utf8`。
- plugin hook 通过存在性检查 fail-open：旧任务所指向的 cache 路径在热更新中消失时，不再反复报错。
- dispatcher 忽略插件自身的 `assets/governor-harness` 模板根。
- 用户项目安装包仍不包含产品示例 ticket、根目录 README/AGENTS/tests 或 legacy governor。

## 准确率验证

### 内置跨行业回归

verification 覆盖量化、AI 媒体、产品几何、医疗、法律、教育、供应链、机器人、企业知识、游戏、科学计算、隐私数据、设计生产、IoT、金融风控、内容、农业和能源等不同目标。重点固定以下不变量：

- 精确验收、validation manifest 和真实引用链优先保护；
- 只抄写 North Star 的内容不能因此得到 `PROTECTED`；
- RBAC、marketplace、未来平台等负向内容不能靠几个主线词伪装成核心文件；
- 同路径、同 basename、历史回归和结构化 manifest 的证据不能被关键词多数票覆盖。

### 外部盲测

使用独立生成的 `AI Agent Goal Drift Benchmark v1.0.0` 运行 18 个行业仓库、378 个 artifact。预测时只读取 blind cases、仓库内容和 North Star 合同，随后再用隐藏 ground truth 评分。

基线版本：

- micro accuracy：40.48%；
- macro F1：27.61%；
- cleanup candidate precision：92.98%；
- cleanup candidate recall：40.77%；
- quarantine precision：32.73%；
- core action false positives：4；
- core quarantine false positives：2。

当前版本：

- micro accuracy：96.56%；
- macro F1：96.65%；
- cleanup candidate precision：100%；
- cleanup candidate recall：96.15%；
- quarantine precision：100%；
- quarantine recall：93.42%；
- core action false positives：0；
- core quarantine false positives：0；
- same-axis drift discovery/action：54/54；
- command-level reversible quarantine validation：18/18 PASS；
- restore：18/18 PASS。

评分器的总 gate 仍为 false，因为唯一的 `tmp/customer_export/workbench_a.json` 按插件既有规则不参与递归扫描，导致 path-identity trap 为 17/18。没有为了追求分数而重新递归扫描 `tmp/**`，因为那会恢复已知的性能和运行产物污染风险。

高分不等于可以放权。本轮 blind set 仍属于有限样本，而且清理分类存在对基准表达风格过拟合的可能，因此正式能力继续固定为 `MARK_ONLY`。

## 权限升级门槛

当前固定：`MARK_ONLY`。

未来只有独立盲测同时满足以下条件，才讨论 `REVERSIBLE_QUARANTINE`：

- 两轮互不重叠盲测；
- 至少 600 个原始隔离建议和 600 个真值负例；
- 至少 6 个行业，每行业至少 50 个建议；
- 单侧 95% 置信下界 precision >= 99.5%；
- FPR 上界 <= 0.5%；
- 核心/验收文件误伤为 0；
- 隔离后 validation 退化上界 <= 0.5%；
- 恢复成功率 100%；
- 模型越权尝试为 0。

即使达到门槛，也只能由模型不可写的外部执行器做可逆隔离。模型可以主动降权，不能给自己升权。

永久删除不属于 Goal Compass 常驻能力。若未来讨论删除，应在至少 3,000 个建议/负例、30 天隔离无访问、precision 下界 99.9%、核心误伤 0 的基础上，由资产所有者逐批明确授权并由外部执行器完成。

## 当前边界

- 18 行业/54 工件是回归集，不是统计意义上的放权证据。
- 轻量引用图不能可靠发现反射、运行时加载、外部消费者和法律保留义务；这类冲突必须保持 `REVIEW_REQUIRED`。
- 外部 scanner 只能提供候选信号，不能提高权限。
- onboard-scan 仍有扫描上限以避免在超大仓库卡死；核心路径与高权重文档优先，真实大仓库仍需继续采样观察。
