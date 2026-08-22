#!/usr/bin/env python3
"""Content-aware fingerprint for an exact dirty pre-release worktree."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path


def run_git(root: Path, *args: str, text: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(root),
        timeout=10,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        errors="replace" if text else None,
    )
    if result.returncode != 0:
        error = result.stderr if text else result.stderr.decode("utf-8", errors="replace")
        raise RuntimeError(error.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def untracked_manifest(root: Path) -> tuple[bytes, int]:
    raw = run_git(root, "ls-files", "--others", "--exclude-standard", "-z")
    assert isinstance(raw, bytes)
    paths = sorted(value for value in raw.split(b"\0") if value)
    digest = hashlib.sha256()
    count = 0
    for encoded in paths:
        relative = encoded.decode("utf-8", errors="surrogateescape")
        path = root / relative
        digest.update(encoded)
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"SYMLINK\0")
            digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
        elif path.is_file():
            digest.update(b"FILE\0")
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            digest.update(b"MISSING\0")
        digest.update(b"\n")
        count += 1
    return digest.digest(), count


def fingerprint(root: Path) -> dict[str, object]:
    head = str(run_git(root, "rev-parse", "HEAD", text=True)).strip()
    tracked_diff = run_git(root, "diff", "--binary", "HEAD", "--", ".")
    assert isinstance(tracked_diff, bytes)
    untracked_digest, untracked_count = untracked_manifest(root)
    tracked_hash = sha256(tracked_diff)
    untracked_hash = untracked_digest.hex()
    combined = hashlib.sha256()
    combined.update(head.encode("ascii"))
    combined.update(b"\n")
    combined.update(bytes.fromhex(tracked_hash))
    combined.update(bytes.fromhex(untracked_hash))
    return {
        "schema_version": 1,
        "head": head,
        "dirty": bool(tracked_diff) or untracked_count > 0,
        "tracked_diff_sha256": tracked_hash,
        "untracked_manifest_sha256": untracked_hash,
        "untracked_file_count": untracked_count,
        "worktree_fingerprint_sha256": combined.hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Hash HEAD plus tracked and untracked worktree content.")
    parser.add_argument("root", nargs="?", default=".")
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    try:
        payload = fingerprint(root)
    except RuntimeError as exc:
        print(json.dumps({"ok": False, "status": "NOT_A_GIT_WORKTREE", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"ok": True, **payload}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
