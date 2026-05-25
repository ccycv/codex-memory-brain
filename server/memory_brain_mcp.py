#!/usr/bin/env python3
"""Local Codex Memory Brain MCP server.

This server intentionally uses only the Python standard library. It speaks the
MCP JSON-RPC protocol over stdio using Content-Length message framing.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import traceback
import uuid


SERVER_NAME = "codex-memory-brain"
SERVER_VERSION = "0.1.7"
PROTOCOL_VERSION = "2024-11-05"

MAX_CONTENT_CHARS = 4000
MAX_CONTENT_LINES = 80
DEFAULT_LIMIT = 8
MAX_LIMIT = 50

ALLOWED_SCOPES = {"user", "repo", "file", "global"}
ALLOWED_TYPES = {
    "preference",
    "repo_convention",
    "command",
    "decision",
    "gotcha",
    "landmark",
    "task_summary",
}

TYPE_WEIGHTS = {
    "gotcha": 2.3,
    "decision": 2.2,
    "repo_convention": 2.0,
    "command": 1.9,
    "landmark": 1.5,
    "preference": 1.4,
    "task_summary": 1.0,
}

LAST_REPO: dict[str, str] | None = None

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(api[_-]?key|token|secret|password|passwd|pwd)\s*[:=]\s*['\"]?[A-Za-z0-9_./+=:-]{12,}"),
    re.compile(r"(?m)^[A-Z0-9_]*(KEY|TOKEN|SECRET|PASSWORD|PASSWD|PWD)\s*=.+$"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b"),
]

PROMPT_INJECTION_PATTERNS = [
    re.compile(r"(?i)\bignore (all |any |the )?(previous|prior|above) instructions\b"),
    re.compile(r"(?i)\bdisregard (all |any |the )?(previous|prior|above) instructions\b"),
    re.compile(r"(?i)\bsystem prompt\b.*\boverride\b"),
    re.compile(r"(?i)\bdeveloper message\b.*\boverride\b"),
    re.compile(r"(?i)\byou are now\b.*\b(system|developer|admin)\b"),
]

FILE_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+(?:\.[A-Za-z0-9]+)?)"
)
IGNORED_PATH_PARTS = {
    ".git",
    ".next",
    "coverage",
    "dist",
    "node_modules",
    "vendor",
}
PATH_ROOT_HINTS = {
    ".github",
    "api",
    "app",
    "apps",
    "backend",
    "config",
    "content",
    "docker",
    "docs",
    "frontend",
    "functions",
    "lib",
    "packages",
    "public",
    "scripts",
    "server",
    "src",
    "test",
    "tests",
}

AUDIT_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".next",
    ".turbo",
    ".venv",
    "__pycache__",
    "build",
    "coverage",
    "dist",
    "node_modules",
    "target",
    "vendor",
}

AUDIT_LANDMARK_DIRS = [
    "src",
    "app",
    "apps",
    "backend",
    "server",
    "api",
    "packages",
    "lib",
    "components",
    "routes",
    "pages",
    "content",
    "docs",
    "scripts",
    "tests",
    "test",
    ".github/workflows",
]

AUDIT_DOC_FILES = [
    "README.md",
    "README.rst",
    "docs/README.md",
    "ARCHITECTURE.md",
    "docs/ARCHITECTURE.md",
    "DECISIONS.md",
    "docs/DECISIONS.md",
]


class MemoryErrorValue(ValueError):
    """A user-correctable memory operation error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_time(value: str) -> _dt.datetime:
    try:
        return _dt.datetime.fromisoformat(value)
    except ValueError:
        return _dt.datetime.now(_dt.timezone.utc)


def clamp_limit(value: object, default: int = DEFAULT_LIMIT) -> int:
    if value is None:
        return default
    try:
        limit = int(value)
    except (TypeError, ValueError):
        raise MemoryErrorValue("invalid_limit", "limit must be an integer")
    return max(1, min(MAX_LIMIT, limit))


def as_list(value: object, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise MemoryErrorValue("invalid_" + field_name, field_name + " must be a string or list of strings")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise MemoryErrorValue("invalid_" + field_name, field_name + " must contain only strings")
        cleaned = item.strip()
        if cleaned:
            result.append(cleaned)
    return unique_preserve_order(result)


def unique_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def memory_home() -> Path:
    raw = os.environ.get("CODEX_MEMORY_HOME", "~/.codex-memory")
    return Path(raw).expanduser()


def db_path() -> Path:
    return memory_home() / "memory.db"


def connect_db() -> sqlite3.Connection:
    home = memory_home()
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(home / "memory.db"))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    try:
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    repo_id TEXT NOT NULL DEFAULT '',
                    related_files TEXT NOT NULL DEFAULT '[]',
                    tags TEXT NOT NULL DEFAULT '[]',
                    confidence REAL NOT NULL DEFAULT 0.7,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    archived INTEGER NOT NULL DEFAULT 0,
                    dedupe_key TEXT NOT NULL UNIQUE
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_repo ON memories(repo_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_scope ON memories(scope)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memories_archived ON memories(archived)")
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
                    content,
                    tags,
                    related_files,
                    content='memories',
                    content_rowid='rowid'
                )
                """
            )
            conn.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, content, tags, related_files)
                    VALUES (new.rowid, new.content, new.tags, new.related_files);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, content, tags, related_files)
                    VALUES ('delete', old.rowid, old.content, old.tags, old.related_files);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, content, tags, related_files)
                    VALUES ('delete', old.rowid, old.content, old.tags, old.related_files);
                    INSERT INTO memories_fts(rowid, content, tags, related_files)
                    VALUES (new.rowid, new.content, new.tags, new.related_files);
                END;
                """
            )
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).lower():
            raise RuntimeError("SQLite FTS5 is required but is not available in this Python build") from exc
        raise


def run_git(args: list[str], cwd: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def safe_cwd(cwd: object | None) -> Path:
    if cwd is None:
        raw = os.environ.get("CODEX_TASK_CWD") or os.environ.get("CODEX_WORKSPACE") or os.environ.get("PWD") or os.getcwd()
    elif isinstance(cwd, str) and cwd.strip():
        raw = cwd
    else:
        raise MemoryErrorValue("invalid_cwd", "cwd must be a non-empty string when provided")
    path = Path(raw).expanduser()
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    if resolved.exists() and resolved.is_file():
        return resolved.parent
    if resolved.exists():
        return resolved
    parent = resolved.parent
    return parent if parent.exists() else Path.cwd()


def sanitize_remote(remote: str) -> str:
    value = remote.strip()
    value = re.sub(r"(https?://)([^/@]+)@", r"\1", value)
    ssh_match = re.match(r"git@([^:]+):(.+)$", value)
    if ssh_match:
        value = "https://" + ssh_match.group(1) + "/" + ssh_match.group(2)
    ssh_url_match = re.match(r"ssh://git@([^/]+)/(.+)$", value)
    if ssh_url_match:
        value = "https://" + ssh_url_match.group(1) + "/" + ssh_url_match.group(2)
    value = value.rstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    return value


def repo_identity(cwd: object | None = None) -> dict[str, str]:
    base = safe_cwd(cwd)
    root = run_git(["rev-parse", "--show-toplevel"], base)
    if root:
        root_path = Path(root).expanduser().resolve()
        remote = run_git(["remote", "get-url", "origin"], root_path)
        remote = sanitize_remote(remote) if remote else ""
        repo_id = "git:" + remote if remote else "path:" + str(root_path)
        return {
            "repo_id": repo_id,
            "root": str(root_path),
            "remote": remote,
            "is_git": "true",
        }
    return {
        "repo_id": "path:" + str(base),
        "root": str(base),
        "remote": "",
        "is_git": "false",
    }


def set_current_repo(repo: dict[str, str]) -> None:
    global LAST_REPO
    LAST_REPO = dict(repo)


def current_repo_identity() -> dict[str, str]:
    if LAST_REPO is not None:
        return dict(LAST_REPO)
    return repo_identity(None)


def normalize_path_for_repo(path_text: str, repo_root: str) -> str:
    path_text = path_text.strip()
    if not path_text:
        return ""
    path = Path(path_text).expanduser()
    if path.is_absolute():
        try:
            rel = path.resolve().relative_to(Path(repo_root).resolve())
            return rel.as_posix()
        except (OSError, ValueError):
            return path.as_posix()
    return Path(path_text).as_posix().lstrip("./")


def normalize_files(files: object, repo: dict[str, str]) -> list[str]:
    raw_files = as_list(files, "files")
    return unique_preserve_order([normalize_path_for_repo(item, repo["root"]) for item in raw_files if item.strip()])


def extract_files_from_content(content: str, repo: dict[str, str]) -> list[str]:
    candidates: list[str] = []
    for match in FILE_PATH_PATTERN.findall(content):
        cleaned = match.strip("`'\"(),.;:")
        parts = [part for part in cleaned.split("/") if part]
        if not parts:
            continue
        if any(part in IGNORED_PATH_PARTS for part in parts):
            continue
        if cleaned.startswith(("http:/", "https:/")):
            continue
        has_extension = "." in parts[-1]
        root_hint = parts[0].lower() in PATH_ROOT_HINTS
        if not has_extension and not root_hint:
            continue
        candidates.append(normalize_path_for_repo(cleaned, repo["root"]))
    return unique_preserve_order(candidates)[:12]


def normalize_content(content: str) -> str:
    return re.sub(r"\s+", " ", content.strip()).lower()


def dedupe_key(scope: str, repo_id: str, memory_type: str, content: str) -> str:
    raw = "\0".join([scope, repo_id, memory_type, normalize_content(content)])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def tokenize(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z0-9_]{2,}", text)]


def fts_query(text: str) -> str:
    tokens = unique_preserve_order(tokenize(text))[:16]
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)


