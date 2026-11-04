"""A bounded personal movie shortlist. Only user-scoped Emby reads, no persistent state."""
from __future__ import annotations

import re

from .candidates import DIMENSION_LABELS, _baseline_index, _taste
from .emby import EmbyReadError, EmbyReadOnlyClient, ITEM_ID, _base_url
from .profile import _provider_identity, _snapshot_features


MAX_MOVIES = 96
MAX_ROOTS = 8
PAGE_SIZE = 32
TICKS_PER_MINUTE = 600_000_000
FIELDS = "Genres,People,ProductionYear,ProviderIds,RunTimeTicks,PlayAccess,MediaSources,AlternateMediaSources,MediaStreams"
CHINESE = re.compile(r"(?:zh|zho|chi|cmn)(?:[-_](?:hans|hant))?(?:[-_](?:cn|tw|hk|mo|sg))?", re.I)


def _track_evidence(streams):
    if not isinstance(streams, list) or len(streams) > 512:
        return False
    # A forced track can cover only signs or foreign dialogue.
    return any(isinstance(track, dict) and track.get("Type") == "Subtitle" and track.get("IsForced", False) is False
               and isinstance(track.get("Language"), str) and CHINESE.fullmatch(track["Language"])
               for track in streams)


def _version_facts(item):
    """The link opens a title, so all editions must satisfy an asserted constraint."""
    sources = item.get("MediaSources")
    count = item.get("MediaSourceCount")
    if count is not None and (type(count) is not int or count < 1):
        return None, None
    if (sources is None or sources == []) and count == 1:
        sources = [{}]  # Emby can put the single edition's fields on the item.
    if (not isinstance(sources, list) or not 1 <= len(sources) <= 32
            or (count is not None and count != len(sources))
            or any(not isinstance(source, dict) for source in sources)):
        return None, None
    durations, tracks = [], []
    for source in sources:
        single = len(sources) == 1
        ticks = source.get("RunTimeTicks", item.get("RunTimeTicks") if single else None)
        durations.append((ticks + TICKS_PER_MINUTE - 1) // TICKS_PER_MINUTE
                         if type(ticks) is int and 0 < ticks <= 7 * 24 * 60 * TICKS_PER_MINUTE else None)
        streams = source.get("MediaStreams", item.get("MediaStreams") if single else None)
        tracks.append(_track_evidence(streams))
    return max(durations) if None not in durations else None, True if all(tracks) else None


