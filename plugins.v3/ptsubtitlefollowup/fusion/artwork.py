"""Display-only details for the visible ranked window. Never supply execution scope."""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time

import requests

from .sources import TmdbSourceClient, _proxy_config


_KEY = re.compile(r"(themoviedb|douban):(movie|tv):([1-9][0-9]{0,18})")
_TMDB_IMAGE = re.compile(r"/[A-Za-z0-9_-]{1,100}\.(?:jpg|png)")
_DOUBAN_IMAGE = re.compile(r"https://img[0-9]{0,2}\.doubanio\.com/view/photo/(?:s|m|l)_ratio_poster/public/p[0-9]{1,20}\.(?:jpg|jpeg|webp)")
_WEEK = 7 * 86400
_THUMB_REV = 2
MAX_THUMB_BYTES = 40 * 1024
MAX_POSTER_WIDTH = 360


def _positive(value, maximum):
    return value if type(value) is int and 1 <= value <= maximum else None


def display_fields(provider, kind, media_id, detail):
    """Only details for the requested provider/type/id may decorate that row."""
    if not isinstance(detail, dict) or str(detail.get("id")) != media_id:
        raise ValueError("Unexpected detail identity")
    info, url = {}, None
    if provider == "douban":
        if detail.get("type") != kind:
            raise ValueError("Unexpected Douban media type")
        pic = detail.get("pic")
        if isinstance(pic, dict):
            for size in ("large", "normal"):
                path = pic.get(size)
                if isinstance(path, str) and _DOUBAN_IMAGE.fullmatch(path):
                    url = path
                    break
        if kind == "tv":
            info["episodes"] = _positive(detail.get("episodes_count"), 10000)
            season = detail.get("season_number")
            info["season"] = season if type(season) is int and 0 <= season <= 999 else None
    elif provider == "themoviedb":
        path = detail.get("poster_path")
        if isinstance(path, str) and _TMDB_IMAGE.fullmatch(path):
            url = "https://image.tmdb.org/t/p/w500" + path
        if kind == "tv":
            info["season_count"] = _positive(detail.get("number_of_seasons"), 999)
            info["episodes"] = _positive(detail.get("number_of_episodes"), 10000)
    return {k: v for k, v in info.items() if v is not None}, url


