"""Keep an alternate release inside the existing subscription's identity and filters."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins.v2/ptsubtitlefollowup"))
from fusion.gap_host import release
from fusion.gap_search import _filters, _offer


class GapSearchTests(unittest.TestCase):
    def test_only_standalone_subscription_group_can_relax(self):
        snap = {"group": "primary", "episode_groups": {9: "backup"}}
        params = {"include": "Primary", "exclude": "CAM", "min_seeders": 5, "effect": "DV"}
        kept = {k: v for k, v in params.items() if k != "include"}
        self.assertEqual(_filters(params, "Primary", snap, True), kept)
        self.assertEqual(_filters(params, "Primary", snap, False), params)
        self.assertEqual(_filters(params, None, snap, True), params)
        for criterion in ("Primary|Other", "Other", "Primary.*DV"):
            rule = {**params, "include": criterion}
            self.assertEqual(_filters(rule, criterion, snap, True), rule)
        self.assertEqual(_filters({**params, "include": "Backup"}, "Backup", snap, False), kept)
        self.assertEqual(params["include"], "Primary")

    def test_offer_checks_series_season_spec_and_episode_scope(self):
        media = SimpleNamespace(type="tv", media_source="themoviedb", media_id="42", tmdb_id=42)
        meta = SimpleNamespace(resource_team="Primary", resource_type="WEB-DL", resource_pix="1080p",
                               video_encode="H265", season_list=[2], episode_list=[9])
        torrent = SimpleNamespace(site=1, title="Synthetic E09", page_url=None, enclosure="fixture")
        ctx = SimpleNamespace(meta_info=meta, media_info=media, torrent_info=torrent)
        snapshot = {"media_id": "42", "season": 2, "spec": release(meta)[1]}
        offer = _offer(ctx, snapshot, "tv")
        self.assertEqual((offer["group"], offer["episodes"], offer["selectable"]), ("primary", [9], False))
        for obj, key, value in ((media, "type", "movie"), (media, "media_id", "41"),
                                (media, "media_source", "douban"), (meta, "season_list", [1, 2]),
                                (meta, "episode_list", []), (meta, "video_encode", "H264")):
            with self.subTest(key=key):
                original = getattr(obj, key)
                setattr(obj, key, value)
                self.assertIsNone(_offer(ctx, snapshot, "tv"))
                setattr(obj, key, original)
