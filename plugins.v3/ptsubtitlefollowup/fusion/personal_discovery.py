"""Turn positive local profile evidence into a bounded TMDB genre query."""
import math
import unicodedata


def profile_genres(profile, binding):
    if (not isinstance(profile, dict) or not binding or profile.get("binding") != binding
            or profile.get("enabled") is not True or profile.get("stale") is not False):
        return []
    baseline = profile.get("baseline") or {}
    entries = baseline.get("dimensions", {}).get("genres", {}).get("features", [])
    usable = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        label, support = entry.get("label"), entry.get("support")
        if (isinstance(label, str) and 0 < len(label.strip()) <= 80 and type(support) in (int, float)
                and 0 < support <= 100000 and math.isfinite(support)):
            usable.append((support, label))
    return list(dict.fromkeys(label for _, label in sorted(usable, key=lambda x: (-x[0], x[1]))))[:24]


def _key(label):
    return unicodedata.normalize("NFKC", label).strip().casefold()


def genre_matches(labels, localized, english):
    # Only IDs in this medium's official catalog are eligible. Prefer the
    # strongest local spelling when the same genre occurs in two languages.
    names = {_key(name): gid for catalog in (localized, english) for gid, name in catalog.items()}
    matches = {}
    for label in labels:
        gid = names.get(_key(label))
        if gid in localized:
            matches.setdefault(gid, label)
    return matches


def rotating_genres(matches, day):
    ids = list(matches)[:6]
    if len(ids) <= 3:
        return ids
    rest = ids[1:]
    offset = day.toordinal() % len(rest)
    return [ids[0], rest[offset], rest[(offset + 1) % len(rest)]]
