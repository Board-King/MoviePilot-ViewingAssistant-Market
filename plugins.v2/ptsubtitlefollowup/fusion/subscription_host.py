"""MoviePilot v2.15.6 subscription boundary for ordinary TMDB movies.

Read contracts: Emby /Features requires an administrator and /Items supports
root recursive queries, AnyProviderIdEquals and HasTmdbId. Never query user
watch data. A native add can synchronously emit SubscribeAdded, even when MP
returns an existing SID, and MP may subsequently search a new subscription.
The caller owns authorization, reservations and reconciliation across writers.
"""
from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import hashlib
import inspect
import json
from pathlib import Path
import re
import time
import unicodedata


VERSION = "v2.15.6"
MAX_SUBSCRIPTIONS = 2048
MAX_SERVERS = 8
_SOURCE_HASHES = {
    "app.chain.subscribe": "acb32d818b2cb8bf8dec3062e1b9155595b2045b8de08aa7db3005f6d48b0f95",
    "app.db.subscribe_oper": "83c3c0f6d4a588a7a32b3fc67f343e89c8aa1545301c1553859316aa03bf9b2c",
    "app.db.models.subscribe": "efa92d87ca839f42e2f2c38f591dc0635c21ce93fe5cb5120cd0d261e694a67e",
}


def _tmdb(value):
    if type(value) is int:
        value = str(value)
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,17}", value):
        return value
    return None


def _movie(identity):
    if not isinstance(identity, Mapping):
        return False
    title, year = identity.get("title"), identity.get("year")
    return (identity.get("provider") == "themoviedb" and identity.get("media_type") == "movie"
            and isinstance(identity.get("media_id"), str) and _tmdb(identity["media_id"]) is not None
            and identity.get("season") is None and identity.get("episode_group") is None
            and isinstance(title, str) and bool(title.strip()) and len(title) <= 240 and title.isprintable()
            and (year is None or type(year) is int and 1800 <= year <= 2200))


def _owner_name(owner):
    return (isinstance(owner, str) and bool(owner) and owner == owner.strip()
            and len(owner) <= 128 and owner.isprintable())


class _NotSent(Exception):
    """This call stopped strictly before native subscription persistence."""


def _permit(callback):
    try:
        allowed = callable(callback) and callback() is True
    except Exception:
        allowed = False
    if not allowed:
        raise _NotSent()


def _existing(rows, identity, owner, *, external_ids=None):
    """Classify both canonical and legacy identities across all owners."""
    if len(rows) > MAX_SUBSCRIPTIONS:
        return {"status": "unknown", "sid": None}
    matches = []
    incomplete = False
    for row in rows:
        tmdb_ids = set()
        provider, media_id = row["media_source"], row["media_id"]
        if provider in ("themoviedb", "tmdb"):
            tmdb_ids.add(_tmdb(media_id))
        if row["tmdbid"] is not None:
            tmdb_ids.add(_tmdb(row["tmdbid"]))
        legacy_key = row["mediaid"]
        if isinstance(legacy_key, str) and legacy_key.startswith(("tmdb:", "themoviedb:")):
            tmdb_ids.add(_tmdb(legacy_key.split(":", 1)[1]))
        if identity["media_id"] not in tmdb_ids:
            # Compare in Douban's own namespace only when the movie and its
            # exact IMDb cross-reference are both known; never search by name.
            if not tmdb_ids and provider == "douban" and row["type"] == "电影" and external_ids:
                douban_id = _tmdb(media_id)
                aliases = [row.get("doubanid")]
                if legacy_key:
                    aliases.append(legacy_key.removeprefix("douban:") if isinstance(legacy_key, str)
                                   and legacy_key.startswith("douban:") else False)
                if douban_id and all(value in (None, "") or _tmdb(value) == douban_id for value in aliases):
                    candidate_douban = external_ids(identity["media_id"]).get("douban")
                    if candidate_douban == douban_id:
                        return {"status": "conflict", "sid": None}
                    if _tmdb(candidate_douban):
                        continue
            # An unmapped movie may already represent the request on a different
            # provider. An explicit TV row with no matching key cannot own it.
            if row["type"] != "电视剧" and (not tmdb_ids or None in tmdb_ids or len(tmdb_ids) != 1):
                incomplete = True
            continue
        exact_owner_row = (row["type"] == "电影" and row["season"] is None and row["episode_group"] is None
                           and row["username"] == owner and row["best_version"] == 0
                           and tmdb_ids == {identity["media_id"]}
                           and provider in (None, "", "tmdb", "themoviedb")
                           and type(row["id"]) is int and row["id"] > 0)
        if not exact_owner_row:
            return {"status": "conflict", "sid": None}
        matches.append(row["id"])
    if len(matches) > 1:
        return {"status": "conflict", "sid": None}
    if matches:
        return {"status": "existing", "sid": matches[0]}
    return {"status": "unknown" if incomplete else "absent", "sid": None}


