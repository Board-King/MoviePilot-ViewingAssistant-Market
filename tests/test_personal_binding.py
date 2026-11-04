"""A personal lookup must never change the selected account, its scope or a grant."""
from contextlib import nullcontext
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins.v2/ptsubtitlefollowup"))
with patch.dict(sys.modules, {"requests": types.ModuleType("requests")}):
    from fusion.service import SnapshotService
    from fusion import service as service_module


class PersonalBindingTests(unittest.TestCase):
    def setUp(self):
        self.write = Mock()
        self.resolve = Mock(return_value={"host": "https://media.invalid"})
        self.client = Mock()
        self.service = SnapshotService(lambda key: None, self.write, self.resolve,
                                       client_factory=lambda *a, **k: nullcontext(self.client))
        self.config = dict(fusion_enabled=True, fusion_server_ref="Demo", fusion_user_id="a" * 32,
                           fusion_library_mode="selected_ids", fusion_library_ids=["movies"])
        self.service.configure(self.config)
        self.write.reset_mock()

    def test_unverified_or_incomplete_binding_never_contacts_emby(self):
        self.assertEqual(self.service.tonight()["reason"], "VERIFY_REQUIRED")
        self.write.assert_not_called()
        self.service.configure({**self.config, "fusion_library_ids": []})
        self.assertEqual(self.service.tonight()["reason"], "SETUP_REQUIRED")
        self.resolve.assert_not_called()

    def test_new_configuration_during_request_suppresses_old_results(self):
        self.service._state["native_server_id"] = "server-A"
        def rebind(_server, *, user_id, scope, stop_callback, **kwargs):
            self.assertEqual((user_id, scope["library_ids"]), ("a" * 32, ["movies"]))
            self.assertFalse(stop_callback())
            self.service.configure({**self.config, "fusion_user_id": "b" * 32})
            self.assertTrue(stop_callback())
            return {"success": True, "items": [{"title": "Previous account"}]}
        with patch.object(service_module, "select_tonight", side_effect=rebind):
            self.assertEqual(self.service.tonight(), {"success": False, "reason": "UNAVAILABLE"})

    def test_valid_read_can_start_without_an_existing_taste_profile_or_subscription(self):
        self.service._state["native_server_id"] = "server-A"
        with patch.object(service_module, "select_tonight", return_value={"success": True, "items": []}) as query:
            self.assertTrue(self.service.tonight(90, True)["success"])
        args = query.call_args.kwargs
        self.assertEqual((args["max_minutes"], args["chinese_subtitles"], args["baseline"]), (90, True, None))
        self.write.assert_not_called()
        with self.service._run_lock:
            self.assertEqual(self.service.tonight()["reason"], "BUSY")

    def test_account_choices_exclude_disabled_unverified_and_private_fields(self):
        self.client.list_users.return_value = [
            {"id": "a" * 32, "name": "Example", "disabled": False, "other": "hidden"},
            {"id": "b" * 32, "name": "Disabled", "disabled": True},
            {"id": "c" * 32, "name": "Unknown", "disabled": None}]
        self.assertEqual(self.service.binding_users("Demo"), {"success": True, "users": [
            {"id": "a" * 32, "name": "Example"}]})
        self.resolve.assert_called_once_with("Demo")
        self.client.verify_binding.assert_not_called()
        self.write.assert_not_called()

    def test_library_choices_read_only_the_named_users_views_and_accept_mixed_legacy_libraries(self):
        uid = "a" * 32
        self.client.describe_server.return_value = {"id": "server-A"}
        self.client.list_views.return_value = [
            {"id": "cinema", "name": "Cinema", "collection_type": "movies"},
            {"id": "misc", "name": "Mixed", "collection_type": None},
            {"id": "music", "name": "Music", "collection_type": "music"}]
        result = self.service.binding_libraries("Demo", uid)
        self.assertEqual(result["libraries"], [{"id":"cinema","name":"Cinema"},{"id":"misc","name":"Mixed"}])
        self.client.verify_binding.assert_called_once_with("server-A", uid)
        self.client.list_views.assert_called_once_with(uid)
        self.write.assert_not_called()
