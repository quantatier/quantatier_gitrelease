from __future__ import annotations

import shlex
from typing import Optional

import click
import typer

from .config import (
    ensure_release_host,
    list_repos,
    load_addresses_config,
    load_release_config_for_target,
    load_release_config,
    load_rollback_config,
    load_send_config,
    resolve_release_repo,
)
from .release import prepare_github_release_repo, release_repo
from .runner import run_live
from .transfer import rollback_repo, send_repo

def _address_hint() -> str:
    try:
        addresses = load_addresses_config()
    except RuntimeError:
        addresses = {}
    if not addresses:
        return "Address aliases: none configured."
    items = ", ".join(f"{name}={address}" for name, address in sorted(addresses.items()))
    return f"Address aliases: {items}"


ADDRESS_HINT = _address_hint()

app = typer.Typer(
    help=(
        "qtcli_gitrelease: source send and Git release CLI\n\n"
        f"{ADDRESS_HINT}"
    )
)


def _confirm(prompt: str) -> bool:
    try:
        return typer.confirm(prompt)
    except (click.Abort, EOFError):
        return False


def _resolve_release_repos(
    config: dict,
    repo: str | None,
):
    ensure_release_host(config)
    raw_repos = list_repos(config, repo_name=repo)
    return [resolve_release_repo(config, r) for r in raw_repos]


