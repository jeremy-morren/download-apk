"""Tests for download-apk. Offline by default; `pytest -m network` runs the live Google Play checks.

Nothing here signs in to Google or downloads an APK: apkeep, sign-in and Google Play lookups are
replaced with fakes, and the APKs are generated with a minimal binary AndroidManifest.xml.
"""

import json
import os
import re
import shutil
import struct
import subprocess
import zipfile
from pathlib import Path

import pytest

import download_apk
from download_apk import apk, cli, credentials, google_play, location

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def test_version_matches_pyproject():
    """The release workflow tags releases from pyproject.toml; --version reports __version__."""
    match = re.search(r'^version\s*=\s*"([^"]+)"', PYPROJECT.read_text(encoding="utf-8"), re.MULTILINE)
    assert match, "no version in pyproject.toml"
    assert download_apk.__version__ == match.group(1)


# -------------------------------------------------------------------------------- hatch_build.py ---
@pytest.fixture(scope="module")
def hatch_build():
    import importlib.util

    spec = importlib.util.spec_from_file_location("hatch_build", PYPROJECT.parent / "hatch_build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("markdown", "expected"),
    [
        ("[MIT](LICENSE).", "[MIT](BASE/LICENSE)."),
        ("![logo](docs/logo.png)", "![logo](BASE/docs/logo.png)"),
        ("[a](src/x.py) and [b](tests/)", "[a](BASE/src/x.py) and [b](BASE/tests/)"),
        ("[site](https://example.com/x)", "[site](https://example.com/x)"),
        ("[mail](mailto:a@b.c)", "[mail](mailto:a@b.c)"),
        ("[jump](#install)", "[jump](#install)"),
        ("[root](/abs/path)", "[root](/abs/path)"),
        ("```\npip install x\n```", "```\npip install x\n```"),
    ],
)
def test_readme_links_become_absolute_at_the_tag(hatch_build, markdown, expected):
    assert hatch_build.absolute_links(markdown, "BASE") == expected


def test_release_urls_point_at_the_tag(hatch_build):
    urls = hatch_build.release_urls("https://github.com/o/r", "1.2.3")
    assert urls == {
        "Homepage": "https://github.com/o/r",
        "Source": "https://github.com/o/r/tree/v1.2.3",
        "Documentation": "https://github.com/o/r/blob/v1.2.3/README.md",
        "Changelog": "https://github.com/o/r/releases/tag/v1.2.3",
        "Issues": "https://github.com/o/r/issues",
    }


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://github.com/jeremy-morren/download-apk", "https://github.com/jeremy-morren/download-apk"),
        ("https://github.com/jeremy-morren/download-apk.git", "https://github.com/jeremy-morren/download-apk"),
        ("https://github.com/jeremy-morren/download-apk/", "https://github.com/jeremy-morren/download-apk"),
        ("git@github.com:jeremy-morren/download-apk.git", "https://github.com/jeremy-morren/download-apk"),
        ("ssh://git@github.com/jeremy-morren/download-apk.git", "https://github.com/jeremy-morren/download-apk"),
        ("https://x-access-token:secret@github.com/o/r.git\n", "https://github.com/o/r"),  # credentials dropped
        ("github.com:o/r.git", "https://github.com/o/r"),
        ("https://ghp_token@github.com/o/r", "https://github.com/o/r"),  # a token as the user name too
        ("http://github.com/o/r", "https://github.com/o/r"),
        # Any host.
        ("https://gitlab.com/o/r.git", "https://gitlab.com/o/r"),
        ("git@gitlab.com:group/subgroup/r.git", "https://gitlab.com/group/subgroup/r"),
        ("https://user:pw@git.example.com:8443/o/r.git", "https://git.example.com:8443/o/r"),  # HTTPS port kept
        ("ssh://git@git.example.com:2222/o/r.git", "https://git.example.com/o/r"),  # SSH port dropped
        # Not a repository on a host.
        ("C:/repos/download-apk", None),
        ("C:\\repos\\download-apk", None),
        ("/home/me/download-apk", None),
        ("file:///home/me/download-apk", None),
        ("https://github.com/o", None),
        ("https://github.com//r", None),
        ("", None),
    ],
)
def test_repository_url_from_remote_url(hatch_build, remote, expected):
    assert hatch_build.repository_url(remote) == expected


