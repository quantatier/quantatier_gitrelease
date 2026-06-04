from __future__ import annotations

import os
import sys
import shlex
from contextlib import contextmanager
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .git_ops import (
    commit_if_needed,
    create_and_push_tag,
    ensure_not_behind_or_diverged,
    ensure_repo_valid,
    ensure_tag_not_exists,
    is_git_worktree,
    origin_url,
    push_branch,
)
from .history import append_local_history, history_record
from .models import ResolvedRepo
from .runner import output, run_live
from .runner import run
from .transfer import _build_close_ssh_connection_command, _ssh_connection_reuse_options
from .versioning import read_pyproject_version, version_to_tag


def release_repo(
    repo: ResolvedRepo,
    message: str | None = None,
    confirm: Callable[[], bool] | None = None,
) -> None:
    if repo.ssh_address:
        release_repo_remote(repo, message=message, confirm=confirm)
        return

    version = read_pyproject_version(repo.worktree)
    tag = version_to_tag(version)
    release_message = message or f"release {repo.name} {tag}"

    print("=" * 80)
    print(f"Release repo:      {repo.name}")
    print(f"Local git root:    {repo.worktree}")
    print(f"Branch:            {repo.branch}")
    print(f"Bare repo target:  {repo.bare}")
    print("Scope:             entire Git repo; partial release is not allowed")
    print(f"Version:           {version}")
    print(f"Tag:               {tag}")
    print(f"Commit message:    {release_message}")
    ssh_reuse = _git_ssh_reuse_for_bare(repo.bare)
    if ssh_reuse:
        print("Git SSH reuse:     enabled for remote bare repo; password is not stored")
    print("=" * 80)
    sys.stdout.flush()

    with _temporary_git_ssh_reuse(ssh_reuse):
        ensure_repo_valid(repo)
        ensure_not_behind_or_diverged(repo)

        if confirm is not None and not confirm():
            print("Release cancelled.")
            return

        ensure_tag_not_exists(repo, tag)
        commit_if_needed(repo, release_message)
        push_branch(repo)
        create_and_push_tag(repo, tag, release_message)
        try:
            append_local_history(
                repo.worktree,
                history_record(
                    "release",
                    repo.name,
                    target_address=repo.target_name,
                    target_root=repo.bare,
                    branch=repo.branch,
                    commit=output(["git", "rev-parse", "HEAD"], cwd=repo.worktree),
                    tag=tag,
                    result="success",
                ),
            )
        except OSError:
            print("History record failed; the release had already completed.")

    print(f"Release complete: {repo.name} {tag}")


def prepare_github_release_repo(
    repo: ResolvedRepo,
    confirm: Callable[[str], bool],
    prompt: Callable[[str], str],
) -> None:
    if not _is_github_bare(repo.bare):
        return

    print("=" * 80)
    print(f"GitHub release setup: {repo.name}")
    print(f"Local git root:       {repo.worktree}")
    print(f"GitHub target:        {repo.bare}")
    print(f"Branch:               {repo.branch}")
    print("This setup may initialize .git, set origin, and set local Git identity.")
    print("SSH private keys and GitHub passwords are never stored in this project.")
    print("=" * 80)

    if not repo.worktree.exists():
        raise RuntimeError(f"worktree not found: {repo.worktree}")

    if not is_git_worktree(repo.worktree):
        if not confirm(f"Initialize Git repo at {repo.worktree}?"):
            raise RuntimeError("GitHub release setup cancelled.")
        run_live(["git", "init", "-b", repo.branch], cwd=repo.worktree)

    current_origin = origin_url(repo.worktree)
    if not current_origin:
        if not confirm(f"Set origin to {repo.bare}?"):
            raise RuntimeError("GitHub release setup cancelled.")
        run_live(["git", "remote", "add", "origin", repo.bare], cwd=repo.worktree)
    elif current_origin != repo.bare:
        if not confirm(f"Replace origin {current_origin} with {repo.bare}?"):
            raise RuntimeError("GitHub release setup cancelled.")
        run_live(["git", "remote", "set-url", "origin", repo.bare], cwd=repo.worktree)

    _ensure_local_git_identity(repo.worktree, prompt)
    _ensure_github_ssh_ready(prompt, confirm)
    _bootstrap_existing_github_history(repo)


def _is_github_bare(value: str) -> bool:
    return value.startswith("git@github.com:")


def _ensure_local_git_identity(worktree: Path, prompt: Callable[[str], str]) -> None:
    name = output(["git", "config", "--get", "user.name"], cwd=worktree, check=False)
    email = output(["git", "config", "--get", "user.email"], cwd=worktree, check=False)

    if not name:
        name = prompt("Git user.name for this repo")
        run_live(["git", "config", "user.name", name], cwd=worktree)

    if not email:
        email = prompt("Git user.email for this repo")
        run_live(["git", "config", "user.email", email], cwd=worktree)


