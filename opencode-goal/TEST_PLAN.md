# OpenCode Goal Test Plan

This plan verifies that OpenCode Goal behaves like a local `/goal` feature for OpenCode.

## Scope

- Install-time config wiring for commands and plugin loading.
- Slash-command behavior through OpenCode's command runner.
- Goal persistence by session ID.
- Active-goal injection across turns.
- Goal update and completion flow.
- Isolation from the real user goal store during automated tests.

## Environment

- OpenCode CLI installed.
- `@opencode-ai/plugin` available in the OpenCode config directory.
- `goal-brain.js` installed in the OpenCode global plugin directory.
- Tests use a temporary `OPENCODE_GOAL_HOME`.

## Automated Smoke Flow

Run from the repository root.

```bash
goal_home="$(mktemp -d)"
export OPENCODE_GOAL_HOME="$goal_home"

opencode run --model opencode-go/qwen3.6-plus --format json \
  --command goal "Validate OpenCode Goal with slash commands" \
  > /tmp/opencode-goal-set.jsonl

session_id="$(python3 - <<'PY'
import json
for line in open('/tmp/opencode-goal-set.jsonl'):
    data = json.loads(line)
    if data.get('sessionID'):
        print(data['sessionID'])
        break
PY
)"

opencode run --model opencode-go/qwen3.6-plus --format json \
  --session "$session_id" \
  --command goal-status \
  > /tmp/opencode-goal-status.jsonl

opencode run --model opencode-go/qwen3.6-plus --format json \
  --session "$session_id" \
  "Without calling goal tools, tell me the active goal from your current context. Do not edit files." \
  > /tmp/opencode-goal-context.jsonl

opencode run --model opencode-go/qwen3.6-plus --format json \
  --session "$session_id" \
  --command goal-update "progress: status and context checks passed; next steps: complete the test" \
  > /tmp/opencode-goal-update.jsonl

opencode run --model opencode-go/qwen3.6-plus --format json \
  --session "$session_id" \
  --command goal-complete "OpenCode Goal smoke flow passed" \
  > /tmp/opencode-goal-complete.jsonl

python3 - "$goal_home/goals.json" "$session_id" <<'PY'
import json
import sys

store = json.load(open(sys.argv[1]))
goal = store["sessions"][sys.argv[2]]

assert goal["objective"] == "Validate OpenCode Goal with slash commands"
assert goal["status"] == "complete"
assert goal["progress"] == "OpenCode Goal smoke flow passed"
assert goal["next_steps"] == ["complete the test"]
assert any(item["event"] == "set" for item in goal["history"])
assert any(item["event"] == "update" for item in goal["history"])
assert any(item["event"] == "complete" for item in goal["history"])

print("OpenCode Goal smoke flow passed")
PY
```

## Expected Results

- `/goal` command calls `goal_set`.
- `/goal-status` command calls `goal_status`.
- A later turn can state the active goal from injected context.
- `/goal-update` persists progress and next steps.
- `/goal-complete` marks the goal complete.
- The JSON store contains `set`, `update`, and `complete` history events.

## Controlled Runner Smoke Flow

Run from the repository root.

```bash
goal_home="$(mktemp -d)"

OPENCODE_GOAL_HOME="$goal_home" python3 opencode-goal/goal-runner.py \
  --model opencode-go/qwen3.6-plus \
  --max-steps 2 \
  --goal "Runner smoke test" \
  --step-prompt "Call goal_complete with summary 'runner smoke test completed'. Do not edit files." \
  --json
```

Expected result:

- The command exits `0`.
- The JSON summary reason is `terminal_status:complete`.
- The goal store marks the session goal `complete`.
- The run summary includes a `goal_complete` tool call.

Safety-limit check:

```bash
goal_home="$(mktemp -d)"
set +e
OPENCODE_GOAL_HOME="$goal_home" python3 opencode-goal/goal-runner.py \
  --model opencode-go/qwen3.6-plus \
  --max-steps 1 \
  --goal "Runner max step smoke test" \
  --step-prompt "Call goal_update with progress 'still working' and next_steps ['continue later']; do not complete the goal. Do not edit files." \
  --json
runner_rc="$?"
set -e
test "$runner_rc" -eq 2
```

Expected result:

- The command exits `2`.
- The JSON summary reason is `max_steps`.
- The goal store marks the session goal `paused`.
- The history includes a `runner_paused` event.

## Manual TUI Checks

Restart OpenCode after install, then in the TUI run:

```text
/goal Validate TUI command behavior
/goal-status
/goal-update progress: TUI status works; next steps: finish manual check
/goal-complete Manual TUI check passed
```

Confirm that each command appears in slash-command completion and the assistant shows compact goal status.

## Negative Checks

- Run `/goal-status` in a new session before setting a goal. It should report no active goal.
- Run `/goal-clear` after setting a temporary goal. A following `/goal-status` should report no active goal.
- Run the automated flow with `OPENCODE_GOAL_HOME` set to a temp directory. The real user goal store must not change.
