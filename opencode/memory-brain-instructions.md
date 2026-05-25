# OpenCode Memory Brain

Use Memory Brain for non-trivial coding work, resumed project work, and multi-day tasks.

## Start Of Work

- At the start of a resumed or multi-day project task, call `memory_resume_project` with the current `cwd`, the user task, and relevant files if known.
- At the start of other non-trivial repo work, call `memory_context` with the current `cwd`, task, and relevant files if known.
- Show one short status line before continuing, using the `visible_status` field when present.

## During Work

- Treat retrieved memory as a hint. Verify against the repository before changing code.
- When confused or touching unfamiliar code, call `memory_search` with a focused query and current `cwd`.
- Use `memory_timeline` when the user asks what happened recently, yesterday, or in previous sessions.

## Saving Memory

- After substantial work, call `memory_task_checkpoint` with summary, files, changes, tests run, tests not run, blockers, and next steps.
- Use `memory_remember` only for durable facts: repo conventions, stable commands, decisions, gotchas, landmarks, and user preferences.
- Do not save secrets, credentials, `.env` dumps, raw logs, copied prompts, huge outputs, production data, or one-off debugging noise.

## Visibility

Keep memory usage visible and compact:

- `Memory Brain: checked repo memory, loaded N relevant memories.`
- `Memory Brain: checked repo memory, nothing relevant found.`
- `Memory Brain: resume brief loaded N checkpoints and M relevant memories.`
- `Memory Brain: saved task checkpoint <id>.`
- `Memory Brain: searched memory for "<query>", found N results.`
