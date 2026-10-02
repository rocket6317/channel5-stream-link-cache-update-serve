"""Observe public playback configuration in a separate browser, then resolve DAI.

No CDM, licence bodies, decryption keys, or extension decryption code are used.
"""
import argparse
import fcntl
import json
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

import websocket
from channel5 import ROOT, ENTRY_URL, EVENT, STATE, load_state, save_state, validate_license

HOOK = r'''(() => {
  const parse = JSON.parse;
  const emitted = new Set();
  JSON.parse = function(...args) {
    const value = parse.apply(this, args);
    try {
      const seen = new WeakSet();
      const walk = (x, depth) => {
        if (depth > 30) return;
        if (typeof x === 'string' && x.startsWith('https://') &&
            (x.includes('cassie.channel5.com/api/v2/licences/widevine/582/C5') ||
             x.includes('dai.google.com/linear/dash/')) && !emitted.has(x)) {
          emitted.add(x);
          window.channel5Metadata(x);
        } else if (x && typeof x === 'object' && !seen.has(x)) {
          seen.add(x);
          Object.values(x).forEach(v => walk(v, depth + 1));
        }
      };
      walk(value, 0);
    } catch (_) {}
    return value;
  };
})();'''


def capture(timeout=75):
    chrome = os.environ.get('CHANNEL5_CHROME', shutil.which('chromium') or '/usr/bin/chromium')
    with tempfile.TemporaryDirectory(prefix='channel5-browser-') as profile:
        process = subprocess.Popen([
            chrome, '--headless=new', '--no-sandbox', '--disable-dev-shm-usage',
            '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0',
            '--remote-allow-origins=http://localhost', '--user-data-dir=' + profile,
            'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ws = None
        try:
            from pathlib import Path
            deadline = time.monotonic() + timeout
            active = Path(profile) / 'DevToolsActivePort'
            while not active.exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Isolated browser failed to start')
                time.sleep(.2)
            port = active.read_text().splitlines()[0]
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json', timeout=5) as r:
                tabs = json.load(r)
            ws = websocket.create_connection(tabs[0]['webSocketDebuggerUrl'],
                                             origin='http://localhost', timeout=5)
            ws.settimeout(1)
            commands = [
                ('Runtime.enable', {}), ('Page.enable', {}),
                ('Runtime.addBinding', {'name': 'channel5Metadata'}),
                ('Page.addScriptToEvaluateOnNewDocument', {'source': HOOK}),
                ('Page.navigate', {'url': 'https://www.channel5.com/channels/5'}),
            ]
            for i, (method, params) in enumerate(commands, 1):
                ws.send(json.dumps({'id': i, 'method': method, 'params': params}))
            license_url, manifest_entry = None, None
            license_seen_at = None
            while time.monotonic() < deadline:
                try:
                    event = json.loads(ws.recv())
                except websocket.WebSocketTimeoutException:
                    if license_seen_at and time.monotonic() - license_seen_at > 3:
                        break
                    continue
                if event.get('id') in range(1, 6) and event.get('error'):
                    raise RuntimeError('Browser capture initialization failed')
                if event.get('method') != 'Runtime.bindingCalled':
                    continue
                params = event['params']
                if params.get('name') != 'channel5Metadata':
                    continue
                url = params.get('payload', '')
                u = urlsplit(url)
                if u.netloc == 'cassie.channel5.com':
                    validate_license(url)
                    license_url = url
                    license_seen_at = time.monotonic()
                elif u.netloc == 'dai.google.com' and (not EVENT or EVENT in u.path):
                    manifest_entry = url
                if license_url and manifest_entry:
                    break
            if not license_url:
                raise RuntimeError('No fresh Channel 5 licence URL observed within capture timeout')
            entry = manifest_entry or ENTRY_URL
            if not entry:
                raise RuntimeError('No manifest entry observed; configure CHANNEL5_DAI_EVENT for fallback')
            return license_url, entry
        finally:
            if ws:
                try:
                    ws.send(json.dumps({'id': 999, 'method': 'Browser.close'}))
                except (OSError, websocket.WebSocketException):
                    pass
                ws.close()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


def resolve_manifest(entry):
    # Follow Channel 5's Google DAI redirect to a newly allocated stream session.
    u = urlsplit(entry)
    if (u.scheme != 'https' or u.netloc != 'dai.google.com' or (EVENT and EVENT not in u.path)):
        raise ValueError('Unexpected manifest entry URL')
    req = urllib.request.Request(entry, headers={'User-Agent': 'Mozilla/5.0',
                                                 'Referer': 'https://www.channel5.com/'})
    with urllib.request.urlopen(req, timeout=20) as response:
        root = ET.fromstring(response.read(2_000_000))
        if root.tag != '{urn:mpeg:dash:schema:mpd:2011}MPD':
            raise ValueError('Upstream did not return a DASH manifest')
        return response.url


def renewal_needed(state):
    if (state['expires_at'] - time.time() <= 3600
            or time.time() - state['captured_at'] >= 21600):
        return True
    # DAI sessions can disappear before their independent licence expires.
    try:
        resolve_manifest(state['manifest_url'])
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 410):
            print(f'Cached stream session unavailable (HTTP {exc.code}); renewing', flush=True)
            return True
        raise
    return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE.parent / '.renew.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Renewal already running')
            return
        if not args.force:
            try:
                old = load_state()
            except (OSError, ValueError, KeyError):
                old = None
            if old is not None:
                try:
                    needed = renewal_needed(old)
                except Exception as exc:
                    # Avoid browser captures on transient upstream/network failures.
                    code = getattr(exc, 'code', None)
                    detail = f'HTTP {code}' if code else type(exc).__name__
                    print(f'Cached manifest check failed ({detail}); prior state retained', flush=True)
                    raise SystemExit(1)
                if not needed:
                    print('Current playback authorization and stream remain fresh')
                    return
        for attempt in range(3):
            stage = 'browser capture'
            try:
                license_url, entry = capture()
                stage = 'manifest resolution'
                manifest = resolve_manifest(entry)
                stage = 'state persistence'
                state = save_state(license_url, manifest)
                print(f'Renewed Channel 5; authorization expires at Unix {state["expires_at"]}')
                return
            except Exception as exc:
                # Exception text can contain signed URLs; log class only.
                errno = getattr(exc, 'errno', None)
                detail = type(exc).__name__ + (f', errno={errno}' if errno else '')
                print(f'Renewal attempt {attempt + 1} failed during {stage} ({detail})', flush=True)
                if attempt < 2:
                    time.sleep(5 * 2 ** attempt)
        raise SystemExit('Channel 5 renewal failed; prior state retained')


if __name__ == '__main__':
    main()
