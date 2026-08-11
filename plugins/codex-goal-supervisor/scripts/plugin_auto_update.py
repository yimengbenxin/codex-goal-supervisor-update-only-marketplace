#!/usr/bin/env python3
"""Low-noise updater for the Codex Goal Supervisor plugin.

The updater delegates marketplace refresh and plugin installation to the Codex
CLI. It never edits a user project or an active plugin cache in place.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


PLUGIN_NAME = "codex-goal-supervisor"
MARKETPLACE_NAME = "goal-supervisor"
DEFAULT_TIMEOUT_SECONDS = 120
LOCK_STALE_SECONDS = 15 * 60
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-[0-9A-Za-z.-]+)?(?:\+codex\.(\d{14}))?$")


class UpdateError(RuntimeError):
    pass


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def iso_time(value: dt.datetime | None = None) -> str:
    return (value or utc_now()).isoformat()


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def updater_home() -> Path:
    override = os.environ.get("GOAL_SUPERVISOR_UPDATER_HOME")
    return Path(override).expanduser() if override else codex_home() / "goal-supervisor-updater"


def config_path() -> Path:
    return updater_home() / "config.json"


def state_path() -> Path:
    return updater_home() / "state.json"


def lock_path() -> Path:
    return updater_home() / "update.lock"


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temp, 0o600)
    temp.replace(path)


def find_codex(explicit: str | None = None) -> str:
    candidates = [
        explicit,
        os.environ.get("CODEX_CLI_PATH"),
        shutil.which("codex"),
        "/Applications/Codex.app/Contents/Resources/codex",
        "/Applications/ChatGPT.app/Contents/Resources/codex",
    ]
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            candidates.extend([
                str(Path(local) / "Programs" / "Codex" / "codex.exe"),
                str(Path(local) / "Codex" / "codex.exe"),
            ])
    for candidate in candidates:
        if candidate and Path(candidate).expanduser().is_file():
            return str(Path(candidate).expanduser().resolve())
    raise UpdateError("Codex CLI not found; set CODEX_CLI_PATH or re-run setup from Codex.")


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            process.kill()
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def process_environment() -> dict[str, str]:
    environment = {**os.environ, "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1"}
    proxies = urllib.request.getproxies()
    for scheme in ("http", "https"):
        lower = f"{scheme}_proxy"
        upper = lower.upper()
        if lower in environment or upper in environment:
            continue
        value = proxies.get(scheme)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            environment[upper] = value
    return environment


def run_process(command: list[str], timeout: int = DEFAULT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "env": process_environment(),
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        _kill_process_tree(process)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", "process tree did not flush after termination"
        raise UpdateError(f"Command timed out after {timeout}s: {' '.join(command)}; {stderr[-500:]}") from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def run_codex_json(codex: str, args: list[str], timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    result = run_process([codex, *args, "--json"], timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "Codex command failed").strip()
        raise UpdateError(f"codex {' '.join(args)} failed: {detail[-1000:]}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise UpdateError(f"codex {' '.join(args)} returned non-JSON output") from exc
    if not isinstance(payload, dict):
        raise UpdateError(f"codex {' '.join(args)} returned an unsupported JSON shape")
    return payload


def version_key(version: str) -> tuple[int, int, int, int] | None:
    match = VERSION_RE.fullmatch(version.strip())
    if not match:
        return None
    major, minor, patch, build = match.groups()
    return int(major), int(minor), int(patch), int(build or 0)


def compare_versions(installed: str, candidate: str) -> int | None:
    """Return -1 when candidate is older, 0 when equal, 1 when newer."""
    if installed == candidate:
        return 0
    current_key = version_key(installed)
    candidate_key = version_key(candidate)
    if current_key is None or candidate_key is None:
        return None
    return (candidate_key > current_key) - (candidate_key < current_key)


def plugin_entry(payload: dict[str, Any], plugin: str, marketplace: str) -> dict[str, Any] | None:
    for collection in ("installed", "available"):
        values = payload.get(collection, [])
        if not isinstance(values, list):
            continue
        for entry in values:
            if not isinstance(entry, dict):
                continue
            if entry.get("name") == plugin and entry.get("marketplaceName") == marketplace:
                return entry
    return None


def verify_install(
    installed_path: str,
    expected_name: str,
    expected_version: str,
    marketplace: str,
    expected_edition: str = "full",
) -> dict[str, Any]:
    root = Path(installed_path).resolve()
    manifest_path = root / ".codex-plugin" / "plugin.json"
    manifest = read_json(manifest_path, {})
    if manifest.get("name") != expected_name or manifest.get("version") != expected_version:
        raise UpdateError("Installed plugin manifest does not match the marketplace result.")
    if str(manifest.get("distributionEdition") or "full") != expected_edition:
        raise UpdateError(
            f"Refusing cross-edition update: expected {expected_edition}, "
            f"received {manifest.get('distributionEdition', 'full')}."
        )
    cache_root = (codex_home() / "plugins" / "cache" / marketplace / expected_name).resolve()
    try:
        root.relative_to(cache_root)
    except ValueError as exc:
        raise UpdateError("Codex installed the plugin outside its expected versioned cache.") from exc
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return {"installed_path": str(root), "manifest_sha256": digest}


def due_for_check(config: dict[str, Any], state: dict[str, Any], now: dt.datetime) -> bool:
    interval = max(1, int(config.get("interval_hours", 24)))
    raw = state.get("last_successful_check_at")
    if not isinstance(raw, str) or not raw:
        return True
    try:
        previous = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=dt.timezone.utc)
    return now - previous.astimezone(dt.timezone.utc) >= dt.timedelta(hours=interval)


@contextmanager
def update_lock(path: Path) -> Iterator[bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        age = utc_now().timestamp() - path.stat().st_mtime
        if age > LOCK_STALE_SECONDS:
            path.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        yield False
        return
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        os.close(descriptor)
        yield True
    finally:
        try:
            path.unlink()
        except OSError:
            pass


def sync_stable_updater(installed_path: str, stable_script: str | None) -> bool:
    if not stable_script:
        return False
    source = Path(installed_path) / "scripts" / "plugin_auto_update.py"
    destination = Path(stable_script).expanduser()
    if not source.is_file():
        return False
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).digest() == hashlib.sha256(source.read_bytes()).digest():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix(destination.suffix + ".tmp")
    shutil.copy2(source, temp)
    os.chmod(temp, 0o700)
    temp.replace(destination)
    return True


def update_once(config: dict[str, Any], *, force: bool = False, dry_run: bool = False) -> tuple[int, dict[str, Any]]:
    now = utc_now()
    previous_state = read_json(state_path(), {})
    if not config.get("enabled", False):
        return 0, {"status": "DISABLED", "checked_at": iso_time(now), "updated": False}
    if not force and not due_for_check(config, previous_state, now):
        return 0, {
            "status": "NOT_DUE",
            "checked_at": iso_time(now),
            "updated": False,
            "last_successful_check_at": previous_state.get("last_successful_check_at"),
        }

    with update_lock(lock_path()) as acquired:
        if not acquired:
            return 0, {"status": "ALREADY_RUNNING", "checked_at": iso_time(now), "updated": False}

        codex = find_codex(config.get("codex_cli"))
        plugin = str(config.get("plugin_name") or PLUGIN_NAME)
        marketplace = str(config.get("marketplace_name") or MARKETPLACE_NAME)
        edition = str(config.get("distribution_edition") or "full")
        before_payload = run_codex_json(codex, ["plugin", "list"])
        before = plugin_entry(before_payload, plugin, marketplace)
        before_version = str(before.get("version")) if before else None

        upgrade = run_codex_json(codex, ["plugin", "marketplace", "upgrade", marketplace])
        if upgrade.get("errors"):
            raise UpdateError(f"Marketplace refresh failed: {upgrade['errors']}")

        refreshed = run_codex_json(codex, ["plugin", "list"])
        candidate = plugin_entry(refreshed, plugin, marketplace)
        if not candidate or not candidate.get("version"):
            raise UpdateError(f"Plugin {plugin}@{marketplace} is absent after marketplace refresh.")
        candidate_version = str(candidate["version"])

        comparison = None if before_version is None else compare_versions(before_version, candidate_version)
        base = {
            "checked_at": iso_time(now),
            "plugin": f"{plugin}@{marketplace}",
            "installed_before": before_version,
            "marketplace_version": candidate_version,
            "updated": False,
            "restart_required": False,
        }
        if comparison is None and before_version is not None:
            payload = {**base, "status": "UNRECOGNIZED_VERSION", "required_action": "manual_update_review"}
            atomic_write_json(state_path(), payload)
            return 1, payload
        if comparison == -1:
            payload = {**base, "status": "REMOTE_VERSION_OLDER", "required_action": "keep_installed_version"}
            payload["last_successful_check_at"] = iso_time(now)
            atomic_write_json(state_path(), payload)
            return 0, payload
        if comparison == 0:
            payload = {**base, "status": "UP_TO_DATE", "last_successful_check_at": iso_time(now)}
            atomic_write_json(state_path(), payload)
            return 0, payload
        if dry_run:
            payload = {**base, "status": "UPDATE_AVAILABLE", "required_action": "run_without_dry_run"}
            atomic_write_json(state_path(), payload)
            return 0, payload

        installed = run_codex_json(codex, ["plugin", "add", f"{plugin}@{marketplace}"])
        installed_version = str(installed.get("version") or "")
        installed_path = str(installed.get("installedPath") or "")
        if installed_version != candidate_version or not installed_path:
            raise UpdateError("Codex did not report the expected installed version and cache path.")
        verification = verify_install(installed_path, plugin, candidate_version, marketplace, edition)
        self_updated = sync_stable_updater(installed_path, config.get("stable_script"))
        payload = {
            **base,
            **verification,
            "status": "UPDATED",
            "updated": True,
            "installed_after": installed_version,
            "restart_required": True,
            "updater_refreshed": self_updated,
            "last_successful_check_at": iso_time(now),
            "required_action": "start_a_new_codex_session_when_convenient",
        }
        atomic_write_json(state_path(), payload)
        return 0, payload


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Check and install Codex Goal Supervisor updates.")
    value.add_argument("--config", type=Path, default=config_path())
    value.add_argument("--force", action="store_true", help="Ignore the configured check interval.")
    value.add_argument(
        "--scheduled",
        action="store_true",
        help="Run an operating-system scheduled check at its configured cadence.",
    )
    value.add_argument("--dry-run", action="store_true", help="Refresh metadata but do not install a newer plugin.")
    value.add_argument("--status", action="store_true", help="Read updater state without network access.")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    config = read_json(args.config, {})
    if args.status:
        print(json.dumps({"config": config, "state": read_json(state_path(), {})}, ensure_ascii=False, indent=2))
        return 0
    if not config:
        print(json.dumps({"status": "NOT_CONFIGURED", "required_action": "run configure_plugin_auto_update.py"}, indent=2))
        return 2
    try:
        code, payload = update_once(config, force=args.force or args.scheduled, dry_run=args.dry_run)
    except UpdateError as exc:
        payload = {
            "status": "UPDATE_CHECK_FAILED",
            "checked_at": iso_time(),
            "updated": False,
            "error": str(exc),
            "required_action": "keep_current_version_and_retry_later",
        }
        atomic_write_json(state_path(), payload)
        code = 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