def _douban_movie_id(result):
    if not isinstance(result, dict):
        return None
    value = result.get("id")
    if _tmdb(value):
        return _tmdb(value)
    match = re.fullmatch(r"https://api\.douban\.com/movie/([1-9][0-9]{0,17})/?", value) if isinstance(value, str) else None
    return match.group(1) if match else None


class _ExactRecognition:
    """One call's receiver for the pinned native method; no singleton edits."""

    def __init__(self, chain, identity, movie_type, may_dispatch):
        self.chain, self.identity, self.movie_type = chain, identity, movie_type
        self.may_dispatch = may_dispatch
        self.past_guard = False

    def _check(self, media):
        if (media is None or media.type != self.movie_type or media.source != "themoviedb"
                or _tmdb(media.tmdb_id) != self.identity["media_id"]
                or getattr(media, "media_id", None) not in (None, "", self.identity["media_id"],
                                                            int(self.identity["media_id"]))
                or getattr(media, "episode_group", None) is not None):
            raise _NotSent("Native media identity mismatch")
        return media

    def recognize_media(self, **kwargs):
        # Raising here prevents SubscribeChain.add's same-provider title fallback.
        _permit(self.may_dispatch)
        return self._check(self.chain.recognize_media(**kwargs))

    def obtain_images(self, *, mediainfo):
        result = self.chain.obtain_images(mediainfo=mediainfo)
        self._check(mediainfo)
        return result

    def _defaults(self, *args, **kwargs):
        values = self.chain._SubscribeChain__get_default_kwargs(*args, **kwargs)
        if values.get("best_version") not in (None, False, 0):
            raise _NotSent("Default preset requests an upgrade")
        # In the pinned add body the very next operation is SubscribeOper.add.
        _permit(self.may_dispatch)
        self.past_guard = True
        return values

    def __getattr__(self, name):
        if name == "_SubscribeChain__get_default_kwargs":
            return self._defaults
        return getattr(self.chain, name)