def _ensure_github_ssh_ready(
    prompt: Callable[[str], str],
    confirm: Callable[[str], bool],
) -> None:
    while True:
        proc = run(["ssh", "-T", "git@github.com"], check=False)
        text = f"{proc.stdout or ''}\n{proc.stderr or ''}"
        if "successfully authenticated" in text:
            print("GitHub SSH auth is ready.")
            return

        key_path = _ensure_github_public_key(prompt, confirm)
        _print_github_key_setup_help(key_path)

        answer = prompt(
            "Add the public key to GitHub, then press Enter to recheck "
            "(or type q to cancel)"
        )
        if answer.strip().lower() in {"q", "quit", "cancel", "n", "no"}:
            raise RuntimeError("GitHub SSH setup cancelled.")


def _ensure_github_public_key(
    prompt: Callable[[str], str],
    confirm: Callable[[str], bool],
) -> Path:
    pubkeys = _github_public_keys()
    if not pubkeys:
        if not confirm("No SSH public key found. Generate one for GitHub now?"):
            raise RuntimeError("GitHub SSH key is not configured.")
        email = prompt("Email/comment for SSH key")
        run_live(["ssh-keygen", "-t", "ed25519", "-C", email])
        pubkeys = _github_public_keys()

    if not pubkeys:
        raise RuntimeError("No SSH public key was found after ssh-keygen.")

    preferred = Path.home() / ".ssh" / "id_ed25519.pub"
    if preferred in pubkeys:
        return preferred
    return pubkeys[0]


def _github_public_keys() -> list[Path]:
    ssh_dir = Path.home() / ".ssh"
    if not ssh_dir.exists():
        return []
    return sorted(ssh_dir.glob("*.pub"))


def _print_github_key_setup_help(key_path: Path) -> None:
    print("\nGitHub SSH auth is not ready yet.")
    print("Add this public key to GitHub:")
    print("  GitHub -> Settings -> SSH and GPG keys -> New SSH key")
    print("  Title: any label that helps you recognize this machine, e.g. Work Laptop")
    print("  Key type: Authentication Key")
    print("  Key: paste the full public key below, including ssh-ed25519 and the comment")
    print(f"\n# {key_path}")
    print(key_path.read_text(encoding="utf-8").strip())
    print("\nThe private key stays in ~/.ssh and is never stored in this project.")


def _bootstrap_existing_github_history(repo: ResolvedRepo) -> None:
    has_head = run(["git", "rev-parse", "--verify", "HEAD"], cwd=repo.worktree, check=False)
    run_live(["git", "fetch", "origin", "--tags", "--prune"], cwd=repo.worktree)

    if has_head.returncode == 0:
        return

    remote_branch = f"origin/{repo.branch}"
    remote_exists = run(
        ["git", "rev-parse", "--verify", remote_branch],
        cwd=repo.worktree,
        check=False,
    )
    if remote_exists.returncode == 0:
        run_live(["git", "checkout", "-B", repo.branch, remote_branch], cwd=repo.worktree)
    else:
        run_live(["git", "checkout", "-B", repo.branch], cwd=repo.worktree)


def release_repo_remote(
    repo: ResolvedRepo,
    message: str | None = None,
    confirm: Callable[[], bool] | None = None,
) -> None:
    release_message = message or f"release {repo.name}"
    command = _build_remote_release_command(repo, release_message)

    print("=" * 80)
    print(f"Release repo:      {repo.name}")
    print(f"Bare repo address: {repo.target_name}")
    print(f"SSH address:       {repo.ssh_address}")
    print(f"Remote git root:   {repo.worktree}")
    print(f"Branch:            {repo.branch}")
    print(f"Bare repo target:  {repo.bare}")
    print("Scope:             entire Git repo; partial release is not allowed")
    print("Version:           read from remote pyproject.toml during release")
    print("Tag:               generated as v{remote version}")
    print(f"Commit message:    {release_message}")
    print("=" * 80)
    print("\n# remote release command")
    print(" ".join(shlex.quote(x) for x in command))
    sys.stdout.flush()

    if confirm is not None and not confirm():
        print("Release cancelled.")
        return

    run_live(command)
    print(f"Release complete on {repo.target_name}: {repo.name}")


