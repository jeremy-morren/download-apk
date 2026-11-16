"""EFF's apkeep (https://github.com/EFForg/apkeep), which talks to Google Play.

The wheel carries apkeep compiled for its platform, in bin/ next to this file (see hatch_build.py).
"""

import subprocess
import sys
from pathlib import Path

# apkeep prints this when the saved token is refused.
LOGIN_FAILED = "Could not log in to Google Play"


def find_apkeep() -> Path:
    apkeep_path = Path(__file__).parent / "bin" / ("apkeep.exe" if sys.platform == "win32" else "apkeep")
    if not apkeep_path.is_file():
        sys.exit(
            f"apkeep is missing from {apkeep_path.parent}. Reinstall download-apk; an editable install "
            "compiles it unless DOWNLOAD_APK_SKIP_APKEEP is set."
        )
    return apkeep_path


def run_apkeep(*args: str) -> subprocess.CompletedProcess[str]:
    """apkeep exits 0 on most failures, so callers judge the result by its output and files."""
    return subprocess.run([find_apkeep(), *args], stdin=subprocess.DEVNULL, capture_output=True, text=True)
