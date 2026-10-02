# Channel 5 Stream Link Cache

A small Docker service that renews Channel 5 live DASH playback metadata and
supplies the current manifest and Widevine licence URL to a compatible player.
It keeps the playlist entry stable when upstream session URLs expire.

The player handles the normal Widevine licence exchange and playback. The
service does not decrypt media, extract content keys, or restream video.

## Quick start

Requirements: Docker Engine and Docker Compose.

```sh
git clone https://github.com/rocket6317/channel5-stream-link-cache-update-serve.git
cd channel5-stream-link-cache-update-serve
cp .env.example .env
# Edit .env for your deployment, then:
docker compose up -d --build
```

The default HTTP listener is available only on the Docker host:

- Playlist: `http://localhost:1996/playlist.m3u`
- Playback attributes: `http://localhost:1996/attributes.m3u`
- Status: `http://localhost:1996/health`

For a player on another device, set `CHANNEL5_HOST_BIND` to your chosen host
interface and `CHANNEL5_PUBLIC_URL` to an address the player can reach.
Configuration belongs in the ignored `.env` file. No deployment addresses,
short links, event IDs, captured sessions, or credentials are included here.

## OTT Navigator

Add the configured `/playlist.m3u` endpoint as a playlist source. Alternatively,
use this entry in an existing playlist, replacing the example endpoint:

```m3u
#EXTINF:-1 tvg-id="5.uk",Channel 5
#EXTATTRFROMURL:https://example.com/attributes.m3u
https://example.com/attributes.m3u
```

`#EXTATTRFROMURL` fetches the current Widevine licence property and DASH URL when
playback starts. Remove any old static licence lines for that channel.
[OTT Navigator documents this format](https://ottnav.github.io/faq.html).

An optional short-link service can redirect to the attributes endpoint. Configure
its URL using `CHANNEL5_METADATA_URL`; no short-link credentials are needed by
this application. The player's device must be able to reach the redirect target.

Widevine playback has been tested with OTT Navigator on Fire TV. Other players
need support for both Widevine and runtime playlist attributes, or their own
integration with the metadata endpoint.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `CHANNEL5_HOST_BIND` | Compose host interface | `127.0.0.1` |
| `CHANNEL5_PORT` | Compose host port | `1996` |
| `CHANNEL5_PUBLIC_URL` | Player-reachable service base URL | `http://localhost:1996` |
| `CHANNEL5_METADATA_URL` | Optional metadata alias or short link | Uses base URL plus `/attributes.m3u` |
| `CHANNEL5_DAI_EVENT` | Optional DAI event ID for fallback if capture misses the manifest entry | Empty |
| `CHANNEL5_CHROME` | Browser executable inside the container | `/usr/bin/chromium` |
| `CHANNEL5_STATE` | Internal persistent metadata path | `/state/playback.json` |

The browser normally observes the manifest entry from the site's playback
configuration. If it cannot find one, configure the fallback event ID from your
own authorised playback configuration. No event identifier is hardcoded.

The image runs as UID/GID 1000. The bundled Compose file stores playback state
in a Docker named volume outside the source checkout. Custom bind-mounted state
directories must be writable by that UID/GID.

## Renewal behavior

Every 15 minutes the supervisor checks the saved expiry locally. Fresh metadata
causes no browser visit. It renews when less than one hour remains on the signed
licence URL, or when the current pair is at least six hours old.

A renewal launches an isolated headless Chromium profile, observes URL strings
in the site's normal playback configuration, resolves the Google DAI manifest
redirect, and validates the manifest and expiry before publishing the pair.
It uses no browser extension, bundled CDM, account credentials, or key extraction.
The site's playback configuration may change; capture failures require review.

Each renewal job makes up to three attempts. Failed jobs retry after five
minutes, with a six-minute job timeout. Failed captures retain the previous
valid state without extending its original expiry. Nearly expired or unavailable
metadata produces HTTP 503. Renewal does not currently react to player errors.

## Operations and updates

```sh
docker compose ps
docker compose logs --tail 50
docker compose exec channel5 python renew.py --force
```

To check component versions without restarting the running service:

```sh
python3 check_updates.py --container channel5-stream
```

This host-side helper uses temporary Docker containers to check the Python base,
Debian packages (including Chromium and CA certificates), pip and the supported
Python requirements. It also checks source and build fingerprints. It does not
mount production playback state into probe containers.

For a rebuild with tracked fingerprints and fresh packages:

```sh
python3 check_updates.py --metadata > /tmp/channel5-build.json
export CHANNEL5_BASE_ID="$(python3 -c 'import json; print(json.load(open("/tmp/channel5-build.json"))["base_id"])')"
export CHANNEL5_SOURCE_DIGEST="$(python3 -c 'import json; print(json.load(open("/tmp/channel5-build.json"))["source_digest"])')"
docker compose build --pull --no-cache
docker compose up -d --wait --wait-timeout 120
```

The supported runtime stays on Python 3.13 and websocket-client 1.x. A generic
local updater can use `check_updates.py` JSON output to select this stack for
updates. The helper's `updates` count covers package, base and source changes;
its failure exit status must be treated as a failed check rather than current.
The optional updater integration tests use `UPDATE_SCRIPT` or an `update`
executable on PATH; no private updater script is included in this repository.

## Development

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python -m unittest -v
docker compose config --quiet
```

The tests use synthetic metadata. They do not require captured licence responses,
private URLs, or live playback. Optional host-updater tests skip if that tool is
not available. Live playback still needs validation on the target player.

## Private data

Runtime metadata contains signed playback URLs. It is stored with mode 0600 in
the state volume, omitted from normal logs, and never bundled into the image.
The metadata endpoint exposes those URLs to its clients, so restrict it to
trusted devices or protect it with access controls before external exposure.

`.gitignore` and `.dockerignore` exclude local environment files, playback state,
HAR captures, browser extensions, keys, and logs. Keep private deployment notes,
real addresses and credentials outside this repository. Do not upload them in
issues or screenshots.
