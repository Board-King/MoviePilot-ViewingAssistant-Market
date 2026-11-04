"""Bounded public upcoming-TV RSS snapshot; no host sync or account endpoints.

Contract reference: DIYgod/RSSHub commit
daacf9b9a20fbbaf88715f5cf90539a2283724f2,
lib/routes/douban/tv/coming.ts. The route defaults to hot/10, has no
pagination, maps subject pubdate to RSS pubDate, and errors on empty subjects.
The fixed third-party instance was anonymously checked on 2026-11-03:
HTTP 200, application/xml, identity encoding, 9,131 bytes, 10 items. No media
listing is embedded here. Availability and upstream contents can change.

Only the public RSS endpoint is requested. This module neither uses nor
implements the upstream aggregator's private API signing. Cancellation and
deadlines are cooperative; OS DNS resolution cannot be instantly interrupted.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import io
import re
import time
import xml.etree.ElementTree as ET

import requests

from .sources import SourceReadError, _bound, _label, _proxy_config


FEED_URL = "https://rsshub.ddsrem.com/douban/tv/coming"
SOURCE_ID = "douban_tv_upcoming"
SOURCE_GROUP = "douban_trending"
MAX_RESPONSE_BYTES = 512 * 1024
MAX_XML_NODES = 5000
MAX_XML_DEPTH = 12
MAX_FEED_ITEMS = 200
MAX_LIMIT = 60
_SUBJECT = re.compile(r"https://movie\.douban\.com/subject/([1-9][0-9]{0,19})/?")
_GENRE = re.compile(r"[\u3400-\u9fff]{2,20}")


def _category_metadata(values):
    """Read only the route's documented category forms, never parse a synopsis."""
    if not values or len(values) > 32 or any(not isinstance(v, str) or len(v) > 2048 for v in values):
        return None, []
    # RSSHub prefers a single card_subtitle, whose positional format is also
    # consumed by MP's original plugin: year / countries / genres / people.
    if len(values) == 1 and "/" in values[0]:
        parts = [part.strip() for part in values[0].split("/")]
        if len(parts) < 3 or not re.fullmatch(r"(?:18|19|20|21|22)[0-9]{2}", parts[0]) or not parts[1]:
            return None, []
        year = int(parts[0])
        year = year if 1800 <= year <= 2200 else None
        genres = re.split(r"[\s、,，]+", parts[2])
    else:
        # The upstream route explicitly falls back to subject.genres as separate
        # category values when card_subtitle is absent. No extra details lookup.
        year, genres = None, [v.strip() for v in values]
    if len(genres) > 32 or not all(_GENRE.fullmatch(g) for g in genres):
        return year, []
    return year, list(dict.fromkeys(genres))


