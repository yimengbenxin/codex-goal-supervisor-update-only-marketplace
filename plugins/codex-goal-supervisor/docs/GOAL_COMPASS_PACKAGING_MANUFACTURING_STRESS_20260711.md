# Goal Compass 包装制造业深度压力测试报告

> 历史测试报告。2026-07-12 版本已在产品编辑与 close 边界加入公司角色运行回执；下文旧限制保留为测试历史。

日期：2026-07-11

## 结论

本版已通过包装制造业的完整机器基准和多轮独立黑盒长跑，可进入真实包装项目的人工旁观连续小票试跑。

- 完整统一基准：14 个包装子行业、140 张 bounded tickets、168 个 request gate 探针，56.8923 秒，结果 `PASS`。
- 其中 112 张票按机器验收关闭为 `PASS`，14 个 validation 失败被拦截，14 个预算超限被拦截。
- 14/14 个行业的默认公司均为 4 个部门；14/14 个 8 部门关键票均先被 CEO 指纹门阻断，确认后才可启动。
- 14/14 个行业的高后果票都满足双钥匙并将战略角色路由到 `gpt-5.6-sol ultra`。
- 14/14 个行业的 North Star 保持不变。
- 14/14 个行业的 Janitor 均只标记，不移动、不删除文件。
- 14/14 组不同制造工序没有再被通用模板词误报为同轴疲劳。
- 独立 request gate 压测：7 个项目、21 张票、84 个请求；42 个当前小请求接受，28 个未来请求进 backlog，14 个重型平台请求拒绝，问题数为 0。
- 独立复杂度基准：6 个项目、72 张票；复杂度分层 72/72，包装原生高后果召回 18/18，双钥匙 ultra 18/18。

机器结果见 `docs/GOAL_COMPASS_PACKAGING_MANUFACTURING_STRESS_20260711.json`。

## 行业覆盖

统一基准覆盖：

1. PET、HDPE、PP 刚性塑料容器
2. 软包装薄膜、印刷、复合、制袋
3. 瓦楞纸板与纸箱
4. 彩盒与折叠纸盒
5. 模塑纸浆与纤维包装
6. 棉布、帆布、无纺布和编织袋
7. 金属食品罐
8. 气雾罐与压力包装
9. 食品、饮料和药用玻璃包装
10. 纸、铝、聚合物无菌复合包装
11. 标签与包装印刷
12. 木箱、托盘与运输包装
13. 泡棉、蜂窝纸和保护性缓冲包装
14. 可堆肥与生物基包装

独立子智能体还补充测试了瓶盖/泵头、药品泡罩与医疗包装、工业散装桶和 IBC、冷链温控包装、餐饮纸容器、高端礼盒、可循环包装、化妆品包装、电商邮寄包装和 Bag-in-Box 等专项场景。

## 发现并修复的问题

### 1. ERP/MES/WMS 请求可被当前工艺词“洗白”

**原问题**

重型请求同时提到当前工艺名时，旧逻辑可能把它变成 `ACCEPT_SIMPLIFIED`，并允许当前变更。例如“为当前封口工序建设完整 ERP/MES/WMS 平台”。在首轮 21 张票、84 请求中，19/21 个重型平台请求曾逃逸。

**根因**

只要重型请求与当前票据共享两个词，旧逻辑就允许“简化接受”；行业词和工艺词被误当成了当前验收映射。

**改动**

- 补充 ERP、MES、WMS、供应商市场等重型范围信号。
- 重型请求只有明确映射为“最小 permission guard”时才允许 `ACCEPT_SIMPLIFIED`。
- 普通行业词、路径词和工艺词不能为平台级请求提供合法性。

**为什么这样改**

需要保留“把完整 RBAC 简化成一个最小 guard”的合理能力，但不能把任何平台请求都自动缩写成当前实现。最终复跑 84 请求，问题数为 0。

### 2. 包装原生高后果风险大量落到 T1

**原问题**

首轮包装战略压测中，包装原生高后果召回只有 4/18。迁移污染、无菌屏障、爆破压力、玻璃脱片、微生物残留、二重卷封等真实制造风险未被识别，战略角色没有进入应有的 T2/T3。

**根因**

高后果词表偏软件安全和通用业务风险，不理解包装制造的质量与人身后果。

**改动**

