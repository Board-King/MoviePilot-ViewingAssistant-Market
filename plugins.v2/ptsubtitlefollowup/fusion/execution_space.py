"""Bounded, read-only disk headroom checks for MP v2.15.6 movie and TV downloads.

DirectoryHelper.get_dir and DownloadChain._resolve_media_download_dir select
the configured root. _DownloaderBase.normalize_path maps full path prefixes.
The administrator attests that these namespaces share backing storage and no
download event replaces the path. statvfs reports currently available bytes,
including aggregate space for Unraid shfs. There is no allocation/reservation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from urllib.parse import urlsplit


MINIMUM_FREE_BYTES = 100 * 1024**3
_LOCAL_FS = {"xfs", "ext4", "ext3", "btrfs", "zfs", "f2fs", "bcachefs", "fuse.shfs"}
_SYSTEM = {"config", "app", "bin", "boot", "dev", "etc", "proc", "root", "run", "sbin", "sys", "tmp", "usr", "var"}


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _path(value):
    if not isinstance(value, str) or not 1 < len(value) <= 1024 or not value.isprintable():
        raise ValueError
    if (not value.startswith("/") or "//" in value or "\\" in value or ":" in value or value != value.strip()
            or any(c in (".", "..") for c in value.split("/"))):
        raise ValueError
    path = PurePosixPath(value)
    if len(path.parts) < 2 or len(path.parts) > 64 or path.parts[1] in _SYSTEM:
        raise ValueError
    return path


def _mounts():
    with open("/proc/self/mountinfo", "rb") as stream:
        raw = stream.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError

    def unescape(field):
        return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), field)

    mounts = []
    for line in raw.decode("utf-8").splitlines():
        left, right = line.split(" - ", 1)
        fields, fs = left.split(), right.split()
        mounts.append((PurePosixPath(unescape(fields[4])), fs[0], fields[2], unescape(fields[3]),
                       unescape(fs[1]), fields[5].split(",")))
    return mounts


def _volume(path, mounts):
    path = Path(path)
    for entry in (path, *path.parents):
        status = entry.lstat()
        if not stat.S_ISDIR(status.st_mode):
            raise ValueError
    device = path.stat().st_dev
    if device == Path("/").stat().st_dev:
        raise ValueError
    matches = [m for m in mounts if path.is_relative_to(m[0])]
    if not matches:
        raise ValueError
    deepest = max(len(m[0].parts) for m in matches)
    deepest_mounts = [m for m in matches if len(m[0].parts) == deepest]
    if len(deepest_mounts) != 1 or any(m[0] != path and m[0].is_relative_to(path) for m in mounts):
        raise ValueError
    mount, fs, devnum, fsroot, source, options = deepest_mounts[0]
    if mount == PurePosixPath("/") or fs not in _LOCAL_FS or "rw" not in options:
        raise ValueError
    if devnum != f"{os.major(device)}:{os.minor(device)}":
        raise ValueError
    counters = os.statvfs(path)
    if counters.f_flag & os.ST_RDONLY or counters.f_frsize <= 0 or counters.f_bavail < 0:
        raise ValueError
    return {"mount": _digest([str(mount), fs, devnum, fsroot, source]),
            "available_bytes": counters.f_frsize * counters.f_bavail}


def _downloader(row, roots):
    name, kind, settings = row.get("name"), row.get("type"), row.get("config")
    if (not isinstance(name, str) or not name or len(name) > 128 or not name.isprintable()
            or kind not in ("qbittorrent", "transmission") or not isinstance(settings, dict)):
        raise ValueError
    host = settings.get("host")
    if not isinstance(host, str) or not 0 < len(host) <= 512 or not host.isprintable():
        raise ValueError
    parsed = urlsplit(host if "://" in host else "http://" + host)
    if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError
    port = settings.get("port")
    if port not in (None, "") and not re.fullmatch(r"[0-9]{1,5}", str(port)):
        raise ValueError
    if port not in (None, "") and not 1 <= int(port) <= 65535:
        raise ValueError
    mappings = row.get("path_mapping") or []
    if not isinstance(mappings, list) or len(mappings) > 32:
        raise ValueError
    pairs = []
    for pair in mappings:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ValueError
        pairs.append((_path(pair[0]), _path(pair[1])))
    effective = []
    for root in roots:
        # Multiple applicable prefixes, or a different mapping inside a download
        # destination, cannot be independently verified at subscription time.
        applicable = [(a, b) for a, b in pairs if root.is_relative_to(a)]
        if len(applicable) > 1 or any(a != root and a.is_relative_to(root) for a, _ in pairs):
            raise ValueError
        mapped = applicable[0][1] / root.relative_to(applicable[0][0]) if applicable else root
        effective.append([str(root), str(mapped)])
    if len({pair[1] for pair in effective}) != len(effective):
        raise ValueError
    # Store only the final digest. Usernames, passwords and general connection
    # options do not enter the fingerprint or the public result.
    return [name, kind, parsed.scheme, parsed.hostname, parsed.port, str(port or ""),
            parsed.path, settings.get("protocol"), effective]


def _possible_rows(rows, label):
    """First match per category, plus the first wildcard for every other one."""
    result, categories = [], set()
    for row in rows:
        media_type, category = row.get("media_type"), row.get("media_category")
        if media_type not in (None, "", label):
            continue
        # v2.15.6 checks a blank media_type before its category conditions.
        if not media_type or not category:
            result.append(row)
            return result, True
        if not isinstance(category, str):
            raise ValueError
        if category not in categories:
            result.append(row)
            categories.add(category)
    return result, False


def _destinations(directories, template, label):
    if not isinstance(directories, list) or not 1 <= len(directories) <= 100:
        raise ValueError
    if not all(isinstance(row, dict) for row in directories):
        raise ValueError
    forced = template.get("save_path")
    if forced not in (None, ""):
        forced = _path(forced[6:] if isinstance(forced, str) and forced.startswith("local:") else forced)
    else:
        forced = None
    roots = set()
    if forced is not None:
        # Native explicit-path validation checks configured roots, independently
        # of get_dir's implicit selection. Only exact roots inherit classification.
        containing = []
        for row in directories:
            if row.get("storage") != "local" or row.get("media_type") not in (None, "", label) or not row.get("download_path"):
                continue
            base = _path(row["download_path"])
            if forced.is_relative_to(base):
                containing.append((base, row))
        if not containing:
            raise ValueError
        exact = [row for base, row in containing if base == forced]
        # get_download_dir_by_save_path (unlike get_dir) scans by priority.
        if any(type(row.get("priority", 0)) is not int for row in exact):
            raise ValueError
        rows, covers_all = _possible_rows(sorted(exact, key=lambda row: row.get("priority", 0)), label)
        if not covers_all:
            roots.add(forced)
    else:
        # The native call is get_dir(media, include_unsorted=True). An unmonitored
        # movie directory remains eligible. Stored order, not priority, wins.
        rows, _ = _possible_rows(directories, label)
    for row in rows:
        if row.get("storage") != "local":
            raise ValueError
        base = _path(row.get("download_path"))
        for key in ("download_type_folder", "download_category_folder"):
            if row.get(key) is not None and type(row[key]) is not bool:
                raise ValueError
        if row.get("download_category_folder") and not row.get("media_category"):
            # Category is assigned after host recognition. Do not scan user data
            # to guess which directory, symlink or filesystem it may resolve to.
            raise ValueError
        if row.get("download_type_folder") and not row.get("media_type"):
            base /= label
        roots.add(base)
    if not 1 <= len(roots) <= 32:
        raise ValueError
    return sorted(roots)


def check_movie_download_space(directories, downloaders, template, *, storage_verified, probe=None, media_type="movie"):
    result = {"status": "unknown", "fingerprint": None, "locations_checked": 0,
              "minimum_free_bytes": MINIMUM_FREE_BYTES, "available_bytes": None}
    if storage_verified is not True:
        return result
    try:
        if template is None:
            template = {}
        if not isinstance(template, dict) or not isinstance(downloaders, list) or not 1 <= len(downloaders) <= 32:
            raise ValueError
        roots = _destinations(directories, template, {"movie": "电影", "tv": "电视剧"}[media_type])
        selected = template.get("downloader")
        choices = []
        names = set()
        for row in downloaders:
            if not isinstance(row, dict) or type(row.get("enabled")) is not bool:
                raise ValueError
            if not row["enabled"] or selected not in (None, "") and row.get("name") != selected:
                continue
            data = _downloader(row, roots)
            if data[0] in names:
                raise ValueError
            names.add(data[0])
            choices.append(data)
        if not choices:
            raise ValueError
        if probe is None:
            mounts = _mounts()
            probe = lambda path: _volume(path, mounts)
        locations, free = [], []
        for root in roots:
            checked = probe(str(root))
            available = checked["available_bytes"]
            if (type(available) is not int or available < 0 or not isinstance(checked.get("mount"), str)
                    or not re.fullmatch(r"[a-f0-9]{64}", checked["mount"])):
                raise ValueError
            locations.append([str(root), checked["mount"]])
            free.append(available)
        minimum = min(free)
        return {**result, "status": "ready" if minimum >= MINIMUM_FREE_BYTES else "low",
                "fingerprint": _digest([f"{media_type}-download-space-v1", sorted(choices), locations, MINIMUM_FREE_BYTES]),
                "locations_checked": len(roots), "available_bytes": minimum}
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError):
        return result


def host_movie_download_space(*, storage_verified, systemconfig=None, media_type="movie"):
    """Read the matching MP subscription template and possible native download paths."""
    if storage_verified is True:
        try:
            from app.core.config import settings
            from app.schemas.types import SystemConfigKey
            if systemconfig is None:
                from app.db.systemconfig_oper import SystemConfigOper
                systemconfig = SystemConfigOper()
            if media_type not in ("movie", "tv"):
                raise ValueError
            key = (SystemConfigKey.DefaultMovieSubscribeConfig if media_type == "movie" else SystemConfigKey.DefaultTvSubscribeConfig).value
            template = getattr(settings, key) if hasattr(settings, key) else systemconfig.get(key)
            return check_movie_download_space(systemconfig.get(SystemConfigKey.Directories),
                                              systemconfig.get(SystemConfigKey.Downloaders),
                                              template, storage_verified=True, media_type=media_type)
        except Exception:
            pass
    return check_movie_download_space(None, None, None, storage_verified=False)