@needs_git
def test_git_info_reads_the_repository(hatch_build, repo):
    (repo / "f").write_text("x")
    git(repo, "add", "f")
    dated = {"GIT_AUTHOR_DATE": "@1790856000", "GIT_COMMITTER_DATE": "@1790856000"}
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "c"],
                   check=True, capture_output=True, env={**os.environ, **dated})
    git(repo, "remote", "add", "origin", "git@github.com:o/r.git")
    assert hatch_build.git_info(repo) == {"commit_time": 1790856000, "repository": "https://github.com/o/r"}


def test_git_info_falls_back_to_what_the_sdist_recorded(hatch_build, tmp_path):
    (tmp_path / hatch_build.GIT_INFO_FILE).write_text('{"commit_time": 5, "repository": "https://github.com/o/r"}')
    assert hatch_build.git_info(tmp_path) == {"commit_time": 5, "repository": "https://github.com/o/r"}


def test_git_info_without_git_or_a_record(hatch_build, tmp_path):
    assert hatch_build.git_info(tmp_path) == {"commit_time": None, "repository": None}


def test_every_readme_link_resolves_on_pypi(hatch_build):
    """After the rewrite, every link in the published description is absolute or an in-page anchor."""
    readme = hatch_build.absolute_links((PYPROJECT.parent / "README.md").read_text(encoding="utf-8"), "https://x")
    targets = re.findall(r"\]\(([^)\s]+)\)", readme)
    assert targets, "the README has links"
    assert all(re.match(r"https?://|mailto:|#", t) for t in targets), targets


# --------------------------------------------------------------------------------------- APK fakes ---
VERSION_CODE_ATTR = 0x0101021B
VERSION_NAME_ATTR = 0x0101021C


def _string_pool(strings: list[str], utf8: bool) -> bytes:
    body = b""
    offsets = []
    for s in strings:
        offsets.append(len(body))
        if utf8:
            encoded = s.encode("utf-8")
            assert len(s) < 0x80 and len(encoded) < 0x80, "test strings stay short"
            body += bytes([len(s), len(encoded)]) + encoded + b"\x00"
        else:
            body += struct.pack("<H", len(s)) + s.encode("utf-16-le") + b"\x00\x00"
    body += b"\x00" * (-len(body) % 4)
    header_size = 28
    strings_start = header_size + 4 * len(strings)
    chunk_size = strings_start + len(body)
    flags = 0x100 if utf8 else 0
    header = struct.pack("<HHIIIIII", 0x0001, header_size, chunk_size, len(strings), 0, flags, strings_start, 0)
    return header + struct.pack(f"<{len(strings)}I", *offsets) + body


def binary_manifest(version: str, *, utf8: bool = False, typed_value: bool = False, obfuscated: bool = False) -> bytes:
    """A compiled AndroidManifest.xml whose <manifest> carries android:versionName.

    typed_value stores the version only in the attribute's typed value (raw value -1), as some build
    tools do. obfuscated blanks the attribute's name string, leaving only its resource ID to find it by.
    """
    strings = ["versionCode", "" if obfuscated else "versionName", "manifest", version]
    pool = _string_pool(strings, utf8)
    resource_map = struct.pack("<HHI", 0x0180, 8, 8 + 8) + struct.pack("<II", VERSION_CODE_ATTR, VERSION_NAME_ATTR)

    attrs = [
        # ns, name, raw value, Res_value(size, res0, dataType, data)
        struct.pack("<IIIHBBI", 0xFFFFFFFF, 0, 0xFFFFFFFF, 8, 0, 0x10, 42),  # versionCode = 42
        struct.pack("<IIIHBBI", 0xFFFFFFFF, 1, 0xFFFFFFFF if typed_value else 3, 8, 0, 0x03, 3),
    ]
    attr_ext = struct.pack("<IIHHHHHH", 0xFFFFFFFF, 2, 20, 20, len(attrs), 0, 0, 0)
    node_header_size = 16
    element_body = attr_ext + b"".join(attrs)
    element = struct.pack("<HHIII", 0x0102, node_header_size, node_header_size + len(element_body), 1, 0xFFFFFFFF)
    element += element_body

    content = pool + resource_map + element
    return struct.pack("<HHI", 0x0003, 8, 8 + len(content)) + content


