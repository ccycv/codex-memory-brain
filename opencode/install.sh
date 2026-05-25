#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="codex-memory-brain"
REPO_URL="${CODEX_MEMORY_REPO_URL:-https://github.com/ccycv/codex-memory-brain.git}"
INSTALL_DIR="${CODEX_MEMORY_PLUGIN_DIR:-$HOME/plugins/$PROJECT_NAME}"
OPENCODE_CONFIG="${OPENCODE_CONFIG:-$HOME/.config/opencode/opencode.jsonc}"
SOURCE_DIR=""
DRY_RUN=0

usage() {
  cat <<USAGE
Install Codex Memory Brain for OpenCode.

Usage:
  bash opencode/install.sh [options]

Options:
  --dry-run           Show what would be changed.
  --source DIR        Source checkout directory. Defaults to this repository when run locally.
  --install-dir DIR   Install directory. Defaults to ~/plugins/codex-memory-brain.
  --config FILE       OpenCode config file. Defaults to \$OPENCODE_CONFIG or ~/.config/opencode/opencode.jsonc.
  -h, --help          Show this help.

One-command install:
  bash -c "\$(curl -fsSL https://raw.githubusercontent.com/ccycv/codex-memory-brain/HEAD/opencode/install.sh)"
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --source)
      SOURCE_DIR="$2"
      shift 2
      ;;
    --install-dir)
      INSTALL_DIR="$2"
      shift 2
      ;;
    --config)
      OPENCODE_CONFIG="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

need_python() {
  if command -v python3 >/dev/null 2>&1; then
    return 0
  fi
  echo "python3 is required." >&2
  exit 1
}

script_dir() {
  local src="${BASH_SOURCE[0]-}"
  if [ -z "$src" ] || [ ! -e "$src" ]; then
    pwd
    return 0
  fi
  while [ -L "$src" ]; do
    local dir
    dir="$(cd -P "$(dirname "$src")" >/dev/null 2>&1 && pwd)"
    src="$(readlink "$src")"
    case "$src" in
      /*) ;;
      *) src="$dir/$src" ;;
    esac
  done
  cd -P "$(dirname "$src")/.." >/dev/null 2>&1 && pwd
}

need_python

if [ -z "$SOURCE_DIR" ]; then
  SOURCE_DIR="$(script_dir)"
fi

if [ ! -f "$SOURCE_DIR/server/memory_brain_mcp.py" ]; then
  if ! command -v git >/dev/null 2>&1; then
    echo "git is required when installing from $REPO_URL." >&2
    exit 1
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] would clone or update $REPO_URL into $INSTALL_DIR"
  else
    mkdir -p "$(dirname "$INSTALL_DIR")"
    if [ -d "$INSTALL_DIR/.git" ]; then
      git -C "$INSTALL_DIR" pull --ff-only
    else
      rm -rf "$INSTALL_DIR"
      git clone "$REPO_URL" "$INSTALL_DIR"
    fi
  fi
  SOURCE_DIR="$INSTALL_DIR"
fi

SOURCE_DIR="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$SOURCE_DIR")"
INSTALL_DIR="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$INSTALL_DIR")"
OPENCODE_CONFIG="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$OPENCODE_CONFIG")"

SERVER_PATH="$INSTALL_DIR/server/memory_brain_mcp.py"
INSTRUCTIONS_PATH="$INSTALL_DIR/opencode/memory-brain-instructions.md"

echo "Codex Memory Brain OpenCode installer"
echo "  source:       $SOURCE_DIR"
echo "  install dir:  $INSTALL_DIR"
echo "  config:       $OPENCODE_CONFIG"
echo "  server:       $SERVER_PATH"
echo "  instructions: $INSTRUCTIONS_PATH"
echo "  dry run:      $DRY_RUN"

if [ "$DRY_RUN" -eq 1 ]; then
  exit 0
fi

python3 - "$SOURCE_DIR" "$INSTALL_DIR" "$OPENCODE_CONFIG" <<'PY'
import json
import pathlib
import re
import shutil
import sys
import time

source = pathlib.Path(sys.argv[1])
install_dir = pathlib.Path(sys.argv[2])
config_path = pathlib.Path(sys.argv[3])
server_path = install_dir / "server" / "memory_brain_mcp.py"
instructions_path = install_dir / "opencode" / "memory-brain-instructions.md"


def copy_tree(src: pathlib.Path, dst: pathlib.Path) -> None:
    if src.resolve() == dst.resolve():
        return
    ignore = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".DS_Store")
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=ignore)


def strip_jsonc(text: str) -> str:
    result = []
    i = 0
    in_string = False
    quote = ""
    while i < len(text):
        ch = text[i]
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if in_string:
            result.append(ch)
            if ch == "\\" and nxt:
                result.append(nxt)
                i += 2
                continue
            if ch == quote:
                in_string = False
            i += 1
            continue
        if ch in ("'", '"'):
            in_string = True
            quote = ch
            result.append(ch)
            i += 1
            continue
        if ch == "/" and nxt == "/":
            i = text.find("\n", i)
            if i == -1:
                break
            result.append("\n")
            i += 1
            continue
        if ch == "/" and nxt == "*":
            end = text.find("*/", i + 2)
            i = len(text) if end == -1 else end + 2
            continue
        result.append(ch)
        i += 1
    clean = "".join(result)
    clean = re.sub(r",\s*([}\]])", r"\1", clean)
    return clean.strip()


def load_config(path: pathlib.Path) -> dict:
    if not path.exists() or not path.read_text().strip():
        return {"$schema": "https://opencode.ai/config.json"}
    raw = path.read_text()
    try:
        data = json.loads(strip_jsonc(raw))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Could not parse OpenCode config at {path}: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"OpenCode config must be a JSON object: {path}")
    return data


copy_tree(source, install_dir)
server_path.chmod(server_path.stat().st_mode | 0o111)

config_path.parent.mkdir(parents=True, exist_ok=True)
if config_path.exists():
    backup = config_path.with_suffix(config_path.suffix + f".bak-{time.strftime('%Y%m%d%H%M%S')}")
    shutil.copy2(config_path, backup)
else:
    backup = None

config = load_config(config_path)
config.setdefault("$schema", "https://opencode.ai/config.json")
config.setdefault("mcp", {})
if not isinstance(config["mcp"], dict):
    raise SystemExit("OpenCode config field 'mcp' must be an object")
config["mcp"]["codex-memory-brain"] = {
    "type": "local",
    "command": ["python3", str(server_path)],
    "enabled": True,
    "timeout": 10000,
}

instructions = config.setdefault("instructions", [])
if not isinstance(instructions, list):
    raise SystemExit("OpenCode config field 'instructions' must be an array")
instruction_value = str(instructions_path)
instructions[:] = [item for item in instructions if item != instruction_value]
instructions.append(instruction_value)

config_path.write_text(json.dumps(config, indent=2) + "\n")

print(f"Installed OpenCode MCP server: {server_path}")
print(f"Installed OpenCode instructions: {instructions_path}")
print(f"Updated OpenCode config: {config_path}")
if backup:
    print(f"Backup: {backup}")
print("Run: opencode mcp list")
PY
