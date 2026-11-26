"""Coordinate missing-episode searches for verified MoviePilot season snapshots."""
from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import threading
import time

from .execution_store import _file_lock


GRACE_SECONDS = 3 * 60 * 60
SEARCH_INTERVAL = 15 * 60
RESOURCE_RECHECK = 6 * 60 * 60
MAX_EPISODE = 5000


def _episodes(value):
    if not isinstance(value, (list, set, tuple, frozenset)) or len(value) > MAX_EPISODE:
        raise ValueError("Invalid episode list")
    if any(type(number) is not int or not 1 <= number <= MAX_EPISODE for number in value):
        raise ValueError("Ambiguous episode number")
    return set(value)


def _label(value, limit=250):
    return isinstance(value, str) and 0 < len(value) <= limit and value.isprintable()


def _offers(resources, spec):
    result = []
    for resource in resources:
        if (resource.get("spec") != spec or not _label(resource.get("group"))
                or not _label(resource.get("token"), 512)):
            continue
        try:
            episodes = _episodes(resource.get("episodes"))
        except ValueError:
            continue
        if episodes:
            result.append((resource, episodes))
    return result


def _select(offers, group, available):
    """Choose a resource that can deliver only the still-needed episodes."""
    choices = []
    for resource, episodes in offers:
        wanted = episodes & available
        if resource["group"] == group and wanted and (episodes == wanted or resource.get("selectable") is True):
            choices.append((resource, wanted))
    return max(choices, key=lambda item: len(item[1]), default=None)


