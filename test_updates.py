"""Exercise updater dispatch with mocked Docker/probes; no real stack updates."""
import json
import subprocess
import os
import shutil
import unittest
from pathlib import Path
from unittest.mock import patch

import check_updates


class ComponentTests(unittest.TestCase):
    def test_current_base_config_identity_does_not_report_false_update(self):
        metadata = {'base_image': 'python:3.13-slim', 'base_id': 'sha256:base',
                    'source_digest': 'source', 'python_version': '3.13.16'}
        replies = [json.dumps([{'State': {'Running': True}, 'Image': 'sha256:app'}]),
                   json.dumps([{'Config': {'Labels': {'io.channel5.base-image-id': 'sha256:base',
                                                     'io.channel5.source-digest': 'source'}}}]),
                   json.dumps({'rows': [], 'python_version': '3.13.16'})]
        with patch.object(check_updates, 'metadata', return_value=metadata), \
             patch.object(check_updates, 'command', side_effect=replies):
            result = check_updates.check('container')
        self.assertEqual(result['updates'], 0)

    def updater(self, action, outdated, selection=''):
        updater = os.environ.get('UPDATE_SCRIPT') or shutil.which('update')
        if not updater:
            self.skipTest('Optional local updater integration is not installed')
        script = Path(updater).read_text()
        script = script.rsplit('main "$@"', 1)[0]
        context = str(check_updates.ROOT)
        config = json.dumps({'services': {'channel5': {'build': {'context': context}}}})
        report = json.dumps({'base_id': 'sha256:base', 'source_digest': 'source',
                             'updates': int(outdated), 'rows': [
                                 {'component': 'chromium', 'installed': '1',
                                  'available': '2' if outdated else '1', 'update': outdated}]})
        # Mock every external update/check path; exercise real CLI dispatch.
        stubs = r'''
need_command() { :; }
discover_stacks() { STACKS=(channel5-stream); }
compose() {
  shift
  case "$1" in
    config) printf '%s\n' "$TEST_CONFIG" ;;
    ps) printf 'test-container\n' ;;
    pull) : ;;
    build|up) printf 'COMPOSE_CALL %s\n' "$*" ;;
    *) return 1 ;;
  esac
}
python3() { printf '%s\n' "$TEST_REPORT"; }
check_codex() { :; }
check_berk_codex() { :; }
check_blesh() { :; }
'''
        env = {**os.environ, 'TEST_CONFIG': config, 'TEST_REPORT': report}
        return subprocess.run(['bash', '-c', script + stubs + '\nmain ' + action],
                              input=selection, text=True, capture_output=True, env=env)

    def test_check_detects_component_update_without_rebuild(self):
        result = self.updater('-c channel5-stream', True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('UPDATE', result.stdout)
        self.assertNotIn('COMPOSE_CALL', result.stdout)

    def test_all_rebuilds_detected_updates_and_waits_for_health(self):
        result = self.updater('-a', True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('build --pull --no-cache', result.stdout)
        self.assertIn('CHANNEL5_BASE_ID=sha256:base', result.stdout)
        self.assertIn('up -d --wait --wait-timeout 120', result.stdout)

    def test_all_does_not_rebuild_current_components(self):
        result = self.updater('-a', False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('COMPOSE_CALL', result.stdout)

    def test_interactive_selection_updates_channel5(self):
        result = self.updater('interactive', True, 'a\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('build --pull --no-cache', result.stdout)


if __name__ == '__main__':
    unittest.main()
