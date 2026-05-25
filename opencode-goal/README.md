# OpenCode Goal

OpenCode Goal is a local `/goal`-like feature for OpenCode.

It combines:

- a native OpenCode plugin with goal tools;
- system prompt injection for the active session goal;
- custom slash commands such as `/goal`, `/goal-status`, and `/goal-complete`;
- local JSON persistence under `~/.local/share/opencode-goal/goals.json`.

## Install

macOS or Linux:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ccycv/codex-memory-brain/HEAD/opencode-goal/install.sh)"
```

From a local checkout:

```bash
bash opencode-goal/install.sh
```

Restart OpenCode after installation.

The installer also adds a terminal runner command at `~/.local/bin/opencode-goal-run`.
Make sure `~/.local/bin` is on your `PATH` if your shell does not already include it.

## Commands

In the OpenCode TUI:

```text
/goal Ship OpenCode goal support with local persistence
/goal-status
/goal-update blocked on failing tests
/goal-complete Implemented and tested
/goal-clear no longer needed
```

From the CLI:

```bash
opencode run --command goal "Ship OpenCode goal support with local persistence"
opencode run --command goal-status
opencode run --command goal-complete "Implemented and tested"
```

## Controlled Goal Runner

For longer tasks, use the optional runner to keep OpenCode working in repeated
non-interactive turns until the goal reaches a stop condition:

```bash
opencode-goal-run \
  --model opencode-go/qwen3.6-plus \
  --max-steps 8 \
  --max-minutes 30 \
  --goal "Ship the current feature end to end"
```

For trusted local worktrees where you want OpenCode to approve tool permissions
without stopping, pass OpenCode's explicit permission flag:

```bash
opencode-goal-run \
  --model opencode-go/qwen3.6-plus \
  --dangerously-skip-permissions \
  --goal "Run the test suite, fix failures, and stop when green"
```

Continue an existing goal session:

```bash
opencode-goal-run \
  --model opencode-go/qwen3.6-plus \
  --session ses_your_session_id \
  --max-steps 4
```

The runner stops when the goal is `complete`, `blocked`, `paused`,
`budget_limited`, or `usage_limited`. It also stops on OpenCode run failure,
`--max-steps`, or `--max-minutes`. When it hits a safety limit, it marks the
goal `paused` so the next run can resume intentionally.

## Tools

The plugin adds these tools:

- `goal_set`
- `goal_status`
- `goal_update`
- `goal_complete`
- `goal_clear`
- `goal_list`

The active goal is automatically injected into the model's system context on each turn until it is completed or cleared.
