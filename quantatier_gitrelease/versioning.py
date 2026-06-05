from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib


VERSION_RE = re.compile(r"^\d+\.\d+\.\d+([a-zA-Z0-9.\-+]*)?$")


def read_pyproject_version(repo_path: Path) -> str:
    pyproject = repo_path / "pyproject.toml"

    if not pyproject.exists():
        raise RuntimeError(f"pyproject.toml not found: {pyproject}")

    with pyproject.open("rb") as f:
        data = tomllib.load(f)

    version = data.get("project", {}).get("version")

    if not version:
        raise RuntimeError("project.version not found in pyproject.toml")

    if version.startswith(("v", "V")):
        raise RuntimeError(
            f'pyproject.toml version should not start with "v" or "V": {version}\n'
            f'Use version = "{version[1:]}" instead.\n'
            f'The Git tag will be generated automatically as v{version[1:]}.'
        )

    if not VERSION_RE.match(version):
        raise RuntimeError(
            f"Invalid project.version format: {version}\n"
            f"Expected examples: 0.1.0, 1.0.5, 1.2.0rc1"
        )

    return version


def version_to_tag(version: str) -> str:
    return f"v{version}"
