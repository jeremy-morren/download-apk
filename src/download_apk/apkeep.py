"""EFF's apkeep (https://github.com/EFForg/apkeep), which talks to Google Play. Fetched on first use."""

import platform
import subprocess
import sys
import urllib.request
from pathlib import Path

from platformdirs import user_cache_path

APKEEP_RELEASE = "https://github.com/EFForg/apkeep/releases/download/1.1.0/apkeep-"
APKEEP_ASSETS = {
    ("Windows", "AMD64"): "x86_64-pc-windows-msvc.exe",
    ("Linux", "x86_64"): "x86_64-unknown-linux-gnu",
    ("Linux", "aarch64"): "aarch64-unknown-linux-gnu",
}

# apkeep prints this when the saved token is refused.
LOGIN_FAILED = "Could not log in to Google Play"


def tools_dir() -> Path:
    return user_cache_path("download-apk", appauthor=False) / "tools"


def find_apkeep() -> Path:
    asset = APKEEP_ASSETS.get((platform.system(), platform.machine()))
    if asset is None:
        sys.exit(f"No apkeep build for {platform.system()} {platform.machine()}")
    apkeep_path = tools_dir() / f"apkeep-{asset}"
    if not apkeep_path.exists():
        print(f"Fetching apkeep into {apkeep_path.parent}...")
        apkeep_path.parent.mkdir(parents=True, exist_ok=True)
        partial = apkeep_path.with_name(apkeep_path.name + ".partial")
        _download_file(APKEEP_RELEASE + asset, partial)
        partial.chmod(0o755)
        partial.replace(apkeep_path)
    return apkeep_path


def run_apkeep(*args: str) -> subprocess.CompletedProcess[str]:
    """apkeep exits 0 on most failures, so callers judge the result by its output and files."""
    return subprocess.run([find_apkeep(), *args], stdin=subprocess.DEVNULL, capture_output=True, text=True)


def _download_file(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "download-apk"})
    live = sys.stdout.isatty()  # a progress counter only makes sense on a terminal
    with urllib.request.urlopen(request, timeout=300) as response, open(dest, "wb") as out:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while chunk := response.read(1 << 16):
            out.write(chunk)
            done += len(chunk)
            if live and total:
                print(f"\r  {done / total:6.1%} of {total / 1e6:.1f} MB", end="", flush=True)
    print(f"\r  {done / 1e6:.1f} MB downloaded" + " " * 10 if live else f"  {done / 1e6:.1f} MB downloaded")
