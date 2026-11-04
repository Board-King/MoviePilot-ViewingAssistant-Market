"""Synthetic filesystem/mount and MP path contracts. Never inspect a real NAS."""
import copy
import importlib.util
from pathlib import Path, PurePosixPath
import stat
from types import SimpleNamespace
import unittest
from unittest.mock import patch

FILE = Path(__file__).resolve().parents[1] / "plugins.v2/ptsubtitlefollowup/fusion/execution_space.py"
spec = importlib.util.spec_from_file_location("test_space_domain", FILE)
space = importlib.util.module_from_spec(spec)
spec.loader.exec_module(space)
MIN = space.MINIMUM_FREE_BYTES


class SpaceTests(unittest.TestCase):
    def setUp(self):
        self.dirs = [{"media_type": "电影", "media_category": "明确的类别", "storage": "local", "download_path": "/media/movies"},
                     {"media_type": None, "storage": "local", "download_path": "/array/general"},
                     {"media_type": "电视剧", "storage": "u115", "download_path": "/cloud/tv"}]
        self.downloaders = [{"name": "Primary", "enabled": True, "type": "qbittorrent",
                             "path_mapping": [["/media", "/downloads"]], "config": {"host": "http://downloader.invalid:8080",
                             "username": "fixture-viewer", "password": "fixture-marker"}}]
        self.volumes = {path: {"mount": str(n) * 64, "available_bytes": MIN + n}
                        for n, path in enumerate(("/media/movies", "/array/general", "/media/movies/sub"), 1)}
        self.probed = []

    def probe(self, path):
        self.probed.append(path)
        return copy.deepcopy(self.volumes[path])

    def check(self, template=None, verified=True):
        return space.check_movie_download_space(self.dirs, self.downloaders, template,
                                                storage_verified=verified, probe=self.probe)

    def test_all_plausible_movie_volumes_checked_and_lowest_free_wins(self):
        result = self.check()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["locations_checked"], 2)
        self.assertEqual(result["available_bytes"], MIN + 1)
        self.assertEqual(set(self.probed), {"/media/movies", "/array/general"})
        self.volumes["/array/general"]["available_bytes"] = MIN - 1
        low = self.check()
        self.assertEqual(low["status"], "low")
        self.assertEqual(low["fingerprint"], result["fingerprint"])
        for secret in ("/media/", "/array/", "fixture-marker", "downloader.invalid"):
            self.assertNotIn(secret, str(result))

    def test_fingerprint_binds_verified_mount_and_effective_downloader_mapping(self):
        original = self.check()["fingerprint"]
        self.volumes["/array/general"]["mount"] = "c" * 64
        self.assertNotEqual(self.check()["fingerprint"], original)
        self.volumes["/array/general"]["mount"] = "2" * 64
        self.downloaders[0]["path_mapping"][0][1] = "/other-downloads"
        self.assertNotEqual(self.check()["fingerprint"], original)
        self.downloaders[0]["path_mapping"][0][1] = "/downloads"
        self.downloaders[0]["config"]["host"] = "http://alternate-downloader.invalid:8080"
        self.assertNotEqual(self.check()["fingerprint"], original)

    def test_explicit_template_must_stay_inside_known_movie_roots(self):
        self.assertEqual(self.check({"save_path": "local:/media/movies/sub", "downloader": "Primary"})["status"], "ready")
        self.assertEqual(self.probed, ["/media/movies/sub"])
        self.probed.clear()
        for path in ("/media/other", "/media/movies-neighbor", "u115:/media/movies", "/", "/config/downloads",
                     "C:/movies", "//fixture.invalid/movies", "/media/movies/../private"):
            with self.subTest(path=path):
                self.assertEqual(self.check({"save_path": path})["status"], "unknown")
        self.assertFalse(self.probed)
        self.assertEqual(self.check({"save_path": "/media/movies", "downloader": "missing"})["status"], "unknown")

    def test_disabled_missing_unverified_or_invalid_mapping_returns_unknown(self):
        self.assertEqual(self.check(verified=False)["status"], "unknown")
        self.assertFalse(self.probed)
        for mapping in ([["/media", "/mp"], ["/media/movies", "/elsewhere"]],
                        [["/media/movies/split", "/elsewhere"]], [["/media", "smb://mapping.invalid/remote"]],
                        [["/media", "/"]], [["/media", "/downloads"], ["/array/general", "/downloads/movies"]]):
            with self.subTest(mapping=mapping):
                self.downloaders[0]["path_mapping"] = mapping
                self.assertEqual(self.check()["status"], "unknown")
        self.downloaders[0]["enabled"] = False
        self.assertEqual(self.check()["status"], "unknown")
        self.assertFalse(self.probed)

    def test_unknown_counts_or_inaccessible_directory_fail_closed(self):
        for value in (None, -1, True, float("inf")):
            self.volumes["/array/general"]["available_bytes"] = value
            self.assertEqual(self.check()["status"], "unknown")
        del self.volumes["/array/general"]
        self.assertEqual(self.check()["status"], "unknown")

    def test_mount_verification_rejects_container_root_nested_mount_symlink_cloud_and_ro(self):
        state = {"link": None, "device": 23, "flag": 0}

        class FakePath(PurePosixPath):
            def lstat(self):
                return SimpleNamespace(st_mode=stat.S_IFLNK if str(self) == state["link"] else stat.S_IFDIR)
            def stat(self):
                return SimpleNamespace(st_dev=1 if str(self) == "/" else state["device"])

        lookup = lambda location: SimpleNamespace(f_flag=state["flag"], f_bavail=100, f_frsize=4096)
        root = (PurePosixPath("/"), "overlay", "0:1", "/", "overlay", ["rw"])
        data = (PurePosixPath("/media"), "fuse.shfs", "0:23", "/files", "shfs", ["rw"])
        with (patch.object(space, "Path", FakePath), patch.object(space.os, "major", lambda _: 0, create=True),
              patch.object(space.os, "minor", lambda n: n, create=True), patch.object(space.os, "ST_RDONLY", 1, create=True),
              patch.object(space.os, "statvfs", lookup, create=True)):
            valid = space._volume("/media/movies", [root, data])
            self.assertEqual(valid["available_bytes"], 409600)
            cases = [[root], [root, data, (PurePosixPath("/media/movies/parts"), "xfs", "8:2", "/", "dev", ["rw"])],
                     [root, (data[0], "fuse.rclone", *data[2:])], [root, (*data[:-1], ["ro"])],
                     [root, (data[0], data[1], "0:24", *data[3:])], [root, data, data]]
            for mounts in cases:
                with self.subTest(mounts=mounts), self.assertRaises(ValueError):
                    space._volume("/media/movies", mounts)
            for key, value in (("link", "/media"), ("device", 1), ("flag", 1)):
                before = state[key]
                state[key] = value
                with self.subTest(key=key), self.assertRaises(ValueError):
                    space._volume("/media/movies", [root, data])
                state[key] = before


if __name__ == "__main__":
    unittest.main()
