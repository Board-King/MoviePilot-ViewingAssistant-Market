"""Offline contracts for the narrow V2 TMDB-movie subscription adapter."""
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1] / "plugins.v2" / "ptsubtitlefollowup" / "fusion"
spec = importlib.util.spec_from_file_location("offline_subscription_fusion.subscription_host",
                                             ROOT / "subscription_host.py")
host = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host)

MOVIE = {"provider": "themoviedb", "media_id": "123", "media_type": "movie", "season": None,
         "episode_group": None, "title": "Synthetic", "year": 2023}
SERVER = {"enabled": True, "type": "emby", "config": {"host": "https://library.invalid", "apikey": "fixture-only"}}


def row(**changes):
    return {"id": 12, "username": "FixtureOwner", "type": "电影", "media_source": "themoviedb",
            "media_id": "123", "mediaid": None, "tmdbid": 123, "season": None,
            "episode_group": None, "best_version": 0, **changes}


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
