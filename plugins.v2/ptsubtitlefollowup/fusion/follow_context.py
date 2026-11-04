"""Local-only normalization of the host's sharing gate and opaque follow IDs."""
from collections.abc import Mapping
import hashlib
import json


MAX_FOLLOWS = 200
MAX_UID_LENGTH = 256
UNAVAILABLE_CODES = frozenset({"SHARING_DISABLED", "FOLLOWS_EMPTY", "INVALID_FOLLOWS", "CONTEXT_UNAVAILABLE"})


def valid_follow_uid(value):
    return (isinstance(value, str) and 1 <= len(value) <= MAX_UID_LENGTH
            and value == value.strip() and all(char.isprintable() for char in value))


def normalize_follow_context(raw):
    """Fail closed on incomplete or malformed snapshots; never trim/guess IDs.

    Results are idempotent. UID lists belong only in memory, never logs, plugin
    status or saved configuration; persist only follow_fingerprint(result).
    """
    result = {"context_valid": False, "sharing_enabled": False, "followed_uids": [],
              "error_code": "CONTEXT_UNAVAILABLE"}
    if not isinstance(raw, Mapping):
        return result
    if raw.get("context_valid") is not True:
        if raw.get("error_code") == "INVALID_FOLLOWS":
            result["error_code"] = "INVALID_FOLLOWS"
        return result
    enabled = raw.get("sharing_enabled")
    if type(enabled) is not bool:
        return result
    if not enabled:
        result.update(context_valid=True, error_code="SHARING_DISABLED")
        return result
    values = raw.get("followed_uids")
    if values is None:
        values = []
    if not isinstance(values, list) or len(values) > MAX_FOLLOWS or not all(valid_follow_uid(value) for value in values):
        result["error_code"] = "INVALID_FOLLOWS"
        return result
    values = sorted(set(values))
    result.update(context_valid=True, sharing_enabled=True, followed_uids=values,
                  error_code=None if values else "FOLLOWS_EMPTY")
    return result


def follow_fingerprint(context):
    """Stable local context marker; no raw UID leaves the plugin namespace."""
    clean = normalize_follow_context(context)
    return hashlib.sha256(json.dumps(clean, ensure_ascii=True, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()
