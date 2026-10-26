"""Signing in to Google Play, searching it, and downloading an app's current version."""

import shutil
import sys
import tempfile
import time
from pathlib import Path

from .apk import first_version_name, safe_dir_name, write_metadata
from .apkeep import LOGIN_FAILED, run_apkeep

GOOGLE_SIGN_IN_URL = "https://accounts.google.com/EmbeddedSetup"
SIGN_IN_TIMEOUT_SECONDS = 600

# apkeep logs in before looking an app up, so asking for an app that doesn't exist checks the saved
# token without downloading anything.
SIGN_IN_CHECK_APP = "invalid.downloadapk.signin.check"


# ------------------------------------------------------------------------------------------ sign-in ---
def ensure_signed_in(ini: Path, force: bool, email: str | None) -> None:
    """Signs in when there are no saved credentials, when asked to, or when the saved token stops working."""
    if force or not ini.exists():
        sign_in(ini, email)
    if login_works(ini):
        return
    print("The saved Google Play sign-in no longer works; signing in again.")
    sign_in(ini, email)
    if not login_works(ini):
        sys.exit("Google Play still refuses the sign-in. Try another account with --sign-in --email ...")


def login_works(ini: Path) -> bool:
    with tempfile.TemporaryDirectory() as scratch:
        result = run_apkeep("-d", "google-play", "-i", str(ini), "--accept-tos", "-a", SIGN_IN_CHECK_APP, scratch)
    output = result.stdout + result.stderr
    if LOGIN_FAILED in output:
        return False
    if result.returncode != 0:
        sys.exit(f"Could not check the Google Play sign-in:\n{output}".strip())
    return True


def sign_in(ini: Path, email: str | None) -> None:
    oauth_token = _capture_oauth_token()
    email = email or input("Email of the Google account you signed in with: ").strip()
    result = run_apkeep("-d", "google-play", "-e", email, "--oauth-token", oauth_token)
    # apkeep prints the token, or an "Error: ..." line, on stdout and exits 0 either way.
    aas_token = result.stdout.strip()
    if result.returncode != 0 or not aas_token or aas_token.startswith("Error") or "\n" in aas_token:
        sys.exit(f"Could not exchange the sign-in for an AAS token:\n{result.stdout}{result.stderr}".strip())
    ini.parent.mkdir(parents=True, exist_ok=True)
    ini.write_text(f"[google]\nemail = {email}\naas_token = {aas_token}\n")
    print(f"Saved Google Play credentials to {ini}")


def _capture_oauth_token() -> str:
    """Opens Chrome on Google's embedded setup page and waits for the oauth_token cookie after sign-in."""
    from patchright.sync_api import sync_playwright

    print("Sign in to Google in the browser window. It closes by itself once sign-in completes.")
    # A throwaway profile, so the Google session doesn't outlive the sign-in.
    with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(profile, channel="chrome", headless=False, no_viewport=True)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(GOOGLE_SIGN_IN_URL)
            deadline = time.monotonic() + SIGN_IN_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                for cookie in context.cookies("https://accounts.google.com"):
                    if cookie["name"] == "oauth_token":
                        return cookie["value"]
                if not context.pages:
                    sys.exit("Browser closed before sign-in completed")
                time.sleep(1)
            sys.exit(f"Timed out after {SIGN_IN_TIMEOUT_SECONDS}s waiting for Google sign-in")
        finally:
            context.close()


# ------------------------------------------------------------------------------------------- search ---
def search(term: str, limit: int, country: str, lang: str) -> None:
    from google_play_scraper import search as play_search

    results = [r for r in play_search(term, lang=lang, country=country, n_hits=limit) if r.get("appId")]
    if not results:
        print(f"No Google Play results for {term!r}")
        return

    rows = [
        (
            r["appId"],
            _clip(r.get("title") or "", 40),
            _clip(r.get("developer") or "", 30),
            r.get("installs") or "",
            f"{r['score']:.1f}" if r.get("score") else "-",
            "Free" if r.get("free", True) else f"{r.get('price')} {r.get('currency') or ''}".strip(),
        )
        for r in results
    ]
    _print_table(("App ID", "Title", "Developer", "Installs", "Rating", "Price"), rows)


def _clip(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def _print_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    widths = [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(headers)]
    line = "  ".join("{:<%d}" % w for w in widths)
    for row in (headers, tuple("-" * w for w in widths), *rows):
        print(line.format(*row).rstrip())


# ----------------------------------------------------------------------------------------- download ---
def download(app_id: str, out: Path, ini: Path, country: str, lang: str) -> None:
    """Downloads the app's current version into out/<appId>/<version>/, unless that version is already there."""
    app_dir = out / app_id
    listed = current_version(app_id, country, lang)
    if listed and (app_dir / safe_dir_name(listed)).exists():
        print(f"{app_id} {listed} (current on Google Play) already downloaded: {app_dir / safe_dir_name(listed)}")
        return

    app_dir.mkdir(parents=True, exist_ok=True)
    staging: Path | None = Path(tempfile.mkdtemp(prefix=".partial-", dir=app_dir))
    try:
        print(f"Downloading {app_id} from Google Play...")
        result = run_apkeep(
            "-d", "google-play", "-i", str(ini), "-o", "split_apk=true", "--accept-tos", "-a", app_id, str(staging),
        )
        if LOGIN_FAILED in result.stdout + result.stderr:
            sys.exit("Google Play refused the saved sign-in. Run again with --sign-in.")

        # apkeep exits 0 even when nothing was downloaded, so the files are the only proof.
        apks = sorted(staging.rglob("*.apk"))
        if not apks:
            sys.exit(f"Google Play has no app {app_id}, or doesn't offer it to this account.")
        for apk in apks:  # flatten the per-app subfolder apkeep makes
            if apk.parent != staging:
                shutil.move(apk, staging / apk.name)
        for folder in [p for p in staging.iterdir() if p.is_dir()]:
            shutil.rmtree(folder)

        version = first_version_name(staging)
        if not version:
            sys.exit(f"Could not read versionName from {', '.join(a.name for a in apks)}")
        version_dir = app_dir / safe_dir_name(version)
        if version_dir.exists():
            print(f"{app_id} {version} already downloaded: {version_dir}")
            return
        write_metadata(staging, app_id, version)
        staging.rename(version_dir)
        staging = None
        print(f"Saved {app_id} {version} to {version_dir}")
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        if app_dir.exists() and not any(app_dir.iterdir()):
            app_dir.rmdir()  # nothing was saved for this app


def current_version(app_id: str, country: str, lang: str) -> str | None:
    """The version Google Play's store page shows, so a version already on disk isn't downloaded again.

    None when it can't tell (an error, or "Varies with device"): then the download goes ahead and the
    APK itself says which version it is.
    """
    from google_play_scraper import app as play_app

    try:
        version = play_app(app_id, lang=lang, country=country).get("version")
    except Exception:
        return None
    return version if version and version[0].isdigit() else None