class _MoviePilot:
    def __init__(self):
        from importlib import import_module
        from version import APP_VERSION
        if APP_VERSION != VERSION:
            raise ValueError("Unsupported host version")
        modules = {name: import_module(name) for name in _SOURCE_HASHES}
        for name, digest in _SOURCE_HASHES.items():
            source = Path(inspect.getsourcefile(modules[name]))
            if source.stat().st_size > 1024 * 1024 or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                raise ValueError("Unsupported host source")
        from app.db import Engine
        from app.db.models.user import User
        from app.db.models.subscribehistory import SubscribeHistory
        from app.schemas.types import MediaType
        from sqlalchemy import select
        self.engine, self.user, self.select = Engine, User, select
        self.subscribe = modules["app.db.models.subscribe"].Subscribe
        self.history = SubscribeHistory
        self.chain_type = modules["app.chain.subscribe"].SubscribeChain
        self.movie_type = MediaType.MOVIE
        add = self.chain_type.add
        parameters = inspect.signature(add).parameters
        if (add.__module__ != "app.chain.subscribe" or add.__qualname__ != "SubscribeChain.add"
                or not {"title", "year", "mtype", "media_source", "media_id", "tmdbid", "username",
                        "season", "episode_group", "message", "kwargs"} <= parameters.keys()
                or parameters["kwargs"].kind != inspect.Parameter.VAR_KEYWORD):
            raise ValueError("Unsupported native add")
        self.native_add = add

    def owner_context(self, owner):
        # SQLite file replacement is a new host scope, even at the same path.
        if self.engine.dialect.name != "sqlite" or not self.engine.url.database:
            raise ValueError("Unsupported database scope")
        database = Path(self.engine.url.database).resolve(strict=True)
        stat = database.stat()
        if not database.is_file() or stat.st_ino <= 0:
            raise ValueError("Database identity unavailable")
        host_key = hashlib.sha256(f"{database}|{stat.st_dev}|{stat.st_ino}".encode()).hexdigest()
        user = self.user
        with self.engine.connect() as connection:
            rows = connection.execute(self.select(user.id, user.name, user.is_active)
                                      .where(user.name == owner).limit(2)).mappings().all()
        if (len(rows) != 1 or rows[0]["name"] != owner or rows[0]["is_active"] is not True
                or type(rows[0]["id"]) is not int or rows[0]["id"] <= 0):
            raise ValueError("Active owner unavailable")
        return str(rows[0]["id"]), host_key

    def subscriptions(self, history=False):
        sub = self.history if history else self.subscribe
        with self.engine.connect() as connection:
            statement = self.select(sub.id, sub.username, sub.type, sub.media_source, sub.media_id,
                                    sub.mediaid, sub.tmdbid, sub.doubanid, sub.season, sub.episode_group,
                                    sub.best_version).limit(MAX_SUBSCRIPTIONS + 1)
            return connection.execute(statement).mappings().all()

    def external_ids(self, media_id):
        from app.core.config import settings
        from app.modules.douban.apiv2 import DoubanApi
        from .sources import TmdbSourceClient
        config = {"api_key": settings.TMDB_API_KEY, "proxies": settings.PROXY if settings.PROXY_HOST else None}
        with TmdbSourceClient(config, deadline_seconds=10) as client:
            detail = client.display_detail("movie", media_id)
        imdb = detail.get("imdb_id")
        if _tmdb(detail.get("id")) != media_id or not isinstance(imdb, str) or not re.fullmatch(r"tt[0-9]{7,12}", imdb):
            return {}
        # This native endpoint accepts only an IMDb ID. Higher-level MP search
        # APIs silently fall back to a fuzzy title match when no ID is found.
        douban = _douban_movie_id(DoubanApi().imdbid(imdb))
        return {"imdb": imdb, "douban": douban}

    def add(self, identity, owner, may_dispatch):
        receiver = _ExactRecognition(self.chain_type(), identity, self.movie_type, may_dispatch)
        _permit(may_dispatch)
        try:
            return self.native_add(receiver, title=identity["title"], year=str(identity["year"] or ""),
                                   mtype=self.movie_type, media_source="themoviedb", media_id=identity["media_id"],
                                   tmdbid=int(identity["media_id"]), season=None, episode_group=None,
                                   username=owner, owner_scope=True, message=False)
        except _NotSent:
            if receiver.past_guard:
                raise RuntimeError("Native persistence outcome unknown") from None
            raise


@lru_cache(maxsize=1)
def _verified_host():
    return _MoviePilot()


def _query_result(response):
    if not isinstance(response, dict):
        raise ValueError("Invalid item query")
    count, items = response.get("TotalRecordCount"), response.get("Items")
    if (type(count) is not int or count < 0 or not isinstance(items, list)
            or len(items) != min(count, 1) or items and not isinstance(items[0], dict)):
        raise ValueError("Incomplete item query")
    return items


def _title_key(value):
    if not isinstance(value, str) or len(value) > 512:
        return ""
    return "".join(c for c in unicodedata.normalize("NFKC", value).casefold() if c.isalnum())