- 加入食品接触迁移、无菌屏障、封口完整性、危险品包装、药品追溯、二重卷封、气雾罐爆破、玻璃热冲击、过敏原标签、ISPM-15、可燃推进剂、灭菌剂量不均等英文和中文信号。
- `global_goal` 不再直接参与子票复杂度判定，避免 North Star 中的高后果词抬高无关票据。
- 保留双钥匙：高后果只是第一钥匙；只有同时存在 prior xhigh insufficiency 证据才升级 `ultra`。

**为什么这样改**

包装制造的严重性不能依赖把行业语言改写成软件安全语言；同时也不能让整个项目因为 North Star 中有一个严重词而全票 ultra。最终召回 18/18，复杂度分层 72/72，且全局目标泄漏为 0。

### 3. 同轴疲劳在真实切轴后仍持续误报

**原问题**

标签、气雾罐、玻璃、无菌复合、木质包装和医疗包装独立测试都复现了：`recent_axis_count=1`，却因为 `label quality`、`JSON`、`must not issue a real production batch decision` 等模板文本返回 `AXIS_FATIGUE_WARNING`。

**根因**

- 旧逻辑允许“重复概念”单独触发告警，即使所有最近票据的实际轴都不同。
- 领域词、North Star 词、文件格式词和验收模板词进入了概念计数。
- 相同时间戳的 done ticket 缺少稳定的次级排序。

**改动**

- 轴加入任务专属词，不再只看目录根。
- 概念统计排除 North Star 领域词、路径根词、质量/验证/记录/JSON 等模板词。
- 删除概念为空时回退扫描整个 ticket 的错误逻辑。
- 重复概念只有在至少已有两个相同轴时才能辅助触发；`recent_axis_count=1` 不再告警。
- done ticket 按 `mtime_ns + filename` 稳定排序，概念按 `count + name` 稳定排序。

**为什么这样改**

继续追加行业停用词只能不断追词。结构上把“同轴证据”和“概念重复”重新绑定，才能既保留真正连续四张相同局部票的告警，又不惩罚合法的工序切换。

### 4. 中文 GOAL 与中文当前请求映射不足

**原问题**

瓦楞纸箱黑盒测试复现：声明式中文 `GOAL.md` 可能检测失败，中文 BCT 当前请求也可能被拒绝。

**根因**

目标和请求映射主要依赖英文 token；中文连续文本缺少稳定特征。

**改动**

- 增加中文二元词特征，并过滤常见中文虚词。
- 根目录 `GOAL.md` 提高来源权重。
- 忽略只有“North Star Goal/北极星目标”的空标题，不把标题当目标正文。

**为什么这样改**

包装制造项目大量使用中文目标和工艺描述。目标护栏若要求先翻译成英文，本身就会制造漂移。

### 5. 否定句中的未来范围误伤核心文件

**原问题**

核心文件写着“不要构建完整合规平台”时，Janitor 可能因为看到“合规平台”而把它降级为未来范围。

**根因**

范围命中没有判断短语前的否定语境。

**改动**

anti-pattern 和 future-scope 命中会检查同句中的 `do not/must not/avoid/不要/不得/禁止/避免`，只有存在非否定出现时才计为负向证据。

**为什么这样改**

`must_not_do` 是边界证据，不应反过来成为删除或 backlog 证据。

### 6. 预算硬超限给出冲突动作

**原问题**

预算硬超限时，顶层 `suggested_action=close`，MDCP 审核员却给 `required_action=abort`。

**改动**

硬 `BUDGET_EXCEEDED` 统一为 `abort`；只有干净的小幅 diff 超限保留 `DIFF_BUDGET_EXCEEDED_CLEAN -> compress_or_split`。

**为什么这样改**

同一状态不能让执行线程在 close 和 abort 之间猜测。小幅干净超限仍保留压缩/拆票的温和路径。

### 7. 非 Git 项目可在忽略目录中藏入未授权文件

**原问题**

化妆品包装两轮独立复现：票据只允许 `artifacts/COS-098.json`，但新增 `artifacts/COS-098-unallowed.json` 后，`check` 仍返回 `PASS_READY`。

**根因**

非 Git snapshot 会在显式允许某个 artifacts 文件时进入该目录，但文件级过滤仍只保留精确允许文件；同目录或子目录中的未授权文件被静默丢弃。

**改动**

- 将显式触达的 ignore root 作为完整计量边界。例如票据触达 `artifacts/...`，本票期间整个 `artifacts/**` 都纳入 baseline 和 changed-files 检测。
- `.git/.agent/.codex` 继续永久忽略，不因 ticket 配置而开放。
- 新增回归，确认未授权 sibling 会进入 `changed_files` 并触发 `DRIFT`。

