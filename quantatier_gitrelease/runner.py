from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence


class CommandError(RuntimeError):
    pass


def run(
    cmd: Sequence[str],
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    if capture:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd else None,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    else:
        proc = subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd else None,
            text=True,
            check=False,
        )

    if check and proc.returncode != 0:
        stdout = getattr(proc, "stdout", "") or ""
        stderr = getattr(proc, "stderr", "") or ""
        raise CommandError(
            "Command failed:\n"
            f"  {' '.join(cmd)}\n"
            f"cwd:\n"
            f"  {cwd}\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )

    return proc


def run_live(
    cmd: Sequence[str],
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    print(f"$ {' '.join(cmd)}")
    return run(cmd, cwd=cwd, check=check, capture=False)


def output(
    cmd: Sequence[str],
    cwd: Path | None = None,
    check: bool = True,
) -> str:
    proc = run(cmd, cwd=cwd, check=check, capture=True)
    return (proc.stdout or "").strip()