def _thumbnail(url, proxies, deadline):
    # URLs are constructed/allowlisted by display_fields; redirects cannot expand the allowlist.
    from PIL import Image, ImageOps
    with requests.Session() as http:
        http.trust_env = False
        headers = {"Accept-Encoding": "identity"}
        if _DOUBAN_IMAGE.fullmatch(url):
            headers["Referer"] = "https://movie.douban.com/"
        with http.get(url, proxies=_proxy_config(proxies), headers=headers, timeout=(3, 5),
                      stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                raise ValueError("Poster unavailable")
            body = bytearray()
            for chunk in response.iter_content(8192):
                if len(body) + len(chunk) > 512 * 1024 or time.monotonic() > deadline:
                    raise ValueError("Poster size/time limit")
                body.extend(chunk)
    with Image.open(io.BytesIO(body)) as image:
        if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > 4_000_000:
            raise ValueError("Unsupported poster")
        # Respect both native dimensions even for oddly cropped or genuinely tiny source art.
        unit = min(MAX_POSTER_WIDTH // 2, image.width // 2, image.height // 3)
        if not unit:
            raise ValueError("Poster too small")
        source = image.convert("RGB")
        for target in dict.fromkeys((unit, min(unit, 140), min(unit, 100))):
            rendered = ImageOps.fit(source, (2 * target, 3 * target), method=Image.Resampling.LANCZOS)
            for quality in (84, 72):
                output = io.BytesIO()
                rendered.save(output, "JPEG", quality=quality, optimize=True)
                if output.tell() <= MAX_THUMB_BYTES:
                    return base64.b64encode(output.getvalue()).decode("ascii")
    raise ValueError("Thumbnail too large")


class CandidateArtwork:
    def __init__(self, directory, credentials, *, clock=time.time):
        self._directory, self._credentials, self._clock = directory, credentials, clock
        self._worker = threading.Lock()
        self._cursor = 0
        self._epoch = threading.Event()

    def configure(self):
        self.stop()
        self._epoch = threading.Event()

    def stop(self):
        self._epoch.set()

    def _path(self, key):
        match = _KEY.fullmatch(key) if isinstance(key, str) else None
        if not match:
            return None
        return Path(self._directory()) / "candidate-artwork-v1" / ("-".join(match.groups()) + ".json")

    @staticmethod
    def _read(path):
        try:
            if path is None or path.stat().st_size > MAX_THUMB_BYTES * 2:
                return {}
            value = json.loads(path.read_bytes())
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def decorate(self, status):
        # Only visible rows may add image bytes to the native MP page response.
        rows = []
        for row in status.get("candidates", [])[:50]:
            try:
                cached = self._read(self._path(row.get("entity_key"))) if len(rows) < status.get("display_limit", 20) else {}
            except OSError:
                cached = {}
            rows.append({**row, "display_details": cached.get("info") or {}, "poster_jpeg": cached.get("jpeg")})
        return {**status, "candidates": rows}

    def _lookup(self, identity, cancelled):
        provider, kind, media_id = identity
        config = self._credentials()
        if provider == "douban":
            from app.modules.douban.apiv2 import DoubanApi
            client = DoubanApi()
            detail = (client.tv_detail if kind == "tv" else client.movie_detail)(media_id)
        else:
            with TmdbSourceClient(config, stop_callback=cancelled, deadline_seconds=12) as client:
                detail = client.display_detail(kind, media_id)
        info, url = display_fields(provider, kind, media_id, detail)
        jpeg = None
        urls = [url] if url else []
        if url and provider == "douban":
            normal = detail["pic"].get("normal")
            if isinstance(normal, str) and _DOUBAN_IMAGE.fullmatch(normal) and normal != url:
                urls.append(normal)
        deadline = time.monotonic() + 8
        for variant in urls:
            if cancelled() or time.monotonic() >= deadline:
                break
            try:
                jpeg = _thumbnail(variant, config.get("proxies"), deadline)
                break
            except (ImportError, OSError, ValueError, requests.RequestException):
                pass
        return info, jpeg, bool(url)

    def refresh(self, status):
        epoch = self._epoch
        if epoch.is_set() or not status.get("enabled") or not self._worker.acquire(blocking=False):
            return
        try:
            requests_left, deadline = 4, time.monotonic() + 20
            pool = status.get("candidates", [])[:500]
            for _ in range(min(50, len(pool))):
                if requests_left == 0 or epoch.is_set() or time.monotonic() > deadline:
                    break
                row = pool[self._cursor % len(pool)]
                self._cursor = (self._cursor + 1) % len(pool)
                key = row.get("entity_key")
                path = self._path(key)
                if path is None:
                    continue
                old = self._read(path)
                now = self._clock()
                # Leave old artwork visible, upgrading at most the usual four records per tick.
                needs_upgrade = old.get("jpeg") and old.get("thumb_rev") != _THUMB_REV
                if not needs_upgrade and isinstance(old.get("next"), (int, float)) and now < old["next"]:
                    continue
                requests_left -= 1
                try:
                    info, jpeg, had_url = self._lookup(_KEY.fullmatch(key).groups(), epoch.is_set)
                    record = {"info": info, "jpeg": jpeg or old.get("jpeg"), "next": now + (3600 if had_url and not jpeg else _WEEK)}
                except Exception:
                    record = {**old, "next": now + 3600}
                record["thumb_rev"] = _THUMB_REV  # also honour retry cooldown after a failed upgrade
                if epoch.is_set():
                    break
                path.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
                    tmp = stream.name
                    try:
                        json.dump(record, stream, ensure_ascii=True)
                    except BaseException:
                        stream.close()
                        os.unlink(tmp)
                        raise
                try:
                    os.replace(tmp, path)
                finally:
                    Path(tmp).unlink(missing_ok=True)
            # Cache only bounded public display data in this plugin's private directory.
            directory = Path(self._directory()) / "candidate-artwork-v1"
            if directory.is_dir():
                files = [p for p in directory.glob("*.json") if re.fullmatch(r"(?:themoviedb|douban)-(?:tv|movie)-[1-9][0-9]{0,18}\.json", p.name)]
                if len(files) > 500:
                    for old in sorted(files, key=lambda p: p.stat().st_mtime)[:-500]:
                        old.unlink(missing_ok=True)
        finally:
            self._worker.release()
