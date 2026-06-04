from __future__ import annotations

import shlex
import uuid
from pathlib import Path
from typing import Callable

from .history import (
    build_append_remote_history_script_with_shell_fields,
    history_record,
)
from .models import RepoConfig
from .runner import run_live


DEFAULT_EXCLUDES = [
    ".git/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".DS_Store",
    ".ipynb_checkpoints/",
    ".vntrader/",
    ".qtcli_gitrelease/",
    "dist/",
    "build/",
    "BacktestingResult/",
    "*.egg-info/",
]
ROLLBACK_ROOT = "~/.qtcli_gitrelease/send_rollback"
PROTECTED_PATH_PARTS = {
    ".git",
    ".venv",
    "venv",
    ".DS_Store",
    ".ipynb_checkpoints",
    ".vntrader",
    ".qtcli_gitrelease",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    "BacktestingResult",
}


def _local_repo_root(workspace_root: str, worktree: str) -> Path:
    _validate_local_workspace_root(workspace_root)
    return _join_repo_path(workspace_root, worktree).resolve()


def _remote_repo_root(target: dict, worktree: str) -> str:
    ssh_address, path = _remote_repo_parts(target, worktree)
    return f"{ssh_address}:{path}"


def _remote_repo_parts(target: dict, worktree: str) -> tuple[str, str]:
    root = target.get("root") or target.get("workspace_root")
    if not root:
        raise RuntimeError("target.root is missing in send config.")
    _validate_remote_workspace_root(root)

    ssh_address = target.get("ssh_address") or _ssh_address_from_target_address(target)
    if not ssh_address:
        raise RuntimeError(
            "send target requires target.address with target.ssh_user."
        )
    _validate_ssh_address(ssh_address)

    path = str(_join_repo_path(root, worktree))
    if _has_placeholder(path):
        raise RuntimeError(
            f"Remote repo path still contains a placeholder: {path!r}. "
            f"Please check send.json before sending."
        )
    return ssh_address, path


def _target_repo_parts(target: dict, worktree: str) -> tuple[str | None, str]:
    root = target.get("root") or target.get("workspace_root")
    if not root:
        raise RuntimeError("target.root is missing in target config.")
    _validate_remote_workspace_root(root)

    ssh_address = target.get("ssh_address") or _ssh_address_from_target_address(target)
    if ssh_address:
        _validate_ssh_address(ssh_address)

    path = str(_join_repo_path(root, worktree))
    if _has_placeholder(path):
        raise RuntimeError(
            f"Target repo path still contains a placeholder: {path!r}. "
            f"Please check config before running."
        )
    return ssh_address, path


def _target_by_name(config: dict, target_name: str | None) -> dict:
    if isinstance(config.get("target"), dict):
        target = dict(config["target"])
        configured_address = target.get("address") or target.get("target_address") or "127.0.0.1"
        target["address"] = target_name or configured_address
        return target

    targets = config.get("targets") or {}
    if isinstance(targets, dict):
        if target_name is None and len(targets) == 1:
            return next(iter(targets.values()))
        if target_name is None:
            raise RuntimeError("target address is required when multiple targets are configured.")
        if target_name not in targets:
            raise RuntimeError(f"target not found: {target_name}")
        return targets[target_name]

    if isinstance(targets, list):
        if target_name is None and len(targets) == 1:
            return targets[0]
        if target_name is None:
            raise RuntimeError("target address is required when multiple targets are configured.")
        for target in targets:
            if target.get("target_name") == target_name:
                return target
        raise RuntimeError(f"target not found: {target_name}")

    raise RuntimeError("targets must be an object or a list.")


def _ssh_address_from_target_address(target: dict) -> str | None:
    address = target.get("address") or target.get("target_address")
    if not address or _is_local_address(address):
        return None

    ssh_user = target.get("ssh_user")
    if not ssh_user:
        raise RuntimeError(
            f"ssh_user is required for remote target.address: {address}"
        )
    return f"{ssh_user}@{address}"


def _is_local_address(value: str) -> bool:
    return value in {"local", "localhost", "127.0.0.1", "::1"} or value.startswith("127.")


