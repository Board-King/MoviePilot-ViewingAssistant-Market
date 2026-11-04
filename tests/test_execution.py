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

    def lookup_subscription(self, identity, owner, *, indexed_only=False):
        if self.lookup_hook:
            self.lookup_hook(identity)
        sid = self.ledger.get((owner, identity["media_id"]))
        return {"status": "existing" if sid else "absent", "sid": sid}

    def lookup_library(self, identity, *, indexed_only=False):
        return self.libs.get(identity["media_id"], "absent")

    def add(self, identity, owner, *, may_dispatch, indexed_only=False):
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

    def ranked(self, limit=3):
        self.config.update(fusion_execution_preset="ranked", fusion_execution_daily_limit=limit)
        self.service.configure(self.config)

    def test_actual_scan_records_disjoint_movie_outcomes_and_untouched_candidates(self):
        self.ranked()
        self.candidates = [self.candidate(str(i)) for i in range(10, 16)]
        self.candidates.append(copy.deepcopy(self.candidates[0]))
        self.host.ledger[("FixtureViewer", "10")] = 81
        self.host.libs.update({"11": "present", "12": "unknown"})
        self.arm()
        self.assertIsNone(self.service.status()["last_scan"])
        self.service.tick()
        row = self.service.status()["last_scan"]
        self.assertEqual((row["candidates"], row["eligible"], row["checked"]), (6, 6, 4))
        self.assertEqual((row["subscribed"], row["owned"], row["library_unknown"], row["unseen"]), (1, 1, 1, 2))
        self.assertEqual((row["new"], row["remaining"]), (1, 2))
        self.assertNotIn("scope", row)
        self.assertEqual(self.create().status()["last_scan"], row)
        self.service.revoke()
        self.assertIsNone(self.service.status()["last_scan"])

    def test_scan_reports_which_gate_excluded_each_unique_movie_and_distinguishes_missing_inputs(self):
        self.candidates = [self.candidate(str(i)) for i in range(10, 14)]
        self.candidates[0]["taste_match"] = .6
        self.candidates[1]["sources"][0]["metadata"]["ratings"][0]["value"] = 7.9
        self.candidates[2]["contributions"] = {}
        self.candidates[3]["sources"][0]["observed_at"] = "2020-01-01T00:00:00Z"
        self.arm()
        self.service.tick()
        row = self.service.status()["last_scan"]
        self.assertEqual([row[k] for k in ("candidates","eligible","checked","match","rating","evidence","metadata")],
                         [4, 0, 0, 1, 1, 1, 1])
        self.service._candidates = lambda: {"enabled":True, "profile_available":True, "sources":[]}
        self.service.tick()
        row = self.service.status()["last_scan"]
        self.assertEqual(row["input"], "source")
        self.assertNotIn("candidates", row)

    def test_conflict_is_not_counted_as_existing_or_new_and_queued_success_is_disjoint(self):
        self.ranked()
        self.candidates = [self.candidate(str(i)) for i in range(10, 14)]
        original = self.host.lookup_subscription
        def lookup(identity, owner, **kwargs):
            if identity["media_id"] == "10":
                return {"status": "conflict", "sid": None}
            return original(identity, owner, **kwargs)
        self.host.lookup_subscription = lookup
        self.arm()
        self.service.tick()
        row = self.service.status()["last_scan"]
        self.assertEqual((row["conflict"], row["new"], row["subscribed"], row["remaining"]), (1, 3, 0, 0))
        self.service.tick()  # Quota guard must not pretend it screened another set of films.
        self.assertEqual(self.service.status()["last_scan"], row)

    def test_ranked_fills_remaining_slots_by_match_without_public_rating_cutoff(self):
        self.ranked()
        self.candidates = [self.candidate(str(i)) for i in range(10, 17)]
        for row, taste in zip(self.candidates, (.52, .60, .59, .80, .63, .79, .77)):
            row.update(taste_match=taste, confidence=.05)
            row["sources"][0]["metadata"]["ratings"] = []
        self.candidates[6]["sources"][0]["metadata"] = copy.deepcopy(self.candidate("16")["sources"][0]["metadata"])
        self.candidates[6]["sources"][0]["metadata"]["ratings"][0]["value"] = 6.12
        self.host.ledger[("FixtureViewer", "13")] = 91
        self.host.libs["15"] = "present"
        self.arm()
        self.service.tick()
        self.service.tick()
        self.service.tick()
        self.assertEqual(self.host.dispatched, [("FixtureViewer", i) for i in ("16", "14", "11")])
        self.assertEqual(self.service.status()["used_today"], 3)

    def test_ranked_requires_new_grant_and_counts_existing_quota(self):
        self.config["fusion_execution_preset"] = "balanced"
        self.service.configure(self.config)
        old_digest = self.arm()
        self.assertEqual(self.store.record["grant"]["scope"]["policy_version"], execution.POLICY_VERSION)
        self.service.tick()
        self.candidates = [self.candidate(str(i)) for i in range(11, 15)]
        self.ranked()
        self.assertFalse(self.service.authorize(old_digest)["success"])
        self.service.tick()
        self.assertEqual(len(self.host.dispatched), 1)
        self.arm()
        self.assertEqual(self.store.record["grant"]["scope"]["policy_version"], execution.RANKED_POLICY_VERSION)
        self.service.tick()
        self.assertEqual([i for _, i in self.host.dispatched], ["10", "11", "12"])
        self.assertEqual(self.service.status()["used_today"], 3)

    def test_ranked_keeps_source_evidence_guards_and_only_accepts_one_to_three_slots(self):
        self.ranked(4)
        self.assertEqual(self.service.preflight(self.config), {"success": False, "reason": "INVALID_POLICY"})
        self.ranked(2)
        self.candidates = [self.candidate(str(i)) for i in range(10, 15)]
        self.candidates[0]["contributions"] = {}
        self.candidates[1]["sources"][0]["source_id"] = "unapproved"
        self.candidates[2]["sources"][0]["observed_at"] = "2020-01-01T00:00:00Z"
        self.candidates[3]["state"] = "HOLD"
        self.arm()
        self.space["status"] = "low"
        self.service.tick()
        self.assertFalse(self.host.dispatched)
        self.space["status"] = "ready"
        self.service.tick()
        self.assertEqual(self.host.dispatched, [("FixtureViewer", "14")])

    def test_ranked_ties_follow_shelf_order_and_unknown_first_add_stops_batch(self):
        self.ranked()
        self.candidates = [self.candidate(str(i)) for i in (73, 91, 11, 92)]
        self.arm()
        def timeout(identity, owner, may_dispatch):
            self.assertTrue(may_dispatch())
            self.host.dispatched.append((owner, identity["media_id"]))
            raise TimeoutError()
        self.host.add_hook = timeout
        self.service.tick()
        self.service.tick()
        self.assertEqual(self.host.dispatched, [("FixtureViewer", "73")])
        self.assertEqual(self.service.status()["unknown_count"], 1)

    def test_ranked_rescans_from_best_when_day_or_rank_changes(self):
        for refresh in ("new_day", "new_order"):
            with self.subTest(refresh=refresh):
                self.setUp()
                self.ranked()
                self.candidates = [self.candidate(str(i)) for i in range(10, 20)]
                self.host.libs = {str(i): "present" for i in range(10, 14)}
                self.arm()
                self.service.tick()
                self.assertFalse(self.host.dispatched)
                if refresh == "new_day":
                    self.now += 120
                    self.host.libs.clear()
                    first = "10"
                else:
                    self.candidates[8]["taste_match"] = .99
                    first = "18"
                self.service.tick()
                self.assertEqual(self.host.dispatched[0], ("FixtureViewer", first))

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
