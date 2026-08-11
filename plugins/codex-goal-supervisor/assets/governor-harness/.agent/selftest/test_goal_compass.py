#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".agent" / "goal_compass.py"
RUNTIME_PACKAGE = ROOT / ".agent" / "goal_compass_runtime"


def load_module():
    agent_root = str(SCRIPT.parent)
    if agent_root not in sys.path:
        sys.path.insert(0, agent_root)
    spec = importlib.util.spec_from_file_location("goal_compass_selftest", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOAL_COMPASS = load_module()


@contextlib.contextmanager
def pushd(path: Path):
    old = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def run(repo: Path, *args: str, check: bool = True):
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = 0
    with pushd(repo), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            code = int(GOAL_COMPASS.main(list(args)))
        except SystemExit as exc:
            code = int(exc.code or 0) if isinstance(exc.code, int) else 1
    if check and code != 0:
        raise AssertionError(stdout.getvalue() + stderr.getvalue())
    return code, stdout.getvalue(), stderr.getvalue()


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        repo = Path(raw)
        (repo / ".agent").mkdir()
        shutil.copy2(SCRIPT, repo / ".agent" / "goal_compass.py")
        shutil.copytree(
            RUNTIME_PACKAGE,
            repo / ".agent" / "goal_compass_runtime",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        reuse_fixture = repo / "reuse-probe-empty.json"
        reuse_fixture.write_text('{"total_count":0,"items":[]}\n', encoding="utf-8")
        previous_reuse_fixture = os.environ.get("GOAL_COMPASS_REUSE_PROBE_FIXTURE")
        os.environ["GOAL_COMPASS_REUSE_PROBE_FIXTURE"] = str(reuse_fixture)
        run(repo, "init")
        if not (repo / ".agent" / "protocols" / "llm_judge.schema.json").exists():
            raise AssertionError("init did not write the LLM Judge schema")
        goal = "Maintain a bounded local project with machine-checkable results."
        definition = {
            "precise_goal": goal,
            "problem_statement": "The local project needs one reproducible bounded execution path.",
            "current_state": "No verified bounded artifact exists.",
            "desired_state": "One artifact is produced and proven by machine-checkable evidence.",
            "stakeholders": ["local operator"],
            "source_requirements": ["Keep the workflow bounded", "Require machine-checkable evidence"],
            "first_principles": [
                {
                    "principle": "Completion requires observable evidence.",
                    "rationale": "A completion claim without evidence cannot be reproduced.",
                    "implications": ["Every result has an acceptance consumer"],
                },
                {
                    "principle": "One ticket owns one bounded result.",
                    "rationale": "Mixed outcomes hide drift and make failure ambiguous.",
                    "implications": ["Unrelated ideas go to backlog"],
                },
            ],
            "process": {
                "entry_conditions": ["The North Star definition is available"],
                "nodes": [
                    {
                        "node_id": "N1",
                        "name": "Create artifact",
                        "objective": "Produce the bounded selftest artifact.",
                        "inputs": ["selftest ticket"],
                        "actions": ["write the expected artifact"],
                        "outputs": ["tests/smoke.test.txt"],
                        "exit_criteria": ["the artifact exists with expected content"],
                        "dependencies": [],
                        "execution_mode": "SERIAL",
                        "contribution_to_goal": "Produces the only product artifact required by the bounded selftest goal.",
                    },
                    {
                        "node_id": "N2",
                        "name": "Verify artifact",
                        "objective": "Prove the artifact satisfies its acceptance.",
                        "inputs": ["tests/smoke.test.txt"],
                        "actions": ["run Goal Compass check and close"],
                        "outputs": ["PASS terminal ticket"],
                        "exit_criteria": ["machine acceptance passes"],
                        "dependencies": ["N1"],
                        "execution_mode": "SERIAL",
                        "contribution_to_goal": "Converts the artifact into machine evidence that the bounded goal is complete.",
                    },
                ],
                "completion_conditions": ["The ticket is archived as PASS"],
            },
            "deliverables": [
                {
                    "name": "Verified selftest artifact",
                    "description": "One local text artifact with frozen acceptance.",
                    "format": "text file plus terminal ticket record",
                    "consumer": "Goal Compass selftest",
                    "acceptance": ["artifact contains ok", "ticket closes PASS"],
                }
            ],
            "final_acceptance": [
                {
                    "criterion": "The bounded artifact passes and the ticket closes.",
                    "evidence": "tests/smoke.test.txt and done ticket",
                    "validation_method": "run check followed by close",
                }
            ],
            "constraints": ["Do not add product-specific behavior"],
            "non_goals": ["Full application implementation"],
            "assumptions": ["The local filesystem is writable"],
            "open_questions": [],
        }
        definition_path = repo / "goal-definition.json"
        definition_path.write_text(json.dumps(definition, indent=2) + "\n", encoding="utf-8")
        run(
            repo,
            "goal-set",
            "--text",
            goal,
            "--definition-file",
            str(definition_path.relative_to(repo)),
            "--require-detailed",
        )
        convergence = json.loads(run(repo, "convergence")[1])["convergence"]
        if convergence["goal_stack"]["l0_final_goal"] != goal:
            raise AssertionError(convergence)
        if not convergence["goal_stack"]["l1_success_criteria"]:
            raise AssertionError("convergence state did not project success criteria")
        ticket_path = repo / ".agent" / "tickets" / "pending" / "SELFTEST-001.json"
        ticket = {
            "ticket_id": "SELFTEST-001",
            "title": "Goal Compass neutral selftest",
            "global_goal": goal,
            "why_now": "Verify the bounded execution lifecycle without injecting a product domain.",
            "task_goal": "Create and verify one neutral smoke-test artifact.",
            "status": "PENDING",
            "acceptance_ready": True,
            "must_do": ["Verify the neutral smoke-test artifact"],
            "must_not_do": ["Do not add unrelated product behavior"],
            "anti_patterns": ["unrelated product implementation"],
            "allowed_paths": ["tests/**"],
            "forbidden_paths": [".agent/**", ".codex/**"],
            "acceptance": {
                "commands_pass": [],
                "files_exist": ["tests/smoke.test.txt"],
                "contains": [],
                "assertions": [],
                "files_not_changed": [".agent/**"],
                "max_changed_files": 5,
                "max_diff_lines": 300,
            },
            "validation_ids": [],
            "budget": {"max_minutes": 5, "max_tool_calls": 10, "max_changed_files": 5, "max_diff_lines": 300},
            "drift_signals": ["Starts implementing an unrelated product"],
            "backlog_only": ["Product-specific examples"],
        }
        ticket_path.write_text(json.dumps(ticket, indent=2) + "\n", encoding="utf-8")
        (repo / "tests").mkdir()
        (repo / "tests" / "smoke.test.txt").write_text("ok\n", encoding="utf-8")
        run(repo, "start", str(ticket_path.relative_to(repo)))
        convergence = json.loads(run(repo, "convergence")[1])["convergence"]
        if convergence["goal_stack"]["l3_current_action"] != ticket["task_goal"]:
            raise AssertionError(convergence)
        check = json.loads(run(repo, "check")[1])
        if check["status"] != "PASS_READY":
            raise AssertionError(check)
        company = json.loads(run(repo, "company-status")[1])["company_subagents"]
        for index, role in enumerate(company.get("missing_roles", [])):
            agent_id = f"selftest-{role}-{index}"
            run(
                repo, "company-record", "--role", role, "--agent-id", agent_id, "--status", "COMPLETED",
                "--result-hash", f"selftest-{role}-result", "--summary", "selftest receipt",
            )
        close = json.loads(run(repo, "close")[1])
        if close["status"] != "PASS":
            raise AssertionError(close)
        if previous_reuse_fixture is None:
            os.environ.pop("GOAL_COMPASS_REUSE_PROBE_FIXTURE", None)
        else:
            os.environ["GOAL_COMPASS_REUSE_PROBE_FIXTURE"] = previous_reuse_fixture
    print("Goal Compass selftest OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
