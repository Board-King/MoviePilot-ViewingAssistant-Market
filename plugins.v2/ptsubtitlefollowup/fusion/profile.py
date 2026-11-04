"""Pure positive-evidence profiles and bounded user-snapshot normalisation.

No I/O, network, MoviePilot imports, subscriptions or inferred metadata.
Scores are ranking signals, never probabilities of liking a work.
"""
from __future__ import annotations

import copy
from collections import Counter, defaultdict
from datetime import datetime, timezone
import math
import re


DIMENSIONS = ("genres", "directors", "actors", "decades", "languages", "keywords")
MAX_RECORDS = 100_000


def _time(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _labels(features):
    result = {name: set() for name in DIMENSIONS}
    if not isinstance(features, dict):
        return result
    for name in DIMENSIONS:
        labels = features.get(name)
        if not isinstance(labels, list):
            continue
        for label in labels:
            if isinstance(label, str) and label.strip() and len(label) <= 256:
                result[name].add(label.strip())
    return result


def build_baseline(evidence, *, as_of, half_life_days=180.0,
                   unknown_age_days=180.0, prior_strength=3.0):
    """Return deterministic aggregates; never mutate or return source records.

    Favorite supersedes played for the same entity. Supplied feature labels for
    duplicate entities are unioned, then each dimension's weight is divided
    equally over its distinct labels. A series favorite also supersedes played
    evidence from its seasons. Remaining series/season q values are scaled
    proportionally so that their sum is at most one *before* time decay.

    Season records require parent_key so the series cap cannot be silently
    bypassed. Conflicting identity records are excluded and counted. Invalid or
    timezone-naive observation dates are unknown, not fabricated recent dates.
    A favorite has unknown age even when observed_at exists: snapshot retrieval
    or LastPlayedDate does not establish when the favorite was set.
    """
    now = _time(as_of)
    if now is None:
        raise ValueError("as_of must be an ISO timestamp with a timezone")
    for name, value, allow_zero in (
        ("half_life_days", half_life_days, False),
        ("unknown_age_days", unknown_age_days, True),
        ("prior_strength", prior_strength, False),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
        if value < 0 or (value == 0 and not allow_zero):
            raise ValueError(f"{name} is outside its allowed range")
    if not isinstance(evidence, list) or len(evidence) > MAX_RECORDS:
        raise ValueError("evidence must be a list of at most 100000 records")

    stats = Counter({name: 0 for name in (
        "input_records", "invalid_records", "unsupported_kind_records", "neutral_records",
        "duplicate_positive_records", "identity_conflict_entities",
        "deduplicated_positive_entities", "suppressed_played_entities",
        "favorite_entities", "played_entities", "missing_feature_entities",
        "contributing_entities", "unknown_time_entities", "future_time_clamped_entities",
        "capped_series_groups",
    )})
    entities, conflicts = {}, set()
    for record in evidence:
        stats["input_records"] += 1
        if not isinstance(record, dict):
            stats["invalid_records"] += 1
            continue
        kind, signal = record.get("kind"), record.get("signal")
        if not isinstance(kind, str) or kind not in {"movie", "season", "series"}:
            stats["unsupported_kind_records"] += 1
            continue
        if not isinstance(signal, str) or signal not in {"favorite", "played"}:
            stats["neutral_records"] += 1
            continue
        key, parent = record.get("entity_key"), record.get("parent_key")
        if (not isinstance(key, str) or not key.strip() or len(key) > 256
                or (kind == "season" and (not isinstance(parent, str) or not parent.strip()
                                           or len(parent) > 256 or parent == key))):
            stats["invalid_records"] += 1
            continue
        parent = parent if kind == "season" else key
        identity = (kind, parent)
        row = entities.get(key)
        if row is not None:
            stats["duplicate_positive_records"] += 1
            if row["identity"] != identity:
                conflicts.add(key)
                continue
        else:
            row = entities[key] = {"identity": identity, "favorite": False,
                                   "played_at": None, "features": _labels({})}
        row["favorite"] |= signal == "favorite"
        if signal == "played":
            observed = _time(record.get("observed_at"))
            if observed is not None and (row["played_at"] is None or observed > row["played_at"]):
                row["played_at"] = observed
        for name, labels in _labels(record.get("features")).items():
            row["features"][name].update(labels)

    for key in conflicts:
        entities.pop(key, None)
    stats["identity_conflict_entities"] = len(conflicts)
    stats["deduplicated_positive_entities"] = len(entities)
    favorite_series = {key for key, row in entities.items()
                       if row["identity"][0] == "series" and row["favorite"]}
    active = []
    for key, row in sorted(entities.items()):
        kind, parent = row["identity"]
        if kind == "season" and not row["favorite"] and parent in favorite_series:
            stats["suppressed_played_entities"] += 1
            continue
        favorite = row["favorite"]
        stats["favorite_entities" if favorite else "played_entities"] += 1
        observed = None if favorite else row["played_at"]
        if observed is None:
            age = float(unknown_age_days)
            stats["unknown_time_entities"] += 1
        else:
            age = (now - observed).total_seconds() / 86400
            if age < 0:
                stats["future_time_clamped_entities"] += 1
                age = 0.0
        active.append({"group": ("movie" if kind == "movie" else "series", parent),
                       "q": 1.0 if favorite else 0.15,
                       "decay": 0.5 ** (age / half_life_days), "features": row["features"]})

    group_q = defaultdict(float)
    for row in active:
        group_q[row["group"]] += row["q"]
    stats["capped_series_groups"] = sum(kind == "series" and total > 1
                                          for (kind, _), total in group_q.items())
    supports = {name: defaultdict(float) for name in DIMENSIONS}
    raw_q = bounded_q = decayed_q = 0.0
    for row in active:
        raw_q += row["q"]
        q = row["q"] / max(1.0, group_q[row["group"]])
        bounded_q += q
        weight = q * row["decay"]
        decayed_q += weight
        has_features = any(row["features"].values())
        stats["contributing_entities" if has_features else "missing_feature_entities"] += 1
        for name, labels in row["features"].items():
            if labels:
                for label in sorted(labels):
                    supports[name][label] += weight / len(labels)

    dimensions = {}
    for name, labels in supports.items():
        entries = []
        for label, support in sorted(labels.items(), key=lambda item: (-item[1], item[0])):
            preference = support / (prior_strength + support)
            entries.append({"label": label, "support": support, "preference": preference,
                            "score": 0.5 + 0.5 * preference})
        dimensions[name] = {"support": sum(labels.values()), "features": entries}
    return {
        "schema_version": 1,
        "method": {
            "name": "positive_snapshot_baseline", "as_of": now.isoformat(),
            "parameters": {"favorite_q": 1.0, "played_q": 0.15,
                           "half_life_days": half_life_days, "unknown_age_days": unknown_age_days,
                           "prior_strength": prior_strength, "series_q_cap": 1.0},
            "parameters_are_engineering_defaults_not_user_presets": True,
            "observation_quality_cap": 0.6,
            "score_interpretation": "Positive-evidence ranking signal, not a liking probability; absent features remain unknown.",
            "notes": ["Favorite age is unknown; played time is only the supplied observation time.",
                      "Only normalised features are used; languages and descriptions are not inferred.",
                      "Episode inputs are skipped; season aggregation belongs upstream.",
                      "No negative preferences are inferred from unwatched or partial items."],
        },
        "statistics": {**dict(stats), "raw_q_total": raw_q, "bounded_q_total": bounded_q,
                       "decayed_q_total": decayed_q},
        "dimensions": dimensions,
    }


class ProfileError(ValueError):
    """Sanitised normalisation failure; never contains a source value."""


def _local_id(value):
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        return value
    return None


def _provider_identity(item, kind):
    providers = item.get("ProviderIds")
    if not isinstance(providers, dict):
        return None
    normalised = defaultdict(set)
    for key, raw in providers.items():
        if not isinstance(key, str) or isinstance(raw, bool) or not isinstance(raw, (str, int)):
            continue
        text = str(raw).strip()
        name = key.casefold()
        if name in {"tmdb", "tvdb"} and re.fullmatch(r"[1-9][0-9]{0,19}", text):
            normalised[name].add(text)
        elif name == "imdb" and re.fullmatch(r"tt[0-9]{1,20}", text):
            normalised[name].add(text)
    choices = (("tmdb", "themoviedb"), ("imdb", "imdb"))
    if kind == "series":
        choices += (("tvdb", "thetvdb"),)
    for source, provider in choices:
        identifiers = normalised[source]
        if len(identifiers) > 1:
            return None
        if identifiers:
            return f"{provider}:{'movie' if kind == 'movie' else 'tv'}:{next(iter(identifiers))}"
    return None


def _feature_label(value):
    if isinstance(value, str) and value.strip() and len(value) <= 128:
        return value.strip()
    return None


def _snapshot_features(item):
    """Keep only explicit metadata, with bounded labels per dimension."""
    result = {name: [] for name in DIMENSIONS}
    genres = item.get("Genres")
    if isinstance(genres, list):
        for value in genres:
            label = _feature_label(value)
            if label and label not in result["genres"]:
                result["genres"].append(label)
                if len(result["genres"]) == 32:
                    break
    people = item.get("People")
    if isinstance(people, list):
        for person in people:
            if not isinstance(person, dict):
                continue
            dimension = {"Actor": "actors", "Director": "directors"}.get(person.get("Type")) if isinstance(person.get("Type"), str) else None
            label = _feature_label(person.get("Name"))
            if dimension and label and len(result[dimension]) < 5 and label not in result[dimension]:
                result[dimension].append(label)
            if len(result["actors"]) == 5 and len(result["directors"]) == 5:
                break
    year = item.get("ProductionYear")
    if isinstance(year, int) and not isinstance(year, bool) and 1800 <= year <= 2200:
        result["decades"] = [f"{year // 10 * 10}s"]
    language = item.get("OriginalLanguage")
    if isinstance(language, str) and re.fullmatch(r"[A-Za-z]{2,3}(?:-[A-Za-z]{2,4})?", language):
        result["languages"] = [language.lower()]
    return result


def _signal(item):
    data = item.get("UserData")
    if not isinstance(data, dict):
        return None, None
    if data.get("IsFavorite") is True:
        return "favorite", None
    if data.get("Played") is True:
        observed = _time(data.get("LastPlayedDate"))
        return "played", observed.isoformat() if observed else None
    return None, None


def _merge_features(first, second):
    limits = {"genres": 32, "directors": 5, "actors": 5, "decades": 1,
              "languages": 1, "keywords": 0}
    return {name: sorted(set(first.get(name, ())) | set(second.get(name, ())))[:limits[name]]
            for name in DIMENSIONS}


class SnapshotAccumulator:
    """Bounded builder for dictionaries already read in one authorised user scope.

    The caller is responsible for user/library filtering and complete pagination.
    Series metadata may arrive before or after its episodes. Only movie, season
    and series-favorite evidence is retained, never episode histories, titles,
    paths or credentials. Emby item IDs are transient lookup keys, not provider
    identities; finish removes them and returns aggregate output only.

    Series.Played is ignored to avoid counting a series together with its seasons.
    Every episode requires an explicit nonnegative ParentIndexNumber (zero is
    valid); its own ProviderIds and guest cast do not define the parent series.
    Exceeding any hard bound poisons the entire snapshot, clears its evidence and
    makes finish fail instead of producing a misleading partial profile.
    """

    def __init__(self, *, as_of, max_entities=10_000, max_series=5_000):
        if _time(as_of) is None:
            raise ProfileError("Snapshot requires a timezone-aware analysis timestamp")
        for value, maximum in ((max_entities, 10_000), (max_series, 5_000)):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ProfileError("Snapshot capacity must be within the supported bounds")
        self._as_of = _time(as_of)
        self._max_entities = max_entities
        self._max_series = max_series
        self._series = {}
        self._records = {}
        self._failed = False
        self._result = None
        self._stats = Counter({key: 0 for key in (
            "movie_records_seen", "episode_records_seen", "series_records_seen",
            "neutral_records", "invalid_records", "unsupported_type_records",
            "missing_identity_records", "invalid_season_records", "missing_parent_records",
            "duplicate_positive_records", "conflicting_series_records", "ignored_series_played_records",
            "missing_series_groups", "positive_movie_entities", "positive_season_entities",
            "positive_series_entities",
        )})

    def _check(self):
        if self._failed:
            raise ProfileError("Snapshot failed; no profile is available")
        if self._result is not None:
            raise ProfileError("Snapshot is already complete")

    def _fail(self, message):
        self._failed = True
        self._series.clear()
        self._records.clear()
        raise ProfileError(message)

    def _remember(self, key, record):
        existing = self._records.get(key)
        if existing is None:
            if len(self._records) >= self._max_entities:
                self._fail("Snapshot entity limit exceeded; no profile was produced")
            self._records[key] = record
            return
        self._stats["duplicate_positive_records"] += 1
        existing["features"] = _merge_features(existing["features"], record["features"])
        if existing["signal"] == "favorite":
            return
        if record["signal"] == "favorite":
            existing.update(signal="favorite", observed_at=None)
        elif record["observed_at"] and (not existing["observed_at"]
                                         or record["observed_at"] > existing["observed_at"]):
            existing["observed_at"] = record["observed_at"]

    def add_series(self, item):
        self._check()
        self._stats["series_records_seen"] += 1
        if not isinstance(item, dict) or item.get("Type") != "Series" or not _local_id(item.get("Id")):
            self._stats["invalid_records"] += 1
            return
        local_id = item["Id"]
        identity = _provider_identity(item, "series")
        if local_id not in self._series:
            if len(self._series) >= self._max_series:
                self._fail("Snapshot series limit exceeded; no profile was produced")
            self._series[local_id] = {"identity": identity, "features": _snapshot_features(item), "conflict": False}
        else:
            previous = self._series[local_id]
            if previous["identity"] and identity and previous["identity"] != identity:
                previous["conflict"] = True
                self._stats["conflicting_series_records"] += 1
            elif identity and not previous["identity"]:
                previous["identity"] = identity
            previous["features"] = _merge_features(previous["features"], _snapshot_features(item))
        if not identity:
            self._stats["missing_identity_records"] += 1
        signal, _ = _signal(item)
        if signal == "played":
            self._stats["ignored_series_played_records"] += 1
        if signal == "favorite":
            self._remember(("series", local_id), {"kind": "series", "local_series": local_id,
                                                "signal": signal, "observed_at": None, "features": _snapshot_features(item)})

    def add_item(self, item):
        self._check()
        if not isinstance(item, dict):
            self._stats["invalid_records"] += 1
            return
        kind = item.get("Type")
        if kind == "Series":
            self.add_series(item)
            return
        if not isinstance(kind, str) or kind not in {"Movie", "Episode"}:
            self._stats["unsupported_type_records"] += 1
            return
        self._stats["movie_records_seen" if kind == "Movie" else "episode_records_seen"] += 1
        signal, observed = _signal(item)
        if not signal:
            self._stats["neutral_records"] += 1
            return
        if kind == "Movie":
            identity = _provider_identity(item, "movie")
            if identity is None:
                self._stats["missing_identity_records"] += 1
                return
            self._remember(("movie", identity), {"entity_key": identity, "kind": "movie", "signal": signal,
                                                "observed_at": observed, "features": _snapshot_features(item)})
            return
        parent = _local_id(item.get("SeriesId"))
        if parent is None:
            self._stats["missing_parent_records"] += 1
            return
        season = item.get("ParentIndexNumber")
        if isinstance(season, bool) or not isinstance(season, int) or not 0 <= season <= 10_000:
            self._stats["invalid_season_records"] += 1
            return
        self._remember(("season", parent, season), {"kind": "season", "local_series": parent, "season": season,
                                                   "signal": signal, "observed_at": observed,
                                                   "features": {name: [] for name in DIMENSIONS}})

    def finish(self):
        if self._failed:
            raise ProfileError("Snapshot failed; no profile is available")
        if self._result is not None:
            return copy.deepcopy(self._result)
        evidence = []
        for record in self._records.values():
            if record["kind"] == "movie":
                evidence.append(record)
                continue
            series = self._series.get(record["local_series"])
            if not series or series["conflict"] or not series["identity"]:
                self._stats["missing_series_groups"] += 1
                continue
            parent = series["identity"]
            row = {"entity_key": parent, "kind": record["kind"], "signal": record["signal"],
                   "observed_at": record["observed_at"], "features": series["features"]}
            if record["kind"] == "season":
                row.update(entity_key=f"{parent}:season:{record['season']}", parent_key=parent)
            evidence.append(row)
        # Several local Emby copies can resolve to the same provider identity.
        # The pure baseline performs final identity deduplication across copies.
        for kind in ("movie", "season", "series"):
            self._stats[f"positive_{kind}_entities"] = len({row["entity_key"] for row in evidence if row["kind"] == kind})
        baseline = build_baseline(evidence, as_of=self._as_of)
        self._result = {"baseline": baseline, "stats": dict(self._stats)}
        self._records.clear()
        self._series.clear()
        return copy.deepcopy(self._result)
