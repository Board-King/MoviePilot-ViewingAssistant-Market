"""Offline discovery through catalog scheduling, HTTP parameters and ranking."""
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from test_fusion_emby import REQUESTS, Session
from test_execution import Host, MemoryStore

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins.v2" / "ptsubtitlefollowup"))
from fusion.candidates import merge_pool, rank_candidates
with patch.dict(sys.modules, {"requests": REQUESTS}):
    from fusion.discovery import CandidateService, normalize
    from fusion import sources
from fusion.personal_discovery import profile_genres
from fusion.execution import ExecutionService, SPACE_FLOOR
TmdbSourceClient = sources.TmdbSourceClient


class PersonalDiscoveryTests(unittest.TestCase):
    def setUp(self):
        session = patch.object(sources.requests, "Session", Session, create=True)
        session.start()
        self.addCleanup(session.stop)
        self.config = {"fusion_discovery_enabled": True, "fusion_discovery_pages": 1,
                       "fusion_discovery_sources": ["tmdb_personal_movie", "tmdb_personal_tv"],
                       "fusion_enabled": True, "fusion_user_id": "a" * 32,
                       "fusion_server_ref": "Fiction", "fusion_library_mode": "all_visible"}
        self.profile = {"binding": normalize(self.config)["profile_binding"], "enabled": True, "stale": False,
                        "baseline": {"dimensions": {"genres": {"features": [{"label": "Drama", "support": 5},
                                                                           {"label": "Fiction", "support": 0}]}}}}
        self.calls = []
        self.service = CandidateService(lambda _key: None, lambda *_: None,
                                        lambda: {"api_key": "fixture"}, lambda: self.profile)
        self.service.configure(self.config)

    def upstream(self, path, query, **options):
        self.calls.append((path, query))
        if path.startswith("/3/genre/"):
            return {"genres": [{"id": 18, "name": "剧情" if query["language"] == "zh-CN" else "Drama"}]}
        if path.startswith("/3/discover/"):
            movie = path.endswith("/movie")
            return {"page": 1, "total_pages": 1, "results": [
                {"id": n, "title" if movie else "name": f"Fixture {n}", "genre_ids": [18],
                 "release_date" if movie else "first_air_date": "2020-01-01"} for n in range(1, 12)]}
        identifier = int(path.split("/")[-1])
        return {"id": identifier, "last_episode_to_air": {"show_id": identifier, "season_number": 2,
                "episode_number": 3, "air_date": "2020-01-10"},
                "seasons": [{"season_number": 2, "episode_count": 8, "air_date": "2020-01-01"}]}

    def test_scheduler_discovers_from_real_genre_ids_and_season_proof_is_bounded(self):
        with patch.object(TmdbSourceClient, "_read_json", side_effect=self.upstream):
            self.service.tick()
        queries = [query for path, query in self.calls if path.startswith("/3/discover/")]
        self.assertEqual([q["with_genres"] for q in queries], ["18", "18"])
        self.assertIn("primary_release_date.lte", queries[0])
        self.assertIn("first_air_date.lte", queries[1])
        self.assertNotIn("Drama", str(self.calls))
        state = self.service.status()
        self.assertEqual(state["candidate_count"], 22)
        self.assertEqual(sum(x["season_hints"] == [2] for x in state["candidates"]), 5)
        for row in state["candidates"]:
            self.assertEqual(row["personal_match"]["dimensions"][0]["matched_labels"], ["Drama"])
            self.assertEqual(row["personal_match"]["source_item_id"], row["media_id"])
        self.assertEqual([s["status"] for s in state["sources"]], ["ready", "ready"])

    def paged_catalog(self, budget, feeds=None):
        self.config.update(fusion_discovery_pages=budget, fusion_discovery_interval_hours=168,
                           fusion_discovery_sources=feeds or ["tmdb_personal_movie"])
        self.now = datetime.now(timezone.utc).timestamp()
        self.saved, self.requested, self.upstream_pages = {}, [], 6
        def create():
            service = CandidateService(self.saved.get, self.saved.__setitem__, lambda: {"api_key": "fixture"},
                                       lambda: self.profile, clock=lambda: self.now)
            service.configure(self.config)
            return service
        self.service = create()
        def upstream(path, query, **options):
            if not path.startswith("/3/discover/"):
                return self.upstream(path, query, **options)
            page = query["page"]
            personal = "with_genres" in query
            self.requested.append((personal, page))
            return {"page": page, "total_pages": self.upstream_pages, "results": [
                {"id": page, "title": f"Page {page}", "genre_ids": [18], "release_date": "2020-01-01"}]
                if page <= self.upstream_pages else []}
        requests = patch.object(TmdbSourceClient, "_read_json", side_effect=upstream)
        requests.start()
        self.addCleanup(requests.stop)
        return create

    def collect_next(self):
        self.service.tick()
        state = self.service.status()["sources"][0]
        self.now = state["next_due"]
        return state

    def test_personal_probe_visits_deeper_pages_without_increasing_cycle_budget(self):
        restart = self.paged_catalog(3)
        for index in range(12):
            state = self.collect_next()
            self.assertEqual(state["status"], "ready" if index % 3 == 2 else "queued")
            if index == 3:
                self.service = restart()
        self.assertEqual([page for _, page in self.requested], [1, 2, 3, 1, 4, 5, 1, 6, 2, 1, 3, 4])
        self.assertEqual(self.service.status()["candidate_count"], 6)

    def test_single_page_alternates_front_and_probe_and_recovers_after_catalog_shrinks(self):
        self.paged_catalog(1)
        for _ in range(5):
            state = self.collect_next()
            self.assertEqual(state["next_due"] - state["last_success"], 12 * 3600)
        self.upstream_pages = 1
        state = self.collect_next()  # The previously valid page four now returns no items.
        self.assertEqual((state["status"], state["cursor"]), ("ready", 1))
        self.collect_next()
        self.upstream_pages = 2
        self.collect_next()
        self.collect_next()
        self.assertEqual([p for _, p in self.requested], [1, 2, 1, 3, 1, 4, 1, 1, 2])

    def test_saved_personal_schedule_recovers_before_evidence_expires_and_legacy_keeps_interval(self):
        restart = self.paged_catalog(1, ["tmdb_personal_movie", "tmdb_popular_movie"])
        self.service.tick()
        first_success = self.now
        persisted = next(iter(self.saved.values()))["sources"]
        for state in persisted.values():
            state["next_due"] = first_success + 168 * 3600
        self.now += 26 * 3600
        self.service = restart()
        personal, popular = self.service.status()["sources"]
        self.assertTrue(personal["stale"])
        self.assertFalse(popular["stale"])
        self.assertEqual((personal["next_due"], popular["next_due"]),
                         (first_success + 12 * 3600, first_success + 168 * 3600))
        self.service.tick()
        self.assertEqual(self.requested, [(True, 1), (False, 1), (True, 2)])
        personal, popular = self.service.status()["sources"]
        self.assertFalse(personal["stale"])
        self.assertEqual(popular["last_success"], first_success)

    def test_stale_or_changed_binding_never_requests_personal_discovery(self):
        for change in ({"stale": True}, {"enabled": False}, {"binding": "different"}, {"baseline": None}):
            with self.subTest(change=change):
                self.assertEqual(profile_genres({**self.profile, **change}, self.profile["binding"]), [])
        self.profile["stale"] = True
        with patch.object(TmdbSourceClient, "_read_json", side_effect=AssertionError) as remote:
            self.service.tick()
        remote.assert_not_called()
        self.assertTrue(all(s["error_code"] == "PROFILE_GENRES_REQUIRED" for s in self.service.status()["sources"]))

    def test_unmapped_genres_do_not_fall_back_to_popularity(self):
        self.profile["baseline"]["dimensions"]["genres"]["features"][0]["label"] = "Imaginary genre"
        with patch.object(TmdbSourceClient, "_read_json", side_effect=self.upstream):
            self.service.tick()
        self.assertFalse(any(path.startswith("/3/discover") for path, _ in self.calls))
        self.assertEqual(self.service.status()["candidate_count"], 0)

    def test_authorized_scheduler_turns_personal_discovery_into_three_movies_and_one_latest_season(self):
        host = Host(self)
        with patch.object(TmdbSourceClient, "_read_json", side_effect=self.upstream):
            self.service.tick()
        config = {**self.config, "fusion_execution_enabled": True, "fusion_execution_owner": "fixture",
                  "fusion_execution_preset": "personal", "fusion_execution_sources": self.config["fusion_discovery_sources"],
                  "fusion_execution_legacy_isolated": True, "fusion_execution_storage_verified": True}
        def space():
            return {"status": "ready", "fingerprint": "a" * 64, "locations_checked": 1,
                    "minimum_free_bytes": SPACE_FLOOR, "available_bytes": 2 * SPACE_FLOOR}
        execution = ExecutionService(MemoryStore(), self.service.status, lambda: host, current_config=lambda: config,
                                     storage_check=space, tv_storage_check=space)
        execution.configure(config)
        execution.tick()
        self.assertEqual(host.dispatched, [])
        self.assertTrue(execution.authorize(execution.status()["authorization_digest"])["success"])
        execution.tick()
        execution.tick()
        status = execution.status()
        self.assertEqual((status["used_movies_today"], status["used_tv_today"], len(host.dispatched)), (3, 1, 4))
        self.assertEqual([r["season"] for r in status["recent"] if r["media_type"] == "tv"], [2])
        self.assertTrue(all("Drama" in row["match_reason"] for row in status["recent"]))

    def test_missing_season_in_an_unrelated_list_does_not_override_verified_season(self):
        now = datetime.now(timezone.utc)
        common = {"source_item_id": "42", "external_ids": {"themoviedb": "42"}, "media_type": "tv",
                  "intent_kind": "discovery", "source_group": "tmdb_trending", "observed_at": now.isoformat()}
        own = {**common, "source_id": "tmdb_personal_tv", "season_hint": 3}
        popular = {**common, "source_id": "tmdb_popular_tv"}
        row = rank_candidates(merge_pool({}, [own, popular], as_of=now)[0], as_of=now)[0]
        self.assertEqual((row["season_hints"], row["season_unknown"], row["state"]), ([3], False, "SHADOW"))
        conflict = {**popular, "season_hint": 2}
        row = rank_candidates(merge_pool({}, [own, conflict], as_of=now)[0], as_of=now)[0]
        self.assertEqual(row["state"], "HOLD")

    def test_overlapping_public_list_cannot_replace_personal_scoring_evidence(self):
        now = datetime.now(timezone.utc)
        raw = {"source_item_id": "42", "external_ids": {"themoviedb": "42"}, "media_type": "movie",
               "intent_kind": "discovery", "source_group": "tmdb_trending"}
        personal = {**raw, "source_id": "tmdb_personal_movie", "observed_at": now.date().isoformat() + "T00:00:00Z",
                    "metadata": {"genres": ["Drama"]}}
        public = {**raw, "source_id": "tmdb_popular_movie", "observed_at": now.isoformat(),
                  "metadata": {"genres": ["Adventure"]}}
        pool, _ = merge_pool({}, [personal, public], as_of=now)
        row = rank_candidates(pool, self.profile["baseline"], as_of=now)[0]
        self.assertEqual(row["metadata"]["genres"], ["Adventure"])
        self.assertEqual(row["personal_match"]["dimensions"][0]["matched_labels"], ["Drama"])
        self.assertEqual(row["consensus_bonus"], 0)
