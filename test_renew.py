import time
import unittest
import urllib.error
from unittest.mock import patch

import renew


class RenewalTests(unittest.TestCase):
    def setUp(self):
        self.state = {'expires_at': time.time() + 10800,
                      'captured_at': time.time() - 60,
                      'manifest_url': 'https://example.com/manifest.mpd'}

    def test_fresh_license_with_gone_session_requires_renewal(self):
        for code in (404, 410):
            with self.subTest(code=code):
                error = urllib.error.HTTPError(self.state['manifest_url'], code, 'unavailable', {}, None)
                with patch.object(renew, 'resolve_manifest', side_effect=error):
                    self.assertTrue(renew.renewal_needed(self.state))

    def test_valid_session_skips_browser_renewal(self):
        with patch.object(renew, 'resolve_manifest') as probe:
            self.assertFalse(renew.renewal_needed(self.state))
            probe.assert_called_once_with(self.state['manifest_url'])

    def test_transient_failure_does_not_trigger_browser_capture(self):
        error = urllib.error.HTTPError(self.state['manifest_url'], 503, 'unavailable', {}, None)
        with patch.object(renew, 'resolve_manifest', side_effect=error):
            with self.assertRaises(urllib.error.HTTPError):
                renew.renewal_needed(self.state)

    def test_expiry_or_age_renews_without_probing_old_session(self):
        for field, value in [('expires_at', time.time() + 300),
                             ('captured_at', time.time() - 21601)]:
            with self.subTest(field=field), patch.object(renew, 'resolve_manifest') as probe:
                self.assertTrue(renew.renewal_needed(dict(self.state, **{field: value})))
                probe.assert_not_called()