def _unidentified_movies(get, query, first):
    """Only cache complete bounded reads, without paths, user data or playback history."""
    records = []
    count = first.get("TotalRecordCount")
    if type(count) is not int or not 0 <= count <= 512:
        raise ValueError("Unidentified movie inventory exceeded bounds")
    seen = set()
    page = first
    while True:
        rows = page.get("Items")
        if (page.get("TotalRecordCount") != count or not isinstance(rows, list)
                or len(rows) != min(128, count - len(records))):
            raise ValueError("Incomplete unidentified movie inventory")
        for row in rows:
            if not isinstance(row, dict) or row.get("Type") != "Movie" or not isinstance(row.get("ProviderIds"), dict):
                raise ValueError("Invalid movie record")
            key = row.get("Id")
            if (not isinstance(key, str) or not key or key in seen
                    or any(str(k).casefold() == "tmdb" for k in row["ProviderIds"])):
                raise ValueError("Unstable or unfiltered movie inventory")
            seen.add(key)
            names = frozenset(filter(None, (_title_key(row.get("Name")), _title_key(row.get("OriginalTitle")))))
            year = row.get("ProductionYear")
            records.append((names, year if type(year) is int and 1800 <= year <= 2200 else None))
        if len(records) == count:
            return records
        page = get("/Items", {**query, "StartIndex": len(records)})


def _unidentified_match(records, identity):
    if not records:
        return "absent"
    name, year = _title_key(identity["title"]), identity.get("year")
    for names, record_year in records:
        year_overlaps = year is None or record_year is None or abs(year - record_year) <= 1
        if not year_overlaps:
            continue
        if not name or not names or name in names:
            return "unknown"
        if any(min(len(name), len(label)) >= 4 and (name in label or label in name) for label in names):
            return "unknown"
    return "no_known_match"


