"""Synthetic local preference persistence, reversible decisions and receipt races."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from test_fusion_emby import REQUESTS

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins.v2/ptsubtitlefollowup"))
with patch.dict(sys.modules, {"requests": REQUESTS}):
    from fusion.choices import ChoiceStore, ChoiceError, DATA_KEY, MAX_CHOICES, MAX_SCOPES, movie_key, scope_key
    from fusion.service import SnapshotService
    from fusion import service as service_module


class ChoiceTests(unittest.TestCase):
    def setUp(self):
        self.data, self.now, self.writes = {}, 1000, []
        self.store = ChoiceStore(lambda key: copy.deepcopy(self.data.get(key)), self.write, lambda: self.now)
        self.scope = scope_key("binding-A", "server-A")

    def write(self, key, value):
        self.writes.append(key)
        self.data[key] = copy.deepcopy(value)

    def offer(self, identity=1, scope=None, available=True):
        scope = scope or self.scope
        rows = [{"id": str(identity), "_choice_key": movie_key(f"tmdb:{identity}"), "available": available}]
        context = self.store.offer(scope, rows, self.store.snapshot(scope))
        self.assertNotIn("_choice_key", rows[0])
        return {"context": context, "item_id": str(identity), "action": "later"}

    def fail(self, code, payload, scope=None):
        before = copy.deepcopy(self.data)
        with self.assertRaises(ChoiceError) as caught:
            self.store.change(scope or self.scope, payload)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(before, self.data)

    def test_explicit_save_hide_clear_persistence_and_reverse_preserve_previous_state(self):
        payload = self.offer()
        self.assertEqual(self.writes, [])
        saved = self.store.change(self.scope, payload)
        self.assertEqual(saved["counts"], {"later": 1, "hide": 0})
        self.assertEqual(saved["undo"]["action"], "clear")
        hidden = self.store.change(self.scope, {**self.offer(), "action": "hide"})
        self.assertEqual(hidden["counts"], {"later": 0, "hide": 1})
        restored = self.store.change(self.scope, hidden["undo"])
        self.assertEqual(restored["counts"], {"later": 1, "hide": 0})
        loaded = ChoiceStore(lambda k: copy.deepcopy(self.data.get(k)), self.write, lambda: self.now)
        self.assertEqual(loaded.snapshot(self.scope), self.store.snapshot(self.scope))
        self.assertNotIn("title", json.dumps(self.data))
        self.assertEqual(set(self.writes), {DATA_KEY})

    def test_stale_tabs_and_undo_cannot_overwrite_a_newer_choice(self):
        first, other = self.offer(), self.offer()
        saved = self.store.change(self.scope, first)
        self.fail("CHOICE_CHANGED", {**other, "action": "hide"})
        hidden = self.store.change(self.scope, {**self.offer(), "action": "hide"})
        self.fail("CHOICE_CHANGED", saved["undo"])
        self.store.change(self.scope, hidden["undo"])
        self.fail("CHOICE_CHANGED", hidden["undo"])

    def test_clear_recreate_does_not_reuse_an_old_revision_and_empty_scopes_do_not_fill_limit(self):
        oldest = self.offer()
        saved = self.store.change(self.scope, oldest)
        self.store.change(self.scope, saved["undo"])
        self.assertNotIn(self.scope, self.data[DATA_KEY]["scopes"])
        self.store.change(self.scope, self.offer())
        self.fail("CHOICE_CHANGED", saved["undo"])
        self.fail("CHOICE_CHANGED", oldest)
        for i in range(MAX_SCOPES + 3):
            scope = scope_key(f"binding-{i}", "server")
            saved = self.store.change(scope, self.offer(i + 100, scope))
            self.store.change(scope, saved["undo"])
        self.assertEqual(len(self.data[DATA_KEY]["scopes"]), 1)

    def test_full_store_no_eviction_and_clear_of_never_chosen_is_safe_at_scope_limit(self):
        for i in range(MAX_CHOICES):
            self.store.change(self.scope, self.offer(i))
        self.fail("CHOICE_LIMIT", self.offer(MAX_CHOICES))
        saved = self.store.change(self.scope, {**self.offer(0), "action": "clear"})
        self.assertEqual(saved["counts"]["later"], MAX_CHOICES - 1)
        self.store.change(self.scope, self.offer(MAX_CHOICES))
        for i in range(MAX_SCOPES - 1):
            scope = scope_key(f"other-{i}", "server")
            self.store.change(scope, self.offer(900 + i, scope))
        extra = scope_key("extra", "server")
        self.fail("CHOICE_LIMIT", self.offer(999, extra), extra)
        self.store.change(extra, {**self.offer(999, extra), "action": "clear"})
        self.assertEqual(len(self.data[DATA_KEY]["scopes"]), MAX_SCOPES)
        self.assertEqual(len(self.store.snapshot(self.scope)), MAX_CHOICES)

    def test_receipt_scope_forgery_expiry_reload_and_strict_payload(self):
        payload = self.offer()
        self.fail("STALE_CONTEXT", payload, scope_key("other-binding", "server-A"))
        self.fail("INVALID_CHOICE", {**payload, "item_id": "not-shown"})
        self.fail("INVALID_CHOICE", {**payload, "title": "untrusted"})
        self.fail("INVALID_CHOICE", {**payload, "action": "download"})
        self.now += 901
        self.fail("STALE_CONTEXT", payload)
        payload = self.offer()
        self.store.reset()
        self.fail("STALE_CONTEXT", payload)

    def test_unavailable_remembered_entry_can_be_cleared_and_undone_but_not_changed(self):
        self.store.change(self.scope, self.offer())
        unavailable = self.offer(available=False)
        self.fail("INVALID_CHOICE", {**unavailable, "action": "hide"})
        cleared = self.store.change(self.scope, {**unavailable, "action": "clear"})
        self.assertEqual(cleared["counts"], {"later": 0, "hide": 0})
        self.assertEqual(self.store.change(self.scope, cleared["undo"])["counts"]["later"], 1)

    def test_storage_failure_preserves_state_and_can_retry_same_receipt(self):
        payload = self.offer()
        original = self.store._write
        self.store._write = lambda *_: (_ for _ in ()).throw(RuntimeError("secret-db-url"))
        self.fail("STORAGE_FAILED", payload)
        self.store._write = original
        self.assertTrue(self.store.change(self.scope, payload)["success"])
        self.data[DATA_KEY] = {"schema": 9, "secret": "not echoed"}
        with self.assertRaises(ChoiceError) as caught:
            self.store.snapshot(self.scope)
        self.assertEqual(str(caught.exception), "STORAGE_FAILED")

    def test_service_reload_retains_choices_but_config_changes_invalidate_receipts(self):
        cfg = dict(fusion_enabled=True, fusion_server_ref="Example", fusion_user_id="a" * 32,
                   fusion_library_mode="selected_ids", fusion_library_ids=["movies"])
        def service():
            obj = SnapshotService(lambda k: copy.deepcopy(self.data.get(k)), self.write, lambda _: {}, clock=lambda: self.now)
            obj.configure(cfg)
            obj._state["native_server_id"] = "server-A"
            obj._persist()
            return obj
        current = service()
        def selected(*a, **kw):
            if movie_key("tmdb:1") in kw["remembered"]:
                return {"success": True, "items": []}
            return {"success": True, "items": [{"id": "1", "title": "Synthetic", "_choice_key": movie_key("tmdb:1")} ]}
        with patch.object(service_module, "select_tonight", side_effect=selected):
            before = copy.deepcopy(self.data)
            picks = current.tonight()
            self.assertEqual(before, self.data)
            self.assertNotIn("_choice_key", json.dumps(picks))
            payload = {"context": picks["context"], "item_id": "1", "action": "later"}
            self.assertTrue(current.tonight_choice(payload)["success"])
            fresh = service()
            self.assertEqual(fresh.tonight()["counts"]["later"], 1)
            self.assertEqual(fresh.tonight_choice(payload)["reason"], "STALE_CONTEXT")
            current.configure({**cfg, "fusion_user_id": "b" * 32})
            self.assertEqual(current.tonight_choice(payload)["reason"], "STALE_CONTEXT")
            current._state["native_server_id"] = "server-A"
            self.assertEqual(current.tonight()["counts"]["later"], 0)
            current.configure(cfg)
            current._state["native_server_id"] = "server-A"
            self.assertEqual(current.tonight()["counts"]["later"], 1)

    def test_revoked_library_allows_local_cleanup_without_media_but_timeouts_do_not_claim_it_is_missing(self):
        service = SnapshotService(lambda k: copy.deepcopy(self.data.get(k)), self.write, lambda _: {}, clock=lambda: self.now)
        service.configure(dict(fusion_enabled=True, fusion_server_ref="Example", fusion_user_id="a" * 32,
                               fusion_library_mode="selected_ids", fusion_library_ids=["movies"]))
        service._state["native_server_id"] = "server-A"
        with patch.object(service_module, "select_tonight", return_value={"success": True, "items": [
                {"id": "1", "title": "Private synthetic film", "url": "https://private.invalid/film",
                 "_choice_key": movie_key("tmdb:1")} ]}):
            selected = service.tonight()
        self.assertTrue(service.tonight_choice({"context": selected["context"], "item_id": "1", "action": "later"})["success"])
        for reason in ("LIBRARY_NOT_VISIBLE", "FORBIDDEN"):
            with patch.object(service_module, "select_tonight", return_value={"success": False, "reason": reason}):
                inaccessible = service.tonight(mode="saved")
            self.assertEqual(inaccessible["unavailable_reason"], reason)
            self.assertFalse(inaccessible["items"][0]["available"])
            self.assertNotIn("Private", json.dumps(inaccessible))
            self.assertNotIn("url", json.dumps(inaccessible))
        for reason in ("UNAVAILABLE", "VERIFY_REQUIRED"):
            with patch.object(service_module, "select_tonight", return_value={"success": False, "reason": reason}):
                self.assertEqual(service.tonight(mode="saved"), {"success": False, "reason": reason})
        self.assertTrue(service.tonight_choice({"context": inaccessible["context"], "item_id": "1", "action": "clear"})["success"])
