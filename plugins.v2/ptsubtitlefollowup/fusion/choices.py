"""Bounded, local choices for one verified Emby identity and library scope.

Only item identifiers and explicit choices are retained. Browser receipts are
short-lived and process-local; persisted data never grants access to media.
Call under the coordinator lock.
"""
from __future__ import annotations

import copy
import hashlib
import re
import secrets

from .emby import ITEM_ID


DATA_KEY = "fusion_choices_v1"
MAX_CHOICES = 200
MAX_SCOPES = 16
PAGE_SIZE = 12
RECEIPT_SECONDS = 15 * 60
MAX_RECEIPTS = 128
HEX = re.compile(r"[a-f0-9]{64}")


class ChoiceError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def scope_key(binding, native_server):
    return hashlib.sha256((binding + "\n" + native_server).encode()).hexdigest()


def movie_key(identity):
    return hashlib.sha256(identity.encode()).hexdigest()


class ChoiceStore:
    def __init__(self, read, write, clock):
        self._read, self._write, self._clock = read, write, clock
        self.reset()

    def reset(self):
        self._receipts = {}
        self._changes = {}

    def _load(self):
        try:
            raw = self._read(DATA_KEY)
            if raw is None:
                return {"schema": 1, "scopes": {}}
            if not isinstance(raw, dict) or raw.get("schema") != 1 or not isinstance(raw.get("scopes"), dict):
                raise ValueError
            if len(raw["scopes"]) > MAX_SCOPES:
                raise ValueError
            for key, scope in raw["scopes"].items():
                if (not isinstance(key, str) or not HEX.fullmatch(key) or not isinstance(scope, dict)
                        or type(scope.get("revision")) is not int or scope["revision"] < 0
                        or not isinstance(scope.get("entries"), dict) or len(scope["entries"]) > MAX_CHOICES):
                    raise ValueError
                for identity, entry in scope["entries"].items():
                    if (not isinstance(identity, str) or not HEX.fullmatch(identity) or not isinstance(entry, dict)
                            or entry.get("action") not in ("later", "hide")
                            or not isinstance(entry.get("item_id"), str) or not ITEM_ID.fullmatch(entry["item_id"])
                            or type(entry.get("revision")) is not int or not 0 < entry["revision"] <= scope["revision"]):
                        raise ValueError
            return copy.deepcopy(raw)
        except Exception:
            raise ChoiceError("STORAGE_FAILED") from None

    @staticmethod
    def counts(entries):
        return {kind: sum(entry["action"] == kind for entry in entries.values()) for kind in ("later", "hide")}

    def snapshot(self, scope):
        entries = self._load()["scopes"].get(scope, {}).get("entries", {})
        return copy.deepcopy(entries)

    def _prune(self):
        now = self._clock()
        self._receipts = {key: receipt for key, receipt in self._receipts.items() if receipt["until"] > now}
        needed = {(r["scope"], item["key"]) for r in self._receipts.values() for item in r["items"].values()}
        self._changes = {key: revision for key, revision in self._changes.items() if key in needed}

    def _revision(self, scope, key, entries):
        return self._changes.get((scope, key), entries.get(key, {}).get("revision", 0))

    def offer(self, scope, rows, entries):
        """Strip internal identity fields and issue a receipt for displayed rows."""
        self._prune()
        # These are ephemeral read receipts, not user choices. Expiring the oldest
        # one at the limit is equivalent to the usual short receipt lifetime.
        if len(self._receipts) >= MAX_RECEIPTS:
            del self._receipts[next(iter(self._receipts))]
        context = secrets.token_hex(24)
        offered = {}
        for row in rows:
            key = row.pop("_choice_key")
            current = entries.get(key)
            row["choice"] = current["action"] if current else "none"
            offered[row["id"]] = {"key": key, "revision": self._revision(scope, key, entries),
                                  "action": row["choice"], "available": row.get("available", True)}
        self._receipts[context] = {"scope": scope, "until": self._clock() + RECEIPT_SECONDS, "items": offered}
        return context

    def change(self, scope, payload):
        if (not isinstance(payload, dict) or set(payload) != {"context", "item_id", "action"}
                or not isinstance(payload["context"], str) or not re.fullmatch(r"[a-f0-9]{48}", payload["context"])
                or not isinstance(payload["item_id"], str) or not ITEM_ID.fullmatch(payload["item_id"])
                or payload["action"] not in ("later", "hide", "clear")):
            raise ChoiceError("INVALID_CHOICE")
        self._prune()
        receipt = self._receipts.get(payload["context"])
        if not receipt or receipt["scope"] != scope:
            raise ChoiceError("STALE_CONTEXT")
        offered = receipt["items"].get(payload["item_id"])
        if not offered:
            raise ChoiceError("INVALID_CHOICE")
        raw = self._load()
        partition = raw["scopes"].get(scope, {"revision": 0, "entries": {}})
        entries = partition["entries"]
        key, action = offered["key"], payload["action"]
        previous = entries.get(key, {}).get("action", "clear")
        if offered["revision"] != self._revision(scope, key, entries):
            raise ChoiceError("CHOICE_CHANGED")
        if not offered["available"] and action != "clear" and action != offered.get("restore"):
            raise ChoiceError("INVALID_CHOICE")
        if action != "clear" and key not in entries and (len(entries) >= MAX_CHOICES or (
                scope not in raw["scopes"] and len(raw["scopes"]) >= MAX_SCOPES)):
            raise ChoiceError("CHOICE_LIMIT")
        revision = max([partition["revision"], *(v for (s, _), v in self._changes.items() if s == scope)]) + 1
        if action == "clear":
            entries.pop(key, None)
        else:
            entries[key] = {"item_id": payload["item_id"], "action": action, "revision": revision}
        partition["revision"] = revision
        if entries:
            raw["scopes"][scope] = partition
        else:
            raw["scopes"].pop(scope, None)
        try:
            self._write(DATA_KEY, raw)
        except Exception:
            raise ChoiceError("STORAGE_FAILED") from None
        self._changes[(scope, key)] = revision
        undo_row = {"id": payload["item_id"], "_choice_key": key, "available": offered["available"]}
        undo_context = self.offer(scope, [undo_row], entries)
        # Restoring a just-removed unavailable row needs no media disclosure.
        self._receipts[undo_context]["items"][payload["item_id"]]["restore"] = previous
        return {"success": True, "counts": self.counts(entries),
                "undo": {"context": undo_context, "item_id": payload["item_id"], "action": previous}}