def _join_repo_path(root: str, child: str) -> Path:
    root_path = Path(root).expanduser()
    child_text = str(child)
    child_path = Path(child_text).expanduser()
    if child_path.is_absolute():
        if str(child_path).startswith(str(root_path)):
            return child_path
        return root_path / child_text.lstrip("/")
    return root_path / child_path


def _rollback_pointer_path(repo_name: str) -> str:
    safe_name = repo_name.replace("/", "_").replace(":", "_")
    return f"{ROLLBACK_ROOT}/{safe_name}/latest_commit"


def _remote_git_worktree_root(repo: RepoConfig) -> str:
    return repo.remote_git_worktree_root or repo.worktree


def _local_source(config: dict) -> dict:
    if config.get("from"):
        source = config["from"]
        return {
            "name": source.get("address") or "local",
            "workspace_root": source.get("root") or source.get("workspace_root"),
        }

    source = config.get("local_source") or {}
    if "workspace_root" not in source and config.get("workspace_root"):
        source = {**source, "workspace_root": config["workspace_root"]}
    if "name" not in source:
        source = {**source, "name": "local"}
    return source


def _local_workspace_root(config: dict) -> str:
    source = _local_source(config)
    workspace_root = source.get("workspace_root")
    if not workspace_root:
        raise RuntimeError("from.root is missing in send config.")
    return workspace_root


def _has_placeholder(value: str) -> bool:
    return "<" in value or ">" in value


def _validate_local_workspace_root(workspace_root: str) -> None:
    if _has_placeholder(workspace_root):
        raise RuntimeError(
            f"from.root still contains a placeholder: {workspace_root!r}. "
            f"Please check send.json before sending."
        )

    root = Path(workspace_root).expanduser()
    if not root.is_absolute():
        raise RuntimeError(
            f"from.root must be an absolute path: {workspace_root!r}. "
            f"Please check send.json before sending."
        )
    if not root.exists():
        raise RuntimeError(
            f"from.root does not exist: {root}. Please check send.json before sending."
        )


def _validate_remote_workspace_root(workspace_root: str) -> None:
    if _has_placeholder(workspace_root):
        raise RuntimeError(
            f"target.root still contains a placeholder: {workspace_root!r}. "
            f"Please check send.json before sending."
        )

    root = Path(workspace_root)
    if not root.is_absolute():
        raise RuntimeError(
            f"target.root must be an absolute path: {workspace_root!r}. "
            f"Please check send.json before sending."
        )


def _validate_ssh_address(ssh_address: str) -> None:
    if _has_placeholder(ssh_address):
        raise RuntimeError(
            f"ssh_address still contains a placeholder: {ssh_address!r}. "
            f"Please check send.json before sending."
        )
    if "@" not in ssh_address:
        raise RuntimeError(
            "ssh_address must include an SSH username, for example user@10.0.0.2."
        )
    user, host = ssh_address.split("@", 1)
    if not user or not host:
        raise RuntimeError(
            "ssh_address must be written as user@host, for example user@10.0.0.2."
        )
    if ":" in ssh_address:
        raise RuntimeError(
            "ssh_address must not include a remote path. Put only user@host in send.json."
        )


def _is_dir_path(path: str) -> bool:
    return path.endswith("/")


def _is_whole_root_path(path: str) -> bool:
    return path in {".", "./"}


def _send_scope_label(send_include_paths: list[str]) -> str:
    if any(_is_whole_root_path(p) for p in send_include_paths):
        return "entire local_git_worktree_root -> remote_git_worktree_root"
    return f"partial repo send ({len(send_include_paths)} configured paths)"


