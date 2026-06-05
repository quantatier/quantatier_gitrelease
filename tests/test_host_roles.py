import io
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from contextlib import redirect_stdout

from typer.testing import CliRunner

from qtcli_gitrelease.cli import app
from qtcli_gitrelease.config import (
    ensure_release_host,
    list_repos,
    load_release_config_for_target,
    load_send_config,
    resolve_release_repo,
)
from qtcli_gitrelease.models import RepoConfig, ResolvedRepo
from qtcli_gitrelease.release import (
    _bootstrap_existing_github_history,
    _build_remote_release_command,
    release_github_repo,
    release_repo,
)
from qtcli_gitrelease.transfer import (
    _build_one_path_command,
    _build_record_rollback_pointer_command,
    _build_rollback_command,
    _build_validate_remote_git_worktree_command,
    _remote_repo_root,
    _validate_send_include_path,
    rollback_repo,
    send_repo,
)


class WorkflowTests(TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.tmp_path = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_send_help_only_describes_send_syntax(self) -> None:
        result = CliRunner().invoke(app, ["send", "--help"])

        self.assertEqual(0, result.exit_code)
        self.assertIn("to ADDRESS", result.stdout)
        self.assertIn("on ADDRESS", result.stdout)
        self.assertIn("Address aliases:", result.stdout)
        self.assertNotIn("from ADDRESS to ADDRESS", result.stdout)
        self.assertNotIn("rollback from", result.stdout)

    def test_rollback_help_describes_address_syntax(self) -> None:
        result = CliRunner().invoke(app, ["rollback", "--help"])

        self.assertEqual(0, result.exit_code)
        self.assertIn("on ADDRESS", result.stdout)
        self.assertNotIn("via ADDRESS", result.stdout)
        self.assertIn("Address aliases:", result.stdout)

    def test_send_on_remote_executor_runs_remote_cli(self) -> None:
        config = {
            "target": {
                "ssh_user": "devuser",
                "root": "/srv/quantatier",
            },
        }

        with patch("qtcli_gitrelease.cli.load_send_config", return_value=config):
            with patch("qtcli_gitrelease.cli.run_live") as run_live:
                result = CliRunner().invoke(
                    app,
                    [
                        "send",
                        "--repo",
                        "demo",
                        "on",
                        "10.0.0.2",
                        "to",
                        "10.0.0.9",
                    ],
                )

        self.assertEqual(0, result.exit_code)
        command = run_live.call_args_list[0].args[0]
        self.assertEqual(["ssh", "devuser@10.0.0.2"], command[:2])
        self.assertEqual("gitrelease send --repo demo on local to 10.0.0.9", command[2])

    def test_rollback_does_not_support_remote_executor(self) -> None:
        result = CliRunner().invoke(
            app,
            [
                "rollback",
                "--repo",
                "demo",
                "via",
                "10.0.0.2",
                "on",
                "10.0.0.9",
            ],
        )

        self.assertNotEqual(0, result.exit_code)

    def test_release_help_describes_executor_and_target_syntax(self) -> None:
        result = CliRunner().invoke(app, ["release", "--help"])

        self.assertEqual(0, result.exit_code)
        self.assertIn("on ADDRESS to ADDRESS", result.stdout)
        self.assertIn("on local to github", result.stdout)
        self.assertIn("config/release_github.json", result.stdout)
        self.assertIn("target.github_username", result.stdout)
        self.assertIn("ssh -T git@github.com", result.stdout)
        self.assertIn("origin must match the GitHub target", result.stdout)
        self.assertIn("initial commit", result.stdout)
        self.assertIn("on main_devbox to github", result.stdout)
        self.assertIn("Address aliases:", result.stdout)
        self.assertNotIn("from ADDRESS to ADDRESS", result.stdout)

    def test_release_on_remote_executor_runs_remote_cli(self) -> None:
        config = {
            "target": {
                "ssh_user": "devuser",
                "root": "/opt/git",
            },
        }

        with patch("qtcli_gitrelease.cli.load_release_config", return_value=config):
            with patch("qtcli_gitrelease.cli.run_live") as run_live:
                result = CliRunner().invoke(
                    app,
                    [
                        "release",
                        "--repo",
                        "demo",
                        "on",
                        "10.0.0.2",
                        "to",
                        "127.0.0.1",
                        "--message",
                        "release demo",
                    ],
                )

        self.assertEqual(0, result.exit_code)
        command = run_live.call_args_list[0].args[0]
        self.assertEqual(["ssh", "devuser@10.0.0.2"], command[:2])
        self.assertIn("gitrelease release --repo demo on local to 127.0.0.1", command[2])
        self.assertIn("--message 'release demo'", command[2])

    def send_config(self) -> dict:
        return {
            "from": {
                "address": "127.0.0.1",
                "root": str(self.tmp_path),
            },
            "target": {
                "address": "10.0.0.2",
                "ssh_user": "devuser",
                "root": "/srv/quantatier",
            },
        }

    def rollback_config(self) -> dict:
        return {
            "target": {
                "address": "10.0.0.2",
                "ssh_user": "devuser",
                "root": "/srv/quantatier",
            },
        }

    def local_rollback_config(self) -> dict:
        return {
            "target": {
                "address": "127.0.0.1",
                "root": str(self.tmp_path),
            },
        }

    def release_config(self) -> dict:
        return {
            "default_branch": "master",
            "ssh_user": "devuser",
        }

    def repo(self) -> RepoConfig:
        return RepoConfig(
            name="demo",
            worktree="packages/demo",
            bare="demo.git",
            branch="master",
            enabled=True,
            send_include_paths=["src/"],
        )

    def test_release_has_no_identity_check_when_identity_fields_are_absent(self) -> None:
        config = self.release_config()

        ensure_release_host(config)

    def test_release_repo_uses_top_level_workspace_root(self) -> None:
        repo = RepoConfig(
            name="demo",
            worktree=str(self.tmp_path / "packages" / "demo"),
            bare="/opt/git/packages/demo.git",
            branch="master",
            enabled=True,
            send_include_paths=[],
            bare_repo_address="127.0.0.1",
        )
        resolved = resolve_release_repo(self.release_config(), repo)
        self.assertEqual((self.tmp_path / "packages" / "demo").resolve(), resolved.worktree)
        self.assertEqual("/opt/git/packages/demo.git", resolved.bare)
        self.assertEqual("127.0.0.1", resolved.target_name)
        self.assertIsNone(resolved.ssh_address)

    def test_release_repo_config_uses_repo_paths_and_bare_repo_addresses(self) -> None:
        config = self.release_config()
        config["repos"] = [
            {
                "repo_name": "demo",
                "project_path": str(self.tmp_path / "packages" / "demo"),
                "bare_repo_address": "127.0.0.1",
                "bare_repo_path": "/opt/git/packages/demo.git",
            }
        ]

        repos = list_repos(config, repo_name="demo")
        resolved = resolve_release_repo(config, repos[0])

        self.assertEqual("master", repos[0].branch)
        self.assertEqual("/opt/git/packages/demo.git", resolved.bare)

    def test_release_repo_can_resolve_remote_bare_repo_address(self) -> None:
        repo = RepoConfig(
            name="demo",
            worktree="/srv/quantatier/packages/demo",
            bare="/srv/git/demo.git",
            branch="master",
            enabled=True,
            send_include_paths=[],
            bare_repo_address="10.0.0.2",
        )

        resolved = resolve_release_repo(self.release_config(), repo)

        self.assertEqual(Path("/srv/quantatier/packages/demo"), resolved.worktree)
        self.assertEqual("/srv/git/demo.git", resolved.bare)
        self.assertEqual("10.0.0.2", resolved.target_name)
        self.assertEqual("devuser@10.0.0.2", resolved.ssh_address)

    def test_path_release_repo_can_use_full_github_remote_url(self) -> None:
        config = {
            "from": {
                "root": str(self.tmp_path),
            },
            "target": {
                "github_username": "owner",
            },
            "repos": [
                {
                    "repo_name": "demo",
                    "from_repo_path": "/packages/demo",
                    "target_repo_path": "git@github.com:{github_username}/demo.git",
                    "branch": "master",
                }
            ],
        }

        repos = list_repos(config, repo_name="demo")
        resolved = resolve_release_repo(config, repos[0])

        self.assertEqual((self.tmp_path / "packages" / "demo").resolve(), resolved.worktree)
        self.assertEqual("git@github.com:owner/demo.git", resolved.bare)
        self.assertEqual("127.0.0.1", resolved.target_name)
        self.assertIsNone(resolved.ssh_address)

    def test_send_target_requires_address_and_ssh_user(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "requires target.address with target.ssh_user"):
            _remote_repo_root({"root": "/srv/quantatier"}, "packages/demo")

    def test_send_target_requires_ssh_username(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "ssh_user is required"):
            _remote_repo_root(
                {"root": "/srv/quantatier", "address": "10.0.0.2"},
                "packages/demo",
            )

    def test_send_target_rejects_placeholder_address(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "ssh_address still contains a placeholder"):
            _remote_repo_root(
                {
                    "root": "/srv/quantatier",
                    "ssh_address": "<SSH_USER>@<SSH_HOST>",
                },
                "packages/demo",
            )

    def test_send_target_rejects_placeholder_root(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "target.root still contains"):
            _remote_repo_root(
                {
                    "root": "<TARGET_ROOT>/quantatier",
                    "address": "10.0.0.2",
                    "ssh_user": "devuser",
                },
                "packages/demo",
            )

    def test_send_target_requires_absolute_root(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "target.root must be an absolute path"):
            _remote_repo_root(
                {"root": "quantatier", "address": "10.0.0.2", "ssh_user": "devuser"},
                "packages/demo",
            )

    def test_send_repo_config_does_not_require_bare_repo_address(self) -> None:
        config = self.send_config()
        config["repos"] = [
            {
                "repo_name": "demo",
                "local_git_worktree_root": "packages/demo",
                "remote_git_worktree_root": "packages/demo",
                "send_include_paths": ["src/"],
            }
        ]

        repos = list_repos(config, repo_name="demo", require_bare=False)

        self.assertEqual("", repos[0].bare)
        self.assertEqual("", repos[0].branch)
        self.assertEqual("packages/demo", repos[0].worktree)
        self.assertEqual("packages/demo", repos[0].remote_git_worktree_root)

    def test_default_config_prefers_package_source_config(self) -> None:
        config_dir = self.tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "send.json").write_text('{"workspace_root": "/dev/tree"}')

        with patch("qtcli_gitrelease.config.PACKAGE_CONFIG_DIR", config_dir):
            config = load_send_config(None)

        self.assertEqual("/dev/tree", config["workspace_root"])

    def test_release_github_target_prefers_release_github_config(self) -> None:
        config_dir = self.tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "release.json").write_text('{"name": "normal"}')
        (config_dir / "release_github.json").write_text('{"name": "github"}')

        with patch("qtcli_gitrelease.config.PACKAGE_CONFIG_DIR", config_dir):
            config = load_release_config_for_target(None, "github")

        self.assertEqual("github", config["name"])

    def test_release_preview_cancellation_does_not_commit_push_or_tag(self) -> None:
        repo = ResolvedRepo(
            name="demo",
            worktree=self.tmp_path / "packages" / "demo",
            bare="/opt/git/packages/demo.git",
            branch="master",
            enabled=True,
            send_include_paths=[],
        )
        output = io.StringIO()

        with patch("qtcli_gitrelease.release.ensure_repo_valid") as ensure_repo_valid:
            with patch("qtcli_gitrelease.release.ensure_not_behind_or_diverged") as ensure_not_behind:
                with patch("qtcli_gitrelease.release.read_pyproject_version", return_value="1.2.3"):
                    with patch("qtcli_gitrelease.release.commit_if_needed") as commit:
                        with patch("qtcli_gitrelease.release.push_branch") as push:
                            with patch("qtcli_gitrelease.release.create_and_push_tag") as tag:
                                with patch("qtcli_gitrelease.release.ensure_tag_not_exists") as ensure_tag:
                                    with redirect_stdout(output):
                                        release_repo(repo, message="release demo", confirm=lambda: False)

        text = output.getvalue()
        self.assertIn("Scope:             entire Git repo; partial release is not allowed", text)
        self.assertIn("Release cancelled.", text)
        ensure_repo_valid.assert_called_once_with(repo)
        ensure_not_behind.assert_called_once_with(repo)
        ensure_tag.assert_not_called()
        commit.assert_not_called()
        push.assert_not_called()
        tag.assert_not_called()

    def test_release_checks_tag_before_commit_or_push(self) -> None:
        repo = ResolvedRepo(
            name="demo",
            worktree=self.tmp_path / "packages" / "demo",
            bare="/opt/git/packages/demo.git",
            branch="master",
            enabled=True,
            send_include_paths=[],
        )

        with patch("qtcli_gitrelease.release.ensure_repo_valid"):
            with patch("qtcli_gitrelease.release.ensure_not_behind_or_diverged"):
                with patch("qtcli_gitrelease.release.read_pyproject_version", return_value="1.2.3"):
                    with patch("qtcli_gitrelease.release.ensure_tag_not_exists", side_effect=RuntimeError("tag exists")):
                        with patch("qtcli_gitrelease.release.commit_if_needed") as commit:
                            with patch("qtcli_gitrelease.release.push_branch") as push:
                                with patch("qtcli_gitrelease.release.create_and_push_tag") as tag:
                                    with self.assertRaisesRegex(RuntimeError, "tag exists"):
                                        release_repo(repo, message="release demo", confirm=lambda: True)

        commit.assert_not_called()
        push.assert_not_called()
        tag.assert_not_called()

    def test_release_to_remote_bare_repo_reuses_git_ssh(self) -> None:
        repo = ResolvedRepo(
            name="demo",
            worktree=self.tmp_path / "packages" / "demo",
            bare="devuser@10.0.0.9:/opt/git/packages/demo.git",
            branch="master",
            enabled=True,
            send_include_paths=[],
        )
        seen_commands: list[str] = []

        def record_git_ssh(_repo: ResolvedRepo, *_args) -> None:
            seen_commands.append(os.environ.get("GIT_SSH_COMMAND", ""))

        with patch("qtcli_gitrelease.release.read_pyproject_version", return_value="1.2.3"):
            with patch("qtcli_gitrelease.release.ensure_repo_valid", side_effect=record_git_ssh):
                with patch("qtcli_gitrelease.release.ensure_not_behind_or_diverged", side_effect=record_git_ssh):
                    with patch("qtcli_gitrelease.release.ensure_tag_not_exists", side_effect=record_git_ssh):
                        with patch("qtcli_gitrelease.release.commit_if_needed", side_effect=record_git_ssh):
                            with patch("qtcli_gitrelease.release.push_branch", side_effect=record_git_ssh):
                                with patch("qtcli_gitrelease.release.create_and_push_tag", side_effect=record_git_ssh):
                                    with patch("qtcli_gitrelease.release.output", return_value="abc123"):
                                        with patch("qtcli_gitrelease.release.append_local_history"):
                                            with patch("qtcli_gitrelease.release.run_live") as run_live:
                                                release_repo(repo, message="release demo", confirm=lambda: True)

        self.assertTrue(seen_commands)
        self.assertTrue(all("ControlMaster=auto" in command for command in seen_commands))
        self.assertTrue(all("ControlPath=/tmp/qtgr_" in command for command in seen_commands))
        self.assertEqual("ssh", run_live.call_args_list[-1].args[0][0])
        self.assertIn("-O", run_live.call_args_list[-1].args[0])
        self.assertIn("exit", run_live.call_args_list[-1].args[0])

    def test_github_release_explains_missing_target_repo(self) -> None:
        repo = ResolvedRepo(
            name="demo",
            worktree=self.tmp_path / "packages" / "demo",
            bare="git@github.com:owner/demo.git",
            branch="master",
            enabled=True,
            send_include_paths=[],
        )

        class Proc:
            def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
                self.returncode = returncode
                self.stdout = stdout
                self.stderr = stderr

        with patch(
            "qtcli_gitrelease.release.run",
            side_effect=[
                Proc(0),
                Proc(128, stderr="ERROR: Repository not found.\n"),
            ],
        ):
            with self.assertRaisesRegex(RuntimeError, "target repository was not found"):
                _bootstrap_existing_github_history(repo)

    def test_github_release_pushes_local_head_to_configured_github_branch(self) -> None:
        repo = ResolvedRepo(
            name="demo",
            worktree=self.tmp_path / "packages" / "demo",
            bare="git@github.com:owner/demo.git",
            branch="main",
            enabled=True,
            send_include_paths=[],
        )

        with patch("qtcli_gitrelease.release.read_pyproject_version", return_value="1.2.3"):
            with patch("qtcli_gitrelease.release.current_branch", return_value="master"):
                with patch("qtcli_gitrelease.release.is_git_worktree", return_value=True):
                    with patch("qtcli_gitrelease.release.origin_url", return_value=repo.bare):
                        with patch("qtcli_gitrelease.release._fetch_github_origin_or_explain"):
                            with patch("qtcli_gitrelease.release.ahead_behind", return_value=(0, 0)):
                                with patch("qtcli_gitrelease.release.ensure_tag_not_exists"):
                                    with patch("qtcli_gitrelease.release.commit_if_needed"):
                                        with patch("qtcli_gitrelease.release.create_and_push_tag"):
                                            with patch("qtcli_gitrelease.release.append_local_history"):
                                                with patch("qtcli_gitrelease.release.output", return_value="abc123"):
                                                    with patch("qtcli_gitrelease.release.run_live") as run_live:
                                                        run_live.return_value.returncode = 0
                                                        repo.worktree.mkdir(parents=True)
                                                        release_github_repo(
                                                            repo,
                                                            message="release demo",
                                                            confirm=lambda: True,
                                                        )

        self.assertIn(
            ["git", "push", "origin", "HEAD:main"],
            [call.args[0] for call in run_live.call_args_list],
        )

    def test_github_release_preflight_failure_happens_before_commit(self) -> None:
        repo = ResolvedRepo(
            name="demo",
            worktree=self.tmp_path / "packages" / "demo",
            bare="git@github.com:owner/demo.git",
            branch="main",
            enabled=True,
            send_include_paths=[],
        )

        class Proc:
            def __init__(self, returncode: int) -> None:
                self.returncode = returncode

        def fake_run_live(command, *_args, **_kwargs):
            if command == ["git", "push", "--dry-run", "origin", "HEAD:main"]:
                return Proc(1)
            return Proc(0)

        with patch("qtcli_gitrelease.release.read_pyproject_version", return_value="1.2.3"):
            with patch("qtcli_gitrelease.release.current_branch", return_value="master"):
                with patch("qtcli_gitrelease.release.is_git_worktree", return_value=True):
                    with patch("qtcli_gitrelease.release.origin_url", return_value=repo.bare):
                        with patch("qtcli_gitrelease.release._fetch_github_origin_or_explain"):
                            with patch("qtcli_gitrelease.release.ahead_behind", return_value=(0, 0)):
                                with patch("qtcli_gitrelease.release.ensure_tag_not_exists"):
                                    with patch("qtcli_gitrelease.release.commit_if_needed") as commit:
                                        with patch("qtcli_gitrelease.release.run_live", side_effect=fake_run_live):
                                            repo.worktree.mkdir(parents=True)
                                            with self.assertRaisesRegex(RuntimeError, "before creating"):
                                                release_github_repo(
                                                    repo,
                                                    message="release demo",
                                                    confirm=lambda: True,
                                                )

        commit.assert_not_called()

    def test_remote_release_preview_cancellation_does_not_ssh(self) -> None:
        repo = ResolvedRepo(
            name="demo",
            worktree=Path("/srv/quantatier/packages/demo"),
            bare="/srv/git/demo.git",
            branch="master",
            enabled=True,
            send_include_paths=[],
            target_name="10.0.0.2",
            ssh_address="devuser@10.0.0.2",
        )
        output = io.StringIO()

        with patch("qtcli_gitrelease.release.run_live") as run_live:
            with redirect_stdout(output):
                release_repo(repo, message="release demo", confirm=lambda: False)

        text = output.getvalue()
        self.assertIn("Bare repo address: 10.0.0.2", text)
        self.assertIn("SSH address:       devuser@10.0.0.2", text)
        self.assertIn("Release cancelled.", text)
        run_live.assert_not_called()

    def test_remote_release_command_runs_git_release_on_target(self) -> None:
        repo = ResolvedRepo(
            name="demo",
            worktree=Path("/srv/quantatier/packages/demo"),
            bare="/srv/git/demo.git",
            branch="master",
            enabled=True,
            send_include_paths=[],
            target_name="10.0.0.2",
            ssh_address="devuser@10.0.0.2",
        )

        command = _build_remote_release_command(repo, "release demo")

        self.assertEqual(["ssh", "devuser@10.0.0.2"], command[:2])
        self.assertIn("git -C \"$worktree\" rev-parse --show-toplevel", command[2])
        self.assertIn("git -C \"$worktree\" push -u origin \"$branch\"", command[2])
        self.assertIn("git -C \"$worktree\" tag -a \"$tag\" -m \"$message\"", command[2])

    def test_directory_preview_excludes_git_and_only_deletes_inside_allowlist(self) -> None:
        src_root = self.tmp_path / "demo"
        (src_root / "src").mkdir(parents=True)

        command = _build_one_path_command(
            src_root,
            "devuser@10.0.0.2:/srv/quantatier/packages/demo",
            "src/",
        )
        self.assertIsNotNone(command)
        assert command is not None
        exclude_index = command.index("--exclude")
        self.assertEqual(["--exclude", ".git/"], command[exclude_index : exclude_index + 2])
        self.assertIn("--delete", command)
        self.assertEqual("devuser@10.0.0.2:/srv/quantatier/packages/demo/src/", command[-1])

    def test_directory_send_include_path_requires_trailing_slash(self) -> None:
        src_root = self.tmp_path / "demo"
        (src_root / "src").mkdir(parents=True)

        with self.assertRaisesRegex(RuntimeError, "missing trailing '/'"):
            _build_one_path_command(
                src_root,
                "devuser@10.0.0.2:/srv/quantatier/packages/demo",
                "src",
            )

    def test_file_send_include_path_may_not_have_trailing_slash(self) -> None:
        src_root = self.tmp_path / "demo"
        src_root.mkdir(parents=True)
        (src_root / "README.md").write_text("demo", encoding="utf-8")

        with self.assertRaisesRegex(RuntimeError, "source is not a directory"):
            _build_one_path_command(
                src_root,
                "devuser@10.0.0.2:/srv/quantatier/packages/demo",
                "README.md/",
            )

    def test_git_history_may_not_be_added_to_send_include_paths(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Protected path may not be sent"):
            _validate_send_include_path(".git/")

    def test_send_include_path_may_not_escape_repo_root(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Invalid send include path"):
            _validate_send_include_path("../outside")

    def test_result_directory_may_not_be_added_to_send_include_paths(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "Protected path may not be sent"):
            _validate_send_include_path("BacktestingResult/")

    def test_send_preview_cancellation_does_not_run_rsync(self) -> None:
        config = self.send_config()
        (self.tmp_path / "packages" / "demo" / "src").mkdir(parents=True)

        with patch("qtcli_gitrelease.transfer.run_live") as run_live:
            send_repo(config, self.repo(), target_name="10.0.0.2", confirm=lambda: False)

        run_live.assert_not_called()

    def test_send_uses_json_target_address_when_command_address_is_omitted(self) -> None:
        config = self.send_config()
        (self.tmp_path / "packages" / "demo" / "src").mkdir(parents=True)
        output = io.StringIO()

        with patch("qtcli_gitrelease.transfer.run_live") as run_live:
            with redirect_stdout(output):
                send_repo(config, self.repo(), target_name=None, confirm=lambda: False)

        text = output.getvalue()
        self.assertIn("Remote target:   10.0.0.2", text)
        self.assertIn("devuser@10.0.0.2:/srv/quantatier/packages/demo", text)
        run_live.assert_not_called()

    def test_send_command_address_overrides_json_target_address(self) -> None:
        config = self.send_config()
        (self.tmp_path / "packages" / "demo" / "src").mkdir(parents=True)
        output = io.StringIO()

        with patch("qtcli_gitrelease.transfer.run_live") as run_live:
            with redirect_stdout(output):
                send_repo(config, self.repo(), target_name="10.0.0.9", confirm=lambda: False)

        text = output.getvalue()
        self.assertIn("Remote target:   10.0.0.9", text)
        self.assertIn("devuser@10.0.0.9:/srv/quantatier/packages/demo", text)
        run_live.assert_not_called()

    def test_send_preflight_rejects_missing_include_paths_before_remote_commands(self) -> None:
        config = self.send_config()
        (self.tmp_path / "packages" / "demo").mkdir(parents=True)

        with patch("qtcli_gitrelease.transfer.run_live") as run_live:
            with self.assertRaisesRegex(RuntimeError, "preflight failed"):
                send_repo(config, self.repo(), target_name="10.0.0.2", confirm=lambda: True)

        run_live.assert_not_called()

    def test_send_preview_shows_scope_and_path_mapping_before_confirmation(self) -> None:
        config = self.send_config()
        (self.tmp_path / "packages" / "demo" / "src").mkdir(parents=True)
        output = io.StringIO()

        with patch("qtcli_gitrelease.transfer.run_live") as run_live:
            with redirect_stdout(output):
                send_repo(config, self.repo(), target_name="10.0.0.2", confirm=lambda: False)

        text = output.getvalue()
        self.assertIn("Local source:    127.0.0.1", text)
        self.assertIn("Remote target:   10.0.0.2", text)
        self.assertIn("Scope:           partial repo send (1 configured paths)", text)
        self.assertIn("Local git root:", text)
        self.assertIn("Remote git root:", text)
        self.assertIn("Planned path updates:", text)
        self.assertIn("local source:", text)
        self.assertIn("remote target:", text)
        self.assertIn("Confirming this transfer will update the remote paths shown above.", text)
        run_live.assert_not_called()

    def test_whole_root_path_is_reported_as_entire_git_worktree_root(self) -> None:
        config = self.send_config()
        repo = RepoConfig(
            name="demo",
            worktree="packages/demo",
            bare="demo.git",
            branch="master",
            enabled=True,
            send_include_paths=["./"],
        )
        (self.tmp_path / "packages" / "demo").mkdir(parents=True)
        output = io.StringIO()

        with patch("qtcli_gitrelease.transfer.run_live") as run_live:
            with redirect_stdout(output):
                send_repo(config, repo, target_name="10.0.0.2", confirm=lambda: False)

        text = output.getvalue()
        self.assertIn("Scope:           entire local_git_worktree_root -> remote_git_worktree_root", text)
        self.assertIn("directory mirror (--delete)", text)
        run_live.assert_not_called()

    def test_send_records_rollback_commit_before_rsync_after_confirmation(self) -> None:
        config = self.send_config()
        (self.tmp_path / "packages" / "demo" / "src").mkdir(parents=True)

        with patch("qtcli_gitrelease.transfer.run_live") as run_live:
            send_repo(config, self.repo(), target_name="10.0.0.2", confirm=lambda: True)

        self.assertEqual(5, run_live.call_count)
        self.assertEqual("ssh", run_live.call_args_list[0].args[0][0])
        self.assertIn("ControlMaster=auto", run_live.call_args_list[0].args[0])
        self.assertIn("rev-parse --show-toplevel", run_live.call_args_list[0].args[0][-1])
        self.assertEqual("ssh", run_live.call_args_list[1].args[0][0])
        self.assertIn("rev-parse --verify HEAD", run_live.call_args_list[1].args[0][-1])
        self.assertEqual("rsync", run_live.call_args_list[2].args[0][0])
        self.assertIn("-e", run_live.call_args_list[2].args[0])
        self.assertTrue(any("ControlPath=/tmp/qtgr_" in arg for arg in run_live.call_args_list[2].args[0]))
        self.assertEqual("ssh", run_live.call_args_list[3].args[0][0])
        self.assertIn(".gitrelease/history.jsonl", run_live.call_args_list[3].args[0][-1])
        self.assertIn('"action":"send"', run_live.call_args_list[3].args[0][-1])
        self.assertEqual("ssh", run_live.call_args_list[4].args[0][0])
        self.assertIn("-O", run_live.call_args_list[4].args[0])
        self.assertIn("exit", run_live.call_args_list[4].args[0])

    def test_validate_remote_git_worktree_command_requires_exact_git_root(self) -> None:
        command = _build_validate_remote_git_worktree_command(
            "devuser@10.0.0.2",
            "/srv/quantatier/multiprocess_framework",
        )

        self.assertEqual("ssh", command[0])
        self.assertIn("git -C \"$expected\" rev-parse --show-toplevel", command[2])
        self.assertIn("remote_git_worktree_root mismatch", command[2])
        self.assertIn("git init", command[2])
        self.assertIn("git commit --allow-empty", command[2])

    def test_record_rollback_pointer_command_records_remote_head(self) -> None:
        command = _build_record_rollback_pointer_command(
            "devuser@10.0.0.2",
            "/srv/quantatier/packages/demo",
            "demo",
        )

        self.assertEqual("ssh", command[0])
        self.assertEqual("devuser@10.0.0.2", command[1])
        self.assertIn("git -C /srv/quantatier/packages/demo rev-parse --verify HEAD", command[2])
        self.assertIn("~/.gitrelease/send_rollback/demo/latest_commit", command[2])

    def test_rollback_restores_recorded_commit(self) -> None:
        config = self.rollback_config()
        (self.tmp_path / "packages" / "demo" / "src").mkdir(parents=True)

        with patch("qtcli_gitrelease.transfer.run_live") as run_live:
            rollback_repo(config, self.repo(), target_name="10.0.0.2", confirm=lambda: True)

        self.assertEqual(4, run_live.call_count)
        self.assertEqual("ssh", run_live.call_args_list[0].args[0][0])
        self.assertIn("ControlMaster=auto", run_live.call_args_list[0].args[0])
        self.assertEqual("ssh", run_live.call_args_list[1].args[0][0])
        self.assertIn(".gitrelease/history.jsonl", run_live.call_args_list[2].args[0][-1])
        self.assertIn('"action":"rollback"', run_live.call_args_list[2].args[0][-1])
        self.assertEqual("ssh", run_live.call_args_list[3].args[0][0])
        self.assertIn("-O", run_live.call_args_list[3].args[0])
        self.assertIn("exit", run_live.call_args_list[3].args[0])

    def test_rollback_can_target_local_worktree(self) -> None:
        config = self.local_rollback_config()
        (self.tmp_path / "packages" / "demo" / "src").mkdir(parents=True)

        with patch("qtcli_gitrelease.transfer.run_live") as run_live:
            rollback_repo(config, self.repo(), target_name="127.0.0.1", confirm=lambda: True)

        self.assertEqual(3, run_live.call_count)
        self.assertEqual("sh", run_live.call_args_list[0].args[0][0])
        self.assertEqual("sh", run_live.call_args_list[1].args[0][0])
        self.assertEqual("sh", run_live.call_args_list[2].args[0][0])
        self.assertIn(".gitrelease/history.jsonl", run_live.call_args_list[2].args[0][2])

    def test_rollback_uses_json_target_address_when_command_address_is_omitted(self) -> None:
        config = self.rollback_config()
        (self.tmp_path / "packages" / "demo" / "src").mkdir(parents=True)

        with patch("qtcli_gitrelease.transfer.run_live") as run_live:
            rollback_repo(config, self.repo(), target_name=None, confirm=lambda: True)

        self.assertEqual(4, run_live.call_count)
        self.assertEqual("ssh", run_live.call_args_list[1].args[0][0])
        self.assertIn("devuser@10.0.0.2", run_live.call_args_list[1].args[0])

    def test_rollback_command_resets_to_recorded_commit(self) -> None:
        command = _build_rollback_command(
            "devuser@10.0.0.2",
            "/srv/quantatier/packages/demo",
            "demo",
        )

        self.assertEqual("ssh", command[0])
        self.assertIn("~/.gitrelease/send_rollback/demo/latest_commit", command[-1])
        self.assertIn("git -C /srv/quantatier/packages/demo reset --hard", command[-1])
        self.assertIn("git -C /srv/quantatier/packages/demo clean -fd", command[-1])
