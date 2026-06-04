from __future__ import annotations

from pathlib import Path

from .models import RepoStatus, ResolvedRepo
from .runner import run, run_live
from .versioning import read_pyproject_version, version_to_tag


def is_git_worktree(path: Path) -> bool:
    return (path / ".git").exists()


def origin_url(path: Path) -> str | None:
    proc = run(["git", "remote", "get-url", "origin"], cwd=path, check=False)
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def current_branch(path: Path) -> str | None:
    proc = run(["git", "branch", "--show-current"], cwd=path, check=False)
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def is_dirty(path: Path) -> bool:
    proc = run(["git", "status", "--porcelain"], cwd=path, check=False)
    return bool((proc.stdout or "").strip())


def fetch_origin(path: Path) -> None:
    run_live(["git", "fetch", "origin", "--tags", "--prune"], cwd=path)


def ahead_behind(path: Path, branch: str) -> tuple[int, int]:
    proc = run(
        ["git", "rev-list", "--left-right", "--count", f"HEAD...origin/{branch}"],
        cwd=path,
        check=False,
    )
    if proc.returncode != 0:
        return 0, 0

    raw = (proc.stdout or "").strip()
    if not raw:
        return 0, 0

    parts = raw.split()
    if len(parts) != 2:
        return 0, 0

    return int(parts[0]), int(parts[1])


def local_tag_exists(path: Path, tag: str) -> bool:
    proc = run(
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/{tag}"],
        cwd=path,
        check=False,
    )
    return proc.returncode == 0


def remote_tag_exists(path: Path, tag: str) -> bool:
    proc = run(
        ["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}"],
        cwd=path,
        check=False,
    )
    return bool((proc.stdout or "").strip())


def _bare_exists(bare: str) -> bool:
    if bare.startswith(("git@", "ssh://", "https://", "http://")):
        return True
    return Path(bare).exists()


def validate_repo(repo: ResolvedRepo, check_remote_state: bool = True) -> RepoStatus:
    errors: list[str] = []

    branch = None
    origin = None
    dirty = False
    ahead = None
    behind = None
    version = None
    tag = None
    local_tag = None
    remote_tag = None

    if not repo.worktree.exists():
        errors.append(f"worktree not found: {repo.worktree}")
        return RepoStatus(repo.name, False, str(repo.worktree), repo.bare, branch, origin, dirty, ahead, behind, version, tag, local_tag, remote_tag, errors)

    if not is_git_worktree(repo.worktree):
        errors.append(f"not a Git worktree: {repo.worktree}")
        return RepoStatus(repo.name, False, str(repo.worktree), repo.bare, branch, origin, dirty, ahead, behind, version, tag, local_tag, remote_tag, errors)

    if not _bare_exists(repo.bare):
        errors.append(f"bare repo not found or unreachable: {repo.bare}")

    origin = origin_url(repo.worktree)
    if not origin:
        errors.append("origin is not configured")
    elif origin != repo.bare:
        errors.append(f"origin mismatch: expected {repo.bare}, actual {origin}")

    branch = current_branch(repo.worktree)
    if not branch:
        errors.append("detached HEAD")
    elif branch != repo.branch:
        errors.append(f"branch mismatch: expected {repo.branch}, actual {branch}")

    dirty = is_dirty(repo.worktree)

    if check_remote_state and origin and branch:
        proc = run(["git", "fetch", "origin", "--tags", "--prune"], cwd=repo.worktree, check=False)
        if proc.returncode == 0:
            ahead, behind = ahead_behind(repo.worktree, repo.branch)

    try:
        version = read_pyproject_version(repo.worktree)
        tag = version_to_tag(version)
        local_tag = local_tag_exists(repo.worktree, tag)
        remote_tag = remote_tag_exists(repo.worktree, tag) if origin else None
    except Exception:
        version = None
        tag = None
        local_tag = None
        remote_tag = None

    return RepoStatus(
        name=repo.name,
        ok=len(errors) == 0,
        worktree=str(repo.worktree),
        bare=repo.bare,
        branch=branch,
        origin=origin,
        dirty=dirty,
        ahead=ahead,
        behind=behind,
        version=version,
        tag=tag,
        local_tag_exists=local_tag,
        remote_tag_exists=remote_tag,
        errors=errors,
    )


def ensure_repo_valid(repo: ResolvedRepo) -> None:
    status = validate_repo(repo)
    if not status.ok:
        joined = "\n".join(f"- {e}" for e in status.errors)
        raise RuntimeError(f"Repo validation failed for {repo.name}:\n{joined}")


def ensure_not_behind_or_diverged(repo: ResolvedRepo) -> None:
    fetch_origin(repo.worktree)
    ahead, behind = ahead_behind(repo.worktree, repo.branch)

    if behind > 0 and ahead == 0:
        raise RuntimeError(
            f"{repo.name} is behind origin/{repo.branch} by {behind} commit(s).\n\n"
            f"Run on main machine:\n"
            f"  cd {repo.worktree}\n"
            f"  git pull --rebase origin {repo.branch}\n\n"
            f"Then retry."
        )

    if ahead > 0 and behind > 0:
        raise RuntimeError(
            f"{repo.name} has diverged from origin/{repo.branch}.\n\n"
            f"Local ahead: {ahead}\n"
            f"Remote ahead: {behind}\n\n"
            f"Resolve manually:\n"
            f"  cd {repo.worktree}\n"
            f"  git pull --rebase origin {repo.branch}\n\n"
            f"If conflicts occur, resolve them and run:\n"
            f"  git rebase --continue"
        )


def commit_if_needed(repo: ResolvedRepo, message: str) -> bool:
    run_live(["git", "add", "-A"], cwd=repo.worktree)

    staged = run(["git", "diff", "--cached", "--quiet"], cwd=repo.worktree, check=False)
    if staged.returncode == 0:
        print(f"[{repo.name}] no staged changes, skip commit")
        return False

    run_live(["git", "commit", "-m", message], cwd=repo.worktree)
    return True


def push_branch(repo: ResolvedRepo) -> None:
    run_live(["git", "push", "-u", "origin", repo.branch], cwd=repo.worktree)


def ensure_tag_not_exists(repo: ResolvedRepo, tag: str) -> None:
    if local_tag_exists(repo.worktree, tag):
        raise RuntimeError(
            f"Local tag already exists: {tag}\n"
            f"This version appears to have already been released locally.\n"
            f"Update pyproject.toml version and retry."
        )

    if remote_tag_exists(repo.worktree, tag):
        raise RuntimeError(
            f"Remote tag already exists: {tag}\n"
            f"This version appears to have already been released to origin.\n"
            f"Update pyproject.toml version and retry."
        )


def create_and_push_tag(repo: ResolvedRepo, tag: str, message: str) -> None:
    ensure_tag_not_exists(repo, tag)
    run_live(["git", "tag", "-a", tag, "-m", message], cwd=repo.worktree)
    run_live(["git", "push", "origin", tag], cwd=repo.worktree)
