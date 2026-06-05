from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .models import RepoConfig, ResolvedRepo


DEFAULT_CONFIG_DIR = Path.home() / ".config" / "gitrelease"
DEFAULT_SEND_CONFIG_PATH = DEFAULT_CONFIG_DIR / "send.json"
DEFAULT_ROLLBACK_CONFIG_PATH = DEFAULT_CONFIG_DIR / "rollback.json"
DEFAULT_RELEASE_CONFIG_PATH = DEFAULT_CONFIG_DIR / "release.json"
DEFAULT_RELEASE_GITHUB_CONFIG_PATH = DEFAULT_CONFIG_DIR / "release_github.json"
DEFAULT_ADDRESSES_CONFIG_PATH = DEFAULT_CONFIG_DIR / "addresses.json"
PACKAGE_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load_config(config_path: str | None, default_path: Path) -> dict[str, Any]:
    path = _resolve_config_path(config_path, default_path)

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _resolve_config_path(config_path: str | None, default_path: Path) -> Path:
    if config_path:
        return Path(config_path).expanduser()

    package_config = PACKAGE_CONFIG_DIR / default_path.name
    if package_config.exists():
        return package_config

    package_example_config = PACKAGE_CONFIG_DIR / default_path.name.replace(".json", ".example.json")
    if package_example_config.exists():
        return package_example_config

    return default_path


def load_send_config(config_path: str | None) -> dict[str, Any]:
    return _load_config(config_path, DEFAULT_SEND_CONFIG_PATH)


def load_release_config(config_path: str | None) -> dict[str, Any]:
    return _load_config(config_path, DEFAULT_RELEASE_CONFIG_PATH)


def load_release_config_for_target(config_path: str | None, target_name: str) -> dict[str, Any]:
    if config_path:
        return load_release_config(config_path)

    if target_name in {"github", "github.com"}:
        return _load_config(None, DEFAULT_RELEASE_GITHUB_CONFIG_PATH)

    return load_release_config(None)


def load_rollback_config(config_path: str | None) -> dict[str, Any]:
    return _load_config(config_path, DEFAULT_ROLLBACK_CONFIG_PATH)


def load_addresses_config(config_path: str | None = None) -> dict[str, str]:
    try:
        raw = _load_config(config_path, DEFAULT_ADDRESSES_CONFIG_PATH)
    except FileNotFoundError:
        return {}

    addresses = raw.get("addresses", raw)
    if not isinstance(addresses, dict):
        raise RuntimeError("addresses.json must contain an object or top-level addresses object.")

    result: dict[str, str] = {}
    for name, address in addresses.items():
        if not isinstance(name, str) or not isinstance(address, str):
            raise RuntimeError("addresses.json keys and values must be strings.")
        result[name] = address
    return result


def ensure_release_host(config: dict[str, Any]) -> None:
    return


def resolve_release_repo(
    config: dict[str, Any],
    repo: RepoConfig,
    target_name: str = "local",
) -> ResolvedRepo:
    if config.get("from") or config.get("target"):
        return _resolve_path_release_repo(config, repo)

    if repo.bare_repo_address:
        return _resolve_address_release_repo(config, repo)

    target = _resolve_release_target(config, target_name)
    workspace_root = target.get("workspace_root")
    if not workspace_root:
        raise RuntimeError(f"workspace_root is missing for release target: {target_name}")

    bare_root = _resolve_bare_root(config, repo.bare_target, target)
    if not bare_root:
        raise RuntimeError(f"bare_root is missing for release target: {target_name}")

    worktree = _join_path(workspace_root, repo.worktree).resolve()
    bare = repo.bare if _looks_like_remote(repo.bare) else str(_join_path(bare_root, repo.bare))

    return ResolvedRepo(
        name=repo.name,
        worktree=worktree,
        bare=bare,
        branch=repo.branch,
        enabled=repo.enabled,
        send_include_paths=repo.send_include_paths,
        target_name=target_name,
        ssh_address=target.get("ssh_address"),
    )