def send_repo(
    config: dict,
    repo: RepoConfig,
    target_name: str | None,
    confirm: Callable[[], bool],
) -> None:
    try:
        target_config = _target_by_name(config, target_name)
    except RuntimeError as exc:
        raise RuntimeError(str(exc).replace("target not found", "send target not found")) from exc

    target_name = target_config.get("address") or target_config.get("target_address") or target_name or "127.0.0.1"
    local_source = _local_source(config)
    workspace_root = _local_workspace_root(config)

    src_root = _local_repo_root(workspace_root, repo.worktree)
    ssh_address, remote_repo_path = _remote_repo_parts(
        target_config,
        _remote_git_worktree_root(repo),
    )
    remote_git_path = remote_repo_path
    dst_root = f"{ssh_address}:{remote_repo_path}"
    ssh_options = _ssh_connection_reuse_options()

    if not src_root.exists():
        raise RuntimeError(f"source repo root not found: {src_root}")

    if not repo.send_include_paths:
        raise RuntimeError(
            f"repo {repo.name} has no send_include_paths configured. "
            f"Refusing to transfer entire project."
        )

    _validate_send_include_paths_before_preview(src_root, repo.send_include_paths)

    print("=" * 80)
    print(f"Repo: {repo.name}")
    print(f"Local source:    {local_source['name']}")
    print(f"Remote target:   {target_name}")
    print(f"Local git root:  {src_root}")
    print(f"Remote git root: {target_name}: {remote_git_path}")
    print(f"Remote address:  {dst_root}")
    print(f"Scope:           {_send_scope_label(repo.send_include_paths)}")
    print("Mode:            source paths only; .git history is excluded")
    print(f"Rollback ref:    {target_name}: {_rollback_pointer_path(repo.name)}")
    print("SSH reuse:       enabled for this command; password is not stored")
    print("=" * 80)

    commands = []
    validate_command = _build_validate_remote_git_worktree_command(
        ssh_address,
        remote_git_path,
        ssh_options=ssh_options,
    )
    pointer_command = _build_record_rollback_pointer_command(
        ssh_address,
        remote_git_path,
        repo.name,
        ssh_options=ssh_options,
    )
    history_command = _build_record_send_history_command(
        ssh_address,
        remote_git_path,
        repo.name,
        local_source.get("name") or "local",
        target_name,
        len(repo.send_include_paths),
        ssh_options=ssh_options,
    )
    print("\n# validate remote git worktree")
    print(" ".join(shlex.quote(x) for x in validate_command))

    print("\n# record remote rollback commit")
    print(" ".join(shlex.quote(x) for x in pointer_command))

    print("\nPlanned path updates:")
    for rel in repo.send_include_paths:
        commands.append(_build_one_path_command(src_root, dst_root, rel, ssh_options=ssh_options))

    print("\nConfirming this transfer will update the remote paths shown above.")
    print("Directory entries use rsync --delete inside that directory.")
    if not confirm():
        print("Source transfer cancelled.")
        return

    try:
        print("\nValidating remote git worktree.")
        run_live(validate_command)

        print("\nRecording remote rollback commit.")
        run_live(pointer_command)

        print("\nStarting source transfer.")
        for command in commands:
            run_live(command)

        print("\nRecording send history.")
        _run_history_command(history_command)
    finally:
        print("\nClosing reused SSH connection.")
        run_live(_build_close_ssh_connection_command(ssh_address, ssh_options), check=False)


