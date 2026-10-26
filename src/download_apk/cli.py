"""The download-apk command."""

import argparse
import os
import re
import sys
import urllib.error
from pathlib import Path

from . import __version__
from .credentials import warn_if_tracked_by_git
from .google_play import download, ensure_signed_in, search
from .location import detect_country

DEFAULT_INI = ".google-play.ini"
DEFAULT_OUT = "apps"
OUT_ENV = "DOWNLOAD_APK_DIR"  # a standing default for --out

# An Android application ID: dot-separated segments, each starting with a letter. Checked before the
# ID becomes a folder name, so it can't escape the output folder.
APP_ID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")


def app_id_arg(value: str) -> str:
    if not APP_ID_RE.match(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not an Android app ID, e.g. com.example.app")
    return value


def country_arg(value: str) -> str:
    if not COUNTRY_RE.match(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a two-letter country code, e.g. au")
    return value.lower()


def store_country(given: str | None) -> str:
    """The Google Play country: --country if given, else detected. Logged either way, since Google Play's
    search results and app availability depend on it."""
    if given:
        log(f"Using Google Play country '{given}' (from --country).")
        return given
    country, source = detect_country()
    log(f"Determined Google Play country '{country}' from {source}; use --country to override.")
    return country


def log(message: str) -> None:
    print(message, file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="download-apk",
        description="Search Google Play and download apps' APKs into <out>/<appId>/<version>/.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--ini", type=Path, default=Path(DEFAULT_INI),
                        help=f"Google Play credentials file (default ./{DEFAULT_INI})")
    parser.add_argument("--sign-in", action="store_true", help="sign in to Google Play again even if credentials are saved")
    parser.add_argument("--email", help="Google account email (prompted for when signing in otherwise)")
    parser.add_argument("--country", type=country_arg,
                        help="two-letter store country code (default: detected from your IP address)")
    parser.add_argument("--lang", default="en", help="store language (default en)")
    commands = parser.add_subparsers(dest="command", required=True)

    p = commands.add_parser("search", help="search Google Play")
    p.add_argument("term", help="what to search for")
    p.add_argument("--limit", type=int, default=20, help="maximum results (default 20)")

    p = commands.add_parser("download", help="download an app's current version")
    p.add_argument("app_id", type=app_id_arg, help="the app's ID, e.g. com.example.app")
    p.add_argument("-o", "--out", type=Path, default=Path(os.environ.get(OUT_ENV) or DEFAULT_OUT),
                   help=f"output folder (default ${OUT_ENV} if set, else ./{DEFAULT_OUT})")
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    ini = args.ini.resolve()
    warn_if_tracked_by_git(ini)
    try:
        ensure_signed_in(ini, args.sign_in, args.email)
        country = store_country(args.country)
        if args.command == "search":
            search(args.term, args.limit, country, args.lang)
        else:
            download(args.app_id, args.out.resolve(), ini, country, args.lang)
    except urllib.error.URLError as error:
        sys.exit(f"Network error: {error.reason}")
    except KeyboardInterrupt:
        sys.exit(130)