def _build_remote_release_command(repo: ResolvedRepo, message: str) -> list[str]:
    if not repo.ssh_address:
        raise RuntimeError("ssh_address is required for remote release.")

    worktree = shlex.quote(str(repo.worktree))
    bare = shlex.quote(repo.bare)
    branch = shlex.quote(repo.branch)
    message_q = shlex.quote(message)
    repo_name = shlex.quote(repo.name)
    target_name = shlex.quote(repo.target_name)
    script = (
        "set -e; "
        f"worktree={worktree}; "
        f"bare={bare}; "
        f"branch={branch}; "
        f"message={message_q}; "
        f"repo_name={repo_name}; "
        f"target_name={target_name}; "
        "actual=$(git -C \"$worktree\" rev-parse --show-toplevel); "
        "if [ \"$actual\" != \"$worktree\" ]; then "
        "echo \"release worktree mismatch: expected $worktree, got $actual\" >&2; exit 1; "
        "fi; "
        "origin=$(git -C \"$worktree\" remote get-url origin); "
        "if [ \"$origin\" != \"$bare\" ]; then "
        "echo \"origin mismatch: expected $bare, got $origin\" >&2; exit 1; "
        "fi; "
        "current_branch=$(git -C \"$worktree\" branch --show-current); "
        "if [ \"$current_branch\" != \"$branch\" ]; then "
        "echo \"branch mismatch: expected $branch, got $current_branch\" >&2; exit 1; "
        "fi; "
        "git -C \"$worktree\" fetch origin --tags --prune; "
        "counts=$(git -C \"$worktree\" rev-list --left-right --count HEAD...origin/\"$branch\" || echo '0 0'); "
        "ahead=${counts%%[[:space:]]*}; behind=${counts##*[[:space:]]}; "
        "if [ \"$behind\" != \"0\" ]; then "
        "echo \"remote worktree is behind or diverged from origin/$branch\" >&2; exit 1; "
        "fi; "
        "version=$(python3 -c \"import importlib,pathlib,sys; "
        "toml=importlib.import_module('tomllib' if sys.version_info >= (3,11) else 'tomli'); "
        "p=pathlib.Path('$worktree')/'pyproject.toml'; "
        "print(toml.loads(p.read_text())['project']['version'])\"); "
        "case \"$version\" in v*|V*) echo \"pyproject version must not start with v/V\" >&2; exit 1;; esac; "
        "tag=v$version; "
        "if git -C \"$worktree\" rev-parse -q --verify refs/tags/\"$tag\" >/dev/null; then "
        "echo \"local tag already exists: $tag\" >&2; exit 1; "
        "fi; "
        "if git -C \"$worktree\" ls-remote --tags origin refs/tags/\"$tag\" | grep -q .; then "
        "echo \"remote tag already exists: $tag\" >&2; exit 1; "
        "fi; "
        "git -C \"$worktree\" add -A; "
        "if git -C \"$worktree\" diff --cached --quiet; then "
        "echo \"no staged changes, skip commit\"; "
        "else git -C \"$worktree\" commit -m \"$message\"; fi; "
        "git -C \"$worktree\" push -u origin \"$branch\"; "
        "git -C \"$worktree\" tag -a \"$tag\" -m \"$message\"; "
        "git -C \"$worktree\" push origin \"$tag\"; "
        "{ "
        "commit=$(git -C \"$worktree\" rev-parse HEAD); "
        "history_dir=\"$worktree/.qtcli_gitrelease\"; "
        "mkdir -p \"$history_dir\"; "
        "time_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ); "
        "printf '{\"action\":\"release\",\"branch\":\"%s\",\"commit\":\"%s\",\"repo_name\":\"%s\",\"result\":\"success\",\"tag\":\"%s\",\"target_address\":\"%s\",\"target_root\":\"%s\",\"time\":\"%s\"}\\n' "
        "\"$branch\" \"$commit\" \"$repo_name\" \"$tag\" \"$target_name\" \"$bare\" \"$time_utc\" "
        ">> \"$history_dir/history.jsonl\"; "
        "} || echo \"History record failed; the release had already completed.\" >&2"
    )
    return ["ssh", repo.ssh_address, script]


def _git_ssh_reuse_for_bare(bare: str) -> tuple[str, list[str]] | None:
    parsed = _parse_git_ssh_address(bare)
    if not parsed:
        return None

    ssh_address, port = parsed
    ssh_options = _ssh_connection_reuse_options()
    if port:
        ssh_options.extend(["-p", port])
    return ssh_address, ssh_options


def _parse_git_ssh_address(remote: str) -> tuple[str, str | None] | None:
    if remote.startswith("ssh://"):
        parsed = urlparse(remote)
        if not parsed.hostname:
            return None
        ssh_address = parsed.hostname
        if parsed.username:
            ssh_address = f"{parsed.username}@{ssh_address}"
        return ssh_address, str(parsed.port) if parsed.port else None

    if remote.startswith(("http://", "https://")):
        return None

    left, sep, _right = remote.partition(":")
    if sep and "@" in left:
        return left, None

    return None


@contextmanager
def _temporary_git_ssh_reuse(ssh_reuse: tuple[str, list[str]] | None):
    if not ssh_reuse:
        yield
        return

    ssh_address, ssh_options = ssh_reuse
    old_value = os.environ.get("GIT_SSH_COMMAND")
    os.environ["GIT_SSH_COMMAND"] = _git_ssh_command(ssh_options)
    try:
        yield
    finally:
        if old_value is None:
            os.environ.pop("GIT_SSH_COMMAND", None)
        else:
            os.environ["GIT_SSH_COMMAND"] = old_value
        print("\nClosing reused Git SSH connection.")
        run_live(_build_close_ssh_connection_command(ssh_address, ssh_options), check=False)


def _git_ssh_command(ssh_options: list[str]) -> str:
    return "ssh " + " ".join(shlex.quote(option) for option in ssh_options)
