#!/usr/bin/env python3
"""One-time setup for Goal Supervisor's Git-marketplace auto updater."""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import plugin_auto_update as updater


FULL_MARKETPLACE_NAME = "goal-supervisor"
UPDATE_ONLY_MARKETPLACE_NAME = "goal-supervisor-update-only"
FULL_MARKETPLACE_URL = "https://github.com/yimengbenxin/codex-goal-supervisor-marketplace.git"
UPDATE_ONLY_MARKETPLACE_URL = "https://github.com/yimengbenxin/codex-goal-supervisor-update-only-marketplace.git"
DEFAULT_MARKETPLACE_URL = FULL_MARKETPLACE_URL
LAUNCH_AGENT_LABEL = "xyz.yimengbenxin.codex-goal-supervisor-update"
WINDOWS_TASK_NAME = "Codex Goal Supervisor Update"
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def distribution_edition() -> str:
    manifest = updater.read_json(Path(__file__).resolve().parents[1] / ".codex-plugin" / "plugin.json", {})
    return str(manifest.get("distributionEdition") or "full")


def marketplace_defaults(edition: str) -> tuple[str, str]:
    if edition == "update-only":
        return UPDATE_ONLY_MARKETPLACE_NAME, UPDATE_ONLY_MARKETPLACE_URL
    return FULL_MARKETPLACE_NAME, FULL_MARKETPLACE_URL


def require_safe_url(url: str, allow_insecure_localhost: bool = False) -> None:
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if allow_insecure_localhost and parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}:
        return
    raise updater.UpdateError("Marketplace URL must use HTTPS.")


def validate_name(value: str, label: str) -> None:
    if not SAFE_NAME_RE.fullmatch(value):
        raise updater.UpdateError(f"Invalid {label}: {value}")


def configured_marketplace(payload: dict[str, Any], name: str) -> dict[str, Any] | None:
    for item in payload.get("marketplaces", []):
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return None


def configure_marketplace(codex: str, name: str, url: str) -> dict[str, Any]:
    listed = updater.run_codex_json(codex, ["plugin", "marketplace", "list"])
    existing = configured_marketplace(listed, name)
    existing_source = ((existing or {}).get("marketplaceSource") or {}).get("source")
    changed = bool(existing and existing_source != url)
    if changed:
        updater.run_codex_json(codex, ["plugin", "marketplace", "remove", name])
        existing = None
    if existing is None:
        added = updater.run_codex_json(codex, ["plugin", "marketplace", "add", url])
        if added.get("marketplaceName") != name:
            raise updater.UpdateError(
                f"Remote marketplace declared {added.get('marketplaceName')!r}, expected {name!r}."
            )
    return {"marketplace_reconfigured": changed, "marketplace_url": url}


def install_remote_plugin(codex: str, plugin: str, marketplace: str, edition: str) -> dict[str, Any]:
    result = updater.run_codex_json(codex, ["plugin", "add", f"{plugin}@{marketplace}"])
    version = str(result.get("version") or "")
    installed_path = str(result.get("installedPath") or "")
    if not version or not installed_path:
        raise updater.UpdateError("Codex did not return the installed plugin version and cache path.")
    verified = updater.verify_install(installed_path, plugin, version, marketplace, edition)
    return {"version": version, **verified}


def remove_duplicate_installations(codex: str, plugin: str, keep_marketplace: str) -> list[str]:
    payload = updater.run_codex_json(codex, ["plugin", "list"])
    removed: list[str] = []
    for entry in payload.get("installed", []):
        if not isinstance(entry, dict) or entry.get("name") != plugin:
            continue
        marketplace = str(entry.get("marketplaceName") or "")
        if not marketplace or marketplace == keep_marketplace:
            continue
        selector = f"{plugin}@{marketplace}"
        updater.run_codex_json(codex, ["plugin", "remove", selector])
        removed.append(selector)
    return removed


def stable_updater_path() -> Path:
    return updater.updater_home() / "plugin_auto_update.py"


def copy_stable_updater() -> Path:
    source = Path(__file__).with_name("plugin_auto_update.py")
    target = stable_updater_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(".py.tmp")
    shutil.copy2(source, temp)
    os.chmod(temp, 0o700)
    temp.replace(target)
    return target


