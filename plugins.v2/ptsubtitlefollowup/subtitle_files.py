"""Small, dependency-free helpers for PT supplied subtitles.

These checks validate file safety, subtitle syntax and Chinese text. They do not
prove authorship, translation quality, or audio/video synchronisation. Nothing
in this module invokes an AI service, edits a video, or overwrites a subtitle.
"""

from __future__ import annotations

import errno
import hashlib
import io
import os
import re
import stat
import secrets
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

MAX_DOWNLOAD_BYTES = 8 * 1024 * 1024
MAX_EXTRACTED_BYTES = 20 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 128
MAX_SUBTITLE_ENTRIES = 32
MAX_COMPRESSION_RATIO = 200
SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa"}
MEDIA_EXTENSIONS = {".strm", ".mkv", ".mp4", ".m4v", ".avi", ".mov", ".ts", ".m2ts", ".wmv"}


class SubtitleError(ValueError):
    """Unusable or unsafe subtitle input; caller should keep waiting/report it."""


class AmbiguousSubtitleError(SubtitleError):
    """More than one distinct eligible subtitle; do not guess a version."""


class ExistingSubtitleError(FileExistsError):
    """A Chinese subtitle already exists and was left untouched."""


@dataclass(frozen=True)
class SubtitleCandidate:
    filename: str
    extension: str
    payload: bytes  # normalised UTF-8, without a BOM
    cue_count: int
    last_cue_seconds: float
    sha256: str
    chinese_characters: int


_MACHINE_MARKER = re.compile(
    r"机翻|機翻|机器翻译|機器翻譯|AI[\s_-]*(?:翻译|翻譯|字幕)|"
    r"(?:machine|automatic|automated)[\s_-]*translat|"
    r"(?:chatgpt|deepseek|whisper)[\s_-]*(?:翻译|翻譯|字幕|translat)",
    re.IGNORECASE,
)
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_SRT_TIME = re.compile(
    r"^(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*"
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})(?:\s+.*)?$"
)
_ASS_TIME = re.compile(r"^(\d{1,2}):(\d{2}):(\d{2})[.](\d{1,3})$")
_EPISODE = re.compile(r"(?i)(?<![a-z0-9])s(\d{1,2})[ ._-]*e(\d{1,3})(?!\d)")
_EPISODE_ONLY = re.compile(r"(?i)(?<![a-z0-9])(?:e|ep)[ ._-]?(\d{1,3})(?!\d)")
_CHINESE_EPISODE = re.compile(r"第\s*(\d{1,3})\s*[集话話]")
_SEASON = re.compile(r"(?i)(?<![a-z0-9])s(?:eason)?[ ._-]?(\d{1,2})(?!\d)")
_CHINESE_SEASON = re.compile(r"第\s*(\d{1,2})\s*季")


def explicitly_machine_translated(*metadata: object) -> bool:
    """Only detect explicit labels; an unlabelled file is not proven human-made."""
    return bool(_MACHINE_MARKER.search(" ".join(str(x or "") for x in metadata)))


def _safe_member_name(name: str) -> PurePosixPath:
    if not name or "\x00" in name or "\\" in name:
        raise SubtitleError("Unsafe archive member name")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("..", "") for part in path.parts):
        raise SubtitleError("Archive path traversal is not allowed")
    if any(":" in part for part in path.parts):
        raise SubtitleError("Drive paths and alternate data streams are not allowed")
    return path


def _decode(payload: bytes) -> str:
    encodings = ("utf-16",) if payload.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
    for encoding in encodings:
        try:
            text = payload.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" in text or re.search(r"[\x01-\x08\x0b\x0c\x0e-\x1f]", text):
            raise SubtitleError("Binary/control data is not a subtitle")
        return text.replace("\r\n", "\n").replace("\r", "\n").strip() + "\n"
    raise SubtitleError("Subtitle encoding is not supported")


def _seconds(parts: Iterable[str]) -> float:
    hour, minute, second, fraction = list(parts)
    if int(minute) >= 60 or int(second) >= 60:
        raise SubtitleError("Invalid subtitle timestamp")
    return int(hour) * 3600 + int(minute) * 60 + int(second) + int(fraction) / (10 ** len(fraction))


def _cue(start: float, end: float, text: str) -> tuple[float, float, str]:
    if start < 0 or end <= start or end > 12 * 3600 or end - start > 3600:
        raise SubtitleError("Implausible subtitle timestamp range")
    # Strip formatting only for validation. Preserve the original subtitle.
    visible = re.sub(r"\{[^}]*\}|<[^>]*>", "", text)
    visible = visible.replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", " ")
    if not visible.strip():
        raise SubtitleError("Subtitle cue contains no visible text")
    return start, end, visible


