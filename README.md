# download-apk

Searches Google Play and download an app's APKs from the command line.

```
download-apk search firefox
download-apk download org.mozilla.firefox
```

Each download goes to `apps/<appId>/<version>/` and holds the base APK, its split APKs, and a `metadata.json` with each file's SHA-256. Running a download again picks up a new release. Google Play does not allow downloading historic versions, so only the current version can be downloaded.

## Requirements

- Python 3.10 or later, on Windows, macOS or Linux (x64 or arm64).
- Google Chrome, for the one-time sign-in.
- A Google account. Use a spare one: Google may flag accounts that download apps outside a phone.
- Optionally, [jadx](https://github.com/skylot/jadx) on your `PATH`, for `--decompile`.

## Install

```
pip install download-apk
```

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

`--limit` sets how many results to show (default 20).

### Download

```
download-apk download com.example.app
```

```
Downloading com.example.app from Google Play...
Saved com.example.app 1.4.2 to /home/me/apks/apps/com.example.app/1.4.2
```

To save somewhere other than `./apps`, pass `--out FOLDER`, or set `DOWNLOAD_APK_DIR` for every run.

Google Play only serves an app's current version. Paid apps download only if the account owns them.

### Decompile

```
download-apk download com.example.app --decompile
```

After downloading (or finding the version already downloaded), this runs [jadx](https://github.com/skylot/jadx) on the APKs and writes Java sources and decoded resources to `apps/<appId>/decompiled/<version>/`. jadx must be on your `PATH`, but only when you pass `--decompile`: the command then checks for it before signing in or downloading. A version that is already decompiled is skipped. jadx often can't decompile every method of a large app: it says so, and the rest of its output is kept.

### Options

| Option            | Default              | What it does                                     |
|-------------------|----------------------|--------------------------------------------------|
| `--ini PATH`      | `./.google-play.ini` | Where the sign-in token is saved                 |
| `--sign-in`       |                      | Sign in again, replacing the saved token         |
| `--email ADDRESS` | asked for            | The Google account's email, used when signing in |
| `--country CODE`  | detected             | The store country, e.g. `us`                     |
| `--lang CODE`     | detected             | The store language, e.g. `en` or `en-GB`         |

The country is detected from your IP address (through Cloudflare, or ipinfo.io as a fallback), and the language from your system's display language. Each run prints what it picked.

## Signing in

The first time you run a command, Chrome opens on Google's sign-in page. Sign in, and the window closes by itself; then type the account's email when asked. A token is saved to `.google-play.ini`, and later runs reuse it. If Google stops accepting it, the sign-in runs again.

`.google-play.ini` works like a password for the account, so keep it out of git: add it to `.gitignore`. download-apk warns you if the file is tracked by git, or would be on the next `git add`.

## Troubleshooting

- **"Google Play has no app …, or doesn't offer it to this account."** Check the app ID with `search`. Some apps are limited to certain countries or devices.
- **"Google Play refused the saved sign-in."** Run again with `--sign-in`.
- **Chrome doesn't open.** Install Google Chrome.

## Develop

```
git clone https://github.com/jeremy-morren/download-apk
cd download-apk
python -m venv .venv
source .venv/bin/activate                              # Windows: .venv\Scripts\activate
DOWNLOAD_APK_SKIP_APKEEP=1 pip install -e . --group test
pytest
```

The tests run offline and don't need apkeep. To run real downloads from a checkout, install without `DOWNLOAD_APK_SKIP_APKEEP`: that compiles apkeep, which needs [Rust](https://rustup.rs).

## Acknowledgements

This tool uses the EFF's [apkeep](https://github.com/EFForg/apkeep) to download from Google Play and [google-play-scraper](https://github.com/JoMingyu/google-play-scraper) to search.

## License

This project is available under the [MIT](https://github.com/jeremy-morren/download-apk/blob/main/LICENSE) license.