def _release_date(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 128:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None or not 1800 <= parsed.year <= 2200:
            return None
        return parsed.astimezone(timezone.utc).date().isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


class UpcomingSourceClient:
    """One anonymous RSS snapshot, with optional explicit proxy injection.

    config contains only a requests-compatible `proxies` mapping; no credentials
    are read. collect(source_id, cursor=1, limit=1..60) parses the complete bounded
    feed before sampling. received/window_limited describe the RSS window, never
    the entire Douban catalog. Missing/invalid identities fail the whole snapshot.
    """

    def __init__(self, config, *, stop_callback=None, deadline_seconds=30,
                 connect_timeout=5, read_timeout=10):
        self._closed, self._session = False, None
        self._stop_callback = stop_callback
        if not isinstance(config, Mapping):
            raise SourceReadError("INVALID_CONFIG")
        self._proxies = _proxy_config(config.get("proxies"))
        self._connect_timeout = _bound(connect_timeout, 0.1, 10)
        self._read_timeout = _bound(read_timeout, 0.1, 20)
        self._deadline = time.monotonic() + _bound(deadline_seconds, 1, 120)
        self._session = requests.Session()
        self._session.trust_env = False
        self._session.headers.update({
            "Accept": "application/rss+xml, application/xml, text/xml",
            "Accept-Encoding": "identity", "User-Agent": "MoviePilot-ViewingAssistant RSS reader",
        })

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
                seconds = int(value)
            else:
                parsed = parsedate_to_datetime(value)
                if parsed.tzinfo is None:
                    return None
                seconds = int(parsed.timestamp() - time.time() + .999)
            return max(1, min(3600, seconds))
        except (TypeError, ValueError, OverflowError):
            return None

    def _read(self):
        remaining = self._check()
        try:
            with self._session.get(
                    FEED_URL, proxies=dict(self._proxies), stream=True, allow_redirects=False, verify=True,
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
                    self._fail("SERVER_ERROR")
                if status != 200:
                    self._fail("HTTP_ERROR")
                if response.headers.get("Content-Encoding", "identity").strip().lower() not in {"", "identity"}:
                    self._fail("UNSUPPORTED_ENCODING")
                content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                if content_type not in {"application/rss+xml", "application/xml", "text/xml"}:
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
            return bytes(body)
        except SourceReadError:
            raise
        except Exception:
            self._fail("NETWORK_ERROR")

    def _xml(self, body):
        try:
            text = body.decode("utf-8-sig")
            if ("\x00" in text or re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", text, re.I)
                    or re.search(r"<\?xml[^?]*encoding\s*=\s*['\"](?!utf-?8['\"])[^'\"]+['\"]", text, re.I)):
                self._fail("UNSAFE_XML")
            # Reject declarations before parsing, then bound the tree while it is
            # built. No HTML renderer, external resolver or recovery parser.
            depth, nodes = 0, 0
            parser = ET.iterparse(io.StringIO(text), events=("start", "end"))
            for event, _element in parser:
                self._check()
                if event == "start":
                    depth += 1
                    nodes += 1
                    if depth > MAX_XML_DEPTH or nodes > MAX_XML_NODES:
                        self._fail("INVALID_RESPONSE")
                else:
                    depth -= 1
            root = parser.root
            if root.tag != "rss" or root.get("version") != "2.0" or len(root.findall("channel")) != 1:
                self._fail("INVALID_RESPONSE")
            channel = root.find("channel")
            if (self._text_field(channel, "title") != "豆瓣剧集-即将播出"
                    or self._text_field(channel, "link") != "https://movie.douban.com/tv/"):
                self._fail("INVALID_RESPONSE")
            items = channel.findall("item")
            if not items:
                self._fail("EMPTY_UNEXPECTED")
            if len(items) > MAX_FEED_ITEMS:
                self._fail("ITEM_LIMIT")
            return items
        except SourceReadError:
            raise
        except (ET.ParseError, ValueError, TypeError, UnicodeError, RecursionError):
            self._fail("INVALID_RESPONSE")

    def _text_field(self, element, name, *, required=True):
        values = element.findall(name)
        if not values and not required:
            return None
        if len(values) == 1 and not len(values[0]) and not values[0].text and not required:
            return None
        if len(values) != 1 or len(values[0]) or not isinstance(values[0].text, str):
            self._fail("INVALID_RESPONSE")
        return values[0].text.strip()

    def _item(self, element, observed_at):
        title = _label(self._text_field(element, "title"))
        link = self._text_field(element, "link")
        match = _SUBJECT.fullmatch(link)
        if not title or not match:
            self._fail("INVALID_RESPONSE")
        guid = self._text_field(element, "guid", required=False)
        if guid is not None:
            guid_match = _SUBJECT.fullmatch(guid)
            if not guid_match or guid_match[1] != match[1]:
                self._fail("INVALID_RESPONSE")
        categories = element.findall("category")
        values = [node.text for node in categories] if all(not len(node) for node in categories) else []
        year, genres = _category_metadata(values)
        published = self._text_field(element, "pubDate", required=False)
        identity = match[1]
        return {
            "source_id": SOURCE_ID, "source_item_id": identity, "source_group": SOURCE_GROUP,
            "intent_kind": "discovery", "media_type": "tv", "external_ids": {"douban": identity},
            "season_hint": None, "title": title, "year": year,
            "metadata": {"genres": genres, "languages": [], "decades": [f"{year // 10 * 10}s"] if year else [],
                         "actors": [], "directors": [], "keywords": [], "ratings": []},
            "observed_at": observed_at, "release_date": _release_date(published),
            "list_name": "豆瓣待播热度 · RSSHub", "rank": None, "list_size": None,
            "provenance_url": f"https://movie.douban.com/subject/{identity}/",
        }

    def collect(self, source_id=SOURCE_ID, cursor=1, limit=20):
        self._check()
        if source_id != SOURCE_ID:
            self._fail("INVALID_SOURCE")
        if type(cursor) is not int or cursor != 1:
            self._fail("INVALID_CURSOR")
        if type(limit) is not int or not 1 <= limit <= MAX_LIMIT:
            self._fail("INVALID_LIMIT")
        elements = self._xml(self._read())
        observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        items, seen = [], set()
        for element in elements:
            self._check()
            item = self._item(element, observed_at)
            identity = item["source_item_id"]
            if identity in seen:
                self._fail("DUPLICATE_ITEM")
            seen.add(identity)
            items.append(item)
        self._check()
        return {"source_id": SOURCE_ID, "items": items[:limit], "page": 1, "total_pages": None,
                "next_cursor": None, "fetched_at": observed_at, "truncated": False, "reported_total": None,
                "window_limited": len(items) > limit,
                "observed_window": {"kind": "snapshot", "received": len(items),
                                    "returned": min(len(items), limit), "limit": limit}}