def make_apk(path: Path, version: str, **manifest_options) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("AndroidManifest.xml", binary_manifest(version, **manifest_options))
        archive.writestr("classes.dex", b"dex\n035\x00")
    return path


# ----------------------------------------------------------------------------------------- apk.py ---
@pytest.mark.parametrize(
    "options",
    [{}, {"utf8": True}, {"typed_value": True}, {"obfuscated": True}],
    ids=["utf16", "utf8", "typed-value", "resource-id-only"],
)
def test_apk_version_name_reads_the_manifest(tmp_path, options):
    assert apk.apk_version_name(make_apk(tmp_path / "a.apk", "3.3.0", **options)) == "3.3.0"


def test_apk_version_name_is_none_without_a_manifest(tmp_path):
    path = tmp_path / "split.apk"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("res/raw/x", b"")
    assert apk.apk_version_name(path) is None


def test_first_version_name_skips_apks_without_one(tmp_path):
    with zipfile.ZipFile(tmp_path / "a-split.apk", "w") as archive:
        archive.writestr("res/raw/x", b"")
    make_apk(tmp_path / "b-base.apk", "2.0")
    assert apk.first_version_name(tmp_path) == "2.0"


@pytest.mark.parametrize(
    ("version", "expected"),
    [("1.2.3", "1.2.3"), ("1.0-beta_2", "1.0-beta_2"), ("1.0 (build 7)", "1.0__build_7_"), ("../x", ".._x")],
)
def test_safe_dir_name_keeps_versions_inside_one_folder(version, expected):
    assert apk.safe_dir_name(version) == expected


def test_write_metadata_hashes_every_file(tmp_path):
    (tmp_path / "base.apk").write_bytes(b"abc")
    apk.write_metadata(tmp_path, "com.example.app", "1.0")
    metadata = json.loads((tmp_path / "metadata.json").read_text())
    assert metadata["package"] == "com.example.app"
    assert metadata["version"] == "1.0"
    assert metadata["source"] == "google-play"
    assert metadata["sha256"] == {"base.apk": "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"}


# ----------------------------------------------------------------------------------------- cli.py ---
@pytest.fixture(autouse=True)
def no_country_lookup(monkeypatch, request):
    """Tests never contact the IP lookup service unless marked real_location."""
    if "real_location" not in request.keywords:
        monkeypatch.setattr(cli, "detect_country", lambda: ("zz", "a test"))


@pytest.mark.parametrize("app_id", ["com.example.app", "org.mozilla.firefox", "com.Example99.my_app", "a.b"])
def test_app_id_arg_accepts_android_ids(app_id):
    assert cli.app_id_arg(app_id) == app_id


@pytest.mark.parametrize("app_id", ["example", "../etc", "com..app", "com.example.", "1com.app", "com/app", "com.ex ample", ""])
def test_app_id_arg_rejects_anything_else(app_id):
    with pytest.raises(Exception):
        cli.app_id_arg(app_id)


def test_cli_rejects_a_bad_app_id_before_doing_anything(monkeypatch):
    monkeypatch.setattr(cli, "ensure_signed_in", lambda *a: pytest.fail("signed in for an invalid command"))
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["download", "../etc"])
    assert exit_info.value.code == 2


def test_cli_checks_git_then_signs_in_then_runs_the_command(monkeypatch, tmp_path):
    calls = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "warn_if_tracked_by_git", lambda ini: calls.append(("git", ini)))
    monkeypatch.setattr(cli, "ensure_signed_in", lambda ini, force, email: calls.append(("sign-in", ini, force, email)))
    monkeypatch.setattr(cli, "download", lambda app_id, out, ini, country, lang: calls.append(("download", app_id, out, country)))

    cli.main(["--sign-in", "--email", "a@b.c", "--country", "us", "download", "com.example.app", "--out", "dl"])

    ini = (tmp_path / ".google-play.ini").resolve()
    assert calls == [
        ("git", ini),
        ("sign-in", ini, True, "a@b.c"),
        ("download", "com.example.app", (tmp_path / "dl").resolve(), "us"),
    ]


