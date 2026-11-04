"""Synthetic personal selection and scope tests using the connector's GET-only fake."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from test_fusion_emby import OTHER_USER, REQUESTS, SERVER, USER, Response, Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins.v2/ptsubtitlefollowup"))
with patch.dict(sys.modules, {"requests": REQUESTS}):
    from fusion import emby, tonight
    from fusion.choices import movie_key
from fusion.profile import SnapshotAccumulator


CONFIG = {"host": "http://internal.invalid/emby", "play_host": "https://watch.invalid/films",
          "apikey": "fixture-secret"}
ALL = {"mode": "all_visible", "library_ids": []}
SELECTED = {"mode": "selected_ids", "library_ids": ["my-library"]}
TICKS = tonight.TICKS_PER_MINUTE


def source(duration=90, language="zho"):
    return {"RunTimeTicks": duration * TICKS, "Path": "https://signed.invalid/hidden?signature=secret",
            "MediaStreams": [{"Type": "Subtitle", "Language": language, "IsExternal": True}]}


def movie(i, genres=None, editions=None, played=False):
    return {"Id": str(i), "Name": f"Fixture {i}", "Type": "Movie", "ProductionYear": 2026,
            "Genres": genres or [], "UserData": {"UserId": USER, "Played": played},
            "ProviderIds": {"Tmdb": str(i)}, "MediaSources": [source()] if editions is None else editions,
            "Path": f"/private/{i}.strm"}


def page(items, total=None):
    return Response({"Items": items, "TotalRecordCount": len(items) if total is None else total})


class TonightTests(unittest.TestCase):
    def setUp(self):
        profile = SnapshotAccumulator(as_of="2040-01-01T00:00:00Z")
        profile.add_item({"Id": "fav", "Type": "Movie", "ProviderIds": {"Tmdb": "101"},
                          "Genres": ["adventure"], "UserData": {"IsFavorite": True}})
        self.baseline = profile.finish()["baseline"]

    def request(self, responses, *, config=CONFIG, scope=ALL, user=USER, native=SERVER,
                policy=None, **filters):
        session = Session([Response({"Id": SERVER}),
                           Response({"Id": user, "Policy": policy if policy is not None else {"IsDisabled": False}}),
                           *responses])
        with patch.object(emby.requests, "Session", return_value=session):
            result = tonight.select_tonight(config, server_id=native, user_id=USER, scope=scope,
                                            baseline=self.baseline, **filters)
        return result, session

    def test_scoped_read_matching_dedup_and_exact_safe_detail_link(self):
        rows = [movie(10), movie(20, ["adventure"]), movie(30, played=True), movie(40), movie(50)]
        rows[-1]["ProviderIds"] = {"Tmdb": "20"}
        result, session = self.request([page([{"Id": "my-library"}]), page(rows)], scope=SELECTED)
        self.assertTrue(result["success"])
        self.assertFalse(result["limited"])
        self.assertEqual([r["id"] for r in result["items"]], ["20", "10", "40"])
        top = result["items"][0]
        self.assertGreater(top["score"], 50)
        self.assertEqual(top["reason"], "题材相合 · adventure")
        self.assertIsNone(result["items"][1]["score"])
        self.assertEqual(top["url"], f"https://watch.invalid/films/web/index.html#!/item?id=20&context=home&serverId={SERVER}")
        self.assertEqual(session.calls[-1][0], f"http://internal.invalid/emby/Users/{USER}/Items")
        self.assertEqual(session.calls[-1][1]["params"]["ParentId"], "my-library")
        self.assertEqual(session.calls[-1][1]["params"]["IsPlayed"], "false")
        for secret in ("fixture-secret", "signed.invalid", "/private", "internal.invalid", USER):
            self.assertNotIn(secret, json.dumps(result))
        self.assertTrue(session.closed)

    def test_wrong_binding_invisible_root_wrong_movie_user_and_cancel_fail_without_leaking_results(self):
        for options, calls, reason in [({"native": "different"}, 1, "VERIFY_REQUIRED"),
                                       ({"user": OTHER_USER}, 2, "VERIFY_REQUIRED"),
                                       ({"scope": SELECTED}, 3, "LIBRARY_NOT_VISIBLE"),
                                       ({"policy": {"IsDisabled": False, "EnableMediaPlayback": False}}, 2, "FORBIDDEN"),
                                       ({"stop_callback": lambda: True}, 0, "CANCELLED")]:
            with self.subTest(options=options):
                result, session = self.request([page([{"Id": "not-mine"}])], **options)
                self.assertEqual(result, {"success": False, "reason": reason})
                self.assertEqual(len(session.calls), calls)
        wrong = movie(12)
        wrong["UserData"]["UserId"] = OTHER_USER
        result, _ = self.request([page([movie(11), wrong])])
        self.assertEqual(result, {"success": False, "reason": "VERIFY_REQUIRED"})

    def test_strict_subtitles_and_duration_require_evidence_on_the_same_openable_title(self):
        good = movie(1, editions=[source(89, "zh-Hans"), source(90, "CHI")])
        mixed = movie(2, editions=[source(120), source(80, "eng")])
        conflicting = movie(3, editions=[source(91), source(74)])
        unknown = movie(4, editions=[])
        unknown["Name"] = "中文字幕片名不作为证据"
        audio = movie(5)
        audio["MediaSources"][0]["MediaStreams"] = [{"Type": "Audio", "Language": "zh"}]
        missing_duration = movie(6, editions=[{"MediaStreams": [{"Type": "Subtitle", "Language": "zho"}]}])
        misleading = movie(7, editions=[source(70), source(70, "yue")])
        mismatch = movie(8)
        mismatch["MediaSourceCount"] = 2
        fake = movie(9)
        fake["LocationType"] = "Virtual"
        longer = movie(10)
        longer["MediaSources"][0]["RunTimeTicks"] += 1
        rows = [good, mixed, conflicting, unknown, audio, missing_duration, misleading, mismatch, fake, longer]
        result, _ = self.request([page(rows)], max_minutes=90, chinese_subtitles=True)
        self.assertEqual([x["id"] for x in result["items"]], ["1"])
        self.assertEqual(result["items"][0]["minutes"], 90)
        self.assertIs(result["items"][0]["chinese_subtitles"], True)
        self.assertIn("多版本", result["items"][0]["version"])
        result, _ = self.request([page([unknown])])
        self.assertIsNone(result["items"][0]["minutes"])
        self.assertIsNone(result["items"][0]["chinese_subtitles"])

    def test_forced_chinese_only_does_not_confirm_dialogue_subtitles_for_an_edition(self):
        forced_only = source()
        forced_only["MediaStreams"][0]["IsForced"] = True
        full = source()
        full["MediaStreams"].extend(forced_only["MediaStreams"])
        result, _ = self.request([page([movie(1, editions=[source(), forced_only]), movie(2, editions=[full])])],
                                 chinese_subtitles=True)
        self.assertEqual([item["id"] for item in result["items"]], ["2"])

    def test_limits_partial_pages_and_invalid_options(self):
        rows = [movie(i) for i in range(1, 97)]
        result, session = self.request([page(rows[i:i + 32], 9999) for i in range(0, 96, 32)])
        self.assertTrue(result["limited"])
        self.assertEqual(len(session.calls), 5)
        self.assertEqual([call[1]["params"]["StartIndex"] for call in session.calls[2:]], [0, 32, 64])
        result, _ = self.request([page([movie(1)], 100)])
        self.assertEqual(result, {"success": False, "reason": "UNAVAILABLE"})
        for filters in ({"max_minutes": True}, {"max_minutes": 100.5}, {"max_minutes": 361}, {"chinese_subtitles": "no"}):
            result, session = self.request([], **filters)
            self.assertEqual(result["reason"], "INVALID_FILTER")
            self.assertEqual(session.calls, [])
        result, session = self.request([], config={**CONFIG, "play_host": "https://watch.invalid/?token=123"})
        self.assertEqual(result["reason"], "SETUP_REQUIRED")
        self.assertEqual(session.calls, [])

    def test_remembered_movie_exclusion_covers_duplicate_copies_without_excluding_a_genre(self):
        remembered = {movie_key("themoviedb:movie:1"): {"item_id": "1", "action": "hide", "revision": 1}}
        duplicate = movie(100, ["adventure"])
        duplicate["ProviderIds"] = {"Tmdb": "1"}
        result, _ = self.request([page([movie(1), duplicate, movie(2, ["adventure"])])], remembered=remembered)
        self.assertEqual([row["id"] for row in result["items"]], ["2"])
        self.assertGreater(result["items"][0]["score"], 0)

    def test_saved_fetch_is_by_id_with_user_and_root_scope_not_recent_window_or_filters(self):
        remembered = {movie_key("themoviedb:movie:987"): {"item_id": "987", "action": "later", "revision": 1}}
        result, session = self.request([page([{"Id": "my-library"}]), page([movie(987, editions=[source(180, "eng")])]),
                                        Response([{"Id": "my-library"}])],
                                       scope=SELECTED, remembered=remembered, mode="saved", max_minutes=90,
                                       chinese_subtitles=True)
        self.assertEqual([row["id"] for row in result["items"]], ["987"])
        self.assertEqual(result["items"][0]["minutes"], 180)
        query = session.calls[-2][1]["params"]
        self.assertEqual(query["Ids"], "987")
        self.assertEqual(query["ParentId"], "my-library")
        self.assertEqual(query["IsPlayed"], "false")
        self.assertEqual(query["Limit"], 12)
        self.assertNotIn("SortBy", query)
        self.assertTrue(session.calls[-1][0].endswith("/Items/987/Ancestors"))
        self.assertEqual(session.calls[-1][1]["params"], {"UserId": USER})

    def test_saved_ancestor_check_blocks_ids_fastpath_ignoring_parent_filter(self):
        remembered = {movie_key("themoviedb:movie:987"): {"item_id": "987", "action": "later", "revision": 1}}
        result, _ = self.request([page([{"Id": "my-library"}]), page([movie(987)]),
                                  Response([{"Id": "another-library"}])],
                                 scope=SELECTED, remembered=remembered, mode="saved")
        self.assertFalse(result["items"][0]["available"])
        self.assertNotIn("Fixture", json.dumps(result))

    def test_saved_missing_played_or_reused_id_never_exposes_old_movie_and_keeps_clearable_placeholders(self):
        remembered = {movie_key(f"themoviedb:movie:{i}"): {"item_id": str(i), "action": "later", "revision": i}
                      for i in (1, 2, 3)}
        reused = movie(3)
        reused["ProviderIds"] = {"Tmdb": "999"}
        result, _ = self.request([page([movie(2, played=True), reused])], remembered=remembered, mode="saved")
        self.assertEqual([row["id"] for row in result["items"]], ["3", "2", "1"])
        self.assertTrue(all(row["available"] is False for row in result["items"]))
        self.assertNotIn("Fixture", json.dumps(result))
        self.assertTrue(all("url" not in row for row in result["items"]))
        result, _ = self.request([page([movie(999)])], remembered=remembered, mode="saved")
        self.assertEqual(result, {"success": False, "reason": "UNAVAILABLE"})

    def test_saved_and_hidden_pages_are_bounded_and_empty_still_verify_identity(self):
        remembered = {movie_key(f"themoviedb:movie:{i}"): {"item_id": str(i), "action": "hide", "revision": i}
                      for i in range(1, 15)}
        result, session = self.request([page([movie(1), movie(2)])], remembered=remembered, mode="hidden", page=2)
        self.assertEqual((result["page"], result["pages"]), (2, 2))
        self.assertEqual([row["id"] for row in result["items"]], ["2", "1"])
        self.assertEqual(session.calls[-1][1]["params"]["Ids"], "1,2")
        result, session = self.request([], mode="saved")
        self.assertEqual(result["items"], [])
        self.assertEqual(len(session.calls), 2)
        for options in ({"mode": "admin"}, {"page": 0}, {"page": 18}, {"page": True}):
            result, session = self.request([], **options)
            self.assertEqual(result["reason"], "INVALID_FILTER")
            self.assertEqual(session.calls, [])

    def test_ancestor_route_is_read_only_and_requires_the_already_verified_user(self):
        for params in ({}, {"UserId": OTHER_USER}):
            session = Session([])
            with patch.object(emby.requests, "Session", return_value=session):
                with tonight._TonightClient(CONFIG) as client:
                    client._verified_user_id = USER
                    with self.assertRaises(emby.EmbyReadError) as caught:
                        client._get("/Items/1/Ancestors", params)
                    self.assertEqual(caught.exception.code, "FORBIDDEN_ROUTE")
            self.assertEqual(session.calls, [])
