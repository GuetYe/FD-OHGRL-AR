#!/usr/bin/env python3
"""Run in the VM ordinary terminal, not inside a Mininet host namespace."""
import argparse
import fcntl
import json
import os
import socketserver
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.request import urlopen
from policy_access import DEFAULT_SOCKET
import setting

class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_GET(self):
        if self.path != '/api/action':
            self.send_error(404)
            return
        try:
            # No cached policy: every request obtains a fresh complete snapshot.
            with urlopen(self.server.upstream + '/api/action', timeout=4) as r:
                data = json.load(r)
            body = json.dumps(data).encode()
            status = 200
        except Exception as exc:
            status = 503
            body = json.dumps({'error': repr(exc)}).encode()
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', default='http://%s:%s' % (setting.data_server_ip, setting.data_server_port))
    p.add_argument('--socket', default=DEFAULT_SOCKET)
    a = p.parse_args()
    path = Path(a.socket)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path)+'.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if path.exists():
            if not path.is_socket():
                raise RuntimeError('refusing to replace non-socket: ' + str(path))
            path.unlink()
        try:
            with Server(str(path), Handler) as server:
                os.chmod(str(path), 0o600)
                server.upstream = a.url.rstrip('/')
                print('Policy broker: %s -> %s/api/action' % (path, server.upstream), flush=True)
                server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            if path.exists() and path.is_socket():
                path.unlink()

if __name__ == '__main__':
    main()
