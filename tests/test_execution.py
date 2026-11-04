"""Lifecycle and failure injection for the opt-in subscription coordinator."""
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import threading
import unittest

FILE = Path(__file__).resolve().parents[1] / "plugins.v2/ptsubtitlefollowup/fusion/execution.py"
spec = importlib.util.spec_from_file_location("test_execution_domain", FILE)
execution = importlib.util.module_from_spec(spec)
spec.loader.exec_module(execution)
SOURCE = execution.SOURCE


class MemoryStore:
    def __init__(self):
        self.mutex = threading.Lock()
        self.record = None
        self.writes = 0
        self.before_write = None
        self.after_write = None
        self.scope_hash, self.nonce = None, 0

    def scope_fence(self, config_hash, *, force=False):
        if self.scope_hash != config_hash or force:
            self.scope_hash, self.nonce = config_hash, self.nonce + 1
        return str(self.nonce)

    def read_fence(self, config_hash):
        return str(self.nonce) if self.scope_hash == config_hash else None

    @contextmanager
    def locked(self):
        if not self.mutex.acquire(blocking=False):
            raise BlockingIOError()
        try:
            yield
        finally:
            self.mutex.release()

    def read(self, _key):
        assert self.mutex.locked()
        return copy.deepcopy(self.record)

    def write(self, _key, value):
        assert self.mutex.locked()
        if self.before_write:
            self.before_write(value)
        self.record = copy.deepcopy(value)
        self.writes += 1
        if self.after_write:
            self.after_write(value)


