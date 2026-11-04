"""Shadow catalog collection using bounded pages and one host-storage record.

Pool and cursor advance together only after a page has been validated. This is
not an execution/outbox repository and never modifies host subscriptions.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import re
import threading
import time

from .candidates import merge_pool, rank_candidates
from .service import binding_key, normalize as normalize_profile
from .catalog import SOURCE_SPECS, CatalogSourceClient
from .follow_context import normalize_follow_context, follow_fingerprint
from .sources import SourceReadError


DATA_KEY = "fusion_candidates_v1"
FOLLOW_SOURCE = "mp_followed_shares"


def _bounded_int(value, default, maximum):
    try:
        return max(1, min(maximum, int(value)))
    except (ValueError, TypeError, OverflowError):
        return default


def normalize(raw):
    selected = raw.get("fusion_discovery_sources", ["tmdb_popular_movie"])
    selected = sorted({s for s in selected if isinstance(s, str) and s in SOURCE_SPECS}) if isinstance(selected, list) else []
    return {"enabled": raw.get("fusion_discovery_enabled") is True,
            "sources": selected,
            "pages": _bounded_int(raw.get("fusion_discovery_pages", 3), 3, 3),
            "hours": _bounded_int(raw.get("fusion_discovery_interval_hours", 6), 6, 168),
            "profile_binding": binding_key(normalize_profile(raw))}


def _context(config):
    payload = {k: config[k] for k in ("sources", "pages", "profile_binding")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _utc_timestamp(value):
    if not isinstance(value, str) or len(value) > 64:
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


class CandidateService:
    def __init__(self, read, write, resolve_config, profile_supplier, *,
                 client_factory=CatalogSourceClient, clock=time.time, source_context_supplier=None):
        self._read, self._write = read, write
        self._resolve, self._profile = resolve_config, profile_supplier
        self._client_factory, self._clock = client_factory, clock
        self._lock, self._run_lock = threading.RLock(), threading.Lock()
        self._epoch, self._cancel = 0, threading.Event()
        self._config, self._state = normalize({}), {}
        self._source_context_supplier = source_context_supplier
        self._follow_context, self._follow_digest = None, None
        self._follow_generation, self._follow_cancel = 0, threading.Event()
        self._context_serial = 0
        self._isolation_dirty = False

    def configure(self, raw):
        config = normalize(raw)
        with self._lock:
            self._cancel.set()
            self._cancel = threading.Event()
            self._follow_cancel.set()
            self._follow_cancel = threading.Event()
            self._follow_generation += 1
            self._follow_context, self._follow_digest = None, None
            self._isolation_dirty = False
            self._epoch += 1
            self._config = config
            stored = self._read(DATA_KEY)
            if not isinstance(stored, dict) or stored.get("schema") != 1 or stored.get("context") != _context(config):
                stored = {"schema": 1, "context": _context(config), "pool": {}, "sources": {}}
            self._state = copy.deepcopy(stored)
            for source_id in config["sources"]:
                state = self._state["sources"].setdefault(source_id, {
                    "cursor": 1, "cycle_pages": 0, "status": "queued", "next_due": 0,
                    "last_success": None, "retry_until": 0, "authentication_blocked": False,
                    "last_count": 0,
                })
                if state["status"] == "running":
                    state["status"] = "queued"
            if not config["enabled"]:
                self._cancel.set()
            # A host-scope change must be applied before a restored share pool
            # becomes observable or is written back as current state.
        self._synchronize_context()
        with self._lock:
            try:
                self._write(DATA_KEY, copy.deepcopy(self._state))
                self._isolation_dirty = False
            except Exception:
                if FOLLOW_SOURCE not in self._config["sources"]:
                    raise
                self._isolation_dirty = True

    def _without_followed(self, pool):
        remaining = {}
        for key, row in pool.items() if isinstance(pool, dict) else []:
            if not isinstance(row, dict) or not isinstance(row.get("sources"), list):
                continue
            sources = [source for source in row["sources"] if isinstance(source, dict)
                       and source.get("source_id") != FOLLOW_SOURCE]
            if sources:
                remaining[key] = {**row, "sources": sources}
        return merge_pool(remaining, [], as_of=datetime.fromtimestamp(self._clock(), timezone.utc))[0]

    def _synchronize_context(self):
        """Read only the local host gate; isolate first, then attempt persistence."""
        with self._lock:
            epoch = self._epoch
            selected = FOLLOW_SOURCE in self._config["sources"]
            enabled = self._config["enabled"]
            self._context_serial += 1
            serial = self._context_serial
        if not selected:
            return
        raw = {"context_valid": True, "sharing_enabled": False, "followed_uids": []}
        if enabled:
            try:
                raw = self._source_context_supplier() if self._source_context_supplier is not None else None
            except Exception:
                raw = None
        context = normalize_follow_context(raw)
        digest = follow_fingerprint(context)
        with self._lock:
            if epoch != self._epoch or serial != self._context_serial:
                return
            stored_contexts = self._state.get("source_contexts")
            stored_contexts = stored_contexts if isinstance(stored_contexts, dict) else {}
            changed = stored_contexts.get(FOLLOW_SOURCE) != digest
            if self._follow_context is None or self._follow_digest != digest:
                self._follow_cancel.set()
                self._follow_cancel = threading.Event()
                self._follow_generation += 1
                self._follow_context, self._follow_digest = context, digest
            if context["error_code"] is not None:
                self._follow_cancel.set()
            if changed:
                candidate = copy.deepcopy(self._state)
                candidate["pool"] = self._without_followed(candidate.get("pool"))
                candidate["source_contexts"] = {FOLLOW_SOURCE: digest}
                previous = candidate["sources"].get(FOLLOW_SOURCE, {})
                retry_until = previous.get("retry_until", 0)
                authentication_blocked = previous.get("authentication_blocked") is True
                error_code = context["error_code"]
                candidate["sources"][FOLLOW_SOURCE] = {
                    "cursor": 1, "cycle_pages": 0, "next_due": max(self._clock(), retry_until),
                    "last_success": None, "retry_until": retry_until, "last_count": 0,
                    "authentication_blocked": authentication_blocked,
                    "context_error": error_code,
                    "status": "unavailable" if error_code else "error" if authentication_blocked else "queued",
                    "error_code": error_code or (previous.get("error_code") if retry_until > self._clock()
                                                  or authentication_blocked else None),
                }
                # Revocation cannot wait for a successful disk/database write.
                self._state = candidate
                self._isolation_dirty = True
            if self._isolation_dirty:
                try:
                    self._write(DATA_KEY, copy.deepcopy(self._state))
                    self._isolation_dirty = False
                except Exception:
                    pass

    def _follow_ready(self):
        return bool(self._follow_context is not None and self._follow_context["error_code"] is None
                    and not self._follow_cancel.is_set() and not self._isolation_dirty)

    @property
    def enabled(self):
        with self._lock:
            return self._config["enabled"]

    def stop(self):
        with self._lock:
            self._cancel.set()
            self._follow_cancel.set()
            self._epoch += 1

    def _capture(self):
        with self._lock:
            if not self._config["enabled"] or self._cancel.is_set() or not self._config["sources"]:
                return None
            return (self._epoch, self._cancel, copy.deepcopy(self._config), copy.deepcopy(self._state),
                    (self._follow_generation, self._follow_cancel, copy.deepcopy(self._follow_context)))

    def _current(self, capture, source_id=None):
        return (capture is not None and capture[0] == self._epoch and not capture[1].is_set()
                and self._config["enabled"] and (source_id != FOLLOW_SOURCE or
                     (capture[4][0] == self._follow_generation and not capture[4][1].is_set()
                      and self._follow_ready())))

    def _commit(self, capture, replacement):
        with self._lock:
            if not self._current(capture):
                return False
            self._write(DATA_KEY, copy.deepcopy(replacement))
            self._state = replacement
            self._isolation_dirty = False
            return True

    def _error(self, capture, source_id, error):
        code = str(getattr(error, "code", "FAILED"))
        allowed = {"AUTH_REQUIRED", "FORBIDDEN", "RATE_LIMITED", "NETWORK_ERROR", "DEADLINE_EXCEEDED",
                   "INVALID_RESPONSE", "INVALID_PAGE", "RESPONSE_TOO_LARGE", "INVALID_CREDENTIAL",
                   "REDIRECT_BLOCKED", "CANCELLED", "SERVER_ERROR", "UNSUPPORTED_ENCODING", "FAILED",
                   "INVALID_CONFIG", "INVALID_PROXY", "DUPLICATE_ITEM", "NOT_FOUND", "HTTP_ERROR",
                   "EMPTY_UNEXPECTED", "INVALID_FEED", "UNSAFE_XML", "ITEM_LIMIT"}
        code = code if code in allowed else "FAILED"
        blocked = code in {"AUTH_REQUIRED", "FORBIDDEN", "INVALID_CREDENTIAL"}
        try:
            delay = max(300, min(3600, int(getattr(error, "retry_after", None) or 300)))
        except (ValueError, TypeError, OverflowError):
            delay = 300
        if blocked:
            delay = 3600
        with self._lock:
            if not self._current(capture, source_id):
                return
            candidate = copy.deepcopy(self._state)
            targets = ([sid for sid in self._config["sources"]
                        if SOURCE_SPECS[sid]["throttle_group"] == SOURCE_SPECS[source_id]["throttle_group"]]
                       if code == "RATE_LIMITED" or blocked else [source_id])
            # Rate limits follow the actual upstream, independently of the
            # related-evidence group used for ranking. Other providers keep working.
            for target in targets:
                candidate["sources"][target].update(
                    status="error", error_code=code, retry_until=self._clock() + delay,
                    next_due=self._clock() + delay, authentication_blocked=blocked)
            try:
                self._commit(capture, candidate)
            except Exception:
                # Do not expose SQL exceptions or publish an uncommitted page.
                candidate["sources"][source_id].update(error_code="STORAGE_FAILED")
                self._state = candidate

    def request_refresh(self):
        self._synchronize_context()
        with self._lock:
            capture = self._capture()
            if not capture:
                return {"success": False, "message": "请先启用并选择候选来源"}
            if self._run_lock.locked():
                return {"success": True, "message": "候选采集仍在进行，请稍后刷新状态"}
            candidate = copy.deepcopy(self._state)
            queued = []
            for source_id in self._config["sources"]:
                state = candidate["sources"][source_id]
                if source_id == FOLLOW_SOURCE and not self._follow_ready():
                    continue
                if self._clock() < state.get("retry_until", 0):
                    continue
                # Explicit admin retry may probe credentials after their cooldown;
                # the periodic collector never retries an authentication failure.
                state.update(status="queued", cursor=1, cycle_pages=0, next_due=self._clock(),
                             authentication_blocked=False, error_code=None)
                queued.append(source_id)
            if not queued:
                return {"success": False, "message": "来源暂不可用或处于冷却中，请检查宿主分享设置与来源状态"}
            try:
                self._commit(capture, candidate)
            except Exception as error:
                for source_id in queued:
                    self._error(capture, source_id, error)
                return {"success": False, "message": "未能保存采集请求，请检查宿主存储"}
            return {"success": True, "message": "候选采集已排队，通常 1 分钟内开始"}

    def tick(self):
        self._synchronize_context()
        if not self._capture() or not self._run_lock.acquire(blocking=False):
            return
        try:
            self._synchronize_context()
            capture = self._capture()
            if not capture:
                return
            _, cancel, config, _, _ = capture
            for source_id in config["sources"]:
                if source_id == FOLLOW_SOURCE:
                    self._synchronize_context()
                current = self._capture()
                if not current or current[0] != capture[0]:
                    return
                state = current[3]["sources"][source_id]
                if source_id == FOLLOW_SOURCE and not self._follow_ready():
                    continue
                if state.get("authentication_blocked") or self._clock() < max(state.get("next_due", 0), state.get("retry_until", 0)):
                    continue
                try:
                    client_config = self._resolve()
                    stop_callback = cancel.is_set
                    if source_id == FOLLOW_SOURCE:
                        self._synchronize_context()
                        with self._lock:
                            if not self._current(current, source_id):
                                continue
                        context = current[4][2]
                        client_config = {**client_config, **{key: copy.deepcopy(context[key]) for key in
                                         ("context_valid", "sharing_enabled", "followed_uids")}}
                        follow_cancel = current[4][1]
                        stop_callback = lambda: cancel.is_set() or follow_cancel.is_set()
                    with self._client_factory(client_config, stop_callback=stop_callback) as client:
                        limit = 20 * config["pages"] if SOURCE_SPECS[source_id]["pagination"] == "snapshot" else 20
                        batch = client.collect(source_id, cursor=state["cursor"], limit=limit)
                    if cancel.is_set():
                        return
                    self._synchronize_context()
                    self._save_page(current, source_id, batch)
                except Exception as error:
                    self._synchronize_context()
                    self._error(current, source_id, error)
        finally:
            self._run_lock.release()

    def _save_page(self, capture, source_id, batch):
        old = capture[3]["sources"][source_id]
        spec = SOURCE_SPECS[source_id]
        snapshot = spec["pagination"] == "snapshot"
        filtered = spec["pagination"] == "filtered_pages"
        limit = 20 * capture[2]["pages"] if snapshot else 20
        if (not isinstance(batch, dict) or batch.get("source_id") != source_id
                or type(batch.get("page")) is not int or batch["page"] != old["cursor"]
                or not isinstance(batch.get("items"), list) or len(batch["items"]) > limit
                or batch.get("truncated") is not False or not _utc_timestamp(batch.get("fetched_at"))):
            raise SourceReadError("INVALID_RESPONSE")
        next_cursor = batch.get("next_cursor")
        total_pages = batch.get("total_pages")
        items, page = batch["items"], batch["page"]
        if snapshot:
            window = batch.get("observed_window")
            if (page != 1 or "total_pages" not in batch or total_pages is not None
                    or "next_cursor" not in batch or next_cursor is not None
                    or "reported_total" not in batch or batch["reported_total"] is not None
                    or not isinstance(window, dict) or window.get("kind") != "snapshot"
                    or type(window.get("received")) is not int or not 1 <= window["received"] <= spec["max_received"]
                    or type(window.get("returned")) is not int or window["returned"] != len(items)
                    or type(window.get("limit")) is not int or window["limit"] != limit
                    or len(items) != min(window["received"], limit)
                    or type(batch.get("window_limited")) is not bool
                    or batch["window_limited"] != (window["received"] > len(items))):
                raise SourceReadError("INVALID_RESPONSE")
            observed_window = {"kind": "snapshot", "received": window["received"],
                               "returned": len(items), "limit": limit}
        elif filtered:
            window = batch.get("observed_window")
            if (not 1 <= page <= 3 or "total_pages" not in batch or total_pages is not None
                    or "reported_total" not in batch or batch["reported_total"] is not None
                    or "next_cursor" not in batch or not isinstance(window, dict)
                    or window.get("kind") != "filtered_pages"
                    or type(window.get("page")) is not int or window["page"] != page
                    or type(window.get("received")) is not int or not 0 <= window["received"] <= 20
                    or type(window.get("matched")) is not int or not 0 <= window["matched"] <= window["received"]
                    or type(window.get("returned")) is not int or window["returned"] != len(items)
                    or len(items) > window["matched"]
                    or type(window.get("excluded")) is not int or window["excluded"] != window["matched"] - len(items)
                    or type(window.get("page_size")) is not int or window["page_size"] != 20):
                raise SourceReadError("INVALID_RESPONSE")
            expected_cursor = page + 1 if window["received"] == 20 else None
            if next_cursor != expected_cursor or (next_cursor is not None and type(next_cursor) is not int):
                raise SourceReadError("INVALID_RESPONSE")
            observed_window = {key: window[key] for key in
                               ("kind", "page", "received", "matched", "returned", "excluded", "page_size")}
            observed_window["page_cap"] = capture[2]["pages"]
        else:
            if (type(total_pages) is not int or not 0 <= total_pages <= 1_000_000
                    or (items and total_pages < page) or (not items and total_pages >= page)):
                raise SourceReadError("INVALID_RESPONSE")
            expected_cursor = page + 1 if items and page < min(total_pages, 500) else None
            if next_cursor != expected_cursor or (next_cursor is not None and type(next_cursor) is not int):
                raise SourceReadError("INVALID_RESPONSE")
            observed_window = {"kind": "numbered", "page": page, "returned": len(items),
                               "page_size": 20, "page_cap": capture[2]["pages"]}
        observed_ids = set()
        for item in items:
            if (not isinstance(item, dict) or item.get("source_id") != source_id
                    or item.get("source_group") != spec["group"]
                    or item.get("intent_kind") != "discovery" or item.get("observed_at") != batch["fetched_at"]
                    or not isinstance(item.get("title"), str) or not item["title"].strip()
                    or not isinstance(item.get("metadata"), dict)
                    or not isinstance(item.get("source_item_id"), str)
                    or not re.fullmatch(r"[1-9][0-9]{0,19}", item["source_item_id"])
                    or item["source_item_id"] in observed_ids):
                raise SourceReadError("INVALID_RESPONSE")
            if filtered:
                identities = item.get("external_ids")
                kind, season = item.get("media_type"), item.get("season_hint")
                if (kind not in spec["media_types"] or not isinstance(identities, dict) or len(identities) != 1
                        or any(provider not in spec["providers"] or not isinstance(identifier, str)
                               or not re.fullmatch(r"[1-9][0-9]{0,19}", identifier)
                               for provider, identifier in identities.items())
                        or (season is not None and (kind != "tv" or type(season) is not int or not 0 <= season <= 999))):
                    raise SourceReadError("INVALID_RESPONSE")
            elif (item.get("media_type") != spec["media_type"] or item.get("season_hint") is not None
                  or item.get("external_ids") != {spec["provider"]: item["source_item_id"]}):
                raise SourceReadError("INVALID_RESPONSE")
            observed_ids.add(item["source_item_id"])
        with self._lock:
            if not self._current(capture, source_id):
                return False
            now = self._clock()
            # Another source's in-flight capture may predate a sharing revocation.
            # Always merge with the current, already-isolated pool under the lock.
            base_pool = self._state["pool"]
            if filtered:
                targets = {item["source_item_id"]: f"{provider}:{item['media_type']}:{identifier}"
                           for item in items for provider, identifier in item["external_ids"].items()}
                base_pool = copy.deepcopy(base_pool)
                for key, row in list(base_pool.items()):
                    row["sources"] = [source for source in row["sources"] if not
                                      (source["source_id"] == source_id and source["source_item_id"] in targets
                                       and targets[source["source_item_id"]] != key)]
                    if not row["sources"]:
                        del base_pool[key]
            pool, counts = merge_pool(base_pool, batch["items"],
                                     as_of=datetime.fromtimestamp(now, timezone.utc), max_candidates=500, ttl_days=30)
            if counts.get("items_rejected"):
                raise SourceReadError("INVALID_RESPONSE")
            replacement = copy.deepcopy(self._state)
            replacement["pool"] = pool
            pages = old["cycle_pages"] + 1
            finished = next_cursor is None or pages >= capture[2]["pages"]
            replacement["sources"][source_id].update(
                status="ready" if finished else "queued", error_code=None, retry_until=0,
                last_success=now, last_count=len(batch["items"]), counts=counts,
                cursor=1 if finished else next_cursor, cycle_pages=0 if finished else pages,
                last_page=batch["page"], last_window_complete=finished,
                window_limited=batch["window_limited"] if snapshot else bool(finished and next_cursor is not None),
                reported_total=(batch.get("reported_total") if type(batch.get("reported_total")) is int
                                and 0 <= batch["reported_total"] <= 1_000_000_000 else None),
                observed_window=observed_window,
                next_due=now + (capture[2]["hours"] * 3600 if finished else 60),
                authentication_blocked=False,
            )
            return self._commit(capture, replacement)

    def status(self):
        self._synchronize_context()
        profile = self._profile()
        # Formatting is bounded and local. Keep one coherent state while ranking
        # so a concurrent revocation cannot leak a pre-isolation share result.
        with self._lock:
            return self._status(profile)

    def _status(self, profile):
        config, data = copy.deepcopy(self._config), copy.deepcopy(self._state)
        paused = self._cancel.is_set()
        running = self._run_lock.locked()
        usable = bool(config["enabled"] and profile.get("binding") == config["profile_binding"]
                      and profile.get("enabled") and profile.get("baseline") and not profile.get("stale"))
        ranked = rank_candidates(data.get("pool", {}), profile.get("baseline") if usable else None,
                                 as_of=datetime.fromtimestamp(self._clock(), timezone.utc))
        sources = []
        for source_id in config["sources"]:
            source = copy.deepcopy(data.get("sources", {}).get(source_id, {}))
            if source_id == FOLLOW_SOURCE and self._isolation_dirty:
                source.update(status="unavailable", error_code="STORAGE_FAILED", isolation_pending=True)
            source.update(id=source_id, label=SOURCE_SPECS[source_id]["label"],
                          cooldown_remaining=max(0, int(source.get("retry_until", 0) - self._clock() + .999)))
            source["stale"] = bool(source.get("last_success") and (
                source.get("status") == "error" or self._clock() - source["last_success"] > config["hours"] * 7200))
            sources.append(source)
        state = ("disabled" if not config["enabled"] else "paused" if paused else "running" if running
                 else "error" if any(s.get("status") == "error" for s in sources)
                  else "queued" if any(s.get("status") == "queued" for s in sources)
                  else "unavailable" if sources and all(s.get("status") == "unavailable" for s in sources) else "idle")
        result = {"enabled": config["enabled"], "mode": "shadow", "status": state, "can_execute": False,
                  "message": "候选只供排序和解释；TMDB 与关注分享每轮最多 %s 页，关注分享仅筛选已关注发布者；待播源读取单份 RSS 窗口，不创建订阅" % config["pages"],
                  "sources": sources, "candidate_count": len(ranked), "candidates": ranked,
                  "media_counts": {kind: sum(row.get("media_type") == kind for row in ranked)
                                  for kind in ("movie", "tv")},
                  "profile_available": usable,
                  "profile_reason": "使用已授权用户的最近有效画像" if usable else "尚无当前用户的有效画像，当前为通用榜单排序",
                  "profile_as_of": profile.get("as_of") if usable else None}
        return result