def rollback_repo(
    config: dict,
    repo: RepoConfig,
    target_name: str | None,
    confirm: Callable[[], bool],
) -> None:
    try:
        target_config = _target_by_name(config, target_name)
    except RuntimeError as exc:
        raise RuntimeError(str(exc).replace("target not found", "rollback target not found")) from exc

    workspace_root = target_config.get("root") or target_config.get("workspace_root")
    if not workspace_root:
        raise RuntimeError("target.root is missing in rollback config.")

    target_name = target_config.get("address") or target_config.get("target_address") or target_name or "127.0.0.1"
    src_root = _join_repo_path(workspace_root, repo.worktree).resolve()
    ssh_address, remote_repo_path = _target_repo_parts(
        target_config,
        _remote_git_worktree_root(repo),
    )
    remote_git_path = remote_repo_path
    pointer = _rollback_pointer_path(repo.name)
    if ssh_address:
        ssh_options = _ssh_connection_reuse_options()
        validate_command = _build_validate_remote_git_worktree_command(
            ssh_address,
            remote_git_path,
            ssh_options=ssh_options,
        )
        command = _build_rollback_command(
            ssh_address,
            remote_git_path,
            repo.name,
            ssh_options=ssh_options,
        )
        history_command = _build_record_remote_rollback_history_command(
            ssh_address,
            remote_git_path,
            repo.name,
            target_name,
            ssh_options=ssh_options,
        )
        close_ssh_command = _build_close_ssh_connection_command(ssh_address, ssh_options)
        target_label = f"{target_name}: {remote_git_path}"
        pointer_label = f"{target_name}: {pointer}"
    else:
        validate_command = _build_validate_local_git_worktree_command(remote_git_path)
        command = _build_local_rollback_command(remote_git_path, repo.name)
        history_command = _build_record_local_rollback_history_command(
            remote_git_path,
            repo.name,
            target_name,
        )
        close_ssh_command = None
        target_label = remote_git_path
        pointer_label = str(Path(_rollback_pointer_path(repo.name)).expanduser())

    print("=" * 80)
    print(f"Repo: {repo.name}")
    print(f"Target:          {target_name}")
    print(f"Configured root: {workspace_root}")
    print(f"Object git root: {src_root}")
    print(f"Target git root: {target_label}")
    print(f"Commit ref:      {pointer_label}")
    print("Mode:            reset target worktree to recorded commit; no push")
    if ssh_address:
        print("SSH reuse:       enabled for this command; password is not stored")
    print("=" * 80)
    print("\n# validate target git worktree")
    print(" ".join(shlex.quote(x) for x in validate_command))

    print("\n# restore target rollback commit")
    print(" ".join(shlex.quote(x) for x in command))

    if not confirm():
        print("Rollback cancelled.")
        return

    try:
        print("\nValidating target git worktree.")
        run_live(validate_command)

        print("\nRestoring target rollback commit.")
        run_live(command)

        print("\nRecording rollback history.")
        _run_history_command(history_command)
    finally:
        if close_ssh_command:
            print("\nClosing reused SSH connection.")
            run_live(close_ssh_command, check=False)


def _run_history_command(command: list[str]) -> None:
    proc = run_live(command, check=False)
    if isinstance(proc.returncode, int) and proc.returncode != 0:
        print("History record failed; the operation had already completed.")


def _ssh_connection_reuse_options() -> list[str]:
    control_path = f"/tmp/qtgr_{uuid.uuid4().hex}.sock"
    return [
        "-o",
        "ControlMaster=auto",
        "-o",
        "ControlPersist=10m",
        "-o",
        f"ControlPath={control_path}",
    ]


def _build_close_ssh_connection_command(ssh_address: str, ssh_options: list[str]) -> list[str]:
    return ["ssh", *ssh_options, "-O", "exit", ssh_address]


def _build_validate_remote_git_worktree_command(
    ssh_address: str,
    remote_git_path: str,
    ssh_options: list[str] | None = None,
) -> list[str]:
    expected = shlex.quote(remote_git_path)
    script = (
        f"expected={expected} && "
        f"if [ ! -d \"$expected\" ]; then "
        f"echo \"target repo path does not exist: $expected\" >&2; "
        f"echo \"Create it and run git init before send/rollback.\" >&2; "
        f"exit 1; "
        f"fi && "
        f"if ! actual=$(git -C \"$expected\" rev-parse --show-toplevel 2>/dev/null); then "
        f"echo \"target repo path is not a Git worktree: $expected\" >&2; "
        f"echo \"Run on target: cd $expected && git init && git checkout -B master && git commit --allow-empty -m 'init'\" >&2; "
        f"exit 1; "
        f"fi && "
        f"if [ \"$actual\" != \"$expected\" ]; then "
        f"echo \"remote_git_worktree_root mismatch: expected $expected, got $actual\" >&2; "
        f"exit 1; "
        f"fi && "
        f"if ! git -C \"$expected\" rev-parse --verify HEAD >/dev/null 2>&1; then "
        f"echo \"target Git worktree has no commits: $expected\" >&2; "
        f"echo \"Run on target: cd $expected && git commit --allow-empty -m 'init'\" >&2; "
        f"exit 1; "
        f"fi"
    )
    return ["ssh", *(ssh_options or []), ssh_address, script]