def schedule_environment(codex_cli: str | None = None) -> dict[str, str]:
    environment = {
        "CODEX_HOME": str(updater.codex_home()),
        "GOAL_SUPERVISOR_UPDATER_HOME": str(updater.updater_home()),
    }
    if codex_cli:
        environment["CODEX_CLI_PATH"] = codex_cli
    return environment


def mac_launch_agent_payload(
    python: str,
    script: Path,
    hour: int,
    minute: int,
    interval_hours: int,
    codex_cli: str | None = None,
) -> dict[str, Any]:
    return {
        "Label": LAUNCH_AGENT_LABEL,
        "ProgramArguments": [python, str(script), "--scheduled"],
        "RunAtLoad": True,
        "StartCalendarInterval": {"Hour": hour, "Minute": minute},
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "Nice": 10,
        "ThrottleInterval": 3600,
        "StandardOutPath": str(updater.updater_home() / "update.log"),
        "StandardErrorPath": str(updater.updater_home() / "update-error.log"),
        "EnvironmentVariables": schedule_environment(codex_cli),
    }


def install_macos_schedule(
    python: str,
    script: Path,
    hour: int,
    minute: int,
    interval_hours: int,
    codex_cli: str | None = None,
) -> str:
    path = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".plist.tmp")
    with temp.open("wb") as stream:
        plistlib.dump(
            mac_launch_agent_payload(python, script, hour, minute, interval_hours, codex_cli),
            stream,
            sort_keys=False,
        )
    temp.replace(path)
    domain = f"gui/{os.getuid()}"
    updater.run_process(["launchctl", "bootout", f"{domain}/{LAUNCH_AGENT_LABEL}"], timeout=10)
    result = updater.run_process(["launchctl", "bootstrap", domain, str(path)], timeout=10)
    if result.returncode != 0:
        raise updater.UpdateError(f"launchctl bootstrap failed: {(result.stderr or result.stdout).strip()}")
    return str(path)


def windows_task_command(python: str, script: Path, hour: int, minute: int) -> list[str]:
    action = f'"{python}" "{script}" --scheduled'
    return [
        "schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", f"{hour:02d}:{minute:02d}",
        "/TN", WINDOWS_TASK_NAME, "/TR", action,
    ]


def install_windows_schedule(python: str, script: Path, hour: int, minute: int, interval_hours: int) -> str:
    result = updater.run_process(windows_task_command(python, script, hour, minute), timeout=20)
    if result.returncode != 0:
        raise updater.UpdateError(f"schtasks setup failed: {(result.stderr or result.stdout).strip()}")
    return WINDOWS_TASK_NAME


def install_linux_schedule(
    python: str,
    script: Path,
    hour: int,
    minute: int,
    interval_hours: int,
    codex_cli: str | None = None,
) -> str:
    user_root = Path.home() / ".config" / "systemd" / "user"
    user_root.mkdir(parents=True, exist_ok=True)
    service = user_root / "codex-goal-supervisor-update.service"
    timer = user_root / "codex-goal-supervisor-update.timer"
    environment_lines = f'Environment="CODEX_HOME={updater.codex_home()}"\n'
    if codex_cli:
        environment_lines += f'Environment="CODEX_CLI_PATH={codex_cli}"\n'
    service.write_text(
        "[Unit]\nDescription=Update Codex Goal Supervisor\n\n"
        "[Service]\nType=oneshot\n"
        f'ExecStart="{python}" "{script}" --scheduled\n'
        + environment_lines,
        encoding="utf-8",
    )
    timer.write_text(
        "[Unit]\nDescription=Daily Codex Goal Supervisor update check\n\n"
        "[Timer]\n"
        f"OnCalendar=*-*-* {hour:02d}:{minute:02d}:00\nPersistent=true\nRandomizedDelaySec=1800\n\n"
        "[Install]\nWantedBy=timers.target\n",
        encoding="utf-8",
    )
    for command in (["systemctl", "--user", "daemon-reload"], ["systemctl", "--user", "enable", "--now", timer.name]):
        result = updater.run_process(command, timeout=20)
        if result.returncode != 0:
            raise updater.UpdateError(f"systemd user timer setup failed: {(result.stderr or result.stdout).strip()}")
    return str(timer)


