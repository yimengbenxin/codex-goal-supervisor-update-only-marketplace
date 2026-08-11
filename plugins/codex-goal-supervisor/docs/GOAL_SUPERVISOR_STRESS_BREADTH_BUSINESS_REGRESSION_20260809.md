# Codex Goal Supervisor 压力、广度与业务回归报告

日期：2026-08-09  
发布候选：`2.1.1+codex.20260809134453`

## 结论

本轮发现并修复了两个并发 P0。修复后，运行时压力、跨行业广度、包装制造深度、核心业务链、完整 verification 和 selftest 均通过。插件可以继续人工旁观长任务试跑，但完整历史 verification 仍超过 60 秒目标，不能宣称测试基础设施性能完全达标。

## 测试原则

- 未启用项目的普通工作必须静默，不要求票据。
- 只有确定性破坏边界保持硬阻断。
- North Star、Goal Return、验收、Janitor 和反馈隐私按实际状态验证，不以文档声明代替。
- 重型压力场景独立运行，不塞进每次日常 unittest，避免测试本身成为流程税。
- 所有临时项目相互隔离；反馈上传保持关闭；没有执行删除或破坏性清理。

## 压力测试

### Goal Return 并发与有界状态

场景：24 个 session、每个 40 个临时分支周期、8 个并发 worker，共 2,880 次状态操作。

结果：

- 总耗时：50.3327 秒。
- Goal Return 阶段：45.4593 秒。
- 单事件延迟：P50 130.103 ms、P95 174.702 ms、P99 189.997 ms、最大 270.451 ms。
- 最终保留 16 个 session、512 个 interrupt、512 条事件、161,784 bytes。
- 每个 session 最多 32 个 interrupt，且最多一个活动分支。
- 所有保留分支均 CLOSED，没有 Stop 丢失。
- 第一次复活仅上下文、第二次 warning、第三次才 `needs_judge=true`。
- 无关路径不触发旧分支复活。
- compact 恢复上下文不超过 1,400 字符。
- 测试 token 未出现在状态或事件日志中。

### 非 Git 大仓库 status

场景：11,561 个工作区文件、179,743 bytes validation catalog，连续三次冷进程 `status`。

结果：

- 延迟：386.175 ms、315.919 ms、356.705 ms。
- 最大延迟：386.175 ms。
- 默认输出：1,651 字符。
- 没有全仓审计、没有自动启动 ticket、没有读取每个文件时重复解析 catalog。

### 未启用项目和确定性边界

- 普通 `src/small_fix.py` 修改：静默放行。
- `git reset --hard`：拒绝。
- 直接写 `.agent/current_ticket.json`：拒绝。
- feedback capture：启用本地记录。
- feedback upload：关闭。
- delivery：`local_outbox_only`。

机器结果：[runtime-pressure-20260809.json](../verification/results/runtime-pressure-20260809.json)

## 广度测试

### 跨行业

18 个领域通过：量化、视频、几何/CAD、医疗、法律、教育、供应链、机器人、知识服务、游戏、科学计算、隐私、设计、工业 IoT、金融风险、内容运营、农业、能源。

验证内容：

- 用户项目目标不被 Goal Supervisor 自身文件污染。
- 当前核心产物保持 `PROTECTED`。
- 复制 North Star 文本的历史文件不会仅靠词重叠获得强保护。
- 写满目标词的 RBAC/provider marketplace 文件仍不能变成 `PROTECTED`。

### 长跑行业

16 个领域通过连续 bounded ticket 生命周期：航空、制药、水务、应急、保险、酒店、交通、博物馆、半导体、电信、建筑、港口、采矿、固废、渔业、地产。

验证内容：正常票关闭、重范围请求路由、validation 失败语义、软预算、同轴疲劳、CEO 扩编约束、Janitor MARK_ONLY、North Star 保持。

### 包装制造

14 个包装领域通过：硬塑、软膜、瓦楞、折叠纸盒、模塑纤维、布袋、金属食品罐、气雾罐、玻璃、无菌复合纸盒、标签印刷、木质运输包装、保护泡棉、可堆肥包装。

