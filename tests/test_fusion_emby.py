"""Offline contract tests: synthetic users/data and a GET-only HTTP fake."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "plugins.v2" / "ptsubtitlefollowup" / "fusion" / "emby.py"
REQUESTS = types.ModuleType("requests")
REQUESTS.RequestException = type("RequestException", (Exception,), {})
REQUESTS.Session = lambda: (_ for _ in ()).throw(AssertionError("Tests must supply a fake session"))
SPEC = importlib.util.spec_from_file_location("offline_fusion_emby", SOURCE)
emby = importlib.util.module_from_spec(SPEC)
with patch.dict(sys.modules, {"requests": REQUESTS}):
    SPEC.loader.exec_module(emby)

USER = "a" * 32
OTHER_USER = "b" * 32
SERVER = "c" * 32
SCOPE = {"mode": "all_visible", "library_ids": []}


def item(identity="1", kind="Movie", userdata=None):
    return {"Id": identity, "Name": "Synthetic title", "Type": kind,
            "ProviderIds": {"Tmdb": "123"}, "Genres": ["Drama"],
            "People": [{"Id": "10", "Name": "Synthetic actor", "Type": "Actor", "Secret": "drop"}],
            "ProductionYear": 2026, "RunTimeTicks": 10000000,
            "UserData": {"Played": True, "IsFavorite": False, "PlayCount": 1,
                         "LastPlayedDate": "2026-01-01T00:00:00Z"} if userdata is None else userdata,
            "Path": "/fixture/library/invented.mkv", "MediaSources": [{"Path": "secret"}],
            "RemoteEndPoint": "private", "OtherUsers": [{"Id": OTHER_USER}]}


def page(items, total=None):
    return {"Items": items, "TotalRecordCount": len(items) if total is None else total}


class Response:
    def __init__(self, body=None, status=200, headers=None, chunks=None):
        self.status_code = status
        self.headers = headers or {}
        self.chunks = chunks if chunks is not None else [json.dumps(body).encode()]
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True

    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


class Session:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.headers = {}
        self.trust_env = True
        self.closed = False
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("Unexpected HTTP request")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def close(self):
        self.closed = True


class EmbyConnectorTests(unittest.TestCase):
    def client(self, responses=(), **kwargs):
        session = Session(responses)
        with patch.object(REQUESTS, "Session", return_value=session):
            client = emby.EmbyReadOnlyClient({"host": "http://fixture.invalid/emby", "apikey": "synthetic-key"}, **kwargs)
        self.addCleanup(client.close)
        return client, session

    def bound(self, responses=(), **kwargs):
        client, session = self.client([
            Response({"Id": SERVER.upper(), "ServerName": "Synthetic server", "Version": "4.10.2.0"}),
            Response({"Id": USER.upper(), "Name": "Synthetic user", "Policy": {"IsDisabled": False}}),
            *responses,
        ], **kwargs)
        client.verify_binding(SERVER, USER)
        return client, session

    def assertFailure(self, client, session, code, function):
        with self.assertRaises(emby.EmbyReadError) as caught:
            function()
        self.assertEqual(caught.exception.code, code)
        self.assertFalse(client.snapshot_complete)
        self.assertTrue(session.closed)
        self.assertNotIn("synthetic-key", str(caught.exception))
        self.assertNotIn("fixture.invalid", str(caught.exception))
        return caught.exception

    def test_binding_is_case_normalized_and_no_user_directory_is_enumerated(self):
        client, session = self.bound()
        self.assertEqual(client._verified_user_id, USER)
        self.assertEqual(len(session.calls), 2)
        self.assertTrue(session.calls[-1][0].endswith("/Users/" + USER))

    def test_wrong_server_user_or_disabled_binding_fails_closed(self):
        cases = [
            ([Response({"Id": "d" * 32})], "SERVER_MISMATCH"),
            ([Response({"Id": SERVER}), Response({"Id": OTHER_USER, "Policy": {"IsDisabled": False}})], "USER_MISMATCH"),
            ([Response({"Id": SERVER}), Response({"Id": USER, "Policy": {"IsDisabled": True}})], "USER_DISABLED"),
            ([Response({"Id": SERVER}), Response({"Id": USER})], "BINDING_UNVERIFIED"),
        ]
        for responses, code in cases:
            with self.subTest(code=code):
                client, session = self.client(responses)
                self.assertFailure(client, session, code, lambda: client.verify_binding(SERVER, USER))

    def test_unbound_or_invalid_user_never_fetches_items(self):
        for identity, code in ((USER, "BINDING_REQUIRED"), ("admin", "INVALID_USER_ID"), ("../Sessions", "INVALID_USER_ID")):
            client, session = self.client()
            self.assertFailure(client, session, code, lambda: list(client.iter_items(identity, ["Movie"], SCOPE)))
            self.assertEqual(session.calls, [])

    def test_success_allows_only_get_user_items_and_minimizes_dto(self):
        client, session = self.bound([Response(page([item()]))])
        iterator = client.iter_items(USER, ["Series", "Movie", "Episode"], SCOPE)
        result = next(iterator)
        self.assertFalse(client.snapshot_complete)
        self.assertNotIn("Path", result)
        self.assertNotIn("Name", result)
        self.assertNotIn("PremiereDate", result)
        self.assertNotIn("RunTimeTicks", result)
        self.assertNotIn("OtherUsers", result)
        self.assertNotIn("MediaSources", result)
        self.assertNotIn("Secret", result["People"][0])
        self.assertEqual(result["UserData"]["LastPlayedDate"], "2026-01-01T00:00:00Z")
        with self.assertRaises(StopIteration):
            next(iterator)
        self.assertTrue(client.snapshot_complete)
        self.assertEqual(client.snapshot_count, 1)
        url, request = session.calls[-1]
        self.assertTrue(url.endswith(f"/Users/{USER}/Items"))
        self.assertNotIn("Sessions", url)
        self.assertFalse(request["allow_redirects"])
        self.assertTrue(request["stream"])
        self.assertEqual(request["params"]["Limit"], 200)
        self.assertNotIn("IsPlayed", request["params"])
        self.assertNotIn("Path", request["params"]["Fields"].split(","))
        self.assertTrue({"UserDataPlayCount", "UserDataLastPlayedDate", "ProviderIds", "Genres", "People", "ProductionYear"}.issubset(request["params"]["Fields"].split(",")))
        self.assertNotIn("apikey", request["params"])

    def test_unselected_and_empty_selected_scope_are_denied(self):
        for scope in (None, {}, {"mode": "unselected"}, {"mode": "selected_ids", "library_ids": []},
                      {"mode": "all_visible", "library_ids": ["1"]}):
            client, session = self.bound()
            with self.assertRaises(emby.EmbyReadError):
                list(client.iter_items(USER, ["Movie"], scope))
            self.assertTrue(session.closed)
            self.assertEqual(len(session.calls), 2)

    def test_selected_libraries_require_current_visibility(self):
        views = Response({"Items": [{"Id": "10", "Name": "Library", "CollectionType": "movies", "Path": "private"}]})
        client, session = self.bound([views, Response(page([item()]))])
        scope = {"mode": "selected_ids", "library_ids": ["10"]}
        self.assertEqual(len(list(client.iter_items(USER, ["Movie"], scope))), 1)
        self.assertEqual(session.calls[-1][1]["params"]["ParentId"], "10")
        other, other_session = self.bound([Response({"Items": []})])
        self.assertFailure(other, other_session, "LIBRARY_NOT_VISIBLE", lambda: list(other.iter_items(USER, ["Movie"], scope)))

    def test_duplicates_and_foreign_userdata_are_rejected(self):
        for rows, code in (([item(), item()], "DUPLICATE_ITEM"),
                           ([item(userdata={"UserId": OTHER_USER})], "USER_MISMATCH"),
                           ([item(userdata=[])], "USERDATA_UNAVAILABLE")):
            client, session = self.bound([Response(page(rows))])
            self.assertFailure(client, session, code, lambda: list(client.iter_items(USER, ["Movie"], SCOPE)))

    def test_route_guard_rejects_sessions_and_arbitrary_urls_without_request(self):
        for route in ("/Sessions", "/Sessions/Playing/Progress", "https://evil.invalid", "/Users/../Sessions"):
            client, session = self.client()
            self.assertFailure(client, session, "FORBIDDEN_ROUTE", lambda: client._get(route))
            self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
