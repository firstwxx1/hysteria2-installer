import unittest
from unittest.mock import patch

import portal


class OutboundRuleTests(unittest.TestCase):
    def test_direct_rule_stays_direct_when_warp_is_installed(self):
        with patch.object(portal, "_existing_outbound_names", return_value=["direct_ipv4", "warp_socks"]):
            acl = portal.build_acl_block(
                "rules", [{"domain": "example.com", "outbound": "direct"}], [], "direct_ipv4")
        self.assertIn("direct(suffix:example.com)", acl)
        self.assertNotIn("warp_socks(suffix:example.com)", acl)

    def test_configured_builtin_and_custom_targets_are_preserved(self):
        with patch.object(portal, "_existing_outbound_names", return_value=["warp_socks"]):
            builtin = portal.build_acl_block(
                "rules", [{"domain": "example.com", "outbound": "warp_socks"}], [], "direct")
            custom = portal.build_acl_block(
                "rules", [{"domain": "example.com", "outbound": "proxy_1"}],
                [{"name": "proxy_1", "type": "socks5"}], "direct")
        self.assertIn("warp_socks(suffix:example.com)", builtin)
        self.assertIn("proxy_1(suffix:example.com)", custom)

    def test_missing_legacy_target_uses_existing_fallback(self):
        with patch.object(portal, "_existing_outbound_names", return_value=["warp_socks"]):
            acl = portal.build_acl_block(
                "rules", [{"domain": "example.com", "outbound": "removed_proxy"}], [], "direct")
        self.assertIn("warp_socks(suffix:example.com)", acl)

    def test_target_validation_accepts_only_real_or_acl_builtin_names(self):
        self.assertTrue(portal.outbound_target_exists("direct", [], []))
        self.assertTrue(portal.outbound_target_exists("reject", [], []))
        self.assertTrue(portal.outbound_target_exists(
            "proxy_1", [{"name": "proxy_1"}], []))
        self.assertTrue(portal.outbound_target_exists("warp_socks", [], ["warp_socks"]))
        self.assertFalse(portal.outbound_target_exists("typo_proxy", [], ["warp_socks"]))


if __name__ == "__main__":
    unittest.main()
