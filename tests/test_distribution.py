"""Both MoviePilot generations receive the same reviewed plugin payload."""
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
import zipfile


REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("va_build", REPO / "tools/build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class DistributionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.plugin = self.root / "plugins.v2" / build.PID.lower()
        self.mirror = self.root / "plugins.v3" / build.PID.lower()
        names = (REPO / "tools/package-files.txt").read_text(encoding="utf-8").splitlines()
        paths = ["package.v2.json", "package.v3.json", "tools/package-files.txt", "icons/viewing-assistant.png"]
        paths.extend("frontend/" + name for name in build.FRONTEND)
        paths.extend("plugins.v2/ptsubtitlefollowup/" + name for name in names)
        for name in paths:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / name, target)
        for name, value in (("ROOT", self.root), ("PLUGIN", self.plugin), ("V3_PLUGIN", self.mirror)):
            patcher = patch.object(build, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Fixtures use the checked-in authoring sources; real CI separately checks the generated entry.
        (self.plugin / build.ENTRY).write_bytes(build.render_frontend())

    def update_v3(self, **changes):
        path = self.root / "package.v3.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data[build.PID].update(changes)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_shared_release_and_source_fallback_have_identical_payloads(self):
        with self.assertRaisesRegex(ValueError, "--write-v3"):
            build.package()
        filename, payload, sums = build.package(write_v3=True)
        self.assertEqual((filename, payload, sums), build.package())
        version = build.release_listing()["version"]
        self.assertEqual(filename, f"ptsubtitlefollowup_v{version}.zip")
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = (self.root / "tools/package-files.txt").read_text(encoding="utf-8").splitlines()
            self.assertEqual(set(archive.namelist()), set(names))
            for name in names:
                self.assertEqual(archive.read(name), (self.mirror / name).read_bytes())
        self.assertNotIn("AGENTS.md", names)

    def test_market_indexes_cannot_point_to_different_versions(self):
        self.update_v3(version="99.99.99")
        with self.assertRaisesRegex(ValueError, "same release metadata"):
            build.package(write_v3=True)

    def test_untested_host_ranges_or_free_threaded_runtime_are_rejected(self):
        self.update_v3(system_version=">=3.2.9")
        with self.assertRaisesRegex(ValueError, "host compatibility"):
            build.package(write_v3=True)
        self.update_v3(system_version="==3.2.9", v3t=True)
        with self.assertRaisesRegex(ValueError, "runtime compatibility"):
            build.package(write_v3=True)

    def test_release_tag_must_match_both_market_indexes(self):
        with self.assertRaisesRegex(ValueError, "Tag does not match"):
            build.package("PtSubtitleFollowup_v0.0.0", write_v3=True)

    def test_stale_source_fallback_blocks_publishing_until_regenerated(self):
        expected = build.package(write_v3=True)
        (self.mirror / "host_compat.py").write_text("# stale fallback\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "--write-v3"):
            build.package()
        self.assertEqual(build.package(write_v3=True), expected)

    def test_unreviewed_files_are_never_silently_removed_or_published(self):
        build.package(write_v3=True)
        extra = self.mirror / "private-debug.json"
        extra.write_text("fixture", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Unexpected file"):
            build.package(write_v3=True)
        self.assertEqual(extra.read_text(encoding="utf-8"), "fixture")


if __name__ == "__main__":
    unittest.main()
