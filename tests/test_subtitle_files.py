import concurrent.futures
import importlib.util
import io
from pathlib import Path
import stat
import sys
import tempfile
import unittest
import zipfile


SOURCE = Path(__file__).resolve().parents[1] / "plugins.v2" / "ptsubtitlefollowup" / "subtitle_files.py"
spec = importlib.util.spec_from_file_location("subtitle_files", SOURCE)
sf = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = sf
spec.loader.exec_module(sf)


def srt(text="这是匹配影片的中文字幕，正在安全测试。", end="00:00:04,000"):
    return f"1\n00:00:01,000 --> {end}\n{text}\n\n2\n00:00:05,000 --> 00:00:07,000\n这是另外一行对白的字幕。\n".encode()


def archive(entries, compression=zipfile.ZIP_DEFLATED):
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=compression) as z:
        for name, data in entries:
            z.writestr(name, data)
    return result.getvalue()


class ContentTests(unittest.TestCase):
    def test_real_srt_chinese_and_original_timing_preserved(self):
        candidate = sf.parse_download("Film.2040.chs.srt", srt(), video_duration=100)[0]
        self.assertEqual(candidate.cue_count, 2)
        self.assertEqual(candidate.last_cue_seconds, 7)
        self.assertIn(b"00:00:01,000 --> 00:00:04,000", candidate.payload)

    def test_explicit_machine_label_is_excluded_without_banning_release_mt(self):
        for name in ("[MT机翻]Film.srt", "Film.AI翻译.srt", "Film.machine-translated.srt"):
            with self.subTest(name=name), self.assertRaises(sf.SubtitleError):
                sf.parse_download(name, srt())
        self.assertEqual(len(sf.parse_download("Film-MT.chs.srt", srt())), 1)

    def test_invalid_times_and_past_video_are_rejected(self):
        for data in (srt(end="00:00:00,000"), srt(end="00:99:01,000"), srt(end="05:00:00,000")):
            with self.assertRaises(sf.SubtitleError):
                sf.parse_download("Film.srt", data, video_duration=60)

    def test_zip_paths_are_checked_even_for_non_subtitles(self):
        for bad in ("../escape.srt", "/tmp/escape.srt", "C:/escape.srt", "folder\\escape.srt", "a/../../escape.txt", "a/file.txt:stream"):
            with self.subTest(bad=bad), self.assertRaises(sf.SubtitleError):
                packed = archive([("good.srt", srt()), (bad, b"bad")])
                # Windows' ZipInfo constructor normalises backslashes while
                # creating a ZIP. Restore the actual unsafe header bytes.
                if "\\" in bad:
                    packed = packed.replace(bad.replace("\\", "/").encode(), bad.encode())
                sf.parse_download("Film.zip", packed)

    def test_zip_symlink_and_duplicate_paths_rejected(self):
        link = zipfile.ZipInfo("unsafe.srt")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with self.assertRaises(sf.SubtitleError):
            sf.parse_download("Film.zip", archive([(link, b"/tmp/elsewhere")]))
        with self.assertRaises(sf.SubtitleError):
            sf.parse_download("Film.zip", archive([("Sub.srt", srt()), ("sub.srt", srt())]))

    def test_zip_bomb_and_many_entries_rejected(self):
        with self.assertRaises(sf.SubtitleError):
            sf.parse_download("Film.zip", archive([("large.txt", b"A" * 1000000)]))
        with self.assertRaises(sf.SubtitleError):
            sf.parse_download("Film.zip", archive([(f"{i}.txt", b"x") for i in range(129)]))

    def test_two_versions_are_ambiguous_but_exact_duplicates_are_not(self):
        one = sf.parse_download("Film.2040.chs.srt", srt())[0]
        two = sf.parse_download("Film.other.srt", srt("另一个版本的电影字幕，不能猜测时间轴。"))[0]
        with self.assertRaises(sf.AmbiguousSubtitleError):
            sf.select_candidate([one, two])
        self.assertEqual(sf.select_candidate([one, one]).sha256, one.sha256)

    def test_tv_requires_both_season_and_episode(self):
        self.assertTrue(sf.episode_matches("Show.S02E03.chs.srt", season=2, episode=3))
        self.assertTrue(sf.episode_matches("Show/Season 2/Show.E03.chs.srt", season=2, episode=3))
        self.assertTrue(sf.episode_matches("Show.S02E03-1080p.srt", season=2, episode=3))
        for name in ("Show.E03.srt", "Show.S01E03.srt", "Show.S02E04.srt", "Show.S02E03-E04.srt", "Show.S02E03.S02E04.srt", "Show.S02E03E04.srt"):
            with self.subTest(name=name):
                self.assertFalse(sf.episode_matches(name, season=2, episode=3))
        self.assertFalse(sf.episode_matches("Show.S02E03.srt", season=None, episode=None))

    def test_tv_zip_uses_full_member_path_and_skips_wrong_episode(self):
        data = archive([("Show.S02/Show.E03.srt", srt()), ("Show.S02/Show.E04.srt", srt()), ("Show.S02/Show.E03.eng.srt", b"not a subtitle")])
        parsed = sf.parse_download("Show.S02.zip", data, season=2, episode=3)
        self.assertEqual(len(parsed), 1)
        self.assertIn("E03", sf.select_candidate(parsed, season=2, episode=3).filename)


class SaveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.library = self.root / "library"
        self.library.mkdir()
        self.media = self.library / "虚构影片 (2040).strm"
        self.media.write_text("https://media.invalid/invented", encoding="utf8")
        self.original = self.media.read_bytes()
        self.candidate = sf.parse_download("Film.chs.srt", srt())[0]

    def tearDown(self):
        self.temp.cleanup()

    def test_save_only_adds_one_sidecar_and_never_overwrites(self):
        target = sf.save_sidecar(self.candidate, self.media, [self.library])
        self.assertEqual(target.name, "虚构影片 (2040).zh.srt")
        self.assertEqual(self.media.read_bytes(), self.original)
        with self.assertRaises(sf.ExistingSubtitleError):
            sf.save_sidecar(self.candidate, self.media, [self.library])
        self.assertEqual(target.read_bytes(), self.candidate.payload)
        self.assertFalse(list(self.library.glob(".pt-subtitle-*")))

    def test_outside_root_missing_media_and_traversal_rejected(self):
        outside = self.root / "outside.strm"
        outside.write_text("x")
        for path in (outside, self.library / "missing.strm", self.library / ".." / "outside.strm"):
            with self.subTest(path=path), self.assertRaises(sf.SubtitleError):
                sf.save_sidecar(self.candidate, path, [self.library])
        self.assertFalse((self.root / "outside.zh.srt").exists())

    def test_parallel_writers_only_one_wins(self):
        def write(_):
            try:
                sf.save_sidecar(self.candidate, self.media, [self.library])
                return True
            except sf.ExistingSubtitleError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            outcomes = list(executor.map(write, range(12)))
        self.assertEqual(sum(outcomes), 1)
        self.assertFalse(list(self.library.glob(".pt-subtitle-*")))

    def test_symlink_parent_is_rejected(self):
        link = self.library / "alias"
        outside = self.root / "other"
        outside.mkdir()
        (outside / "Film.strm").write_text("x")
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("OS does not permit creating test symlinks")
        with self.assertRaises(sf.SubtitleError):
            sf.save_sidecar(self.candidate, link / "Film.strm", [self.library])
        self.assertFalse((outside / "Film.zh.srt").exists())


if __name__ == "__main__":
    unittest.main()
