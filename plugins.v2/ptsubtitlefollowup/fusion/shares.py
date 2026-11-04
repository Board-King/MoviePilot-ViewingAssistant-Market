"""Anonymous, bounded reads of public MP shares, filtered by local follows only.

Contract references (read, never invoked as a subscription flow):
MoviePilot 684e76e5184525db6df1a627d327d3356064783c:
app/helper/server.py:get_subscribe_shares, app/chain/subscribe.py:follow.
MoviePilot-Server c666678df85eb8b4d18f9ebbe5b054ca518e868f:
app/api/subscribe_share.py:subscribe_shares, app/models/subscribe_share.py:list.
The public endpoint returns a bare offset-paginated list, without total/next or
UID filtering. On 2026-11-03 an anonymous page/count20 smoke check returned HTTP
200, identity JSON, 20 rows. No media listing is embedded or retained here.

No instance UID, followed UID, key, cookie or subscription settings are sent.
GET /fork is deliberately absent: it changes reuse statistics. The host alone
schedules up to three pages; a zero-match page is not an exhausted public page.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import math
import re
import time

import requests

from .follow_context import normalize_follow_context, valid_follow_uid
from .sources import SourceReadError, _bound, _json_constant, _label, _proxy_config


SHARES_URL = "https://movie-pilot.org/subscribe/shares"
SOURCE_ID = "mp_followed_shares"
SOURCE_GROUP = "mp_shares"
PAGE_SIZE = 20
MAX_PAGES = 3
MAX_RESPONSE_BYTES = 512 * 1024
MAX_JSON_DEPTH = 12
MAX_JSON_NODES = 6000
_ID = re.compile(r"[1-9][0-9]{0,19}")
_PROVIDER = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")
_LEGACY = {"tmdbid": "themoviedb", "doubanid": "douban", "bangumiid": "bangumi",
           "anilistid": "anilist", "tvdbid": "tvdb", "imdbid": "imdb"}


def _identifier(value):
    if type(value) is int:
        value = str(value)
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON member")
        result[key] = value
    return result


class FollowedSharesSourceClient:
    """One public page per collect; local context never enters request arguments."""

    def __init__(self, config, *, stop_callback=None, deadline_seconds=30,
                 connect_timeout=5, read_timeout=10):
        self._closed, self._session = False, None
        self._followed, self._proxies = set(), {}
        self._stop_callback = stop_callback
        if not isinstance(config, Mapping):
            raise SourceReadError("INVALID_CONFIG")
        context = normalize_follow_context({
            "context_valid": config.get("context_valid", True),
            "sharing_enabled": config.get("sharing_enabled"),
            "followed_uids": config.get("followed_uids"),
        })
        if not context.get("context_valid") or context.get("error_code"):
            raise SourceReadError(context.get("error_code") or "CONTEXT_UNAVAILABLE")
        self._followed = set(context["followed_uids"])
        self._proxies = _proxy_config(config.get("proxies"))
        self._connect_timeout = _bound(connect_timeout, 0.1, 10)
        self._read_timeout = _bound(read_timeout, 0.1, 20)
        self._deadline = time.monotonic() + _bound(deadline_seconds, 1, 120)
        self._session = requests.Session()
        self._session.trust_env = False
        self._session.auth = None
        self._session.headers.clear()
        self._session.headers.update({"Accept": "application/json", "Accept-Encoding": "identity",
                                      "User-Agent": "MoviePilot-ViewingAssistant public share reader"})

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
                self._followed.clear()
                self._proxies.clear()

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
            return max(1, min(3600, delay))
        except (TypeError, ValueError, OverflowError):
            return None

    def _read(self, page):
        remaining = self._check()
        try:
            # A response cookie from an earlier collect must never be replayed.
            self._session.cookies.clear()
            with self._session.get(
                    SHARES_URL, params={"page": page, "count": PAGE_SIZE, "sort_type": "time"},
                    proxies=dict(self._proxies), allow_redirects=False, stream=True, verify=True,
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
                    self._fail("NOT_FOUND")
                if status >= 500:
                    self._fail("SERVER_ERROR", self._retry_after(response.headers.get("Retry-After")))
                if status != 200:
                    self._fail("HTTP_ERROR")
                if response.headers.get("Content-Encoding", "identity").strip().lower() not in {"", "identity"}:
                    self._fail("UNSUPPORTED_ENCODING")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type != "application/json" and not content_type.endswith("+json"):
                    self._fail("INVALID_RESPONSE")
                declared = response.headers.get("Content-Length")
                if declared is not None:
                    try:
                        size = int(declared)
                    except (TypeError, ValueError):
                        self._fail("INVALID_RESPONSE")
                    if size < 0:
                        self._fail("INVALID_RESPONSE")
                    if size > MAX_RESPONSE_BYTES:
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
            payload = json.loads(body.decode("utf-8-sig"), parse_constant=_json_constant,
                                 object_pairs_hook=_json_object)
            if not isinstance(payload, list) or len(payload) > PAGE_SIZE:
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
            return payload
        except SourceReadError:
            raise
        except (ValueError, TypeError, UnicodeError, RecursionError):
            self._fail("INVALID_RESPONSE")
        except Exception:
            self._fail("NETWORK_ERROR")

    def _identity(self, row):
        provider, identity = row.get("media_source"), row.get("media_id")
        declared = provider is not None or identity is not None
        if declared:
            if (not isinstance(provider, str) or not _PROVIDER.fullmatch(provider)
                    or not isinstance(identity, str) or not 1 <= len(identity) <= 128
                    or identity != identity.strip() or any(ord(c) < 32 or ord(c) == 127 for c in identity)):
                self._fail("INVALID_RESPONSE")
            if provider in {"themoviedb", "douban"}:
                identity = _identifier(identity)
                if identity is None:
                    self._fail("INVALID_RESPONSE")
        legacy = []
        for field, legacy_provider in _LEGACY.items():
            value = row.get(field)
            if value is None:
                continue
            legacy_id = (value if isinstance(value, str) and re.fullmatch(r"tt[0-9]{1,20}", value) else None) if legacy_provider == "imdb" else _identifier(value)
            if legacy_id is None:
                self._fail("INVALID_RESPONSE")
            legacy.append((legacy_provider, legacy_id))
        if declared:
            if any(legacy_id != identity for legacy_provider, legacy_id in legacy if legacy_provider == provider):
                self._fail("INVALID_RESPONSE")
        elif legacy:
            # Explicit legacy ID columns provide identities, not inferred links.
            # Pick one supported provider in a stable order; never merge them.
            provider, identity = next((pair for preferred in ("themoviedb", "douban")
                                       for pair in legacy if pair[0] == preferred), legacy[0])
        else:
            self._fail("INVALID_RESPONSE")
        return (provider, identity) if provider in {"themoviedb", "douban"} else None

    def _item(self, row, share_id, observed_at):
        raw_type = row.get("type")
        if (not isinstance(raw_type, str) or not 1 <= len(raw_type) <= 64 or raw_type != raw_type.strip()
                or any(ord(c) < 32 or ord(c) == 127 for c in raw_type)):
            self._fail("INVALID_RESPONSE")
        kind = {"电影": "movie", "电视剧": "tv", "movie": "movie", "tv": "tv"}.get(raw_type.casefold())
        if kind is None:
            return None
        identity = self._identity(row)
        if identity is None:
            return None
        title = _label(row.get("name"), 240)
        if not title:
            self._fail("INVALID_RESPONSE")
        season = row.get("season") if kind == "tv" else None
        if season is not None and (type(season) is not int or not 0 <= season <= 999):
            self._fail("INVALID_RESPONSE")
        raw_year = row.get("year")
        year = None
        if raw_year is not None and raw_year != "":
            if type(raw_year) is int:
                year = raw_year
            elif isinstance(raw_year, str) and re.fullmatch(r"[0-9]{4}", raw_year):
                year = int(raw_year)
            else:
                self._fail("INVALID_RESPONSE")
            if not 1800 <= year <= 2200:
                self._fail("INVALID_RESPONSE")
        provider, media_id = identity
        return {
            "source_id": SOURCE_ID, "source_item_id": share_id, "source_group": SOURCE_GROUP,
            "intent_kind": "discovery", "media_type": kind, "external_ids": {provider: media_id},
            "season_hint": season, "title": title, "year": year, "release_date": None,
            "metadata": {"genres": [], "languages": [], "decades": [f"{year // 10 * 10}s"] if year else [],
                         "actors": [], "directors": [], "keywords": [], "ratings": []},
            "observed_at": observed_at, "list_name": "关注分享", "rank": None, "list_size": None,
            "provenance_url": (f"https://www.themoviedb.org/{kind}/{media_id}" if provider == "themoviedb"
                               else f"https://movie.douban.com/subject/{media_id}/"),
        }

    def collect(self, source_id=SOURCE_ID, cursor=1, limit=PAGE_SIZE):
        self._check()
        if source_id != SOURCE_ID:
            self._fail("INVALID_SOURCE")
        if type(cursor) is not int or not 1 <= cursor <= MAX_PAGES:
            self._fail("INVALID_CURSOR")
        if type(limit) is not int or limit != PAGE_SIZE:
            self._fail("INVALID_LIMIT")
        rows = self._read(cursor)
        observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        items, seen, matched = [], set(), 0
        for row in rows:
            self._check()
            if not isinstance(row, dict):
                self._fail("INVALID_RESPONSE")
            share_id = _identifier(row.get("id"))
            uid = row.get("share_uid")
            if share_id is None:
                self._fail("INVALID_RESPONSE")
            if share_id in seen:
                self._fail("DUPLICATE_ITEM")
            seen.add(share_id)
            # The public schema permits missing/empty authors. They cannot
            # match a local follow, but still count toward public pagination.
            if uid is None or uid == "":
                continue
            if not valid_follow_uid(uid):
                self._fail("INVALID_RESPONSE")
            if uid not in self._followed:
                continue
            matched += 1
            item = self._item(row, share_id, observed_at)
            if item is not None:
                items.append(item)
        self._check()
        return {"source_id": SOURCE_ID, "items": items, "page": cursor,
                "total_pages": None, "reported_total": None, "truncated": False,
                "next_cursor": cursor + 1 if len(rows) == PAGE_SIZE else None,
                "fetched_at": observed_at,
                "observed_window": {"kind": "filtered_pages", "page": cursor, "received": len(rows),
                                    "matched": matched, "returned": len(items), "excluded": matched - len(items),
                                    "page_size": PAGE_SIZE}}
