"""Reading APKs and recording what was downloaded."""

import hashlib
import json
import re
import struct
import zipfile
from datetime import datetime, timezone
from pathlib import Path


def safe_dir_name(version: str) -> str:
    return re.sub(r"[^A-Za-z0-9._\-]", "_", version)


def write_metadata(version_dir: Path, app_id: str, version: str) -> None:
    files = {
        p.relative_to(version_dir).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(version_dir.rglob("*"))
        if p.is_file()
    }
    metadata = {
        "package": app_id,
        "version": version,
        "source": "google-play",
        "downloaded": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sha256": files,
    }
    (version_dir / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")


def first_version_name(folder: Path) -> str | None:
    return next(filter(None, map(apk_version_name, sorted(folder.rglob("*.apk")))), None)


def apk_version_name(apk: Path) -> str | None:
    """Read android:versionName from an APK's binary AndroidManifest.xml."""
    with zipfile.ZipFile(apk) as archive:
        try:
            data = archive.read("AndroidManifest.xml")
        except KeyError:
            return None

    strings: list[str] = []
    resource_ids: list[int] = []
    offset = struct.unpack_from("<H", data, 2)[0]  # skip the RES_XML_TYPE file header
    while offset + 8 <= len(data):
        chunk_type, header_size, chunk_size = struct.unpack_from("<HHI", data, offset)
        if chunk_type == 0x0001:  # string pool
            count, _, flags, strings_start = struct.unpack_from("<IIII", data, offset + 8)
            utf8 = bool(flags & 0x100)
            for i in range(count):
                pos = offset + strings_start + struct.unpack_from("<I", data, offset + header_size + 4 * i)[0]
                strings.append(_read_pool_string(data, pos, utf8))
        elif chunk_type == 0x0180:  # resource ids of attribute names
            resource_ids = list(struct.unpack_from(f"<{(chunk_size - header_size) // 4}I", data, offset + header_size))
        elif chunk_type == 0x0102:  # start element; the first one is <manifest>
            attr_start, attr_size, attr_count = struct.unpack_from("<HHH", data, offset + header_size + 8)
            for i in range(attr_count):
                pos = offset + header_size + attr_start + i * attr_size
                _, name, raw_value, _, _, data_type, value = struct.unpack_from("<IIIHBBI", data, pos)
                is_version_name = (name < len(strings) and strings[name] == "versionName") or (
                    name < len(resource_ids) and resource_ids[name] == 0x0101021C
                )
                if is_version_name:
                    index = raw_value if raw_value != 0xFFFFFFFF else value if data_type == 0x03 else None
                    return strings[index] if index is not None else None
            return None
        offset += chunk_size
    return None


def _read_pool_string(data: bytes, pos: int, utf8: bool) -> str:
    if utf8:
        pos += 2 if data[pos] & 0x80 else 1  # UTF-16 length, unused
        length = data[pos]
        if length & 0x80:
            length = ((length & 0x7F) << 8) | data[pos + 1]
            pos += 1
        return data[pos + 1 : pos + 1 + length].decode("utf-8", errors="replace")
    length = struct.unpack_from("<H", data, pos)[0]
    if length & 0x8000:
        length = ((length & 0x7FFF) << 16) | struct.unpack_from("<H", data, pos + 2)[0]
        pos += 2
    return data[pos + 2 : pos + 2 + 2 * length].decode("utf-16-le", errors="replace")