def _build_validate_local_git_worktree_command(target_git_path: str) -> list[str]:
    expected = shlex.quote(target_git_path)
    script = (
        f"expected={expected} && "
        f"if [ ! -d \"$expected\" ]; then "
        f"echo \"target repo path does not exist: $expected\" >&2; "
        f"echo \"Create it and run git init before rollback.\" >&2; "
        f"exit 1; "
        f"fi && "
        f"if ! actual=$(git -C \"$expected\" rev-parse --show-toplevel 2>/dev/null); then "
        f"echo \"target repo path is not a Git worktree: $expected\" >&2; "
        f"echo \"Run: cd $expected && git init && git checkout -B master && git commit --allow-empty -m 'init'\" >&2; "
        f"exit 1; "
        f"fi && "
        f"if [ \"$actual\" != \"$expected\" ]; then "
        f"echo \"target_git_worktree_root mismatch: expected $expected, got $actual\" >&2; "
        f"exit 1; "
        f"fi && "
        f"if ! git -C \"$expected\" rev-parse --verify HEAD >/dev/null 2>&1; then "
        f"echo \"target Git worktree has no commits: $expected\" >&2; "
        f"echo \"Run: cd $expected && git commit --allow-empty -m 'init'\" >&2; "
        f"exit 1; "
        f"fi"
    )
    return ["sh", "-c", script]


def _build_record_rollback_pointer_command(
    ssh_address: str,
    remote_git_path: str,
    repo_name: str,
    ssh_options: list[str] | None = None,
) -> list[str]:
    pointer = _rollback_pointer_path(repo_name)
    parent = pointer.rsplit("/", 1)[0]
    script = (
        f"mkdir -p {shlex.quote(parent)} && "
        f"git -C {shlex.quote(remote_git_path)} rev-parse --verify HEAD "
        f"> {shlex.quote(pointer)}"
    )
    return ["ssh", *(ssh_options or []), ssh_address, script]


def _build_record_send_history_command(
    ssh_address: str,
    remote_git_path: str,
    repo_name: str,
    from_address: str,
    target_address: str,
    paths_count: int,
    ssh_options: list[str] | None = None,
) -> list[str]:
    pointer = _rollback_pointer_path(repo_name)
    record = history_record(
        "send",
        repo_name,
        from_address=from_address,
        target_address=target_address,
        target_root=remote_git_path,
        rollback_commit="__ROLLBACK_COMMIT__",
        paths_count=paths_count,
        result="success",
    )
    script = (
        f"rollback_commit=$(cat {shlex.quote(pointer)}) && "
        + build_append_remote_history_script_with_shell_fields(
            remote_git_path,
            record,
            {"rollback_commit": "rollback_commit"},
        )
    )
    return ["ssh", *(ssh_options or []), ssh_address, script]


def _build_rollback_command(
    ssh_address: str,
    remote_git_path: str,
    repo_name: str,
    ssh_options: list[str] | None = None,
) -> list[str]:
    pointer = _rollback_pointer_path(repo_name)
    script = (
        f"test -s {shlex.quote(pointer)} && "
        f"commit=$(cat {shlex.quote(pointer)}) && "
        f"git -C {shlex.quote(remote_git_path)} cat-file -e \"$commit^{{commit}}\" && "
        f"git -C {shlex.quote(remote_git_path)} reset --hard \"$commit\" && "
        f"git -C {shlex.quote(remote_git_path)} clean -fd"
    )
    return ["ssh", *(ssh_options or []), ssh_address, script]


def _build_record_remote_rollback_history_command(
    ssh_address: str,
    remote_git_path: str,
    repo_name: str,
    target_address: str,
    ssh_options: list[str] | None = None,
) -> list[str]:
    pointer = _rollback_pointer_path(repo_name)
    record = history_record(
        "rollback",
        repo_name,
        target_address=target_address,
        target_root=remote_git_path,
        restored_commit="__RESTORED_COMMIT__",
        result="success",
    )
    script = (
        f"restored_commit=$(cat {shlex.quote(pointer)}) && "
        + build_append_remote_history_script_with_shell_fields(
            remote_git_path,
            record,
            {"restored_commit": "restored_commit"},
        )
    )
    return ["ssh", *(ssh_options or []), ssh_address, script]


def _build_local_rollback_command(
    target_git_path: str,
    repo_name: str,
) -> list[str]:
    pointer = str(Path(_rollback_pointer_path(repo_name)).expanduser())
    script = (
        f"test -s {shlex.quote(pointer)} && "
        f"commit=$(cat {shlex.quote(pointer)}) && "
        f"git -C {shlex.quote(target_git_path)} cat-file -e \"$commit^{{commit}}\" && "
        f"git -C {shlex.quote(target_git_path)} reset --hard \"$commit\" && "
        f"git -C {shlex.quote(target_git_path)} clean -fd"
    )
    return ["sh", "-c", script]


