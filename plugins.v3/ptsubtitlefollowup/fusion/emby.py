"""Bound, read-only Emby snapshot connector; no playback or control endpoints.

Items are provisional until the iterator is exhausted AND snapshot_complete is
true. Callers must stage them before replacing a previous snapshot. Deadlines
and cancellation are cooperative: requests cannot interrupt a stalled OS DNS
resolver, and an in-flight socket read is bounded by its configured timeout.
Use the context manager, including when abandoning an iterator early.
Stable counts and duplicate checks detect some concurrent changes; the REST
endpoint has no documented snapshot token, so this is not a transactional view.
"""
from __future__ import annotations

from collections.abc import Mapping
from email.utils import parsedate_to_datetime
import ipaddress
import json
import math
import re
import time
from urllib.parse import urlsplit, urlunsplit

import requests


MAX_ITEMS = 30_000
MAX_PAGES = 200
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_RETRY_AFTER = 3600
ITEM_FIELDS = "UserDataPlayCount,UserDataLastPlayedDate,ProviderIds,Genres,People,ProductionYear"
ITEM_TYPES = frozenset({"Movie", "Episode", "Series"})
USER_ID = re.compile(r"(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})")
ITEM_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


class EmbyReadError(RuntimeError):
    """Only a fixed category and optional bounded delay; never raw HTTP text."""

    def __init__(self, code, retry_after=None):
        self.code = code
        self.retry_after = retry_after
        super().__init__(f"Emby read failed: {code}")


def _user_id(value):
    if not isinstance(value, str) or not USER_ID.fullmatch(value):
        raise EmbyReadError("INVALID_USER_ID")
    return value.replace("-", "").lower()


def _item_id(value):
    if not isinstance(value, str) or not ITEM_ID.fullmatch(value):
        raise EmbyReadError("INVALID_ITEM_ID")
    return value


def _text(value, limit=512):
    return value[:limit] if isinstance(value, str) else None


def _base_url(value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise EmbyReadError("INVALID_HOST")
    if any(character.isspace() or ord(character) < 32 or ord(character) == 127 for character in value):
        raise EmbyReadError("INVALID_HOST")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        hostname = parsed.hostname
        if (parsed.scheme not in {"http", "https"} or not hostname or parsed.username is not None
                or parsed.password is not None or parsed.query or parsed.fragment
                or "?" in value or "#" in value or "\\" in value or "%" in parsed.netloc
                or (port is not None and not 1 <= port <= 65535)):
            raise ValueError
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            ascii_name = hostname.encode("idna").decode("ascii").rstrip(".")
            labels = ascii_name.split(".")
            if not all(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) for label in labels):
                raise ValueError
        path = parsed.path.rstrip("/")
        if path and (not path.startswith("/") or any(
                part in {"", ".", ".."} or not re.fullmatch(r"[A-Za-z0-9._~-]+", part)
                for part in path[1:].split("/"))):
            raise ValueError
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    except (ValueError, UnicodeError):
        raise EmbyReadError("INVALID_HOST") from None


def _finite_bound(value, lower, upper):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise EmbyReadError("INVALID_LIMIT")
    if not lower <= value <= upper:
        raise EmbyReadError("INVALID_LIMIT")
    return float(value)


