import unittest
from collaboration_policy import plan_collaboration


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


if __name__ == "__main__":
    unittest.main()