def json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def row_to_memory(row: sqlite3.Row, include_score: bool = False) -> dict[str, object]:
    memory = {
        "id": row["id"],
        "scope": row["scope"],
        "type": row["type"],
        "content": row["content"],
        "repo_id": row["repo_id"],
        "related_files": json.loads(row["related_files"] or "[]"),
        "tags": json.loads(row["tags"] or "[]"),
        "confidence": row["confidence"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "archived": bool(row["archived"]),
    }
    if include_score and "score" in row.keys():
        memory["score"] = row["score"]
    return memory


def validate_scope(scope: object) -> str:
    if not isinstance(scope, str) or scope not in ALLOWED_SCOPES:
        raise MemoryErrorValue("invalid_scope", "scope must be one of: " + ", ".join(sorted(ALLOWED_SCOPES)))
    return scope


def validate_type(memory_type: object) -> str:
    if not isinstance(memory_type, str) or memory_type not in ALLOWED_TYPES:
        raise MemoryErrorValue("invalid_type", "type must be one of: " + ", ".join(sorted(ALLOWED_TYPES)))
    return memory_type


def validate_confidence(value: object | None) -> float:
    if value is None:
        return 0.7
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        raise MemoryErrorValue("invalid_confidence", "confidence must be a number between 0 and 1")
    if confidence < 0 or confidence > 1:
        raise MemoryErrorValue("invalid_confidence", "confidence must be between 0 and 1")
    return confidence


def validate_content(content: object) -> str:
    if not isinstance(content, str) or not content.strip():
        raise MemoryErrorValue("invalid_content", "content must be a non-empty string")
    cleaned = content.strip()
    if len(cleaned) > MAX_CONTENT_CHARS:
        raise MemoryErrorValue("content_too_large", "content is too large for durable memory")
    if cleaned.count("\n") + 1 > MAX_CONTENT_LINES:
        raise MemoryErrorValue("content_too_large", "content has too many lines for durable memory")
    env_lines = re.findall(r"(?m)^[A-Z][A-Z0-9_]{2,}=.+$", cleaned)
    if len(env_lines) >= 3:
        raise MemoryErrorValue("secret_detected", "content looks like an environment file dump")
    for pattern in SECRET_PATTERNS:
        if pattern.search(cleaned):
            raise MemoryErrorValue("secret_detected", "content appears to contain a secret or credential")
    for pattern in PROMPT_INJECTION_PATTERNS:
        if pattern.search(cleaned):
            raise MemoryErrorValue("prompt_injection_detected", "content looks like prompt-injection instructions")
    return cleaned


def validate_args(args: object) -> dict[str, object]:
    if args is None:
        return {}
    if not isinstance(args, dict):
        raise MemoryErrorValue("invalid_arguments", "arguments must be an object")
    return args


def repo_filter_sql(repo: dict[str, str]) -> tuple[str, list[object]]:
    return " AND (m.repo_id = ? OR m.repo_id = '' OR m.scope IN ('user','global'))", [repo["repo_id"]]


def fetch_recent(conn: sqlite3.Connection, repo: dict[str, str] | None, limit: int) -> list[sqlite3.Row]:
    sql = "SELECT m.*, 0.0 AS score FROM memories m WHERE m.archived = 0"
    params: list[object] = []
    if repo:
        extra, repo_params = repo_filter_sql(repo)
        sql += extra
        params.extend(repo_params)
    sql += " ORDER BY m.updated_at DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def search_rows(
    conn: sqlite3.Connection,
    query: str,
    repo: dict[str, str] | None,
    files: list[str],
    scope: str | None,
    memory_type: str | None,
    limit: int,
) -> list[sqlite3.Row]:
    q = fts_query(query + " " + " ".join(files))
    params: list[object] = []
    where = ["m.archived = 0"]
    if repo:
        extra, repo_params = repo_filter_sql(repo)
        where.append(extra[5:])
        params.extend(repo_params)
    if scope:
        where.append("m.scope = ?")
        params.append(scope)
    if memory_type:
        where.append("m.type = ?")
        params.append(memory_type)

    if q:
        sql = (
            "SELECT m.*, bm25(memories_fts) AS score "
            "FROM memories_fts JOIN memories m ON m.rowid = memories_fts.rowid "
            "WHERE memories_fts MATCH ? AND " + " AND ".join(where) + " "
            "ORDER BY score ASC, m.updated_at DESC LIMIT ?"
        )
        try:
            return conn.execute(sql, [q, *params, limit]).fetchall()
        except sqlite3.OperationalError:
            pass

    like_terms = tokenize(query)[:6]
    sql = "SELECT m.*, 0.0 AS score FROM memories m WHERE " + " AND ".join(where)
    if like_terms:
        likes = []
        for term in like_terms:
            likes.append("lower(m.content) LIKE ?")
            params.append("%" + term.lower() + "%")
        sql += " AND (" + " OR ".join(likes) + ")"
    sql += " ORDER BY m.updated_at DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def rank_memory(memory: dict[str, object], task: str, repo: dict[str, str], files: list[str]) -> float:
    score = 0.0
    if memory["repo_id"] == repo["repo_id"]:
        score += 2.0
    if memory["scope"] == "file":
        score += 0.6
    elif memory["scope"] == "repo":
        score += 0.4
    elif memory["scope"] in {"user", "global"}:
        score += 0.2
    score += TYPE_WEIGHTS.get(str(memory["type"]), 1.0)

    memory_files = [str(item) for item in memory.get("related_files", [])]
    file_set = {item.lower() for item in files}
    memory_file_set = {item.lower() for item in memory_files}
    if file_set and memory_file_set:
        exact = file_set.intersection(memory_file_set)
        if exact:
            score += 4.0 + min(2.0, len(exact) * 0.5)
        else:
            requested_names = {Path(item).name.lower() for item in files}
            memory_names = {Path(item).name.lower() for item in memory_files}
            if requested_names.intersection(memory_names):
                score += 1.0

    task_terms = set(tokenize(task))
    if task_terms:
        content_terms = set(tokenize(str(memory["content"]) + " " + " ".join(memory_files) + " " + " ".join(memory.get("tags", []))))
        score += min(2.5, len(task_terms.intersection(content_terms)) * 0.25)

    try:
        text_score = float(memory.get("score", 0.0) or 0.0)
        score += max(0.0, 1.0 - min(1.0, abs(text_score)))
    except (TypeError, ValueError):
        pass

    try:
        score += float(memory.get("confidence", 0.7)) * 1.5
    except (TypeError, ValueError):
        score += 1.0

    updated_at = str(memory.get("updated_at", ""))
    now = _dt.datetime.now(_dt.timezone.utc)
    age_days = max(0.0, (now - parse_time(updated_at)).total_seconds() / 86400.0)
    score += max(0.0, 1.2 - min(age_days, 90.0) / 75.0)
    return round(score, 4)


def build_context_block(memories: list[dict[str, object]]) -> str:
    if not memories:
        return "No relevant local memories found."
    lines = ["Relevant local memory:"]
    for index, memory in enumerate(memories, start=1):
        files = memory.get("related_files") or []
        file_text = " files=" + ", ".join(files) if files else ""
        tags = memory.get("tags") or []
        tag_text = " tags=" + ", ".join(tags) if tags else ""
        lines.append(
            f"{index}. [{memory['id']}] {memory['type']} / {memory['scope']}{file_text}{tag_text}: {memory['content']}"
        )
    return "\n".join(lines)


def repo_root_from_identity(repo: dict[str, str]) -> Path:
    root = Path(repo["root"]).expanduser()
    try:
        return root.resolve()
    except OSError:
        return root.absolute()


def safe_repo_file(root: Path, rel_path: str) -> Path | None:
    candidate = (root / rel_path).expanduser()
    try:
        resolved = candidate.resolve()
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return None
    return resolved


def read_repo_text(root: Path, rel_path: str, max_chars: int = 20000) -> str:
    path = safe_repo_file(root, rel_path)
    if path is None or not path.exists() or not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except OSError:
        return ""


def repo_path_exists(root: Path, rel_path: str) -> bool:
    path = safe_repo_file(root, rel_path)
    return bool(path and path.exists())