def _build_record_local_rollback_history_command(
    target_git_path: str,
    repo_name: str,
    target_address: str,
) -> list[str]:
    pointer = str(Path(_rollback_pointer_path(repo_name)).expanduser())
    record = history_record(
        "rollback",
        repo_name,
        target_address=target_address,
        target_root=target_git_path,
        restored_commit="__RESTORED_COMMIT__",
        result="success",
    )
    script = (
        f"restored_commit=$(cat {shlex.quote(pointer)}) && "
        + build_append_remote_history_script_with_shell_fields(
            target_git_path,
            record,
            {"restored_commit": "restored_commit"},
        )
    )
    return ["sh", "-c", script]


def _build_one_path_command(
    src_root: Path,
    dst_root: str,
    rel: str,
    ssh_options: list[str] | None = None,
) -> list[str]:
    _validate_send_include_path(rel)
    src = src_root / rel

    if not src.exists():
        raise RuntimeError(f"send_include_paths source path does not exist: {rel!r} ({src})")

    _validate_send_include_path_type(src, rel)

    base_cmd = ["rsync", "-avz"]

    if ssh_options:
        base_cmd.extend(["-e", _rsync_ssh_command(ssh_options)])

    for item in DEFAULT_EXCLUDES:
        base_cmd.extend(["--exclude", item])

    if _is_whole_root_path(rel):
        base_cmd.append("--delete")

        src_arg = str(src_root).rstrip("/") + "/"
        dst_arg = dst_root.rstrip("/") + "/"
    elif _is_dir_path(rel):
        base_cmd.append("--delete")

        src_arg = str(src).rstrip("/") + "/"
        dst_arg = dst_root.rstrip("/") + "/" + rel.rstrip("/") + "/"
    else:
        src_arg = str(src)
        dst_arg = dst_root.rstrip("/") + "/" + rel

    cmd = base_cmd + [src_arg, dst_arg]

    safe_cmd = " ".join(shlex.quote(x) for x in cmd)
    print(f"\n# {rel}")
    print(f"  local source:  {src_arg}")
    print(f"  remote target: {dst_arg}")
    print(f"  update mode:   {'directory mirror (--delete)' if '--delete' in base_cmd else 'single file overwrite'}")
    print(safe_cmd)

    return cmd


def _rsync_ssh_command(ssh_options: list[str]) -> str:
    return "ssh " + " ".join(shlex.quote(option) for option in ssh_options)


def _validate_send_include_paths_before_preview(src_root: Path, paths: list[str]) -> None:
    errors: list[str] = []
    for rel in paths:
        try:
            _validate_send_include_path(rel)
            src = src_root / rel
            if not src.exists():
                raise RuntimeError(
                    f"source path does not exist: {rel!r} ({src})"
                )
            _validate_send_include_path_type(src, rel)
        except RuntimeError as exc:
            errors.append(f"- {exc}")

    if errors:
        joined = "\n".join(errors)
        raise RuntimeError(
            "send_include_paths preflight failed. Fix send.json before sending:\n"
            f"{joined}"
        )


def _validate_send_include_path(rel: str) -> None:
    path = Path(rel)
    if not rel or path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"Invalid send include path: {rel!r}")

    protected = PROTECTED_PATH_PARTS.intersection(path.parts)
    if protected:
        joined = ", ".join(sorted(protected))
        raise RuntimeError(f"Protected path may not be sent: {rel!r} ({joined})")


def _validate_send_include_path_type(src: Path, rel: str) -> None:
    if src.is_dir() and not _is_dir_path(rel) and not _is_whole_root_path(rel):
        raise RuntimeError(
            f"send_include_paths entry points to a directory but is missing trailing '/': {rel!r}. "
            f"Write it as {rel + '/'!r} to make directory mirror semantics explicit."
        )
    if _is_dir_path(rel) and not src.is_dir():
        raise RuntimeError(
            f"send_include_paths entry has trailing '/' but source is not a directory: {rel!r}."
        )
