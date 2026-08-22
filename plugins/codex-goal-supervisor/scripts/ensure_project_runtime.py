#!/usr/bin/env python3
"""Keep one explicitly activated project's runtime aligned with this plugin."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import install_governor


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_MANIFEST = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
INSTALLER = PLUGIN_ROOT / "scripts" / "install_governor.py"


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def inspect(project_root: Path) -> dict[str, Any]:
    manifest = load_json(PLUGIN_MANIFEST)
    provenance_path = project_root / ".agent" / "goal_compass_install.json"
    provenance = load_json(provenance_path)
    runtime_path = project_root / ".agent" / "goal_compass.py"
    runtime_sha256 = sha256_file(runtime_path)
    expected_managed_sha256 = install_governor.managed_runtime_sha256(install_governor.HARNESS_ROOT)
    managed_runtime_sha256 = install_governor.managed_runtime_sha256(project_root)
    plugin_version = str(manifest.get("version") or "unknown")
    installed_version = str(provenance.get("plugin_version") or "unknown")
    recorded_sha256 = str(provenance.get("runtime_sha256") or "") or None
    recorded_managed_sha256 = str(provenance.get("managed_runtime_sha256") or "") or None
    reasons: list[str] = []
    if not runtime_path.is_file():
        reasons.append("project_runtime_missing")
    if installed_version != plugin_version:
        reasons.append("plugin_version_mismatch")
    if runtime_sha256 != recorded_sha256:
        reasons.append("runtime_hash_mismatch")
    if managed_runtime_sha256 != expected_managed_sha256 or recorded_managed_sha256 != managed_runtime_sha256:
        reasons.append("managed_runtime_hash_mismatch")
    return {
        "project_root": str(project_root),
        "plugin_version": plugin_version,
        "installed_version": installed_version,
        "runtime_sha256": runtime_sha256,
        "recorded_runtime_sha256": recorded_sha256,
        "managed_runtime_sha256": managed_runtime_sha256,
        "expected_managed_runtime_sha256": expected_managed_sha256,
        "recorded_managed_runtime_sha256": recorded_managed_sha256,
        "current": not reasons,
        "reasons": reasons,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify or update one explicitly activated Goal Supervisor project runtime."
    )
    parser.add_argument("project_root")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()

    project_root = Path(args.project_root).expanduser().resolve()
    before = inspect(project_root)
    if before["current"]:
        print(json.dumps({"ok": True, "status": "CURRENT", **before}, ensure_ascii=False, indent=2))
        return 0
    if args.check_only:
        print(json.dumps({"ok": False, "status": "STALE", **before}, ensure_ascii=False, indent=2))
        return 2

    try:
        result = subprocess.run(
            [sys.executable, str(INSTALLER), str(project_root), "--force", "--no-init"],
            cwd=str(PLUGIN_ROOT),
            timeout=30,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        print(json.dumps({
            "ok": False,
            "status": "UPDATE_TIMEOUT",
            "before": before,
            "required_action": "retry_project_runtime_update",
        }, ensure_ascii=False, indent=2))
        return 2
    if result.returncode != 0:
        print(json.dumps({
            "ok": False,
            "status": "UPDATE_FAILED",
            "before": before,
            "error": (result.stderr or result.stdout or "installer failed")[-2000:],
            "required_action": "repair_installed_plugin_before_project_work",
        }, ensure_ascii=False, indent=2))
        return 2

    after = inspect(project_root)
    status = "UPDATED" if after["current"] else "UPDATE_UNVERIFIED"
    print(json.dumps({
        "ok": after["current"],
        "status": status,
        "before": before,
        "after": after,
        "project_state_preserved": True,
        "current_task_hook_reload_verified": False,
        "required_action": None if after["current"] else "retry_project_runtime_update",
    }, ensure_ascii=False, indent=2))
    return 0 if after["current"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