验证内容：不同工序不会被误判为同轴疲劳；食品接触、密封、耐压、序列化等高后果验收能正确提高判断强度；ERP/MES/WMS/RBAC/供应商市场不会因包装术语被误保。

## 业务回归

- 显式启用和未启用项目隔离：通过。
- 安装/升级不覆盖用户 README、AGENTS、tests：通过。
- 反馈默认本地、不上传，只有显式确认才可上传：通过。
- 普通小任务无需 ticket：通过。
- DRAFT、空验收、未运行 validation、validation 失败语义：通过。
- request gate 的重范围与小验收补强：通过。
- Janitor 保护核心、拒绝弱词误保、默认 MARK_ONLY：通过。
- onboard-scan 全仓扫描并忽略插件自身内容：通过。
- 大读取分层本地记录、compact 后按需恢复：通过。
- Goal Return 临时插话关闭、连续 compact 后不复活：通过。
- Windows 固定 Hook 启动、嵌套项目路由和 Observer 竞争回退：通过。

## 本轮发现并修复的问题

### P0-1：事件日志饱和后丢 Stop

原因：Goal Return 达到 512 条事件后，每个新 Hook 都在生命周期锁内读取并重写整份 JSONL。8 路并发时锁等待超过 350 ms，`Stop` fail-open，临时分支残留 OPEN/CLOSE_CANDIDATE。

修复：事件日志是诊断投影而非权威状态。达到硬上限后停止重复重写已饱和日志；紧凑权威状态继续更新。这样保留 512 条上限，同时不让诊断日志阻塞生命周期转换。

### P0-2：锁交接竞态导致 JSON 损坏和 lost update

原因：旧锁使用文件 nonce，释放阶段存在检查后再 unlink 的竞态；同 PID 线程还共享同一个临时文件名。竞争写者可能删掉后继锁，导致两个写者同时替换状态文件。

修复：

- 跨进程使用原子 `mkdir` 作为锁占用点。
- `owner.json` 缺失的短窗口视为初始化，不误判 stale。
- stale 锁先原子 rename 到唯一隔离路径，再清理，不会碰到新锁。
- 同一进程按锁路径增加 `RLock`，阻止线程共享 PID 临时文件竞态。
- 新增 320 次并发 revision 增量和饱和 Goal Return 并发关闭回归。

## 实际验证结果

```text
python3 -m unittest -q verification.tests.test_goal_compass
Ran 380 tests in 138.365s
OK

python3 -m unittest discover -s verification/tests -v
Ran 380 tests in 142.063s
OK

python3 assets/governor-harness/.agent/selftest/test_goal_compass.py
Goal Compass selftest OK
real 0.88s
```

最终 ZIP 解压后独立复验：

```text
plugin validator: PASS
skill validator: PASS
py_compile: PASS
module suite: Ran 380 tests in 137.453s, OK
discover suite: Ran 380 tests in 140.597s, OK
selftest: Goal Compass selftest OK, real 0.91s
fresh install: README preserved, no root AGENTS, no legacy governor
fresh install feedback: upload_enabled=false, local_outbox_only
```

单独业务矩阵：

```text
test_cross_domain_benchmark: 1 test, 2.933s, OK
test_long_run_industry_stress: 1 test, 6.701s, OK
test_packaging_manufacturing_stress: 1 test, 5.817s, OK
test_plugin_hook: 33 tests, 28.807s, OK
```

## 残余风险

1. 完整历史 suite 仍为 138-142 秒，未达到旧的 60 秒目标。功能稳定性通过，但测试基础设施性能不合格项仍存在。
2. 极端“每轮都新建临时分支”的压力下单事件 P50 为 130 ms；普通未启用编辑是静默路径，不承担这项开销，但高并发 Goal Return 仍有继续优化空间。
3. 事件日志饱和后优先保护权威状态和执行延迟，不再无限保留后续低价值诊断事件；具体错误仍由 feedback outbox 独立保存。
4. 本轮是确定性本地矩阵，没有声称等价于数小时真实模型自主执行。下一阶段仍应对照观察“插件开/关”两组真实长任务的完成时间、返工率和人工纠正次数。