def _srt_cues(text: str) -> list[tuple[float, float, str]]:
    cues = []
    for block in re.split(r"\n[ \t]*\n", text.strip()):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit():
            raise SubtitleError("Invalid SRT cue structure")
        match = _SRT_TIME.fullmatch(lines[1].strip())
        if not match:
            raise SubtitleError("Invalid SRT timestamp line")
        cues.append(_cue(_seconds(match.groups()[:4]), _seconds(match.groups()[4:]), "\n".join(lines[2:])))
    return cues


def _ass_cues(text: str) -> list[tuple[float, float, str]]:
    if not re.search(r"(?im)^\[Script Info\]\s*$", text):
        raise SubtitleError("Missing ASS script header")
    in_events = False
    columns = []
    cues = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_events = stripped.lower() == "[events]"
            continue
        if not in_events:
            continue
        if stripped.lower().startswith("format:"):
            columns = [x.strip().lower() for x in stripped.split(":", 1)[1].split(",")]
            if not all(x in columns for x in ("start", "end", "text")) or columns[-1] != "text":
                raise SubtitleError("Unsupported ASS event format")
        elif stripped.lower().startswith("dialogue:"):
            if not columns:
                raise SubtitleError("ASS dialogue has no format declaration")
            values = stripped.split(":", 1)[1].lstrip().split(",", len(columns) - 1)
            if len(values) != len(columns):
                raise SubtitleError("Invalid ASS dialogue")
            row = dict(zip(columns, values))
            start, end = _ASS_TIME.fullmatch(row["start"].strip()), _ASS_TIME.fullmatch(row["end"].strip())
            if not start or not end:
                raise SubtitleError("Invalid ASS timestamp")
            cues.append(_cue(_seconds(start.groups()), _seconds(end.groups()), row["text"]))
    return cues


def validate_subtitle(filename: str, payload: bytes, *, video_duration: float | None = None) -> SubtitleCandidate:
    """Validate syntax/Chinese text, never synchronise, translate or edit timing."""
    extension = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if extension not in SUBTITLE_EXTENSIONS:
        raise SubtitleError("Unsupported subtitle format")
    if explicitly_machine_translated(filename):
        raise SubtitleError("Explicitly machine-translated subtitle was excluded")
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_DOWNLOAD_BYTES:
        raise SubtitleError("Subtitle file is empty or exceeds the size limit")
    text = _decode(payload)
    cues = _srt_cues(text) if extension == ".srt" else _ass_cues(text)
    if not 1 <= len(cues) <= 50000:
        raise SubtitleError("Subtitle contains no usable cues or too many cues")
    visible = "\n".join(x[2] for x in cues)
    chinese = len(_CJK.findall(visible))
    letters = sum(ch.isalpha() for ch in visible)
    chinese_cues = sum(bool(_CJK.search(x[2])) for x in cues)
    if chinese < 10 or chinese_cues / len(cues) < 0.2 or chinese / max(1, letters) < 0.03:
        raise SubtitleError("Subtitle does not contain sufficient Chinese dialogue")
    last = max(x[1] for x in cues)
    if video_duration is not None:
        if not isinstance(video_duration, (float, int)) or not 0 < video_duration <= 12 * 3600:
            raise SubtitleError("Invalid reference video duration")
        if last > video_duration + max(30.0, video_duration * 0.01):
            raise SubtitleError("Subtitle extends beyond the reference video duration")
    encoded = text.encode("utf-8")
    return SubtitleCandidate(filename, extension, encoded, len(cues), last, hashlib.sha256(encoded).hexdigest(), chinese)


def episode_matches(filename: str, *, season: int | None, episode: int | None) -> bool:
    """Require unambiguous season+episode evidence for TV. Never position-match."""
    exact = {(int(a), int(b)) for a, b in _EPISODE.findall(filename)}
    episode_only = {int(x) for x in _EPISODE_ONLY.findall(filename)}
    episode_only.update(int(x) for x in _CHINESE_EPISODE.findall(filename))
    if season is None and episode is None:
        return not exact and not episode_only
    if season is None or episode is None:
        return False
    # Ranges and multi-episode files need manual selection; do not attach the
    # same subtitle to each episode merely because the first number matches.
    if re.search(r"(?i)(?:e\d{1,3}|第\s*\d{1,3}\s*集)\s*[-~至+&]\s*(?:e)?\d{1,3}(?!\d)", filename):
        return False
    if re.search(r"(?i)e\d{1,3}e\d{1,3}", filename):
        return False
    if exact:
        return exact == {(season, episode)} and not (episode_only - {episode})
    seasons = {int(x) for x in _SEASON.findall(filename)}
    seasons.update(int(x) for x in _CHINESE_SEASON.findall(filename))
    return seasons == {season} and episode_only == {episode}


