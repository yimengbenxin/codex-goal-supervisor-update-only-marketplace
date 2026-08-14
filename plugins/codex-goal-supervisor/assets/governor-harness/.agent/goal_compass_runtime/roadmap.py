"""Live, project-local visualization of the detailed Goal execution route.

The dashboard is a read-only projection. North Star and convergence JSON remain
the only authority-bearing state, so the visualizer cannot create a second plan.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
HOST = "127.0.0.1"
MAX_NODES = 256
MAX_LIST_ITEMS = 64
MAX_TEXT = 1200
DEFAULT_IDLE_SECONDS = 48 * 60 * 60


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(default)
    return value if isinstance(value, dict) else dict(default)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _text(value: Any) -> str | None:
    result = str(value or "").strip()
    return result[:MAX_TEXT] if result else None


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip()[:MAX_TEXT] for item in value[:MAX_LIST_ITEMS] if str(item).strip()]


def _actions(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value[:MAX_LIST_ITEMS] if isinstance(value, list) else []):
        if isinstance(item, dict):
            row = {
                "action_id": _text(item.get("action_id")) or f"A{index + 1}",
                "name": _text(item.get("name") or item.get("action") or item.get("description")),
                "from": _text(item.get("from")),
                "to": _text(item.get("to")),
                "inputs": _strings(item.get("inputs")),
                "outputs": _strings(item.get("outputs")),
                "consumer": _text(item.get("consumer")),
                "acceptance": _strings(item.get("acceptance")),
            }
        else:
            row = {"action_id": f"A{index + 1}", "name": _text(item)}
        if row.get("name"):
            result.append(row)
    return result


def _subnodes(value: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value[:MAX_LIST_ITEMS] if isinstance(value, list) else []):
        if not isinstance(item, dict):
            continue
        result.append({
            "node_id": _text(item.get("node_id")) or f"S{index + 1}",
            "name": _text(item.get("name")),
            "objective": _text(item.get("objective")),
            "inputs": _strings(item.get("inputs")),
            "actions": _actions(item.get("actions")),
            "outputs": _strings(item.get("outputs")),
            "consumer": _text(item.get("consumer")),
            "exit_criteria": _strings(item.get("exit_criteria")),
        })
    return result


def build_snapshot(project_root: Path) -> dict[str, Any]:
    """Build a bounded live graph from the authoritative project Goal state."""
    root = project_root.resolve()
    agent = root / ".agent"
    north = _load_json(agent / "north_star_goal.json", {})
    convergence = _load_json(agent / "runtime" / "convergence_state.json", {})
    definition = north.get("goal_definition") if isinstance(north.get("goal_definition"), dict) else {}
    process = definition.get("process") if isinstance(definition.get("process"), dict) else {}
    source_nodes = [item for item in process.get("nodes", []) if isinstance(item, dict)]
    source_nodes = source_nodes[:MAX_NODES]

    segments = convergence.get("segments") if isinstance(convergence.get("segments"), dict) else {}
    active = segments.get("active") if isinstance(segments.get("active"), dict) else {}
    completed_rows = [item for item in segments.get("completed", []) if isinstance(item, dict)]
    completed = {str(item.get("node_id") or "").strip() for item in completed_rows}
    node_ids = [str(item.get("node_id") or f"N{index + 1}").strip() for index, item in enumerate(source_nodes)]

    dependent_consumers: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for index, item in enumerate(source_nodes):
        consumer_id = str(item.get("node_id") or f"N{index + 1}").strip()
        for dependency in _strings(item.get("dependencies")):
            if dependency in dependent_consumers and consumer_id not in dependent_consumers[dependency]:
                dependent_consumers[dependency].append(consumer_id)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, str]] = []
    for index, item in enumerate(source_nodes):
        node_id = str(item.get("node_id") or f"N{index + 1}").strip()
        dependencies = _strings(item.get("dependencies"))
        for dependency in dependencies:
            edges.append({"from": dependency, "to": node_id, "kind": "dependency"})
        if node_id in active:
            status = "ACTIVE"
        elif node_id in completed:
            status = "COMPLETED"
        elif all(dependency in completed for dependency in dependencies):
            status = "READY"
        else:
            status = "BLOCKED"
        explicit_consumers = _strings(item.get("consumers"))
        consumers = list(dict.fromkeys([*explicit_consumers, *dependent_consumers.get(node_id, [])]))
        runtime = active.get(node_id) if isinstance(active.get(node_id), dict) else {}
        nodes.append({
            "node_id": node_id,
            "name": _text(item.get("name")) or node_id,
            "objective": _text(item.get("objective")),
            "status": status,
            "dependencies": dependencies,
            "inputs": _strings(item.get("inputs")),
            "actions": _actions(item.get("actions")),
            "outputs": _strings(item.get("outputs")),
            "consumers": consumers,
            "exit_criteria": _strings(item.get("exit_criteria")),
            "execution_mode": _text(item.get("execution_mode")),
            "parallel_group": _text(item.get("parallel_group")),
            "contribution_to_goal": _text(item.get("contribution_to_goal")),
            "timebox_hours": item.get("timebox_hours"),
            "reminder_interval_hours": item.get("reminder_interval_hours", 0),
            "affected_paths": _strings(item.get("affected_paths")),
            "affected_modules": _strings(item.get("affected_modules")),
            "subnodes": _subnodes(item.get("subnodes")),
            "runtime": {
                key: runtime.get(key)
                for key in ("started_at", "deadline_at", "next_reminder_at", "reminder_count", "started_by")
                if runtime.get(key) is not None
            },
        })

    deliverables = []
    for item in definition.get("deliverables", [])[:MAX_LIST_ITEMS] if isinstance(definition.get("deliverables"), list) else []:
        if isinstance(item, dict):
            deliverables.append({
                "name": _text(item.get("name")),
                "description": _text(item.get("description")),
                "format": _text(item.get("format")),
                "consumer": _text(item.get("consumer")),
                "acceptance": _strings(item.get("acceptance")),
            })

    goal_completion = convergence.get("goal_completion") if isinstance(convergence.get("goal_completion"), dict) else {}
    current_ids = [node_id for node_id in node_ids if node_id in active]
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "source": {
            "north_star": ".agent/north_star_goal.json",
            "runtime": ".agent/runtime/convergence_state.json",
            "authority": "read_only_projection",
        },
        "route_map_ready": bool(
            north.get("confirmed")
            and definition.get("quality") == "STRUCTURED_DETAILED"
            and nodes
        ),
        "goal": {
            "north_star": _text(north.get("goal")),
            "precise_goal": _text(definition.get("precise_goal")),
            "current_state": _text(definition.get("current_state")),
            "desired_state": _text(definition.get("desired_state")),
            "quality": _text(definition.get("quality")) or "MISSING",
        },
        "current_node_ids": current_ids,
        "nodes": nodes,
        "edges": edges,
        "deliverables": deliverables,
        "final_acceptance": definition.get("final_acceptance", [])[:MAX_LIST_ITEMS]
        if isinstance(definition.get("final_acceptance"), list) else [],
        "progress": {
            "completed": len(completed.intersection(node_ids)),
            "active": len(current_ids),
            "ready": sum(1 for item in nodes if item["status"] == "READY"),
            "blocked": sum(1 for item in nodes if item["status"] == "BLOCKED"),
            "total": len(nodes),
            "goal_completion": goal_completion.get("status", "NOT_CERTIFIED"),
        },
        "projection_truncated": len([item for item in process.get("nodes", []) if isinstance(item, dict)]) > MAX_NODES,
    }


def _runtime_dir(project_root: Path) -> Path:
    return project_root.resolve() / ".agent" / "runtime" / "roadmap"


def metadata_path(project_root: Path) -> Path:
    return _runtime_dir(project_root) / "server.json"


def server_summary(project_root: Path) -> dict[str, Any]:
    snapshot = build_snapshot(project_root)
    metadata = _load_json(metadata_path(project_root), {})
    return {
        "status": metadata.get("status", "NOT_STARTED"),
        "url": metadata.get("url"),
        "pid": metadata.get("pid"),
        "route_map_ready": snapshot.get("route_map_ready", False),
        "current_node_ids": snapshot.get("current_node_ids", []),
        "progress": snapshot.get("progress", {}),
        "browser_surface": "open_url_in_codex_in_app_browser_when_available",
    }


def _healthy(url: str, project_root: Path, timeout: float = 0.35) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/health", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return False
    return payload.get("ok") is True and payload.get("project_root") == str(project_root.resolve())


def _public_server_state(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        key: metadata.get(key)
        for key in ("schema_version", "status", "pid", "host", "port", "url", "project_root", "started_at")
        if metadata.get(key) is not None
    }


def ensure_server(project_root: Path, *, wait_seconds: float = 2.5) -> dict[str, Any]:
    root = project_root.resolve()
    snapshot = build_snapshot(root)
    if not snapshot.get("route_map_ready"):
        return {
            "status": "NEEDS_DETAILED_GOAL",
            "url": None,
            "route_map_ready": False,
            "required_action": "goal-set --require-detailed",
        }
    if os.environ.get("GOAL_SUPERVISOR_DISABLE_ROADMAP_SERVER") == "1":
        return {
            "status": "READY_NOT_STARTED",
            "url": None,
            "route_map_ready": True,
            "required_action": "roadmap",
        }
    state_path = metadata_path(root)
    current = _load_json(state_path, {})
    current_url = str(current.get("url") or "")
    if current_url and _healthy(current_url, root):
        current["status"] = "RUNNING"
        current["route_map_ready"] = True
        return {**_public_server_state(current), "route_map_ready": True}

    token = secrets.token_urlsafe(24)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--serve",
        "--project-root",
        str(root),
        "--token",
        token,
    ]
    kwargs: dict[str, Any] = {
        "cwd": str(root),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUTF8": "1"},
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    deadline = time.monotonic() + max(0.25, wait_seconds)
    while time.monotonic() < deadline:
        time.sleep(0.05)
        current = _load_json(state_path, {})
        current_url = str(current.get("url") or "")
        if current.get("pid") == process.pid and current_url and _healthy(current_url, root):
            current["route_map_ready"] = True
            return {**_public_server_state(current), "route_map_ready": True}
        if process.poll() is not None:
            break
    return {
        "status": "START_FAILED",
        "url": None,
        "pid": process.pid,
        "route_map_ready": True,
        "required_action": "roadmap",
    }


def stop_server(project_root: Path) -> dict[str, Any]:
    root = project_root.resolve()
    path = metadata_path(root)
    metadata = _load_json(path, {})
    url = str(metadata.get("url") or "")
    token = str(metadata.get("shutdown_token") or "")
    if not url or not token:
        return {"status": "NOT_RUNNING", "stopped": False}
    request = urllib.request.Request(
        url.rstrip("/") + "/api/shutdown",
        data=b"{}",
        method="POST",
        headers={"X-Goal-Roadmap-Token": token, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=1.0) as response:
            response.read()
    except (OSError, urllib.error.URLError):
        pass
    try:
        path.unlink()
    except OSError:
        pass
    return {"status": "STOPPED", "stopped": True}


class RoadmapServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, project_root: Path, token: str):
        super().__init__((HOST, 0), RoadmapHandler)
        self.project_root = project_root.resolve()
        self.shutdown_token = token
        self.started_monotonic = time.monotonic()
        self.last_request_monotonic = self.started_monotonic
        self.shutdown_requested = False


class RoadmapHandler(BaseHTTPRequestHandler):
    server: RoadmapServer

    def log_message(self, _format: str, *_args: Any) -> None:
        return

    def _headers(self, content_type: str, length: int) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'")
        self.end_headers()

    def _json(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._headers("application/json; charset=utf-8", len(body))
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self.server.last_request_monotonic = time.monotonic()
        if self.path == "/api/health":
            self._json({"ok": True, "project_root": str(self.server.project_root), "pid": os.getpid()})
            return
        if self.path == "/api/roadmap":
            self._json(build_snapshot(self.server.project_root))
            return
        if self.path not in {"/", "/index.html"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        html_path = Path(__file__).with_name("roadmap.html")
        try:
            body = html_path.read_bytes()
        except OSError:
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self._headers("text/html; charset=utf-8", len(body))
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        self.server.last_request_monotonic = time.monotonic()
        if self.path != "/api/shutdown" or self.headers.get("X-Goal-Roadmap-Token") != self.server.shutdown_token:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        self._json({"ok": True, "status": "STOPPING"})
        # The server uses a bounded handle_request loop rather than
        # serve_forever(), so shutdown() would wait for a loop that is not
        # running. Let the loop close itself after this response is flushed.
        self.server.shutdown_requested = True


def serve(project_root: Path, token: str, idle_seconds: int = DEFAULT_IDLE_SECONDS) -> int:
    root = project_root.resolve()
    if not (root / ".agent").is_dir():
        return 2
    server = RoadmapServer(root, token)
    host, port = server.server_address
    metadata = {
        "schema_version": SCHEMA_VERSION,
        "status": "RUNNING",
        "pid": os.getpid(),
        "host": host,
        "port": port,
        "url": f"http://{host}:{port}/",
        "project_root": str(root),
        "started_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat(),
        "shutdown_token": token,
    }
    _write_json(metadata_path(root), metadata)
    server.timeout = 1.0
    try:
        while (root / ".agent").is_dir() and not server.shutdown_requested:
            server.handle_request()
            if time.monotonic() - server.last_request_monotonic > idle_seconds:
                break
    finally:
        server.server_close()
        current = _load_json(metadata_path(root), {})
        if current.get("pid") == os.getpid():
            try:
                metadata_path(root).unlink()
            except OSError:
                pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--idle-seconds", type=int, default=DEFAULT_IDLE_SECONDS)
    args = parser.parse_args(argv)
    return serve(Path(args.project_root), args.token, max(10, args.idle_seconds)) if args.serve else 2


if __name__ == "__main__":
    raise SystemExit(main())
