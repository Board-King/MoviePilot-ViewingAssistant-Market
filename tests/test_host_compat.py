"""Offline boundaries for durable event dates and native V3 page actions."""
from contextlib import contextmanager
from datetime import datetime
import importlib.util
from pathlib import Path
import sqlite3
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


SOURCE = Path(__file__).resolve().parents[1] / "plugins.v2/ptsubtitlefollowup/host_compat.py"


def load_compat(version):
    modules = {}
    def module(name, **values):
        result = ModuleType(name)
        result.__dict__.update(values)
        modules[name] = result
    module("version", APP_VERSION=version)
    for name in ("app", "app.sdk", "app.core", "app.schemas"):
        module(name)
    for name in ("app.sdk.config", "app.core.config"):
        module(name, settings=object())
    for name in ("app.sdk.events", "app.core.event"):
        module(name, Event=object, eventmanager=object())
    for name in ("app.sdk.logging", "app.log"):
        module(name, logger=object())
    for name in ("app.sdk.plugin", "app.plugins"):
        module(name, _PluginBase=object)
    module("app.schemas.types", EventType=object, MessageType=object, NotificationType=object)
    spec = importlib.util.spec_from_file_location("offline_host_compat", SOURCE)
    compat = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(compat)
    return compat


class EventDateTests(unittest.TestCase):
    def setUp(self):
        self.v3 = load_compat("v3.2.9")
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE downloadhistory(id INTEGER PRIMARY KEY, download_hash TEXT, downloader TEXT, date TEXT)")

    @contextmanager
    def connection(self):
        yield self.db

    def add(self, hash="a" * 32, downloader="FixtureDownloader", date="2026-01-02 03:04:05"):
        self.db.execute("INSERT INTO downloadhistory(download_hash,downloader,date) VALUES (?,?,?)",
                        (hash, downloader, date))

    def test_delayed_event_keeps_business_date_and_does_not_include_other_downloaders(self):
        self.add()
        self.add(downloader="UnrelatedDownloader", date="2026-11-21 01:00:00")
        self.add(hash="b" * 32, date="2026-11-21 02:00:00")
        self.assertEqual(self.v3.download_added_date("a" * 32, "FixtureDownloader", self.connection),
                         "2026-01-02 03:04:05")

    def test_latest_matching_download_bounds_reused_hash_without_old_library_scan(self):
        self.add()
        self.add(date="2026-06-02 03:04:05")
        self.assertEqual(self.v3.download_added_date("a" * 32, "FixtureDownloader", self.connection),
                         "2026-06-02 03:04:05")

    def test_absent_or_malformed_history_fails_closed(self):
        for value in (None, "2026-2-02 03:04:05", "2026-02-30 03:04:05", "2026-01-02T03:04:05", ""):
            with self.subTest(date=value):
                self.db.execute("DELETE FROM downloadhistory")
                self.add(date=value)
                with self.assertRaises(ValueError):
                    self.v3.download_added_date("a" * 32, "FixtureDownloader", self.connection)
        with self.assertRaises(ValueError):
            self.v3.download_added_date("c" * 32, "FixtureDownloader", self.connection)

    def test_missing_downloader_only_matches_missing_downloader(self):
        self.add(downloader=None)
        self.add(date="2026-11-21 01:00:00")
        self.assertEqual(self.v3.download_added_date("a" * 32, None, self.connection), "2026-01-02 03:04:05")

    def test_v2_retains_event_time_window_without_database_lookup(self):
        v2 = load_compat("v2.15.6")
        forbidden_lookup = Mock(side_effect=AssertionError("V2 event must not query history"))
        with patch.object(v2.time, "time", return_value=1770000000):
            result = v2.download_added_date("a" * 32, "FixtureDownloader", forbidden_lookup)
        self.assertEqual(datetime.strptime(result, "%Y-%m-%d %H:%M:%S").timestamp(), 1769999940)
        forbidden_lookup.assert_not_called()


class NativePageActionTests(unittest.TestCase):
    def setUp(self):
        self.v3 = load_compat("v3.2.9")

    def test_native_status_and_commands_have_complete_envelopes(self):
        for payload in ({"status": "ready", "candidate_count": 2},
                        {"success": True, "message": "Queued"},
                        {"success": False, "message": "Scope unavailable"}):
            with self.subTest(payload=payload):
                calls = []
                def endpoint():
                    calls.append(True)
                    return payload
                action = self.v3.native_page_action(endpoint)
                self.assertEqual(calls, [])
                result = action()
                self.assertEqual(calls, [True])
                self.assertEqual(set(result), {"success", "message", "data"})
                self.assertIs(result["data"], payload)
                self.assertEqual(result["success"], payload.get("success", True))
                self.assertEqual(result["message"], payload.get("message", ""))

    def test_invalid_native_action_response_is_not_reported_as_success(self):
        with self.assertRaises(ValueError):
            self.v3.native_page_action(lambda: None)()


if __name__ == "__main__":
    unittest.main()