def install_schedule(
    python: str,
    script: Path,
    hour: int,
    minute: int,
    interval_hours: int,
    codex_cli: str | None = None,
) -> dict[str, str]:
    if sys.platform == "darwin":
        return {
            "scheduler": "launchd",
            "schedule": install_macos_schedule(python, script, hour, minute, interval_hours, codex_cli),
        }
    if os.name == "nt":
        return {"scheduler": "windows_task_scheduler", "schedule": install_windows_schedule(python, script, hour, minute, interval_hours)}
    return {
        "scheduler": "systemd_user_timer",
        "schedule": install_linux_schedule(python, script, hour, minute, interval_hours, codex_cli),
    }


def disable_schedule() -> dict[str, Any]:
    if sys.platform == "darwin":
        path = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCH_AGENT_LABEL}.plist"
        updater.run_process(["launchctl", "bootout", f"gui/{os.getuid()}/{LAUNCH_AGENT_LABEL}"], timeout=10)
        path.unlink(missing_ok=True)
        return {"scheduler": "launchd", "removed": str(path)}
    if os.name == "nt":
        updater.run_process(["schtasks", "/Delete", "/F", "/TN", WINDOWS_TASK_NAME], timeout=20)
        return {"scheduler": "windows_task_scheduler", "removed": WINDOWS_TASK_NAME}
    timer = Path.home() / ".config" / "systemd" / "user" / "codex-goal-supervisor-update.timer"
    updater.run_process(["systemctl", "--user", "disable", "--now", timer.name], timeout=20)
    timer.unlink(missing_ok=True)
    timer.with_suffix(".service").unlink(missing_ok=True)
    return {"scheduler": "systemd_user_timer", "removed": str(timer)}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configure automatic Codex Goal Supervisor updates.")
    parser.add_argument("--marketplace-url")
    parser.add_argument("--marketplace-name")
    parser.add_argument("--plugin-name", default=updater.PLUGIN_NAME)
    parser.add_argument("--codex-cli")
    parser.add_argument("--interval-hours", type=int, default=24)
    parser.add_argument("--hour", type=int, default=9)
    parser.add_argument("--minute", type=int, default=30)
    parser.add_argument("--disable", action="store_true")
    parser.add_argument("--allow-insecure-localhost", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not 0 <= args.hour <= 23 or not 0 <= args.minute <= 59 or args.interval_hours < 1:
        raise SystemExit("Invalid schedule or interval.")
    try:
        if args.disable:
            config = updater.read_json(updater.config_path(), {})
            config["enabled"] = False
            updater.atomic_write_json(updater.config_path(), config)
            result = {"status": "DISABLED", **disable_schedule()}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0

        edition = distribution_edition()
        default_name, default_url = marketplace_defaults(edition)
        marketplace_name = args.marketplace_name or default_name
        marketplace_url = args.marketplace_url or default_url
        require_safe_url(marketplace_url, args.allow_insecure_localhost)
        validate_name(marketplace_name, "marketplace name")
        validate_name(args.plugin_name, "plugin name")
        codex = updater.find_codex(args.codex_cli)
        marketplace = configure_marketplace(codex, marketplace_name, marketplace_url)
        installed = install_remote_plugin(codex, args.plugin_name, marketplace_name, edition)
        removed = remove_duplicate_installations(codex, args.plugin_name, marketplace_name)
        stable = copy_stable_updater()
        config = {
            "schema_version": 1,
            "enabled": True,
            "plugin_name": args.plugin_name,
            "marketplace_name": marketplace_name,
            "marketplace_url": marketplace_url,
            "interval_hours": args.interval_hours,
            "codex_cli": codex,
            "stable_script": str(stable),
            "distribution_edition": edition,
        }
        updater.atomic_write_json(updater.config_path(), config)
        schedule = install_schedule(
            sys.executable,
            stable,
            args.hour,
            args.minute,
            args.interval_hours,
            codex,
        )
        result = {
            "status": "CONFIGURED",
            **marketplace,
            **installed,
            **schedule,
            "removed_duplicate_installations": removed,
            "check_interval_hours": args.interval_hours,
            "new_sessions_use_updated_versions": True,
        }
        updater.atomic_write_json(updater.state_path(), {
            "status": "UP_TO_DATE",
            "installed_after": installed["version"],
            "last_successful_check_at": updater.iso_time(),
            "restart_required": True,
        })
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except updater.UpdateError as exc:
        print(json.dumps({"status": "CONFIGURATION_FAILED", "error": str(exc)}, ensure_ascii=False, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
