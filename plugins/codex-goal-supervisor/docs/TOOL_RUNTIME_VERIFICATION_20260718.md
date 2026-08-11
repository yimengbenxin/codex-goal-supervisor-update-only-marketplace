# Codex Goal Supervisor: 运行验证记录

## 验证目标

验证工具版的核心不是“字段仍然存在”，而是以下行为真实成立：

1. 项目未安装时不介入。
2. 项目安装后隐性 observer 持续工作。
3. 没有 ACTIVE ticket 时普通编辑静默放行。
4. 显性 Custodian、Company、Auditor、Janitor、ticket 仍可调用。
5. 普通工作不因缺少显性能力而失败。
6. 确定性不可逆边界仍被阻断。
7. Janitor 不移动或删除产品文件。
8. status 不触发全仓扫描。
9. onboard-scan 保留大目录压力覆盖，但不重复解析同一判断上下文。
10. V1 保持冻结。

## 工具版专项测试

`verification/tests/test_v2_tool_mode.py` 覆盖：

- init 打开后台 observer，但 `visible_ticket_required=false`；
- 无 ticket 的普通 edit 无输出，同时写入 observer metadata；
- 无 ticket 时直接修改 `.agent/current_ticket.json` 仍被拒绝；
- project-authored anti-goal 只触发 warning，不产生 hidden deny；
- 三次连续失败只触发一次强提醒；
- Custodian 可推荐 ticket，但不能要求 ticket。

`verification/tests/test_plugin_hook.py` 额外覆盖：

- 项目本地 Hook 在无 ACTIVE ticket 时使用轻量 observer；
- 普通编辑保持零输出，但 observer state 实际递增；
- 控制状态直改与 destructive Git 仍被拒绝；
- ACTIVE ticket 才委托完整合同 Hook；
- 旧安装缺少轻量入口时兼容回退；
- Hook/runtime 异常 fail-open，不把工具故障伪装成产品失败。

长跑和制造业压力测试还验证：

- 普通票不需要伪造公司回执；
- CEO 扩编票仍可显式运行多角色；
- validation failure 原地修复后可重新 close；
- Janitor 对产品文件保持 MARK_ONLY；
- North Star 不被长跑过程改写。

## 性能修复

修复前，1600 个重复文本文件的显式 onboard 分类阶段约 7.1 秒，完整 onboard 测试模块约 15 秒。

修复后：

- 分类上下文只加载一次 North Star；
- main-path、allowed-subgoal、anti-goal、backlog 和 heavy-scope 结果在单文件内复用；
- path/body 负向判断基于已命中的候选，不再完整重跑所有词表；
- onboard 测试模块约 6.7 秒，且扫描上限与核心压力场景未降低。

## 最终命令

最终发布前运行：

```bash
python3 -m py_compile assets/governor-harness/.agent/goal_compass.py scripts/install_governor.py scripts/goal_hook.py verification/tests/*.py
python3 -m unittest -q verification.tests.test_goal_compass
python3 -m unittest discover -s verification/tests -v
python3 assets/governor-harness/.agent/selftest/test_goal_compass.py
```

## 源码回归结果

macOS 系统 Python 3.9 实测：

- module suite：289 tests，100.444s，OK；
- discover suite：289 tests，100.764s，OK；
- selftest：0.59s，`Goal Compass selftest OK`；
- `py_compile`：通过。

完整回归包含跨行业长跑、制造业、并发票据、非 Git 大目录、状态并发和
Windows Hook 兼容测试。普通项目 Hook 不会运行这套测试或全仓扫描。

## 本机安装

- marketplace：`personal`；
- plugin：`codex-goal-supervisor`；
- installed version：`2.0.0+codex.20260718171542`；
- installed cache 与发布源码逐文件一致；
- V1 `0.1.0+codex.20260718020826` 继续冻结并保持逐文件一致。

最终 ZIP SHA256 通过同目录 `.sha256` sidecar 与交付消息提供，避免把
压缩包自身哈希写回包内形成自引用变化。
