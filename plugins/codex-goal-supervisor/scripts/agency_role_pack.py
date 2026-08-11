#!/usr/bin/env python3
"""Inspect the optional Agency Agents specialist role library."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import verified_asset


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PACK = PLUGIN_ROOT / "assets" / "role-packs" / "agency-agents"
DEFAULT_REMOTE_DESCRIPTOR = PLUGIN_ROOT / "assets" / "role-packs" / "agency-agents.remote.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(pack: Path) -> dict[str, Any]:
    path = pack / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("pack_id") != "agency-agents" or not isinstance(data.get("roles"), list):
        raise ValueError(f"invalid Agency Agents manifest: {path}")
    return data


def remote_descriptor_path() -> Path:
    override = os.environ.get("GOAL_SUPERVISOR_ROLE_PACK_DESCRIPTOR")
    return Path(override).expanduser() if override else DEFAULT_REMOTE_DESCRIPTOR


def role_pack_cache_root() -> Path:
    override = os.environ.get("GOAL_SUPERVISOR_ROLE_PACK_CACHE")
    if override:
        return Path(override).expanduser()
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()
    return codex_home / "goal-supervisor-assets" / "role-packs"


def materialize_remote_pack(descriptor_path: Path) -> Path:
    descriptor = verified_asset.load_descriptor(descriptor_path, "agency-agents")
    cache = role_pack_cache_root() / f"agency-agents-{str(descriptor['source_commit'])[:12]}"

    def validate(path: Path) -> None:
        manifest = load_manifest(path)
        if str(manifest.get("source", {}).get("commit")) != str(descriptor["source_commit"]):
            raise verified_asset.AssetError("remote role-pack source commit does not match its descriptor")
        result = verify(path, manifest)
        if not result["ok"]:
            raise verified_asset.AssetError(
                "remote role-pack content verification failed: " + "; ".join(result["errors"][:5])
            )

    return verified_asset.materialize(descriptor_path, "agency-agents", cache, validate)


def resolve_pack(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit.expanduser().resolve()
    if (DEFAULT_PACK / "manifest.json").is_file():
        return DEFAULT_PACK.resolve()
    descriptor = remote_descriptor_path()
    if not descriptor.is_file():
        raise ValueError("optional role pack is not bundled and no remote descriptor is available")
    return materialize_remote_pack(descriptor).resolve()


def role_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(row["id"]): row for row in manifest["roles"]}


def resolve_role(manifest: dict[str, Any], value: str) -> dict[str, Any]:
    roles = role_map(manifest)
    normalized = value.strip().removesuffix(".md").casefold()
    exact = [row for role_id, row in roles.items() if role_id.casefold() == normalized]
    if exact:
        return exact[0]
    candidates = [
        row for row in roles.values()
        if str(row.get("name", "")).casefold() == normalized
        or Path(str(row["id"])).name.casefold() == normalized
        or str(row.get("source_file", "")).casefold().removesuffix(".md") == normalized
    ]
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise ValueError(f"unknown role: {value}")
    raise ValueError("ambiguous role; use one exact id: " + ", ".join(str(row["id"]) for row in candidates))


def prompt_path(pack: Path, row: dict[str, Any]) -> Path:
    path = (pack / str(row["prompt_file"])).resolve()
    roles_root = (pack / "roles").resolve()
    if path != roles_root and roles_root not in path.parents:
        raise ValueError(f"role prompt escapes pack root: {row['id']}")
    return path


def query_terms(query: str) -> list[str]:
    return [term.casefold() for term in re.findall(r"[\w+#.-]+", query, flags=re.UNICODE) if len(term) > 1]


def search(pack: Path, manifest: dict[str, Any], query: str, division: str | None, limit: int) -> list[dict[str, Any]]:
    terms = query_terms(query)
    phrase = query.strip().casefold()
    if not terms:
        raise ValueError("search query must contain at least one meaningful term")

    results: list[dict[str, Any]] = []
    for row in manifest["roles"]:
        if division and str(row["division"]).casefold() != division.casefold():
            continue
        path = prompt_path(pack, row)
        raw = path.read_text(encoding="utf-8")
        fields = {
            "id": str(row["id"]).casefold(),
            "name": str(row.get("name", "")).casefold(),
            "description": str(row.get("description", "")).casefold(),
            "vibe": str(row.get("vibe", "")).casefold(),
            "division": str(row.get("division", "")).casefold(),
            "prompt": raw.casefold(),
        }
        score = 0
        matched: set[str] = set()
        if phrase and phrase in fields["name"]:
            score += 40
            matched.add("name_phrase")
        elif phrase and phrase in fields["description"]:
            score += 20
            matched.add("description_phrase")
        for term in terms:
            weights = {"id": 14, "name": 14, "description": 8, "vibe": 5, "division": 3, "prompt": 1}
            for field, weight in weights.items():
                if term in fields[field]:
                    score += weight
                    matched.add(field)
        if score:
            results.append({
                "id": row["id"],
                "division": row["division"],
                "name": row.get("name", ""),
                "description": row.get("description", ""),
                "vibe": row.get("vibe", ""),
                "score": score,
                "matched_fields": sorted(matched),
                "prompt_sha256": row["prompt_sha256"],
            })
    results.sort(key=lambda row: (-int(row["score"]), str(row["id"])))
    return results[: max(1, limit)]


def verify(pack: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    pack = pack.resolve()
    errors: list[str] = []
    roles = manifest["roles"]
    ids = [str(row.get("id", "")) for row in roles]
    if len(ids) != len(set(ids)):
        errors.append("duplicate role ids")
    if manifest.get("role_count") != len(roles):
        errors.append("manifest role_count mismatch")

    declared_files: set[str] = set()
    for row in roles:
        try:
            path = prompt_path(pack, row)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        declared_files.add(path.relative_to(pack).as_posix())
        if not path.is_file():
            errors.append(f"missing prompt: {row.get('id')}")
        elif sha256_file(path) != row.get("prompt_sha256"):
            errors.append(f"prompt hash mismatch: {row.get('id')}")

    actual_files = {path.relative_to(pack).as_posix() for path in (pack / "roles").rglob("*.md")}
    for extra in sorted(actual_files - declared_files):
        errors.append(f"undeclared prompt: {extra}")
    for missing in sorted(declared_files - actual_files):
        errors.append(f"declared prompt not found: {missing}")

    license_path = pack / str(manifest.get("source", {}).get("license_file", "LICENSE"))
    if not license_path.is_file():
        errors.append("MIT license file missing")
    elif sha256_file(license_path) != manifest.get("source", {}).get("license_sha256"):
        errors.append("MIT license hash mismatch")
    return {
        "ok": not errors,
        "pack_id": manifest.get("pack_id"),
        "roles": len(roles),
        "divisions": len({str(row.get("division")) for row in roles}),
        "source_commit": manifest.get("source", {}).get("commit"),
        "raw_prompt_policy": manifest.get("raw_prompt_policy"),
        "selection_policy": manifest.get("selection_policy"),
        "authority": manifest.get("authority"),
        "errors": errors,
    }


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", type=Path, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show pinned source and catalog size.")
    verify_parser = sub.add_parser("verify", help="Verify every raw prompt and the MIT license hash.")
    verify_parser.add_argument("--quiet", action="store_true")

    list_parser = sub.add_parser("list", help="List available expert role profiles.")
    list_parser.add_argument("--division")
    list_parser.add_argument("--json", action="store_true")

    search_parser = sub.add_parser("search", help="Return lexical candidates; the main thread makes the selection.")
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--division")
    search_parser.add_argument("--limit", type=int, default=12)
    search_parser.add_argument("--json", action="store_true")

    show_parser = sub.add_parser("show", help="Read one exact upstream role prompt without truncation.")
    show_parser.add_argument("--role", required=True)
    show_parser.add_argument("--format", choices=["raw", "json", "path"], default="raw")

    args = parser.parse_args(argv)
    pack = resolve_pack(args.pack)
    try:
        manifest = load_manifest(pack)
        if args.command == "status":
            print_json({
                "pack_id": manifest["pack_id"],
                "display_name": manifest["display_name"],
                "roles": manifest["role_count"],
                "divisions": manifest["division_count"],
                "source": manifest["source"],
                "raw_prompt_policy": manifest["raw_prompt_policy"],
                "selection_policy": manifest["selection_policy"],
                "authority": manifest["authority"],
            })
            return 0
        if args.command == "verify":
            result = verify(pack, manifest)
            if not args.quiet or not result["ok"]:
                print_json(result)
            return 0 if result["ok"] else 1
        if args.command == "list":
            rows = [
                {key: row.get(key) for key in ["id", "division", "name", "description", "vibe", "prompt_sha256"]}
                for row in manifest["roles"]
                if not args.division or str(row["division"]).casefold() == args.division.casefold()
            ]
            if args.json:
                print_json(rows)
            else:
                for row in rows:
                    print(f"{row['id']}\t{row['name']}\t{row['description']}")
            return 0
        if args.command == "search":
            rows = search(pack, manifest, args.query, args.division, args.limit)
            if args.json:
                print_json(rows)
            else:
                for row in rows:
                    print(f"{row['id']}\t{row['score']}\t{row['name']}\t{row['description']}")
            return 0
        if args.command == "show":
            row = resolve_role(manifest, args.role)
            path = prompt_path(pack, row)
            if args.format == "raw":
                sys.stdout.write(path.read_text(encoding="utf-8"))
            elif args.format == "path":
                print(path)
            else:
                print_json({"metadata": row, "prompt": path.read_text(encoding="utf-8")})
            return 0
    except (OSError, ValueError, json.JSONDecodeError, verified_asset.AssetError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
