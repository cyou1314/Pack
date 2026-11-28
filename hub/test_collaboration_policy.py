import unittest
from collaboration_policy import plan_collaboration, suggest_roles


class CollaborationPolicyTests(unittest.TestCase):
    def plan(self, **overrides):
        args = dict(actor_role="owner", mode="focus",
                    eligible={"a", "b", "c", "d", "e"}, leader="a")
        args.update(overrides)
        return plan_collaboration(**args)

    def test_focus_never_broadcasts(self):
        steps = self.plan()
        self.assertEqual([(s.member, s.after) for s in steps], [("a", ())])

    def test_compare_parallel_then_synthesis(self):
        steps = self.plan(mode="compare", workers=("b", "c"))
        self.assertEqual(len(steps), 4)
        self.assertTrue(all(not s.after for s in steps[:-1]))
        self.assertEqual(steps[-1].member, "a")
        self.assertEqual(steps[-1].after, tuple(s.key for s in steps[:-1]))

    def test_team_requires_review_before_final(self):
        steps = self.plan(mode="team", workers=("b", "c"), reviewer="d")
        self.assertEqual([s.member for s in steps], ["b", "c", "d", "a"])
        self.assertEqual(steps[2].after, ("work-0", "work-1"))
        self.assertEqual(steps[3].after, ("work-0", "work-1", "review"))

    def test_fail_closed(self):
        for overrides in (
            dict(actor_role="member"),
            dict(actor_role="dispatcher"),
            dict(leader="outside"),
            dict(mode="compare", workers=("outside",)),
            dict(mode="compare", workers=("a",)),
            dict(mode="compare", workers=("b", "b")),
            dict(mode="focus", workers=("b",)),
            dict(mode="team", workers=("b",)),
            dict(mode="team", workers=("b",), reviewer="b"),
            dict(mode="team", workers=("b",), reviewer="outside"),
            dict(mode="compare", workers=("b",), max_calls=2),
            dict(max_calls=0),
            dict(max_calls=9),
            dict(max_calls=True),
            dict(mode="free"),
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    self.plan(**overrides)

    def test_max_team_budget(self):
        self.assertEqual(len(self.plan(mode="team", workers=("b", "c", "d"),
                                      reviewer="e", max_calls=5)), 5)
        with self.assertRaises(ValueError):
            self.plan(mode="team", workers=("b", "c", "d"),
                      reviewer="e", max_calls=4)

class RouteEfficiencyTests(unittest.TestCase):
    def candidate(self, member_id, provider, pct=20, **changes):
        row = dict(member_id=member_id, provider=provider, account_ref=f"ref-{member_id}",
                   online=True, queue=0, active_plan=False, execution_allowed=True,
                   quota_state="available", quota_pct=pct)
        row.update(changes)
        return row

    def test_compare_prefers_distinct_ecosystems_at_same_readiness(self):
        rows = [self.candidate(1,"chatgpt",1), self.candidate(2,"openai",2),
                self.candidate(3,"gemini-api",30), self.candidate(4,"doubao",40)]
        route = suggest_roles(mode="compare",candidates=rows,worker_count=2)
        self.assertEqual(route,dict(leader=1,workers=[3,4],reviewer=None,planned_calls=4))
        self.assertEqual(route,suggest_roles(mode="compare",candidates=list(reversed(rows)),worker_count=2))

    def test_known_quota_precedes_diversity_and_focus_unchanged(self):
        rows = [self.candidate(1,"openai",1),self.candidate(2,"chatgpt",2),
                self.candidate(3,"gemini",None,quota_state="unknown")]
        self.assertEqual(suggest_roles(mode="focus",candidates=rows)["leader"],1)
        self.assertEqual(suggest_roles(mode="compare",candidates=rows,allow_unknown_quota=True)["workers"],[2])

    def test_disabled_busy_and_duplicate_refs_never_fill_roles(self):
        rows = [self.candidate(1,"openai",1,account_ref="REF"),
                self.candidate(2,"gemini",2,account_ref="ref"),
                self.candidate(3,"doubao",0,execution_allowed=False),
                self.candidate(4,"gemini-api",0,queue=1)]
        self.assertIsNone(suggest_roles(mode="compare",candidates=rows))
        rows.append(self.candidate(5,"volcengine",50))
        self.assertEqual(suggest_roles(mode="compare",candidates=rows)["workers"],[5])


if __name__ == "__main__":
    unittest.main()
