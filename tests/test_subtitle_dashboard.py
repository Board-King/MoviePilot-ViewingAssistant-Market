import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins.v2" / "ptsubtitlefollowup"))
from fusion.subtitle_ui import render_subtitles


class SubtitleDashboardTests(unittest.TestCase):
    def test_marks_are_file_outcomes_even_when_the_rest_of_a_series_is_pending(self):
        task = {"title": "Test Series", "status": "waiting_subtitle", "targets": {
            "/private/one": {"status": "saved", "season": 2, "episode": 1, "refresh": "Emby刷新未确认"},
            "/private/two": {"status": "existing", "season": 2, "episode": 2},
            "/private/three": {"status": "waiting", "season": 2, "episode": 3}}}
        card = render_subtitles([task], True, rich=True)
        record, = card["content"][1]["content"][0]["subtitle_records"]
        self.assertEqual([(m[0], m[1]) for m in record["marks"]],
                         [("saved", "S02E01"), ("existing", "S02E02"), ("waiting_subtitle", "S02E03")])
        self.assertEqual(record["marks"][0][3], "Emby刷新未确认")
        self.assertNotIn("/private/", json.dumps(card))
        fallback = render_subtitles([task], True, rich=False)
        self.assertIn("S02E03", str(fallback))
        self.assertNotIn("subtitle_records", str(fallback))

    def test_no_target_records_preserve_status_and_do_not_invent_completed_files(self):
        tasks = [{"status": s, "title": s} for s in ("waiting_transfer", "retry", "expired", "existing", "saved", "other")]
        report = render_subtitles(tasks, False, rich=True)["content"][1]["content"][0]["subtitle_records"]
        self.assertEqual([r["marks"][0][0] for r in report],
                         ["waiting_transfer", "retry", "expired", "existing", "saved", "unknown"])