class GapService:
    def __init__(self, directory, host_factory, *, current_config=None, clock=time.time):
        self._directory = directory
        self._host_factory = host_factory
        self._current_config = current_config
        self._clock = clock
        self._cancel = threading.Event()
        self._cancel.set()
        self._lock = threading.RLock()
        self._tick_lock = threading.Lock()
        self._state = "disabled"

    @property
    def enabled(self):
        return not self._cancel.is_set()

    def configure(self, config):
        with self._lock:
            self._cancel.set()
            self._cancel = threading.Event()
            if config is not True and not (isinstance(config, dict) and config.get("gap_enabled") is True):
                self._cancel.set()
            self._state = "ready" if self.enabled else "disabled"

    def stop(self):
        with self._lock:
            self._cancel.set()
            self._state = "disabled"

    def _allowed(self, cancel):
        if cancel.is_set():
            return False
        if self._current_config is None:
            return True
        try:
            return (self._current_config() or {}).get("gap_enabled") is True and not cancel.is_set()
        except Exception:
            return False

    def tick(self):
        cancel = self._cancel
        if not self._allowed(cancel) or not self._tick_lock.acquire(blocking=False):
            return
        try:
            self._state = "running"
            directory = Path(self._directory())
            directory.mkdir(parents=True, exist_ok=True)
            with _file_lock(directory / "gaps.lock"), closing(sqlite3.connect(directory / "gaps.sqlite3")) as db:
                db.execute("PRAGMA synchronous=FULL")
                db.execute("""CREATE TABLE IF NOT EXISTS season
                    (key TEXT PRIMARY KEY, record TEXT NOT NULL, visible INTEGER NOT NULL)""")
                self._scan(db, self._host_factory(), cancel)
            if not cancel.is_set():
                self._state = "ready"
        except BlockingIOError:
            if not cancel.is_set():
                self._state = "ready"
        except Exception:
            if not cancel.is_set():
                self._state = "error"
        finally:
            self._tick_lock.release()

    @staticmethod
    def _save(db, key, record, *, commit=True):
        db.execute("INSERT OR REPLACE INTO season VALUES (?,?,?)",
                   (key, json.dumps(record, ensure_ascii=False), int(record["state"] != "complete")))
        if commit:
            db.commit()

    def _scan(self, db, host, cancel):
        known = {key: json.loads(raw) for key, raw in db.execute("SELECT key, record FROM season")}
        resume = []
        for row in known.values():
            if row.get("resume") and (row["missing"] or row["pending"] or row["borrowed_groups"]):
                has_borrow = bool(row["borrowed_groups"] or any(p["mode"] == "borrow" for p in row["pending"].values()))
                resume.append({**row["resume"], "borrowed_intent": has_borrow,
                               "original_group": row["group"], "original_spec": row["spec"]})
        snapshots = list(host.snapshots(resume=resume))
        now = int(self._clock())
        due = []
        visible = set()
        for snap in snapshots:
            key = snap["key"]
            if not _label(key, 1024) or not _label(snap.get("title")) or not _label(snap.get("group")):
                continue
            if not _label(snap.get("spec")) or type(snap.get("season")) is not int or snap["season"] < 1:
                continue
            previous = known.get(key, {})
            record = self._observe(host, snap, previous, now)
            self._save(db, key, record, commit=False)
            visible.add(key)
            held = _episodes(snap["active"]) | {int(ep) for ep in record["pending"]}
            missing = set(record["missing"]) - held - set(record["borrowed"])
            returning = set(record["returning"]) - held
            if (missing or returning) and record["state"] != "blocked" and record["next_search"] <= now:
                due.append((record["next_search"], key, snap, record, missing, returning))
        for key in known.keys() - visible:
            # A missing snapshot is never permission to continue a deleted subscription.
            db.execute("UPDATE season SET visible=0 WHERE key=?", (key,))
            row = known[key]
            if not row.get("pending") and not row.get("borrowed_groups") and now - row["last_seen"] > 30 * 86400:
                db.execute("DELETE FROM season WHERE key=?", (key,))
        db.commit()
        if due and self._allowed(cancel):
            _, key, snap, record, missing, returning = min(due, key=lambda item: item[:2])
            self._search(db, host, snap, key, record, missing, returning, cancel, now)

    def _observe(self, host, snap, previous, now):
        present = _episodes(snap["present"])
        active = _episodes(snap["active"])
        first = snap["first_episode"]
        if type(first) is not int or not 1 <= first <= MAX_EPISODE:
            raise ValueError("Invalid first episode")
        archived = snap.get("archived") is True
        missing = set() if archived else set(range(first, max(present, default=0) + 1)) - present
        original = previous.get("group") or snap["group"]
        spec = previous.get("spec") or snap["spec"]
        dates = {str(ep): previous.get("dates", {}).get(str(ep), now) for ep in missing}
        pending = previous.get("pending", {})
        borrowed = previous.get("borrowed_groups", {})
        borrowed_tasks = previous.get("borrowed_tasks", {})
        restored = set(previous.get("restored", []))
        groups = snap["episode_groups"]
        if not isinstance(groups, dict):
            raise ValueError("Missing imported episode provenance")
        def group_for(ep):
            return groups.get(ep, groups.get(str(ep))) if ep in present and spec == snap["spec"] else None
        def same_task(ep, expected):
            tasks = snap.get("episode_tasks")
            if tasks is None:
                return False
            actual = tasks.get(ep) or tasks.get(str(ep))
            return bool(expected and isinstance(actual, str) and expected.casefold() == actual.casefold())
        review = set()
        for key, intent in list(pending.items()):
            ep = int(key)
            actual = group_for(ep)
            if intent["mode"] == "borrow" and actual == intent["group"]:
                if same_task(ep, intent.get("task")):
                    borrowed[key] = actual
                    borrowed_tasks[key] = intent["task"]
                    del pending[key]
                else:
                    review.add(ep)
            elif actual == original:
                del pending[key]
            elif actual and intent["mode"] == "original":
                # A competing download filled the hole. Never claim or replace it.
                del pending[key]
            elif ep not in active or now >= intent["at"] + SEARCH_INTERVAL:
                receipt = "unknown"
                if intent.get("task") and intent.get("downloader"):
                    try:
                        receipt = host.receipt_status(snap, intent["task"], intent["downloader"], ep)
                    except Exception:
                        pass
                if receipt == "discarded":
                    del pending[key]
                elif receipt in ("transferred", "failed") or (receipt != "active" and now >= intent["at"] + SEARCH_INTERVAL):
                    review.add(ep)
        for key in list(borrowed):
            ep = int(key)
            if group_for(ep) == original:
                restored.add(ep)
                del borrowed[key]
                borrowed_tasks.pop(key, None)
                pending.pop(key, None)
            elif group_for(ep) != borrowed[key] or not same_task(ep, borrowed_tasks.get(key)):
                review.add(ep)
        # Only replace a verified episode borrowed by this coordinator; do not
        # overwrite a third group's copy or resurrect a manually deleted file.
        returning = sorted(int(k) for k in borrowed if int(k) not in review)
        if spec != snap["spec"] or original != snap["group"]:
            state = "blocked"
        elif review:
            state = "review"
        elif any(p["outcome"] == "unknown" for p in pending.values()):
            state = "uncertain"
        elif any(p["mode"] == "restore" for p in pending.values()):
            state = "restoring"
        elif any(p["mode"] == "borrow" for p in pending.values()):
            state = "borrowing"
        elif borrowed:
            state = "borrowed"
        elif active.intersection(missing):
            state = "downloading"
        elif pending:
            state = "submitted"
        elif missing:
            state = "searching_backup" if min(dates.values()) + GRACE_SECONDS <= now else "waiting"
        elif restored:
            state = "restored"
        else:
            state = "complete"
        # Pin only the host's minimal archive identity (no free-form subscription config).
        resume = snap.get("resume") or previous.get("resume") or {}
        resume = {k: resume[k] for k in ("sid", "media_id", "season", "key", "activity", "downloader",
                                        "category", "category_id")
                  if k in resume and (resume[k] is None or type(resume[k]) is int or _label(resume[k],512))}
        oldest = min(dates.values(), default=previous.get("first_seen", now))
        return dict(title=snap["title"], season=snap["season"], group=original, spec=spec, state=state,
                    dates=dates, missing=sorted(missing), first_seen=oldest, deadline=oldest + GRACE_SECONDS,
                    pending=pending, borrowed_groups=borrowed, borrowed_tasks=borrowed_tasks,
                    borrowed=sorted(map(int,borrowed)),
                    returning=returning, restored=sorted(restored), next_search=previous.get("next_search", 0),
                    last_seen=now, resume=resume, review_episodes=sorted(review),
                    skipped_resources={token: until for token, until in previous.get("skipped_resources", {}).items()
                                       if until > now})

    def _search(self, db, host, snap, key, record, missing, returning, cancel, now):
        deadlines = {ep:record["dates"][str(ep)] + GRACE_SECONDS for ep in missing}
        expired = {ep for ep, deadline in deadlines.items() if deadline <= now}
        # A search just before a three-hour deadline must not postpone it by 15 minutes.
        record["next_search"] = min([now + SEARCH_INTERVAL, *(t for t in deadlines.values() if t > now)])
        self._save(db, key, record)
        try:
            resources = host.search(snap, include_alternatives=bool(expired),
                                    allowed=lambda: self._allowed(cancel))
            offers = [(offer, episodes) for offer, episodes in _offers(resources, record["spec"])
                      if offer["token"] not in record["skipped_resources"]]
        except Exception:
            record["state"] = "retry"
            self._save(db, key, record)
            return
        # Restore borrowed episodes only when this precise group and spec reappear.
        for mode, allowed in (("original", missing), ("restore", returning)):
            selected = _select(offers, record["group"], allowed)
            if selected:
                self._dispatch(db, host, snap, key, record, *selected, mode, cancel)
                return
        if expired:
            choices = []
            for group in dict.fromkeys(res["group"] for res, _ in offers if res["group"] != record["group"]):
                selected = _select(offers, group, expired)
                if selected:
                    choices.append(selected)
            if choices:
                selected = max(choices, key=lambda item: len(item[1]))
                self._dispatch(db, host, snap, key, record, *selected, "borrow", cancel)

    def _dispatch(self, db, host, snap, key, record, resource, episodes, mode, cancel):
        if not self._allowed(cancel):
            return
        for ep in episodes:
            record["pending"][str(ep)] = {"mode":mode, "group":resource["group"], "outcome":"unknown",
                                          "at":int(self._clock())}
        record["state"] = "uncertain"
        self._save(db, key, record)
        try:
            outcome = host.dispatch(snap, resource, tuple(sorted(episodes)), mode, lambda: self._allowed(cancel))
        except Exception:
            outcome = "unknown"
        if isinstance(outcome, dict):
            task, downloader = outcome.get("task"), outcome.get("downloader")
            if (isinstance(task, str) and re.fullmatch(r"[a-fA-F0-9]{32,64}", task) and _label(downloader)):
                for ep in episodes:
                    record["pending"][str(ep)].update(task=task, downloader=downloader)
            outcome = outcome.get("outcome")
        if outcome == "accepted":
            record["state"] = {"original":"submitted", "restore":"restoring", "borrow":"borrowing"}[mode]
            for ep in episodes:
                record["pending"][str(ep)]["outcome"] = "accepted"
        elif outcome in ("not_sent", "unverified_resource"):
            record["state"] = "retry"
            if outcome == "unverified_resource":
                # A mislabeled listing must not monopolize every future search.
                record["skipped_resources"][resource["token"]] = int(self._clock()) + RESOURCE_RECHECK
            for ep in episodes:
                record["pending"].pop(str(ep))
        if record["review_episodes"]:
            record["state"] = "review"
        self._save(db, key, record)

    def status(self):
        status = {"enabled": self.enabled, "status": self._state, "seasons": []}
        if not status["enabled"]:
            return status
        path = Path(self._directory()).resolve() / "gaps.sqlite3"
        if not path.is_file():
            return status
        try:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                for (raw,) in db.execute("SELECT record FROM season WHERE visible=1 ORDER BY key"):
                    record = json.loads(raw)
                    status["seasons"].append({k: record[k] for k in
                                              ("title", "season", "missing", "first_seen", "deadline", "state",
                                               "group", "borrowed", "borrowed_groups", "returning", "restored",
                                               "review_episodes")})
        except (sqlite3.Error, ValueError, KeyError):
            status["status"] = "error"
        status["seasons"].sort(key=lambda row: row["first_seen"])
        return status
