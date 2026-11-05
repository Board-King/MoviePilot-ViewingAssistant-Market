"""Identify the ordinary season last confirmed on air by TMDB."""
from datetime import date, datetime, timezone
import re


def _date(value):
    if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    return None


def latest_aired_season(detail, media_id, *, today=None):
    """Return a season only when the series detail and its last aired episode agree."""
    if (not isinstance(detail, dict) or type(detail.get("id")) is not int or detail["id"] <= 0
            or str(detail["id"]) != media_id):
        return None
    last, seasons = detail.get("last_episode_to_air"), detail.get("seasons")
    if (not isinstance(last, dict) or type(last.get("show_id")) is not int or last["show_id"] != detail["id"]
            or not isinstance(seasons, list) or not 1 <= len(seasons) <= 256):
        return None
    season, episode = last.get("season_number"), last.get("episode_number")
    aired = _date(last.get("air_date"))
    as_of = today if today is not None else datetime.now(timezone.utc).date()
    if (type(season) is not int or not 1 <= season <= 99 or type(episode) is not int or episode < 1
            or type(as_of) is not date or aired is None or aired > as_of):
        return None
    matched = False
    for entry in seasons:
        if not isinstance(entry, dict) or type(entry.get("season_number")) is not int:
            return None
        number = entry["season_number"]
        start = _date(entry.get("air_date"))
        if number == season:
            if (matched or start is None or start > aired or type(entry.get("episode_count")) is not int
                    or entry["episode_count"] < episode):
                return None
            matched = True
        elif number > season and start is not None and start <= as_of:
            # Conflicting metadata during a season transition: wait for the next refresh.
            return None
    return season if matched else None
