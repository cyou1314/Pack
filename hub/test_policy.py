#!/usr/bin/env python3
"""Direct unit tests for pure Hub policy primitives."""
import sqlite3
import unittest

import hub

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

    def test_egress_host_normalizes_scheme_authority_and_idna(self):
        self.assertEqual(
            policy.egress_host("HTTPS://User:pass@ExAmPlE.COM:8443/path"),
            "example.com",
        )
        self.assertEqual(
            policy.egress_host("https://例子.测试/"),
            "xn--fsqu00a.xn--0zwm56d",
        )

    def test_egress_host_rejects_empty_and_non_http_urls(self):
        for value in ("", None, "example.com", "ftp://example.com", "https:///path"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    policy.egress_host(value)

    def test_egress_pattern_normalizes_wildcards_and_rejects_bad_domains(self):
        self.assertEqual(policy.egress_pattern(" *.Example.COM. "), "example.com")
        for value in ("", "localhost", "bad..example.com", "-bad.example.com"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    policy.egress_pattern(value)

    def test_host_covers_only_exact_or_dot_boundary_subdomains(self):
        self.assertTrue(policy.host_covers("example.com", "example.com"))
        self.assertTrue(policy.host_covers("api.example.com", "example.com"))
        self.assertFalse(policy.host_covers("notexample.com", "example.com"))

    def test_task_hosts_extracts_lowercase_candidates(self):
        self.assertEqual(
            policy.task_hosts("Open https://Docs.Example.com/path and API.example.net."),
            {"docs.example.com", "api.example.net"},
        )

    def test_approval_rule_candidates_preserve_specificity_order(self):
        self.assertEqual(
            policy.approval_rule_candidates("[ENG]", "alice"),
            (("[ENG]", "alice"), ("[ENG]", ""), ("", "alice"), ("", "")),
        )

    def test_egress_rule_selection_prefers_exact_then_deepest_match(self):
        rows = [
            {"id": 1, "pattern": "example.com"},
            {"id": 2, "pattern": "api.example.com"},
            {"id": 3, "pattern": "notexample.com"},
        ]
        self.assertEqual(policy.select_egress_rule(rows, "api.example.com")["id"], 2)
        self.assertEqual(policy.select_egress_rule(rows, "v2.api.example.com")["id"], 2)
        self.assertIsNone(policy.select_egress_rule(rows, "other.test"))

    def test_hub_egress_rule_wrapper_uses_policy_selector(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE egress_rules(id INTEGER PRIMARY KEY, pattern TEXT, enabled INTEGER)"
        )
        conn.executemany(
            "INSERT INTO egress_rules(id, pattern, enabled) VALUES(?,?,1)",
            [(1, "example.com"), (2, "api.example.com")],
        )
        selected = hub._egress_rule_eval(conn, "api.example.com")
        self.assertEqual(selected["id"], 2)
        conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