@pytest.mark.parametrize(
    ("argv", "env", "expected"),
    [
        (["download", "com.example.app"], None, "apps"),
        (["download", "com.example.app"], "from-env", "from-env"),
        (["download", "com.example.app", "-o", "short"], "from-env", "short"),
        (["download", "com.example.app", "--out", "long"], None, "long"),
    ],
    ids=["default", "environment", "short-flag-wins", "long-flag"],
)
def test_output_folder_can_be_overridden(monkeypatch, tmp_path, argv, env, expected):
    monkeypatch.chdir(tmp_path)
    if env is None:
        monkeypatch.delenv(cli.OUT_ENV, raising=False)
    else:
        monkeypatch.setenv(cli.OUT_ENV, env)
    seen = []
    monkeypatch.setattr(cli, "warn_if_tracked_by_git", lambda ini: None)
    monkeypatch.setattr(cli, "ensure_signed_in", lambda *a: None)
    monkeypatch.setattr(cli, "download", lambda app_id, out, *rest: seen.append(out))
    cli.main(argv)
    assert seen == [(tmp_path / expected).resolve()]


# ---------------------------------------------------------------------------------- credentials.py ---


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    folder = tmp_path / "repo"
    folder.mkdir()
    git(folder, "-c", "init.defaultBranch=main", "init", "-q")
    return folder


@needs_git
def test_no_warning_outside_a_repository(tmp_path, capsys):
    credentials.warn_if_tracked_by_git(tmp_path / ".google-play.ini")
    assert capsys.readouterr().err == ""


@needs_git
@pytest.mark.parametrize("exists", [True, False], ids=["existing", "not-yet-created"])
def test_warns_when_the_file_would_be_added(repo, capsys, exists):
    ini = repo / ".google-play.ini"
    if exists:
        ini.write_text("[google]\n")
    credentials.warn_if_tracked_by_git(ini)
    err = capsys.readouterr().err
    assert "will be added to git" in err
    assert "add '.google-play.ini' to .gitignore" in err


@needs_git
def test_no_warning_when_ignored(repo, capsys):
    (repo / ".gitignore").write_text(".google-play.ini\n")
    (repo / ".google-play.ini").write_text("[google]\n")
    credentials.warn_if_tracked_by_git(repo / ".google-play.ini")
    assert capsys.readouterr().err == ""


@needs_git
def test_no_warning_when_ignored_by_a_parent_folder(repo, capsys):
    (repo / ".gitignore").write_text(".google-play.ini\n")
    (repo / "sub").mkdir()
    credentials.warn_if_tracked_by_git(repo / "sub" / ".google-play.ini")
    assert capsys.readouterr().err == ""


@needs_git
@pytest.mark.parametrize("also_ignored", [False, True], ids=["tracked", "tracked-and-ignored"])
def test_warns_when_already_tracked(repo, capsys, also_ignored):
    ini = repo / ".google-play.ini"
    ini.write_text("[google]\n")
    git(repo, "add", "--", ".google-play.ini")
    if also_ignored:  # ignoring a tracked file doesn't untrack it
        (repo / ".gitignore").write_text(".google-play.ini\n")
    credentials.warn_if_tracked_by_git(ini)
    err = capsys.readouterr().err
    assert "already in git" in err
    assert "rm --cached" in err