def parse_download(
    filename: str,
    payload: bytes,
    *,
    video_duration: float | None = None,
    season: int | None = None,
    episode: int | None = None,
) -> list[SubtitleCandidate]:
    """Read a subtitle or ZIP in memory; no archive member is extracted to disk.

    Bad/English individual subtitles in a safe ZIP are ignored. Unsafe ZIP
    structure rejects the whole archive, including unsafe non-subtitle entries.
    """
    if not isinstance(payload, bytes) or not 1 <= len(payload) <= MAX_DOWNLOAD_BYTES:
        raise SubtitleError("Download is empty or exceeds the size limit")
    if explicitly_machine_translated(filename):
        raise SubtitleError("Explicitly machine-translated download was excluded")
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if suffix != ".zip":
        if not episode_matches(filename, season=season, episode=episode):
            raise SubtitleError("Subtitle episode identity is missing or mismatched")
        return [validate_subtitle(filename, payload, video_duration=video_duration)]
    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except (zipfile.BadZipFile, OSError) as exc:
        raise SubtitleError("Invalid ZIP archive") from exc
    with archive:
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_ENTRIES:
            raise SubtitleError("Too many archive entries")
        seen = set()
        total_size = 0
        subtitle_members = []
        for member in members:
            # ZipInfo may normalise backslashes or truncate at a NUL while
            # retaining the original header in orig_filename.
            _safe_member_name(member.orig_filename)
            path = _safe_member_name(member.filename)
            name = path.as_posix().casefold()
            if name in seen:
                raise SubtitleError("Duplicate archive paths")
            seen.add(name)
            mode = member.external_attr >> 16
            kind = stat.S_IFMT(mode)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR) or stat.S_ISLNK(mode):
                raise SubtitleError("Links and special files are not accepted")
            if member.flag_bits & 1:
                raise SubtitleError("Encrypted archive is not supported")
            if member.file_size < 0 or member.compress_size < 0:
                raise SubtitleError("Invalid archive size")
            total_size += member.file_size
            if total_size > MAX_EXTRACTED_BYTES:
                raise SubtitleError("Archive exceeds expanded size limit")
            if member.file_size > 65536 and member.file_size / max(1, member.compress_size) > MAX_COMPRESSION_RATIO:
                raise SubtitleError("Archive compression ratio exceeds limit")
            if not member.is_dir() and path.suffix.lower() in SUBTITLE_EXTENSIONS:
                if member.file_size > MAX_DOWNLOAD_BYTES:
                    raise SubtitleError("Archive subtitle exceeds size limit")
                subtitle_members.append(member)
        if len(subtitle_members) > MAX_SUBTITLE_ENTRIES:
            raise SubtitleError("Too many subtitle entries")
        candidates = []
        for member in subtitle_members:
            if explicitly_machine_translated(member.filename):
                continue
            if not episode_matches(member.filename, season=season, episode=episode):
                continue
            try:
                with archive.open(member) as stream:
                    data = stream.read(MAX_DOWNLOAD_BYTES + 1)
                if len(data) != member.file_size or len(data) > MAX_DOWNLOAD_BYTES:
                    raise SubtitleError("Archive member size does not match")
                candidates.append(validate_subtitle(member.filename, data, video_duration=video_duration))
            except (zipfile.BadZipFile, RuntimeError, EOFError, OSError) as exc:
                raise SubtitleError("Could not safely read ZIP member") from exc
            except SubtitleError:
                # A package may contain English and Chinese tracks together.
                continue
        if not candidates:
            raise SubtitleError("Archive contains no matching usable Chinese subtitle")
        return candidates


def select_candidate(
    candidates: Iterable[SubtitleCandidate], *, season: int | None = None, episode: int | None = None
) -> SubtitleCandidate:
    """Only accept one distinct matching subtitle. Duplicate identical copies are OK."""
    matching = [x for x in candidates if episode_matches(x.filename, season=season, episode=episode)]
    unique = {(x.extension, x.sha256): x for x in matching}
    if not unique:
        raise SubtitleError("No matching Chinese subtitle")
    if len(unique) != 1:
        raise AmbiguousSubtitleError("Multiple distinct Chinese subtitles require explicit selection")
    return next(iter(unique.values()))


