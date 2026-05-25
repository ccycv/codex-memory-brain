#!/usr/bin/env python3
"""Controlled OpenCode goal runner.

This script drives OpenCode in repeated non-interactive turns until the active
goal reaches a terminal status or a configured safety limit is hit.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


TERMINAL_STATUSES = {"complete", "blocked", "budget_limited", "usage_limited", "paused"}
DEFAULT_STEP_PROMPT = """Continue working toward the active OpenCode goal.

Run one focused step. If the goal is already satisfied, call goal_complete with a concise summary.
If more work remains, call goal_update with progress and next_steps.
If blocked, call goal_update with status="blocked" and concise blockers.

Do not ask the user to continue unless the goal is blocked. Keep the final reply compact."""


def default_goal_home() -> Path:
    return Path(os.environ.get("OPENCODE_GOAL_HOME", Path.home() / ".local" / "share" / "opencode-goal")).expanduser()


def read_goal(goal_home: Path, session_id: str) -> dict[str, Any] | None:
    path = goal_home / "goals.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    goal = data.get("sessions", {}).get(session_id)
    if not isinstance(goal, dict) or goal.get("archived"):
        return None
    return goal


def write_goal(goal_home: Path, session_id: str, goal: dict[str, Any]) -> None:
    path = goal_home / "goals.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        data = json.loads(path.read_text())
    else:
        data = {"version": 1, "sessions": {}}
    data.setdefault("sessions", {})[session_id] = goal
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(path)


def append_history(goal: dict[str, Any], event: str, **payload: Any) -> None:
    history = goal.setdefault("history", [])
    if not isinstance(history, list):
        history = []
        goal["history"] = history
    history.append({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, **payload})
    if len(history) > 50:
        del history[:-50]


def mark_goal(goal_home: Path, session_id: str, status: str, progress: str) -> dict[str, Any] | None:
    goal = read_goal(goal_home, session_id)
    if not goal:
        return None
    goal["status"] = status
    goal["progress"] = progress
    goal["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    append_history(goal, "runner_" + status, progress=progress)
    write_goal(goal_home, session_id, goal)
    return goal


def parse_jsonl(text: str) -> dict[str, Any]:
    session_id = ""
    tools: list[str] = []
    texts: list[str] = []
    errors: list[str] = []
    events = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            errors.append(line)
            continue
        events += 1
        if not session_id and isinstance(event.get("sessionID"), str):
            session_id = event["sessionID"]
        event_type = event.get("type")
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        if event_type == "tool_use":
            tool_name = part.get("tool")
            if isinstance(tool_name, str):
                tools.append(tool_name)
        elif event_type == "text":
            text_part = part.get("text")
            if isinstance(text_part, str):
                texts.append(text_part)
        elif event_type in {"error", "step_error"}:
            errors.append(json.dumps(event))
    return {"session_id": session_id, "tools": tools, "texts": texts, "errors": errors, "events": events}


def run_opencode(
    *,
    cwd: Path,
    env: dict[str, str],
    model: str | None,
    agent: str | None = None,
    variant: str | None = None,
    dangerously_skip_permissions: bool = False,
    session_id: str | None = None,
    command_name: str | None = None,
    message: str = "",
    title: str | None = None,
) -> dict[str, Any]:
    cmd = ["opencode", "run", "--format", "json"]
    if model:
        cmd.extend(["--model", model])
    if agent:
        cmd.extend(["--agent", agent])
    if variant:
        cmd.extend(["--variant", variant])
    if dangerously_skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    if session_id:
        cmd.extend(["--session", session_id])
    if command_name:
        cmd.extend(["--command", command_name])
    if title:
        cmd.extend(["--title", title])
    if message:
        cmd.append(message)
    proc = subprocess.run(cmd, cwd=str(cwd), env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    parsed = parse_jsonl(proc.stdout)
    parsed.update(
        {
            "returncode": proc.returncode,
            "stderr": proc.stderr.strip(),
            "command": cmd,
        }
    )
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run OpenCode until a session goal reaches a terminal status.")
    parser.add_argument("--goal", help="Set a new goal before running.")
    parser.add_argument("--session", help="Continue an existing OpenCode session with an active goal.")
    parser.add_argument("--model", help="OpenCode model, e.g. opencode-go/qwen3.6-plus.")
    parser.add_argument("--agent", help="OpenCode agent to use.")
    parser.add_argument("--variant", help="OpenCode model variant/reasoning effort.")
    parser.add_argument(
        "--dangerously-skip-permissions",
        action="store_true",
        help="Pass OpenCode's permission auto-approval flag. Use only in trusted worktrees.",
    )
    parser.add_argument("--cwd", default=".", help="Working directory for opencode run.")
    parser.add_argument("--goal-home", default=str(default_goal_home()), help="Goal store directory.")
    parser.add_argument("--max-steps", type=int, default=8, help="Maximum autonomous continuation steps.")
    parser.add_argument("--max-minutes", type=float, default=30.0, help="Wall-clock safety limit.")
    parser.add_argument("--step-prompt", default=DEFAULT_STEP_PROMPT, help="Prompt sent on each autonomous continuation step.")
    parser.add_argument("--json", action="store_true", help="Print JSON summary.")
    parser.add_argument("--no-mark-limit", action="store_true", help="Do not mark the goal paused when limits are reached.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.goal and not args.session:
        raise SystemExit("Provide --goal for a new goal or --session to continue an existing goal.")
    if args.goal and args.session:
        raise SystemExit("Provide either --goal or --session, not both.")
    if args.max_steps < 1:
        raise SystemExit("--max-steps must be at least 1.")
    cwd = Path(args.cwd).expanduser().resolve()
    if not cwd.exists():
        raise SystemExit(f"Working directory does not exist: {cwd}")
    goal_home = Path(args.goal_home).expanduser().resolve()
    env = os.environ.copy()
    env["OPENCODE_GOAL_HOME"] = str(goal_home)

    started_at = time.monotonic()
    runs: list[dict[str, Any]] = []
    session_id = args.session or ""

    if args.goal:
        initial = run_opencode(
            cwd=cwd,
            env=env,
            model=args.model,
            agent=args.agent,
            variant=args.variant,
            dangerously_skip_permissions=args.dangerously_skip_permissions,
            command_name="goal",
            message=args.goal,
            title="OpenCode Goal Runner",
        )
        runs.append({"phase": "set_goal", **initial})
        if initial["returncode"] != 0:
            summary = {"status": "run_failed", "reason": "goal command failed", "runs": runs}
            print(json.dumps(summary, indent=2) if args.json else "Goal runner failed while setting the goal.")
            return 1
        session_id = initial.get("session_id") or ""

    if not session_id:
        raise SystemExit("Could not determine OpenCode session id.")

    final_reason = ""
    final_goal: dict[str, Any] | None = None
    steps = 0

    while True:
        final_goal = read_goal(goal_home, session_id)
        status = str((final_goal or {}).get("status") or "")
        if status in TERMINAL_STATUSES:
            final_reason = f"terminal_status:{status}"
            break
        elapsed_minutes = (time.monotonic() - started_at) / 60
        if elapsed_minutes >= args.max_minutes:
            final_reason = "max_minutes"
            if not args.no_mark_limit:
                final_goal = mark_goal(goal_home, session_id, "paused", f"Goal runner paused after {args.max_minutes:g} minutes.")
            break
        if steps >= args.max_steps:
            final_reason = "max_steps"
            if not args.no_mark_limit:
                final_goal = mark_goal(goal_home, session_id, "paused", f"Goal runner paused after {args.max_steps} steps.")
            break

        steps += 1
        run = run_opencode(
            cwd=cwd,
            env=env,
            model=args.model,
            agent=args.agent,
            variant=args.variant,
            dangerously_skip_permissions=args.dangerously_skip_permissions,
            session_id=session_id,
            message=args.step_prompt,
            title=f"OpenCode Goal Runner Step {steps}",
        )
        runs.append({"phase": "step", "step": steps, **run})
        if run["returncode"] != 0:
            final_reason = "run_failed"
            final_goal = mark_goal(goal_home, session_id, "blocked", "OpenCode run failed: " + (run["stderr"] or "unknown error"))
            break

    summary = {
        "session_id": session_id,
        "goal_home": str(goal_home),
        "status": (final_goal or {}).get("status"),
        "objective": (final_goal or {}).get("objective"),
        "reason": final_reason,
        "steps": steps,
        "runs": [
            {
                "phase": run.get("phase"),
                "step": run.get("step"),
                "returncode": run.get("returncode"),
                "tools": run.get("tools", []),
                "texts": run.get("texts", []),
                "stderr": run.get("stderr", ""),
            }
            for run in runs
        ],
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"Goal runner stopped: {summary['reason']}")
        print(f"Session: {session_id}")
        print(f"Status: {summary['status']}")
        print(f"Steps: {steps}")
    return 0 if final_reason.startswith("terminal_status:complete") else 2


if __name__ == "__main__":
    sys.exit(main())
