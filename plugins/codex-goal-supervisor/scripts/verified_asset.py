#!/usr/bin/env python3
"""Download one immutable ZIP asset into a user-level cache."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Callable


DOWNLOAD_TIMEOUT_SECONDS = 300
LOCK_STALE_SECONDS = 15 * 60


class AssetError(RuntimeError):
    pass


def load_descriptor(path: Path, expected_asset_id: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetError(f"invalid remote asset descriptor: {path}") from exc
    required = {
        "asset_id", "archive_url", "archive_root", "archive_sha256",
        "archive_bytes", "max_files", "max_uncompressed_bytes",
    }
    if data.get("schema_version") != 1 or data.get("asset_id") != expected_asset_id or not required <= data.keys():
        raise AssetError(f"invalid remote asset descriptor: {path}")
    if not str(data["archive_url"]).startswith("https://"):
        raise AssetError("remote asset URL must use HTTPS")
    if not re.fullmatch(r"[0-9a-f]{64}", str(data["archive_sha256"])):
        raise AssetError("remote asset archive hash is invalid")
    return data


def _safe_member(info: zipfile.ZipInfo, expected_root: str) -> Path:
    name = PurePosixPath(info.filename)
    if name.is_absolute() or ".." in name.parts or not name.parts or name.parts[0] != expected_root:
        raise AssetError(f"unsafe remote asset member: {info.filename}")
    if stat.S_ISLNK(info.external_attr >> 16):
        raise AssetError(f"remote asset contains a symlink: {info.filename}")
    return Path(*name.parts)


def _download(descriptor: dict[str, Any], destination: Path, timeout: int) -> None:
    expected_bytes = int(descriptor["archive_bytes"])
    request = urllib.request.Request(
        str(descriptor["archive_url"]),
        headers={"User-Agent": "Codex-Goal-Supervisor-asset/1"},
    )
    deadline = time.monotonic() + timeout
    digest = hashlib.sha256()
    written = 0
    try:
        with urllib.request.urlopen(request, timeout=30) as response, destination.open("wb") as output:
            declared = response.headers.get("Content-Length")
            if declared and int(declared) != expected_bytes:
                raise AssetError("remote asset size does not match its descriptor")
            while True:
                if time.monotonic() > deadline:
                    raise AssetError(f"remote asset download exceeded {timeout}s")
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > expected_bytes:
                    raise AssetError("remote asset exceeded its declared size")
                digest.update(chunk)
                output.write(chunk)
    except (OSError, TimeoutError) as exc:
        raise AssetError(f"remote asset download failed: {exc}") from exc
    if written != expected_bytes or digest.hexdigest() != descriptor["archive_sha256"]:
        raise AssetError("remote asset failed size or SHA-256 verification")


def _extract(archive: Path, destination: Path, descriptor: dict[str, Any]) -> Path:
    expected_root = str(descriptor["archive_root"])
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = bundle.infolist()
            files = [info for info in members if not info.is_dir()]
            if len(files) > int(descriptor["max_files"]):
                raise AssetError("remote asset exceeds its file-count limit")
            if sum(info.file_size for info in files) > int(descriptor["max_uncompressed_bytes"]):
                raise AssetError("remote asset exceeds its extraction-size limit")
            for info in members:
                relative = _safe_member(info, expected_root)
                target = destination / relative
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output, length=64 * 1024)
    except zipfile.BadZipFile as exc:
        raise AssetError("remote asset is not a valid ZIP archive") from exc
    return destination / expected_root


def materialize(
    descriptor_path: Path,
    expected_asset_id: str,
    cache: Path,
    validate: Callable[[Path], None],
    *,
    timeout: int = DOWNLOAD_TIMEOUT_SECONDS,
) -> Path:
    descriptor = load_descriptor(descriptor_path, expected_asset_id)
    cache = cache.expanduser().resolve()
    if cache.is_dir():
        try:
            validate(cache)
            return cache
        except (AssetError, OSError, ValueError):
            pass

    cache.parent.mkdir(parents=True, exist_ok=True)
    lock = cache.with_suffix(".lock")
    try:
        if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
            lock.unlink(missing_ok=True)
    except OSError:
        pass
    try:
        descriptor_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise AssetError("remote asset download is already running") from exc
    os.close(descriptor_fd)
    try:
        with tempfile.TemporaryDirectory(prefix="goal-supervisor-asset-", dir=cache.parent) as temporary:
            staging = Path(temporary)
            archive = staging / "asset.zip"
            _download(descriptor, archive, timeout)
            extracted = _extract(archive, staging / "extracted", descriptor)
            validate(extracted)
            if cache.exists():
                shutil.rmtree(cache)
            extracted.replace(cache)
    finally:
        lock.unlink(missing_ok=True)
    return cache