def _emby_movie(config, identity, deadline, indexed_only, gap_cache):
    import requests
    from .emby import _base_url
    base, token = _base_url(config.get("host")), config.get("apikey")
    if not isinstance(token, str) or not re.fullmatch(r"[!-~]{1,4096}", token):
        raise ValueError("Invalid server credentials")
    with requests.Session() as session:
        session.trust_env = False
        session.headers.update({"X-Emby-Token": token, "Accept": "application/json", "Accept-Encoding": "identity"})

        def get(path, params):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("Library lookup expired")
            session.cookies.clear()
            with session.get(base + path, params=params, allow_redirects=False, stream=True, verify=True,
                             timeout=(min(3, remaining), min(5, remaining))) as response:
                if (response.status_code != 200 or response.headers.get("Content-Encoding", "identity") != "identity"
                        or response.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json"):
                    raise ValueError("Library lookup unavailable")
                data = bytearray()
                for chunk in response.iter_content(16384):
                    if len(data) + len(chunk) > 256 * 1024 or time.monotonic() >= deadline:
                        raise ValueError("Library response exceeded bounds")
                    data.extend(chunk)
                return json.loads(data.decode("utf-8-sig"))

        # This admin-only endpoint exposes feature names, not keys or user
        # activity. A regular profile token must never establish global absence.
        features = get("/Features", {})
        if (not isinstance(features, list) or not features or len(features) > 512
                or not all(isinstance(value, dict) and isinstance(value.get("Id"), str) for value in features)):
            return "unknown"
        query = {"Recursive": "true", "IncludeItemTypes": "Movie", "Fields": "ProviderIds",
                 "EnableUserData": "false", "EnableImages": "false", "Limit": 1, "StartIndex": 0}
        media_id = identity["media_id"]
        hits = _query_result(get("/Items", {**query, "AnyProviderIdEquals": "tmdb." + media_id}))
        if hits:
            item = hits[0]
            providers = item.get("ProviderIds")
            if (item.get("Type") != "Movie" or not isinstance(providers, dict)
                    or {_tmdb(value) for key, value in providers.items() if key.casefold() == "tmdb"} != {media_id}
                    or item.get("LocationType") == "Virtual"):
                return "unknown"
            return "present"
        # A negative ID query alone misses movies with incomplete metadata.
        gap_query = {**query, "HasTmdbId": "false"}
        if not indexed_only:
            return "unknown" if _query_result(get("/Items", gap_query)) else "absent"
        # A rank-based grant may use identified movies, while similar unindexed
        # records still block dispatch. This result is distinct from full absence.
        cache_key = hashlib.sha256((base + "\0" + token).encode()).digest()
        expiry, records = gap_cache.get(cache_key, (0, ()))
        if expiry <= time.monotonic():
            gap_query.update(Fields="ProviderIds,OriginalTitle,ProductionYear", Limit=128, SortBy="SortName")
            records = _unidentified_movies(get, gap_query, get("/Items", gap_query))
            if len(gap_cache) >= MAX_SERVERS:
                gap_cache.clear()
            gap_cache[cache_key] = (time.monotonic() + 15, records)
        return _unidentified_match(records, identity)


class HostSubscriptionAdapter:
    def __init__(self, server_supplier, *, native_factory=_verified_host, library_probe=_emby_movie):
        self._servers, self._native, self._probe = server_supplier, native_factory, library_probe
        self._gap_cache = {}
        self._external_ids = lru_cache(maxsize=8)(self._read_external_ids)

    def _read_external_ids(self, media_id):
        try:
            return self._native().external_ids(media_id)
        except Exception:
            return {}

    def compatibility(self, owner):
        result = {"ready": False, "version": "", "owner_key": "", "host_key": ""}
        if _owner_name(owner):
            try:
                host = self._native()
                result["version"] = VERSION
                owner_key, host_key = host.owner_context(owner)
                result.update(ready=True, owner_key=owner_key, host_key=host_key)
            except Exception:
                pass
        return result

    def lookup_subscription(self, identity, owner, *, indexed_only=False):
        if _movie(identity) and self.compatibility(owner)["ready"]:
            try:
                host = self._native()
                external = self._external_ids if indexed_only else None
                current = _existing(host.subscriptions(), identity, owner, external_ids=external)
                if current["status"] != "absent":
                    return current
                history = _existing(host.subscriptions(history=True), identity, owner, external_ids=external)
                if history["status"] in {"existing", "conflict"}:
                    return {"status": "conflict", "sid": None}
                return history
            except Exception:
                pass
        return {"status": "unknown", "sid": None}

    def lookup_library(self, identity, *, indexed_only=False, fresh=False):
        if not _movie(identity):
            return "unknown"
        try:
            servers = self._servers()
            if not isinstance(servers, list) or not 1 <= len(servers) <= MAX_SERVERS:
                return "unknown"
            statuses = []
            deadline = time.monotonic() + 20
            for server in servers:
                if (not isinstance(server, dict) or server.get("enabled") is not True
                        or str(server.get("type", "")).casefold() != "emby"
                        or not isinstance(server.get("config"), dict) or time.monotonic() >= deadline):
                    statuses.append("unknown")
                    continue
                try:
                    status = self._probe(server["config"], identity, deadline, indexed_only is True,
                                         {} if fresh else self._gap_cache)
                except Exception:
                    status = "unknown"
                if status == "present":
                    return "present"
                statuses.append(status)
            if all(status == "absent" for status in statuses):
                return "absent"
            if indexed_only is True and all(status in ("absent", "no_known_match") for status in statuses):
                return "no_known_match"
            return "unknown"
        except Exception:
            return "unknown"

    def add(self, identity, owner, *, may_dispatch=None, indexed_only=False):
        """One native call at most. Anything ambiguous requires reconciliation."""
        unknown = {"kind": "unknown", "sid": None}
        not_sent = {"kind": "not_sent", "sid": None}
        try:
            _permit(may_dispatch)
        except _NotSent:
            return not_sent
        initial = self.compatibility(owner)
        if not initial["ready"] or not _movie(identity):
            return not_sent
        before = self.lookup_subscription(identity, owner, indexed_only=indexed_only)
        if before["status"] == "existing":
            return {"kind": "existing", "sid": before["sid"]}
        if before["status"] != "absent":
            return not_sent
        library = self.lookup_library(identity, indexed_only=indexed_only, fresh=True)
        if library != "absent" and not (indexed_only is True and library == "no_known_match"):
            return not_sent
        try:
            if self.compatibility(owner) != initial or self.lookup_subscription(identity, owner, indexed_only=indexed_only)["status"] != "absent":
                return not_sent
            _permit(may_dispatch)
            sid, message = self._native().add(identity, owner, may_dispatch)
            if type(sid) is not int or sid <= 0:
                return unknown
            kind = {"新增订阅成功": "added", "订阅已存在": "existing"}.get(message)
            if (kind is None or self.compatibility(owner) != initial
                    or self.lookup_subscription(identity, owner, indexed_only=indexed_only) != {"status": "existing", "sid": sid}):
                return {"kind": "unknown", "sid": sid}
            return {"kind": kind, "sid": sid}
        except _NotSent:
            return not_sent
        except Exception:
            return unknown
