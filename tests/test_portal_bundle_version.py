"""Whole-bundle version comparisons, including incomplete remote observations."""
import unittest
import portal


class PortalBundleVersionTests(unittest.TestCase):
    def setUp(self):
        self.shas = {name: 'a' * 40 for name in portal.PORTAL_BUNDLE_FILES}

    def test_all_files_equal(self):
        result = portal.portal_bundle_version(self.shas, self.shas)
        self.assertEqual(result['status'], 'up_to_date')
        self.assertEqual(result['current'], result['latest'])
        self.assertFalse(result['has_update'])

    def test_each_file_independently_triggers_update(self):
        for name in portal.PORTAL_BUNDLE_FILES:
            with self.subTest(name=name):
                local = dict(self.shas, **{name: 'b' * 40})
                result = portal.portal_bundle_version(local, self.shas)
                self.assertTrue(result['has_update'])
                self.assertEqual(result['changed_files'], [name])
                self.assertNotEqual(result['current'], result['latest'])

    def test_missing_local_file_is_repairable_update(self):
        local = self.shas.copy()
        del local['portal_assets.py']
        result = portal.portal_bundle_version(local, self.shas)
        self.assertTrue(result['has_update'])
        self.assertEqual(result['current'], '不完整')

    def test_failure_partial_and_malformed_remote_are_unknown(self):
        for remote in (None, {}, {**self.shas, 'portal_assets.py': ''},
                       {**self.shas, 'portal.py': 'invalid'}):
            with self.subTest(remote=remote):
                result = portal.portal_bundle_version(self.shas, remote)
                self.assertEqual(result['status'], 'unknown')
                self.assertFalse(result['has_update'])
                self.assertEqual(result['latest'], '')

    def test_known_difference_survives_partial_remote_failure(self):
        remote = {'portal_assets.py': 'b' * 40}
        result = portal.portal_bundle_version(self.shas, remote)
        self.assertEqual(result['status'], 'update_available')
        self.assertTrue(result['has_update'])
        self.assertTrue(result['unknown_files'])
