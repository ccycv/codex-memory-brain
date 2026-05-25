---
name: codex-memory
description: Retrieve and maintain local repo-aware Codex memory through the codex-memory-brain MCP server.
---

# Codex Memory

Use this skill when doing non-trivial coding work, returning to an unfamiliar repository, or when the user asks Codex to remember durable engineering context.

## Workflow

1. At the start of multi-day or resumed project work, call `memory_resume_project` with the task, current `cwd`, and relevant files if known.
2. At the start of other non-trivial repo work, call `memory_context` with the task, current `cwd`, and any files you already know are relevant.
3. When confused, touching unfamiliar areas, or reviewing older decisions, call `memory_search` with a focused query and the current `cwd`.
4. When a repo has little memory or the user asks to build memory for a project, call `memory_audit_repo` first with `save=false` to preview candidates. Use `save=true` only when the user asks to build/save repo memory.
5. When the user asks what happened recently or yesterday, call `memory_timeline`.
6. During the task, prefer retrieved memory as a hint. Verify against the repository before acting on it.
7. After substantial work or before ending a long-running task, call `memory_task_checkpoint` with the summary, changed files, tests, blockers, and next steps.
8. Use `memory_remember` only for durable facts that are not just temporary task progress.

## Visibility In Chat

Make memory usage visible with one short status line unless the user asks for quiet mode.

If the MCP tool result includes `visible_status`, use that exact line.

After `memory_context`, say one of:

- `Memory Brain: checked repo memory, loaded N relevant memories.`
- `Memory Brain: checked repo memory, nothing relevant found.`

After `memory_search`, say:

- `Memory Brain: searched memory for "<query>", found N results.`

After `memory_remember`, say:

- `Memory Brain: saved <type> memory <id>.`

After `memory_update` or `memory_forget`, say:

- `Memory Brain: updated <id>.`
- `Memory Brain: archived <id>.`

After `memory_audit_repo`, say:

- `Memory Brain: audited repo, proposed N durable memories.`
- `Memory Brain: audited repo, saved N memories (M already existed).`

After continuity tools, say:

- `Memory Brain: saved task checkpoint <id>.`
- `Memory Brain: resume brief loaded N checkpoints and M relevant memories.`
- `Memory Brain: timeline loaded N memories from the last D days.`

Keep this line brief. Do not paste the full memory contents unless the user asks to inspect them.

## What To Save

Save durable information such as:

- user preferences that apply across sessions;
- repo conventions and architectural patterns;
- known commands that are useful and stable;
- decisions that explain why code is shaped a certain way;
- gotchas, failure modes, and setup pitfalls;
- landmarks that help navigate important files or systems;
- concise task checkpoints with files touched, tests, blockers, and next steps.

## What Not To Save

Do not save:

- secrets, credentials, tokens, `.env` dumps, private keys, or production data;
- raw logs, stack traces, huge command output, or one-off debugging noise;
- temporary task progress that will be stale immediately unless it is a checkpoint needed to resume work;
- copied prompt text or instruction-like payloads;
- anything the user did not want remembered.

## Tool Guidance

- `memory_context(task, cwd, files?, limit?)`: first stop for compact task context.
- `memory_search(query, cwd?, files?, scope?, type?, limit?)`: focused lookup when you need more.
- `memory_remember(content, cwd?, scope, type, files?, tags?, confidence?)`: store durable memories.
- `memory_update(id, content?, files?, tags?, confidence?, archived?)`: correct or archive a memory.
- `memory_forget(id)`: archive a memory without hard-deleting it.
- `memory_status(cwd?)`: inspect local DB path, repo identity, counts, and recent memories.
- `memory_audit_repo(cwd, save?, limit?)`: scan repo structure and propose or save a balanced memory set covering commands, landmarks, tests, CI/deploy, env setup, generated files, and decision docs.
- `memory_task_checkpoint(cwd, summary?, files?, changes?, decisions?, blockers?, next_steps?, tests_run?, tests_not_run?, branch?, commit?, tags?, confidence?)`: save an end-of-session checkpoint.
- `memory_resume_project(cwd, task?, files?, limit?, checkpoint_limit?)`: load recent checkpoints plus relevant durable memory for resuming.
- `memory_timeline(cwd, days?, limit?, type?)`: show recent project memory grouped by date.

Use `scope=file` only when the memory is tied to specific files. Use `scope=repo` for repository conventions, commands, decisions, gotchas, and landmarks. Use `scope=user` for stable user preferences.