def list_repo_children(root: Path, rel_path: str = ".", max_entries: int = 80) -> list[str]:
    path = safe_repo_file(root, rel_path)
    if path is None or not path.exists() or not path.is_dir():
        return []
    result: list[str] = []
    try:
        for child in sorted(path.iterdir(), key=lambda item: item.name.lower()):
            if child.name in AUDIT_IGNORE_DIRS:
                continue
            child_rel = child.relative_to(root).as_posix()
            result.append(child_rel + ("/" if child.is_dir() else ""))
            if len(result) >= max_entries:
                break
    except OSError:
        return []
    return result


def walk_repo_files(root: Path, max_files: int = 600) -> list[str]:
    result: list[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name not in AUDIT_IGNORE_DIRS and not name.startswith(".cache")]
        try:
            rel_current = Path(current).resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        if len(rel_current.parts) > 4:
            dirs[:] = []
            continue
        for filename in sorted(files):
            if filename.endswith((".pyc", ".log", ".map", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip")):
                continue
            rel = (rel_current / filename).as_posix()
            result.append(rel)
            if len(result) >= max_files:
                return result
    return result


def first_markdown_heading(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return line.lstrip("#").strip()
    return ""


def compact_list(values: list[str], limit: int = 10) -> str:
    if not values:
        return ""
    selected = values[:limit]
    extra = len(values) - len(selected)
    text = ", ".join(selected)
    if extra > 0:
        text += f", and {extra} more"
    return text


def make_audit_candidate(
    memory_type: str,
    content: str,
    files: list[str],
    tags: list[str],
    confidence: float,
    source: str,
) -> dict[str, object]:
    return {
        "scope": "repo",
        "type": memory_type,
        "content": re.sub(r"\s+", " ", content).strip(),
        "files": unique_preserve_order(files),
        "tags": unique_preserve_order(tags),
        "confidence": confidence,
        "source": source,
    }


def package_manager_for_repo(root: Path) -> str:
    if repo_path_exists(root, "pnpm-lock.yaml"):
        return "pnpm"
    if repo_path_exists(root, "yarn.lock"):
        return "yarn"
    if repo_path_exists(root, "bun.lockb") or repo_path_exists(root, "bun.lock"):
        return "bun"
    if repo_path_exists(root, "package-lock.json"):
        return "npm"
    return "npm"


def audit_package_json(root: Path) -> list[dict[str, object]]:
    text = read_repo_text(root, "package.json")
    if not text:
        return []
    try:
        package = json.loads(text)
    except json.JSONDecodeError:
        return [
            make_audit_candidate(
                "gotcha",
                "package.json exists but could not be parsed as JSON; inspect it before relying on package scripts.",
                ["package.json"],
                ["audit", "node", "package-json"],
                0.65,
                "package.json",
            )
        ]
    manager = package_manager_for_repo(root)
    name = package.get("name")
    scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
    important_order = ["dev", "start", "build", "test", "lint", "typecheck", "preview", "format"]
    script_bits = []
    for script_name in important_order:
        script = scripts.get(script_name)
        if isinstance(script, str):
            script_bits.append(f"{script_name}=`{script}`")
    if not script_bits and scripts:
        for script_name, script in list(scripts.items())[:8]:
            if isinstance(script, str):
                script_bits.append(f"{script_name}=`{script}`")
    files = ["package.json"]
    for lockfile in ["pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock", "package-lock.json"]:
        if repo_path_exists(root, lockfile):
            files.append(lockfile)
            break
    package_text = f" `{name}`" if isinstance(name, str) and name else ""
    if script_bits:
        content = (
            f"This repo has a Node/package.json workflow{package_text}. "
            f"Use {manager} for package commands. Important scripts: {', '.join(script_bits)}."
        )
    else:
        content = (
            f"This repo has package.json{package_text}, but no common scripts were detected; inspect package.json "
            "before assuming dev/build/test commands."
        )
    candidates = [
        make_audit_candidate(
            "command",
            content,
            files,
            ["audit", "commands", "node", "package-json"],
            0.82,
            "package.json",
        )
    ]
    if package.get("workspaces"):
        candidates.append(
            make_audit_candidate(
                "repo_convention",
                "package.json declares workspaces; check the workspace layout before adding dependencies or running package-level commands.",
                ["package.json"],
                ["audit", "monorepo", "workspaces"],
                0.76,
                "package.json",
            )
        )
    return candidates


def audit_python_project(root: Path) -> list[dict[str, object]]:
    files = [rel for rel in ["pyproject.toml", "requirements.txt", "requirements-dev.txt", "setup.py", "pytest.ini"] if repo_path_exists(root, rel)]
    if not files:
        return []
    text = "\n".join(read_repo_text(root, rel, 8000) for rel in files)
    tools = []
    if "pytest" in text.lower() or repo_path_exists(root, "tests"):
        tools.append("pytest/tests")
    if "ruff" in text.lower():
        tools.append("ruff")
    if "mypy" in text.lower():
        tools.append("mypy")
    if "uv" in text.lower() or repo_path_exists(root, "uv.lock"):
        tools.append("uv")
    tool_text = " Detected tooling: " + ", ".join(tools) + "." if tools else ""
    return [
        make_audit_candidate(
            "command",
            "This repo has Python project metadata/configuration in "
            + compact_list(files)
            + ". Inspect those files for install, test, and lint commands before assuming defaults."
            + tool_text,
            files,
            ["audit", "commands", "python"],
            0.76,
            "python-project",
        )
    ]


def audit_makefile(root: Path) -> list[dict[str, object]]:
    rel = "Makefile" if repo_path_exists(root, "Makefile") else "makefile" if repo_path_exists(root, "makefile") else ""
    if not rel:
        return []
    text = read_repo_text(root, rel, 12000)
    targets = []
    for line in text.splitlines():
        if line.startswith("\t") or line.startswith(".") or ":=" in line:
            continue
        match = re.match(r"^([A-Za-z0-9_.-]+):(?:\s|$)", line)
        if match:
            targets.append(match.group(1))
    targets = unique_preserve_order(targets)
    return [
        make_audit_candidate(
            "command",
            "Makefile is present. Useful targets detected: " + (compact_list(targets, 12) or "inspect Makefile for targets") + ".",
            [rel],
            ["audit", "commands", "make"],
            0.74,
            rel,
        )
    ]


def audit_docker(root: Path) -> list[dict[str, object]]:
    files = [
        rel
        for rel in ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", "Dockerfile"]
        if repo_path_exists(root, rel)
    ]
    files.extend([rel for rel in walk_repo_files(root, 400) if rel.endswith(("docker-compose.yml", "docker-compose.yaml")) and rel not in files])
    files = unique_preserve_order(files)[:8]
    if not files:
        return []
    return [
        make_audit_candidate(
            "command",
            "Container/local service setup is present in " + compact_list(files) + "; inspect these files before running services or changing ports/volumes.",
            files,
            ["audit", "commands", "docker"],
            0.76,
            "docker",
        )
    ]


def audit_landmarks(root: Path) -> list[dict[str, object]]:
    landmarks = [rel for rel in AUDIT_LANDMARK_DIRS if repo_path_exists(root, rel)]
    readmes = [rel for rel in AUDIT_DOC_FILES if repo_path_exists(root, rel)]
    root_children = list_repo_children(root, ".", 40)
    if not landmarks and not readmes:
        landmarks = [item.rstrip("/") for item in root_children if item.endswith("/")][:8]
    if not landmarks and not readmes:
        return []
    title = ""
    for readme in readmes:
        title = first_markdown_heading(read_repo_text(root, readme, 5000))
        if title:
            break
    title_text = f"README title: {title}. " if title else ""
    content = (
        title_text
        + "Repo landmarks from audit: "
        + compact_list(landmarks, 12)
        + (". Key docs: " + compact_list(readmes, 6) if readmes else ".")
    )
    return [
        make_audit_candidate(
            "landmark",
            content,
            unique_preserve_order(landmarks[:12] + readmes[:6]),
            ["audit", "landmarks", "repo-map"],
            0.78,
            "repo-tree",
        )
    ]


def audit_tests(root: Path) -> list[dict[str, object]]:
    files = walk_repo_files(root, 500)
    test_files = [
        rel
        for rel in files
        if rel in {"pytest.ini", "vitest.config.ts", "vitest.config.js", "jest.config.js", "playwright.config.ts"}
        or rel.startswith(("tests/", "test/"))
        or "/tests/" in rel
        or rel.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx", "_test.py"))
    ]
    test_dirs = [rel for rel in ["tests", "test", "src/test", "src/tests"] if repo_path_exists(root, rel)]
    package_text = read_repo_text(root, "package.json", 12000)
    scripts = []
    if package_text:
        try:
            package = json.loads(package_text)
            raw_scripts = package.get("scripts") if isinstance(package.get("scripts"), dict) else {}
            for name in ["test", "test:unit", "test:e2e", "vitest", "playwright"]:
                value = raw_scripts.get(name)
                if isinstance(value, str):
                    scripts.append(f"{name}=`{value}`")
        except json.JSONDecodeError:
            pass
    if not test_files and not scripts and not test_dirs:
        return []
    files_for_memory = unique_preserve_order(test_dirs + test_files[:10] + (["package.json"] if scripts else []))
    content = "Testing surface detected"
    if scripts:
        content += "; package scripts include " + ", ".join(scripts)
    if test_dirs:
        content += "; test dirs include " + compact_list(test_dirs)
    if test_files:
        content += "; representative test/config files include " + compact_list(test_files[:8])
    content += "."
    return [
        make_audit_candidate(
            "gotcha",
            content,
            files_for_memory,
            ["audit", "tests"],
            0.72,
            "tests",
        )
    ]


def audit_ci_deploy(root: Path) -> list[dict[str, object]]:
    workflow_dir = safe_repo_file(root, ".github/workflows")
    if workflow_dir is None or not workflow_dir.exists() or not workflow_dir.is_dir():
        return []
    workflow_files = []
    names = []
    try:
        for child in sorted(workflow_dir.iterdir(), key=lambda item: item.name.lower()):
            if child.suffix.lower() not in {".yml", ".yaml"}:
                continue
            rel = child.relative_to(root).as_posix()
            workflow_files.append(rel)
            text = read_repo_text(root, rel, 4000)
            match = re.search(r"(?m)^name:\s*[\"']?(.+?)[\"']?\s*$", text)
            names.append(match.group(1).strip() if match else child.stem)
    except OSError:
        return []
    if not workflow_files:
        return []
    deployish = any(re.search(r"deploy|release|publish|production", name, re.I) for name in names + workflow_files)
    memory_type = "command" if deployish else "landmark"
    return [
        make_audit_candidate(
            memory_type,
            "GitHub Actions workflows are present: "
            + compact_list(names, 10)
            + ". Inspect "
            + compact_list(workflow_files, 8)
            + " before changing CI/deploy behavior.",
            workflow_files[:8],
            ["audit", "ci", "github-actions"] + (["deploy"] if deployish else []),
            0.74,
            ".github/workflows",
        )
    ]


def audit_env_examples(root: Path) -> list[dict[str, object]]:
    files = [
        rel
        for rel in [".env.example", ".env.sample", ".env.local.example", "env.example", "config/env.example"]
        if repo_path_exists(root, rel)
    ]
    if not files:
        return []
    return [
        make_audit_candidate(
            "gotcha",
            "Environment examples exist in "
            + compact_list(files)
            + "; use these as setup references and never store real .env values or secrets in memory.",
            files,
            ["audit", "env", "secrets"],
            0.84,
            "env-examples",
        )
    ]


def audit_decision_docs(root: Path) -> list[dict[str, object]]:
    files = walk_repo_files(root, 600)
    decision_files = []
    for rel in files:
        low = rel.lower()
        if (
            "adr" in Path(rel).parts
            or "decision" in low
            or "architecture" in low
            or low.endswith(("adr.md", "architecture.md", "decisions.md"))
        ):
            decision_files.append(rel)
    decision_files = unique_preserve_order(decision_files)[:10]
    if not decision_files:
        return []
    return [
        make_audit_candidate(
            "decision",
            "Architecture/decision documentation is present in "
            + compact_list(decision_files, 8)
            + "; consult these before changing core design or migration direction.",
            decision_files[:8],
            ["audit", "architecture", "decisions"],
            0.72,
            "decision-docs",
        )
    ]


def audit_generated_files(root: Path) -> list[dict[str, object]]:
    files = walk_repo_files(root, 700)
    generated = [
        rel
        for rel in files
        if "/generated/" in rel
        or rel.startswith("generated/")
        or rel.startswith("src/generated/")
        or rel.endswith((".generated.ts", ".generated.tsx", ".gen.ts", ".pb.ts"))
    ]
    generated = unique_preserve_order(generated)[:8]
    if not generated:
        return []
    return [
        make_audit_candidate(
            "gotcha",
            "Generated files/artifacts are present under "
            + compact_list(generated, 8)
            + "; prefer editing the source generator or source data rather than generated output.",
            generated,
            ["audit", "generated", "gotcha"],
            0.76,
            "generated-files",
        )
    ]


def audit_repo_candidates(root: Path, limit: int) -> list[dict[str, object]]:
    candidates: list[dict[str, object]] = []
    audit_steps = [
        audit_package_json,
        audit_python_project,
        audit_makefile,
        audit_docker,
        audit_landmarks,
        audit_tests,
        audit_ci_deploy,
        audit_env_examples,
        audit_decision_docs,
        audit_generated_files,
    ]
    seen: set[str] = set()
    for step in audit_steps:
        for candidate in step(root):
            key = candidate["type"] + "\0" + normalize_content(str(candidate["content"]))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(candidate)
            if len(candidates) >= limit:
                return candidates
    return candidates


def tool_memory_context(args_obj: object) -> dict[str, object]:
    args = validate_args(args_obj)
    task = args.get("task")
    if not isinstance(task, str):
        raise MemoryErrorValue("invalid_task", "task must be a string")
    limit = clamp_limit(args.get("limit"), DEFAULT_LIMIT)
    repo = repo_identity(args.get("cwd"))
    set_current_repo(repo)
    files = normalize_files(args.get("files"), repo)
    with connect_db() as conn:
        rows = search_rows(conn, task, repo, files, None, None, max(limit * 4, 16))
        if len(rows) < limit:
            seen = {row["id"] for row in rows}
            recent = [row for row in fetch_recent(conn, repo, max(limit * 4, 16)) if row["id"] not in seen]
            rows.extend(recent)
    memories = [row_to_memory(row, include_score=True) for row in rows]
    for memory in memories:
        memory["rank"] = rank_memory(memory, task, repo, files)
    memories.sort(key=lambda item: (float(item["rank"]), str(item["updated_at"])), reverse=True)
    selected = memories[:limit]
    visible_status = (
        f"Memory Brain: checked repo memory, loaded {len(selected)} relevant memories."
        if selected
        else "Memory Brain: checked repo memory, nothing relevant found."
    )
    return {
        "repo": repo,
        "task": task,
        "files": files,
        "limit": limit,
        "visible_status": visible_status,
        "context": build_context_block(selected),
        "memories": selected,
    }


def tool_memory_search(args_obj: object) -> dict[str, object]:
    args = validate_args(args_obj)
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise MemoryErrorValue("invalid_query", "query must be a non-empty string")
    scope = validate_scope(args["scope"]) if args.get("scope") is not None else None
    memory_type = validate_type(args["type"]) if args.get("type") is not None else None
    repo = repo_identity(args.get("cwd")) if args.get("cwd") is not None else None
    if repo:
        set_current_repo(repo)
    files = normalize_files(args.get("files"), repo or repo_identity(None))
    limit = clamp_limit(args.get("limit"), DEFAULT_LIMIT)
    with connect_db() as conn:
        rows = search_rows(conn, query, repo, files, scope, memory_type, limit)
    memories = [row_to_memory(row, include_score=True) for row in rows]
    return {
        "query": query,
        "repo": repo,
        "files": files,
        "count": len(memories),
        "visible_status": f'Memory Brain: searched memory for "{query}", found {len(memories)} results.',
        "memories": memories,
    }


def tool_memory_remember(args_obj: object) -> dict[str, object]:
    args = validate_args(args_obj)
    content = validate_content(args.get("content"))
    scope = validate_scope(args.get("scope"))
    memory_type = validate_type(args.get("type"))
    repo = repo_identity(args.get("cwd"))
    set_current_repo(repo)
    repo_id = "" if scope in {"user", "global"} else repo["repo_id"]
    files = normalize_files(args.get("files"), repo)
    if not files and scope in {"repo", "file"}:
        files = extract_files_from_content(content, repo)
    if scope == "file" and not files:
        raise MemoryErrorValue("missing_files", "scope=file requires at least one related file")
    tags = as_list(args.get("tags"), "tags")
    confidence = validate_confidence(args.get("confidence"))
    key = dedupe_key(scope, repo_id, memory_type, content)
    now = utc_now()
    memory_id = "mem_" + uuid.uuid4().hex[:16]
    with connect_db() as conn:
        existing = conn.execute("SELECT m.*, 0.0 AS score FROM memories m WHERE dedupe_key = ?", [key]).fetchone()
        if existing:
            existing_memory = row_to_memory(existing, include_score=False)
            return {
                "created": False,
                "duplicate": True,
                "visible_status": f"Memory Brain: memory already existed {existing_memory['id']}.",
                "memory": existing_memory,
            }
        with conn:
            conn.execute(
                """
                INSERT INTO memories (
                    id, scope, type, content, repo_id, related_files, tags,
                    confidence, created_at, updated_at, archived, dedupe_key
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                [
                    memory_id,
                    scope,
                    memory_type,
                    content,
                    repo_id,
                    json_dumps(files),
                    json_dumps(tags),
                    confidence,
                    now,
                    now,
                    key,
                ],
            )
        row = conn.execute("SELECT m.*, 0.0 AS score FROM memories m WHERE id = ?", [memory_id]).fetchone()
    return {
        "created": True,
        "duplicate": False,
        "visible_status": f"Memory Brain: saved {memory_type} memory {memory_id}.",
        "memory": row_to_memory(row, include_score=False),
    }


def tool_memory_update(args_obj: object) -> dict[str, object]:
    args = validate_args(args_obj)
    memory_id = args.get("id")
    if not isinstance(memory_id, str) or not memory_id.strip():
        raise MemoryErrorValue("invalid_id", "id must be a non-empty string")
    updates: dict[str, object] = {}
    repo_for_files = repo_identity(args.get("cwd")) if args.get("cwd") is not None else repo_identity(None)
    set_current_repo(repo_for_files)
    if "content" in args:
        updates["content"] = validate_content(args.get("content"))
    if "files" in args:
        updates["related_files"] = json_dumps(normalize_files(args.get("files"), repo_for_files))
    if "tags" in args:
        updates["tags"] = json_dumps(as_list(args.get("tags"), "tags"))
    if "confidence" in args:
        updates["confidence"] = validate_confidence(args.get("confidence"))
    if "archived" in args:
        if not isinstance(args.get("archived"), bool):
            raise MemoryErrorValue("invalid_archived", "archived must be a boolean")
        updates["archived"] = 1 if args.get("archived") else 0
    if not updates:
        raise MemoryErrorValue("no_updates", "provide at least one field to update")

    with connect_db() as conn:
        row = conn.execute("SELECT m.*, 0.0 AS score FROM memories m WHERE id = ?", [memory_id]).fetchone()
        if not row:
            raise MemoryErrorValue("not_found", "memory id was not found")
        content = str(updates.get("content", row["content"]))
        key = dedupe_key(row["scope"], row["repo_id"], row["type"], content)
        existing = conn.execute(
            "SELECT id FROM memories WHERE dedupe_key = ? AND id != ?",
            [key, memory_id],
        ).fetchone()
        if existing:
            raise MemoryErrorValue("duplicate", "update would duplicate an existing memory")
        updates["dedupe_key"] = key
        updates["updated_at"] = utc_now()
        assignments = ", ".join(name + " = ?" for name in updates)
        params = list(updates.values()) + [memory_id]
        with conn:
            conn.execute("UPDATE memories SET " + assignments + " WHERE id = ?", params)
        updated = conn.execute("SELECT m.*, 0.0 AS score FROM memories m WHERE id = ?", [memory_id]).fetchone()
    return {
        "updated": True,
        "visible_status": f"Memory Brain: updated {memory_id}.",
        "memory": row_to_memory(updated, include_score=False),
    }


def tool_memory_forget(args_obj: object) -> dict[str, object]:
    args = validate_args(args_obj)
    memory_id = args.get("id")
    if not isinstance(memory_id, str) or not memory_id.strip():
        raise MemoryErrorValue("invalid_id", "id must be a non-empty string")
    with connect_db() as conn:
        row = conn.execute("SELECT id FROM memories WHERE id = ?", [memory_id]).fetchone()
        if not row:
            raise MemoryErrorValue("not_found", "memory id was not found")
        with conn:
            conn.execute("UPDATE memories SET archived = 1, updated_at = ? WHERE id = ?", [utc_now(), memory_id])
    return {
        "forgotten": True,
        "id": memory_id,
        "archived": True,
        "visible_status": f"Memory Brain: archived {memory_id}.",
    }


def tool_memory_status(args_obj: object) -> dict[str, object]:
    args = validate_args(args_obj)
    repo = repo_identity(args.get("cwd")) if args.get("cwd") is not None else repo_identity(None)
    set_current_repo(repo)
    with connect_db() as conn:
        where = "m.archived = 0"
        params: list[object] = []
        extra, repo_params = repo_filter_sql(repo)
        where += extra
        params.extend(repo_params)
        by_scope = conn.execute(
            "SELECT scope, COUNT(*) AS count FROM memories m WHERE " + where + " GROUP BY scope ORDER BY scope",
            params,
        ).fetchall()
        by_type = conn.execute(
            "SELECT type, COUNT(*) AS count FROM memories m WHERE " + where + " GROUP BY type ORDER BY type",
            params,
        ).fetchall()
        recent = conn.execute(
            "SELECT m.*, 0.0 AS score FROM memories m WHERE "
            + where
            + " ORDER BY updated_at DESC LIMIT 5",
            params,
        ).fetchall()
        total_active = conn.execute("SELECT COUNT(*) AS count FROM memories WHERE archived = 0").fetchone()["count"]
        total_archived = conn.execute("SELECT COUNT(*) AS count FROM memories WHERE archived = 1").fetchone()["count"]
    return {
        "db_path": str(db_path()),
        "repo": repo,
        "total_active": total_active,
        "total_archived": total_archived,
        "visible_status": f"Memory Brain: status loaded for repo; {len(recent)} recent memories shown.",
        "counts_by_scope": {row["scope"]: row["count"] for row in by_scope},
        "counts_by_type": {row["type"]: row["count"] for row in by_type},
        "recent": [row_to_memory(row, include_score=False) for row in recent],
    }


def tool_memory_audit_repo(args_obj: object) -> dict[str, object]:
    args = validate_args(args_obj)
    if "cwd" not in args:
        raise MemoryErrorValue("invalid_cwd", "cwd is required")
    repo = repo_identity(args.get("cwd"))
    set_current_repo(repo)
    root = repo_root_from_identity(repo)
    if not root.exists() or not root.is_dir():
        raise MemoryErrorValue("invalid_cwd", "repo root does not exist or is not a directory")
    save = bool(args.get("save", False))
    if "save" in args and not isinstance(args.get("save"), bool):
        raise MemoryErrorValue("invalid_save", "save must be a boolean")
    limit = clamp_limit(args.get("limit"), 10)
    limit = min(limit, 20)
    candidates = audit_repo_candidates(root, limit)
    saved: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    if save:
        for candidate in candidates:
            remember_args = {
                "content": candidate["content"],
                "cwd": str(root),
                "scope": candidate["scope"],
                "type": candidate["type"],
                "files": candidate["files"],
                "tags": candidate["tags"],
                "confidence": candidate["confidence"],
            }
            try:
                result = tool_memory_remember(remember_args)
                memory = result["memory"]
                saved.append(
                    {
                        "id": memory["id"],
                        "type": memory["type"],
                        "source": candidate["source"],
                        "created": result["created"],
                        "duplicate": result["duplicate"],
                    }
                )
            except MemoryErrorValue as exc:
                skipped.append({"source": candidate["source"], "code": exc.code, "message": exc.message})
    created_count = len([item for item in saved if item.get("created")])
    duplicate_count = len([item for item in saved if item.get("duplicate")])
    if save:
        visible_status = (
            f"Memory Brain: audited repo, saved {created_count} memories"
            + (f" ({duplicate_count} already existed)" if duplicate_count else "")
            + "."
        )
    else:
        visible_status = f"Memory Brain: audited repo, proposed {len(candidates)} durable memories."
    return {
        "repo": repo,
        "save": save,
        "limit": limit,
        "visible_status": visible_status,
        "candidate_count": len(candidates),
        "candidates": candidates,
        "saved": saved,
        "skipped": skipped,
    }


def git_metadata(cwd: object | None) -> dict[str, object]:
    repo = repo_identity(cwd)
    root = repo_root_from_identity(repo)
    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], root) or ""
    commit = run_git(["rev-parse", "--short", "HEAD"], root) or ""
    status_output = run_git(["status", "--short"], root) or ""
    changed_files = []
    for line in status_output.splitlines():
        if len(line) >= 4:
            changed_files.append(line[3:].strip())
    return {
        "branch": branch,
        "commit": commit,
        "dirty": bool(changed_files),
        "changed_files": changed_files[:20],
    }


def optional_text(value: object, field_name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise MemoryErrorValue("invalid_" + field_name, field_name + " must be a string")
    return value.strip()


def append_checkpoint_section(lines: list[str], title: str, values: list[str]) -> None:
    if not values:
        return
    lines.append("")
    lines.append(title + ":")
    for value in values:
        lines.append("- " + value)


def build_checkpoint_content(args: dict[str, object], repo: dict[str, str]) -> tuple[str, list[str], list[str]]:
    summary = optional_text(args.get("summary"), "summary")
    changes = as_list(args.get("changes"), "changes")
    decisions = as_list(args.get("decisions"), "decisions")
    blockers = as_list(args.get("blockers"), "blockers")
    next_steps = as_list(args.get("next_steps"), "next_steps")
    tests_run = as_list(args.get("tests_run"), "tests_run")
    tests_not_run = as_list(args.get("tests_not_run"), "tests_not_run")
    files = normalize_files(args.get("files"), repo)
    if not files:
        extracted_parts = [summary] + changes + decisions + blockers + next_steps + tests_run + tests_not_run
        files = extract_files_from_content(" ".join(extracted_parts), repo)
    if not any([summary, changes, decisions, blockers, next_steps, tests_run, tests_not_run, files]):
        raise MemoryErrorValue("empty_checkpoint", "checkpoint needs a summary, files, changes, tests, blockers, or next steps")

    git = git_metadata(repo["root"])
    branch = optional_text(args.get("branch"), "branch") or str(git.get("branch") or "")
    commit = optional_text(args.get("commit"), "commit") or str(git.get("commit") or "")
    now = utc_now()
    lines = [f"Task checkpoint at {now}."]
    if summary:
        lines.append("Summary: " + summary)
    if branch or commit:
        lines.append("Git: " + " ".join(part for part in [f"branch={branch}" if branch else "", f"commit={commit}" if commit else ""] if part))
    if git.get("dirty"):
        changed_files = [str(item) for item in git.get("changed_files", [])]
        lines.append("Working tree had changes in: " + compact_list(changed_files, 12) + ".")
        files = unique_preserve_order(files + changed_files)
    append_checkpoint_section(lines, "Changes", changes)
    append_checkpoint_section(lines, "Decisions", decisions)
    append_checkpoint_section(lines, "Blockers", blockers)
    append_checkpoint_section(lines, "Next steps", next_steps)
    append_checkpoint_section(lines, "Tests run", tests_run)
    append_checkpoint_section(lines, "Tests not run", tests_not_run)
    if files:
        append_checkpoint_section(lines, "Files", files[:12])
    tags = ["checkpoint", "continuity"]
    if blockers:
        tags.append("blockers")
    if next_steps:
        tags.append("next-steps")
    if tests_not_run:
        tags.append("tests-not-run")
    tags.extend(as_list(args.get("tags"), "tags"))
    return "\n".join(lines), files, unique_preserve_order(tags)


def tool_memory_task_checkpoint(args_obj: object) -> dict[str, object]:
    args = validate_args(args_obj)
    if "cwd" not in args:
        raise MemoryErrorValue("invalid_cwd", "cwd is required")
    repo = repo_identity(args.get("cwd"))
    set_current_repo(repo)
    content, files, tags = build_checkpoint_content(args, repo)
    confidence = validate_confidence(args.get("confidence") if "confidence" in args else 0.86)
    result = tool_memory_remember(
        {
            "content": content,
            "cwd": repo["root"],
            "scope": "repo",
            "type": "task_summary",
            "files": files,
            "tags": tags,
            "confidence": confidence,
        }
    )
    memory = result["memory"]
    status = (
        f"Memory Brain: saved task checkpoint {memory['id']}."
        if result.get("created")
        else f"Memory Brain: task checkpoint already existed {memory['id']}."
    )
    return {
        "created": result.get("created"),
        "duplicate": result.get("duplicate"),
        "repo": repo,
        "visible_status": status,
        "memory": memory,
    }


def repo_memory_rows(conn: sqlite3.Connection, repo: dict[str, str], limit: int, memory_type: str | None = None) -> list[sqlite3.Row]:
    sql = "SELECT m.*, 0.0 AS score FROM memories m WHERE m.archived = 0"
    params: list[object] = []
    extra, repo_params = repo_filter_sql(repo)
    sql += extra
    params.extend(repo_params)
    if memory_type:
        sql += " AND m.type = ?"
        params.append(memory_type)
    sql += " ORDER BY m.updated_at DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def checkpoint_sections(content: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {
        "blockers": [],
        "next_steps": [],
        "tests_not_run": [],
        "tests_run": [],
        "files": [],
    }
    current = ""
    headings = {
        "Blockers:": "blockers",
        "Next steps:": "next_steps",
        "Tests not run:": "tests_not_run",
        "Tests run:": "tests_run",
        "Files:": "files",
    }
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if line in headings:
            current = headings[line]
            continue
        if line.endswith(":") and not line.startswith("- "):
            current = ""
            continue
        if current and line.startswith("- "):
            sections[current].append(line[2:].strip())
    return sections


def collect_checkpoint_items(checkpoints: list[dict[str, object]]) -> dict[str, list[str]]:
    collected = {
        "blockers": [],
        "next_steps": [],
        "tests_not_run": [],
        "tests_run": [],
        "files": [],
    }
    for checkpoint in checkpoints:
        sections = checkpoint_sections(str(checkpoint["content"]))
        for key, values in sections.items():
            collected[key].extend(values)
    return {key: unique_preserve_order(values)[:10] for key, values in collected.items()}


def build_resume_brief(
    repo: dict[str, str],
    task: str,
    checkpoints: list[dict[str, object]],
    relevant: list[dict[str, object]],
    collected: dict[str, list[str]],
) -> str:
    lines = ["Project resume brief:"]
    lines.append(f"- Repo: {repo['repo_id']} at {repo['root']}")
    if task:
        lines.append(f"- Task: {task}")
    if checkpoints:
        lines.append("- Recent checkpoints:")
        for checkpoint in checkpoints[:5]:
            preview = str(checkpoint["content"]).splitlines()[0]
            lines.append(f"  - [{checkpoint['id']}] {checkpoint['updated_at']}: {preview}")
    if collected["blockers"]:
        lines.append("- Open blockers: " + compact_list(collected["blockers"], 6))
    if collected["next_steps"]:
        lines.append("- Next steps: " + compact_list(collected["next_steps"], 8))
    if collected["tests_not_run"]:
        lines.append("- Tests not run: " + compact_list(collected["tests_not_run"], 6))
    if collected["files"]:
        lines.append("- Recently touched files: " + compact_list(collected["files"], 10))
    non_checkpoint = [memory for memory in relevant if memory["type"] != "task_summary"]
    if non_checkpoint:
        lines.append("- Relevant durable memory:")
        for memory in non_checkpoint[:6]:
            files = memory.get("related_files") or []
            file_text = " files=" + compact_list([str(item) for item in files], 4) if files else ""
            lines.append(f"  - [{memory['id']}] {memory['type']}{file_text}: {memory['content']}")
    return "\n".join(lines)


def tool_memory_resume_project(args_obj: object) -> dict[str, object]:
    args = validate_args(args_obj)
    if "cwd" not in args:
        raise MemoryErrorValue("invalid_cwd", "cwd is required")
    repo = repo_identity(args.get("cwd"))
    set_current_repo(repo)
    task = optional_text(args.get("task"), "task") or "resume project"
    files = normalize_files(args.get("files"), repo)
    limit = clamp_limit(args.get("limit"), DEFAULT_LIMIT)
    checkpoint_limit = clamp_limit(args.get("checkpoint_limit"), 5)
    context = tool_memory_context({"task": task, "cwd": repo["root"], "files": files, "limit": limit})
    with connect_db() as conn:
        checkpoint_rows = repo_memory_rows(conn, repo, checkpoint_limit, "task_summary")
    checkpoints = [row_to_memory(row, include_score=False) for row in checkpoint_rows]
    collected = collect_checkpoint_items(checkpoints)
    relevant = [dict(memory) for memory in context["memories"]]
    brief = build_resume_brief(repo, task, checkpoints, relevant, collected)
    return {
        "repo": repo,
        "task": task,
        "files": files,
        "visible_status": f"Memory Brain: resume brief loaded {len(checkpoints)} checkpoints and {len(relevant)} relevant memories.",
        "brief": brief,
        "recent_checkpoints": checkpoints,
        "checkpoint_items": collected,
        "relevant_memories": relevant,
    }


def tool_memory_timeline(args_obj: object) -> dict[str, object]:
    args = validate_args(args_obj)
    if "cwd" not in args:
        raise MemoryErrorValue("invalid_cwd", "cwd is required")
    repo = repo_identity(args.get("cwd"))
    set_current_repo(repo)
    limit = clamp_limit(args.get("limit"), 20)
    days = clamp_limit(args.get("days"), 14)
    memory_type = validate_type(args["type"]) if args.get("type") is not None else None
    cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)
    with connect_db() as conn:
        rows = repo_memory_rows(conn, repo, max(limit * 4, 40), memory_type)
    memories = []
    for row in rows:
        memory = row_to_memory(row, include_score=False)
        if parse_time(str(memory["updated_at"])) >= cutoff:
            memories.append(memory)
        if len(memories) >= limit:
            break
    groups: dict[str, list[dict[str, object]]] = {}
    for memory in memories:
        date_key = parse_time(str(memory["updated_at"])).date().isoformat()
        groups.setdefault(date_key, []).append(memory)
    lines = ["Project memory timeline:"]
    if not groups:
        lines.append("No active memories in the requested window.")
    for date_key in sorted(groups.keys(), reverse=True):
        lines.append(date_key + ":")
        for memory in groups[date_key]:
            lines.append(f"- [{memory['id']}] {memory['type']} / {memory['scope']}: {memory['content']}")
    return {
        "repo": repo,
        "days": days,
        "limit": limit,
        "type": memory_type,
        "visible_status": f"Memory Brain: timeline loaded {len(memories)} memories from the last {days} days.",
        "timeline": "\n".join(lines),
        "groups": groups,
        "memories": memories,
    }


TOOLS = {
    "memory_context": {
        "handler": tool_memory_context,
        "description": "Return compact ranked local memory context for a coding task.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {"type": "string"},
                "cwd": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
            },
            "required": ["task", "cwd"],
            "additionalProperties": False,
        },
    },
    "memory_search": {
        "handler": tool_memory_search,
        "description": "Search active local memories with SQLite FTS5.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "cwd": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
                "scope": {"type": "string", "enum": sorted(ALLOWED_SCOPES)},
                "type": {"type": "string", "enum": sorted(ALLOWED_TYPES)},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    "memory_remember": {
        "handler": tool_memory_remember,
        "description": "Save a durable local memory after safety checks and deduplication.",
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "cwd": {"type": "string"},
                "scope": {"type": "string", "enum": sorted(ALLOWED_SCOPES)},
                "type": {"type": "string", "enum": sorted(ALLOWED_TYPES)},
                "files": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["content", "scope", "type"],
            "additionalProperties": False,
        },
    },
    "memory_update": {
        "handler": tool_memory_update,
        "description": "Update or archive an existing memory.",
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "content": {"type": "string"},
                "cwd": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "archived": {"type": "boolean"},
            },
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    "memory_forget": {
        "handler": tool_memory_forget,
        "description": "Archive a memory without hard-deleting it.",
        "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    "memory_status": {
        "handler": tool_memory_status,
        "description": "Show database path, repo identity, counts, and recent memories.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {"cwd": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "memory_audit_repo": {
        "handler": tool_memory_audit_repo,
        "description": "Audit a repository and propose or save a balanced set of durable memory candidates.",
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string"},
                "save": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 20},
            },
            "required": ["cwd"],
            "additionalProperties": False,
        },
    },
    "memory_task_checkpoint": {
        "handler": tool_memory_task_checkpoint,
        "description": "Save an end-of-session project continuity checkpoint with changes, blockers, tests, and next steps.",
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string"},
                "summary": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
                "changes": {"type": "array", "items": {"type": "string"}},
                "decisions": {"type": "array", "items": {"type": "string"}},
                "blockers": {"type": "array", "items": {"type": "string"}},
                "next_steps": {"type": "array", "items": {"type": "string"}},
                "tests_run": {"type": "array", "items": {"type": "string"}},
                "tests_not_run": {"type": "array", "items": {"type": "string"}},
                "branch": {"type": "string"},
                "commit": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["cwd"],
            "additionalProperties": False,
        },
    },
    "memory_resume_project": {
        "handler": tool_memory_resume_project,
        "description": "Return a compact project resume brief from recent checkpoints plus relevant durable memories.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string"},
                "task": {"type": "string"},
                "files": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "checkpoint_limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
            },
            "required": ["cwd"],
            "additionalProperties": False,
        },
    },
    "memory_timeline": {
        "handler": tool_memory_timeline,
        "description": "Show recent project memory grouped by day for multi-day continuity.",
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False},
        "inputSchema": {
            "type": "object",
            "properties": {
                "cwd": {"type": "string"},
                "days": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT},
                "type": {"type": "string", "enum": sorted(ALLOWED_TYPES)},
            },
            "required": ["cwd"],
            "additionalProperties": False,
        },
    },
}


RESOURCES = {
    "memory://user/profile": {
        "name": "User Memory Profile",
        "description": "User-scoped preferences and durable global memory.",
    },
    "memory://repo/current/brief": {
        "name": "Current Repo Memory Brief",
        "description": "Compact brief of important current-repo memories.",
    },
    "memory://repo/current/commands": {
        "name": "Current Repo Commands",
        "description": "Useful saved commands for the current repo.",
    },
    "memory://repo/current/decisions": {
        "name": "Current Repo Decisions",
        "description": "Saved decisions for the current repo.",
    },
    "memory://repo/current/gotchas": {
        "name": "Current Repo Gotchas",
        "description": "Saved gotchas for the current repo.",
    },
    "memory://repo/current/resume": {
        "name": "Current Repo Resume Brief",
        "description": "Recent checkpoints plus relevant memory for resuming work.",
    },
    "memory://repo/current/timeline": {
        "name": "Current Repo Memory Timeline",
        "description": "Recent project memory grouped by day.",
    },
}

PROMPTS = {
    "memory-check": {
        "description": "Check Codex Memory Brain before starting repo work.",
        "arguments": [
            {
                "name": "task",
                "description": "The coding task or question to retrieve memory for.",
                "required": True,
            },
            {
                "name": "cwd",
                "description": "Absolute path to the current repository or workspace.",
                "required": False,
            },
        ],
    },
    "memory-status": {
        "description": "Show whether Codex Memory Brain is installed and what it knows for this repo.",
        "arguments": [
            {
                "name": "cwd",
                "description": "Absolute path to the current repository or workspace.",
                "required": False,
            }
        ],
    },
    "memory-save": {
        "description": "Save durable repo context after substantial work.",
        "arguments": [
            {
                "name": "summary",
                "description": "Durable convention, command, decision, gotcha, landmark, or preference to remember.",
                "required": True,
            },
            {
                "name": "cwd",
                "description": "Absolute path to the current repository or workspace.",
                "required": False,
            },
        ],
    },
    "memory-audit": {
        "description": "Run a structured repo audit and build a balanced memory set.",
        "arguments": [
            {
                "name": "cwd",
                "description": "Absolute path to the repository or workspace to audit.",
                "required": True,
            },
            {
                "name": "save",
                "description": "Use true to save candidates, false to preview them.",
                "required": False,
            },
        ],
    },
    "memory-checkpoint": {
        "description": "Save an end-of-session project continuity checkpoint.",
        "arguments": [
            {
                "name": "cwd",
                "description": "Absolute path to the repository or workspace.",
                "required": True,
            },
            {
                "name": "summary",
                "description": "Short summary of what changed or what should be resumed later.",
                "required": True,
            },
        ],
    },
    "memory-resume": {
        "description": "Load a project resume brief before continuing multi-day work.",
        "arguments": [
            {
                "name": "cwd",
                "description": "Absolute path to the repository or workspace.",
                "required": True,
            },
            {
                "name": "task",
                "description": "Optional task to retrieve relevant resume context for.",
                "required": False,
            },
        ],
    },
    "memory-timeline": {
        "description": "Show recent project memory grouped by date.",
        "arguments": [
            {
                "name": "cwd",
                "description": "Absolute path to the repository or workspace.",
                "required": True,
            },
            {
                "name": "days",
                "description": "Number of recent days to include.",
                "required": False,
            },
        ],
    },
}


def list_tools() -> dict[str, object]:
    tools = []
    for name, data in TOOLS.items():
        item = {
            "name": name,
            "description": data["description"],
            "inputSchema": data["inputSchema"],
        }
        if "annotations" in data:
            item["annotations"] = data["annotations"]
        tools.append(item)
    return {"tools": tools}


def call_tool(name: object, arguments: object) -> dict[str, object]:
    if not isinstance(name, str) or name not in TOOLS:
        return tool_error("unknown_tool", "unknown tool: " + str(name))
    try:
        result = TOOLS[name]["handler"](arguments)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(result, ensure_ascii=False, indent=2),
                }
            ],
            "isError": False,
        }
    except MemoryErrorValue as exc:
        return tool_error(exc.code, exc.message)
    except Exception as exc:
        return tool_error("internal_error", str(exc))


def tool_error(code: str, message: str) -> dict[str, object]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps({"error": {"code": code, "message": message}}, ensure_ascii=False, indent=2),
            }
        ],
        "isError": True,
    }


def list_resources() -> dict[str, object]:
    return {
        "resources": [
            {
                "uri": uri,
                "name": data["name"],
                "description": data["description"],
                "mimeType": "text/markdown",
            }
            for uri, data in RESOURCES.items()
        ]
    }


def list_prompts() -> dict[str, object]:
    return {
        "prompts": [
            {
                "name": name,
                "description": data["description"],
                "arguments": data["arguments"],
            }
            for name, data in PROMPTS.items()
        ]
    }


def get_prompt(name: object, arguments: object) -> dict[str, object]:
    if not isinstance(name, str) or name not in PROMPTS:
        raise MemoryErrorValue("unknown_prompt", "unknown prompt: " + str(name))
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise MemoryErrorValue("invalid_arguments", "arguments must be an object")

    cwd = arguments.get("cwd")
    cwd_text = str(cwd).strip() if isinstance(cwd, str) and cwd.strip() else "<current repo cwd>"

    if name == "memory-check":
        task = arguments.get("task")
        task_text = str(task).strip() if isinstance(task, str) and task.strip() else "<describe the task>"
        text = (
            "Use codex-memory for this repo. Call memory_context with "
            f'task="{task_text}", cwd="{cwd_text}", and relevant files if known. '
            "Show the Memory Brain status line before continuing."
        )
    elif name == "memory-status":
        text = (
            "Use codex-memory for this repo. Call memory_status with "
            f'cwd="{cwd_text}" and summarize whether memory is active.'
        )
    elif name == "memory-save":
        summary = arguments.get("summary")
        summary_text = str(summary).strip() if isinstance(summary, str) and summary.strip() else "<durable memory to save>"
        text = (
            "Use codex-memory to save this only if it is durable, non-secret engineering context: "
            f'"{summary_text}". Call memory_remember with the correct scope/type and cwd="{cwd_text}", '
            "then show the Memory Brain saved line."
        )
    elif name == "memory-audit":
        save = arguments.get("save")
        save_text = "true" if save is True else "false"
        text = (
            "Use codex-memory for this repo. Call memory_audit_repo with "
            f'cwd="{cwd_text}", save={save_text}, then show the Memory Brain audit status line. '
            "If save=false, review the proposed candidates before saving. If save=true, summarize what was saved and what was already present."
        )
    elif name == "memory-checkpoint":
        summary = arguments.get("summary")
        summary_text = str(summary).strip() if isinstance(summary, str) and summary.strip() else "<what changed and where to resume>"
        text = (
            "Use codex-memory to save a project continuity checkpoint. Call memory_task_checkpoint with "
            f'cwd="{cwd_text}", summary="{summary_text}", and include files, changes, blockers, next_steps, '
            "tests_run, and tests_not_run when known. Show the Memory Brain checkpoint status line."
        )
    elif name == "memory-resume":
        task = arguments.get("task")
        task_text = str(task).strip() if isinstance(task, str) and task.strip() else "resume project"
        text = (
            "Use codex-memory before continuing this project. Call memory_resume_project with "
            f'cwd="{cwd_text}", task="{task_text}", then show the Memory Brain resume status line and summarize the brief.'
        )
    else:
        days = arguments.get("days")
        days_text = str(days) if isinstance(days, int) and days > 0 else "14"
        text = (
            "Use codex-memory to inspect recent project history. Call memory_timeline with "
            f'cwd="{cwd_text}", days={days_text}, then show the Memory Brain timeline status line.'
        )

    return {
        "description": PROMPTS[name]["description"],
        "messages": [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": text,
                },
            }
        ],
    }


def render_memories_markdown(title: str, memories: list[dict[str, object]]) -> str:
    if not memories:
        return "# " + title + "\n\nNo active memories found.\n"
    lines = ["# " + title, ""]
    for memory in memories:
        files = memory.get("related_files") or []
        file_text = " (" + ", ".join(files) + ")" if files else ""
        lines.append(f"- `{memory['id']}` `{memory['type']}` `{memory['scope']}`{file_text}: {memory['content']}")
    return "\n".join(lines) + "\n"


def read_resource(uri: object) -> dict[str, object]:
    if not isinstance(uri, str) or uri not in RESOURCES:
        raise MemoryErrorValue("unknown_resource", "unknown resource: " + str(uri))
    current_repo = current_repo_identity()
    with connect_db() as conn:
        if uri == "memory://user/profile":
            rows = conn.execute(
                """
                SELECT m.*, 0.0 AS score FROM memories m
                WHERE m.archived = 0 AND (m.scope = 'user' OR m.scope = 'global' OR m.type = 'preference')
                ORDER BY m.updated_at DESC LIMIT 25
                """
            ).fetchall()
            text = render_memories_markdown("User Memory Profile", [row_to_memory(row) for row in rows])
        elif uri == "memory://repo/current/brief":
            rows = fetch_recent(conn, current_repo, 25)
            memories = [row_to_memory(row) for row in rows]
            for memory in memories:
                memory["rank"] = rank_memory(memory, "", current_repo, [])
            memories.sort(key=lambda item: -float(item["rank"]))
            text = render_memories_markdown("Current Repo Memory Brief", memories[:12])
        elif uri == "memory://repo/current/resume":
            result = tool_memory_resume_project({"cwd": current_repo["root"], "task": "resume project", "limit": 8})
            text = "# Current Repo Resume Brief\n\n" + str(result["brief"]) + "\n"
        elif uri == "memory://repo/current/timeline":
            result = tool_memory_timeline({"cwd": current_repo["root"], "days": 14, "limit": 20})
            text = "# Current Repo Memory Timeline\n\n" + str(result["timeline"]) + "\n"
        else:
            type_by_uri = {
                "memory://repo/current/commands": "command",
                "memory://repo/current/decisions": "decision",
                "memory://repo/current/gotchas": "gotcha",
            }
            memory_type = type_by_uri[uri]
            rows = conn.execute(
                """
                SELECT m.*, 0.0 AS score FROM memories m
                WHERE m.archived = 0 AND m.type = ? AND (m.repo_id = ? OR m.repo_id = '' OR m.scope IN ('user','global'))
                ORDER BY m.updated_at DESC LIMIT 25
                """,
                [memory_type, current_repo["repo_id"]],
            ).fetchall()
            text = render_memories_markdown(RESOURCES[uri]["name"], [row_to_memory(row) for row in rows])
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "text/markdown",
                "text": text,
            }
        ]
    }


def rpc_result(message_id: object, result: object) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def rpc_error(message_id: object, code: int, message: str, data: object | None = None) -> dict[str, object]:
    error: dict[str, object] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": message_id, "error": error}


def handle_message(message: dict[str, object]) -> dict[str, object] | None:
    message_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if not isinstance(method, str):
        return rpc_error(message_id, -32600, "Invalid request: method must be a string")
    is_notification = "id" not in message

    try:
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            }
        elif method == "notifications/initialized":
            return None
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = list_tools()
        elif method == "tools/call":
            if not isinstance(params, dict):
                return rpc_error(message_id, -32602, "Invalid params: expected object")
            result = call_tool(params.get("name"), params.get("arguments") or {})
        elif method == "resources/list":
            result = list_resources()
        elif method == "resources/read":
            if not isinstance(params, dict):
                return rpc_error(message_id, -32602, "Invalid params: expected object")
            try:
                result = read_resource(params.get("uri"))
            except MemoryErrorValue as exc:
                return rpc_error(message_id, -32602, exc.message, {"code": exc.code})
        elif method == "prompts/list":
            result = list_prompts()
        elif method == "prompts/get":
            if not isinstance(params, dict):
                return rpc_error(message_id, -32602, "Invalid params: expected object")
            try:
                result = get_prompt(params.get("name"), params.get("arguments") or {})
            except MemoryErrorValue as exc:
                return rpc_error(message_id, -32602, exc.message, {"code": exc.code})
        else:
            if is_notification:
                return None
            return rpc_error(message_id, -32601, "Method not found: " + method)
    except Exception as exc:
        return rpc_error(message_id, -32603, "Internal error", {"message": str(exc)})

    if is_notification:
        return None
    return rpc_result(message_id, result)


def read_framed_message(stdin: object, first_line: bytes | None = None) -> dict[str, object] | None:
    headers: dict[str, str] = {}
    pending_line = first_line
    while True:
        if pending_line is None:
            line = stdin.readline()
        else:
            line = pending_line
            pending_line = None
        if line == b"":
            return None
        line = line.strip()
        if not line:
            break
        try:
            key, value = line.decode("ascii").split(":", 1)
        except ValueError:
            continue
        headers[key.lower()] = value.strip()
    length_text = headers.get("content-length")
    if not length_text:
        raise ValueError("missing Content-Length header")
    length = int(length_text)
    body = stdin.read(length)
    if len(body) != length:
        return None
    return json.loads(body.decode("utf-8"))


def write_framed_message(stdout: object, message: dict[str, object]) -> None:
    body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
    stdout.write(header)
    stdout.write(body)
    stdout.flush()


class StdioTransport:
    """Read/write either MCP Content-Length frames or JSON-line messages.

    Codex desktop and older MCP clients may use header-framed stdio, while the
    Codex CLI rmcp client currently speaks newline-delimited JSON-RPC. Supporting
    both keeps the local server installable across Codex surfaces.
    """

    def __init__(self, stdin: object, stdout: object):
        self.stdin = stdin
        self.stdout = stdout
        self.mode: str | None = None

    def read_message(self) -> dict[str, object] | None:
        if self.mode == "framed":
            return read_framed_message(self.stdin)
        if self.mode == "jsonl":
            return self._read_jsonl_message()

        first_line = self.stdin.readline()
        if first_line == b"":
            return None
        stripped = first_line.lstrip()
        if stripped.startswith(b"{") or stripped.startswith(b"["):
            self.mode = "jsonl"
            return json.loads(first_line.decode("utf-8"))
        self.mode = "framed"
        return read_framed_message(self.stdin, first_line=first_line)

    def _read_jsonl_message(self) -> dict[str, object] | None:
        while True:
            line = self.stdin.readline()
            if line == b"":
                return None
            if line.strip():
                return json.loads(line.decode("utf-8"))

    def write_message(self, message: dict[str, object]) -> None:
        if self.mode == "jsonl":
            body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.stdout.write(body + b"\n")
            self.stdout.flush()
            return
        write_framed_message(self.stdout, message)


def main() -> int:
    init_error: str | None = None
    try:
        with connect_db():
            pass
    except Exception:
        init_error = traceback.format_exc()
    transport = StdioTransport(sys.stdin.buffer, sys.stdout.buffer)
    while True:
        try:
            message = transport.read_message()
        except Exception as exc:
            transport.write_message(rpc_error(None, -32700, "Parse error", {"message": str(exc)}))
            continue
        if message is None:
            return 0
        if init_error and message.get("method") != "initialize":
            response = rpc_error(message.get("id"), -32603, "Database initialization failed", {"trace": init_error})
        else:
            response = handle_message(message)
        if response is not None:
            transport.write_message(response)


if __name__ == "__main__":
    raise SystemExit(main())
