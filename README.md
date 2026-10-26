# download-apk

Search Google Play and download an app's APKs into versioned folders, from the command line.

```
download-apk search "firefox"
download-apk download org.mozilla.firefox
```

Downloads land in `apps/<appId>/<version>/`: the base APK, its split APKs, and a `metadata.json` with
each file's SHA-256. Re-running a download picks up new releases and skips versions you already have.

It uses [apkeep](https://github.com/EFForg/apkeep) from the EFF to talk to Google Play, and
[google-play-scraper](https://github.com/JoMingyu/google-play-scraper) for search.

## Requirements

- Python 3.10 or later.
- Google Chrome, for the one-time sign-in.
- A Google account. Use a spare one: Google may flag accounts that download apps outside a phone.
- Windows (x64) or Linux (x64 or arm64). apkeep publishes no macOS build.

## Install

```
pip install download-apk
```

This adds a `download-apk` command. To keep it out of your other Python environments, install it
as a tool instead:

```
pipx install download-apk
uv tool install download-apk
```

To upgrade: `pip install --upgrade download-apk`, `pipx upgrade download-apk` or
`uv tool upgrade download-apk`. To run it once without installing: `uvx download-apk search firefox`.

Other ways in:

- From GitHub's newest release, without PyPI:
  `pip install https://github.com/jeremy-morren/download-apk/releases/latest/download/download-apk.tar.gz`.
- A specific release: `pip install download-apk==0.1.0`, or the wheel attached to that release on
  GitHub.
- The newest commit on `main`: `pip install git+https://github.com/jeremy-morren/download-apk`.

## Use

### Search

```
download-apk search firefox --limit 3
```

```
App ID                    Title                           Developer  Installs     Rating  Price
------------------------  ------------------------------  ---------  -----------  ------  -----
org.mozilla.focus         Firefox Focus: No Fuss Browser  Mozilla    10,000,000+  4.7     Free
org.mozilla.firefox_beta  Firefox Beta for Testers        Mozilla    10,000,000+  4.6     Free
org.mozilla.fenix         Firefox Nightly for Developers  Mozilla    1,000,000+   4.5     Free
```

`--limit` sets the number of results (default 20).

### Download

```
download-apk download com.example.app
```

```
Downloading com.example.app from Google Play...
Saved com.example.app 1.4.2 to /home/me/apks/apps/com.example.app/1.4.2
```

`-o`/`--out` changes the output folder for one run. To change it for every run, set the
`DOWNLOAD_APK_DIR` environment variable; `--out` still wins over it. Without either, apps go to
`./apps`. Google Play only serves an app's current
version, so older versions can't be downloaded. Paid apps download only if the account owns them.

### Options for every command

| Option            | Default              | What it does                                     |
|-------------------|----------------------|--------------------------------------------------|
| `--ini PATH`      | `./.google-play.ini` | Where the Google Play credentials are kept       |
| `--sign-in`       |                      | Sign in again, replacing the saved credentials   |
| `--email ADDRESS` | asked for            | The Google account's email, used when signing in |
| `--country CODE`  | detected             | The store country for search and version lookups |
| `--lang CODE`     | `en`                 | The store language                               |

Without `--country`, the country is where your IP address is, which is also where Google Play places
you. It's looked up from Cloudflare's trace endpoint (`https://www.cloudflare.com/cdn-cgi/trace`), or
from `https://ipinfo.io/country` if Cloudflare doesn't answer, so those services see your IP address.
Offline, it falls back to your system's region setting, then to `us`. Every run logs which it used:

```
Determined Google Play country 'vc' from your IP address; use --country to override.
```

## Signing in

Every command first makes sure you're signed in to Google Play:

1. With no saved credentials, Chrome opens on Google's sign-in page. Sign in; the window closes by
   itself. Then type the account's email when asked.
2. The one-time sign-in cookie is exchanged for a long-lived token, saved in `.google-play.ini` in the
   current folder (or wherever `--ini` points).
3. On later runs the token is checked first. If Google no longer accepts it, the browser sign-in
   runs again.

Downloading accepts the Google Play terms of service for the account.

### Keep the credentials out of git

`.google-play.ini` holds a token that works like a password for the Google account. On every run,
download-apk checks whether that file is in a git repository and warns you:

- **"credentials are already in git"**: the file is tracked. Remove it from the index with
  `git rm --cached .google-play.ini`, add it to `.gitignore`, and sign in again with `--sign-in` so
  the token in the repository's history stops being the current one.
- **"credentials will be added to git"**: the file isn't ignored, so the next `git add .` would
  commit it. Add `.google-play.ini` to `.gitignore`.

Outside a git repository, or without git installed, nothing is checked.

## Where files go

| What                    | Where                                                                                                                                                |
|-------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| Downloaded apps         | `./apps/<appId>/<version>/`, or `--out`, or `$DOWNLOAD_APK_DIR`                                                                                      |
| Google Play credentials | `./.google-play.ini`, or `--ini`                                                                                                                     |
| The apkeep program      | Your user cache folder, e.g. `%LOCALAPPDATA%\download-apk\Cache\tools` on Windows or `~/.cache/download-apk/tools` on Linux, downloaded on first use |

## Develop

```
git clone https://github.com/jeremy-morren/download-apk
cd download-apk
python -m venv .venv
.venv/Scripts/activate            # Windows; on Linux: source .venv/bin/activate
pip install -e . --group test     # pip 25.1 or later
pytest
```

The tests run offline: Google Play, sign-in and apkeep are replaced with fakes, and the APKs are
generated. `pytest -m network` runs the few checks that talk to the real Google Play.

You can also run the command without installing it: `python -m download_apk ...` from the `src`
folder.

### Releasing

GitHub Actions tests every push and pull request on Windows and Linux, with the oldest and newest
supported Python, and builds the package on each of those four machines. The builds must be
byte-identical. Then, for a push to `main`:

1. Raise `version` in `pyproject.toml` and `__version__` in `src/download_apk/__init__.py`; a test
   fails if they differ.
2. Push to `main`. `ci.yml` creates a draft release `v<version>` with two files: the wheel, and the
   source tarball as `download-apk.tar.gz`. If that release already exists it does nothing; if only a
   tag is left from a deleted release, it deletes the tag first.
3. Review the draft on GitHub and publish it. That creates the tag, and `release.yml` rebuilds the
   wheel from the tag, checks it is byte-identical to the release's wheel, and uploads it to PyPI.

PyPI accepts the upload through trusted publishing: its publisher settings for `download-apk` name
this repository, the workflow `release.yml` and the GitHub environment `pypi`, so no PyPI token is
stored anywhere. A version can be uploaded to PyPI only once, even if it is later deleted there.

### Reproducible builds

Building the same commit on Linux gives byte-identical files, so anyone can check a release against
its source:

```
git clone https://github.com/jeremy-morren/download-apk && cd download-apk && git checkout v0.1.0
python -m build
sha256sum dist/*       # compare with the SHA-256 values in the release notes
```

- `hatch_build.py` dates every file with the commit's time (through `SOURCE_DATE_EPOCH`, which wins if
  you set it yourself) and reads the repository links from the `origin` remote. The source tarball
  records both in `.git-info.json`, so a wheel built from the tarball, as `python -m build` does,
  matches one built from a checkout.
- hatchling, the build backend, writes files in a fixed order with fixed permissions. `pyproject.toml`
  pins it to one version, since another version may lay out the files differently.
- `.gitattributes` keeps every text file LF in every checkout; a single CRLF file changes the wheel.

Windows builds hold the same files with the same contents, but not the same bytes: Python's zip
writer records the building OS in each entry, and Python 3.14 on Windows compresses with zlib-ng
rather than zlib. On Windows, compare contents instead: the `RECORD` file inside the wheel lists every
file's SHA-256, and it's identical on every OS.

## Troubleshooting

- **"Google Play has no app …, or doesn't offer it to this account."** Check the app ID with
  `search`. Some apps are limited to certain countries or devices.
- **"Google Play refused the saved sign-in."** Run again with `--sign-in`.
- **Chrome doesn't open.** Install Google Chrome. Sign-in drives your installed Chrome through
  [patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python); no separate browser
  download is needed.

## License

[MIT](LICENSE).