@app.command(
    help=(
        "Send selected source paths to a remote target.\n\n"
        "Syntax: qtcli_gitrelease send --repo <repo> on <executor> to <address>\n\n"
        "This command does not send .git and does not commit or push.\n\n"
        f"{ADDRESS_HINT}"
    )
)
def send(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to send.json"),
    repo: str = typer.Option(..., "--repo", help="Repo name"),
    target: list[str] = typer.Argument(..., metavar="on ADDRESS to ADDRESS"),
):
    """
    Send selected source paths to a remote target.

    This command does not send .git and does not commit or push.
    """
    cfg = load_send_config(config)
    executor_address, target_name = _parse_on_to_clause(
        target,
        command_name="send",
    )
    if not _is_local_address(executor_address):
        _run_send_on_remote_executor(cfg, repo, executor_address, target_name)
        return

    _set_endpoint_address(cfg, "target", target_name)

    repos = list_repos(cfg, repo_name=repo, require_bare=False)
    if len(repos) != 1:
        raise typer.BadParameter(f"Expected exactly one repo, got {len(repos)}")

    try:
        send_repo(
            cfg,
            repos[0],
            target_name=target_name,
            confirm=lambda: _confirm("Continue with source transfer?"),
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


@app.command(
    help=(
        "Ask a target to reset its worktree to the recorded rollback commit.\n\n"
        "Syntax: qtcli_gitrelease rollback --repo <repo> on <address>\n\n"
        "It does not push and does not update the bare repo.\n\n"
        f"{ADDRESS_HINT}"
    )
)
def rollback(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to rollback.json"),
    repo: str = typer.Option(..., "--repo", help="Repo name"),
    target: list[str] = typer.Argument(..., metavar="on ADDRESS"),
):
    """
    Ask a remote target to reset its worktree to the recorded rollback commit.

    This command runs on the current machine but performs the rollback on
    the configured target address, or on ADDRESS when provided.
    It does not push and does not update the bare repo.
    """
    cfg = load_rollback_config(config)
    target_name = _parse_required_target_clause(
        target,
        keyword="on",
        command_name="rollback",
    )

    _set_endpoint_address(cfg, "target", target_name)

    repos = list_repos(cfg, repo_name=repo, require_bare=False)
    if len(repos) != 1:
        raise typer.BadParameter(f"Expected exactly one repo, got {len(repos)}")

    try:
        rollback_repo(
            cfg,
            repos[0],
            target_name=target_name,
            confirm=lambda: _confirm("Reset target worktree to recorded commit?"),
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


def _parse_required_target_clause(
    target: list[str],
    keyword: str,
    command_name: str,
) -> str:
    if len(target) != 2 or target[0] != keyword:
        raise typer.BadParameter(
            f"Expected syntax: {command_name} --repo <repo> {keyword} <address>"
        )
    return _resolve_address_alias(target[1])


def _resolve_address_alias(value: str) -> str:
    addresses = load_addresses_config()
    return addresses.get(value, value)


def _set_endpoint_address(config: dict, key: str, address: str) -> None:
    endpoint = dict(config.get(key) or {})
    endpoint["address"] = address
    config[key] = endpoint


@app.command(
    help=(
        "Run add -> commit -> push -> tag from pyproject version -> push tag.\n\n"
        "Syntax: qtcli_gitrelease release --repo <repo> on <address> to <address>\n\n"
        "Local executor to configured bare repo:\n"
        "  qtcli_gitrelease release --repo <repo> on local to local -m \"release <repo>\"\n\n"
        "Local executor to GitHub:\n"
        "  qtcli_gitrelease release --repo <repo> on local to github -m \"release <repo>\"\n"
        "  GitHub release reads config/release_github.json.\n"
        "  Set target.github_username before using it.\n"
        "  The local project must already be a Git repo.\n"
        "  Its origin must match the GitHub target, for example:\n"
        "    git@github.com:<github_username>/<github_repo>.git\n"
        "  GitHub SSH auth must work before release continues: ssh -T git@github.com\n"
        "  If GitHub already has an initial commit, merge or pull it before release.\n\n"
        "  If any of those are missing, release runs an interactive setup helper.\n"
        "  It may initialize .git, set origin, set local Git identity, generate a key,\n"
        "  print setup instructions, and recheck GitHub SSH auth in a loop.\n\n"
        "Remote executor to GitHub:\n"
        "  qtcli_gitrelease release --repo <repo> on main_devbox to github -m \"release <repo>\"\n"
        "  This SSHes to the executor and uses that machine's source tree and config.\n\n"
        "pyproject.toml version must not start with v/V.\n"
        "Git tag is generated as v{version}.\n\n"
        f"{ADDRESS_HINT}"
    )
)
def release(
    config: Optional[str] = typer.Option(None, "--config", "-c", help="Path to release.json"),
    repo: str = typer.Option(..., "--repo", help="Repo name"),
    route: list[str] = typer.Argument(..., metavar="on ADDRESS to ADDRESS"),
    message: Optional[str] = typer.Option(None, "--message", "-m", help="Release commit and tag message"),
):
    """
    Run add -> commit -> push -> tag from pyproject version -> push tag.

    pyproject.toml version must not start with v/V.
    Git tag is generated as v{version}.
    """
    executor_address, target_address = _parse_on_to_clause(
        route,
        command_name="release",
    )

    if not _is_local_address(executor_address):
        control_cfg = load_release_config(config)
        _run_release_on_remote_executor(control_cfg, repo, executor_address, target_address, message)
        return

    cfg = load_release_config_for_target(config, target_address)
    _set_endpoint_address(cfg, "from", "127.0.0.1")
    _set_endpoint_address(cfg, "target", target_address)
    repos = _resolve_release_repos(cfg, repo)

    if len(repos) != 1:
        raise typer.BadParameter(f"Expected exactly one repo, got {len(repos)}")

    try:
        if target_address == "github":
            prepare_github_release_repo(
                repos[0],
                confirm=_confirm,
                prompt=lambda text: typer.prompt(text),
            )
        release_repo(
            repos[0],
            message=message,
            confirm=lambda: _confirm("Release this entire Git repo to the bare repo?"),
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


def _parse_on_to_clause(route: list[str], command_name: str) -> tuple[str, str]:
    if len(route) != 4 or route[0] != "on" or route[2] != "to":
        raise typer.BadParameter(
            f"Expected syntax: {command_name} --repo <repo> on <address> to <address>"
        )
    return _resolve_address_alias(route[1]), _resolve_address_alias(route[3])


def _run_release_on_remote_executor(
    config: dict,
    repo: str,
    executor_address: str,
    target_address: str,
    message: str | None,
) -> None:
    ssh_user = (
        (config.get("from") or {}).get("ssh_user")
        or (config.get("target") or {}).get("ssh_user")
        or config.get("ssh_user")
    )
    if not ssh_user:
        raise click.ClickException(
            f"ssh_user is required to execute release on remote address: {executor_address}"
        )

    remote_cmd = [
        "qtcli_gitrelease",
        "release",
        "--repo",
        repo,
        "on",
        "local",
        "to",
        target_address,
    ]
    if message:
        remote_cmd.extend(["--message", message])

    script = " ".join(shlex.quote(part) for part in remote_cmd)
    run_live(["ssh", f"{ssh_user}@{executor_address}", script])


def _run_send_on_remote_executor(
    config: dict,
    repo: str,
    executor_address: str,
    target_address: str,
) -> None:
    _run_remote_cli_command(
        config,
        executor_address,
        [
            "qtcli_gitrelease",
            "send",
            "--repo",
            repo,
            "on",
            "local",
            "to",
            target_address,
        ],
    )


def _run_remote_cli_command(
    config: dict,
    executor_address: str,
    remote_cmd: list[str],
) -> None:
    ssh_user = (
        (config.get("from") or {}).get("ssh_user")
        or (config.get("target") or {}).get("ssh_user")
        or config.get("ssh_user")
    )
    if not ssh_user:
        raise click.ClickException(
            f"ssh_user is required to execute command on remote address: {executor_address}"
        )

    script = " ".join(shlex.quote(part) for part in remote_cmd)
    run_live(["ssh", f"{ssh_user}@{executor_address}", script])


def _is_local_address(value: str) -> bool:
    return value in {"local", "localhost", "127.0.0.1", "::1"} or value.startswith("127.")


if __name__ == "__main__":
    app()
