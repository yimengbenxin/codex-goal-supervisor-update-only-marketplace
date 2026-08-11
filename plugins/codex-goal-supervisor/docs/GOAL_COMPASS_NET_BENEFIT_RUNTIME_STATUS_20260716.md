# Goal Compass 净收益版运行状态

日期：2026-07-16

## 版本与加载状态

- 源版本：`0.1.0+codex.20260715185556`
- 本机安装缓存：`~/.codex/plugins/cache/personal/codex-goal-supervisor/0.1.0+codex.20260715185556`
- 源 `goal_compass.py` SHA256：`8b8f6bcf39810b42b573373a70580af50f5743162a8a40c8863c7d97cf0974ca`
- 缓存 `goal_compass.py` SHA256：`8b8f6bcf39810b42b573373a70580af50f5743162a8a40c8863c7d97cf0974ca`
- 缓存包含：`state_store.py`、`validation_catalog.py`、`supervision.py`、`windows_hook.py`
- 全局插件 Hook：`{"hooks": {}}`，默认不介入未显式安装的项目。

## 源目录验证

### Python 编译

```text
python3 -m py_compile ...
exit 0
```

覆盖主脚本、四个 runtime 模块、安装器、plugin hook、verification helper 和 selftest。

### Module suite

```text
python3 -m unittest -q verification.tests.test_goal_compass
Ran 252 tests in 49.548s
OK
```

### Discover suite

```text
python3 -m unittest discover -s verification/tests -v
Ran 252 tests in 58.411s
OK
```

### Selftest

```text
python3 assets/governor-harness/.agent/selftest/test_goal_compass.py
Goal Compass selftest OK
real 0.62s
```

### 真实反馈场景矩阵

```text
python3 verification/scenarios/run_feedback_matrix.py \
  --json-out docs/GOAL_COMPASS_NET_BENEFIT_STRESS_20260716.json
12 / 12 scenarios passed
duration 11.033s
```

场景覆盖运行时 SQLite 归因、普通 `complete` 文本、CSS/反向断言、验证缓存、
上游证据失效、中英文请求等价、artifact quality、阶段完成、non-Git 行差、聚合
preflight、紧凑输出和 change-request routing。

## 结构校验

```text
validate_plugin.py: Plugin validation passed
quick_validate.py: Skill is valid
```

系统 Python 本身没有 PyYAML；校验时只在 `/tmp` 使用临时 PyYAML，不向插件或用户
Python 环境写依赖。

## ZIP 解压副本验证

最终代码包解压到独立临时目录后，从解压根目录运行：

```text
python3 -m unittest -q verification.tests.test_goal_compass
Ran 252 tests in 55.569s
OK

python3 -m unittest discover -s verification/tests -v
Ran 252 tests in 54.761s
OK

python3 assets/governor-harness/.agent/selftest/test_goal_compass.py
Goal Compass selftest OK
real 0.65s
```

三项均低于既定 60s / 60s / 20s 门槛。下方最终归档仅同步了这组运行证据，
没有再改产品代码、测试代码或插件元数据。

## 本轮热路径状态

- `status`：读取缓存状态，不调用完整 `evaluate()`。
- Hook：追加事件并更新紧凑计数，不在每个工具调用上运行完整评估。
- validation catalog：未变化时每进程解析一次。
- Janitor：`NONE` 不运行；`STANDARD` 先做 changed-path 廉价触发，再决定有界扫描。
- validation：首错 fail-fast；输入未变化时 close 复用通过结果。
- Windows Hook：固定脚本入口，无 `cmd /c` 内联 Python。

## 测试开销控制

核心验收场景没有删除或 skip。测试夹具只链接/复制必要 runtime `.py` 文件，排除
`__pycache__` 和 `.pyc`；故障注入测试继续使用可写独立副本。subprocess 统一设置
UTF-8、禁写 bytecode，并保留进程组超时终止。

## 已知边界

- 这是 Goal Orchestration Harness，不是安全控制平面。
- company receipt 是 execution claim，不是签名证明。
- Goal Janitor 仍只有 MARK_ONLY 权限。
- 主脚本仍偏大；本轮先迁出高频和高耦合边界，没有声称单体债务已经清零。
- 新缓存对新任务生效；已经打开的旧任务不保证热加载新版 skill/hook。
