# OpenCode Memory Brain

OpenCode support uses the same local MCP server and SQLite database as the Codex plugin.

## Install

macOS or Linux:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ccycv/codex-memory-brain/HEAD/opencode/install.sh)"
```

From a local checkout:

```bash
bash opencode/install.sh
```

## What The Installer Changes

The installer updates `~/.config/opencode/opencode.jsonc` with:

```json
{
  "mcp": {
    "codex-memory-brain": {
      "type": "local",
      "command": ["python3", "/path/to/codex-memory-brain/server/memory_brain_mcp.py"],
      "enabled": true,
      "timeout": 10000
    }
  },
  "instructions": [
    "/path/to/codex-memory-brain/opencode/memory-brain-instructions.md"
  ]
}
```

It creates a timestamped backup of the previous OpenCode config before writing.

## Verify

```bash
opencode mcp list
```

You should see:

```text
codex-memory-brain connected
```

## Long-Run Workflow

OpenCode loads `memory-brain-instructions.md` globally. For multi-day work, it should:

- call `memory_resume_project` at the start;
- call `memory_search` when it needs older decisions or gotchas;
- call `memory_task_checkpoint` before ending substantial work;
- show compact `Memory Brain:` status lines.

The database remains local at `~/.codex-memory/memory.db` unless `CODEX_MEMORY_HOME` is set in the MCP server environment.
