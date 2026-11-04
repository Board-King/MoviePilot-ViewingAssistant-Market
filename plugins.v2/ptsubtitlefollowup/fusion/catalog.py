"""Explicit discovery contracts and credential-minimal client routing.

Adding a source never enables it. Only registered clients and fixed endpoints
can be used; neither plugin configuration nor source content supplies code/URLs.
"""
from __future__ import annotations

from collections.abc import Mapping

from .source_registry import SOURCE_SPECS
from .sources import SourceReadError, TmdbSourceClient
from .upcoming import UpcomingSourceClient
from .shares import FollowedSharesSourceClient


class CatalogSourceClient:
    """Short-lived dispatcher; RSS receives only the explicit proxy mapping."""

    def __init__(self, config, *, stop_callback=None):
        if not isinstance(config, Mapping):
            raise SourceReadError("INVALID_CONFIG")
        self._config = {"api_key": config.get("api_key"), "proxies": config.get("proxies")}
        self._follow_config = {key: config[key] for key in ("context_valid", "sharing_enabled", "followed_uids")
                               if key in config}
        self._stop_callback, self._closed, self._client = stop_callback, False, None

    def _check(self):
        if self._closed:
            raise SourceReadError("CLOSED")
        try:
            cancelled = self._stop_callback is not None and self._stop_callback()
        except Exception:
            cancelled = True
        if cancelled:
            self.close()
            raise SourceReadError("CANCELLED")

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        self._closed = True
        self._config.clear()
        self._follow_config.clear()
        if self._client is not None:
            try:
                self._client.close()
            finally:
                self._client = None

    def collect(self, source_id, cursor=1, limit=20):
        self._check()
        spec = SOURCE_SPECS.get(source_id) if isinstance(source_id, str) else None
        if spec is None:
            raise SourceReadError("INVALID_SOURCE")
        if spec["transport"] == "tmdb":
            factory = TmdbSourceClient
            config = {"api_key": self._config["api_key"], "proxies": self._config["proxies"]}
        elif spec["transport"] == "upcoming_rss":
            factory = UpcomingSourceClient
            config = {"proxies": self._config["proxies"]}
        elif spec["transport"] == "mp_shares":
            factory = FollowedSharesSourceClient
            config = {"proxies": self._config["proxies"], **self._follow_config}
        else:
            raise SourceReadError("INVALID_SOURCE")
        try:
            with factory(config, stop_callback=self._stop_callback) as client:
                self._client = client
                result = client.collect(source_id, cursor=cursor, limit=limit)
                self._check()
                return result
        finally:
            self._client = None
