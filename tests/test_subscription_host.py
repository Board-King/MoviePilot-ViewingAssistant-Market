"""Offline contracts for the V2 TMDB movie and TV season subscription adapter."""
from datetime import date
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[1] / "plugins.v2" / "ptsubtitlefollowup" / "fusion"
package = ModuleType("offline_subscription_fusion")
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package
spec = importlib.util.spec_from_file_location("offline_subscription_fusion.subscription_host",
                                             ROOT / "subscription_host.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)
from offline_subscription_fusion.series_subscription import latest_aired_season

MOVIE = {"provider": "themoviedb", "media_id": "123", "media_type": "movie", "season": None,
         "episode_group": None, "title": "Synthetic", "year": 2023}
TV = {**MOVIE, "media_type": "tv", "season": 2}
SERVER = {"enabled": True, "type": "emby", "config": {"host": "https://library.invalid", "apikey": "fixture-only"}}


def row(**changes):
    return {"id": 12, "username": "FixtureOwner", "type": "电影", "media_source": "themoviedb",
            "media_id": "123", "mediaid": None, "tmdbid": 123, "season": None,
            "episode_group": None, "best_version": 0, **changes}


def detail():
    return {"id": 123, "last_episode_to_air": {"show_id": 123, "episode_number": 3, "season_number": 2,
                                               "air_date": "2025-01-12"},
            "seasons": [{"season_number": 1, "episode_count": 6, "air_date": "2024-02-02"},
                        {"season_number": 2, "episode_count": 8, "air_date": "2025-01-02"}]}


class FakeNative:
    def __init__(self):
        self.current, self.history = [], []
        self.calls = 0
        self.owner_key = "10"

    def owner_context(self, owner):
        if owner != "FixtureOwner":
            raise ValueError
        return self.owner_key, "db-scope"

    def subscriptions(self, history=False):
        return self.history if history else self.current

    def add(self, identity, owner, may_dispatch):
        self.calls += 1
        self.current.append(row())
        return 12, "新增订阅成功"


class HostTests(unittest.TestCase):
    def setUp(self):
        self.native = FakeNative()
        self.servers = [SERVER]
        self.library = "absent"
        self.adapter = host.HostSubscriptionAdapter(lambda: self.servers, native_factory=lambda: self.native,
                                                     library_probe=lambda *args: self.library)

    def test_exact_owner_history_and_legacy_identities(self):
        lookup = lambda: self.adapter.lookup_subscription(MOVIE, "FixtureOwner")
        self.assertEqual(lookup(), {"status": "absent", "sid": None})
        for record in (row(), row(media_source=None, media_id=None),
                       row(media_source=None, media_id=None, tmdbid=None, mediaid="tmdb:123")):
            self.native.current = [record]
            self.assertEqual(lookup(), {"status": "existing", "sid": 12})
        for record in (row(username="fixtureowner"), row(type="电视剧", season=4), row(season=0),
                       row(episode_group="special"), row(best_version=1),
                       row(media_source="douban", media_id="77"), row(tmdbid=321)):
            self.native.current = [record]
            self.assertEqual(lookup()["status"], "conflict")
        self.native.current = [row(), row(id=13)]
        self.assertEqual(lookup()["status"], "conflict")
        self.native.current, self.native.history = [], [row()]
        self.assertEqual(lookup(), {"status": "conflict", "sid": None})
        self.assertEqual(self.adapter.lookup_subscription(MOVIE, "fixtureowner")["status"], "unknown")

    def test_incomplete_mapping_or_truncated_inventory_blocks_absence(self):
        for rows in ([row(tmdbid=None, media_source="douban", media_id="9")],
                     [row(media_source=None, media_id=None, tmdbid=None)],
                     [row(media_id="111", tmdbid=222)], [row()] * (host.MAX_SUBSCRIPTIONS + 1)):
            self.native.current = rows
            self.assertEqual(self.adapter.lookup_subscription(MOVIE, "FixtureOwner")["status"], "unknown")
            self.assertEqual(self.adapter.add(MOVIE, "FixtureOwner", may_dispatch=lambda: True)["kind"], "not_sent")
        self.assertEqual(self.native.calls, 0)

    def test_tv_requires_matching_last_aired_episode_and_ordinary_season_summary(self):
        as_of = date(2025, 1, 12)
        resolve = lambda item: latest_aired_season(item, "123", today=as_of)
        self.assertEqual(resolve(detail()), 2)
        for override in ({"id": "123"}, {"seasons": []}, {"last_episode_to_air": None}):
            self.assertIsNone(resolve({**detail(), **override}))
        for override in ({"season_number": 0}, {"season_number": True}, {"season_number": 3},
                         {"episode_number": 9}, {"episode_number": 0}, {"show_id": 456},
                         {"air_date": "2025-02-30"}, {"air_date": "2025-01-13"}):
            item = detail()
            item["last_episode_to_air"].update(override)
            self.assertIsNone(resolve(item))
        for record in ({"season_number": 2, "air_date": "2025-01-10", "episode_count": 3},
                       {"season_number": 3, "air_date": "2025-01-10", "episode_count": 6}):
            item = detail()
            item["seasons"].append(record)
            self.assertIsNone(resolve(item))
        item = detail()
        item["seasons"].append({"season_number": 3, "air_date": "2025-02-01", "episode_count": 10})
        self.assertEqual(resolve(item), 2)

    def test_tv_lookup_distinguishes_seasons_namespaces_and_blocks_uncertain_scope(self):
        lookup = lambda: self.adapter.lookup_subscription(TV, "FixtureOwner", indexed_only=True)["status"]
        tv = row(type="电视剧", season=2)
        self.native.current, self.native.history = [row(), {**tv, "season": 1}], [{**tv, "season": 4}]
        self.assertEqual(lookup(), "absent")
        self.native.current.append(tv)
        self.assertEqual(lookup(), "existing")
        self.native.current = []
        for changes in ({}, {"season": None}, {"episode_group": "alternate"}, {"username": "Other"}):
            self.native.history = [{**tv, **changes}]
            self.assertEqual(lookup(), "conflict")
        self.native.history = [{**tv, "media_source": "douban", "media_id": "555", "tmdbid": None}]
        self.assertEqual(lookup(), "unknown")
        self.native.current = []
        for invalid in (None, 0, -1, 100, True, "2"):
            self.assertEqual(self.adapter.add({**TV, "season": invalid}, "FixtureOwner",
                                               may_dispatch=lambda: True)["kind"], "not_sent")
        self.assertEqual(self.native.calls, 0)

    def test_tv_library_needs_target_season_episode_and_complete_absence(self):
        query = {"Limit": 1, "StartIndex": 0, "Fields": "ProviderIds"}
        show = {"Type": "Series", "Id": "a1", "ProviderIds": {"Tmdb": "123"}}
        episode = {"Type": "Episode", "Id": "ep3", "SeriesId": "a1", "ParentIndexNumber": 2, "IndexNumber": 3}
        def probe(*values):
            responses = iter(values)
            def get(path, params):
                self.assertEqual(path, "/Items")
                if params.get("ParentId"):
                    self.assertEqual((params["ParentId"], params["IncludeItemTypes"]), ("a1", "Episode"))
                rows = next(responses)
                return {"Items": rows, "TotalRecordCount": len(rows)}
            return host._emby_season(get, TV, query)
        self.assertEqual(probe([show], [episode]), "present")
        for bad in ({"SeriesId": "b"}, {"ParentIndexNumber": 3}, {"LocationType": "Virtual"},
                    {"IsPlaceHolder": True}, {"IndexNumber": None}):
            self.assertEqual(probe([show], [{**episode, **bad}]), "unknown")
        self.assertEqual(probe([show], [], [{**episode, "ParentIndexNumber": 1}], []), "absent")
        for number in (None, 2, -1):
            self.assertEqual(probe([show], [], [{**episode, "ParentIndexNumber": number}]), "unknown")
        self.assertEqual(probe([], [{"Type": "Series"}]), "unknown")
        self.assertEqual(probe([], []), "absent")
        self.assertEqual(probe([{**show, "ProviderIds": {"Tmdb": "99"}}]), "unknown")

    def test_tv_submit_uses_native_exact_season_and_refuses_stale_metadata(self):
        native = object.__new__(host._MoviePilot)
        native.movie_type, native.tv_type = "Movie", "TV"
        native.chain_type = lambda: SimpleNamespace(obtain_images=lambda **kwargs: None)
        native._detail = lambda kind, media_id: detail() if (kind, media_id) == ("tv", "123") else {}
        def record(receiver, **kwargs):
            self.assertEqual((kwargs["season"], kwargs["tmdbid"], kwargs["mtype"]), (2, 123, "TV"))
            info = SimpleNamespace(type="TV", source="themoviedb", tmdb_id=123, seasons={1:[1], 2:[1, 2]})
            receiver.obtain_images(mediainfo=info)
            info.seasons = {1: [1]}
            with self.assertRaises(host._NotSent):
                receiver.obtain_images(mediainfo=info)
            return 88, "新增订阅成功"
        native.native_add = record
        self.assertEqual(native.add(TV, "FixtureOwner", lambda: True), (88, "新增订阅成功"))
        for value in (None, {}, {**detail(), "id": 333}):
            native._detail = lambda *_, data=value: data
            with self.assertRaises(host._NotSent):
                native.add(TV, "FixtureOwner", lambda: True)
        self.library = "no_known_match"
        self.assertEqual(self.adapter.add(TV, "FixtureOwner", indexed_only=True,
                                          may_dispatch=lambda: True)["kind"], "not_sent")
        self.assertEqual(self.native.calls, 0)

    def test_ranked_compares_only_exact_douban_movie_ids_across_current_and_history(self):
        a = row(tmdbid=None, media_source="douban", media_id="44", doubanid=44, mediaid="douban:44")
        self.native.current = [a]
        self.native.history = [{**a, "media_id": "72", "doubanid": 72, "mediaid": None}]
        requested = []
        def crosswalk(media_id):
            requested.append(media_id)
            return {"imdb": "tt1199123", "douban": "72"}
        self.native.external_ids = crosswalk
        self.assertEqual(self.adapter.lookup_subscription(MOVIE, "FixtureOwner")["status"], "unknown")
        ranked = lambda: self.adapter.lookup_subscription(MOVIE, "FixtureOwner", indexed_only=True)["status"]
        self.assertEqual(ranked(), "conflict")
        self.native.history = []
        self.assertEqual(ranked(), "absent")
        self.assertEqual(requested, ["123"])
        self.native.current = [{**a, "doubanid": "15"}]
        self.assertEqual(ranked(), "unknown")
        self.native.current = [a]
        self.native.external_ids = lambda media_id: {}
        self.adapter._external_ids.cache_clear()
        self.assertEqual(ranked(), "unknown")

    def test_douban_lookup_accepts_only_its_movie_id_shapes(self):
        for value in ("23", 23, "https://api.douban.com/movie/23"):
            self.assertEqual(host._douban_movie_id({"id": value}), "23")
        for value in (None, "movie 23", "https://example.test/movie/23", "https://api.douban.com/tv/23"):
            self.assertIsNone(host._douban_movie_id({"id": value}))

    def test_ineligible_inputs_and_lost_authorization_never_call_native_add(self):
        for changed in ({"provider": "douban"}, {"season": 0}, {"media_type": "tv"},
                        {"episode_group": "x"}, {"media_id": True}, {"title": "bad\ninput"}):
            self.assertEqual(self.adapter.add({**MOVIE, **changed}, "FixtureOwner", may_dispatch=lambda: True)["kind"],
                             "not_sent")
        for guard in (None, lambda: False, lambda: 1, lambda: 1 / 0):
            self.assertEqual(self.adapter.add(MOVIE, "FixtureOwner", may_dispatch=guard)["kind"], "not_sent")
        active = [True]
        def revoked_during_library(*_):
            active[0] = False
            return "absent"
        self.adapter._probe = revoked_during_library
        self.assertEqual(self.adapter.add(MOVIE, "FixtureOwner", may_dispatch=lambda: active[0])["kind"], "not_sent")
        self.assertEqual(self.native.calls, 0)

    def test_all_configured_servers_required_for_absence(self):
        for configured in ([], None, [SERVER, {"enabled": False, "type": "emby"}],
                           [SERVER, {"enabled": True, "type": "jellyfin"}], [SERVER] * 9):
            self.servers = configured
            self.assertEqual(self.adapter.lookup_library(MOVIE), "unknown")
        self.servers = [SERVER, SERVER]
        self.assertEqual(self.adapter.lookup_library(MOVIE), "absent")
        for answers, expected in ((["absent", "present"], "present"), (["unknown", "present"], "present"),
                                  (["absent", "unknown"], "unknown")):
            iterator = iter(answers)
            self.adapter._probe = lambda *_: next(iterator)
            self.assertEqual(self.adapter.lookup_library(MOVIE), expected)

    def test_ranked_library_gaps_never_prove_full_absence(self):
        def gap(identifier, **fields):
            return dict(Id=str(identifier), Type="Movie", ProviderIds={}, **fields)
        first = {"Items": [gap(1, OriginalTitle="ＳＹＮＴＨＥＴＩＣ", ProductionYear=2024),
                           gap(2, Name="Different work")], "TotalRecordCount": 2}
        rows = host._unidentified_movies(None, {}, first)
        self.assertEqual(host._unidentified_match(rows, MOVIE), "unknown")
        self.assertEqual(host._unidentified_match(rows, {**MOVIE, "title": "Different: Work (Extended)"}), "unknown")
        self.assertEqual(host._unidentified_match(rows, {**MOVIE, "title": "Unrelated"}), "no_known_match")
        self.assertEqual(host._unidentified_match([], MOVIE), "absent")
        self.assertEqual(host._unidentified_match([(set(), None)], MOVIE), "unknown")
        self.servers = [SERVER, SERVER]
        for statuses, ranked, expected in ((["absent", "no_known_match"], False, "unknown"),
                                           (["absent", "no_known_match"], True, "no_known_match"),
                                           (["unknown", "no_known_match"], True, "unknown"),
                                           (["no_known_match", "present"], True, "present")):
            answers = iter(statuses)
            self.adapter._probe = lambda *_: next(answers)
            self.assertEqual(self.adapter.lookup_library(MOVIE, indexed_only=ranked), expected)

    def test_unidentified_library_scan_requires_complete_consistent_pages(self):
        items = [{"Type": "Movie", "Id": str(i), "ProviderIds": {}, "Name": "Sample"} for i in range(129)]
        first = {"TotalRecordCount": 129, "Items": items[:128]}
        for tail in ({"TotalRecordCount": 128, "Items": [items[-1]]}, {"TotalRecordCount": 129, "Items": []},
                     {"TotalRecordCount": 129, "Items": [items[0]]},
                     {"TotalRecordCount": 129, "Items": [{**items[-1], "ProviderIds": {"Tmdb": "45"}}]}):
            with self.assertRaises(ValueError):
                host._unidentified_movies(lambda *_: tail, {}, first)
        def last_page(path, params):
            self.assertEqual((path, params["StartIndex"]), ("/Items", 128))
            return {"TotalRecordCount": 129, "Items": [items[-1]]}
        self.assertEqual(len(host._unidentified_movies(last_page, {}, first)), 129)

    def test_add_readback_preexisting_and_unknown_after_commit(self):
        submit = lambda: self.adapter.add(MOVIE, "FixtureOwner", may_dispatch=lambda: True)
        self.assertEqual(submit(), {"kind": "added", "sid": 12})
        self.assertEqual(submit(), {"kind": "existing", "sid": 12})
        self.assertEqual(self.native.calls, 1)
        self.native.current = []
        def failed_return(*_):
            self.native.current.append(row())
            raise TimeoutError("raw-secret")
        self.native.add = failed_return
        self.assertEqual(submit(), {"kind": "unknown", "sid": None})
        self.native.current = []
        self.native.add = lambda *_: (12, "新增订阅成功")
        self.assertEqual(submit(), {"kind": "unknown", "sid": 12})

    def test_no_add_when_scope_changes_during_library_read(self):
        def replace_db(*_):
            self.native.owner_key = "22"
            return "absent"
        self.adapter._probe = replace_db
        self.assertEqual(self.adapter.add(MOVIE, "FixtureOwner", may_dispatch=lambda: True)["kind"], "not_sent")
        self.assertEqual(self.native.calls, 0)
