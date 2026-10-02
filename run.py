"""Supervise the HTTP server and periodic renewal in one container."""
import os
import signal
import subprocess
import sys
import time

from channel5 import ROOT

stopping = False


def stop(signum, frame):
    global stopping
    stopping = True


def launch(script):
    return subprocess.Popen([sys.executable, '-u', str(ROOT / script)],
                            start_new_session=True)


def terminate(process):
    if process is None:
        return
    # Each job has its own process group, including any child browser.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    server = launch('server.py')
    renewal = None
    next_check = 0
    started = 0
    try:
        while not stopping:
            if server.poll() is not None:
                raise SystemExit('HTTP server stopped; restarting container')
            now = time.monotonic()
            if renewal is not None:
                code = renewal.poll()
                if code is not None:
                    terminate(renewal)
                    if code:
                        print('Renewal failed; retrying in five minutes', flush=True)
                    next_check = now + (300 if code else 900)
                    renewal = None
                elif now - started > 360:
                    print('Renewal timed out; retrying in five minutes', flush=True)
                    terminate(renewal)
                    renewal = None
                    next_check = now + 300
            if renewal is None and now >= next_check:
                renewal = launch('renew.py')
                started = now
            time.sleep(1)
    finally:
        terminate(renewal)
        terminate(server)


if __name__ == '__main__':
    main()
