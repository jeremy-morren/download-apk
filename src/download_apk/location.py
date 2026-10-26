"""Which Google Play country to use when --country isn't given."""

import os
import re
import sys
import urllib.request

# Cloudflare's trace endpoint reports where Cloudflare places the caller's IP address, as `loc=VC`. It's
# a troubleshooting endpoint that Cloudflare doesn't formally document, so ipinfo is the backup: it
# answers with just the code, e.g. "VC\n". Neither needs a key.
CLOUDFLARE_TRACE_URL = "https://www.cloudflare.com/cdn-cgi/trace"
IPINFO_COUNTRY_URL = "https://ipinfo.io/country"
FALLBACK_COUNTRY = "us"

_COUNTRY_CODE = re.compile(r"^[A-Za-z]{2}$")
_UNKNOWN_COUNTRIES = {"xx"}  # Cloudflare's "unknown"; Tor is "T1", which isn't two letters anyway
# A POSIX locale such as en_AU.UTF-8: language, then the territory.
_LOCALE_TERRITORY = re.compile(r"^[A-Za-z]{2,3}_(?P<territory>[A-Za-z]{2})(?:[.@]|$)")


def detect_country() -> tuple[str, str]:
    """The country to use and where it came from: the IP address (where Google Play will place you),
    else the operating system's region setting, else a fallback."""
    if country := country_from_ip():
        return country, "your IP address"
    if country := country_from_os():
        return country, "your system's region setting"
    return FALLBACK_COUNTRY, "a fallback, since neither your IP address nor your system said"


def country_from_ip(timeout: float = 3) -> str | None:
    """Cloudflare's answer, else ipinfo's; None when neither answers with a country."""
    trace = _fetch(CLOUDFLARE_TRACE_URL, timeout)
    if trace:
        fields = dict(line.split("=", 1) for line in trace.splitlines() if "=" in line)
        if country := _country(fields.get("loc", "")):
            return country
    return _country(_fetch(IPINFO_COUNTRY_URL, timeout) or "")


def country_from_os() -> str | None:
    if sys.platform == "win32":
        return _windows_region()
    for variable in ("LC_ALL", "LC_MESSAGES", "LANG"):
        match = _LOCALE_TERRITORY.match(os.environ.get(variable, ""))
        if match:
            return match["territory"].lower()
    return None


def _country(code: str) -> str | None:
    code = code.strip()
    return code.lower() if _COUNTRY_CODE.match(code) and code.lower() not in _UNKNOWN_COUNTRIES else None


def _fetch(url: str, timeout: float) -> str | None:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "download-apk"})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read(4096).decode("ascii", errors="replace")
    except (OSError, ValueError):  # offline, blocked, timed out (URLError and timeouts are OSErrors)
        return None


def _windows_region() -> str | None:
    """Settings > Time & language > Region > Country or region, as a two-letter code (Windows 10 1709+)."""
    import ctypes

    try:
        buffer = ctypes.create_unicode_buffer(16)
        if not ctypes.windll.kernel32.GetUserDefaultGeoName(buffer, len(buffer)):
            return None
    except (AttributeError, OSError):
        return None
    return _country(buffer.value)
