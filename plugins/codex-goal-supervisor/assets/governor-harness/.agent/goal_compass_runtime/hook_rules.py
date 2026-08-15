"""Pure, deterministic hook parsing shared by light and ticketed execution."""
from __future__ import annotations

import os
import re
import shlex
from typing import Any


FAILURE_STATUSES = {"error", "failed", "failure"}
EXIT_CODE_FIELDS = ("exit_code", "exit-code", "exitCode", "returncode", "returnCode", "return_code")


def _basename(value: str) -> str:
    name = os.path.basename(value.replace("\\", "/")).lower()
    return name[:-4] if name.endswith(".exe") else name


def shell_segments(command: str) -> list[list[str]]:
    """Split shell syntax enough to inspect literal command boundaries.

    This deliberately does not try to interpret expansions, aliases, or generated
    shell code. Ambiguous commands remain outside the deterministic deny path.
    """
    try:
        lexer = shlex.shlex(command, posix=os.name != "nt", punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token and set(token) <= {";", "&", "|"}:
            if current:
                segments.append(current)
                current = []
            continue
        if os.name == "nt" and len(token) >= 2 and token[0] == token[-1] == '"':
            token = token[1:-1]
        current.append(token)
    if current:
        segments.append(current)
    return segments


def _unwrap_command(tokens: list[str]) -> list[str]:
    row = list(tokens)
    while row and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", row[0]):
        row.pop(0)
    while row and _basename(row[0]) in {"env", "command", "sudo"}:
        wrapper = _basename(row.pop(0))
        while row and row[0].startswith("-"):
            option = row.pop(0)
            if option in {"-u", "--unset", "-C", "--chdir", "-g", "-h", "-p", "-u"} and row:
                row.pop(0)
        if wrapper == "env":
            while row and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", row[0]):
                row.pop(0)
    return row


def destructive_git_command(command: str) -> str | None:
    value_options = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
    for segment in shell_segments(command):
        parts = _unwrap_command(segment)
        if not parts or _basename(parts[0]) != "git":
            continue
        index = 1
        while index < len(parts):
            token = parts[index]
            if token == "--":
                index += 1
                break
            if not token.startswith("-"):
                break
            option = token.split("=", 1)[0]
            index += 1
            if option in value_options and "=" not in token and index < len(parts):
                index += 1
        if index < len(parts) and parts[index].lower() in {"reset", "clean"}:
            return parts[index].lower()
    return None


def _write_mode(mode: str) -> bool:
    return any(flag in mode.lower() for flag in ("w", "a", "x", "+"))


def _literal_python_targets(command: str) -> list[str]:
    paths: list[str] = []
    open_pattern = re.compile(
        r"\b(?:io\.)?open\(\s*(?P<q>['\"])(?P<path>[^'\"]+)(?P=q)\s*,\s*"
        r"(?:mode\s*=\s*)?(?P<mq>['\"])(?P<mode>[^'\"]+)(?P=mq)"
    )
    path_open_pattern = re.compile(
        r"\bPath\(\s*(?P<q>['\"])(?P<path>[^'\"]+)(?P=q)\s*\)\.open\(\s*"
        r"(?P<mq>['\"])(?P<mode>[^'\"]+)(?P=mq)"
    )
    for pattern in (open_pattern, path_open_pattern):
        for match in pattern.finditer(command):
            if _write_mode(match.group("mode")):
                paths.append(match.group("path"))
    for match in re.finditer(
        r"\bPath\(\s*['\"]([^'\"]+)['\"]\s*\)\."
        r"(?:write_text|write_bytes|unlink|rename|replace)\b",
        command,
    ):
        paths.append(match.group(1))
    for match in re.finditer(
        r"\b(?:os\.)?(?:remove|unlink|rmdir)\(\s*['\"]([^'\"]+)['\"]",
        command,
    ):
        paths.append(match.group(1))
    for match in re.finditer(
        r"\b(?:os\.)?(?:rename|replace)\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]",
        command,
    ):
        paths.extend(match.groups())
    return paths


def _sed_in_place_targets(parts: list[str]) -> list[str]:
    args = parts[1:]
    inplace = False
    expression_supplied = False
    positional: list[str] = []
    index = 0
    while index < len(args):
        token = args[index]
        if token == "--":
            positional.extend(args[index + 1 :])
            break
        if token == "-i" or token == "--in-place":
            inplace = True
            index += 1
            if index < len(args) and args[index] == "":
                index += 1
            continue
        if token.startswith("-i") or token.startswith("--in-place="):
            inplace = True
            index += 1
            continue
        if token in {"-e", "--expression", "-f", "--file"}:
            expression_supplied = True
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        positional.append(token)
        index += 1
    if not inplace:
        return []
    if not expression_supplied and positional:
        positional = positional[1:]
    return positional


def _command_targets(parts: list[str]) -> list[str]:
    parts = _unwrap_command(parts)
    if not parts:
        return []
    executable = _basename(parts[0])
    args = parts[1:]
    if executable in {"rm", "unlink", "rmdir", "touch", "mkdir"}:
        return [item for item in args if item and not item.startswith("-")]
    if executable in {"cp", "mv", "install"}:
        values = [item for item in args if item and not item.startswith("-")]
        return values[-1:] if values else []
    if executable == "tee":
        return [item for item in args if item and not item.startswith("-")]
    if executable == "truncate":
        targets: list[str] = []
        index = 0
        while index < len(args):
            token = args[index]
            if token in {"-s", "--size", "-o", "--io-blocks"}:
                index += 2
            elif token.startswith("-"):
                index += 1
            else:
                targets.append(token)
                index += 1
        return targets
    if executable == "sed":
        return _sed_in_place_targets(parts)
    return []


def shell_write_targets(command: str) -> list[str]:
    paths = _literal_python_targets(command)
    for match in re.finditer(r"(?<![<>])(?:[012]?>>|[012]?>)\s*([^\s;&|]+)", command):
        paths.append(match.group(1).strip("'\""))
    for segment in shell_segments(command):
        paths.extend(_command_targets(segment))
    return list(dict.fromkeys(path for path in paths if path))


def _nonzero_exit_code(value: Any) -> bool:
    if value is None or value == "":
        return False
    try:
        return int(value) != 0
    except (TypeError, ValueError):
        return False


def tool_failed(event: dict[str, Any]) -> bool:
    response = event.get("tool_response") or event.get("toolResponse") or event.get("output") or {}
    mappings = [response] if isinstance(response, dict) else []
    mappings.append(event)
    for mapping in mappings:
        if mapping.get("is_error") is True or mapping.get("isError") is True:
            return True
        if str(mapping.get("status") or "").lower() in FAILURE_STATUSES:
            return True
        if any(_nonzero_exit_code(mapping.get(field)) for field in EXIT_CODE_FIELDS):
            return True
    return bool(event.get("error"))
