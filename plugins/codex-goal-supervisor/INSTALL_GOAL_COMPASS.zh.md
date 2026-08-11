# Codex Goal Supervisor 安装与自检

## 安装

macOS / Linux：

```bash
python3 scripts/install_governor.py /path/to/repo --force
```

Windows PowerShell：

```powershell
py -3 scripts/install_governor.py C:\path\to\repo --force
```

安装只写 `.agent/**` 与 `.codex/hooks.json`，不会覆盖项目根目录 README、AGENTS 或 tests。

## 一次配置，后续自动更新插件

在解压后的插件目录运行一次：

```bash
python3 scripts/configure_plugin_auto_update.py
```

Windows PowerShell：

```powershell
py -3 scripts/configure_plugin_auto_update.py
```

它会注册 Codex 原生 Git marketplace、安装远端正式版，并建立每天一次的
低优先级更新检查。更新只写 Codex 的版本化插件缓存，不扫描或修改用户项目，
不会替换运行中对话已经加载的代码；安装新版后，新开 Codex 对话即可使用。

查看状态或立即检查：

```bash
python3 ~/.codex/goal-supervisor-updater/plugin_auto_update.py --status
python3 ~/.codex/goal-supervisor-updater/plugin_auto_update.py --force
```

Windows 对应目录是 `%USERPROFILE%\.codex\goal-supervisor-updater\`。
更换服务器时重新运行配置脚本，并传入新的
`--marketplace-url https://new-host/path.git`。关闭自动更新可运行：

```bash
python3 scripts/configure_plugin_auto_update.py --disable
```

## 两层使用方式

安装后，隐性观察层会低成本运行：普通执行保持静默；连续失败、写入面异常扩大或命中项目已明确的反目标时只给一次强提醒；仅破坏性 Git、控制状态直改、明确 forbidden/immutable 路径会被阻断。

当项目已有 confirmed Goal 时，临时用户请求会被记录为有结束条件的短分支。分支完成后，压缩恢复只把主线程带回当前 Goal，不会把已完成的最新一句话重新当成长期任务。首次疑似复活只做静默上下文校正，第二次提醒，第三次同路径复现才允许交给稀疏 LLM Judge 复核；复核不确定时不阻断。

显性能力按需调用，不强制：

```bash
python3 .agent/goal_compass.py request --text "重要的目标或范围变化"
python3 .agent/goal_compass.py check
python3 .agent/goal_compass.py prune-check
```

确实需要隔离范围或机器认证时，才使用可选票据：

```bash
python3 .agent/goal_compass.py compile rough_task.md --out .agent/tickets/pending/TICKET.json
python3 .agent/goal_compass.py ready .agent/tickets/pending/TICKET.json
python3 .agent/goal_compass.py start .agent/tickets/pending/TICKET.json
python3 .agent/goal_compass.py close
```

没有机器验收不能 start/PASS。验证失败时 `close` 返回 `NOT_CERTIFIED`，票据保持 ACTIVE，修复后可重试。公司角色、Custodian、Auditor、Janitor 都不是默认手续；Janitor 永远只标记，不移动、不删除产品文件。

反馈默认只保存在本地。只有用户对当前项目明确同意后，插件才会自动注册本机并上传脱敏后的结构化事件；用户不需要也不允许手动配置 Token。系统不提供网页、文件、ZIP 或 multipart 上传入口，注册失败或网络失败只会继续保存在本地，不阻断项目。

## 自检

```bash
python3 -m py_compile assets/governor-harness/.agent/goal_compass.py scripts/install_governor.py scripts/goal_hook.py verification/tests/*.py
python3 -m unittest -q verification.tests.test_goal_compass
python3 -m unittest discover -s verification/tests -v
python3 assets/governor-harness/.agent/selftest/test_goal_compass.py
```
