"""Protect native handoff and episode provenance across MoviePilot versions."""
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins.v2/ptsubtitlefollowup"))
from fusion.gap_host import GapHost, _episodes, release


def parse(name):
    season = re.search(r"S(\d+)", name)
    group = re.search(r"-(Origin|Spare|Other)", name)
    return NS(name=name.partition(".")[0], resource_team=group[1] if group else None,
              resource_pix="1080p", resource_type="WEB", video_encode="HEVC",
              season_list=[int(season[1])] if season else [], begin_season=int(season[1]) if season else None,
              episode_list=[int(n) for n in re.findall(r"E(\d+)", name)])


class GapHostTests(unittest.TestCase):
    def setUp(self):
        self.host = object.__new__(GapHost)
        self.host.meta = parse
        self.host.downloads = Mock()
        self.host._receipt_cache = {}

    def test_contiguous_imports_skip_filesystem_scan(self):
        self.host.v3 = True
        self.host._read = Mock(return_value=[{"episodes": "E01-E12"}])
        self.host._inventory = Mock(side_effect=AssertionError("unexpected disk access"))
        sub = NS(media_source="themoviedb", media_id="42", season=1, start_episode=1,
                 episode_group=None, best_version=False, best_version_full=False)
        self.assertIsNone(self.host._snapshot(sub, archived=False))
        self.host._inventory.assert_not_called()

    def test_waiting_for_downloader_confirmation_protects_episodes_and_whole_season(self):
        self.host.v3 = True
        sub = NS(media_id="42", media_source="themoviedb", season=2)
        pending = [object()]
        self.host.downloads.list_pending_downloads.return_value = pending
        covered = Mock(return_value=(False, frozenset({9, 10})))
        modules = {
            "app.application.download.pending": NS(pending_blocked_tv_scope=covered),
            "app.schemas.media": NS(build_media_key=lambda provider, media_id: f"tmdb:{media_id}"),
            "app.schemas.types": NS(MediaType=NS(TV=NS(value="电视剧"))),
        }
        with patch.dict(sys.modules, modules):
            self.assertEqual(self.host._pending_episodes(sub), {9, 10})
            self.host.downloads.list_pending_downloads.assert_called_once_with(target_keys=["电视剧:tmdb:42"])
            covered.assert_called_once_with(pending, "电视剧:tmdb:42", 2)
            covered.return_value = (True, frozenset())
            with self.assertRaisesRegex(ValueError, "待确认"):
                self.host._pending_episodes(sub)
        # Earlier releases do not track pending submissions.
        self.host.downloads = NS()
        self.assertEqual(self.host._pending_episodes(sub), set())

    def test_scope_and_actual_manifest(self):
        self.assertEqual(_episodes("E09E11-E13"), {9,11,12,13})
        self.assertFalse(_episodes("E09-E01"))
        title = "Fixture.S01E09-Origin"
        ctx = NS(torrent_info=object(), meta_info=parse(title))
        offer = dict(zip(("group", "spec"), release(parse(title))))
        def check(files, content=b"torrent"):
            self.host.downloads.download_torrent.return_value = (content, "", files)
            return self.host._verified_content(ctx, offer, {9}, 1)
        self.assertEqual(check([title+".mkv", "zh.srt"]), b"torrent")
        for second in ("Fixture.S01E10-Origin.mkv", "OtherShow.S01E09-Origin.mkv", "disk.iso",
                       "Fixture.S01E09-Spare.mkv", "Fixture.S02E09-Origin.mp4"):
            with self.subTest(second=second):
                self.assertIsNone(check([title+".mkv", second]))
        self.assertIsNone(check([title+".mkv"], content="magnet:fixture"))
        self.assertIsNone(check(["zh.srt"]))

    def test_same_path_needs_live_object_and_one_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "Fixture.S01E09.mkv"
            other = Path(temp) / "Another.S01E01.mkv"
            other.write_bytes(b"another show sharing this directory")
            def write(group, data, stamp):
                path.write_bytes(data)
                os.utime(path, (stamp,stamp))
                st = path.stat()
                return dict(episodes="E09", dest=str(path), dest_storage="local",
                            src=f"Fixture.S01E09-{group}.mkv", download_hash=group, downloader="down",
                            dest_fileitem={"size":st.st_size,"modify_time":st.st_mtime})
            a = write("Origin", b"first", 1000000000)
            b = write("Spare", b"second", 1000000100)
            def inventory(rows):
                return self.host._inventory(rows, NS(name="Fixture",season=1))
            anchor, groups, tasks, _, _ = inventory([a,b])
            self.assertEqual((anchor[0], groups, tasks), ("origin", {9:"spare"}, {9:"Spare"}))
            self.assertEqual(inventory([a,{**b,"src":"Fixture.S01E09-Origin.mkv"},b])[1],{9:None})
            path.write_bytes(b"manual overwrite")
            self.assertEqual(inventory([a,b])[1],{9:None})

    def test_only_confirmed_discard_allows_retry(self):
        snap = {"season":1,"active":[]}
        self.host._read = Mock(return_value=[{"filepath":"Fixture.S01E09-Origin.mkv","state":0}])
        self.host._task_absent = Mock(return_value=False)
        self.host._live_receipt = Mock(return_value="unknown")
        self.assertEqual(self.host.receipt_status(snap, "hash", "down", 9), "unknown")
        self.host._receipt_cache.clear()
        self.host._task_absent.return_value=True
        self.assertEqual(self.host.receipt_status(snap, "hash", "down", 9), "discarded")
        self.assertEqual(self.host.receipt_status(snap, "hash", "down", 10), "unknown")
        self.assertEqual(self.host._read.call_count,2)

    def test_download_history_is_not_an_active_or_successful_transfer(self):
        task = "a" * 40
        jobs = Mock()
        jobs.get_all_torrent_hashes.return_value = set()
        transfer = NS(TransferChain=lambda: NS(jobview=jobs))
        with patch.dict(sys.modules, {"app.chain.transfer":transfer}):
            self.host.downloads.list_torrents.return_value = []
            self.assertEqual(self.host._live_receipt(task,"down"),"unknown")
            self.host.downloads.list_torrents.return_value = [NS(hash=task,downloader="down",state="downloading")]
            self.assertEqual(self.host._live_receipt(task,"down"),"active")
            jobs.get_all_torrent_hashes.return_value = {task}
            jobs.is_torrent_done.return_value = True
            self.assertEqual(self.host._live_receipt(task,"down"),"transferred")


if __name__ == "__main__":
    unittest.main()
