"""Keep an alternate release inside the existing subscription's identity and filters."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins.v2/ptsubtitlefollowup"))
from fusion.gap_host import release
from fusion.gap_search import _filters, _offer, search_candidates


class GapSearchTests(unittest.TestCase):
    def test_new_v3_patches_keep_native_budget_identity_and_subscription_sites(self):
        sub = SimpleNamespace(sites=[1], episode_group=None, best_version=False, keyword=None,
                              search_imdbid=False, filter_groups=["fixture"], custom_words=None, include="Origin")
        snapshot = {"media_id": "42", "season": 2, "group": "origin", "episode_groups": {}}
        media = SimpleNamespace(type="tv", media_source="themoviedb", media_id="42")
        recognize = Mock(return_value=media)
        owner, search, budget = Mock(), Mock(), Mock()
        owner.get_params.return_value = {"include": "Origin", "quality": "WEB"}
        owner.get_sub_sites.return_value = [1, 2]
        search.process.return_value = []
        modules = {
            "app.chain.media": SimpleNamespace(MediaChain=lambda: SimpleNamespace(recognize_media=recognize)),
            "app.chain.search": SimpleNamespace(SearchChain=lambda: search),
            "app.chain.subscribe": SimpleNamespace(SubscribeChain=lambda: owner),
            "app.chain.subscribe.metadata": SimpleNamespace(apply_subscription_classification=lambda media, sub: media),
            "app.application.configuration": SimpleNamespace(get_configured_system_config=dict),
            "app.application.subscription.sitebudget": SimpleNamespace(SubscriptionSiteBudget=budget),
            "app.runtime.stop": SimpleNamespace(runtime_stop_state=object()),
            "app.schemas.types": SimpleNamespace(MediaType=SimpleNamespace(TV="tv"), SystemConfigKey=object()),
        }
        for version in ("v3.2.12", "v3.2.14"):
            with self.subTest(version=version), patch.dict(sys.modules, modules | {"version": SimpleNamespace(APP_VERSION=version)}):
                search.reset_mock()
                self.assertEqual(search_candidates(sub, snapshot, include_alternatives=True), [])
                search.configure_subscription_site_budget.assert_called_once_with(budget.return_value)
                recognize.assert_called_with(mtype="tv", media_source="themoviedb", media_id="42")
                query = search.process.call_args.kwargs
                self.assertEqual((query["sites"], query["filter_params"], query["mediainfo"].season),
                                 ([1], {"quality": "WEB"}, 2))

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