class EmbyReadOnlyClient:
    """Construct per operation with the host's current config.host/apikey.

    Public API: describe_server, list_users, verify_binding, list_views,
    iter_items. Session and raw responses are never returned or persisted.
    """

    def __init__(self, server_config, *, page_size=200, deadline_seconds=90,
                 connect_timeout=5, read_timeout=10, stop_callback=None):
        self.snapshot_complete = False
        self.snapshot_count = 0
        self._closed = False
        self._session = None
        self._verified_user_id = None
        self._stop_callback = stop_callback
        if not isinstance(server_config, Mapping):
            raise EmbyReadError("INVALID_CONFIG")
        self._base = _base_url(server_config.get("host"))
        key = server_config.get("apikey")
        if not isinstance(key, str) or not key or len(key) > 4096 or any(ord(c) < 33 or ord(c) == 127 for c in key):
            raise EmbyReadError("INVALID_CREDENTIAL")
        if type(page_size) is not int or page_size not in {200, 400}:
            raise EmbyReadError("INVALID_PAGE_SIZE")
        self._page_size = page_size
        self._connect_timeout = _finite_bound(connect_timeout, 0.1, 15)
        self._read_timeout = _finite_bound(read_timeout, 0.1, 30)
        self._deadline = time.monotonic() + _finite_bound(deadline_seconds, 1, 300)
        self._session = requests.Session()
        self._session.trust_env = False
        self._session.headers.update({"X-Emby-Token": key, "Accept": "application/json", "Accept-Encoding": "identity"})

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        if not self._closed:
            self._closed = True
            if self._session is not None:
                try:
                    self._session.close()
                except Exception:
                    pass
                finally:
                    self._session.headers.pop("X-Emby-Token", None)

    def _fail(self, code, retry_after=None):
        self.snapshot_complete = False
        self.close()
        raise EmbyReadError(code, retry_after) from None

    def _check(self, stop_callback=None):
        if self._closed:
            self._fail("CLOSED")
        try:
            if any(callback is not None and callback() for callback in (self._stop_callback, stop_callback)):
                self._fail("CANCELLED")
        except EmbyReadError:
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
                seconds = math.ceil(parsed.timestamp() - time.time())
            return max(1, min(MAX_RETRY_AFTER, seconds))
        except (TypeError, ValueError, OverflowError):
            return None

    def _get(self, path, params=None, stop_callback=None):
        # The whitelist remains here even though every caller builds a fixed URL.
        allowed = path in {"/System/Info/Public", "/Users"} or re.fullmatch(
            r"/Users/[0-9a-f]{32}(?:/Views|/Items)?", path)
        if re.fullmatch(r"/Items/[A-Za-z0-9_-]{1,128}/Ancestors", path):
            allowed = (self._verified_user_id is not None and isinstance(params, dict)
                       and params.get("UserId") == self._verified_user_id)
        if not allowed:
            self._fail("FORBIDDEN_ROUTE")
        remaining = self._check(stop_callback)
        try:
            with self._session.get(
                self._base + path, params=params, allow_redirects=False, stream=True,
                timeout=(min(self._connect_timeout, remaining), min(self._read_timeout, remaining)),
            ) as response:
                self._check(stop_callback)
                status = response.status_code
                if status == 401:
                    self._fail("AUTH_REQUIRED")
                if status == 403:
                    self._fail("FORBIDDEN")
                if status == 429:
                    self._fail("RATE_LIMITED", self._retry_after(response.headers.get("Retry-After")))
                if 300 <= status < 400:
                    self._fail("REDIRECT_BLOCKED")
                if status == 404:
                    self._fail("NOT_FOUND")
                if status >= 500:
                    self._fail("SERVER_ERROR")
                if status != 200:
                    self._fail("HTTP_ERROR")
                # Do not let transport decompression allocate an unbounded body
                # before the application-side byte counter sees its chunks.
                if response.headers.get("Content-Encoding", "identity").strip().lower() not in {"", "identity"}:
                    self._fail("UNSUPPORTED_ENCODING")
                declared = response.headers.get("Content-Length")
                if declared is not None:
                    try:
                        if int(declared) > MAX_RESPONSE_BYTES or int(declared) < 0:
                            self._fail("RESPONSE_TOO_LARGE")
                    except ValueError:
                        self._fail("INVALID_RESPONSE")
                body = bytearray()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    self._check(stop_callback)
                    if not isinstance(chunk, bytes):
                        self._fail("INVALID_RESPONSE")
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        self._fail("RESPONSE_TOO_LARGE")
                    body.extend(chunk)
                self._check(stop_callback)
                return json.loads(body.decode("utf-8-sig"), parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
        except EmbyReadError:
            raise
        except requests.RequestException:
            self._fail("NETWORK_ERROR")
        except (UnicodeError, ValueError, TypeError):
            self._fail("INVALID_RESPONSE")
        except Exception:
            self._fail("READ_FAILED")

    def describe_server(self):
        raw = self._get("/System/Info/Public")
        if not isinstance(raw, dict) or not isinstance(raw.get("Id"), str) or not ITEM_ID.fullmatch(raw["Id"]):
            self._fail("INVALID_SERVER")
        return {"id": raw["Id"], "name": _text(raw.get("ServerName")), "version": _text(raw.get("Version"), 64)}

    def _user_summary(self, raw):
        if not isinstance(raw, dict):
            self._fail("INVALID_USER_DIRECTORY")
        try:
            identity = _user_id(raw.get("Id"))
        except EmbyReadError:
            self._fail("INVALID_USER_DIRECTORY")
        policy = raw.get("Policy")
        disabled = policy.get("IsDisabled") if isinstance(policy, dict) else None
        return {"id": identity, "name": _text(raw.get("Name")), "disabled": disabled if type(disabled) is bool else None}

    def list_users(self):
        raw = self._get("/Users")
        if not isinstance(raw, list) or len(raw) > 1000:
            self._fail("INVALID_USER_DIRECTORY")
        users = [self._user_summary(user) for user in raw]
        if len({user["id"] for user in users}) != len(users):
            self._fail("INVALID_USER_DIRECTORY")
        return users

    def verify_binding(self, native_server_id, user_id):
        self._verified_user_id = None
        try:
            identity = _user_id(user_id)
        except EmbyReadError:
            self._fail("INVALID_USER_ID")
        if not isinstance(native_server_id, str) or self.describe_server()["id"].casefold() != native_server_id.casefold():
            self._fail("SERVER_MISMATCH")
        user = self._user_summary(self._get(f"/Users/{identity}"))
        if user["id"] != identity:
            self._fail("USER_MISMATCH")
        if user["disabled"] is not False:
            self._fail("USER_DISABLED" if user["disabled"] else "BINDING_UNVERIFIED")
        self._verified_user_id = identity
        return user

    def _bound_user(self, user_id):
        try:
            identity = _user_id(user_id)
        except EmbyReadError:
            self._fail("INVALID_USER_ID")
        if identity != self._verified_user_id:
            self._fail("BINDING_REQUIRED")
        return identity

    def list_views(self, user_id, stop_callback=None):
        identity = self._bound_user(user_id)
        raw = self._get(f"/Users/{identity}/Views", {"EnableImages": "false"}, stop_callback)
        if not isinstance(raw, dict) or not isinstance(raw.get("Items"), list) or len(raw["Items"]) > 1000:
            self._fail("INVALID_VIEWS")
        views = []
        for view in raw["Items"]:
            if not isinstance(view, dict):
                self._fail("INVALID_VIEWS")
            try:
                item = _item_id(view.get("Id"))
            except EmbyReadError:
                self._fail("INVALID_VIEWS")
            views.append({"id": item, "name": _text(view.get("Name")), "collection_type": _text(view.get("CollectionType"), 64)})
        if len({view["id"] for view in views}) != len(views):
            self._fail("INVALID_VIEWS")
        return views

    def _roots(self, user_id, scope, stop_callback=None):
        if not isinstance(scope, Mapping):
            self._fail("SCOPE_REQUIRED")
        mode = scope.get("mode")
        roots = scope.get("library_ids", [])
        if mode == "all_visible":
            if roots != []:
                self._fail("INVALID_SCOPE")
            return [None]
        if mode != "selected_ids" or not isinstance(roots, list) or not 1 <= len(roots) <= 100:
            self._fail("SCOPE_REQUIRED")
        try:
            roots = [_item_id(root) for root in roots]
        except EmbyReadError:
            self._fail("INVALID_SCOPE")
        if len(set(roots)) != len(roots):
            self._fail("INVALID_SCOPE")
        visible = {view["id"] for view in self.list_views(user_id, stop_callback)}
        if not set(roots).issubset(visible):
            self._fail("LIBRARY_NOT_VISIBLE")
        return roots

    def _item(self, raw, allowed_types, expected_user):
        if not isinstance(raw, dict) or raw.get("Type") not in allowed_types:
            self._fail("INVALID_ITEM")
        try:
            identity = _item_id(raw.get("Id"))
        except EmbyReadError:
            self._fail("INVALID_ITEM")
        userdata = raw.get("UserData")
        if not isinstance(userdata, dict):
            self._fail("USERDATA_UNAVAILABLE")
        if "UserId" in userdata:
            try:
                matches_user = _user_id(userdata["UserId"]) == expected_user
            except EmbyReadError:
                matches_user = False
            if not matches_user:
                self._fail("USER_MISMATCH")
        result = {"Id": identity, "Type": raw["Type"], "UserData": {}}
        for key in ("SeriesId", "SeasonId"):
            if key in raw:
                result[key] = _text(raw[key])
        for key in ("ProductionYear", "ParentIndexNumber", "IndexNumber"):
            if key in raw:
                value = raw[key]
                result[key] = value if type(value) is int and value >= 0 else None
        for key in ("Played", "IsFavorite"):
            if key in userdata:
                result["UserData"][key] = userdata[key] if type(userdata[key]) is bool else None
        for key in ("PlayCount",):
            if key in userdata:
                value = userdata[key]
                result["UserData"][key] = value if type(value) is int and value >= 0 else None
        if "LastPlayedDate" in userdata:
            result["UserData"]["LastPlayedDate"] = _text(userdata["LastPlayedDate"], 64)
        providers = raw.get("ProviderIds", {})
        if providers is None:
            providers = {}
        if not isinstance(providers, dict) or len(providers) > 100:
            self._fail("INVALID_ITEM")
        result["ProviderIds"] = {key: value for key, value in providers.items()
                                 if isinstance(key, str) and len(key) <= 64 and isinstance(value, str) and len(value) <= 128}
        genres = raw.get("Genres", [])
        people = raw.get("People", [])
        if genres is None:
            genres = []
        if people is None:
            people = []
        if not isinstance(genres, list) or len(genres) > 200 or not isinstance(people, list) or len(people) > 1000:
            self._fail("INVALID_ITEM")
        result["Genres"] = [item[:128] for item in genres if isinstance(item, str)]
        result["People"] = [{key: _text(person[key], 128) for key in ("Id", "Name", "Type") if key in person}
                            for person in people if isinstance(person, dict)]
        # Only preserve an explicitly returned ISO-639-style language code.
        # Do not infer language from title/region or request an undocumented Field.
        language = raw.get("OriginalLanguage")
        if isinstance(language, str) and re.fullmatch(r"[A-Za-z]{2,3}", language):
            result["OriginalLanguage"] = language.lower()
        return result

    def iter_items(self, user_id, types, scope, stop_callback=None):
        """Yield allowlisted Emby DTOs; completion is false until exhaustion.

        Empty, partial, unstable or capped results raise and must not replace a
        previous snapshot. No retries occur inside this connector.
        """
        self.snapshot_complete = False
        self.snapshot_count = 0
        try:
            identity = self._bound_user(user_id)
            if not isinstance(types, (tuple, list)) or not types or any(item not in ITEM_TYPES for item in types):
                self._fail("INVALID_TYPES")
            types = tuple(dict.fromkeys(types))
            self._check(stop_callback)
            roots = self._roots(identity, scope, stop_callback)
            seen = set()
            pages = 0
            declared_total = 0
            for root in roots:
                start = 0
                root_total = None
                while True:
                    self._check(stop_callback)
                    if pages >= MAX_PAGES:
                        self._fail("PAGE_LIMIT")
                    params = {"Recursive": "true", "IncludeItemTypes": ",".join(types),
                              "EnableUserData": "true", "EnableImages": "false", "Fields": ITEM_FIELDS,
                              "StartIndex": start, "Limit": self._page_size, "SortBy": "SortName", "SortOrder": "Ascending"}
                    if root is not None:
                        params["ParentId"] = root
                    raw = self._get(f"/Users/{identity}/Items", params, stop_callback)
                    pages += 1
                    if not isinstance(raw, dict) or not isinstance(raw.get("Items"), list):
                        self._fail("INVALID_PAGE")
                    total = raw.get("TotalRecordCount")
                    if type(total) is not int or total < 0:
                        self._fail("INVALID_PAGE")
                    if root_total is None:
                        root_total = total
                        declared_total += total
                        if declared_total > MAX_ITEMS:
                            self._fail("ITEM_LIMIT")
                    elif total != root_total:
                        self._fail("SNAPSHOT_CHANGED")
                    items = raw["Items"]
                    expected = min(self._page_size, root_total - start)
                    if len(items) != expected:
                        self._fail("PARTIAL_PAGE")
                    for raw_item in items:
                        self._check(stop_callback)
                        item = self._item(raw_item, types, identity)
                        if item["Id"] in seen:
                            self._fail("DUPLICATE_ITEM")
                        if self.snapshot_count >= MAX_ITEMS:
                            self._fail("ITEM_LIMIT")
                        seen.add(item["Id"])
                        self.snapshot_count += 1
                        yield item
                    start += len(items)
                    if start == root_total:
                        break
            self._check(stop_callback)
            if self.snapshot_count == 0:
                self._fail("EMPTY_SNAPSHOT")
            self.snapshot_complete = True
        except EmbyReadError:
            self.close()
            raise
        except GeneratorExit:
            self.close()
            raise
        except Exception:
            self._fail("READ_FAILED")
        finally:
            if not self.snapshot_complete:
                self.close()
