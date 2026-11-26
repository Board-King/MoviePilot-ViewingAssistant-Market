"""Missing-episode coordination, using an isolated journal and synthetic host."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugins.v2/ptsubtitlefollowup"))
from fusion.gaps import GapService, GRACE_SECONDS, SEARCH_INTERVAL


def offer(group, episodes, *, spec="1080p-web", selectable=False):
    token = group + "-" + ",".join(map(str, episodes))
    return {"group": group, "spec": spec, "token": token, "episodes": episodes,
            "title": "Fixture " + token, "selectable": selectable}


class Host:
    def __init__(self):
        present = [*range(1, 9), 11, 12]
        self.snap = {"key":"fixture-season", "title":"Fixture", "season":2, "first_episode":1,
                     "present":present, "active":[], "group":"A", "spec":"1080p-web",
                     "episode_groups":{ep:"A" for ep in present},
                     "episode_tasks":{ep:f"{ep + 2000:040x}" for ep in present}}
        self.receipts = {}
        self.resources, self.calls = [], []
        self.searches, self.outcome = 0, "accepted"

    def snapshots(self, resume=()):
        return [deepcopy(self.snap)]

    def search(self, _snap, include_alternatives=False, allowed=lambda: True):
        self.searches += 1
        return [r for r in self.resources if include_alternatives or r["group"] == self.snap["group"]]

    def dispatch(self, _snap, resource, episodes, mode, allowed):
        if not allowed():
            return "not_sent"
        self.calls.append((resource["group"], episodes, mode))
        if self.outcome == "crash":
            raise OSError("handoff timed out")
        if self.outcome == "accepted":
            task = f"{len(self.calls):040x}"
            for ep in episodes:
                self.receipts[ep, resource["group"]] = task
            return {"outcome": "accepted", "task": task, "downloader": "fixture"}
        return self.outcome

    def import_episodes(self, episodes, group):
        self.snap["present"] = sorted(set(self.snap["present"]) | set(episodes))
        self.snap["episode_groups"].update({ep:group for ep in episodes})
        self.snap["episode_tasks"].update({ep:self.receipts.get((ep,group), "f" * 40) for ep in episodes})


class GapsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name)
        self.host = Host()
        self.now = 1000
        self.config = {"gap_enabled":True}
        self.restart()

    def restart(self):
        self.service = GapService(lambda:self.path, lambda:self.host, current_config=lambda:self.config,
                                  clock=lambda:self.now)
        self.service.configure(self.config)

    def tick(self, delta=0):
        self.now += delta
        self.service.tick()
        self.assertNotEqual(self.service.status()["status"], "error")
        return self.service.status()["seasons"]

    def test_grace_and_latest_observed_gap_survive_restart(self):
        self.host.resources = [offer("B", list(range(1,13)),selectable=True)]
        first, = self.tick()
        self.assertEqual(first["missing"], [9,10])
        self.assertEqual(first["deadline"], self.now + GRACE_SECONDS)
        self.restart()
        self.tick(GRACE_SECONDS - 1)
        self.assertEqual(self.host.calls, [])
        self.tick(1)
        self.assertEqual(self.host.calls, [("B",(9,10),"borrow")])

    def test_original_priority_and_active_episodes_are_excluded(self):
        self.host.snap["active"] = [9]
        self.host.resources = [offer("B", list(range(1,13)),selectable=True), offer("A",[9,10],selectable=True),
                               offer("A",[10],spec="720p")]
        self.tick()
        self.assertEqual(self.host.calls,[("A",(10,),"original")])
        self.restart()
        self.tick(GRACE_SECONDS + 20)
        self.assertEqual(len(self.host.calls),1)
        self.host.import_episodes([9,10],"A")
        self.assertEqual(self.tick(),[])

    def test_old_hole_can_borrow_alone_while_new_hole_waits(self):
        self.host.import_episodes([10], "A")
        self.host.resources = [offer("B",[9,13],selectable=True),offer("C",[9],spec="720p")]
        self.tick()
        self.host.import_episodes([14],"A")
        self.tick(GRACE_SECONDS)
        self.assertEqual(self.host.calls,[("B",(9,),"borrow")])
        self.tick(SEARCH_INTERVAL)
        self.assertEqual(len(self.host.calls),1)
        self.tick(GRACE_SECONDS)
        self.assertEqual(self.host.calls[-1],("B",(13,),"borrow"))

    def test_changed_evidence_for_first_group_blocks_new_dispatch(self):
        self.tick()
        self.host.snap["group"] = "B"
        self.host.resources = [offer("C", [9])]
        row, = self.tick(GRACE_SECONDS)
        self.assertEqual((row["group"], row["state"]), ("A", "blocked"))
        self.assertEqual(self.host.calls, [])

    def test_manual_copy_of_same_backup_group_is_not_replaced(self):
        self.host.resources = [offer("B", [9])]
        self.tick()
        self.tick(GRACE_SECONDS)
        self.host.import_episodes([9], "B")
        self.host.snap["episode_tasks"][9] = "e" * 40
        self.host.resources = [offer("A", [9])]
        row, = self.tick(SEARCH_INTERVAL)
        self.assertEqual((row["borrowed"], row["review_episodes"]), ([], [9]))
        self.assertEqual(self.host.calls, [("B", (9,), "borrow")])

    def test_manual_change_after_confirmed_borrow_stops_automatic_return(self):
        self.host.resources = [offer("B", [9])]
        self.tick()
        self.tick(GRACE_SECONDS)
        self.host.import_episodes([9], "B")
        self.assertEqual(self.tick()[0]["borrowed"], [9])
        self.host.snap["episode_tasks"][9] = "e" * 40
        self.host.resources = [offer("A", [9])]
        self.restart()
        row, = self.tick(SEARCH_INTERVAL)
        self.assertEqual((row["returning"], row["review_episodes"]), ([], [9]))
        self.assertEqual(self.host.calls, [("B", (9,), "borrow")])

    def test_unverified_first_offer_yields_to_the_next_resource_after_restart(self):
        self.host.resources = [offer("A", [9]), {**offer("A", [9]), "token": "verified-mirror"}]
        tokens = []
        def attempt(_snap, resource, _episodes, _mode, _allowed):
            tokens.append(resource["token"])
            return "unverified_resource" if len(tokens) == 1 else "accepted"
        self.host.dispatch = attempt
        self.tick()
        self.restart()
        self.tick(SEARCH_INTERVAL)
        self.assertEqual(tokens, ["A-9", "verified-mirror"])

    def test_lend_only_holes_then_restore_each_original_after_verified_import(self):
        self.host.resources = [offer("B",[9,10])]
        self.tick()
        self.tick(GRACE_SECONDS)
        self.assertEqual(self.host.calls,[("B",(9,10),"borrow")])
        self.restart()
        self.host.import_episodes([9,10],"B")
        row, = self.tick()
        self.assertEqual(row["state"],"borrowed")
        self.assertEqual(row["borrowed"],[9,10])
        self.host.resources = [offer("A",[9]),offer("C",[10]),offer("A",[10],spec="2160p")]
        self.tick(SEARCH_INTERVAL)
        self.assertEqual(self.host.calls[-1],("A",(9,),"restore"))
        self.tick(SEARCH_INTERVAL)
        self.assertEqual(len(self.host.calls),2)
        self.host.import_episodes([9],"A")
        self.host.resources = [offer("A",[10])]
        self.tick(SEARCH_INTERVAL)
        self.assertEqual(self.host.calls[-1],("A",(10,),"restore"))
        self.host.import_episodes([10],"A")
        row, = self.tick()
        self.assertEqual(row["state"],"restored")
        self.assertEqual(row["borrowed"],[])
        self.assertEqual(row["restored"],[9,10])
        self.restart()
        self.assertEqual(self.tick()[0]["restored"],[9,10])

    def test_archived_only_restores_verified_borrow_and_deleted_never_dispatches(self):
        self.host.snap["resume"] = {"sid":1, "key":"fixture-season","media_id":"7","sensitive":"secret"}
        self.host.resources = [offer("B",[9])]
        self.tick()
        self.tick(GRACE_SECONDS)
        self.host.import_episodes([9],"B")
        self.host.snap["archived"] = True
        seen = []
        def archive(resume):
            seen.append(resume)
            return [deepcopy(self.host.snap)] if resume else []
        self.host.snapshots = archive
        self.host.resources = [offer("A",[9]),offer("A",[10]),offer("B",[10])]
        self.restart()
        self.tick(SEARCH_INTERVAL)
        self.assertEqual(self.host.calls,[("B",(9,),"borrow"),("A",(9,),"restore")])
        self.assertEqual(seen[-1], [{"sid":1, "key":"fixture-season", "media_id":"7", "borrowed_intent":True,
                                      "original_group":"A", "original_spec":"1080p-web"}])
        self.host.snapshots = lambda resume: []
        self.assertEqual(self.tick(SEARCH_INTERVAL),[])
        self.assertEqual(len(self.host.calls),2)

    def test_ambiguous_send_persists_and_prevents_duplicate(self):
        self.host.resources = [offer("A",[9,10])]
        self.host.outcome = "crash"
        self.assertEqual(self.tick()[0]["state"],"uncertain")
        self.restart()
        self.host.resources.append(offer("B",list(range(1,13))))
        row, = self.tick(GRACE_SECONDS)
        self.assertEqual(row["state"],"review")
        self.assertEqual(row["review_episodes"],[9,10])
        self.assertEqual(len(self.host.calls),1)

    def test_revoked_during_search_is_not_dispatched(self):
        def search(_snap, include_alternatives=False, allowed=lambda: True):
            self.config["gap_enabled"] = False
            return [offer("A",[9,10])]
        self.host.search = search
        self.tick()
        self.assertEqual(self.host.calls,[])

    def test_overlapping_workers_never_submit_the_same_episode(self):
        gate, resume = threading.Event(), threading.Event()
        def search(_snap, include_alternatives=False, allowed=lambda: True):
            gate.set()
            resume.wait(3)
            return [offer("A",[9])]
        self.host.search = search
        first = self.service
        worker = threading.Thread(target=first.tick)
        worker.start()
        try:
            self.assertTrue(gate.wait(2))
            self.restart()
            self.service.tick()
        finally:
            resume.set()
            worker.join(4)
        self.assertFalse(worker.is_alive())
        self.tick()
        self.assertEqual(self.host.calls,[("A",(9,),"original")])

    def test_no_replacement_of_other_groups_or_unselectable_packs(self):
        self.host.snap["episode_groups"][8] = "C"
        self.host.resources = [offer("A",[9,10,11]),offer("B",list(range(1,13))),offer("A",[8])]
        self.tick()
        self.tick(GRACE_SECONDS)
        self.assertFalse(self.host.calls)
        self.host.resources = [offer("B",[9])]
        self.tick(SEARCH_INTERVAL)
        self.host.import_episodes([9],"B")
        self.tick()
        self.host.import_episodes([9],"C")
        self.host.resources = [offer("A",[9])]
        row, = self.tick(SEARCH_INTERVAL)
        self.assertEqual(row["returning"],[])
        self.assertEqual(row["review_episodes"],[9])
        self.assertEqual(self.host.calls,[("B",(9,),"borrow")])

    def test_only_explicit_discard_receipt_allows_exact_episode_retry(self):
        self.host.resources = [offer("A",[9,10],selectable=True)]
        digest = "f" * 40
        self.host.outcome = {"outcome":"accepted","task":digest,"downloader":"qb"}
        self.tick()
        self.restart()
        checked = []
        def receipt(_snap, task, downloader, ep):
            checked.append((task,downloader,ep))
            return "discarded" if ep == 9 else "transferred"
        self.host.receipt_status = receipt
        row, = self.tick(SEARCH_INTERVAL)
        self.assertEqual(checked,[(digest,"qb",9),(digest,"qb",10)])
        self.assertEqual(self.host.calls,[("A",(9,10),"original"),("A",(9,),"original")])
        self.assertEqual(row["review_episodes"],[10])

    def test_failed_transfer_with_stale_download_history_requests_review(self):
        self.host.resources = [offer("A", [9])]
        self.tick()
        self.host.snap["active"] = [9]
        self.host.receipt_status = lambda *_: "active"
        self.assertEqual(self.tick(SEARCH_INTERVAL)[0]["review_episodes"], [])
        self.host.receipt_status = lambda *_: "failed"
        row, = self.tick(SEARCH_INTERVAL)
        self.assertEqual((row["state"], row["review_episodes"]), ("review", [9]))
        self.assertEqual(len(self.host.calls), 1)
