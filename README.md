# Codex Memory Brain

Local, repo-aware durable memory for Codex.

Codex Memory Brain is an installable Codex plugin that exposes a local memory layer through MCP. It stores engineering memory in SQLite with FTS5 search, runs fully on your machine, and makes no cloud calls.

## Install

macOS or Linux:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ccycv/codex-memory-brain/HEAD/install.sh)"
```

Then open a new Codex chat or restart Codex.

Verify:

```bash
codex mcp get codex-memory-brain
```

You should see `enabled: true`.

## What It Adds

The plugin registers the `codex-memory-brain` MCP server and the `codex-memory` skill.

Data is stored locally:

```text
~/.codex-memory/memory.db
```

Override the storage location:

```bash
export CODEX_MEMORY_HOME=/path/to/memory-home
```

## Daily Use

Start a project session with:

```text
Use codex-memory. Resume this project first, then continue the task.
```

End substantial work with:

```text
Save a task checkpoint with files, tests, blockers, and next steps.
```

Build memory for a new repo:

```text
Use codex-memory. Run memory_audit_repo for this repo with save=false and show me the proposed memories.
```

Then:

```text
Save the audit memories.
```

## Tools

- `memory_context(task, cwd, files?, limit?)`
- `memory_search(query, cwd?, files?, scope?, type?, limit?)`
- `memory_remember(content, cwd?, scope, type, files?, tags?, confidence?)`
- `memory_update(id, content?, files?, tags?, confidence?, archived?)`
- `memory_forget(id)`
- `memory_status(cwd?)`
- `memory_audit_repo(cwd, save?, limit?)`
- `memory_task_checkpoint(cwd, summary?, files?, changes?, decisions?, blockers?, next_steps?, tests_run?, tests_not_run?, branch?, commit?, tags?, confidence?)`
- `memory_resume_project(cwd, task?, files?, limit?, checkpoint_limit?)`
- `memory_timeline(cwd, days?, limit?, type?)`

## Resources

- `memory://user/profile`
- `memory://repo/current/brief`
- `memory://repo/current/commands`
- `memory://repo/current/decisions`
- `memory://repo/current/gotchas`
- `memory://repo/current/resume`
- `memory://repo/current/timeline`

## Prompts

- `memory-check`
- `memory-status`
- `memory-save`
- `memory-audit`
- `memory-checkpoint`
- `memory-resume`
- `memory-timeline`

If the Codex UI surfaces MCP prompts or resources from the plus menu, these entries give Memory Brain visible options there. The skill also prints a short `Memory Brain:` status line when it checks, searches, saves, updates, archives, resumes, or checkpoints memory.

## Project Continuity

For multi-day work, this is the most important workflow:

1. Start with `memory_resume_project`.
2. Work normally.
3. End with `memory_task_checkpoint`.
4. Use `memory_timeline` when you need to ask what happened recently or yesterday.

Checkpoints capture:

- changed files
- decisions made
- blockers
- next steps
- tests run
- tests not run
- branch and commit when available

## Install From A Local Checkout

```bash
git clone https://github.com/ccycv/codex-memory-brain.git
cd codex-memory-brain
bash install.sh
```

Dry run:

```bash
bash install.sh --dry-run
```

Custom install source:

```bash
CODEX_MEMORY_REPO_URL=https://github.com/YOUR_ORG/codex-memory-brain.git bash -c "$(curl -fsSL https://raw.githubusercontent.com/YOUR_ORG/codex-memory-brain/HEAD/install.sh)"
```

## Development

Run tests:

```bash
python3 -m unittest discover -s tests
```

Validate the plugin manifest if you have the Codex plugin creator skill available:

```bash
uv run --with pyyaml python ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```

Run the Codex CLI memory benchmark:

```bash
python3 benchmarks/codex_memory_benchmark.py --repo /path/to/repo --runs 1 --max-tasks 8
```

The built-in benchmark tasks are Askio-oriented. The runner writes `results.json`, per-run event logs, final answers, and `benchmark_report.md` under `benchmarks/results/<timestamp>/`.

## Safety

The server rejects likely secrets, `.env` dumps, private keys, oversized content, and common prompt-injection phrases before saving memory.

`memory_forget` archives memories instead of hard-deleting them.

## License

MIT
