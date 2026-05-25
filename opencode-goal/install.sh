#!/usr/bin/env bash
set -euo pipefail

PROJECT_NAME="codex-memory-brain"
REPO_URL="${CODEX_MEMORY_REPO_URL:-https://github.com/ccycv/codex-memory-brain.git}"
SOURCE_DIR=""
INSTALL_DIR="${CODEX_MEMORY_PLUGIN_DIR:-$HOME/plugins/$PROJECT_NAME}"
OPENCODE_CONFIG="${OPENCODE_CONFIG:-$HOME/.config/opencode/opencode.jsonc}"
OPENCODE_PLUGIN_DIR="${OPENCODE_PLUGIN_DIR:-$HOME/.config/opencode/plugins}"
BIN_DIR="${OPENCODE_GOAL_BIN_DIR:-$HOME/.local/bin}"
DRY_RUN=0

usage() {
  cat <<USAGE
Install OpenCode Goal, a /goal-like local goal tracker for OpenCode.

Usage:
  bash opencode-goal/install.sh [options]

Options:
  --dry-run           Show what would be changed.
  --source DIR        Source checkout directory. Defaults to this repository when run locally.
  --install-dir DIR   Install directory. Defaults to ~/plugins/codex-memory-brain.
  --config FILE       OpenCode config file. Defaults to \$OPENCODE_CONFIG or ~/.config/opencode/opencode.jsonc.
  --plugin-dir DIR    OpenCode global plugin dir. Defaults to ~/.config/opencode/plugins.
  --bin-dir DIR       Directory for opencode-goal-run. Defaults to ~/.local/bin.
  -h, --help          Show this help.

One-command install:
  bash -c "\$(curl -fsSL https://raw.githubusercontent.com/ccycv/codex-memory-brain/HEAD/opencode-goal/install.sh)"
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
    --plugin-dir)
      OPENCODE_PLUGIN_DIR="$2"
      shift 2
      ;;
    --bin-dir)
      BIN_DIR="$2"
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

if [ ! -f "$SOURCE_DIR/opencode-goal/goal-plugin.js" ]; then
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
OPENCODE_PLUGIN_DIR="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$OPENCODE_PLUGIN_DIR")"
BIN_DIR="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$BIN_DIR")"

echo "OpenCode Goal installer"
echo "  source:      $SOURCE_DIR"
echo "  install dir: $INSTALL_DIR"
echo "  config:      $OPENCODE_CONFIG"
echo "  plugin dir:  $OPENCODE_PLUGIN_DIR"
echo "  bin dir:     $BIN_DIR"
echo "  dry run:     $DRY_RUN"

if [ "$DRY_RUN" -eq 1 ]; then
  exit 0
fi

python3 - "$SOURCE_DIR" "$INSTALL_DIR" "$OPENCODE_CONFIG" "$OPENCODE_PLUGIN_DIR" "$BIN_DIR" <<'PY'
import json
import pathlib
import re
import shlex
import shutil
import sys
import time

source = pathlib.Path(sys.argv[1])
install_dir = pathlib.Path(sys.argv[2])
config_path = pathlib.Path(sys.argv[3])
plugin_dir = pathlib.Path(sys.argv[4])
bin_dir = pathlib.Path(sys.argv[5])
package_json = config_path.parent / "package.json"


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
    data = json.loads(strip_jsonc(path.read_text()))
    if not isinstance(data, dict):
        raise SystemExit(f"OpenCode config must be a JSON object: {path}")
    return data


copy_tree(source, install_dir)

config_path.parent.mkdir(parents=True, exist_ok=True)
plugin_dir.mkdir(parents=True, exist_ok=True)
plugin_target = plugin_dir / "goal-brain.js"
shutil.copy2(install_dir / "opencode-goal" / "goal-plugin.js", plugin_target)

runner_source = install_dir / "opencode-goal" / "goal-runner.py"
if not runner_source.exists():
    raise SystemExit(f"Missing goal runner script: {runner_source}")
bin_dir.mkdir(parents=True, exist_ok=True)
runner_target = bin_dir / "opencode-goal-run"
runner_target.write_text(
    "#!/usr/bin/env bash\n"
    "set -euo pipefail\n"
    f"exec python3 {shlex.quote(str(runner_source))} \"$@\"\n"
)
runner_target.chmod(0o755)

if package_json.exists():
    package_data = json.loads(package_json.read_text())
    if not isinstance(package_data, dict):
        package_data = {}
else:
    package_data = {}
dependencies = package_data.setdefault("dependencies", {})
if not isinstance(dependencies, dict):
    dependencies = {}
    package_data["dependencies"] = dependencies
dependencies.setdefault("@opencode-ai/plugin", "^1.15.5")
package_json.write_text(json.dumps(package_data, indent=2) + "\n")

backup = None
if config_path.exists():
    backup = config_path.with_suffix(config_path.suffix + f".bak-{time.strftime('%Y%m%d%H%M%S')}")
    shutil.copy2(config_path, backup)

config = load_config(config_path)
config.setdefault("$schema", "https://opencode.ai/config.json")
commands = config.setdefault("command", {})
if not isinstance(commands, dict):
    raise SystemExit("OpenCode config field 'command' must be an object")

commands["goal"] = {
    "description": "Set or show the active session goal",
    "template": "OpenCode Goal command. If these arguments are empty, call goal_status. Otherwise call goal_set with objective exactly equal to the arguments below, then call goal_status. Arguments: $ARGUMENTS",
}
commands["goal-status"] = {
    "description": "Show the active session goal",
    "template": "Call goal_status with include_history=false and summarize the current goal in one compact status line.",
}
commands["goal-update"] = {
    "description": "Update progress, blockers, status, or next steps for the active goal",
    "template": "Update the active OpenCode goal using goal_update based on these notes. If a status is explicitly mentioned, set it. Notes: $ARGUMENTS",
}
commands["goal-complete"] = {
    "description": "Mark the active session goal complete",
    "template": "Call goal_complete with this completion summary, then show the final goal status. Summary: $ARGUMENTS",
}
commands["goal-pause"] = {
    "description": "Pause the active session goal",
    "template": "Call goal_update with status='paused' and progress/reason from these notes, then show goal_status. Notes: $ARGUMENTS",
}
commands["goal-resume"] = {
    "description": "Resume the active session goal",
    "template": "Call goal_update with status='active' and progress from these notes, then show goal_status. Notes: $ARGUMENTS",
}
commands["goal-clear"] = {
    "description": "Archive the active session goal",
    "template": "Call goal_clear with this reason, then confirm the goal was cleared. Reason: $ARGUMENTS",
}

config_path.write_text(json.dumps(config, indent=2) + "\n")

print(f"Installed plugin: {plugin_target}")
print(f"Installed runner: {runner_target}")
print(f"Updated config:   {config_path}")
print(f"Updated package:  {package_json}")
if backup:
    print(f"Backup:           {backup}")
print("Use /goal <objective> in the OpenCode TUI or: opencode run --command goal \"<objective>\"")
print("Use the controlled runner with: opencode-goal-run --goal \"<objective>\"")
PY
