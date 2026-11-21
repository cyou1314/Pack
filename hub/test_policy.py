#!/usr/bin/env python3
"""Direct unit tests for pure Hub policy primitives."""
import unittest

from core import policy


class PolicyTests(unittest.TestCase):
    def test_action_tag_uses_first_supported_marker(self):
        self.assertEqual(policy.action_tag("[ENG] [M4A-image] review"), "[ENG]")
        self.assertEqual(policy.action_tag("no marker"), "")
        self.assertEqual(policy.action_tag(None), "")

    def test_backoff_is_exponential_and_uses_unknown_fallback(self):
        self.assertEqual(policy.backoff_seconds("quota", 1), 1800)
        self.assertEqual(policy.backoff_seconds("quota", 2), 3600)
        self.assertEqual(policy.backoff_seconds("transient", 3), 240)
        self.assertEqual(policy.backoff_seconds("unrecognized", 0), 300)

    def test_member_access_mode_is_backward_compatible(self):
        self.assertEqual(policy.member_access_mode({"name": "legacy"}), "full")
        self.assertEqual(policy.member_access_mode({"access_mode": None}), "full")
        self.assertEqual(policy.member_access_mode({"access_mode": "invalid"}), "full")
        self.assertEqual(policy.member_access_mode({"access_mode": "readonly"}), "readonly")

    def test_executor_marker_pinning(self):
        self.assertTrue(policy.is_executor_pinned("  [ENG] inspect", "ordinary body"))
        self.assertTrue(policy.is_executor_pinned("ordinary title", "contains [ENG-image] marker"))
        self.assertFalse(policy.is_executor_pinned("ordinary title", "ordinary body"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
