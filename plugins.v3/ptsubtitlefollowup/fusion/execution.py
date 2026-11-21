"""Opt-in single-writer coordinator. The injected store owns the cross-instance lock.

Every reservation and attempt is persisted before dispatch. Once SUBMITTING is
durable, an ambiguous result can only be reconciled by an exact read.
"""
from __future__ import annotations

import copy
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
import re
import threading
import time


DATA_KEY = "fusion_execution_journal_v1"
SOURCE = "tmdb_popular_movie"
POLICY_VERSION = "tmdb-movie-policy-v1"
RANKED_POLICY_VERSION = "tmdb-ranked-indexed-title-year-v1"
PRESETS = {"conservative": (.78, .12), "balanced": (.68, .10)}
RANKED = "ranked"
PERSONAL = "personal"
PERSONAL_SOURCES = {"movie": "tmdb_personal_movie", "tv": "tmdb_personal_tv"}
PERSONAL_POLICY_VERSION = "tmdb-personal-movie-aired-season-v1"
FEATURES = {"genres": "题材", "languages": "语言", "decades": "年代", "actors": "演员",
            "directors": "导演", "keywords": "关键词"}
TZ = timezone(timedelta(hours=8))
MAX_INTENTS = 2000
MAX_CHECKS = 4
FRESH_SECONDS = 24 * 3600
SPACE_FLOOR = 100 * 1024**3
UNCERTAIN = {"SUBMITTING", "UNKNOWN"}
STATES = UNCERTAIN | {"QUEUED", "CONFIRMED", "EXISTING", "CANCELLED"}


def _digest(value):
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _equal(left, right):
    return isinstance(left, str) and isinstance(right, str) and bool(re.fullmatch("[0-9a-f]{64}", left)) and hmac.compare_digest(left, right)


def _text(value, limit=160):
    return value if isinstance(value, str) and 0 < len(value) <= limit and value == value.strip() and value.isprintable() else None


def _positive(value):
    return type(value) is int and 0 < value < 2**63


def _finite(value):
    return type(value) in (int, float) and -10**12 <= value <= 10**12 and math.isfinite(value)


def _day(now):
    return datetime.fromtimestamp(now, TZ).date().isoformat()


def _fresh(value, now):
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.utcoffset() is not None and 0 <= now - stamp.timestamp() <= FRESH_SECONDS
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        return False


