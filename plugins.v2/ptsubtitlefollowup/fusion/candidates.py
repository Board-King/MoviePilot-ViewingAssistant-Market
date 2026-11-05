"""Pure bounded candidate fusion for shadow ranking, with no execution or I/O.

Source/group weights are one. Formula constants and TTL are engineering defaults,
not user choices or measured probabilities. Source groups must be assigned by
trusted adapters; an absent group never supplies an independent consensus vote.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, datetime, timezone
import math
import re


DIMENSIONS = ("genres", "languages", "decades", "actors", "directors", "keywords")
MAX_CANDIDATES = 500
MAX_SOURCES = 16
MAX_ITEMS = 5000
ALGORITHM_VERSION = "taste-first-v2"
DIMENSION_LABELS = {"genres": "题材", "languages": "语言", "decades": "年代",
                    "actors": "演员", "directors": "导演", "keywords": "关键词"}


class CandidateError(ValueError):
    """A fixed, non-sensitive input or capacity error."""


def _time(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _now(value):
    result = _time(value)
    if result is None:
        raise CandidateError("A timezone-aware analysis timestamp is required")
    return result


def _text(value, limit=128):
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _identifier(value, limit=128):
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1," + str(limit) + r"}", value):
        return value
    return None


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _identity(item):
    kind, ids = item.get("media_type"), item.get("external_ids")
    if not isinstance(kind, str) or kind not in {"movie", "tv"} or not isinstance(ids, dict):
        return None
    for provider in ("themoviedb", "douban", "imdb"):
        identifier = _identifier(ids.get(provider))
        pattern = r"tt[0-9]{1,20}" if provider == "imdb" else r"[1-9][0-9]{0,19}"
        if identifier and re.fullmatch(pattern, identifier):
            url = ({"themoviedb": f"https://www.themoviedb.org/{kind}/{identifier}",
                    "douban": f"https://movie.douban.com/subject/{identifier}/",
                    "imdb": f"https://www.imdb.com/title/{identifier}/"})[provider]
            return provider, identifier, kind, url
    return None


def _metadata(raw, now):
    raw = raw if isinstance(raw, dict) else {}
    result = {}
    for dimension in DIMENSIONS:
        values = raw.get(dimension)
        limit = 5 if dimension in {"actors", "directors", "languages"} else 32
        labels = []
        if isinstance(values, list):
            for value in values:
                label = _text(value)
                if label and label not in labels:
                    labels.append(label)
                    if len(labels) == limit:
                        break
        result[dimension] = labels
    ratings = []
    for rating in (raw.get("ratings") or [])[:8] if isinstance(raw.get("ratings"), list) else []:
        if not isinstance(rating, dict):
            continue
        provider, value, scale = _identifier(rating.get("provider"), 40), rating.get("value"), rating.get("scale")
        if not provider or not _finite(value) or not _finite(scale) or not 0 < scale <= 100 or not 0 <= value <= scale:
            continue
        count = rating.get("count")
        count = count if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 10**10 else None
        observed = _time(rating.get("observed_at"))
        ratings.append({"provider": provider, "value": value, "scale": scale, "count": count,
                        "observed_at": observed.isoformat() if observed and observed <= now else None})
    result["ratings"] = ratings
    return result


def normalize_item(item, *, as_of):
    """Return a single-source fragment or None. Never trust input URLs or aliases."""
    now = _now(as_of)
    if not isinstance(item, dict):
        return None
    identity = _identity(item)
    source_id = _identifier(item.get("source_id"), 80)
    source_item_id = _identifier(item.get("source_item_id"))
    intent = item.get("intent_kind")
    if not identity or not source_id or not source_item_id or not isinstance(intent, str) or intent not in {"discovery", "explicit_want"}:
        return None
    provider, identifier, kind, url = identity
    observed = _time(item.get("observed_at"))
    observed = observed if observed and observed <= now else None
    seen = (observed or now).isoformat()
    season = item.get("season_hint")
    season = season if kind == "tv" and isinstance(season, int) and not isinstance(season, bool) and 0 <= season <= 10000 else None
    rank, size = item.get("rank"), item.get("list_size")
    if not (isinstance(rank, int) and not isinstance(rank, bool) and isinstance(size, int)
            and not isinstance(size, bool) and 1 <= rank <= size <= 1_000_000):
        rank, size = None, None
    year = item.get("year")
    year = year if isinstance(year, int) and not isinstance(year, bool) and 1800 <= year <= 2200 else None
    released = item.get("release_date")
    try:
        released = date.fromisoformat(released).isoformat() if isinstance(released, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", released) else None
    except ValueError:
        released = None
    group = _identifier(item.get("source_group"), 80)
    source = {"source_id": source_id, "source_item_id": source_item_id,
              "source_group": group.casefold() if group else None, "intent_kind": intent,
              "list_name": _text(item.get("list_name"), 120), "rank": rank, "list_size": size,
              "observed_at": observed.isoformat() if observed else None,
              "first_seen": seen, "last_seen": seen, "provenance_url": url,
              "title": _text(item.get("title"), 240), "year": year, "release_date": released,
              "season_hint": season, "metadata": _metadata(item.get("metadata"), now)}
    return {"entity_key": f"{provider}:{kind}:{identifier}", "provider": provider,
            "media_id": identifier, "media_type": kind, "source": source}


def _candidate(fragment, sources):
    # Choose one coherent metadata snapshot. Never union conflicting casts or
    # genres across sources. Most recent evidence wins; ties are deterministic.
    chosen = sorted(sources, key=lambda row: (-_time(row["observed_at"] or row["last_seen"]).timestamp(),
                                              row["source_id"], row["source_item_id"]))[0]
    seasons = sorted({source["season_hint"] for source in sources if source["season_hint"] is not None})
    verified_target = len(seasons) == 1 and seasons[0] > 0 and any(
        s["source_id"] == "tmdb_personal_tv" and s["source_item_id"] == fragment["media_id"]
        and s["season_hint"] == seasons[0] for s in sources)
    conflict = any(any(source["metadata"][dimension] != chosen["metadata"][dimension] for dimension in DIMENSIONS)
                   for source in sources)
    return {**{key: fragment[key] for key in ("entity_key", "provider", "media_id", "media_type")},
            "title": chosen["title"], "year": chosen["year"], "release_date": chosen["release_date"],
            "metadata": chosen["metadata"], "metadata_source": {"source_id": chosen["source_id"], "source_item_id": chosen["source_item_id"]},
            "metadata_conflict": conflict, "provenance_url": chosen["provenance_url"],
            "season_hints": seasons, "season_unknown": fragment["media_type"] == "tv" and not verified_target
            and any(source["season_hint"] is None for source in sources),
            "first_seen": min(source["first_seen"] for source in sources),
            "last_seen": max(source["last_seen"] for source in sources),
            "sources": sorted(sources, key=lambda row: (row["source_id"], row["source_item_id"]))}


def merge_pool(existing, items, *, as_of, max_candidates=500, ttl_days=30):
    """Return (new_pool, aggregate_stats), without mutating inputs.

    TTL applies to last-seen source evidence, not user preferences. At capacity,
    newer candidates win, with entity_key as a stable tie-break. Source histories
    have a separate fixed cap. Invalid identities are discarded, never guessed.
    """
    now = _now(as_of)
    if (not isinstance(existing, dict) or len(existing) > MAX_CANDIDATES
            or isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or not 1 <= max_candidates <= MAX_CANDIDATES
            or not _finite(ttl_days) or not 0 < ttl_days <= 365):
        raise CandidateError("Invalid candidate capacity, stored pool, or retention limit")
    stats = Counter({key: 0 for key in ("items_seen", "items_rejected", "invalid_existing", "sources_added",
                                      "sources_updated", "candidates_added", "expired_sources", "expired_candidates",
                                      "evicted_sources", "evicted_candidates")})
    pool = {}

    def insert(fragment, *, is_new):
        key, source = fragment["entity_key"], fragment["source"]
        if (now - _time(source["last_seen"])).total_seconds() >= ttl_days * 86400:
            stats["expired_sources"] += 1
            return
        previous = pool.get(key)
        sources = {(row["source_id"], row["source_item_id"]): row for row in previous["sources"]} if previous else {}
        source_key = source["source_id"], source["source_item_id"]
        older = sources.get(source_key)
        if older:
            first, last = min(older["first_seen"], source["first_seen"]), max(older["last_seen"], source["last_seen"])
            # A replay of an older batch must not overwrite newer metadata.
            source = dict(source if source["last_seen"] >= older["last_seen"] else older)
            source.update(first_seen=first, last_seen=last)
        sources[source_key] = source
        ordered = sorted(sources.values(), key=lambda row: (-_time(row["last_seen"]).timestamp(), row["source_id"], row["source_item_id"]))
        stats["evicted_sources"] += max(0, len(ordered) - MAX_SOURCES)
        pool[key] = _candidate(fragment, ordered[:MAX_SOURCES])
        if is_new:
            stats["sources_updated" if older else "sources_added"] += 1
            stats["candidates_added"] += int(previous is None)
        if len(pool) > max_candidates:
            keep = sorted(pool, key=lambda candidate: (-_time(pool[candidate]["last_seen"]).timestamp(), candidate))[:max_candidates]
            for victim in set(pool) - set(keep):
                del pool[victim]
                stats["evicted_candidates"] += 1

    for key, candidate in existing.items():
        if (not isinstance(candidate, dict) or not isinstance(candidate.get("provider"), str)
                or not isinstance(candidate.get("sources"), list) or len(candidate["sources"]) > MAX_SOURCES):
            stats["invalid_existing"] += 1
            continue
        expired_before, had_live_source = stats["expired_sources"], False
        for source in candidate["sources"]:
            if not isinstance(source, dict):
                stats["invalid_existing"] += 1
                continue
            item = {**source, "media_type": candidate.get("media_type"),
                    "external_ids": {candidate.get("provider", ""): candidate.get("media_id")}}
            fragment = normalize_item(item, as_of=now)
            first, last = _time(source.get("first_seen")), _time(source.get("last_seen"))
            if not fragment or fragment["entity_key"] != key or not first or not last or first > last or last > now:
                stats["invalid_existing"] += 1
                continue
            fragment["source"].update(first_seen=first.isoformat(), last_seen=last.isoformat())
            had_live_source |= (now - last).total_seconds() < ttl_days * 86400
            insert(fragment, is_new=False)
        if not had_live_source and stats["expired_sources"] > expired_before:
            stats["expired_candidates"] += 1
    if isinstance(items, (str, bytes, dict)) or items is None:
        raise CandidateError("Candidate batch must contain source records")
    try:
        iterator = iter(items)
    except TypeError:
        raise CandidateError("Candidate batch must contain source records") from None
    for index, item in enumerate(iterator):
        if index >= MAX_ITEMS:
            raise CandidateError("Candidate batch exceeds its fixed limit")
        stats["items_seen"] += 1
        fragment = normalize_item(item, as_of=now)
        if fragment is None:
            stats["items_rejected"] += 1
        else:
            insert(fragment, is_new=True)
    return pool, dict(stats)


def _baseline_index(baseline):
    dimensions = baseline.get("dimensions") if isinstance(baseline, dict) else None
    dimensions = dimensions if isinstance(dimensions, dict) else {}
    index = {}
    for name in DIMENSIONS:
        entries = dimensions.get(name, {}).get("features", []) if isinstance(dimensions.get(name), dict) else []
        entries = entries if isinstance(entries, list) else []
        index[name] = {row["label"]: float(row["support"]) for row in entries if isinstance(row, dict)
                       and isinstance(row.get("label"), str) and _finite(row.get("support")) and 0 <= row["support"] <= 100_000}
    return index


def _taste(metadata, index):
    details, preferences, supports = [], [], []
    for name in DIMENSIONS:
        labels = metadata[name]
        if not labels:
            continue
        lookup = index[name]
        matched = [label for label in labels if lookup.get(label, 0) > 0]
        support = sum(lookup.get(label, 0) for label in labels)
        preference = sum(lookup.get(label, 0) / (3 + lookup.get(label, 0)) for label in labels) / len(labels)
        preferences.append(preference)
        supports.append(support)
        details.append({"dimension": name, "labels": labels, "matched_labels": matched,
                        "support": support, "preference": preference})
    taste = 0.5 + 0.5 * sum(preferences) / len(preferences) if preferences else 0.5
    related_support = sum(supports) / len(supports) if supports else 0.0
    confidence = min(related_support / 5, len(preferences) / len(DIMENSIONS), 0.6) if preferences else 0.0
    return taste, confidence, details, related_support


def rank_candidates(pool, baseline=None, *, as_of):
    """Return explained shadow rankings. Never infer owned/subscribed status."""
    now = _now(as_of)
    # Revalidate persisted shapes and TTL, including fixed public URLs.
    clean, _ = merge_pool(pool, [], as_of=now)
    baseline_index = _baseline_index(baseline)
    profile_method = baseline.get("method") if isinstance(baseline, dict) else None
    profile_time = _time(profile_method.get("as_of")) if isinstance(profile_method, dict) else None
    ranked = []
    for candidate in clean.values():
        sources = candidate["sources"]
        missing = ["library_unknown", "subscription_unknown"]
        reasons = ["仅作影子排序，尚未检查已拥有媒体和现有订阅"]
        taste, confidence, dimensions, related_support = _taste(candidate["metadata"], baseline_index)
        if baseline is None:
            missing.append("baseline_unavailable")
            reasons.append("没有可用个人画像，口味取中性值")
        if not dimensions:
            missing.append("candidate_metadata_missing")
            reasons.append("候选缺少可用特征，未推断个人偏好")
        elif not related_support:
            missing.append("profile_support_missing")
            reasons.append("画像没有此候选相关特征的有效证据")
        else:
            for detail in dimensions:
                if detail["matched_labels"]:
                    reasons.append(f"画像有相关{DIMENSION_LABELS[detail['dimension']]}证据：" + "、".join(detail["matched_labels"][:3]))
        if candidate["metadata_conflict"]:
            missing.append("metadata_conflict")
            reasons.append("来源元数据不一致，采用一份完整快照，未混合演员或题材")
        groups = defaultdict(list)
        for source in sources:
            groups[(source["source_group"] is not None, source["source_group"])].append(source)
        group_details = []
        for (known, group), entries in sorted(groups.items(), key=lambda pair: (not pair[0][0], pair[0][1] or "")):
            signals = [(row["list_size"] - row["rank"] + 1) / row["list_size"] if row["rank"] is not None else 0.5 for row in entries]
            group_details.append({"source_group": group, "known": known, "signal": max(signals),
                                  "source_ids": sorted({row["source_id"] for row in entries})})
        source_base = sum(group["signal"] for group in group_details) / len(group_details)
        consensus = min(0.10, 0.03 * max(sum(group["known"] for group in group_details) - 1, 0))
        if any(source["rank"] is None for source in sources):
            missing.append("rank_missing")
        if any(source["source_group"] is None for source in sources):
            missing.append("source_group_unknown")
        times = [_time(source["observed_at"]) for source in sources if source["observed_at"]]
        freshness = max(0.0, 1 - (now - max(times)).total_seconds() / (30 * 86400)) if times else 0.5
        if not times:
            missing.append("evidence_time_unknown")
        intent = "explicit_want" if any(source["intent_kind"] == "explicit_want" for source in sources) else "discovery"
        if intent == "explicit_want":
            reasons.append("明确想看优先展示；它不作为喜欢或画像训练证据")
        state = "SHADOW"
        if candidate["media_type"] == "tv":
            if candidate["season_unknown"] or not candidate["season_hints"]:
                state = "HOLD"
                missing.append("tv_season_unknown")
                reasons.append("剧集季范围尚未明确，未默认第一季或全剧")
            if len(candidate["season_hints"]) > 1:
                state = "HOLD"
                missing.append("tv_season_ambiguous")
                reasons.append("来源季范围不唯一，保留各自线索")
            if 0 in candidate["season_hints"]:
                state = "HOLD"
                missing.append("special_season_unconfirmed")
                reasons.append("特别篇季号为 0，后续需单独确认范围")
        contributions = {"taste": 0.65 * taste, "source": 0.25 * source_base,
                         "freshness": 0.10 * freshness, "consensus": consensus,
                         "dimensions": dimensions, "groups": group_details}
        ratings = [{**rating, "source_id": source["source_id"], "source_item_id": source["source_item_id"]}
                   for source in sources for rating in source["metadata"]["ratings"]]
        personal = next((s for s in sources if s["source_id"] == f"tmdb_personal_{candidate['media_type']}"
                         and s["source_item_id"] == candidate["media_id"]), None)
        personal_match = None
        if personal and baseline is not None and candidate["provider"] == "themoviedb":
            own_taste, own_confidence, own_dimensions, _ = _taste(personal["metadata"], baseline_index)
            personal_match = {"source_id": personal["source_id"], "source_item_id": personal["source_item_id"],
                              "taste_match": own_taste, "confidence": own_confidence, "dimensions": own_dimensions}
        ranked.append({**candidate, "intent_kind": intent, "state": state, "can_execute": False,
                       "personal_match": personal_match,
                       "algorithm_version": ALGORITHM_VERSION, "evaluated_at": now.isoformat(),
                       "profile_as_of": profile_time.isoformat() if profile_time else None,
                       "library_status": "unknown", "subscription_status": "unknown",
                       "taste_match": taste, "confidence": confidence, "source_base": source_base,
                       "freshness": freshness, "consensus_bonus": consensus,
                       "fusion_rank": min(1.0, sum(contributions[key] for key in ("taste", "source", "freshness", "consensus"))),
                       "ratings": ratings, "contributions": contributions, "reasons": reasons, "missing": missing})
    return sorted(ranked, key=lambda row: (row["intent_kind"] != "explicit_want", -row["taste_match"],
                                           -row["fusion_rank"], row["entity_key"]))
