"""Policy access through a VM-side Unix socket, or explicit direct HTTP."""
import http.client
import json
import math
import socket
import time
from pathlib import Path
from urllib.request import urlopen

DEFAULT_SOCKET = str(Path(__file__).resolve().parent / 'runtime/policy.sock')

class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=5):
        super().__init__('localhost', timeout=timeout)
        self.path = path
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        try:
            self.sock.connect(self.path)
        except BaseException:
            self.sock.close()
            raise

def read_policy(url, socket_path=DEFAULT_SOCKET, timeout=5):
    if socket_path:
        conn = UnixHTTPConnection(socket_path, timeout)
        try:
            conn.request('GET', '/api/action')
            response = conn.getresponse()
            if response.status != 200:
                raise RuntimeError('policy broker HTTP %s: %s' %
                                   (response.status, response.read(2048).decode('utf-8', 'replace')))
            return json.loads(response.read())
        finally:
            conn.close()
    with urlopen(url.rstrip('/') + '/api/action', timeout=timeout) as response:
        return json.load(response)

def validate_policy(action, servers, source, expected_prefix=None):
    if not isinstance(action, dict):
        raise ValueError('policy must be a JSON object')
    probs = action['high_act_list'][0]
    if (len(probs) != len(servers) or
        any(not math.isfinite(float(p)) or float(p) < 0 for p in probs) or
        not math.isclose(sum(float(p) for p in probs), 1, abs_tol=1e-6)):
        raise ValueError('invalid destination probabilities')
    version = action.get('policy_version') or action.get('policy_id')
    if not version:
        raise ValueError('missing policy version')
    if expected_prefix and not str(version).startswith(expected_prefix):
        raise ValueError('policy version prefix mismatch: expected '+expected_prefix)
    src = int(source.rsplit('.', 1)[1])
    for key, (ip, port) in servers.items():
        path = action['low_act_dict'][key]
        if (not isinstance(path, list) or len(path) < 2 or
            any(type(n) is not int for n in path) or len(set(path)) != len(path) or
            path[0] != src or path[-1] != int(ip.rsplit('.', 1)[1])):
            raise ValueError('invalid path for ' + key)
    return action

def wait_policy(url, servers, source, socket_path=DEFAULT_SOCKET, wait_timeout=0,
                retry_interval=2, stop=None, label='policy', report=print, expected_prefix=None):
    start = time.monotonic()
    last_log = float('-inf')
    last_error = None
    while True:
        if stop is not None and stop.is_set():
            raise RuntimeError('policy wait cancelled')
        elapsed = time.monotonic() - start
        if wait_timeout and elapsed >= wait_timeout:
            raise TimeoutError('%s unavailable after %.1fs: %s' % (label, elapsed, last_error))
        request_timeout = min(5, max(.001, wait_timeout-elapsed)) if wait_timeout else 5
        try:
            action = validate_policy(read_policy(url, socket_path, request_timeout), servers, source, expected_prefix)
            if last_error is not None:
                report('[READY] %s after %.1fs' % (label, time.monotonic()-start))
            return action, time.monotonic()-start
        except (OSError, ValueError, KeyError, TypeError, IndexError, RuntimeError, http.client.HTTPException) as exc:
            last_error = repr(exc)
            now = time.monotonic()
            if now-last_log >= 10:
                report('[WAIT] %s (%.1fs): %s' % (label, now-start, last_error))
                last_log = now
        delay = retry_interval
        if wait_timeout:
            delay = min(delay, max(0, wait_timeout-(time.monotonic()-start)))
        if stop is not None:
            stop.wait(delay)
        else:
            time.sleep(delay)
