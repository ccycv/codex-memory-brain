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

## Tools

The plugin adds these tools:

- `goal_set`
- `goal_status`
- `goal_update`
- `goal_complete`
- `goal_clear`
- `goal_list`

The active goal is automatically injected into the model's system context on each turn until it is completed or cleared.
