#!/usr/bin/env bash
set -euo pipefail

PLUGIN_NAME="codex-memory-brain"
DEFAULT_MARKETPLACE_NAME="codex-memory-local"

DRY_RUN=0
SOURCE_DIR=""
INSTALL_DIR="${CODEX_MEMORY_PLUGIN_DIR:-$HOME/plugins/$PLUGIN_NAME}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
MARKETPLACE_NAME="${CODEX_MEMORY_MARKETPLACE_NAME:-}"
REPO_URL="${CODEX_MEMORY_REPO_URL:-https://github.com/ccycv/codex-memory-brain.git}"

usage() {
  cat <<USAGE
Install Codex Memory Brain for Codex.

Usage:
  bash install.sh [options]

Options:
  --dry-run                 Show what would be changed.
  --source DIR              Plugin source directory. Defaults to this script's directory.
  --install-dir DIR         Install directory. Defaults to ~/plugins/codex-memory-brain.
  --codex-home DIR          Codex home. Defaults to \$CODEX_HOME or ~/.codex.
  --marketplace-name NAME   Local marketplace name. Defaults to existing marketplace name or codex-memory-local.
  -h, --help                Show this help.

One-command install:
  bash -c "\$(curl -fsSL https://raw.githubusercontent.com/ccycv/codex-memory-brain/main/install.sh)"

Override the source repo:
  CODEX_MEMORY_REPO_URL=https://github.com/ORG/codex-memory-brain.git bash -c "\$(curl -fsSL https://raw.githubusercontent.com/ORG/codex-memory-brain/main/install.sh)"
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
    --codex-home)
      CODEX_HOME="$2"
      shift 2
      ;;
    --marketplace-name)
      MARKETPLACE_NAME="$2"
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
  local src="${BASH_SOURCE[0]}"
  while [ -L "$src" ]; do
    local dir
    dir="$(cd -P "$(dirname "$src")" >/dev/null 2>&1 && pwd)"
    src="$(readlink "$src")"
    case "$src" in
      /*) ;;
      *) src="$dir/$src" ;;
    esac
  done
  cd -P "$(dirname "$src")" >/dev/null 2>&1 && pwd
}

need_python

if [ -z "$SOURCE_DIR" ]; then
  SOURCE_DIR="$(script_dir)"
fi

if [ ! -f "$SOURCE_DIR/.codex-plugin/plugin.json" ]; then
  if [ -n "$REPO_URL" ]; then
    if ! command -v git >/dev/null 2>&1; then
      echo "git is required when installing from CODEX_MEMORY_REPO_URL." >&2
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
  else
    echo "Could not find plugin source at: $SOURCE_DIR" >&2
    echo "Run this from a local checkout, pass --source DIR, or set CODEX_MEMORY_REPO_URL for curl installs." >&2
    exit 1
  fi
fi

SOURCE_DIR="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$SOURCE_DIR")"
INSTALL_DIR="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$INSTALL_DIR")"
CODEX_HOME="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve())' "$CODEX_HOME")"
HOME_ROOT="$(python3 -c 'import pathlib; print(pathlib.Path.home().resolve())')"
MARKETPLACE_JSON="$HOME_ROOT/.agents/plugins/marketplace.json"

if [ -z "$MARKETPLACE_NAME" ]; then
  MARKETPLACE_NAME="$(python3 - "$MARKETPLACE_JSON" "$DEFAULT_MARKETPLACE_NAME" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
default = sys.argv[2]
if path.exists():
    try:
        data = json.loads(path.read_text())
        name = data.get("name")
        if isinstance(name, str) and name.strip():
            print(name.strip())
            raise SystemExit
    except Exception:
        pass
print(default)
PY
)"
fi

echo "Codex Memory Brain installer"
echo "  source:      $SOURCE_DIR"
echo "  install dir: $INSTALL_DIR"
echo "  codex home:  $CODEX_HOME"
echo "  marketplace: $MARKETPLACE_NAME"
echo "  dry run:     $DRY_RUN"

if [ "$DRY_RUN" -eq 1 ]; then
  exit 0
fi

python3 - "$SOURCE_DIR" "$INSTALL_DIR" "$CODEX_HOME" "$MARKETPLACE_JSON" "$MARKETPLACE_NAME" <<'PY'
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import sys

source = Path(sys.argv[1])
install_dir = Path(sys.argv[2])
codex_home = Path(sys.argv[3])
marketplace_json = Path(sys.argv[4])
marketplace_name = sys.argv[5]
plugin_name = "codex-memory-brain"

def copy_plugin(src: Path, dst: Path) -> None:
    ignore = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", ".DS_Store")
    if src.resolve() == dst.resolve():
        return
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=ignore)

def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid JSON at {path}: {exc}")

def update_marketplace(path: Path, home: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = load_json(
        path,
        {
            "name": marketplace_name,
            "interface": {"displayName": "Local Plugins"},
            "plugins": [],
        },
    )
    data.setdefault("name", marketplace_name)
    data.setdefault("interface", {"displayName": "Local Plugins"})
    plugins = data.setdefault("plugins", [])
    entry = {
        "name": plugin_name,
        "source": {"source": "local", "path": "./plugins/codex-memory-brain"},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Coding",
    }
    for index, plugin in enumerate(plugins):
        if plugin.get("name") == plugin_name:
            plugins[index] = entry
            break
    else:
        plugins.append(entry)
    path.write_text(json.dumps(data, indent=2) + "\n")

def section_header(line):
    stripped = line.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        return stripped
    return None

def remove_sections(lines, headers):
    result = []
    skipping = False
    for line in lines:
        header = section_header(line)
        if header:
            skipping = header in headers
        if not skipping:
            result.append(line)
    while result and result[-1].strip() == "":
        result.pop()
    return result

def toml_quote(value):
    return json.dumps(value)

def update_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text() if path.exists() else ""
    lines = text.splitlines()
    plugin_key = f'[plugins."{plugin_name}@{marketplace_name}"]'
    marketplace_key = f"[marketplaces.{marketplace_name}]"
    mcp_key = f'[mcp_servers."{plugin_name}"]'
    lines = remove_sections(lines, {plugin_key, marketplace_key, mcp_key})
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    blocks = [
        "",
        marketplace_key,
        f"last_updated = {toml_quote(now)}",
        'source_type = "local"',
        f"source = {toml_quote(str(Path.home()))}",
        "",
        plugin_key,
        "enabled = true",
        "",
        mcp_key,
        'command = "python3"',
        f"args = [{toml_quote(str(install_dir / 'server' / 'memory_brain_mcp.py'))}]",
    ]
    new_text = "\n".join(lines + blocks).strip() + "\n"
    path.write_text(new_text)

manifest = json.loads((source / ".codex-plugin" / "plugin.json").read_text())
version = manifest.get("version", "0.1.0")
cache_dir = codex_home / "plugins" / "cache" / marketplace_name / plugin_name / version

copy_plugin(source, install_dir)
copy_plugin(install_dir, cache_dir)
server = install_dir / "server" / "memory_brain_mcp.py"
server.chmod(server.stat().st_mode | 0o111)
update_marketplace(marketplace_json, Path.home())
update_config(codex_home / "config.toml")

print(f"Installed {plugin_name} {version}")
print(f"Plugin source: {install_dir}")
print(f"Plugin cache:  {cache_dir}")
print(f"Marketplace:   {marketplace_json}")
print(f"Codex config:  {codex_home / 'config.toml'}")
print("Open a new Codex chat or restart Codex to load the skill and MCP tools.")
PY
