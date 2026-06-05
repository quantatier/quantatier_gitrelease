from __future__ import annotations

import json
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HISTORY_DIR = ".gitrelease"
HISTORY_FILE = "history.jsonl"


def history_path(repo_root: Path) -> Path:
    return repo_root / HISTORY_DIR / HISTORY_FILE


def history_record(action: str, repo_name: str, **fields: Any) -> dict[str, Any]:
    return {
        "time": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "repo_name": repo_name,
        **fields,
    }


def append_local_history(repo_root: Path, record: dict[str, Any]) -> None:
    path = history_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        f.write("\n")


def build_append_remote_history_script(repo_root: str, record: dict[str, Any]) -> str:
    history_dir = shlex.quote(f"{repo_root.rstrip('/')}/{HISTORY_DIR}")
    history_file = shlex.quote(f"{repo_root.rstrip('/')}/{HISTORY_DIR}/{HISTORY_FILE}")
    payload = shlex.quote(json.dumps(record, ensure_ascii=False, sort_keys=True))
    return f"mkdir -p {history_dir} && printf '%s\\n' {payload} >> {history_file}"


def build_append_remote_history_script_with_shell_fields(
    repo_root: str,
    record: dict[str, Any],
    shell_fields: dict[str, str],
) -> str:
    history_dir = shlex.quote(f"{repo_root.rstrip('/')}/{HISTORY_DIR}")
    history_file = shlex.quote(f"{repo_root.rstrip('/')}/{HISTORY_DIR}/{HISTORY_FILE}")
    parts: list[str] = []
    args: list[str] = []
    for key in sorted(record):
        if key in shell_fields:
            parts.append(f"{json.dumps(key)}:\"%s\"")
            args.append(f'"${shell_fields[key]}"')
        else:
            parts.append(f"{json.dumps(key)}:{json.dumps(record[key], ensure_ascii=False)}")
    payload_format = "{" + ",".join(parts) + "}\\n"
    quoted_format = shlex.quote(payload_format)
    return (
        f"mkdir -p {history_dir} && "
        f"printf {quoted_format} {' '.join(args)} >> {history_file}"
    )
