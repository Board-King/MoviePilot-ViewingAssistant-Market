"""The visible scope must match the two requested media budgets."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins.v2" / "ptsubtitlefollowup"))
from fusion.execution_ui import render_execution


def walk(tree):
    for node in tree:
        yield node
        yield from walk(node.get("content", []))


class PersonalExecutionView(unittest.TestCase):
    status = dict(enabled=True, armed=False, state="ready", owner="mp-reader", profile_name="Emby reader",
                  sources=["tmdb_personal_movie", "tmdb_personal_tv"], media_types=["movie", "tv"],
                  preset="personal", daily_limit=3, tv_daily_limit=1, used_movies_today=2, used_tv_today=0,
                  can_authorize=True, authorization_digest="e" * 64)

    def action(self, **changes):
        nodes = walk(render_execution({**self.status, **changes}, review=True))
        return next((n["events"]["click"] for n in nodes if n.get("text") == "确认范围并授权" and "events" in n), None)

    def test_only_exact_verified_scope_and_both_quotas_enable_authorization(self):
        self.assertEqual(self.action()["params"], {"expected_digest":"e" * 64})
        for change in [dict(profile_name=""), dict(media_types=["movie"]), dict(sources=["tmdb_popular_movie"]),
                       dict(tv_daily_limit=True), dict(tv_daily_limit=0), dict(daily_limit=4)]:
            with self.subTest(change=change):
                self.assertIsNone(self.action(**change))

    def test_board_separates_budget_season_and_evidence_from_unconfirmed_output(self):
        nodes = list(walk(render_execution({**self.status, "state":"armed", "armed":True, "recent":[
            dict(title="季风", media_type="tv", season=2, match_score=84.6, match_reason="题材相合 · 冒险", state="unknown"),
            dict(title="潮汐", media_type="movie", season=1, state="confirmed")]})))
        text = [n.get("text", "") for n in nodes]
        for label in ("2 / 3", "0 / 1", "季风 · 第 2 季", "潮汐", "已确认订阅", "结果待核实"):
            self.assertIn(label, text)
        scores = [label for label in text if "匹配 " in label and " 分" in label]
        self.assertEqual(scores, ["剧集 · 匹配 84.6 分 · 题材相合 · 冒险"])
        self.assertNotIn("潮汐 · 第 1 季", text)
