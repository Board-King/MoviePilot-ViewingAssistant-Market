"""Bounded snapshot coordinator; storage and server credentials belong to the host.

Only a complete user-domain snapshot replaces an aggregate. No raw watch history,
credentials, media titles, paths, or subscription actions are persisted here.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import re
import threading
import time

from .emby import EmbyReadOnlyClient, EmbyReadError
from .profile import SnapshotAccumulator, ProfileError
from .tonight import select_tonight, unavailable_choices
from .choices import ChoiceStore, ChoiceError, scope_key
from .ui import FORM_DEFAULTS as DEFAULTS


DATA_KEY = "fusion_state_v1"


def normalize(config):
    """Flat V2 form keys, explicit library consent, no executable mode switch."""
    raw_ids = config.get("fusion_library_ids") or ""
    if isinstance(raw_ids, str):
        raw_ids = re.split(r"[\s,]+", raw_ids.strip())
    if not isinstance(raw_ids, list):
        raw_ids = []
    ids = sorted(set(str(i).strip() for i in raw_ids if str(i).strip()))
    try:
        hours = max(1, min(168, int(config.get("fusion_interval_hours") or 6)))
    except (ValueError, TypeError):
        hours = 6
    mode = config.get("fusion_library_mode", "unselected")
    if not isinstance(mode, str) or mode not in {"unselected", "all_visible", "selected_ids"}:
        mode = "unselected"
    return {
        "enabled": config.get("fusion_enabled") is True,
        "server_ref": str(config.get("fusion_server_ref") or "").strip()[:200],
        "user_id": str(config.get("fusion_user_id") or "").strip().lower().replace("-", ""),
        "scope": {"mode": mode, "library_ids": ids if mode == "selected_ids" else []},
        "hours": hours,
    }


def binding_key(config):
    value = {key: config[key] for key in ("server_ref", "user_id", "scope")}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def binding_ready(config):
    return bool(config["server_ref"] and re.fullmatch(r"[a-f0-9]{32}", config["user_id"]))


def configuration_ready(config):
    scope = config["scope"]
    return bool(binding_ready(config)
                and (scope["mode"] == "all_visible" or (
                    scope["mode"] == "selected_ids" and 0 < len(scope["library_ids"]) <= 100
                    and all(re.fullmatch(r"[A-Za-z0-9_-]{1,80}", i) for i in scope["library_ids"]))))


class SnapshotService:
    def __init__(self, read, write, resolve_server, *, client_factory=EmbyReadOnlyClient, clock=time.time):
        self._read, self._write, self._resolve = read, write, resolve_server
        self._client_factory, self._clock = client_factory, clock
        self._lock, self._run_lock = threading.RLock(), threading.Lock()
        self._epoch, self._cancel = 0, threading.Event()
        self._config = normalize({})
        self._state = {}
        self._choices = ChoiceStore(read, write, clock)

    def configure(self, raw):
        config = normalize(raw)
        key = binding_key(config)
        with self._lock:
            self._cancel.set()
            self._cancel = threading.Event()
            self._epoch += 1
            self._choices.reset()
            self._config = config
            previous = self._read(DATA_KEY) or {}
            if not isinstance(previous, dict):
                previous = {}
            self._state = (copy.deepcopy(previous) if previous.get("schema") == 1
                           and previous.get("binding") == key else {
                               "schema": 1, "binding": key, "profile": None,
                               "native_server_id": None, "last_success": None,
                               "next_due": 0, "retry_until": 0,
                           })
            # Interrupted work is never represented as a completed snapshot.
            if not config["enabled"]:
                self._cancel.set()
                self._state.update(status="disabled", message="偏好学习已关闭")
            elif not configuration_ready(config):
                self._state.update(status="unconfigured", message="请选择服务器、填写用户 ID 并明确媒体库范围")
            elif not self._state.get("native_server_id"):
                self._state.update(status="unverified", message="请先验证当前绑定，再开始采集")
            elif self._state.get("status") in {"running", "validating", "disabled", "unconfigured"}:
                self._state.update(status="ready", message="绑定已恢复，等待下次快照")
            self._persist()

    def _persist(self):
        self._write(DATA_KEY, copy.deepcopy(self._state))

    def stop(self):
        with self._lock:
            self._cancel.set()
            self._epoch += 1
            self._choices.reset()

    @property
    def enabled(self):
        with self._lock:
            return self._config["enabled"]

    def status(self):
        with self._lock:
            result = copy.deepcopy(self._state)
            result.pop("binding", None)
            result["binding_verified"] = bool(result.pop("native_server_id", None))
            result.update(enabled=self._config["enabled"], mode="shadow", can_execute=False,
                          observer_enabled=False, server_ref=self._config["server_ref"],
                          user_id=self._config["user_id"], scope=copy.deepcopy(self._config["scope"]),
                          interval_hours=self._config["hours"])
            result["cooldown_remaining"] = max(0, int(result.get("retry_until", 0) - self._clock() + 0.999))
            if self._config["enabled"] and self._cancel.is_set():
                result.update(status="paused", message="采集服务已停止，当前没有后台快照运行")
            result["stale"] = self._profile_stale()
            return result

    def _profile_stale(self):
        return bool(self._state.get("profile") and (
            self._state.get("last_attempt_failed") or not self._state.get("last_success")
            or self._clock() - self._state["last_success"] > self._config["hours"] * 3600 * 2))

    def evaluation_context(self):
        """Internal immutable input for ranking; never returns credentials/history."""
        with self._lock:
            current = self.status()
            baseline = (current.get("profile") or {}).get("baseline")
            return {"binding": self._state.get("binding"), "enabled": self._config["enabled"],
                    "baseline": baseline, "stale": current["stale"],
                    "as_of": ((baseline or {}).get("method") or {}).get("as_of")}

    def binding_users(self, server_ref: str):
        """Return choices only for a server already configured in MoviePilot."""
        if not isinstance(server_ref, str) or not server_ref or len(server_ref) > 200:
            return {"success": False, "users": []}
        try:
            with self._client_factory(self._resolve(server_ref), deadline_seconds=8) as client:
                users = [{"id": u["id"], "name": u["name"]} for u in client.list_users()
                         if u["disabled"] is False and u["name"]]
            return {"success": True, "users": users}
        except Exception:
            return {"success": False, "users": []}

    def binding_libraries(self, server_ref: str, user_id: str):
        """List library names for an explicit choice; selecting the allowed scope is separate."""
        if (not isinstance(server_ref, str) or not 0 < len(server_ref) <= 200
                or not isinstance(user_id, str) or not re.fullmatch(r"[a-f0-9]{32}", user_id)):
            return {"success": False, "libraries": []}
        try:
            with self._client_factory(self._resolve(server_ref), deadline_seconds=8) as client:
                client.verify_binding(client.describe_server()["id"], user_id)
                views = [{"id": view["id"], "name": view["name"]} for view in client.list_views(user_id)
                         if view["name"] and len(view["id"]) <= 80 and (view["collection_type"] or "").lower()
                         in ("", "movies", "tvshows", "mixed", "homevideos", "boxsets")]
            return {"success": True, "libraries": views}
        except Exception:
            return {"success": False, "libraries": []}

    def tonight(self, max_minutes: int = 0, chinese_subtitles: bool = False,
                mode: str = "discover", page: int = 1):
        """User-initiated library read. Never change a binding, snapshot or grant here."""
        if not self._run_lock.acquire(blocking=False):
            return {"success": False, "reason": "BUSY"}
        try:
            with self._lock:
                if not self._config["enabled"] or not configuration_ready(self._config):
                    return {"success": False, "reason": "SETUP_REQUIRED"}
                if not self._state.get("native_server_id"):
                    return {"success": False, "reason": "VERIFY_REQUIRED"}
                capture = self._capture()
                if not capture:
                    return {"success": False, "reason": "UNAVAILABLE"}
                epoch, cancel, config, state = capture
                baseline = None if self._profile_stale() else (state.get("profile") or {}).get("baseline")
                choice_scope = scope_key(state["binding"], state["native_server_id"])
                remembered = self._choices.snapshot(choice_scope)
            result = select_tonight(self._resolve(config["server_ref"]), server_id=state["native_server_id"],
                                    user_id=config["user_id"], scope=config["scope"], baseline=baseline,
                                    max_minutes=max_minutes, chinese_subtitles=chinese_subtitles,
                                    stop_callback=cancel.is_set, remembered=remembered, mode=mode, page=page)
            with self._lock:
                if not self._current(epoch, cancel):
                    return {"success": False, "reason": "UNAVAILABLE"}
                if mode in ("saved", "hidden") and result.get("reason") in ("LIBRARY_NOT_VISIBLE", "FORBIDDEN"):
                    result = unavailable_choices(remembered, mode, page, result["reason"])
                if result.get("success") is True:
                    result["context"] = self._choices.offer(choice_scope, result["items"], remembered)
                    result["counts"] = self._choices.counts(remembered)
                return result
        except ChoiceError as error:
            return {"success": False, "reason": error.code}
        except Exception:
            return {"success": False, "reason": "UNAVAILABLE"}
        finally:
            self._run_lock.release()

    def tonight_choice(self, payload):
        """Record a displayed movie choice locally; no Emby or subscription writes."""
        if not self._run_lock.acquire(blocking=False):
            return {"success": False, "reason": "BUSY"}
        try:
            with self._lock:
                if not self._capture() or not self._state.get("native_server_id"):
                    return {"success": False, "reason": "STALE_CONTEXT"}
                current_scope = scope_key(self._state["binding"], self._state["native_server_id"])
                return self._choices.change(current_scope, payload)
        except ChoiceError as error:
            return {"success": False, "reason": error.code}
        except Exception:
            return {"success": False, "reason": "UNAVAILABLE"}
        finally:
            self._run_lock.release()

    def _capture(self, *, binding_only=False):
        with self._lock:
            ready = binding_ready(self._config) if binding_only else configuration_ready(self._config)
            if not self._config["enabled"] or self._cancel.is_set() or not ready:
                return None
            if self._clock() < self._state.get("retry_until", 0):
                return None
            return self._epoch, self._cancel, copy.deepcopy(self._config), copy.deepcopy(self._state)

    def _current(self, epoch, cancel):
        return epoch == self._epoch and not cancel.is_set() and self._config["enabled"]

    def _commit(self, capture, **updates):
        epoch, cancel, _, _ = capture
        with self._lock:
            if not self._current(epoch, cancel):
                return False
            candidate = {**self._state, **updates}
            self._write(DATA_KEY, copy.deepcopy(candidate))
            self._state = candidate
            return True

    def _failed(self, capture, error):
        # Codes are allowlisted; raw requests exceptions may contain headers/URLs.
        code = "PROFILE_FAILED" if isinstance(error, ProfileError) else str(getattr(error, "code", "FAILED"))
        codes = {"AUTH_REQUIRED", "FORBIDDEN", "RATE_LIMITED", "SERVER_MISMATCH", "USER_MISMATCH",
                 "USER_DISABLED", "INVALID_SCOPE", "LIBRARY_NOT_VISIBLE", "EMPTY", "INCOMPLETE",
                 "ITEM_LIMIT", "PAGE_LIMIT", "CANCELLED", "DEADLINE_EXCEEDED", "NETWORK_ERROR",
                 "BINDING_UNVERIFIED", "SERVER_ERROR", "INVALID_RESPONSE", "NOT_FOUND", "PROFILE_FAILED",
                 "PARTIAL_PAGE", "SNAPSHOT_CHANGED", "DUPLICATE_ITEM", "EMPTY_SNAPSHOT",
                 "INVALID_CONFIG", "INVALID_HOST", "INVALID_CREDENTIAL", "RESPONSE_TOO_LARGE",
                 "UNSUPPORTED_ENCODING", "USERDATA_UNAVAILABLE", "REDIRECT_BLOCKED"}
        code = code if code in codes else "FAILED"
        try:
            delay = max(300, min(3600, int(getattr(error, "retry_after", None) or 300)))
        except (ValueError, TypeError):
            delay = 300
        if code in {"AUTH_REQUIRED", "FORBIDDEN", "SERVER_MISMATCH", "USER_MISMATCH", "USER_DISABLED"}:
            delay = 3600
        updates = dict(status="error", error_code=code, last_attempt_failed=True,
                       message="本次读取未完成；请检查绑定或连接。上次完整画像保留，并标记为过期。",
                       retry_until=self._clock() + delay, next_due=self._clock() + delay)
        try:
            self._commit(capture, **updates)
        except Exception:
            # If the host store is unavailable, expose a safe in-memory failure
            # while retaining the last committed profile. Never echo SQL/errors.
            with self._lock:
                if self._current(capture[0], capture[1]):
                    self._state.update(**{**updates, "error_code": "STORAGE_FAILED",
                                          "message": "画像状态未能保存；上次完整结果保留，请检查 MoviePilot 存储状态"})
        return {"success": False, "message": "快照未完成，详情见偏好状态卡片"}

    def validate(self):
        capture = self._capture(binding_only=True)
        if not capture or not self._run_lock.acquire(blocking=False):
            return {"success": False, "message": "请完善配置、等待冷却或当前任务结束"}
        try:
            capture = self._capture(binding_only=True)
            if not capture:
                return {"success": False, "message": "配置已变化或进入错误冷却，请刷新状态"}
            epoch, cancel, config, state = capture
            if not self._commit(capture, status="validating", message="正在验证服务器和指定用户"):
                return {"success": False}
            with self._client_factory(self._resolve(config["server_ref"]), deadline_seconds=20,
                                      stop_callback=cancel.is_set) as client:
                server = client.describe_server()
                expected = state.get("native_server_id") or server["id"]
                user = client.verify_binding(expected, config["user_id"])
                views = client.list_views(config["user_id"])
                visible = {v["id"] for v in views}
                if config["scope"]["mode"] == "selected_ids" and not set(config["scope"]["library_ids"]).issubset(visible):
                    raise EmbyReadError("INVALID_SCOPE")
                ready = configuration_ready(config)
                success = self._commit(capture, native_server_id=expected,
                                       user_name=user["name"], visible_libraries=views,
                                       status="queued" if ready else "scope_required",
                                       message="绑定验证通过，1 分钟内开始首次快照" if ready else "绑定验证通过；请从可见库中选择范围后保存，尚未读取观看记录",
                                       error_code=None, retry_until=0, next_due=self._clock())
                return {"success": success, "message": "绑定验证通过，等待宿主调度采集"}
        except Exception as error:
            return self._failed(capture, error)
        finally:
            self._run_lock.release()

    def request_refresh(self):
        with self._lock:
            capture = self._capture()
            if not capture or not self._state.get("native_server_id"):
                return {"success": False, "message": "请先验证绑定，或等待错误冷却结束"}
            if self._run_lock.locked():
                return {"success": True, "message": "当前任务仍在运行，请稍后刷新页面"}
            try:
                self._commit(capture, status="queued", next_due=self._clock(),
                             message="已排队，1 分钟内开始；稍后刷新页面查看结果")
            except Exception as error:
                return self._failed(capture, error)
            return {"success": True, "message": self._state["message"]}

    def tick(self):
        """Host runs this cheap check each minute; IO only at an explicit due time."""
        capture = self._capture()
        if not capture:
            return
        _, cancel, config, state = capture
        if not state.get("native_server_id") or self._clock() < state.get("next_due", 0):
            return
        if not self._run_lock.acquire(blocking=False):
            return
        try:
            # Another operation may have finished between capture and lock.
            # Re-read both generation and due time before beginning network IO.
            capture = self._capture()
            if not capture:
                return
            _, cancel, config, state = capture
            if not state.get("native_server_id") or self._clock() < state.get("next_due", 0):
                return
            if not self._commit(capture, status="running", last_attempt=self._clock(),
                                message="正在读取选定用户的完整快照"):
                return
            now = self._clock()
            accumulator = SnapshotAccumulator(as_of=datetime.fromtimestamp(now, timezone.utc))
            with self._client_factory(self._resolve(config["server_ref"]), stop_callback=cancel.is_set) as client:
                user = client.verify_binding(state["native_server_id"], config["user_id"])
                for item in client.iter_items(config["user_id"], ["Series", "Movie", "Episode"],
                                              config["scope"], stop_callback=cancel.is_set):
                    if cancel.is_set():
                        return
                    if item.get("Type") == "Series":
                        accumulator.add_series(item)
                    else:
                        accumulator.add_item(item)
                if not client.snapshot_complete or cancel.is_set():
                    raise EmbyReadError("INCOMPLETE")
                result = accumulator.finish()
            self._commit(capture, profile=result, user_name=user["name"], last_success=now,
                         next_due=self._clock() + config["hours"] * 3600, retry_until=0,
                         error_code=None, last_attempt_failed=False, status="ready",
                         message="完整快照已更新；只用于偏好展示，自动订阅未启用")
        except Exception as error:
            self._failed(capture, error)
        finally:
            self._run_lock.release()
