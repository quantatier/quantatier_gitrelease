from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RepoConfig:
    name: str
    worktree: str
    bare: str
    branch: str
    enabled: bool
    send_include_paths: list[str]
    remote_git_worktree_root: str | None = None
    bare_target: str | None = None
    bare_repo_address: str | None = None
    ssh_user: str | None = None


@dataclass(frozen=True)
class ResolvedRepo:
    name: str
    worktree: Path
    bare: str
    branch: str
    enabled: bool
    send_include_paths: list[str]
    target_name: str = "local"
    ssh_address: str | None = None


@dataclass(frozen=True)
class RepoStatus:
    name: str
    ok: bool
    worktree: str
    bare: str
    branch: str | None
    origin: str | None
    dirty: bool
    ahead: int | None
    behind: int | None
    version: str | None
    tag: str | None
    local_tag_exists: bool | None
    remote_tag_exists: bool | None
    errors: list[str]
