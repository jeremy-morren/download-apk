"""Decompiling a downloaded version with jadx (https://github.com/skylot/jadx), found on PATH."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

JADX_MISSING = "jadx was not found on PATH. Install jadx (https://github.com/skylot/jadx) to decompile."


def find_jadx() -> str:
    # shutil.which also finds jadx.bat on Windows, through PATHEXT.
    jadx = shutil.which("jadx")
    if jadx is None:
        sys.exit(JADX_MISSING)
    return jadx


def decompiled_dir(version_dir: Path) -> Path:
    """<out>/<appId>/<version>/ decompiles to <out>/<appId>/decompiled/<version>/."""
    return version_dir.parent / "decompiled" / version_dir.name


def decompile(jadx: str, version_dir: Path) -> None:
    """Decompiles every APK of a downloaded version into decompiled_dir(version_dir), unless already done."""
    target = decompiled_dir(version_dir)
    if target.exists():
        print(f"Already decompiled: {target}")
        return

    # The base APK first, so its manifest is the one jadx keeps; the splits add resources, native
    # libraries and feature code.
    base = f"{version_dir.parent.name}.apk"
    apks = sorted(version_dir.glob("*.apk"), key=lambda p: (p.name != base, p.name))
    if not apks:
        sys.exit(f"No APKs to decompile in {version_dir}")

    target.parent.mkdir(parents=True, exist_ok=True)
    # jadx writes into a staging folder, so an interrupted run never looks finished.
    staging: Path | None = Path(tempfile.mkdtemp(prefix=".partial-", dir=target.parent))
    try:
        print(f"Decompiling {len(apks)} APK(s) with jadx into {target}...")
        # On Windows jadx is jadx.bat, and cmd.exe reads & % ^ in its arguments even inside quotes, so a
        # path such as C:\R&D\apps would break the command. jadx runs in the app's folder and is given
        # only paths relative to it, made of the app ID, safe_dir_name(version) and mkdtemp's name.
        app_dir = version_dir.parent
        args = [staging.relative_to(app_dir), *(apk.relative_to(app_dir) for apk in apks)]
        # Not captured: a large app takes minutes, and jadx reports its progress.
        result = subprocess.run([jadx, "--output-dir", *map(str, args)], cwd=app_dir, stdin=subprocess.DEVNULL)
        if not any(staging.iterdir()):
            sys.exit(f"jadx produced nothing (exit code {result.returncode})")
        staging.rename(target)
        staging = None
        if result.returncode != 0:
            # jadx exits non-zero when any method fails to decompile, which is common in large apps.
            print(f"jadx reported errors (exit code {result.returncode}); some code may be missing.")
        print(f"Decompiled to {target}")
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        if target.parent.exists() and not any(target.parent.iterdir()):
            target.parent.rmdir()  # nothing was decompiled for this app
