# gitrelease

Source transfer and Git release CLI for multi-machine development workflows.

The primary command is `gitrelease`. The distribution package is
`quantatier-gitrelease`, and the Python import package is
`quantatier_gitrelease`. The historical command `qtcli_gitrelease` is kept as a
compatibility alias for existing local automation.

## Workflows

The tool supports three maintenance actions:

- `send` copies selected source paths from the current machine to a configured
  remote target over SSH.
- `rollback` resets the configured target worktree to the last recorded send
  rollback commit. The target may be local or remote.
- `release` pushes one complete Git repo from the configured target to that
  target's bare repo address. The target may be local or remote. It does not
  send partial paths.
- `send` transfers only configured `send_include_paths`. It excludes `.git`, virtual
  environments, caches, and build output.
- SSH authentication remains interactive in the terminal. Do not put passwords
  in config files.

The commands use separate JSON files. The repository only commits
`*.example.json` templates. Copy them to formal config names before real use:

```bash
cp config/send.example.json config/send.json
cp config/rollback.example.json config/rollback.json
cp config/release.example.json config/release.json
cp config/release_github.example.json config/release_github.json
cp config/addresses.example.json config/addresses.json
```

- [config/send.example.json](config/send.example.json) contains `from`,
  `target`, and the source path allowlist. It does not use branch settings.
- [config/rollback.example.json](config/rollback.example.json) contains one
  rollback `target` and `repos`. The target address is a field, not a JSON key.
- [config/release.example.json](config/release.example.json) contains `from`,
  bare repo `target`, and release repo definitions.
- [config/release_github.example.json](config/release_github.example.json)
  contains GitHub release repo definitions using full Git remote URLs.

Do not store a password in JSON. Formal config files are ignored by Git so your
machine paths, LAN addresses, usernames, and GitHub organization names stay
local. Command addresses control whether an operation is local or remote:
`127.*`, `localhost`, `local`, and `::1` are treated as local; network addresses
such as `10.*` or `192.168.*` run through SSH using `ssh_user`. Password
prompts, when needed, come from SSH in the terminal and are not handled by this
project.

The same release boundary applies to GitHub and private bare repositories:
released commits should contain the reusable project source, templates, tests,
and docs only. Local initialization state stays outside the released project,
for example in `.git/config`, `~/.ssh/`, environment variables, or ignored
formal config files under `config/`.

Successful operations write a small project-local history record:

```text
<repo root>/.gitrelease/history.jsonl
```

The file uses one compact JSON object per line. It records only the operation
type, repo name, target address/root, key commit or tag, result, and timestamp.
It does not store passwords, command transcripts, or per-file transfer details.
The `.gitrelease/` directory is excluded from source transfer and should
stay ignored by Git.

Install the project in editable mode. The `gitrelease` command then loads the
development source tree directly:

```bash
cd /path/to/workspace/packages/gitrelease
python -m pip install -e .
gitrelease --help
```

When installed in editable mode, the CLI reads formal config files such as
`config/send.json`, `config/rollback.json`, or `config/release.json` from this
source tree first, no matter which project directory your terminal is currently
in. If a formal config file is absent, it can read the corresponding
`*.example.json` template so help and examples remain inspectable. If no package
source config is present, it falls back to `~/.config/gitrelease/*.json`.

## Editor Workflow

Run the editor-side source transfer command:

```bash
gitrelease send --repo example_project on local to main_devbox
```

The command validates the local workspace, target workspace path, and SSH
address before it previews anything. Placeholder values, missing usernames,
relative target paths, missing local source roots, and protected send paths stop
the command before transfer. It also preflights every configured
`send_include_paths` entry before printing the plan: configured paths must
exist, directories must end in `/`, and file paths must not end in `/`. After
validation, it previews the remote Git
worktree check, rollback commit recording step, whether the operation updates
the entire configured Git root or only selected paths, and each local-source to
remote-target path mapping. Confirm in the terminal to start the transfer. The
terminal may then ask for SSH authentication. Passwords are neither stored nor
handled by this project.

Before each confirmed send, the CLI records one remote rollback pointer: the
current `HEAD` commit of the remote worktree.

```text
~/.gitrelease/send_rollback/<repo>/latest_commit
```

Only the latest pointer is kept; the next confirmed send overwrites it. Rollback
reads that pointer and runs `git reset --hard <commit>` plus `git clean -fd` on
the remote target. It does not push and does not update the bare repo. To restore
the remote worktree to the recorded commit:

```bash
gitrelease rollback --repo example_project on main_devbox
```

`rollback` is started from the current machine, but the reset happens on the
target address given in the command. If the address is remote, rollback runs over
SSH as `target.ssh_user@address`. If the address is local, rollback runs against
the local target path.