def _settings(raw):
    raw = raw if isinstance(raw, dict) else {}
    preset = raw.get("fusion_execution_preset")
    personal = preset == PERSONAL
    limit = raw.get("fusion_execution_daily_limit", 3 if personal else 1)
    if isinstance(limit, str) and re.fullmatch("[1-5]", limit):
        limit = int(limit)
    max_limit = 3 if preset in (RANKED, PERSONAL) else 5
    user = raw.get("fusion_user_id")
    user = user.replace("-", "").lower() if isinstance(user, str) else ""
    libraries = raw.get("fusion_library_ids", [])
    if isinstance(libraries, str):
        libraries = re.split(r"[\s,]+", libraries.strip()) if libraries.strip() else []
    valid_libraries = (isinstance(libraries, list) and len(libraries) <= 100
                       and all(isinstance(i, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", i) for i in libraries))
    mode = raw.get("fusion_library_mode")
    discovery = raw.get("fusion_discovery_sources", [SOURCE])
    expected_sources = list(PERSONAL_SOURCES.values()) if personal else [SOURCE]
    requested = raw.get("fusion_execution_sources")
    sources_ok = isinstance(requested, list) and len(requested) == len(expected_sources) and all(s in requested for s in expected_sources)
    config = {"enabled": raw.get("fusion_execution_enabled") is True, "owner": _text(raw.get("fusion_execution_owner"), 128),
            "sources": expected_sources if sources_ok else [],
            "daily_limit": limit if type(limit) is int and 1 <= limit <= max_limit else 0,
            "preset": preset if isinstance(preset, str) and (preset in (RANKED, PERSONAL) or preset in PRESETS) else None,
            "isolated": raw.get("fusion_execution_legacy_isolated") is True,
            "storage_verified": raw.get("fusion_execution_storage_verified") is True,
            "discovery": raw.get("fusion_discovery_enabled") is True and isinstance(discovery, list) and all(s in discovery for s in expected_sources),
            "profile": raw.get("fusion_enabled") is True, "server_ref": _text(raw.get("fusion_server_ref"), 200),
            "user_id": user if re.fullmatch("[0-9a-f]{32}", user) else None,
            "library_mode": mode if mode in ("all_visible", "selected_ids") else None,
            "library_ids": sorted(set(libraries)) if mode == "selected_ids" and valid_libraries else []}
    if personal:
        limit = raw.get("fusion_execution_tv_daily_limit", 1)
        if isinstance(limit, str) and re.fullmatch("[1-3]", limit):
            limit = int(limit)
        config["tv_daily_limit"] = limit if type(limit) is int and 1 <= limit <= 3 else 0
    return config


def _settings_reason(config):
    if not config["enabled"]:
        return "DISABLED"
    if not config["owner"]:
        return "OWNER_REQUIRED"
    if not config["sources"] or not config["discovery"]:
        return "SOURCE_REQUIRED"
    if not config["isolated"]:
        return "LEGACY_NOT_ISOLATED"
    if not config["storage_verified"]:
        return "SPACE_UNVERIFIED"
    if (not config["profile"] or not config["server_ref"] or not config["user_id"] or not config["library_mode"]
            or (config["library_mode"] == "selected_ids" and not config["library_ids"])):
        return "PROFILE_REQUIRED"
    if not config["daily_limit"] or not config["preset"] or config.get("tv_daily_limit") == 0:
        return "INVALID_POLICY"
    return None


def _business(scope, identity):
    return _digest([scope["host_key"], scope["owner_key"], identity["provider"], identity["media_type"],
                    identity["media_id"], identity["season"], identity["episode_group"], 1])


def _compatible(adapter, owner):
    details = adapter.compatibility(owner)
    if not isinstance(details, dict) or details.get("ready") is not True:
        return None
    keys = ("version", "host_key", "owner_key")
    if not all(_text(details.get(key), 256) for key in keys):
        return None
    return {key: details[key] for key in keys}


def _season(value):
    return type(value) is int and 1 <= value <= 99


def _candidate_key(identity):
    return f"{identity['media_type']}:{identity['media_id']}:{identity['season']}"


def _eligible(snapshot, config, now, report=None):
    """Only the authorized source's own evidence may support dispatch."""
    if report is not None:
        report["input"] = "profile"
    if not isinstance(snapshot, dict) or snapshot.get("enabled") is not True or snapshot.get("profile_available") is not True:
        return []
    source_states = snapshot.get("sources")
    personal = config["preset"] == PERSONAL
    allowed = PERSONAL_SOURCES if personal else {"movie": SOURCE}
    available = {source["id"] for source in source_states if isinstance(source, dict)
        and source.get("id") in allowed.values() and source.get("status") in ("ready", "queued")
        and source.get("stale") is False and not source.get("error_code") and not source.get("authentication_blocked")
    } if isinstance(source_states, list) else set()
    if not available or not isinstance(snapshot.get("candidates"), list):
        if report is not None:
            report["input"] = "source"
        return []
    ranked = config["preset"] in (RANKED, PERSONAL)
    taste_min, confidence_min = (.5, 0) if ranked else PRESETS[config["preset"]]
    choices = []
    decisions = {}
    for row in snapshot["candidates"][:500]:
        if not isinstance(row, dict) or row.get("provider") != "themoviedb" or row.get("media_type") not in ("movie", "tv"):
            continue
        kind, media_id = row["media_type"], row.get("media_id")
        source_id = allowed.get(kind)
        if source_id not in available or not isinstance(media_id, str) or not re.fullmatch(r"[1-9][0-9]{0,19}", media_id):
            continue
        key = f"themoviedb:{kind}:{media_id}"
        sources = row.get("sources")
        if not isinstance(sources, list):
            continue
        origins = [s for s in sources[:16] if isinstance(s, dict) and s.get("source_id") == source_id
                   and s.get("source_item_id") == media_id]
        if not origins:
            continue
        decisions.setdefault(key, "metadata")
        if (len(origins) != 1 or origins[0].get("intent_kind") != "discovery" or row.get("entity_key") != key
                or not personal and (row.get("intent_kind") != "discovery" or row.get("metadata_conflict"))):
            continue
        source = origins[0]
        metadata, title = source.get("metadata"), _text(source.get("title"), 240)
        if not title or not _fresh(source.get("observed_at"), now) or not isinstance(metadata, dict):
            continue
        if personal:
            match = row.get("personal_match")
            if not isinstance(match, dict) or match.get("source_id") != source_id or match.get("source_item_id") != media_id:
                decisions[key] = "evidence"
                continue
            dimensions = match.get("dimensions")
        else:
            if row.get("metadata_source") != {"source_id": source_id, "source_item_id": media_id}:
                continue
            match = row
            contributions = row.get("contributions")
            dimensions = contributions.get("dimensions") if isinstance(contributions, dict) else None
        season = source.get("season_hint")
        if kind == "tv":
            hints = row.get("season_hints")
            if not _season(season) or not isinstance(hints, list) or len(hints) != 1 or not _season(hints[0]) or hints[0] != season:
                decisions[key] = "season"
                continue
            # An unrelated chart may omit the season while the authorized discovery
            # resolved it from TMDB. Conflicting explicit seasons were excluded above.
            if row.get("state") not in ("SHADOW", "HOLD") or row.get("state") == "HOLD" and not row.get("season_unknown"):
                continue
        elif row.get("state") != "SHADOW" or row.get("season_hints") or season is not None:
            continue
        taste, confidence = match.get("taste_match"), match.get("confidence")
        if (not _finite(taste) or not taste_min <= taste <= 1 or not _finite(confidence)
                or not confidence_min <= confidence <= .6):
            decisions[key] = "match"
            continue
        ratings = metadata.get("ratings")
        rating_floor = 0 if ranked else 8
        scores = [r for r in ratings[:8] if isinstance(r, dict) and r.get("provider") == "themoviedb"
                  and r.get("scale") == 10 and _finite(r.get("value")) and rating_floor <= r["value"] <= 10
                  and _positive(r.get("count")) and _fresh(r.get("observed_at"), now)] if isinstance(ratings, list) else []
        score = scores[0] if len(scores) == 1 else None
        if not ranked and score is None:
            decisions[key] = "rating"
            continue
        evidence = []
        for dim in dimensions[:6] if isinstance(dimensions, list) else []:
            if not isinstance(dim, dict) or not _finite(dim.get("support")) or dim["support"] <= 0:
                continue
            name = dim.get("dimension")
            if name not in ("genres", "languages", "decades", "actors", "directors", "keywords"):
                continue
            matched = dim.get("matched_labels")
            supported = metadata.get(name)
            if isinstance(matched, list) and isinstance(supported, list):
                evidence.extend([[name, label] for label in matched[:5] if _text(label, 128) and label in supported])
        if not evidence:
            decisions[key] = "evidence"
            continue
        year = source.get("year")
        identity = {"provider": "themoviedb", "media_id": media_id, "media_type": kind, "season": season,
                    "episode_group": None, "title": title, "year": year if type(year) is int and 1800 <= year <= 2200 else None}
        audit = {"source": source_id, "observed_at": source["observed_at"], "rating": score["value"] if score else None,
                 "rating_count": score["count"] if score else None, "taste": taste, "confidence": confidence,
                 "matched": evidence[:12], "algorithm": _text(row.get("algorithm_version"), 80),
                 "profile_as_of": _text(row.get("profile_as_of"), 64)}
        choices.append((identity, audit))
        decisions[key] = "eligible"
    if report is not None:
        report.update(input="ready", candidates=len(decisions))
        for gate in ("metadata", "match", "rating", "evidence", "eligible", "season"):
            report[gate] = sum(result == gate for result in decisions.values())
    if ranked:
        # The shelf snapshot orders ties by its combined score and stable entity key.
        return sorted(choices, key=lambda item: -item[1]["taste"])
    return sorted(choices, key=lambda item: (-item[1]["taste"], -item[1]["confidence"], item[0]["media_id"]))


def _empty():
    return {"schema": 1, "grant": None, "intents": {}, "scan_after": None, "last_reason": "NOT_AUTHORIZED"}


def _permitted_identity(identity, scope):
    if identity.get("media_type") == "movie":
        return identity.get("season") is None
    return (identity.get("media_type") == "tv" and _season(identity.get("season"))
            and scope.get("policy_version") == PERSONAL_POLICY_VERSION and scope.get("preset") == PERSONAL
            and scope.get("sources") == list(PERSONAL_SOURCES.values()))


def _checked(record):
    if record is None:
        return _empty()
    if (not isinstance(record, dict) or record.get("schema") != 1 or not isinstance(record.get("intents"), dict)
            or len(record["intents"]) > MAX_INTENTS):
        raise ValueError("INVALID_JOURNAL")
    grant = record.get("grant")
    if grant is not None and (not isinstance(grant, dict) or not isinstance(grant.get("scope"), dict)
                              or not _equal(grant.get("digest"), _digest(grant["scope"])) or not _finite(grant.get("at"))):
        raise ValueError("INVALID_GRANT")
    for key, intent in record["intents"].items():
        if (not isinstance(intent, dict) or intent.get("state") not in STATES or type(intent.get("reserved")) is not bool
                or not isinstance(intent.get("identity"), dict) or not isinstance(intent.get("scope"), dict)
                or key != _business(intent["scope"], intent["identity"]) or not _finite(intent.get("updated_at"))
                or not _finite(intent.get("created_at")) or intent.get("quota_day") != _day(intent["created_at"])
                or not _text(intent["scope"].get("owner"), 128) or not _text(intent["scope"].get("host_key"), 256)
                or not _text(intent["scope"].get("owner_key"), 256)
                or intent["reserved"] != (intent["state"] in UNCERTAIN | {"QUEUED", "CONFIRMED"})
                or intent["state"] in {"EXISTING", "CONFIRMED"} and not _positive(intent.get("sid"))
                or not isinstance(intent.get("events"), list) or not 1 <= len(intent["events"]) <= 10
                or not _permitted_identity(intent["identity"], intent["scope"]) or intent["identity"].get("provider") != "themoviedb"
                or not isinstance(intent["identity"].get("media_id"), str)
                or not re.fullmatch(r"[1-9][0-9]{0,19}", intent["identity"]["media_id"])
                or intent["identity"].get("episode_group") is not None
                or not _text(intent["identity"].get("title"), 240)):
            raise ValueError("INVALID_INTENT")
    return copy.deepcopy(record)


class ExecutionService:
    def __init__(self, store, candidates, client_factory, *, current_config=None, storage_check=None,
                 tv_storage_check=None, clock=time.time):
        self._store, self._candidates, self._client_factory = store, candidates, client_factory
        self._current_config, self._clock = current_config, clock
        self._storage_check = storage_check
        self._tv_storage_check = tv_storage_check
        self._local = threading.RLock()
        self._config, self._epoch, self._stopped = None, 0, False
        self._revoke_pending, self._last_fault = False, None
        self._cached_counts = {}
        self._last_scan = None

    def _settings_now(self):
        with self._local:
            config, epoch, stopped = self._config, self._epoch, self._stopped
        if config is None:
            return None, epoch, "NOT_CONFIGURED"
        if stopped:
            return config, epoch, "PAUSED"
        if self._current_config is not None:
            try:
                if _settings(self._current_config()) != config:
                    return config, epoch, "CONFIG_CHANGED"
            except Exception:
                return config, epoch, "CONFIG_UNAVAILABLE"
        return config, epoch, _settings_reason(config)

    def _scope(self, config, adapter):
        runtime = _compatible(adapter, config["owner"])
        fence = self._store.read_fence(_digest(config))
        if runtime is None or not _text(fence, 256):
            return None, "INCOMPATIBLE"
        fingerprint, reason = self._storage_scope(config)
        scope = {**runtime, "owner": config["owner"],
                "policy_version": RANKED_POLICY_VERSION if config["preset"] == RANKED else POLICY_VERSION,
                "scope_fence": fence,
                "binding": _digest([config["server_ref"], config["user_id"], config["library_mode"], config["library_ids"]]),
                "sources": config["sources"], "preset": config["preset"], "daily_limit": config["daily_limit"],
                "timezone": "Asia/Shanghai", "legacy_isolated": config["isolated"],
                "storage_verified": config["storage_verified"], "storage_fingerprint": fingerprint,
                "minimum_free_bytes": SPACE_FLOOR}
        if config["preset"] == PERSONAL:
            scope.update(policy_version=PERSONAL_POLICY_VERSION, tv_daily_limit=config["tv_daily_limit"])
        return scope, reason

    def _storage_scope(self, config):
        movie, reason = self._checked_space(self._storage_check)
        if config["preset"] != PERSONAL:
            return movie, reason
        tv, tv_reason = self._checked_space(self._tv_storage_check)
        if not movie or not tv:
            return None, "SPACE_UNVERIFIED"
        return _digest({"movie": movie, "tv": tv}), reason or tv_reason

    @staticmethod
    def _checked_space(check):
        fingerprint, reason = None, "SPACE_UNVERIFIED"
        try:
            space = check() if check else None
            if (isinstance(space, dict) and space.get("status") in ("ready", "low")
                    and isinstance(space.get("fingerprint"), str) and re.fullmatch("[a-f0-9]{64}", space["fingerprint"])
                    and type(space.get("available_bytes")) is int and space["available_bytes"] >= 0
                    and type(space.get("minimum_free_bytes")) is int and space["minimum_free_bytes"] == SPACE_FLOOR
                    and type(space.get("locations_checked")) is int and 1 <= space["locations_checked"] <= 32):
                fingerprint = space["fingerprint"]
                reason = None if space["status"] == "ready" and space["available_bytes"] >= SPACE_FLOOR else "SPACE_LOW"
        except Exception:
            pass
        return fingerprint, reason

    def preflight(self, draft):
        """Check an unsaved setup without configuring, arming, or opening the ledger."""
        config = _settings(draft)
        issue = _settings_reason(config)
        if issue:
            return {"success": False, "reason": issue}
        try:
            if not _compatible(self._client_factory(), config["owner"]):
                return {"success": False, "reason": "INCOMPATIBLE"}
        except Exception:
            return {"success": False, "reason": "INCOMPATIBLE"}
        _, issue = self._storage_scope(config)
        return {"success": False, "reason": issue} if issue else {"success": True}

    @staticmethod
    def _matches(grant, scope):
        if not scope or not grant:
            return False
        if scope["storage_fingerprint"] is None:
            # Unknown backing blocks dispatch but cannot prove the mount changed.
            scope = {**scope, "storage_fingerprint": grant["scope"].get("storage_fingerprint")}
        return _equal(grant["digest"], _digest(scope))

    def _save(self, journal):
        self._store.write(DATA_KEY, journal)
        with self._local:
            self._last_fault = None

    def _fault(self, code):
        with self._local:
            self._last_fault = code
        return {"success": False, "reason": code, "message": {
            "BUSY": "执行协调器正在处理请求，请稍后重试。",
            "STORAGE_FAILED": "执行记录暂不可用。",
            "SCOPE_CHANGED": "授权范围已变化，请刷新页面后确认。",
            "INCOMPATIBLE": "当前宿主或订阅账号未通过兼容检查。",
        }.get(code, "当前条件不满足，请检查执行设置。")}

    def configure(self, raw):
        config = _settings(raw)
        with self._local:
            changed = self._config is not None and self._config != config
            if changed:
                self._cached_counts = {}
            self._config, self._stopped = config, False
            self._epoch += 1
            self._revoke_pending |= changed
        # A fresh instance must observe mismatching saved settings immediately.
        # This prevents restoring an old grant after a cold A -> B -> A change.
        try:
            if not config["enabled"] and hasattr(self._store, "exists") and not self._store.exists():
                with self._local:
                    self._revoke_pending = False
                return
            # This short transaction can invalidate the old writer's fence even
            # while that writer holds the separate OS lock in the native chain.
            self._store.scope_fence(_digest(config))
            with self._store.locked():
                journal = _checked(self._store.read(DATA_KEY))
                self._revoke_if_pending(journal)
                if journal["grant"]:
                    live, _, reason = self._settings_now()
                    scope = None if reason else self._scope(live, self._client_factory())[0]
                    if not self._matches(journal["grant"], scope):
                        self._revoke_locked(journal)
        except BlockingIOError:
            pass  # The active worker checks live configuration before host dispatch.
        except Exception:
            self._fault("STORAGE_FAILED")

    def stop(self):
        with self._local:
            self._stopped = True
            self._epoch += 1
        return {"success": True, "reason": "PAUSED", "message": "执行调度已暂停。"}

    def _revoke_locked(self, journal):
        changed = journal.get("grant") is not None
        journal["grant"] = None
        for intent in journal["intents"].values():
            if intent["state"] == "QUEUED":
                self._transition(intent, "CANCELLED", reserved=False)
                changed = True
        if changed:
            journal["last_reason"] = "REVOKED"
            self._save(journal)
        with self._local:
            self._revoke_pending = False

    def revoke(self):
        with self._local:
            self._revoke_pending = True
            self._epoch += 1
            config = self._config or _settings({})
        try:
            self._store.scope_fence(_digest(config), force=True)
            with self._store.locked():
                self._revoke_locked(_checked(self._store.read(DATA_KEY)))
            return {"success": True, "reason": "REVOKED", "message": "已撤销后续派发授权。"}
        except BlockingIOError:
            return self._fault("BUSY")
        except Exception:
            return self._fault("STORAGE_FAILED")

    def authorize(self, expected_digest):
        if not _equal(expected_digest, expected_digest):
            return self._fault("SCOPE_CHANGED")
        try:
            with self._store.locked():
                journal = _checked(self._store.read(DATA_KEY))
                self._revoke_if_pending(journal)
                config, epoch, reason = self._settings_now()
                if reason:
                    return self._fault(reason)
                scope, reason = self._scope(config, self._client_factory())
                if reason:
                    return self._fault(reason)
                if not _equal(expected_digest, _digest(scope)):
                    return self._fault("SCOPE_CHANGED")
                if not self._same_epoch(epoch):
                    return self._fault("SCOPE_CHANGED")
                journal["grant"] = {"digest": expected_digest, "scope": scope, "at": self._clock()}
                journal.pop("last_scan", None)
                journal.pop("personal_progress", None)
                self._last_scan = None
                journal["last_reason"], journal["scan_after"] = "AUTHORIZED", None
                self._save(journal)
                self._revoke_if_pending(journal)
                if not journal["grant"]:
                    return self._fault("SCOPE_CHANGED")
                return {"success": True, "reason": "AUTHORIZED", "message": "已按当前范围授权自动订阅。"}
        except BlockingIOError:
            return self._fault("BUSY")
        except Exception:
            return self._fault("STORAGE_FAILED")

    def _same_epoch(self, epoch):
        with self._local:
            return epoch == self._epoch and not self._revoke_pending and not self._stopped

    def _revoke_if_pending(self, journal):
        with self._local:
            pending = self._revoke_pending
        if pending:
            self._revoke_locked(journal)

    def _transition(self, intent, state, *, reserved=None, sid=None):
        now = self._clock()
        intent.update(state=state, updated_at=now)
        if reserved is not None:
            intent["reserved"] = reserved
        if sid is not None:
            intent["sid"] = sid
        intent["events"].append({"state": state, "at": now})

    def _counts(self, journal, scope):
        day, used, unknown = _day(self._clock()), {"movie": 0, "tv": 0}, 0
        for intent in journal["intents"].values():
            unknown += intent["state"] in UNCERTAIN
            if (scope and intent["reserved"] and intent["quota_day"] == day
                    and all(intent["scope"][k] == scope[k] for k in ("host_key", "owner_key"))):
                used[intent["identity"]["media_type"]] += 1
        return used, unknown

    def _remaining(self, journal, scope):
        used, _ = self._counts(journal, scope)
        remaining = {"movie": max(0, scope["daily_limit"] - used["movie"])}
        if scope["preset"] == PERSONAL:
            remaining["tv"] = max(0, scope["tv_daily_limit"] - used["tv"])
        return remaining

    def _reconcile(self, journal, intent, adapter):
        scope = intent["scope"]
        try:
            current = _compatible(adapter, scope["owner"])
            same_host = current is not None and all(scope[key] == value for key, value in current.items())
            policy = {"indexed_only": True} if scope["preset"] in (RANKED, PERSONAL) else {}
            check = adapter.lookup_subscription(copy.deepcopy(intent["identity"]), scope["owner"], **policy) if same_host else {}
        except Exception:
            check = {}
        if (isinstance(check, dict) and check.get("status") == "existing" and _positive(check.get("sid"))
                and (intent.get("sid") is None or check["sid"] == intent["sid"])):
            self._transition(intent, "CONFIRMED", sid=check["sid"])
            journal["last_reason"] = "RECONCILED"
        elif intent["state"] == "SUBMITTING":
            self._transition(intent, "UNKNOWN")
            journal["last_reason"] = "UNKNOWN"
        else:
            return
        self._save(journal)

    def tick(self):
        try:
            if hasattr(self._store, "exists") and not self._store.exists():
                return
            with self._store.locked():
                journal = _checked(self._store.read(DATA_KEY))
                try:
                    self._tick_locked(journal)
                finally:
                    self._revoke_if_pending(journal)
        except BlockingIOError:
            self._fault("BUSY")
        except Exception:
            self._fault("STORAGE_FAILED")

    def _tick_locked(self, journal):
        self._revoke_if_pending(journal)
        for intent in journal["intents"].values():
            if intent["state"] in UNCERTAIN:
                self._reconcile(journal, intent, self._client_factory())
                return
            if intent["state"] == "QUEUED":
                # The dispatch marker was never committed. Release the reservation.
                self._transition(intent, "CANCELLED", reserved=False)
                self._save(journal)
        if journal["grant"] is None:
            return
        config, epoch, reason = self._settings_now()
        if reason == "PAUSED":
            return
        if reason:
            self._revoke_locked(journal)
            return
        adapter = self._client_factory()
        scope, reason = self._scope(config, adapter)
        if not self._matches(journal["grant"], scope):
            self._revoke_locked(journal)
            return
        if reason:
            return self._record_reason(journal, reason)
        if not any(self._remaining(journal, scope).values()):
            return self._record_reason(journal, "QUOTA_REACHED")
        report = {"scope": journal["grant"]["digest"], "at": self._clock()}
        checked = {}
        self._scan(journal, config, epoch, scope, adapter, report, checked)
        if not self._matches(journal.get("grant"), scope):
            return
        if report["input"] == "ready":
            report["checked"] = len(checked)
            for result in ("new", "subscribed", "owned", "conflict", "library_unknown", "subscription_unknown", "unverified", "uncertain"):
                report[result] = sum(value == result for value in checked.values())
            report["unseen"] = report["eligible"] - report["previous"] - report["checked"]
        remaining = self._remaining(journal, scope)
        report["remaining"] = sum(remaining.values())
        if config["preset"] == PERSONAL:
            report.update(remaining_movies=remaining["movie"], remaining_tv=remaining["tv"])
        # The last completed scan is enough. Avoid rewriting the whole dispatch ledger every
        # five minutes for an identical empty result; a restart displays the last persisted time.
        previous = journal.get("last_scan")
        self._last_scan = report
        if not isinstance(previous, dict) or {**previous, "at": report["at"]} != report:
            journal["last_scan"] = report
            self._save(journal)

    def _scan(self, journal, config, epoch, scope, adapter, report, checked):
        try:
            snapshot = self._candidates()
        except Exception:
            report["input"] = "source"
            return self._record_reason(journal, "SOURCE_UNAVAILABLE")
        choices = _eligible(snapshot, config, self._clock(), report)
        personal = config["preset"] == PERSONAL
        ranked = config["preset"] in (RANKED, PERSONAL)
        policy = {"indexed_only": True} if ranked else {}
        pending = []
        previous = 0
        for kind in (("movie", "tv") if personal else ("movie",)):
            group = [item for item in choices if item[0]["media_type"] == kind]
            ids = [_candidate_key(item[0]) if personal else item[0]["media_id"] for item in group]
            progress = journal.setdefault("personal_progress", {}).setdefault(kind, {}) if personal else journal
            if ranked:
                window = _digest([_day(self._clock()), ids])
                if progress.get("scan_window") != window:
                    progress.update(scan_window=window, scan_after=None)
            cursor = progress.get("scan_after")
            start = ids.index(cursor) + 1 if cursor in ids else 0
            group = group[start:] + group[:start]
            remaining = [item for item in group if journal["intents"].get(_business(scope, item[0]), {}).get("state")
                         in (None, "CANCELLED")]
            previous += len({_candidate_key(item[0]) for item in group} - {_candidate_key(item[0]) for item in remaining})
            pending.extend(remaining[:MAX_CHECKS])
        if report["input"] == "ready":
            report["previous"] = previous
        if not pending:
            return self._record_reason(journal, "NO_ELIGIBLE")
        held_reason = "HELD"
        for identity, audit in pending:
            kind = identity["media_type"]
            if not self._remaining(journal, scope)[kind]:
                continue
            media_id = _candidate_key(identity) if personal else identity["media_id"]
            progress = journal["personal_progress"][kind] if personal else journal
            progress["scan_after"] = media_id
            if media_id in checked or journal["intents"].get(_business(scope, identity), {}).get("state") not in (None, "CANCELLED"):
                continue
            checked[media_id] = "unverified"
            try:
                check = adapter.lookup_subscription(copy.deepcopy(identity), scope["owner"], **policy)
            except Exception:
                held_reason = "SUBSCRIPTION_UNVERIFIED"
                checked[media_id] = "subscription_unknown"
                continue
            if isinstance(check, dict) and check.get("status") == "existing" and _positive(check.get("sid")):
                checked[media_id] = "subscribed"
                key = _business(scope, identity)
                if key not in journal["intents"] and len(journal["intents"]) >= MAX_INTENTS:
                    return self._record_reason(journal, "JOURNAL_FULL")
                journal["intents"][key] = self._intent(scope, identity, audit, journal["grant"], "EXISTING", check["sid"])
                self._save(journal)
                held_reason = "PREEXISTING"
                continue
            if not isinstance(check, dict) or check.get("status") != "absent":
                held_reason = "SUBSCRIPTION_CONFLICT" if isinstance(check, dict) and check.get("status") == "conflict" else "SUBSCRIPTION_UNVERIFIED"
                checked[media_id] = "conflict" if held_reason == "SUBSCRIPTION_CONFLICT" else "subscription_unknown"
                continue
            try:
                library = adapter.lookup_library(copy.deepcopy(identity), **policy)
                if library != "absent" and not (kind == "movie" and ranked and library == "no_known_match"):
                    held_reason = "LIBRARY_PRESENT" if library == "present" else "LIBRARY_UNVERIFIED"
                    checked[media_id] = "owned" if library == "present" else "library_unknown"
                    continue
            except Exception:
                held_reason = "LIBRARY_UNVERIFIED"
                checked[media_id] = "library_unknown"
                continue
            if self._dispatch(journal, config, epoch, scope, adapter, identity, audit):
                state = journal["intents"].get(_business(scope, identity), {}).get("state")
                checked[media_id] = {"CONFIRMED": "new", "EXISTING": "subscribed", "UNKNOWN":"uncertain",
                                     "SUBMITTING":"uncertain"}.get(state, "unverified")
                if personal and state == "CANCELLED" and self._same_epoch(epoch) and journal.get("grant"):
                    continue
                if not ranked or state not in ("CONFIRMED", "EXISTING"):
                    return
                if not any(self._remaining(journal, scope).values()):
                    return self._record_reason(journal, "QUOTA_REACHED")
                held_reason = journal["last_reason"]
        journal["last_reason"] = held_reason
        self._save(journal)

    def _record_reason(self, journal, reason):
        if journal["last_reason"] != reason:
            journal["last_reason"] = reason
            self._save(journal)

    def _intent(self, scope, identity, audit, grant, state, sid=None):
        now = self._clock()
        return {"scope": copy.deepcopy(scope), "identity": identity, "audit": audit, "state": state,
                "grant_digest": grant["digest"], "authorized_at": grant["at"], "quota_day": _day(now),
                "reserved": state == "QUEUED", "created_at": now, "updated_at": now, "sid": sid,
                "events": [{"state": state, "at": now}]}

    def _dispatch(self, journal, config, epoch, scope, adapter, identity, audit):
        policy = {"indexed_only": True} if config["preset"] in (RANKED, PERSONAL) else {}
        if (identity, audit) not in _eligible(self._candidates(), config, self._clock()):
            return False
        current, _, reason = self._settings_now()
        live_scope, space_reason = (None, None) if reason or current != config else self._scope(config, adapter)
        if reason or current != config or not self._matches(journal["grant"], live_scope):
            if reason != "PAUSED":
                self._revoke_locked(journal)
            return True
        if space_reason:
            self._record_reason(journal, space_reason)
            return True
        if not self._same_epoch(epoch):
            return True
        key = _business(scope, identity)
        if journal["intents"].get(key, {}).get("state") not in (None, "CANCELLED"):
            return False
        if len(journal["intents"]) >= MAX_INTENTS and key not in journal["intents"]:
            self._record_reason(journal, "JOURNAL_FULL")
            return True
        if not self._remaining(journal, scope)[identity["media_type"]]:
            self._record_reason(journal, "QUOTA_REACHED")
            return True
        intent = self._intent(scope, identity, audit, journal["grant"], "QUEUED")
        journal["intents"][key] = intent
        self._save(journal)
        try:
            check = adapter.lookup_subscription(copy.deepcopy(identity), scope["owner"], **policy)
        except Exception:
            check = {}
        existing = isinstance(check, dict) and check.get("status") == "existing" and _positive(check.get("sid"))
        if not isinstance(check, dict) or check.get("status") != "absent" or not self._same_epoch(epoch):
            self._transition(intent, "EXISTING" if existing else "CANCELLED", reserved=False,
                             sid=check["sid"] if existing else None)
            self._save(journal)
            return True
        self._transition(intent, "SUBMITTING")
        self._save(journal)
        def may_dispatch():
            try:
                current, _, reason = self._settings_now()
                if (reason or current != config or not self._same_epoch(epoch) or self._scope(config, adapter) != (scope, None)
                        or _day(self._clock()) != intent["quota_day"]):
                    return False
                choices = _eligible(self._candidates(), config, self._clock())
                return (identity, audit) in choices and self._same_epoch(epoch)
            except Exception:
                return False

        if not may_dispatch():
            self._transition(intent, "CANCELLED", reserved=False)
            self._save(journal)
            return True
        # This is the only external dispatch site. Every exceptional path from
        # here is uncertain, even an apparently failed/empty host result.
        try:
            outcome = adapter.add(copy.deepcopy(identity), scope["owner"], may_dispatch=may_dispatch, **policy)
        except Exception:
            outcome = {}
        sid = outcome.get("sid") if isinstance(outcome, dict) else None
        if isinstance(outcome, dict) and outcome.get("kind") == "not_sent" and "sid" in outcome and sid is None:
            self._transition(intent, "CANCELLED", reserved=False)
            journal["last_reason"] = "HELD"
            self._save(journal)
            return True
        if isinstance(outcome, dict) and outcome.get("kind") == "existing" and _positive(sid):
            try:
                check = adapter.lookup_subscription(copy.deepcopy(identity), scope["owner"], **policy)
            except Exception:
                check = {}
            if isinstance(check, dict) and check.get("status") == "existing" and check.get("sid") == sid and _positive(check["sid"]):
                self._transition(intent, "EXISTING", reserved=False, sid=sid)
                self._save(journal)
                return True
        if _positive(sid):
            intent["sid"] = sid
        self._transition(intent, "UNKNOWN")
        journal["last_reason"] = "UNKNOWN"
        self._save(journal)
        self._reconcile(journal, intent, adapter)
        return True

    @property
    def needs_tick(self):
        with self._local:
            if self._config and self._config["enabled"] or self._revoke_pending:
                return True
        try:
            if hasattr(self._store, "exists") and not self._store.exists():
                return False
            with self._store.locked():
                journal = _checked(self._store.read(DATA_KEY))
                return bool(journal["grant"] or any(i["state"] in UNCERTAIN | {"QUEUED"} for i in journal["intents"].values()))
        except Exception:
            return True

    def _visible_scan(self, journal):
        grant = journal["grant"]
        scans = [s for s in (self._last_scan, journal.get("last_scan"))
                 if isinstance(s, dict) and _equal(s.get("scope"), grant["digest"]) and _finite(s.get("at"))
                 and s["at"] >= grant["at"] and s.get("input") in ("profile", "source", "ready")]
        if not scans:
            return None
        scan = max(scans, key=lambda s: s["at"])
        result = {"at": scan["at"], "input": scan["input"]}
        fields = ["remaining"]
        if grant["scope"].get("preset") == PERSONAL:
            fields += ["remaining_movies", "remaining_tv"]
            if scan["input"] == "ready":
                fields.append("season")
        if scan["input"] == "ready":
            fields += ["candidates", "metadata", "match", "rating", "evidence", "eligible",
                       "previous", "checked", "new", "subscribed", "owned", "conflict", "library_unknown",
                       "subscription_unknown", "unverified", "uncertain", "unseen"]
        for field in fields:
            count = scan.get(field)
            if type(count) is not int or not 0 <= count <= 500:
                return None
            result[field] = count
        return result

    def status(self):
        config, _, reason = self._settings_now()
        config = config or _settings({})
        result = {"enabled": config["enabled"], "armed": False, "state": "shadow", "owner": config["owner"],
                  "sources": config["sources"], "media_types": ["movie"], "daily_limit": config["daily_limit"],
                  "preset": config["preset"], "used_today": 0, "unknown_count": 0, "last_reason": reason,
                  "recent": [], "last_scan": None, "authorization_digest": None, "can_authorize": False}
        personal = config["preset"] == PERSONAL
        if personal:
            result.update(media_types=["movie", "tv"], tv_daily_limit=config["tv_daily_limit"],
                          used_movies_today=0, used_tv_today=0)
        try:
            missing = hasattr(self._store, "exists") and not self._store.exists()
            with nullcontext() if missing else self._store.locked():
                journal = _empty() if missing else _checked(self._store.read(DATA_KEY))
                self._revoke_if_pending(journal)
                scope, gate_reason = (None, None) if reason else self._scope(config, self._client_factory())
                if scope:
                    result["authorization_digest"] = None if gate_reason else _digest(scope)
                    result["armed"] = self._matches(journal["grant"], scope)
                    result["can_authorize"] = not result["armed"] and gate_reason is None
                    if result["armed"]:
                        result["last_scan"] = self._visible_scan(journal)
                if journal.get("grant") and not result["armed"] and reason != "PAUSED":
                    self._revoke_locked(journal)
                used, unknown = self._counts(journal, scope)
                last_reason = journal["last_reason"]
                if result["armed"] and not gate_reason and last_reason in ("SPACE_LOW", "SPACE_UNVERIFIED"):
                    last_reason = "AUTHORIZED"
                visible_used = sum(used.values()) if personal else used["movie"]
                if personal:
                    result.update(used_movies_today=used["movie"], used_tv_today=used["tv"])
                result.update(used_today=visible_used, unknown_count=unknown, state="unknown" if unknown else "blocked" if gate_reason else "armed" if result["armed"]
                              else "ready" if scope else "paused" if reason == "PAUSED" else "disabled" if not config["enabled"] else "blocked",
                              last_reason=reason or gate_reason or last_reason)
                visible = [i for i in journal["intents"].values() if scope and all(
                    i["scope"].get(k) == scope[k] for k in ("host_key", "owner_key", "binding"))
                    and i["identity"]["media_type"] in result["media_types"]]
                for intent in sorted(visible, key=lambda item: item["updated_at"], reverse=True)[:10]:
                    audit = intent.get("audit") or {}
                    score = audit.get("taste")
                    reason = None
                    for pair in (audit.get("matched") or [])[:12]:
                        if isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str) and pair[0] in FEATURES and _text(pair[1], 128):
                            reason = f"{FEATURES[pair[0]]}相合 · {pair[1]}"
                            break
                    result["recent"].append({**{k: intent[k] for k in ("state", "updated_at", "sid")},
                                             "match_score": round(score * 100, 1) if _finite(score) and 0 <= score <= 1 else None,
                                             "match_reason": reason,
                                             **{k: intent["identity"][k] for k in ("title", "media_type", "season")}})
                with self._local:
                    self._cached_counts = {k: result[k] for k in ("unknown_count", "used_today", "used_movies_today", "used_tv_today") if k in result}
                    if self._last_fault == "STORAGE_FAILED" and not gate_reason:
                        result["last_reason"] = self._last_fault
        except BlockingIOError:
            with self._local:
                result.update(self._cached_counts)
            result.update(state="busy", last_reason="BUSY")
        except Exception:
            result.update(state="blocked", last_reason="STORAGE_FAILED")
        return result