def _resolve_path_release_repo(
    config: dict[str, Any],
    repo: RepoConfig,
) -> ResolvedRepo:
    from_config = config.get("from") or {}
    target_config = config.get("target") or {}

    from_root = from_config.get("root") or from_config.get("workspace_root")
    target_root = target_config.get("root") or target_config.get("workspace_root")
    if not from_root:
        raise RuntimeError("from.root is missing in release config.")

    bare_value = _expand_target_template(repo.bare, target_config)
    bare_is_remote_url = _looks_like_remote(bare_value)
    if not target_root and not bare_is_remote_url:
        raise RuntimeError("target.root is missing in release config.")

    from_address = from_config.get("address") or "127.0.0.1"
    target_address = target_config.get("address") or target_config.get("target_address") or "127.0.0.1"
    ssh_user = target_config.get("ssh_user") or config.get("ssh_user") or repo.ssh_user

    worktree = _join_repo_path(from_root, repo.worktree).resolve()
    bare_path = bare_value if bare_is_remote_url else str(_join_repo_path(target_root, bare_value))
    bare = bare_path
    if not bare_is_remote_url and not _is_local_address(target_address):
        if not ssh_user:
            raise RuntimeError(
                f"target.ssh_user is required when target.address is remote: {target_address}"
            )
        bare = f"{ssh_user}@{target_address}:{bare_path}"

    ssh_address = None
    if not _is_local_address(from_address):
        from_ssh_user = from_config.get("ssh_user") or config.get("ssh_user") or repo.ssh_user
        if not from_ssh_user:
            raise RuntimeError(
                f"from.ssh_user is required when from.address is remote: {from_address}"
            )
        ssh_address = f"{from_ssh_user}@{from_address}"

    return ResolvedRepo(
        name=repo.name,
        worktree=worktree,
        bare=bare,
        branch=repo.branch,
        enabled=repo.enabled,
        send_include_paths=repo.send_include_paths,
        target_name=target_address,
        ssh_address=ssh_address,
    )


def _resolve_address_release_repo(
    config: dict[str, Any],
    repo: RepoConfig,
) -> ResolvedRepo:
    address = repo.bare_repo_address or "127.0.0.1"
    ssh_address = None
    if not _is_local_address(address):
        ssh_user = repo.ssh_user or config.get("ssh_user")
        if not ssh_user:
            raise RuntimeError(
                f"ssh_user is required when bare_repo_address is remote: {address}"
            )
        ssh_address = f"{ssh_user}@{address}"

    return ResolvedRepo(
        name=repo.name,
        worktree=_join_path("", repo.worktree).resolve(),
        bare=repo.bare,
        branch=repo.branch,
        enabled=repo.enabled,
        send_include_paths=repo.send_include_paths,
        target_name=address,
        ssh_address=ssh_address,
    )
def parse_repo(
    raw: dict[str, Any],
    default_branch: str,
    require_bare: bool = True,
) -> RepoConfig:
    name = (
        raw.get("send_object_name")
        or raw.get("release_repo_name")
        or raw.get("repo_name")
        or raw.get("name")
    )
    if not name:
        raise RuntimeError("repo config requires repo_name or name.")

    local_git_worktree_root = (
        raw.get("from_repo_path")
        or raw.get("target_repo_path")
        or raw.get("project_path")
        or raw.get("local_git_worktree_root")
        or raw.get("git_worktree_root")
        or raw.get("git_worktree")
        or raw.get("worktree")
    )
    if not local_git_worktree_root:
        raise RuntimeError(
            "repo config requires from_repo_path, target_repo_path, or git_worktree_root."
        )

    remote_git_worktree_root = (
        raw.get("target_repo_path")
        or raw.get("remote_git_worktree_root")
        or raw.get("git_worktree_root")
        or raw.get("git_worktree")
        or raw.get("worktree")
        or local_git_worktree_root
    )

    return RepoConfig(
        name=name,
        worktree=local_git_worktree_root,
        bare=(
            raw.get("target_repo_path")
            or
            raw.get("bare_repo_path")
            or raw.get("bare_repo")
            or (raw["bare"] if require_bare else raw.get("bare", ""))
        ),
        branch=raw.get("branch") or default_branch,
        enabled=raw.get("enabled", True),
        send_include_paths=list(raw.get("send_include_paths") or []),
        remote_git_worktree_root=remote_git_worktree_root,
        bare_target=raw.get("bare_target"),
        bare_repo_address=raw.get("bare_repo_address"),
        ssh_user=raw.get("ssh_user"),
    )