```bash
gitrelease rollback --repo example_project on main_devbox
```

## Release Workflow

For local release, run:

```bash
gitrelease release --repo example_project on local to local -m "release example_project"
```

For release from a remote executor to a remote or local bare repo target, run:

```bash
gitrelease release --repo example_project on main_devbox to bare_repo_host -m "release example_project"
```

For release to GitHub, set `target.github_username` in
`config/release_github.json`. The config builds the GitHub remote URL as
`git@github.com:{github_username}/example_project.git`. Use `to github`:

```bash
gitrelease release --repo example_project on local to github -m "release example_project"
```

When `to github` is used, the command runs a setup helper before release. It can
initialize the local `.git` directory, set `origin`, set local Git identity, and
generate or print an SSH public key if GitHub SSH auth is not ready. The helper
prints the GitHub setup path, waits for you to add the public key, and rechecks
`ssh -T git@github.com` until authentication succeeds or you cancel. It never
stores SSH private keys, GitHub passwords, or tokens in the project. If GitHub
already has an initial commit, the helper fetches that history first so release
does not overwrite it.

To trigger a main development box from another machine and let that box push
its own code to GitHub:

```bash
gitrelease release --repo example_project on main_devbox to github -m "release example_project"
```

`release` validates the Git worktree, rejects behind or diverged branches, and
checks that the generated tag does not already exist locally or on origin before
any commit or push happens. It always previews the project path, branch, bare
repo address/path, version/tag information, and commit message before the
release starts. Confirming release means the entire configured Git repo is
released; partial release is not supported.

After confirmation and preflight checks, the local release command follows this
Git sequence:

```bash
git add -A
git commit -m "<message>"        # skipped when there are no staged changes
git push -u origin <branch>      # for example: git push -u origin master
git tag -a <tag> -m "<message>"  # for example: git tag -a v0.0.7 -m "..."
git push origin <tag>            # for example: git push origin v0.0.7
```

In the code and terminal output, "push branch" means the `git push -u origin
<branch>` step. The tag is generated from `pyproject.toml` as `v{version}`.

Run the workflow checks with the Python standard library:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -v
```

## Configuration Notes

Each repo declares an explicit `send_include_paths` allowlist. Only listed
project paths are sent; everything else is not sent by default. Directory
directory entries must end in `/`; they are synchronized with `rsync --delete`
inside that directory only, so the remote core-code directory is updated to
match the local copy. If a configured path points to a local directory but is
missing the trailing `/`, the command stops before transfer. If a path has a
trailing `/` but the local source is not a directory, the command also stops.

Git history is local to each machine. `.git`, virtual environments, caches, and
build output are protected paths: they are excluded from `rsync`, and the CLI
also rejects them if they are mistakenly added to `send_include_paths`.
Absolute paths and `..` paths are rejected. The project root is never
synchronized wholesale.

Address aliases live in `config/addresses.json`. For example, `main_devbox`
can map to a LAN or VPN address, so commands can use names instead of
memorizing IP addresses.

To add a repo, add one entry under `repos` in the relevant config file with
explicit source and target repo paths. Use these field names in formal config:

- `from.root`: source workspace root.
- `target.ssh_user`: SSH username used when the command target address is not local.
- `target.root`: target workspace root for send/rollback, or bare repo root for
  path-based release.
- `repo_name`: the name used with `--repo`.
- `from_repo_path`: repo path under `from.root`.
- `target_repo_path`: repo path under `target.root`.
- `send_include_paths`: send-only paths relative to both Git roots above.

Before send or rollback mutates anything, the target must confirm that
`target.root + target_repo_path` is an actual Git top-level worktree; if the configured
path is a non-Git folder or a subdirectory inside a Git repo, the command stops.
The target worktree must also have at least one commit so send can record a
rollback point. For a new target directory, initialize it first:

```bash
git init
git checkout -B master
git commit --allow-empty -m "init"
```

For rollback config:

- `target.ssh_user`: SSH username used when the command target address is not local.
- `target.root`: target workspace root.
- `repos`: repo-level rollback objects, using the same `repo_name` values as
  `send.json`.
- `target_repo_path`: repo path under `target.root`.

For release config:

- `from.root`: source workspace root.
- `target.ssh_user`: SSH username used when the executor or target address is not local.
- `target.root`: bare repo root for path-based release.
- `repo_name`: the name used with `release --repo`.
- `from_repo_path`: repo path under `from.root`.
- `target_repo_path`: bare repo path under `target.root`.
  It may also be a full Git remote URL, such as
  `git@github.com:owner/repo.git`; in that case `target.root` is not joined
  onto the target address.
- `branch`: release branch.