def _normal_path(path: str | os.PathLike[str]) -> Path:
    given = Path(path)
    if not given.is_absolute() or ".." in given.parts:
        raise SubtitleError("Only absolute non-traversing paths are allowed")
    return Path(os.path.abspath(given))


def _check_paths(media_path: str | os.PathLike[str], allowed_roots: Iterable[str | os.PathLike[str]]) -> Path:
    media = _normal_path(media_path)
    roots = [_normal_path(x) for x in allowed_roots]
    if not any(media != root and media.is_relative_to(root) for root in roots):
        raise SubtitleError("Target is outside the allowed library roots")
    if media.suffix.lower() not in MEDIA_EXTENSIONS:
        raise SubtitleError("Target is not a supported media/STRM file")
    for path in (media, *media.parents):
        if path.is_symlink():
            raise SubtitleError("Symbolic-link target paths are not accepted")
    try:
        info = media.stat()
    except OSError as exc:
        raise SubtitleError("Target media/STRM does not exist") from exc
    if not stat.S_ISREG(info.st_mode):
        raise SubtitleError("Target media/STRM is not a regular file")
    return media


def _open_parent_fd(parent: Path) -> int | None:
    """Linux: hold each directory open without following any symlink component."""
    required = (os.open, os.stat, os.unlink, os.link)
    if os.name == "nt" or not hasattr(os, "O_NOFOLLOW") or not all(x in os.supports_dir_fd for x in required):
        return None
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(parent.anchor, flags)
    try:
        for part in parent.parts[1:]:
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def save_sidecar(
    candidate: SubtitleCandidate,
    media_path: str | os.PathLike[str],
    allowed_roots: Iterable[str | os.PathLike[str]],
) -> Path:
    """Save beside an existing media/STRM with an atomic no-overwrite commit.

    On Linux directory descriptors prevent symlink-swap escapes. On Windows
    paths are checked again before committing; NAS deployment is Linux.
    Existing files, including symlinks with the final name, are never replaced.
    """
    roots = list(allowed_roots)
    media = _check_paths(media_path, roots)
    checked = validate_subtitle(candidate.filename, candidate.payload)
    target = media.with_name(media.stem + ".zh" + checked.extension)
    parent_fd = _open_parent_fd(media.parent)
    temporary_name = ".pt-subtitle-" + secrets.token_hex(12) + ".tmp"
    temporary = media.parent / temporary_name
    descriptor = None
    owns_temporary = False

    def open_at(name: str, flags: int, mode: int = 0o666) -> int:
        if parent_fd is not None:
            return os.open(name, flags | getattr(os, "O_NOFOLLOW", 0), mode, dir_fd=parent_fd)
        return os.open(media.parent / name, flags, mode)

    def unlink_at(name: str) -> None:
        if parent_fd is not None:
            os.unlink(name, dir_fd=parent_fd)
        else:
            os.unlink(media.parent / name)

    try:
        if parent_fd is not None:
            info = os.stat(media.name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                raise SubtitleError("Target media changed during validation")
        descriptor = open_at(temporary_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        owns_temporary = True
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(checked.payload)
            output.flush()
            os.fsync(output.fileno())
        if parent_fd is None:
            _check_paths(media, roots)
        try:
            if parent_fd is not None:
                os.link(temporary_name, target.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd, follow_symlinks=False)
            else:
                os.link(temporary, target, follow_symlinks=False)
        except FileExistsError as exc:
            raise ExistingSubtitleError(str(target)) from exc
        except OSError as exc:
            if exc.errno not in (errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS, errno.EACCES):
                raise
            # Filesystem lacks hard links. Exclusive creation still prevents
            # replacement, although a reader may briefly see an incomplete file.
            try:
                descriptor = open_at(target.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError as exists:
                raise ExistingSubtitleError(str(target)) from exists
            created = os.fstat(descriptor)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    descriptor = None
                    output.write(checked.payload)
                    output.flush()
                    os.fsync(output.fileno())
            except BaseException:
                try:
                    current = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False) if parent_fd is not None else target.lstat()
                    if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                        unlink_at(target.name)
                except FileNotFoundError:
                    pass
                raise
        return target
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if owns_temporary:
            try:
                unlink_at(temporary_name)
            except FileNotFoundError:
                pass
        if parent_fd is not None:
            os.close(parent_fd)