def list_repos(
    config: dict[str, Any],
    repo_name: str | None = None,
    include_disabled: bool = False,
    require_bare: bool = True,
) -> list[RepoConfig]:
    release_source = config.get("release_source") or {}
    default_branch = (
        release_source.get("default_branch")
        or config.get("default_branch")
        or ("master" if require_bare else "")
    )
    if config.get("repos") is not None:
        raw_repos = config["repos"]
    elif require_bare:
        raw_repos = config.get("release_repos") or []
    else:
        raw_repos = config.get("send_objects") or config.get("rollback_objects") or []
    repos = [
        parse_repo(r, default_branch, require_bare=require_bare)
        for r in raw_repos
    ]

    if not include_disabled:
        repos = [r for r in repos if r.enabled]

    if repo_name:
        repos = [r for r in repos if r.name == repo_name]

    if repo_name and not repos:
        raise RuntimeError(f"Repo not found or disabled: {repo_name}")

    return repos


def _resolve_release_target(config: dict[str, Any], target_name: str) -> dict[str, Any]:
    targets = config.get("targets") or {}
    if targets:
        if target_name not in targets:
            raise RuntimeError(f"release target not found: {target_name}")
        return targets[target_name]

    if target_name != "local":
        raise RuntimeError(f"release target not found: {target_name}")

    release_source = config.get("release_source") or {}
    target: dict[str, Any] = {}
    if release_source.get("workspace_root") or config.get("workspace_root"):
        target["workspace_root"] = release_source.get("workspace_root") or config.get("workspace_root")
    bare_root = _resolve_bare_root(config, None, None)
    if bare_root:
        target["bare_root"] = bare_root
    return target


def _join_path(root: str, child: str) -> Path:
    p = Path(child).expanduser()
    if p.is_absolute():
        return p
    return Path(root).expanduser() / p


def _join_repo_path(root: str, child: str) -> Path:
    root_path = Path(root).expanduser()
    child_text = str(child)
    child_path = Path(child_text).expanduser()
    if child_path.is_absolute():
        if str(child_path).startswith(str(root_path)):
            return child_path
        return root_path / child_text.lstrip("/")
    return root_path / child_path


def _looks_like_remote(value: str) -> bool:
    return (
        value.startswith("git@")
        or value.startswith("ssh://")
        or value.startswith("https://")
        or value.startswith("http://")
    )


def _expand_target_template(value: str, target_config: dict[str, Any]) -> str:
    if "{" not in value:
        return value

    addresses = load_addresses_config()
    template_values = {
        **addresses,
        "target_address": target_config.get("address") or target_config.get("target_address") or "",
        "target_ssh_user": target_config.get("ssh_user") or "",
        "target_repo_owner": target_config.get("repo_owner") or target_config.get("owner") or "",
        "github_username": target_config.get("github_username") or "",
    }

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = template_values.get(key)
        if not value:
            raise RuntimeError(
                f"Unknown or empty release target template key {{{key}}}."
            )
        return value

    return re.sub(r"\{([A-Za-z0-9_]+)\}", replace, value)


def _is_local_address(value: str) -> bool:
    return value in {"local", "localhost", "127.0.0.1", "::1"} or value.startswith("127.")


def _resolve_bare_root(
    config: dict[str, Any],
    bare_target: str | None,
    target: dict[str, Any] | None = None,
) -> str | None:
    if target and target.get("bare_root"):
        return target.get("bare_root")

    if bare_target:
        targets = config.get("bare_targets") or {}
        target = targets.get(bare_target)
        if not target:
            raise RuntimeError(f"bare target not found: {bare_target}")
        return target.get("bare_root")

    return config.get("bare_root")