class Host:
    def __init__(self, case):
        self.case = case
        self.ledger, self.dispatched, self.libs = {}, [], {}
        self.lookup_hook = self.add_hook = None
        self.version, self.host_key, self.owner_key = "v2.15.6", "synthetic-host", "37"
        self.compatible = True

    def compatibility(self, owner):
        return {"ready": self.compatible, "version": self.version, "host_key": self.host_key, "owner_key": self.owner_key}

    def lookup_subscription(self, identity, owner):
        if self.lookup_hook:
            self.lookup_hook(identity)
        sid = self.ledger.get((owner, identity["media_id"]))
        return {"status": "existing" if sid else "absent", "sid": sid}

    def lookup_library(self, identity):
        return self.libs.get(identity["media_id"], "absent")

    def add(self, identity, owner, *, may_dispatch):
        if self.add_hook:
            return self.add_hook(identity, owner, may_dispatch)
        if not may_dispatch():
            return {"kind": "not_sent", "sid": None}
        self.dispatched.append((owner, identity["media_id"]))
        sid = 100 + len(self.dispatched)
        self.ledger[(owner, identity["media_id"])] = sid
        return {"kind": "added", "sid": sid}


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2030, 1, 1, 15, 59, tzinfo=timezone.utc).timestamp()
        self.config = {
            "fusion_execution_enabled": True, "fusion_execution_owner": "FixtureViewer",
            "fusion_execution_sources": [SOURCE], "fusion_execution_daily_limit": 1,
            "fusion_execution_preset": "conservative", "fusion_execution_legacy_isolated": True,
            "fusion_execution_storage_verified": True,
            "fusion_enabled": True, "fusion_server_ref": "local-synthetic", "fusion_user_id": "e" * 32,
            "fusion_library_mode": "selected_ids", "fusion_library_ids": "one,two",
            "fusion_discovery_enabled": True, "fusion_discovery_sources": [SOURCE],
        }
        self.store, self.host = MemoryStore(), Host(self)
        self.candidates = [self.candidate("10")]
        self.candidate_hook = None
        self.space = {"status": "ready", "fingerprint": "a" * 64, "locations_checked": 1,
                      "minimum_free_bytes": execution.SPACE_FLOOR, "available_bytes": 2 * execution.SPACE_FLOOR}
        self.service = self.create()

    def create(self):
        service = execution.ExecutionService(self.store, self.snapshot, lambda: self.host,
                                             current_config=lambda: copy.deepcopy(self.config), clock=lambda: self.now,
                                             storage_check=lambda: copy.deepcopy(self.space))
        service.configure(self.config)
        return service

    def candidate(self, media_id):
        when = datetime.fromtimestamp(self.now, timezone.utc).isoformat()
        source = {"source_id": SOURCE, "source_item_id": media_id, "intent_kind": "discovery",
                  "observed_at": when, "title": "Synthetic " + media_id, "year": 2028,
                  "metadata": {"genres": ["Drama"], "ratings": [{"provider": "themoviedb", "scale": 10,
                                "value": 8.5, "count": 24, "observed_at": when}]}}
        return {"entity_key": "themoviedb:movie:" + media_id, "provider": "themoviedb", "media_type": "movie",
                "media_id": media_id, "state": "SHADOW", "intent_kind": "discovery", "can_execute": False,
                "algorithm_version": "p1b-shadow-v1", "profile_as_of": when, "season_hints": [],
                "metadata_source": {"source_id": SOURCE, "source_item_id": media_id}, "sources": [source],
                "taste_match": .82, "confidence": .18, "contributions": {"dimensions": [
                    {"dimension": "genres", "matched_labels": ["Drama"], "support": 1.2}]}}

    def snapshot(self):
        if self.candidate_hook:
            self.candidate_hook()
        return {"enabled": True, "profile_available": True, "sources": [
            {"id": SOURCE, "status": "ready", "stale": False, "authentication_blocked": False}],
                "candidates": copy.deepcopy(self.candidates)}

    def arm(self):
        digest = self.service.status()["authorization_digest"]
        self.assertRegex(digest or "", "^[a-f0-9]{64}$")
        self.assertTrue(self.service.authorize(digest)["success"])
        return digest

    def record(self):
        return next(iter(self.store.record["intents"].values()))

    def test_imported_enabled_config_cannot_submit_until_digest_approved(self):
        self.service.tick()
        self.assertIsNone(self.store.record)
        self.assertFalse(self.service.authorize("bad")["success"])
        digest = self.service.status()["authorization_digest"]
        self.config["fusion_execution_daily_limit"] = 2
        self.service.configure(self.config)
        self.assertFalse(self.service.authorize(digest)["success"])
        self.service.tick()
        self.assertEqual(self.host.dispatched, [])
        self.arm()
        self.assertTrue(self.service.status()["armed"])

    def test_grant_binds_user_library_host_version_and_exact_owner(self):
        for change in ("user", "library", "host", "version", "owner"):
            with self.subTest(change=change):
                self.setUp()
                self.arm()
                if change == "user":
                    self.config["fusion_user_id"] = "d" * 32
                elif change == "library":
                    self.config["fusion_library_ids"] = "three"
                elif change == "owner":
                    self.host.owner_key = "38"
                elif change == "host":
                    self.host.host_key = "synthetic-other-host"
                else:
                    self.host.version = "v2.15.7"
                self.service.tick()
                self.assertFalse(self.host.dispatched)
                self.assertIsNone(self.store.record["grant"])

    def test_only_authorized_source_quality_and_positive_related_evidence_can_admit(self):
        for mutation in (
            lambda r: r.update(provider="douban"), lambda r: r.update(media_type="tv"),
            lambda r: r.update(state="HOLD"), lambda r: r.update(taste_match=.6, fusion_rank=.99),
            lambda r: r.update(confidence=0), lambda r: r.update(contributions={}),
            lambda r: r.update(metadata_source={"source_id": "mp_followed_shares", "source_item_id": "10"}),
            lambda r: r["sources"][0]["metadata"].update(genres=["Other"]),
            lambda r: r["sources"][0]["metadata"]["ratings"][0].update(value=7.99),
            lambda r: r["sources"][0]["metadata"]["ratings"][0].update(count=0),
            lambda r: r["sources"][0]["metadata"]["ratings"][0].update(provider="douban"),
            lambda r: r["sources"][0].update(observed_at="2020-01-01T00:00:00Z"),
            lambda r: r["sources"][0]["metadata"]["ratings"][0].update(observed_at="2050-01-01T00:00:00Z"),
        ):
            with self.subTest(mutation=mutation):
                self.setUp()
                mutation(self.candidates[0])
                self.arm()
                self.service.tick()
                self.assertEqual(self.host.dispatched, [])
                self.assertEqual(self.store.record["intents"], {})

    def test_reservation_and_submitting_marker_are_durable_before_add(self):
        self.arm()
        def add(identity, owner, may_dispatch):
            self.assertTrue(may_dispatch())
            item = self.record()
            self.assertTrue(item["reserved"])
            self.assertEqual(item["state"], "SUBMITTING")
            self.assertEqual([e["state"] for e in item["events"]], ["QUEUED", "SUBMITTING"])
            self.assertEqual(item["scope"]["owner"], owner)
            self.assertEqual(item["identity"], identity)
            self.assertEqual(item["quota_day"], "2030-01-01")
            self.host.ledger[(owner, identity["media_id"])] = 8
            return {"kind": "added", "sid": 8}
        self.host.add_hook = add
        self.service.tick()
        self.assertEqual(self.record()["state"], "CONFIRMED")
        self.assertEqual(self.service.status()["used_today"], 1)

    def test_duplicate_discovery_replay_and_host_deletion_do_not_repeat_add(self):
        self.arm()
        self.service.tick()
        self.host.ledger.clear()
        self.candidates += [self.candidate("10")]
        self.now += 120
        restarted = self.create()
        restarted.tick()
        self.assertEqual(self.host.dispatched, [("FixtureViewer", "10")])
        self.assertEqual(len(self.store.record["intents"]), 1)

    def test_preexisting_host_subscription_gets_zero_quota_tombstone(self):
        self.host.ledger[("FixtureViewer", "10")] = 11
        self.arm()
        self.service.tick()
        self.assertEqual(self.record()["state"], "EXISTING")
        self.assertFalse(self.record()["reserved"])
        self.host.ledger.clear()
        self.service.tick()
        self.assertEqual(self.host.dispatched, [])

    def test_timeout_after_host_commit_is_confirmed_by_read_without_second_add(self):
        self.arm()
        def timeout(identity, owner, may_dispatch):
            self.assertTrue(may_dispatch())
            self.host.dispatched.append((owner, identity["media_id"]))
            self.host.ledger[(owner, identity["media_id"])] = 21
            raise TimeoutError("secret native details")
        self.host.add_hook = timeout
        self.service.tick()
        self.create().tick()
        self.assertEqual(self.record()["state"], "CONFIRMED")
        self.assertEqual(self.record()["sid"], 21)
        self.assertEqual(len(self.host.dispatched), 1)
        self.assertNotIn("secret native", str(self.store.record))

    def test_unconfirmed_unknown_holds_risk_across_days_and_revocation(self):
        self.arm()
        def lost(identity, owner, may_dispatch):
            self.host.dispatched.append((owner, identity["media_id"]))
            raise ConnectionError("hidden URL/token")
        self.host.add_hook = lost
        self.service.tick()
        self.assertEqual(self.record()["state"], "UNKNOWN")
        self.now += 120
        self.candidates.append(self.candidate("12"))
        self.create().tick()
        self.assertEqual(len(self.host.dispatched), 1)
        self.assertEqual(self.record()["quota_day"], "2030-01-01")
        self.assertTrue(self.service.revoke()["success"])
        self.config["fusion_execution_enabled"] = False
        restarted = self.create()
        self.assertTrue(restarted.needs_tick)
        self.host.ledger[("FixtureViewer", "10")] = 123
        restarted.tick()
        self.assertEqual(self.record()["state"], "CONFIRMED")
        self.assertEqual(len(self.host.dispatched), 1)

    def test_storage_failure_while_reserving_cannot_call_host(self):
        self.arm()
        self.store.before_write = lambda _: (_ for _ in ()).throw(OSError("synthetic storage failure detail"))
        self.service.tick()
        self.assertEqual(self.host.dispatched, [])
        self.assertEqual(self.store.record["intents"], {})
        self.assertNotIn("synthetic storage failure detail", str(self.service.status()))

    def test_quota_uses_shanghai_day_and_holds_front_candidates_fairly(self):
        self.candidates = [self.candidate(str(i)) for i in range(10, 20)]
        self.host.libs = {str(i): "present" for i in range(10, 16)}
        self.arm()
        self.service.tick()
        self.assertEqual(self.host.dispatched, [])
        self.service.tick()
        self.assertEqual(self.host.dispatched, [("FixtureViewer", "16")])
        self.service.tick()
        self.assertEqual(len(self.host.dispatched), 1)
        self.now += 120
        self.service.tick()
        self.assertEqual(len(self.host.dispatched), 2)
        self.assertEqual({it["quota_day"] for it in self.store.record["intents"].values()}, {"2030-01-01", "2030-01-02"})

    def test_cold_configuration_mismatch_cannot_reactivate_prior_grant(self):
        self.arm()
        original = copy.deepcopy(self.config)
        self.config["fusion_execution_enabled"] = False
        self.create()
        self.assertIsNone(self.store.record["grant"])
        self.config = original
        self.create().tick()
        self.assertFalse(self.host.dispatched)

    def test_scope_is_rechecked_after_native_recognition_returns(self):
        self.arm()
        def identify_then_dispatch(identity, owner, may_dispatch):
            self.assertTrue(may_dispatch())
            self.host.owner_key = "99"
            self.assertFalse(may_dispatch())
            return {"kind": "not_sent", "sid": None}
        self.host.add_hook = identify_then_dispatch
        self.service.tick()
        self.assertEqual(self.record()["state"], "CANCELLED")
        self.assertEqual(self.service.status()["used_today"], 0)
        self.assertFalse(self.host.dispatched)

    def test_crossing_quota_midnight_during_recognition_releases_unsent_reservation(self):
        self.arm()
        def recognition(identity, owner, may_dispatch):
            self.assertTrue(may_dispatch())
            self.now += 65
            self.assertFalse(may_dispatch())
            return {"kind": "not_sent", "sid": None}
        self.host.add_hook = recognition
        self.service.tick()
        self.assertFalse(self.record()["reserved"])
        self.assertEqual(self.record()["state"], "CANCELLED")
        self.host.add_hook = None
        self.candidates.append(self.candidate("11"))
        self.service.tick()
        self.service.tick()
        self.assertEqual(len(self.host.dispatched), 1)
        self.assertEqual(self.service.status()["used_today"], 1)

    def test_transient_space_failure_holds_grant_until_same_mount_recovers(self):
        for result, reason in (({"status": "unknown"}, "SPACE_UNVERIFIED"),
                               ({"status": "low", "available_bytes": 3}, "SPACE_LOW"),
                               ({"status": "ready", "available_bytes": 3}, "SPACE_LOW")):
            with self.subTest(reason=reason):
                self.setUp()
                digest = self.arm()
                healthy = copy.deepcopy(self.space)
                self.space.update(result)
                self.service.tick()
                status = self.service.status()
                self.assertTrue(status["armed"])
                self.assertEqual(status["state"], "blocked")
                self.assertEqual(status["last_reason"], reason)
                self.assertIsNone(status["authorization_digest"])
                self.assertFalse(self.host.dispatched)
                self.space = healthy
                self.assertEqual(self.service.status()["authorization_digest"], digest)
                self.service.tick()
                self.assertEqual(len(self.host.dispatched), 1)

    def test_verified_mount_change_invalidates_scope_and_space_is_rechecked_at_dispatch(self):
        self.arm()
        def disk_changed(identity, owner, may_dispatch):
            self.assertTrue(may_dispatch())
            self.space["fingerprint"] = "b" * 64
            self.assertFalse(may_dispatch())
            return {"kind": "not_sent", "sid": None}
        self.host.add_hook = disk_changed
        self.service.tick()
        self.assertFalse(self.service.status()["armed"])
        self.assertFalse(self.host.dispatched)

if __name__ == "__main__":
    unittest.main()
