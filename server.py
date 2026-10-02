import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from channel5 import attributes, load_state, playlist


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # No raw request path or query; signed metadata stays out of logs.
        pass

    def do_GET(self):
        path = urlsplit(self.path).path
        try:
            if path == '/playlist.m3u':
                body = playlist()
                mime = 'application/x-mpegURL'
            elif path == '/attributes.m3u':
                state = load_state()
                body = attributes(state)
                mime = 'application/x-mpegURL'
            elif path == '/health':
                state = load_state()
                body = json.dumps({'ready': True, 'captured_at': state['captured_at'],
                                   'expires_at': state['expires_at'],
                                   'seconds_remaining': int(state['expires_at'] - time.time())})
                mime = 'application/json'
            else:
                self.send_error(404)
                return
            self.send_response(200)
        except (OSError, ValueError, KeyError):
            if path not in ('/attributes.m3u', '/health'):
                self.send_error(404)
                return
            body = json.dumps({'ready': False, 'error': 'Fresh Channel 5 playback authorization unavailable'})
            mime = 'application/json'
            self.send_response(503)
        data = body.encode()
        self.send_header('Content-Type', mime)
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == '__main__':
    ThreadingHTTPServer((os.environ.get('CHANNEL5_BIND', '127.0.0.1'),
                        int(os.environ.get('CHANNEL5_PORT', '1996'))), Handler).serve_forever()
