"""Build hooks: repository links in the package metadata, and file dates from the git commit.

Hatchling runs both while building (see [tool.hatch.*.hooks.custom] in pyproject.toml). Both read git:
the repository from the `origin` remote, and the time of the last commit.

The metadata hook fills in the two fields pyproject.toml marks dynamic:

- urls: links to the repository, pinned to the release's tag v<version>, so the wheel points at exactly
  the code and docs it was built from. PyPI shows them in the sidebar.
- readme: README.md with relative links made absolute at that tag. PyPI shows the README as the project
  description but has no repository to resolve a link like [MIT](LICENSE) against.

The build hook dates every file in the wheel and source tarball with the last commit's time, through
SOURCE_DATE_EPOCH, which hatchling reads when it writes the archives. An explicitly set
SOURCE_DATE_EPOCH wins.

A source tarball has no git history, so the sdist build records what it read from git in GIT_INFO_FILE;
a wheel built from the tarball, as `python -m build` does, then matches one built from a checkout.
Without git or an `origin` remote, the build still works: without links, and dated 2020-02-02.
The links follow GitHub's layout (/tree/, /blob/, /releases/tag/, /issues), so on other hosts some
may not resolve.
See "Reproducible builds" in README.md.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from hatchling.metadata.plugin.interface import MetadataHookInterface

# What the sdist build read from git, at the tarball's root.
GIT_INFO_FILE = ".git-info.json"

# Git's scp-like remote syntax, [user@]host:path: a colon before the first slash, and no "://". The host
# must be longer than one character, which tells it apart from a Windows drive letter (C:/repos/x).
SCP_LIKE_REMOTE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^@/:]{2,}):(?P<path>.*)$")

# A Markdown link or image target that is relative: not a URL, an in-page anchor, or a mailto.
RELATIVE_LINK = re.compile(r"(\]\()(?![a-zA-Z][a-zA-Z0-9+.-]*:|#|/)([^)\s]+)(\))")


def repository_url(remote_url: str) -> str | None:
    """The repository's web address, https://<host>/<path>, for a remote URL (HTTPS, SSH or scp-like) on
    any host; None for a local path or anything else without a host and at least <owner>/<repo>.

    urlsplit's hostname never includes a user name or password, so credentials in the URL are dropped.
    An HTTPS remote keeps its port; an SSH port is dropped, since the web server doesn't use it.
    """
    url = remote_url.strip()
    scp_like = SCP_LIKE_REMOTE.match(url) if "://" not in url else None
    if scp_like:
        url = f"ssh://{scp_like['host']}/{scp_like['path']}"
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http", "ssh", "git") or not parts.hostname:
        return None
    path = parts.path.strip("/").removesuffix(".git")
    if path.count("/") < 1 or "" in path.split("/"):
        return None
    host = parts.hostname
    if parts.scheme in ("https", "http") and parts.port:
        host = f"{host}:{parts.port}"
    return f"https://{host}/{path}"


def absolute_links(markdown: str, base: str) -> str:
    """Rewrites relative Markdown link targets to start with `base`."""
    return RELATIVE_LINK.sub(lambda m: f"{m.group(1)}{base}/{m.group(2)}{m.group(3)}", markdown)


def release_urls(repository: str, version: str) -> dict[str, str]:
    tag = f"v{version}"
    return {
        "Homepage": repository,
        "Source": f"{repository}/tree/{tag}",
        "Documentation": f"{repository}/blob/{tag}/README.md",
        "Changelog": f"{repository}/releases/tag/{tag}",
        "Issues": f"{repository}/issues",
    }


def _git(root: Path, *args: str) -> str | None:
    """Output of a git command run in root, or None if git is missing, fails, or root isn't the top of a
    repository (an sdist unpacked inside some other repository must not pick up that one's details)."""
    if not (root / ".git").exists():
        return None
    try:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, check=False)
    except OSError:
        return None
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else None


def git_info(root: Path) -> dict:
    """What the build takes from git: from the repository at root, else from GIT_INFO_FILE."""
    if (root / ".git").exists():
        commit_time = _git(root, "log", "-1", "--format=%ct")
        remote = _git(root, "remote", "get-url", "origin")
        return {
            "commit_time": int(commit_time) if commit_time and commit_time.isdigit() else None,
            "repository": repository_url(remote) if remote else None,
        }
    recorded = root / GIT_INFO_FILE
    if recorded.is_file():
        return json.loads(recorded.read_text(encoding="utf-8"))
    return {"commit_time": None, "repository": None}


class CustomMetadataHook(MetadataHookInterface):
    def update(self, metadata: dict) -> None:
        version = metadata["version"]
        repository = git_info(Path(self.root))["repository"]
        readme = (Path(self.root) / "README.md").read_text(encoding="utf-8")
        if repository is None:
            print("hatch_build: no `origin` remote with a host; building without repository links", file=sys.stderr)
            metadata["readme"] = {"content-type": "text/markdown", "text": readme}
            metadata["urls"] = {}
            return
        metadata["readme"] = {
            "content-type": "text/markdown",
            "text": absolute_links(readme, f"{repository}/blob/v{version}"),
        }
        metadata["urls"] = release_urls(repository, version)


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        info = git_info(Path(self.root))
        epoch = os.environ.get("SOURCE_DATE_EPOCH") or (str(info["commit_time"]) if info["commit_time"] else None)
        if epoch:
            # Hatchling reads it when it creates the archive, after this hook. The build runs in its own
            # process, so this changes nothing outside it. Without it, hatchling uses 2020-02-02.
            os.environ["SOURCE_DATE_EPOCH"] = epoch
        if self.target_name == "sdist":
            recorded = Path(tempfile.mkdtemp()) / GIT_INFO_FILE
            # Fixed key order and LF line endings, so the tarball's bytes don't depend on the OS.
            recorded.write_bytes(json.dumps(info, sort_keys=True).encode("utf-8") + b"\n")
            build_data["force_include"][str(recorded)] = GIT_INFO_FILE
