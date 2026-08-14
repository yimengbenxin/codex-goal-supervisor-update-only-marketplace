#!/usr/bin/env python3
"""Install Codex Goal Supervisor into one repository without polluting its root."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))

import verified_asset


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
HARNESS_ROOT = PLUGIN_ROOT / "assets" / "governor-harness"
HARNESS_DESCRIPTOR = PLUGIN_ROOT / "assets" / "governor-harness.remote.json"

ALLOW_FILES = {
    ".agent/goal_compass.py",
    ".agent/north_star_goal.json",
    ".agent/current_ticket.json",
    ".agent/backlog.jsonl",
    ".agent/validation_catalog.json",
    ".agent/prune_plan.json",
    ".agent/request_decisions.jsonl",
    ".agent/quarantine_manifest.jsonl",
    ".agent/docs/README_GOAL_COMPASS.md",
    ".codex/hooks.json",
}

ALLOW_DIR_PREFIXES = (
    ".agent/contracts/",
    ".agent/goal_compass_runtime/",
    ".agent/lenses/",
    ".agent/protocols/",
    ".agent/selftest/",
)

DENY_PARTS = {
    "legacy",
    "__pycache__",
}

STATE_FILES = {
    ".agent/north_star_goal.json",
    ".agent/current_ticket.json",
    ".agent/backlog.jsonl",
    ".agent/prune_plan.json",
    ".agent/request_decisions.jsonl",
    ".agent/quarantine_manifest.jsonl",
}

MERGE_JSON_FILES = {
    ".agent/validation_catalog.json",
}

HOOKS_FILE = ".codex/hooks.json"
PROVENANCE_FILE = ".agent/goal_compass_install.json"

LEGACY_EXAMPLE_FILES = (
    ".agent/tickets/examples/VIDEO-MOCK-001.json",
    ".agent/tickets/examples/ROUTING-MVP-001.json",
    ".agent/tickets/examples/PERMISSION-GUARD-001.json",
)

LEGACY_EXAMPLE_SHA256 = {
    ".agent/tickets/examples/VIDEO-MOCK-001.json": "dd25730bd42e28178b3eaafc547b442da210d026cc935a0b4709e678e3e41937",
    ".agent/tickets/examples/ROUTING-MVP-001.json": "b20657ed68f04f8fa450c66970d6b9b004686c7b9906cffddbcc1f0fbe0adacf",
    ".agent/tickets/examples/PERMISSION-GUARD-001.json": "2bc1b1610df5df41a2ff6d22a2dfc3f4673b6fdf7e814577a3d489ad8aed6c8e",
}

LEGACY_VALIDATION_DEFAULTS = {
    "mock_video_pipeline_test": "npm test -- tests/video/mock-video-pipeline.test.ts",
    "routing_mvp_test": "npm test -- tests/routing/routing-mvp.test.ts",
    "permission_guard_test": "npm test -- tests/security/permission-guard.test.ts",
}


def posix_rel(path: Path) -> str:
    return path.as_posix()


def allowed(rel: Path) -> bool:
    s = posix_rel(rel)
    if any(part in DENY_PARTS for part in rel.parts):
        return False
    if s in ALLOW_FILES:
        return True
    return any(s.startswith(prefix) for prefix in ALLOW_DIR_PREFIXES)


def copy_file(src: Path, dst: Path, force: bool) -> str:
    existed = dst.exists()
    if existed and not force:
        return "skip"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return "update" if existed else "write"


def safe_destination(target: Path, rel: Path) -> Path:
    root = target.resolve()
    dst = target / rel
    if dst.is_symlink():
        raise SystemExit(f"Refusing to write through symlink: {dst}")
    try:
        dst.parent.resolve().relative_to(root)
    except ValueError as exc:
        raise SystemExit(f"Refusing destination outside repository: {dst}") from exc
    return dst


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def merge_json_defaults(src: Path, dst: Path) -> str:
    try:
        defaults = json.loads(src.read_text(encoding="utf-8"))
        existing = json.loads(dst.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "skip"
    if not isinstance(defaults, dict) or not isinstance(existing, dict):
        return "skip"
    retained = {
        key: value
        for key, value in existing.items()
        if not (
            key in LEGACY_VALIDATION_DEFAULTS
            and isinstance(value, dict)
            and value.get("cmd") == LEGACY_VALIDATION_DEFAULTS[key]
        )
    }
    merged = dict(defaults)
    merged.update(retained)
    if merged == existing:
        return "skip"
    dst.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "update"


def is_goal_compass_hook(handler: object) -> bool:
    if not isinstance(handler, dict):
        return False
    command = str(handler.get("command", ""))
    return (
        "goal_compass.py" in command
        or "project_hook.py" in command
        or str(handler.get("statusMessage", "")).startswith("Goal Compass")
    )


def merge_hooks_defaults(src: Path, dst: Path) -> str:
    try:
        generated = json.loads(src.read_text(encoding="utf-8"))
        existing = json.loads(dst.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "skip"
    if not isinstance(generated, dict) or not isinstance(existing, dict):
        return "skip"
    result = json.loads(json.dumps(existing))
    result_hooks = result.setdefault("hooks", {})
    generated_hooks = generated.get("hooks", {})
    for event in (
        "PreToolUse", "PostToolUse", "PreCompact", "PostCompact",
        "SessionStart", "SubagentStart", "UserPromptSubmit", "Stop",
    ):
        preserved = []
        for group in result_hooks.get(event, []):
            if not isinstance(group, dict):
                preserved.append(group)
                continue
            handlers = [handler for handler in group.get("hooks", []) if not is_goal_compass_hook(handler)]
            if handlers:
                updated = dict(group)
                updated["hooks"] = handlers
                preserved.append(updated)
        preserved.extend(generated_hooks.get(event, []))
        if preserved:
            result_hooks[event] = preserved
        else:
            result_hooks.pop(event, None)
    if result == existing:
        return "skip"
    dst.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return "update"


def remove_legacy_examples(target: Path) -> int:
    removed = 0
    for rel in LEGACY_EXAMPLE_FILES:
        path = target / rel
        if path.is_file() and sha256_file(path) == LEGACY_EXAMPLE_SHA256.get(rel):
            path.unlink()
            removed += 1
            print(f"remove: {path}")
    examples = target / ".agent" / "tickets" / "examples"
    if examples.is_dir() and not any(examples.iterdir()):
        examples.rmdir()
    return removed


def resolve_harness_root() -> Path:
    if (HARNESS_ROOT / ".agent" / "goal_compass.py").is_file():
        return HARNESS_ROOT
    descriptor_path = Path(
        os.environ.get("GOAL_SUPERVISOR_HARNESS_DESCRIPTOR", HARNESS_DESCRIPTOR)
    ).expanduser()
    descriptor = verified_asset.load_descriptor(descriptor_path, "governor-harness")
    version = str(descriptor.get("source_version") or "unknown")
    if version != plugin_version():
        raise SystemExit("Remote Goal Supervisor runtime does not match the installed plugin version.")
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    cache_base = Path(os.environ.get("GOAL_SUPERVISOR_ASSET_CACHE", codex_home / "goal-supervisor-assets"))
    cache = cache_base / "runtime" / version.replace("+", "-")

    def validate(path: Path) -> None:
        required = (
            path / ".agent" / "goal_compass.py",
            path / ".agent" / "goal_compass_runtime" / "project_hook.py",
            path / ".agent" / "goal_compass_runtime" / "roadmap.py",
            path / ".agent" / "goal_compass_runtime" / "roadmap.html",
            path / ".agent" / "selftest" / "test_goal_compass.py",
            path / ".codex" / "hooks.json",
        )
        if not all(item.is_file() for item in required):
            raise verified_asset.AssetError("downloaded Goal Supervisor runtime is incomplete")

    try:
        return verified_asset.materialize(descriptor_path, "governor-harness", cache, validate)
    except verified_asset.AssetError as exc:
        raise SystemExit(f"Unable to obtain Goal Supervisor project runtime: {exc}") from exc


def install(target: Path, force: bool, reset_state: bool = False) -> tuple[int, int, int]:
    harness_root = resolve_harness_root()
    target = target.resolve()
    writes = skips = filtered = 0
    remove_legacy_examples(target)
    for src in sorted(p for p in harness_root.rglob("*") if p.is_file()):
        rel = src.relative_to(harness_root)
        if not allowed(rel):
            filtered += 1
            continue
        rel_text = posix_rel(rel)
        dst = safe_destination(target, rel)
        if dst.exists() and rel_text == HOOKS_FILE:
            action = merge_hooks_defaults(src, dst)
        elif dst.exists() and not reset_state and rel_text in STATE_FILES:
            action = "skip"
        elif dst.exists() and not reset_state and rel_text in MERGE_JSON_FILES:
            action = merge_json_defaults(src, dst)
        else:
            action = copy_file(src, dst, force or (reset_state and rel_text in STATE_FILES))
        if action == "skip":
            skips += 1
        else:
            writes += 1
            print(f"{action}: {dst}")
    return writes, skips, filtered


def maybe_init(target: Path) -> int:
    compass = target / ".agent" / "goal_compass.py"
    if not compass.exists():
        print("goal_compass.py missing after install", file=sys.stderr)
        return 2
    return subprocess.run([sys.executable, str(compass), "init"], cwd=str(target), timeout=20, errors="replace").returncode


def plugin_version() -> str:
    try:
        data = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown"
    return str(data.get("version") or "unknown")


def write_install_provenance(target: Path, writes: int, skips: int, filtered: int, initialized: bool) -> None:
    path = target / PROVENANCE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    compass = target / ".agent" / "goal_compass.py"
    payload = {
        "schema_version": 1,
        "plugin_version": plugin_version(),
        "installed_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "runtime_sha256": sha256_file(compass) if compass.is_file() else None,
        "initialized": initialized,
        "install_summary": {"writes": writes, "skips": skips, "filtered": filtered},
        "migration_policy": "preserve_project_state_and_remove_only_known_legacy_examples",
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("target", nargs="?", default=".", help="Repository root to install into.")
    parser.add_argument("--force", action="store_true", help="Update Codex Goal Supervisor code, docs, protocols, and hooks; preserve project state.")
    parser.add_argument("--reset-state", action="store_true", help="Also replace North Star, ticket, backlog, and other runtime state.")
    parser.add_argument("--no-init", action="store_true", help="Copy files without running Goal Compass init.")
    args = parser.parse_args()

    writes, skips, filtered = install(Path(args.target), args.force, reset_state=args.reset_state)
    print(f"installed Goal Compass files: writes={writes} skips={skips} filtered={filtered}")
    target = Path(args.target).resolve()
    if args.no_init:
        write_install_provenance(target, writes, skips, filtered, initialized=False)
        return 0
    result = maybe_init(target)
    if result == 0:
        write_install_provenance(target, writes, skips, filtered, initialized=True)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
