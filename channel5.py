"""Channel 5 playback metadata; the player performs normal Widevine playback."""
import json
import os
import re
import tempfile
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get('CHANNEL5_STATE', Path.home() / '.local/state/channel5-stream/playback.json'))
EVENT = os.environ.get('CHANNEL5_DAI_EVENT', '')
ENTRY_URL = f'https://dai.google.com/linear/dash/event/{EVENT}/manifest.mpd' if EVENT else None
MIN_LIFETIME = 300


def validate_license(url):
    if any(c in url for c in '\r\n'):
        raise ValueError('Invalid licence URL')
    u = urllib.parse.urlsplit(url)
    if (u.scheme != 'https' or u.netloc != 'cassie.channel5.com'
            or u.path != '/api/v2/licences/widevine/582/C5'):
        raise ValueError('Unexpected licence endpoint')
    q = urllib.parse.parse_qs(u.query)
    expiry = int(q['expiry'][0])
    if not q.get('tag') or expiry <= time.time() + MIN_LIFETIME:
        raise ValueError('Missing or expired licence authorization')
    return expiry


def validate_manifest(url):
    if any(c in url for c in '\r\n'):
        raise ValueError('Invalid manifest URL')
    u = urllib.parse.urlsplit(url)
    match = re.fullmatch(r'/linear/dash/pa/event/([^/]+)/stream/[^/]+/manifest\.mpd', u.path)
    if (u.scheme != 'https' or u.netloc != 'dai.google.com' or not match
            or (EVENT and match[1] != EVENT)):
        raise ValueError('Unexpected session manifest')


def load_state(require_fresh=True):
    data = json.loads(STATE.read_text())
    if require_fresh:
        expiry = validate_license(data['license_url'])
        if expiry != data['expires_at']:
            raise ValueError('Inconsistent expiry')
        validate_manifest(data['manifest_url'])
    return data


def save_state(license_url, manifest_url):
    expiry = validate_license(license_url)
    validate_manifest(manifest_url)
    data = {'license_url': license_url, 'manifest_url': manifest_url,
            'expires_at': expiry, 'captured_at': int(time.time())}
    STATE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=STATE.parent, prefix='.playback-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, STATE)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    return data


def attributes(data):
    return ('#EXTINF:-1 tvg-id="5.uk",Channel 5\n'
            '#KODIPROP:inputstream.adaptive.license_type=com.widevine.alpha\n'
            f'#KODIPROP:inputstream.adaptive.license_key={data["license_url"]}\n'
            f'{data["manifest_url"]}\n')


def playlist():
    base = os.environ.get('CHANNEL5_PUBLIC_URL', 'http://localhost:1996').rstrip('/')
    endpoint = os.environ.get('CHANNEL5_METADATA_URL') or base + '/attributes.m3u'
    for url in (base, endpoint):
        u = urllib.parse.urlsplit(url)
        if any(c in url for c in '\r\n') or u.scheme not in ('http', 'https') or not u.netloc or u.username:
            raise ValueError('Invalid public endpoint configuration')
    return ('#EXTM3U\n#EXTINF:-1 tvg-id="5.uk",Channel 5\n'
            f'#EXTATTRFROMURL:{endpoint}\n{endpoint}\n')
