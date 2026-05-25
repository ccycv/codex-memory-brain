#!/usr/bin/env python3
"""Benchmark Codex CLI behavior with and without Codex Memory Brain.

The benchmark is intentionally black-box: it runs `codex exec` on the same
repository and task prompts, captures JSONL events, and scores final answers
against task-specific expected facts.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import time
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
MEMORY_SERVER = PLUGIN_ROOT / "server" / "memory_brain_mcp.py"
DEFAULT_CODEX = "/Applications/Codex.app/Contents/Resources/codex"


TASKS = [
    {
        "id": "local_commands",
        "question": "What are the main local development commands for Askio frontend and backend? Include where they run.",
        "expected": [
            "npm install",
            "npm run dev",
            "docker compose -f docker/supabase-exit/docker-compose.yml up -d",
            "npm run backend:install",
            "npm run backend:dev:api",
        ],
    },
    {
        "id": "route_landmarks",
        "question": "Where are the main frontend route map and backend compatibility API route handlers?",
        "expected": ["src/App.tsx", "backend/apps/api/src/routes", "backend/packages"],
    },
    {
        "id": "supabase_exit",
        "question": "Explain the staged Supabase-exit migration and the key files/env vars involved.",
        "expected": [
            "src/lib/backendClient.ts",
            "src/lib/realtimeClient.ts",
            "VITE_BACKEND_FUNCTION_DRIVER=http",
            "VITE_BACKEND_REALTIME_DRIVER=ws",
            "/functions/v1",
        ],
    },
    {
        "id": "build_blog_seo",
        "question": "Describe the build pipeline for static SEO pages and blog artifacts.",
        "expected": [
            "npm run build",
            "scripts/generate-static-seo-pages.ts",
            "sitemap.xml",
            "content/blog",
            "scripts/blog/generate-blog-artifacts.ts",
            "src/generated",
        ],
    },
    {
        "id": "deployment_shape",
        "question": "What is the current production deployment shape and what older path should not be assumed?",
        "expected": ["GitHub Actions", "Bunny", "deploy-backend-production.yml", "SSH/CloudPanel", "origin/main"],
    },
    {
        "id": "testing_gotcha",
        "question": "What testing gotcha should I know before trusting test coverage in Askio?",
        "expected": ["Vitest", "src/**/*.test.ts", "node:test", "npm run backend:test", "backend/apps/api/src"],
    },
    {
        "id": "backend_client_change",
        "question": "If I change backend client or realtime behavior, what context and files matter most?",
        "expected": [
            "src/lib/backendClient.ts",
            "src/lib/realtimeClient.ts",
            "Supabase",
            "plain HTTP",
            "WebSocket",
        ],
    },
    {
        "id": "newcomer_map",
        "question": "Give a concise map a newcomer should know before editing Askio.",
        "expected": ["Vite React", "src/App.tsx", "backend/apps/api/src/routes", "content/blog", "GitHub Actions"],
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Codex Memory Brain benchmark.")
    parser.add_argument("--repo", default=".", help="Repository to benchmark against. Built-in tasks are Askio-oriented.")
    parser.add_argument("--codex", default=DEFAULT_CODEX, help="Path to codex CLI.")
    parser.add_argument("--model", default="gpt-5.5", help="Codex model.")
    parser.add_argument("--reasoning", default="low", help="Reasoning effort.")
    parser.add_argument("--runs", type=int, default=1, help="Runs per task per mode.")
    parser.add_argument("--max-tasks", type=int, default=len(TASKS), help="Limit number of benchmark tasks.")
    parser.add_argument("--out", default="", help="Output directory. Defaults to benchmarks/results/<timestamp>.")
    return parser.parse_args()


def build_prompt(task: dict[str, Any], repo: str, mode: str) -> str:
    shared = (
        "Do not edit files. Do not run tests. Keep the final answer concise but specific. "
        "Answer only the user question.\n\n"
        f"Question: {task['question']}"
    )
    if mode == "memory":
        return (
            "Use the codex-memory-brain MCP tool first. Call memory_context with "
            f'task="{task["question"]}" and cwd="{repo}". Include the Memory Brain status line, '
            "then answer using any relevant memory and repository facts.\n\n"
            + shared
        )
    return (
        "Do not use codex-memory, memory_context, memory_search, saved memory, or any memory MCP tools. "
        "Use only repository inspection available in this run.\n\n"
        + shared
    )


def codex_command(args: argparse.Namespace, mode: str, out_file: Path) -> list[str]:
    cmd = [
        args.codex,
        "--ask-for-approval",
        "never",
        "exec",
        "--ignore-user-config",
        "--json",
        "--output-last-message",
        str(out_file),
        "-C",
        args.repo,
        "-s",
        "read-only",
        "-m",
        args.model,
        "-c",
        f'model_reasoning_effort="{args.reasoning}"',
    ]
    if mode == "memory":
        cmd.extend(
            [
                "-c",
                'mcp_servers.codex-memory-brain.command="python3"',
                "-c",
                f'mcp_servers.codex-memory-brain.args=["{MEMORY_SERVER}"]',
            ]
        )
    return cmd


def run_codex(args: argparse.Namespace, task: dict[str, Any], mode: str, run_index: int, out_dir: Path) -> dict[str, Any]:
    run_dir = out_dir / mode / f"{task['id']}_run{run_index}"
    run_dir.mkdir(parents=True, exist_ok=True)
    out_file = run_dir / "final.txt"
    events_file = run_dir / "events.jsonl"
    stderr_file = run_dir / "stderr.log"
    prompt = build_prompt(task, args.repo, mode)
    cmd = codex_command(args, mode, out_file) + [prompt]

    started = time.monotonic()
    completed = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    duration = time.monotonic() - started
    events_file.write_text(completed.stdout)
    stderr_file.write_text(completed.stderr)
    final_text = out_file.read_text() if out_file.exists() else ""
    events = parse_events(completed.stdout)
    usage = last_usage(events)
    score = score_answer(final_text, task["expected"])
    return {
        "task_id": task["id"],
        "mode": mode,
        "run": run_index,
        "returncode": completed.returncode,
        "duration_seconds": round(duration, 3),
        "final_path": str(out_file),
        "events_path": str(events_file),
        "stderr_path": str(stderr_file),
        "final_text": final_text,
        "expected": task["expected"],
        "score": score,
        "usage": usage,
        "mcp_tool_calls": count_events(events, "mcp_tool_call"),
        "memory_tool_calls": count_memory_calls(events),
        "command_executions": count_events(events, "command_execution"),
        "agent_messages": count_events(events, "agent_message"),
    }


def parse_events(text: str) -> list[dict[str, Any]]:
    events = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def last_usage(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(events):
        if event.get("type") == "turn.completed":
            return event.get("usage", {})
    return {}


def count_events(events: list[dict[str, Any]], item_type: str) -> int:
    count = 0
    for event in events:
        item = event.get("item")
        if isinstance(item, dict) and item.get("type") == item_type and event.get("type") == "item.completed":
            count += 1
    return count


def count_memory_calls(events: list[dict[str, Any]]) -> int:
    count = 0
    for event in events:
        item = event.get("item")
        if (
            isinstance(item, dict)
            and item.get("type") == "mcp_tool_call"
            and item.get("server") == "codex-memory-brain"
            and event.get("type") == "item.completed"
        ):
            count += 1
    return count


def score_answer(text: str, expected: list[str]) -> dict[str, Any]:
    lower = normalize_for_score(text)
    hits = []
    misses = []
    for phrase in expected:
        if normalize_for_score(phrase) in lower:
            hits.append(phrase)
        else:
            misses.append(phrase)
    total = len(expected)
    return {
        "hits": hits,
        "misses": misses,
        "hit_count": len(hits),
        "total": total,
        "accuracy": round(len(hits) / total, 3) if total else 0.0,
    }


def normalize_for_score(value: str) -> str:
    return value.lower().replace("`", "").replace('"', "").replace("'", "")


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    modes = sorted({result["mode"] for result in results})
    summary: dict[str, Any] = {}
    for mode in modes:
        mode_results = [result for result in results if result["mode"] == mode]
        summary[mode] = {
            "runs": len(mode_results),
            "avg_accuracy": avg(result["score"]["accuracy"] for result in mode_results),
            "avg_hit_count": avg(result["score"]["hit_count"] for result in mode_results),
            "avg_duration_seconds": avg(result["duration_seconds"] for result in mode_results),
            "avg_input_tokens": avg(result["usage"].get("input_tokens", 0) for result in mode_results),
            "avg_output_tokens": avg(result["usage"].get("output_tokens", 0) for result in mode_results),
            "total_memory_tool_calls": sum(result["memory_tool_calls"] for result in mode_results),
            "total_command_executions": sum(result["command_executions"] for result in mode_results),
        }
    return summary


def avg(values) -> float:
    items = [float(value) for value in values]
    return round(sum(items) / len(items), 3) if items else 0.0


def write_report(out_dir: Path, results: list[dict[str, Any]], summary: dict[str, Any]) -> Path:
    lines = ["# Codex Memory Brain Benchmark", ""]
    lines.append(f"Generated: {dt.datetime.now(dt.timezone.utc).isoformat()}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Mode | Runs | Avg Accuracy | Avg Hits | Avg Seconds | Memory Calls | Shell Commands |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for mode, item in summary.items():
        lines.append(
            f"| {mode} | {item['runs']} | {item['avg_accuracy']} | {item['avg_hit_count']} | "
            f"{item['avg_duration_seconds']} | {item['total_memory_tool_calls']} | {item['total_command_executions']} |"
        )
    lines.append("")
    lines.append("## Task Results")
    lines.append("")
    for result in results:
        lines.append(
            f"### {result['task_id']} / {result['mode']} / run {result['run']}"
        )
        lines.append("")
        lines.append(
            f"- score: {result['score']['hit_count']}/{result['score']['total']} "
            f"({result['score']['accuracy']})"
        )
        lines.append(f"- duration: {result['duration_seconds']}s")
        lines.append(f"- memory calls: {result['memory_tool_calls']}")
        lines.append(f"- shell commands: {result['command_executions']}")
        if result["score"]["misses"]:
            lines.append("- misses: " + ", ".join(f"`{miss}`" for miss in result["score"]["misses"]))
        lines.append("")
        snippet = result["final_text"].strip().replace("\n", " ")
        if len(snippet) > 500:
            snippet = snippet[:497] + "..."
        lines.append("> " + snippet)
        lines.append("")
    report = out_dir / "benchmark_report.md"
    report.write_text("\n".join(lines) + "\n")
    return report


def main() -> int:
    args = parse_args()
    out_dir = Path(args.out) if args.out else PLUGIN_ROOT / "benchmarks" / "results" / dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    selected_tasks = TASKS[: max(1, min(args.max_tasks, len(TASKS)))]
    results = []
    for task in selected_tasks:
        for run_index in range(1, args.runs + 1):
            for mode in ["memory", "no_memory"]:
                print(f"running {task['id']} {mode} run {run_index}", flush=True)
                result = run_codex(args, task, mode, run_index, out_dir)
                results.append(result)
                print(
                    f"  score={result['score']['hit_count']}/{result['score']['total']} "
                    f"duration={result['duration_seconds']}s memory_calls={result['memory_tool_calls']}",
                    flush=True,
                )
    summary = summarize(results)
    (out_dir / "results.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    report = write_report(out_dir, results, summary)
    print(json.dumps({"out_dir": str(out_dir), "report": str(report), "summary": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
