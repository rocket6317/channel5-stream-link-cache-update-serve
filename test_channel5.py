import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import channel5
from server import Handler, ThreadingHTTPServer


def licence(expiry):
    return f'https://cassie.channel5.com/api/v2/licences/widevine/582/C5?expiry={expiry}&tag=test'


MANIFEST = f'https://dai.google.com/linear/dash/pa/event/{channel5.EVENT or "example-event"}/stream/test:TEST/manifest.mpd'


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_patch = patch.object(channel5, 'STATE', Path(self.temp.name) / 'state.json')
        self.state_patch.start()
        self.addCleanup(self.state_patch.stop)

    def test_matching_pair_and_atomic_replacement(self):
        first = channel5.save_state(licence(int(time.time()) + 3600), MANIFEST)
        second = channel5.save_state(licence(int(time.time()) + 7200), MANIFEST)
        self.assertNotEqual(first['expires_at'], second['expires_at'])
        self.assertEqual(channel5.load_state(), second)
        self.assertEqual(channel5.STATE.stat().st_mode & 0o777, 0o600)

    def test_invalid_update_preserves_previous_pair(self):
        original = channel5.save_state(licence(int(time.time()) + 3600), MANIFEST)
        with self.assertRaises(ValueError):
            channel5.save_state(licence(int(time.time()) + 3600), 'https://example.com/manifest.mpd')
        self.assertEqual(channel5.load_state(), original)

    def test_expiry_margin(self):
        with self.assertRaises(ValueError):
            channel5.save_state(licence(int(time.time()) + 100), MANIFEST)

    def test_prevent_playlist_injection(self):
        with self.assertRaises(ValueError):
            channel5.save_state(licence(int(time.time()) + 3600) + '\n#EXTINF:injected', MANIFEST)

    def test_default_playlist_has_no_deployment_addresses(self):
        with patch.dict('os.environ', {}, clear=True):
            self.assertIn('#EXTATTRFROMURL:http://localhost:1996/attributes.m3u', channel5.playlist())

    def test_configured_alias_and_empty_alias_fallback(self):
        with patch.dict('os.environ', {'CHANNEL5_PUBLIC_URL': 'https://example.com',
                                       'CHANNEL5_METADATA_URL': ''}):
            self.assertIn('https://example.com/attributes.m3u', channel5.playlist())
        with patch.dict('os.environ', {'CHANNEL5_METADATA_URL': 'https://example.com/channel'}):
            self.assertIn('#EXTATTRFROMURL:https://example.com/channel', channel5.playlist())

    def test_playlist_rejects_newline_in_config(self):
        with patch.dict('os.environ', {'CHANNEL5_METADATA_URL': 'https://example.com\n#EXTINF:injected'}):
            with self.assertRaises(ValueError):
                channel5.playlist()

    def test_api_refreshes_both_attributes_and_rejects_expired_state(self):
        data = channel5.save_state(licence(int(time.time()) + 3600), MANIFEST)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/playlist.m3u') as response:
                playlist = response.read().decode()
                self.assertIn('#EXTATTRFROMURL:', playlist)
                self.assertNotIn('license_key=', playlist)
            url = f'http://127.0.0.1:{server.server_port}/attributes.m3u'
            with urllib.request.urlopen(url) as response:
                text = response.read().decode()
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertEqual(text.count('license_key='), 1)
                self.assertIn(data['license_url'], text)
                self.assertTrue(text.endswith(MANIFEST + '\n'))
            with patch('channel5.time.time', return_value=data['expires_at'] + 1):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(url)
                self.assertEqual(caught.exception.code, 503)
                error = caught.exception.read().decode()
                caught.exception.close()
                self.assertNotIn(data['license_url'], error)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == '__main__':
    unittest.main()