def test_reports_a_git_failure_that_isnt_not_a_repository(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(credentials.shutil, "which", lambda name: "git")
    monkeypatch.setattr(
        credentials, "_git",
        lambda folder, *args: subprocess.CompletedProcess(args, 128, "", "fatal: detected dubious ownership in repository\n"),
    )
    credentials.warn_if_tracked_by_git(tmp_path / ".google-play.ini")
    assert "could not check" in capsys.readouterr().err


def test_silent_without_git(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(credentials.shutil, "which", lambda name: None)
    credentials.warn_if_tracked_by_git(tmp_path / ".google-play.ini")
    assert capsys.readouterr().err == ""


# ------------------------------------------------------------------------------ google_play: sign-in ---
@pytest.fixture
def sign_in_fakes(monkeypatch):
    """Replaces the browser sign-in and the token check. Set `logins` to the answers login_works gives."""
    state = {"sign_ins": 0, "logins": [True]}

    def fake_sign_in(ini, email):
        state["sign_ins"] += 1
        ini.write_text("[google]\n")

    monkeypatch.setattr(google_play, "sign_in", fake_sign_in)
    monkeypatch.setattr(google_play, "login_works", lambda ini: state["logins"].pop(0))
    return state


def test_signs_in_when_there_are_no_credentials(tmp_path, sign_in_fakes):
    google_play.ensure_signed_in(tmp_path / ".google-play.ini", force=False, email=None)
    assert sign_in_fakes["sign_ins"] == 1


def test_reuses_working_credentials(tmp_path, sign_in_fakes):
    (tmp_path / ".google-play.ini").write_text("[google]\n")
    google_play.ensure_signed_in(tmp_path / ".google-play.ini", force=False, email=None)
    assert sign_in_fakes["sign_ins"] == 0


def test_signs_in_again_when_forced(tmp_path, sign_in_fakes):
    (tmp_path / ".google-play.ini").write_text("[google]\n")
    google_play.ensure_signed_in(tmp_path / ".google-play.ini", force=True, email=None)
    assert sign_in_fakes["sign_ins"] == 1


def test_signs_in_again_when_the_token_stopped_working(tmp_path, sign_in_fakes):
    (tmp_path / ".google-play.ini").write_text("[google]\n")
    sign_in_fakes["logins"] = [False, True]
    google_play.ensure_signed_in(tmp_path / ".google-play.ini", force=False, email=None)
    assert sign_in_fakes["sign_ins"] == 1


def test_gives_up_when_a_fresh_sign_in_is_refused(tmp_path, sign_in_fakes):
    (tmp_path / ".google-play.ini").write_text("[google]\n")
    sign_in_fakes["logins"] = [False, False]
    with pytest.raises(SystemExit, match="still refuses"):
        google_play.ensure_signed_in(tmp_path / ".google-play.ini", force=False, email=None)


@pytest.mark.parametrize(
    ("stdout", "returncode", "works"),
    [("Downloading x from Google Play...\n", 0, True),
     ("Could not log in to Google Play.  Please check your credentials and try again later.\n", 1, False)],
)
def test_login_works_reads_apkeeps_answer(monkeypatch, tmp_path, stdout, returncode, works):
    monkeypatch.setattr(google_play, "run_apkeep", lambda *a: subprocess.CompletedProcess(a, returncode, stdout, ""))
    assert google_play.login_works(tmp_path / ".google-play.ini") is works


# ----------------------------------------------------------------------------- google_play: download ---
class FakeApkeep:
    """Stands in for `apkeep -d google-play ... -a <app> <out>`: writes split APKs as apkeep does."""

    def __init__(self, version: str | None = "1.0", stdout: str = ""):
        self.version, self.stdout, self.calls = version, stdout, 0

    def __call__(self, *args: str) -> subprocess.CompletedProcess:
        self.calls += 1
        app_id, out = args[args.index("-a") + 1], Path(args[-1])
        if self.version is not None:
            make_apk(out / app_id / f"{app_id}.apk", self.version)
            with zipfile.ZipFile(out / app_id / f"{app_id}.config.arm64_v8a.apk", "w") as split:
                split.writestr("lib/arm64-v8a/x.so", b"")
        return subprocess.CompletedProcess(args, 0, self.stdout, "")


@pytest.fixture
def play(monkeypatch):
    def install(apkeep: FakeApkeep, store_version: str | None = None) -> FakeApkeep:
        monkeypatch.setattr(google_play, "run_apkeep", apkeep)
        monkeypatch.setattr(google_play, "current_version", lambda *a: store_version)
        return apkeep

    return install


def test_download_saves_under_app_and_version(tmp_path, play):
    play(FakeApkeep("2.4"))
    google_play.download("com.example.app", tmp_path, tmp_path / "ini", "au", "en")

    version_dir = tmp_path / "com.example.app" / "2.4"
    assert sorted(p.name for p in version_dir.iterdir()) == [
        "com.example.app.apk", "com.example.app.config.arm64_v8a.apk", "metadata.json",
    ]
    assert json.loads((version_dir / "metadata.json").read_text())["version"] == "2.4"
    assert [p.name for p in (tmp_path / "com.example.app").iterdir()] == ["2.4"], "no staging left behind"


def test_download_skips_without_downloading_when_the_store_version_is_present(tmp_path, play):
    (tmp_path / "com.example.app" / "2.4").mkdir(parents=True)
    apkeep = play(FakeApkeep("2.4"), store_version="2.4")
    google_play.download("com.example.app", tmp_path, tmp_path / "ini", "au", "en")
    assert apkeep.calls == 0


def test_download_discards_a_version_already_present(tmp_path, play, capsys):
    (tmp_path / "com.example.app" / "2.4").mkdir(parents=True)
    apkeep = play(FakeApkeep("2.4"), store_version=None)  # "Varies with device"
    google_play.download("com.example.app", tmp_path, tmp_path / "ini", "au", "en")
    assert apkeep.calls == 1
    assert "already downloaded" in capsys.readouterr().out
    assert [p.name for p in (tmp_path / "com.example.app").iterdir()] == ["2.4"]


def test_download_of_an_unknown_app_fails_and_leaves_nothing(tmp_path, play):
    play(FakeApkeep(version=None))
    with pytest.raises(SystemExit, match="has no app"):
        google_play.download("com.example.missing", tmp_path, tmp_path / "ini", "au", "en")
    assert not (tmp_path / "com.example.missing").exists()


def test_download_reports_a_refused_sign_in(tmp_path, play):
    play(FakeApkeep(version=None, stdout="Could not log in to Google Play.  Invalid payload.\n"))
    with pytest.raises(SystemExit, match="--sign-in"):
        google_play.download("com.example.app", tmp_path, tmp_path / "ini", "au", "en")


# ------------------------------------------------------------------------------- google_play: search ---
def test_search_prints_a_table(monkeypatch, capsys):
    import google_play_scraper

    results = [
        {"appId": "com.example.one", "title": "One", "developer": "Dev", "installs": "1,000+", "score": 4.25, "free": True},
        {"appId": None, "title": "No ID"},  # google-play-scraper sometimes returns these; they're dropped
        {"appId": "com.example.two", "title": "Two " * 20, "developer": "D", "installs": "", "score": None,
         "free": False, "price": 2.99, "currency": "AUD"},
    ]
    monkeypatch.setattr(google_play_scraper, "search", lambda *a, **k: results)
    google_play.search("x", 10, "au", "en")

    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["App", "ID", "Title", "Developer", "Installs", "Rating", "Price"]
    assert "com.example.one" in lines[2] and "4.2" in lines[2]
    assert "2.99 AUD" in lines[3] and "…" in lines[3]
    assert len(lines) == 4
    assert all(line == line.rstrip() for line in lines)


@pytest.mark.parametrize(("listed", "expected"), [("3.3.0", "3.3.0"), ("Varies with device", None), (None, None)])
def test_current_version_ignores_non_versions(monkeypatch, listed, expected):
    import google_play_scraper

    monkeypatch.setattr(google_play_scraper, "app", lambda *a, **k: {"version": listed})
    assert google_play.current_version("com.example.app", "au", "en") == expected


# ------------------------------------------------------------------------------------ location.py ---
class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def read(self, size=-1):
        return self.body[:size] if size >= 0 else self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


TRACE = b"fl=1f\nh=www.cloudflare.com\nip=203.0.113.9\nts=1.2\nloc=%s\ntls=TLSv1.3\nwarp=off\n"


@pytest.mark.parametrize(
    ("cloudflare", "ipinfo", "expected"),
    [
        (TRACE % b"VC", b"AU\n", "vc"),  # Cloudflare first
        (TRACE % b"XX", b"AU\n", "au"),  # Cloudflare doesn't know: ipinfo
        (TRACE % b"T1", b"AU\n", "au"),  # Tor: ipinfo
        (b"<html>blocked</html>", b"AU", "au"),  # not a trace: ipinfo
        (None, b"VC\n", "vc"),  # Cloudflare unreachable: ipinfo
        (None, b"<html>", None),
        (None, None, None),
    ],
)
def test_country_from_ip_asks_cloudflare_then_ipinfo(monkeypatch, cloudflare, ipinfo, expected):
    answers = {location.CLOUDFLARE_TRACE_URL: cloudflare, location.IPINFO_COUNTRY_URL: ipinfo}

    def urlopen(request, timeout):
        body = answers[request.full_url]
        if body is None:
            raise OSError("unreachable")
        return FakeResponse(body)

    monkeypatch.setattr(location.urllib.request, "urlopen", urlopen)
    assert location.country_from_ip() == expected


def test_country_from_ip_is_none_when_offline(monkeypatch):
    def offline(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr(location.urllib.request, "urlopen", offline)
    assert location.country_from_ip() is None


@pytest.mark.parametrize(
    ("env", "expected"),
    [({"LANG": "en_AU.UTF-8"}, "au"), ({"LC_ALL": "fr_CA", "LANG": "en_US.UTF-8"}, "ca"), ({"LANG": "C.UTF-8"}, None), ({}, None)],
)
def test_country_from_os_reads_the_posix_locale(monkeypatch, env, expected):
    monkeypatch.setattr(location.sys, "platform", "linux")
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(variable, raising=False)
    for variable, value in env.items():
        monkeypatch.setenv(variable, value)
    assert location.country_from_os() == expected


@pytest.mark.parametrize(
    ("ip", "os_region", "expected"),
    [("vc", "au", ("vc", "your IP address")), (None, "au", ("au", "your system's region setting"))],
)
def test_detect_country_prefers_the_ip_address(monkeypatch, ip, os_region, expected):
    monkeypatch.setattr(location, "country_from_ip", lambda: ip)
    monkeypatch.setattr(location, "country_from_os", lambda: os_region)
    assert location.detect_country() == expected


def test_detect_country_falls_back_to_us(monkeypatch):
    monkeypatch.setattr(location, "country_from_ip", lambda: None)
    monkeypatch.setattr(location, "country_from_os", lambda: None)
    assert location.detect_country()[0] == "us"


@pytest.mark.parametrize(("value", "expected"), [("au", "au"), ("VC", "vc")])
def test_country_arg_accepts_two_letters(value, expected):
    assert cli.country_arg(value) == expected


@pytest.mark.parametrize("value", ["aus", "a", "1a", "a-"])
def test_country_arg_rejects_anything_else(value):
    with pytest.raises(Exception):
        cli.country_arg(value)


@pytest.fixture
def quiet_cli(monkeypatch, tmp_path):
    """Runs cli.main with git, sign-in and search replaced; returns the countries search was called with."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "warn_if_tracked_by_git", lambda ini: None)
    monkeypatch.setattr(cli, "ensure_signed_in", lambda *a: None)
    seen = []
    monkeypatch.setattr(cli, "search", lambda term, limit, country, lang: seen.append(country))
    return seen


def test_cli_logs_the_detected_country(monkeypatch, quiet_cli, capsys):
    monkeypatch.setattr(cli, "detect_country", lambda: ("vc", "your IP address"))
    cli.main(["search", "firefox"])
    assert quiet_cli == ["vc"]
    assert "Determined Google Play country 'vc' from your IP address" in capsys.readouterr().err


def test_cli_logs_an_overridden_country_without_detecting(monkeypatch, quiet_cli, capsys):
    monkeypatch.setattr(cli, "detect_country", lambda: pytest.fail("detected despite --country"))
    cli.main(["--country", "AU", "search", "firefox"])
    assert quiet_cli == ["au"]
    assert "Using Google Play country 'au' (from --country)" in capsys.readouterr().err


# --------------------------------------------------------------------------------------- live checks ---
@pytest.mark.network
@pytest.mark.real_location
def test_live_country_from_ip_is_a_country_code():
    country = location.country_from_ip()
    assert country is None or re.fullmatch(r"[a-z]{2}", country)


@pytest.mark.network
def test_live_search_finds_firefox(capsys):
    google_play.search("firefox", 10, "au", "en")
    assert "org.mozilla.firefox" in capsys.readouterr().out


@pytest.mark.network
def test_live_current_version_is_a_version():
    version = google_play.current_version("org.mozilla.firefox", "au", "en")
    assert version is None or version[0].isdigit()