**为什么这样改**

只扫描精确允许文件无法发现范围外新增物。对显式使用的产物根目录做完整计量，是非 Git 项目中 allowed-path 约束成立的最低条件。

### 8. `NEEDS_VALIDATION` 掩盖 Janitor `NOISE_RISK`

**原问题**

Bag-in-Box 两轮独立复现：普通 `check` 顶层返回 `NEEDS_VALIDATION`、退出码 0、动作只提示运行 validation；只有嵌套 `prune_check.status` 显示 `NOISE_RISK`。

**改动**

- 保留顶层 `NEEDS_VALIDATION`，不破坏“未运行 validation 不能 PASS_READY”的既有语义。
- 同时把 `suggested_action` 和 MDCP `required_action` 统一为 `prune_plan`。
- 当 prune 为 `NOISE_RISK/SHIT_MOUNTAIN` 时，普通 check 返回非零，避免只读顶层状态的自动化继续执行。

**为什么这样改**

把状态直接改成 `DRIFT` 会丢失 validation 尚未运行这一事实；继续返回 0 又会掩盖噪音。保留双重事实并用非零退出码阻止继续，是最小且不冲突的处理。

## 没有按反馈修改的项目

### `runtime_execution_verified=false` 时禁止 CLI start/close

没有在 Python CLI 中增加伪证明门。Goal Compass 能生成并冻结所需公司 roster，但无法从仓库脚本中认证 Codex 桌面的真实 `spawn_agent` 调用。若让 CLI 接受一个可手填的 `true`，只会制造更危险的自证。

当前处理：

- ticket 和 status 继续诚实输出 `runtime_binding=external_runtime_required` 与 `runtime_execution_verified=false`。
- skill 明确要求运行时实际创建子智能体；若工具不可用，主线程应在产品编辑前报告 `SUBAGENT_UNAVAILABLE`。
- 本轮测试本身实际使用了多批独立子智能体；但每个黑盒 fixture 的 CLI 仍不伪称这些外部调用属于该票据。

### `close` 复用先前成功的 validation

没有复用。`close` 重新运行 validation 是最终验收权威，避免 `check --run-validation` 后文件又被修改却沿用旧结果。重复执行是有意成本，不是缓存遗漏。

### 把 `prune-plan` 明细自动复制进 `close`

没有直接复制。`prune_plan.json` 可能属于较早状态；在没有票据/文件哈希绑定前，close 复用它会把陈旧候选伪装成当前证据。详细清单继续以 `prune-plan` 和 `quarantine_manifest.jsonl` 为准，close 只使用当下重新计算的阻断状态。

## 清理员权限结论

本轮准确率明显提高，但仍保持 `MARK_ONLY`：

- `prune-apply --confirm` 只写可逆隔离标记。
- `--delete` 继续硬拒绝。
- 不移动产品文件，不自动 unlink。
- validation、acceptance、must_do、existing core flow 和明确 North Star 核心路径继续优先保护。

原因是当前改进证明了误报可显著下降，但尚不足以证明跨所有真实项目都达到自动删除所需的精度。先提高准确率，再讨论扩大权限；不是先放权再用真实项目承担错误成本。

## 已知边界

1. 外部公司子智能体的真实执行仍由 Codex runtime 负责，Python CLI 不做不可验证的认证。
2. Axis fatigue 是下一票选择建议，不会推翻当前已经满足硬验收的有效票。
3. 包装高后果识别已覆盖本轮样本，但新行业风险仍需通过真实案例扩充，而不是把所有“安全/质量”词都升为 ultra。
4. Goal Janitor 仍无删除权。

## 验证

最终源码 SHA-256：`018e84da4990cb3d146a358a9ee1144df1bf69a455bf23f66b38175b2196ab6d`

```text
python3 -m py_compile ...
PASS, 0.10s

python3 -m unittest -q verification.tests.test_goal_compass
Ran 172 tests in 28.806s
OK, 28.88s wall

python3 -m unittest discover -s verification/tests -v
Ran 172 tests in 30.836s
OK, 30.93s wall

python3 assets/governor-harness/.agent/selftest/test_goal_compass.py
Goal Compass selftest OK, 0.30s wall
```

完整 14 行业基准保留为独立长跑命令；日常 verification 只跑一个代表行业，确保完整套件在 60 秒内稳定完成。
