"""Bounded TMDB discovery reads, independent of host workflow/write chains.

Credentials and an explicitly selected proxy are injected at runtime. This
module never imports host settings, reads environment proxies, follows redirects,
fetches media bytes/details, or retries/sleeps. The host owns scheduling and the
three-page cycle cap. Cancellation/deadlines are cooperative: OS DNS resolution
cannot be interrupted by requests, and an in-flight read is timeout bounded.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import re
import time
from urllib.parse import urlsplit

import requests

from .personal_discovery import genre_matches, rotating_genres
from .series_subscription import latest_aired_season
from .source_registry import SOURCE_SPECS as REGISTRY


BASE_URL = "https://api.themoviedb.org"
MAX_PAGES_PER_CYCLE = 3
MAX_PAGE = 500
MAX_PAGE_ITEMS = 20
MAX_RESPONSE_BYTES = 512 * 1024
MAX_JSON_NODES = 6000
MAX_JSON_DEPTH = 12
MAX_RETRY_AFTER = 3600
SOURCE_SPECS = {
    **{sid: {**spec, "settings": {"sort_by": "popularity.desc"}}
       for sid, spec in REGISTRY.items() if spec.get("personal")},
    "tmdb_popular_movie": {
        "id": "tmdb_popular_movie", "label": "TMDB 热门电影", "media_type": "movie",
        "group": "tmdb_trending", "settings": {"sort_by": "popularity.desc"},
        "description": "TMDB discover 按热度排序，不设置额外评分门槛；固定官方端点，使用显式配置的代理。",
    },
    "tmdb_popular_tv": {
        "id": "tmdb_popular_tv", "label": "TMDB 热门剧集", "media_type": "tv",
        "group": "tmdb_trending",
        "settings": {"sort_by": "popularity.desc", "with_original_language": "zh|en|ja|ko"},
        "description": "TMDB discover 按热度排序；沿用 MP 热门剧集原语种 zh|en|ja|ko，不设置额外评分门槛。",
    },
}
_PATHS = frozenset({"/3/discover/movie", "/3/discover/tv",
                    "/3/genre/movie/list", "/3/genre/tv/list"})
_PARAMS = frozenset({"language", "page", "sort_by", "with_original_language", "with_genres",
                     "primary_release_date.lte", "first_air_date.lte"})


class SourceReadError(RuntimeError):
    """Safe category and optional retry delay; no URLs, credentials or body."""

    def __init__(self, code, retry_after=None):
        self.code, self.retry_after = code, retry_after
        super().__init__(f"Source read failed: {code}")


def _bound(value, minimum, maximum):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not minimum <= value <= maximum or not math.isfinite(value)):
        raise SourceReadError("INVALID_LIMIT")
    return float(value)


def _proxy_config(value):
    if value is None:
        return {}
    if not isinstance(value, Mapping) or set(value) - {"http", "https"}:
        raise SourceReadError("INVALID_PROXY")
    result = {}
    for key, address in value.items():
        if (not isinstance(address, str) or not 1 <= len(address) <= 2048
                or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in address)):
            raise SourceReadError("INVALID_PROXY")
        try:
            parsed = urlsplit(address)
            port = parsed.port
            if (parsed.scheme not in {"http", "https", "socks4", "socks4a", "socks5", "socks5h"}
                    or not parsed.hostname or parsed.path not in {"", "/"}
                    or "?" in address or "#" in address or "\\" in address
                    or (port is not None and not 1 <= port <= 65535)):
                raise ValueError
        except (ValueError, UnicodeError):
            raise SourceReadError("INVALID_PROXY") from None
        result[key] = address
    return result


def _label(value, maximum=512):
    if not isinstance(value, str):
        return None
    value = "".join(c for c in value if ord(c) >= 32 and ord(c) != 127).strip()
    return value[:maximum] or None


def _positive_int(value, maximum=1_000_000_000):
    return type(value) is int and 1 <= value <= maximum


def _json_constant(_value):
    raise ValueError("Non-finite JSON number")


class TmdbSourceClient:
    """One short collection operation; use with and close on every exit.

    config: {api_key: MP's v3 TMDB_API_KEY, proxies: explicit requests mapping}
    collect returns one sampled page, never an implicit whole-list traversal.
    Genres are cached only for the lifetime of this client. Unknown genre IDs
    stay absent. A failed metadata read fails the page instead of inventing tags.
    """

    def __init__(self, config, *, stop_callback=None, deadline_seconds=30,
                 connect_timeout=5, read_timeout=10):
        self._closed = False
        self._session = None
        self._key = None
        self._proxies = {}
        self._genres = {}
        self._stop_callback = stop_callback
        if not isinstance(config, Mapping):
            raise SourceReadError("INVALID_CONFIG")
        key = config.get("api_key")
        if (not isinstance(key, str) or not 1 <= len(key) <= 256
                or any(ord(c) < 33 or ord(c) > 126 for c in key)):
            raise SourceReadError("INVALID_CREDENTIAL")
        self._connect_timeout = _bound(connect_timeout, 0.1, 10)
        self._read_timeout = _bound(read_timeout, 0.1, 20)
        self._deadline = time.monotonic() + _bound(deadline_seconds, 1, 120)
        self._proxies = _proxy_config(config.get("proxies"))
        self._key = key
        self._profile_genres = config.get("profile_genres") or []
        self._session = requests.Session()
        self._session.trust_env = False
        self._session.headers.update({"Accept": "application/json", "Accept-Encoding": "identity"})

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        if not self._closed:
            self._closed = True
            try:
                if self._session is not None:
                    self._session.close()
            except Exception:
                pass
            finally:
                self._key = None
                self._proxies.clear()
                self._genres.clear()

    def _fail(self, code, retry_after=None):
        self.close()
        raise SourceReadError(code, retry_after) from None

    def _check(self):
        if self._closed:
            self._fail("CLOSED")
        try:
            if self._stop_callback is not None and self._stop_callback():
                self._fail("CANCELLED")
        except SourceReadError:
            raise
        except Exception:
            self._fail("CANCELLED")
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            self._fail("DEADLINE_EXCEEDED")
        return remaining

    @staticmethod
    def _retry_after(value):
        try:
            if isinstance(value, str) and re.fullmatch(r"[0-9]{1,12}", value.strip()):
                delay = int(value)
            else:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    return None
                delay = math.ceil(parsed.timestamp() - time.time())
            return max(1, min(MAX_RETRY_AFTER, delay))
        except (TypeError, ValueError, OverflowError):
            return None

    def display_detail(self, kind, media_id):
        if kind not in ("movie", "tv") or not isinstance(media_id, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", media_id):
            self._fail("INVALID_ENDPOINT")
        return self._read_json(f"/3/{kind}/{media_id}", {"language": "zh-CN"})

    def _get(self, path, params):
        if path not in _PATHS or not isinstance(params, dict) or set(params) - _PARAMS:
            self._fail("INVALID_ENDPOINT")
        return self._read_json(path, params)

    def _read_json(self, path, params, *, missing_ok=False):
        remaining = self._check()
        query = {**params, "api_key": self._key}
        try:
            with self._session.get(
                    BASE_URL + path, params=query, proxies=dict(self._proxies),
                    allow_redirects=False, stream=True, verify=True,
                    timeout=(min(self._connect_timeout, remaining), min(self._read_timeout, remaining))) as response:
                self._check()
                status = response.status_code
                if 300 <= status < 400:
                    self._fail("REDIRECT_BLOCKED")
                if status in {401, 403}:
                    self._fail("AUTH_REQUIRED" if status == 401 else "FORBIDDEN")
                if status == 429:
                    self._fail("RATE_LIMITED", self._retry_after(response.headers.get("Retry-After")))
                if status == 404:
                    if missing_ok:
                        return {}
                    self._fail("NOT_FOUND")
                if status >= 500:
                    self._fail("SERVER_ERROR")
                if status != 200:
                    self._fail("HTTP_ERROR")
                encoding = response.headers.get("Content-Encoding", "identity").lower().strip()
                if encoding not in {"", "identity"}:
                    self._fail("UNSUPPORTED_ENCODING")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type and content_type != "application/json" and not content_type.endswith("+json"):
                    self._fail("INVALID_RESPONSE")
                length = response.headers.get("Content-Length")
                if length is not None:
                    try:
                        declared = int(length)
                    except (TypeError, ValueError):
                        self._fail("INVALID_RESPONSE")
                    if declared < 0:
                        self._fail("INVALID_RESPONSE")
                    if declared > MAX_RESPONSE_BYTES:
                        self._fail("RESPONSE_TOO_LARGE")
                body = bytearray()
                for chunk in response.iter_content(chunk_size=8192):
                    self._check()
                    if not chunk:
                        continue
                    if not isinstance(chunk, bytes):
                        self._fail("INVALID_RESPONSE")
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        self._fail("RESPONSE_TOO_LARGE")
                    body.extend(chunk)
            self._check()
            payload = json.loads(body.decode("utf-8-sig"), parse_constant=_json_constant)
            self._validate_json(payload)
            return payload
        except SourceReadError:
            raise
        except requests.RequestException:
            self._fail("NETWORK_ERROR")
        except (ValueError, TypeError, UnicodeError, RecursionError):
            self._fail("INVALID_RESPONSE")
        except Exception:
            self._fail("NETWORK_ERROR")

    def _validate_json(self, payload):
        if not isinstance(payload, dict) or payload.get("success") is False:
            self._fail("INVALID_RESPONSE")
        stack, count = [(payload, 0)], 0
        while stack:
            value, depth = stack.pop()
            count += 1
            if count > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
                self._fail("INVALID_RESPONSE")
            if isinstance(value, dict):
                stack.extend((item, depth + 1) for item in value.values())
            elif isinstance(value, list):
                stack.extend((item, depth + 1) for item in value)
            elif isinstance(value, float) and not math.isfinite(value):
                self._fail("INVALID_RESPONSE")
        self._check()

    def _genre_map(self, kind, language="zh-CN"):
        cache_key = (kind, language)
        if cache_key not in self._genres:
            payload = self._get(f"/3/genre/{kind}/list", {"language": language})
            rows = payload.get("genres")
            if not isinstance(rows, list) or len(rows) > 100:
                self._fail("INVALID_RESPONSE")
            result = {}
            for row in rows:
                if not isinstance(row, dict) or not _positive_int(row.get("id")):
                    self._fail("INVALID_RESPONSE")
                label = _label(row.get("name"), 80)
                if label is None or row["id"] in result:
                    self._fail("INVALID_RESPONSE")
                result[row["id"]] = label
            self._genres[cache_key] = result
        return self._genres[cache_key]

    def _item(self, row, spec, genre_map, observed_at):
        if not isinstance(row, dict) or not _positive_int(row.get("id")):
            self._fail("INVALID_RESPONSE")
        kind = spec["media_type"]
        title = _label(row.get("title" if kind == "movie" else "name"))
        if title is None:
            self._fail("INVALID_RESPONSE")
        ids = row.get("genre_ids") or []
        if not isinstance(ids, list) or len(ids) > 30 or not all(_positive_int(i) for i in ids):
            self._fail("INVALID_RESPONSE")
        genres = list(dict.fromkeys(genre_map[i] for i in ids if i in genre_map))
        raw_date = row.get("release_date" if kind == "movie" else "first_air_date")
        release_date = None
        if isinstance(raw_date, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw_date):
            try:
                release_date = date.fromisoformat(raw_date).isoformat()
            except ValueError:
                pass
        year = int(release_date[:4]) if release_date else None
        language = row.get("original_language")
        languages = [language.lower()] if isinstance(language, str) and re.fullmatch(r"[A-Za-z]{2,3}", language) else []
        rating, count = row.get("vote_average"), row.get("vote_count")
        ratings = []
        if (not isinstance(rating, bool) and isinstance(rating, (int, float))
                and 0 <= rating <= 10 and math.isfinite(rating)):
            ratings.append({"provider": "themoviedb", "value": float(rating), "scale": 10,
                            "count": count if type(count) is int and 0 <= count <= 1_000_000_000 else None,
                            "observed_at": observed_at})
        identity = str(row["id"])
        return {
            "source_id": spec["id"], "source_item_id": identity,
            "source_group": spec["group"], "intent_kind": "discovery", "media_type": kind,
            "external_ids": {"themoviedb": identity}, "season_hint": None,
            "title": title, "year": year,
            "metadata": {"genres": genres, "languages": languages,
                         "decades": [f"{year // 10 * 10}s"] if year else [],
                         "actors": [], "directors": [], "keywords": [], "ratings": ratings},
            "observed_at": observed_at, "release_date": release_date,
            "list_name": spec["label"], "rank": None, "list_size": None,
            "provenance_url": f"https://www.themoviedb.org/{kind}/{identity}",
        }

    def collect(self, source_id, cursor=1, limit=20):
        """Read one page; optional limit samples its first N items explicitly.

        A valid empty upstream page is success. Network/schema errors never become
        empty success. next_cursor is a hint, not permission to traverse all pages.
        """
        self._check()
        if not isinstance(source_id, str) or source_id not in SOURCE_SPECS:
            self._fail("INVALID_SOURCE")
        if type(cursor) is not int or not 1 <= cursor <= MAX_PAGE:
            self._fail("INVALID_CURSOR")
        if type(limit) is not int or not 1 <= limit <= MAX_PAGE_ITEMS:
            self._fail("INVALID_LIMIT")
        spec = SOURCE_SPECS[source_id]
        kind = spec["media_type"]
        params = {"language": "zh-CN", "page": cursor, **spec["settings"]}
        today = datetime.now(timezone.utc).date()
        matches = {}
        if spec.get("personal"):
            if not self._profile_genres:
                self._fail("PROFILE_GENRES_REQUIRED", 900)
            localized = self._genre_map(kind)
            # Match imported English-language Emby genres to the same TMDB
            # IDs without sending titles, user IDs or genre strings upstream.
            english = self._genre_map(kind, "en-US") if any(
                label.isascii() for label in self._profile_genres) else {}
            matches = genre_matches(self._profile_genres, localized, english)
            genre_ids = rotating_genres(matches, today)
            if not genre_ids:
                self._fail("PROFILE_GENRES_REQUIRED", 3600)
            params["with_genres"] = "|".join(map(str, genre_ids))
            params["primary_release_date.lte" if kind == "movie" else "first_air_date.lte"] = today.isoformat()
        payload = self._get(f"/3/discover/{kind}", params)
        rows, page, total_pages = payload.get("results"), payload.get("page"), payload.get("total_pages")
        if (not isinstance(rows, list) or len(rows) > MAX_PAGE_ITEMS
                or type(page) is not int or page != cursor
                or type(total_pages) is not int or not 0 <= total_pages <= 1_000_000
                or (rows and total_pages < page)
                or (not rows and total_pages >= page)):
            self._fail("INVALID_RESPONSE")
        if "total_results" in payload and (type(payload["total_results"]) is not int
                or payload["total_results"] < len(rows) or payload["total_results"] > 1_000_000_000):
            self._fail("INVALID_RESPONSE")
        if not rows and payload.get("total_results", 0) != 0 and cursor == 1:
            self._fail("INVALID_RESPONSE")
        # Empty results do not require an additional genre lookup.
        genre_map = {**self._genre_map(kind), **matches} if rows else {}
        observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        items, seen = [], set()
        for row in rows:
            self._check()
            item = self._item(row, spec, genre_map, observed_at)
            if item["source_item_id"] in seen:
                self._fail("DUPLICATE_ITEM")
            seen.add(item["source_item_id"])
            items.append(item)
        if source_id == "tmdb_personal_tv" and items:
            # Cover different series on successive days without issuing up to
            # twenty detail requests per page on the user's NAS.
            sample_size = min(5, len(items), limit)
            offset = today.toordinal() * 5 % min(len(items), limit)
            for index in range(sample_size):
                item = items[(offset + index) % min(len(items), limit)]
                detail = self._read_json(f"/3/tv/{item['source_item_id']}", {"language": "zh-CN"}, missing_ok=True)
                item["season_hint"] = latest_aired_season(detail, item["source_item_id"], today=today)
        self._check()
        return {"source_id": source_id, "items": items[:limit], "page": cursor,
                "total_pages": total_pages,
                "next_cursor": cursor + 1 if rows and cursor < min(total_pages, MAX_PAGE) else None,
                "fetched_at": observed_at, "truncated": len(items) > limit,
                "reported_total": payload.get("total_results"),
                "observed_window": {"page": cursor, "received": len(items),
                                    "returned": min(len(items), limit), "page_size": MAX_PAGE_ITEMS}}
