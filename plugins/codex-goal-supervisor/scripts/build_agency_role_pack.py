#!/usr/bin/env python3
"""Vendor an exact Agency Agents snapshot and build its searchable manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DESTINATION = PLUGIN_ROOT / "assets" / "role-packs" / "agency-agents"
SOURCE_REPO = "https://github.com/msitarzewski/agency-agents"
NON_ROLE_DIRECTORIES = {".git", ".github", "examples", "integrations", "scripts", "strategy"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_commit(source: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip()


def unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_frontmatter(text: str) -> dict[str, str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("missing YAML frontmatter")
    try:
        end = next(index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---")
    except StopIteration as exc:
        raise ValueError("unterminated YAML frontmatter") from exc

    result: dict[str, str] = {}
    current: str | None = None
    folded = False
    for line in lines[1:end]:
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$", line)
        if match:
            current = match.group(1)
            value = match.group(2).strip()
            folded = value in {">", "|"}
            result[current] = "" if folded else unquote(value)
            continue
        if current and (line.startswith(" ") or line.startswith("\t")):
            part = line.strip()
            if part:
                separator = " " if folded or result[current] else ""
                result[current] += separator + part
    return result


def role_sources(source: Path) -> list[Path]:
    roles: list[Path] = []
    for directory in sorted(source.iterdir()):
        if not directory.is_dir() or directory.name in NON_ROLE_DIRECTORIES:
            continue
        for path in sorted(directory.rglob("*.md")):
            metadata = parse_frontmatter(path.read_text(encoding="utf-8"))
            if metadata.get("name") and metadata.get("description"):
                roles.append(path)
    return roles


def build_pack(source: Path, destination: Path) -> dict[str, Any]:
    source = source.resolve()
    destination = destination.resolve()
    if not (source / "LICENSE").is_file():
        raise ValueError(f"Agency Agents LICENSE not found under {source}")

    sources = role_sources(source)
    if not sources:
        raise ValueError("no Agency Agents role prompts found")

    roles_root = destination / "roles"
    roles_root.mkdir(parents=True, exist_ok=True)
    expected: set[Path] = set()
    rows: list[dict[str, Any]] = []

    for path in sources:
        relative = path.relative_to(source)
        target = roles_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        expected.add(target.resolve())

        raw = target.read_bytes()
        text = raw.decode("utf-8")
        metadata = parse_frontmatter(text)
        role_id = relative.with_suffix("").as_posix()
        rows.append({
            "id": role_id,
            "division": relative.parts[0],
            "name": metadata.get("name", ""),
            "description": metadata.get("description", ""),
            "vibe": metadata.get("vibe", ""),
            "color": metadata.get("color", ""),
            "emoji": metadata.get("emoji", ""),
            "source_file": relative.as_posix(),
            "prompt_file": f"roles/{relative.as_posix()}",
            "prompt_sha256": sha256_bytes(raw),
            "bytes": len(raw),
            "lines": len(text.splitlines()),
        })

    for stale in roles_root.rglob("*.md"):
        if stale.resolve() not in expected:
            stale.unlink()

    license_target = destination / "LICENSE"
    shutil.copy2(source / "LICENSE", license_target)
    license_bytes = license_target.read_bytes()
    commit = source_commit(source)
    manifest = {
        "schema_version": 1,
        "pack_id": "agency-agents",
        "display_name": "Agency Agents Specialist Role Library",
        "role_count": len(rows),
        "division_count": len({row["division"] for row in rows}),
        "raw_prompt_policy": "byte_for_byte_upstream_snapshot",
        "selection_policy": "optional_main_thread_choice",
        "authority": "expert_reference_not_final_decision_maker",
        "source": {
            "repository": SOURCE_REPO,
            "commit": commit,
            "imported_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            "license": "MIT",
            "license_file": "LICENSE",
            "license_sha256": sha256_bytes(license_bytes),
        },
        "roles": sorted(rows, key=lambda row: row["id"]),
    }
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Local Agency Agents git checkout.")
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    manifest = build_pack(args.source, args.destination)
    print(json.dumps({
        "ok": True,
        "pack": manifest["pack_id"],
        "roles": manifest["role_count"],
        "divisions": manifest["division_count"],
        "source_commit": manifest["source"]["commit"],
        "destination": str(args.destination.resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
