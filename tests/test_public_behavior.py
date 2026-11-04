"""Offline regressions. All titles, identities and viewing events below are invented."""
import copy
from hashlib import sha256
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock
from uuid import UUID


# Import the pure plugin modules without starting a MoviePilot instance.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins.v2" / "ptsubtitlefollowup"))
from fusion.candidate_ui import browse_candidates
from fusion.execution import ExecutionService, SOURCE
from fusion.execution_store import ExecutionStore
from fusion.profile import build_baseline


def nodes(tree):
    if isinstance(tree, list):
        for child in tree:
            yield from nodes(child)
    elif isinstance(tree, dict):
        yield tree
        yield from nodes(tree.get("content", []))


class DiscoveryTests(unittest.TestCase):
    def test_repeated_views_do_not_outweigh_a_favorite(self):
        timestamp = "2040-03-01T12:00:00Z"
        watched = {"entity_key": "fixture:film:one", "kind": "movie", "signal": "played",
                   "observed_at": timestamp, "features": {"genres": ["Fixture genre A"]}}
        favorite = {"entity_key": "fixture:film:two", "kind": "movie", "signal": "favorite",
                    "features": {"genres": ["Fixture genre B"]}}
        single = build_baseline([watched, favorite], as_of=timestamp)
        repeated = build_baseline([watched] * 12 + [favorite], as_of=timestamp)
        self.assertEqual(single["dimensions"], repeated["dimensions"])
        genres = repeated["dimensions"]["genres"]["features"]
        self.assertEqual([row["label"] for row in genres], ["Fixture genre B", "Fixture genre A"])
        self.assertGreater(genres[0]["support"], genres[1]["support"])
        self.assertEqual(repeated["statistics"]["contributing_entities"], 2)

    def test_tv_card_shows_known_episodes_without_assuming_season_one(self):
        row = {"title": "Fixture Series", "media_type": "tv", "year": 2040,
               "entity_key": "fixture:tv:one", "season_hints": [], "season_unknown": True,
               "display_details": {"episodes": 7}, "state": "HOLD"}
        before = copy.deepcopy(row)
        def decorate(snapshot):
            return snapshot
        shelf = browse_candidates({"candidates": [row]}, decorate, kind="tv")
        self.assertTrue(shelf["success"])
        self.assertEqual(shelf["total"], 1)
        card_nodes = list(nodes(shelf["cards"]))
        labels = " ".join(node.get("text", "") for node in card_nodes)
        self.assertIn("Fixture Series", labels)
        self.assertIn("7 集", labels)
        self.assertNotIn("第 1 季", labels)
        self.assertFalse(any(node.get("events") for node in card_nodes))
        self.assertEqual(row, before)


class SubscriptionTests(unittest.TestCase):
    def test_two_workers_share_the_authorization_fence_and_exclude_concurrent_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            first = ExecutionStore(lambda: directory)
            second = ExecutionStore(lambda: directory)
            digest = sha256(b"fictional subscription policy A").hexdigest()
            replacement = sha256(b"fictional subscription policy B").hexdigest()
            original = first.scope_fence(digest)
            self.assertEqual(second.scope_fence(digest), original)
            with first.locked():
                with self.assertRaises(BlockingIOError), second.locked():
                    self.fail("concurrent dispatch was allowed")
                new_fence = second.scope_fence(replacement)
                self.assertNotEqual(new_fence, original)
                self.assertIsNone(first.read_fence(digest))
            with second.locked():
                self.assertEqual(second.read_fence(replacement), new_fence)

    def test_disabled_execution_does_not_create_a_ledger_or_contact_a_host(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ExecutionStore(lambda: directory)
            candidates, host = Mock(), Mock()
            service = ExecutionService(store, candidates, host)
            service.configure({})
            service.tick()
            self.assertFalse(service.needs_tick)
            self.assertFalse(store.exists())
            host.assert_not_called()
            candidates.assert_not_called()

    def test_preflight_requires_explicit_storage_and_source_confirmation(self):
        config = {
            "fusion_enabled": True, "fusion_server_ref": "fixture-server",
            "fusion_user_id": UUID(int=17).hex, "fusion_library_mode": "all_visible",
            "fusion_discovery_enabled": True, "fusion_discovery_sources": [SOURCE],
            "fusion_execution_enabled": True, "fusion_execution_owner": "fixture-viewer",
            "fusion_execution_sources": [SOURCE], "fusion_execution_storage_verified": True,
            "fusion_execution_legacy_isolated": True, "fusion_execution_daily_limit": 2,
        }
        with tempfile.TemporaryDirectory() as directory:
            store = ExecutionStore(lambda: directory)
            host = Mock()
            service = ExecutionService(store, Mock(), host)
            for change, reason in (
                ({"fusion_execution_storage_verified": False}, "SPACE_UNVERIFIED"),
                ({"fusion_execution_legacy_isolated": False}, "LEGACY_NOT_ISOLATED"),
                ({"fusion_execution_sources": []}, "SOURCE_REQUIRED"),
                ({"fusion_execution_daily_limit": 0}, "INVALID_POLICY"),
            ):
                with self.subTest(change=change):
                    self.assertEqual(service.preflight({**config, **change}), {"success": False, "reason": reason})
            host.assert_not_called()
            self.assertFalse(store.exists())


if __name__ == "__main__":
    unittest.main()
