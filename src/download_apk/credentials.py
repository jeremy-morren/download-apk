"""Warns when the Google Play credentials file is, or is about to be, tracked by git."""

import shutil
import subprocess
import sys
from pathlib import Path


def warn_if_tracked_by_git(ini: Path) -> None:
    """Prints a warning to stderr when `ini` sits in a git work tree and is tracked or not ignored.

    Runs on every invocation, whether or not the file exists yet: one that doesn't exist will be created
    at sign-in. Says nothing outside a git repository or when git isn't installed.
    """
    if shutil.which("git") is None:
        return
    folder, name = ini.parent, ini.name
    if not folder.is_dir():
        return

    inside = _git(folder, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0:
        if "not a git repository" not in inside.stderr:
            first_line = (inside.stderr.strip().splitlines() or ["unknown error"])[0]
            _warn(f"could not check whether {ini} is tracked by git: {first_line}")
        return
    if inside.stdout.strip() != "true":
        return  # inside a .git folder or a bare repository

    if _git(folder, "ls-files", "--error-unmatch", "--", name).returncode == 0:
        _warn(
            f"Google Play credentials are already in git: {ini} is tracked.\n"
            f"  Remove it from the index with: git -C \"{folder}\" rm --cached -- {name}\n"
            f"  then add '{name}' to .gitignore. Anyone with the repository's history can use the token,\n"
            f"  so also sign in again with --sign-in to replace it and remove the account's access if it leaked."
        )
        return

    if _git(folder, "check-ignore", "-q", "--", name).returncode != 0:
        _warn(
            f"Google Play credentials will be added to git: {ini} is in a git repository and not ignored.\n"
            f"  Recommended: add '{name}' to .gitignore."
        )


def _git(folder: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(folder), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL)


def _warn(message: str) -> None:
    print(f"WARNING: {message}", file=sys.stderr)