class _TonightClient(EmbyReadOnlyClient):
    def _user_summary(self, raw):
        user = super()._user_summary(raw)
        policy = raw.get("Policy")
        if isinstance(policy, dict) and policy.get("EnableMediaPlayback") is False:
            self._fail("FORBIDDEN")
        return user

    def shortlist(self, server_id, user_id, scope, index, link_base, max_minutes, chinese_subtitles):
        self.verify_binding(server_id, user_id)
        uid = self._bound_user(user_id)
        roots = self._roots(uid, scope)
        limited = len(roots) > MAX_ROOTS
        roots = roots[:MAX_ROOTS]
        budget = MAX_MOVIES // len(roots)
        choices = {}
        for root in roots:
            offset, total = 0, None
            while offset < budget:
                limit = min(PAGE_SIZE, budget - offset)
                params = {"Recursive": "true", "IncludeItemTypes": "Movie", "IsPlayed": "false",
                          "IsPlaceHolder": "false", "EnableImages": "false", "EnableUserData": "true",
                          "Fields": FIELDS, "SortBy": "DateCreated,SortName", "SortOrder": "Descending",
                          "StartIndex": offset, "Limit": limit}
                if root is not None:
                    params["ParentId"] = root
                page = self._get(f"/Users/{uid}/Items", params)
                if not isinstance(page, dict):
                    self._fail("INVALID_RESPONSE")
                rows, count = page.get("Items"), page.get("TotalRecordCount")
                if (type(count) is not int or count < offset or not isinstance(rows, list)
                        or len(rows) != min(limit, count - offset) or total is not None and count != total):
                    self._fail("INCOMPLETE")
                total = count
                limited |= count > budget
                for raw in rows:
                    movie = self._item(raw, ("Movie",), uid)
                    if (movie["UserData"].get("Played") is not False or raw.get("LocationType") == "Virtual"
                            or raw.get("IsPlaceHolder") is True or raw.get("PlayAccess") not in (None, "Full")
                            or raw.get("IsMissing") is True):
                        continue
                    title = raw.get("Name")
                    if not isinstance(title, str) or not title.strip():
                        continue
                    minutes, chinese = _version_facts(raw)
                    if (max_minutes and (minutes is None or minutes > max_minutes)) or (chinese_subtitles and not chinese):
                        continue
                    taste, _, details, support = _taste(_snapshot_features(movie), index)
                    matched = next((f"{DIMENSION_LABELS[dim['dimension']]}相合 · {dim['matched_labels'][0]}"
                                    for dim in details if dim["matched_labels"]), None)
                    iid = movie["Id"]
                    result = {"id": iid, "title": title.strip()[:240], "year": movie.get("ProductionYear"),
                              "minutes": minutes, "chinese_subtitles": chinese,
                              "score": round(taste * 100, 1) if support else None, "reason": matched,
                              "url": f"{link_base}/web/index.html#!/item?id={iid}&context=home&serverId={server_id}"}
                    if isinstance(raw.get("MediaSources"), list) and len(raw["MediaSources"]) > 1 and minutes is not None:
                        result["version"] = "多版本，时长按最长计"
                    key = _provider_identity(movie, "movie") or f"emby:{iid}"
                    if key not in choices or taste > choices[key][0]:
                        choices[key] = taste, result
                offset += len(rows)
                if offset == total:
                    break
        self._check()
        ordered = sorted(choices.values(), key=lambda pair: -pair[0])
        return {"success": True, "items": [row for _, row in ordered[:3]], "limited": limited}


def select_tonight(server_config, *, server_id, user_id, scope, baseline=None,
                   max_minutes=0, chinese_subtitles=False, stop_callback=None):
    if type(max_minutes) is not int or not 0 <= max_minutes <= 360 or type(chinese_subtitles) is not bool:
        return {"success": False, "reason": "INVALID_FILTER"}
    if not isinstance(server_id, str) or not ITEM_ID.fullmatch(server_id):
        return {"success": False, "reason": "VERIFY_REQUIRED"}
    if not isinstance(server_config, dict):
        return {"success": False, "reason": "SETUP_REQUIRED"}
    try:
        link_base = _base_url(server_config.get("play_host") or server_config.get("host"))
        with _TonightClient(server_config, deadline_seconds=12, connect_timeout=3, read_timeout=5,
                            stop_callback=stop_callback) as client:
            return client.shortlist(server_id, user_id, scope, _baseline_index(baseline),
                                    link_base, max_minutes, chinese_subtitles)
    except EmbyReadError as err:
        if err.code in {"USER_MISMATCH", "SERVER_MISMATCH", "BINDING_REQUIRED", "BINDING_UNVERIFIED", "AUTH_REQUIRED"}:
            reason = "VERIFY_REQUIRED"
        elif err.code in {"USER_DISABLED", "FORBIDDEN"}:
            reason = "FORBIDDEN"
        elif err.code == "LIBRARY_NOT_VISIBLE":
            reason = "LIBRARY_NOT_VISIBLE"
        elif err.code in {"INVALID_SCOPE", "SCOPE_REQUIRED", "INVALID_CONFIG", "INVALID_HOST", "INVALID_USER_ID"}:
            reason = "SETUP_REQUIRED"
        elif err.code in {"RATE_LIMITED", "CANCELLED"}:
            reason = err.code
        else:
            reason = "UNAVAILABLE"
        return {"success": False, "reason": reason}
