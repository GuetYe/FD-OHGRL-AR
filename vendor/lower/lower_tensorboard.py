# -*- coding: utf-8 -*-
"""Low-overhead TensorBoard logging for the Mininet/Ryu data plane.

This module is deliberately independent from routing/control logic. If
``tensorboardX`` is unavailable, all calls become no-ops and forwarding keeps
running. JSON flow records and Ryu telemetry remain the experiment ground truth.

Python 3.6 compatible (the active Ryu VM uses Python 3.6).
"""
from __future__ import print_function

import atexit
import json
import math
import os
from pathlib import Path
import re
import threading
import time

_ROOT = Path(__file__).resolve().parent
_RUNTIME = _ROOT / 'runtime'
_SESSION_FILE = _RUNTIME / 'tensorboard_session.json'
_TB_ROOT = _ROOT / 'tensorboard' / 'lower'
_LOCK = threading.RLock()
_WRITERS = {}
_STEPS = {}
_WARNED = False
_FLOW_TOTAL = 0
_FLOW_COMPLETED = 0
_FLOW_VALID = 0
_FLOW_CLEANUP_OK = 0


def _safe_name(value):
    text = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(value))
    return text.strip('._') or 'run'


def _warn_once(message):
    global _WARNED
    with _LOCK:
        if not _WARNED:
            print('[TensorBoard] %s' % message, flush=True)
            _WARNED = True


def _summary_writer(logdir):
    try:
        from tensorboardX import SummaryWriter
    except Exception as exc:
        _warn_once('tensorboardX unavailable; lower-network logging disabled: %r' % (exc,))
        return None
    try:
        # tensorboardX 2.5 (Python 3.6 compatible) accepts logdir/flush_secs.
        return SummaryWriter(logdir=str(logdir), flush_secs=10)
    except Exception as exc:
        _warn_once('cannot create SummaryWriter at %s: %r' % (logdir, exc))
        return None


def start_session(prefix='perflow'):
    """Create/announce one lower-network run; called once when Ryu starts."""
    _RUNTIME.mkdir(parents=True, exist_ok=True)
    env = os.environ.get('LOWER_TB_RUN')
    if env:
        run_id = _safe_name(env)
    else:
        run_id = '%s_%s_%06d' % (
            _safe_name(prefix), time.strftime('%Y%m%d_%H%M%S'), int((time.time() % 1) * 1000000))
    payload = {'run_id': run_id, 'created_at': time.time(), 'pid': os.getpid()}
    tmp = str(_SESSION_FILE) + '.tmp.%s' % os.getpid()
    with open(tmp, 'w') as stream:
        json.dump(payload, stream, indent=2)
    os.replace(tmp, str(_SESSION_FILE))
    return run_id


def current_session():
    env = os.environ.get('LOWER_TB_RUN')
    if env:
        return _safe_name(env)
    try:
        with open(str(_SESSION_FILE), 'r') as stream:
            run_id = json.load(stream).get('run_id')
        if run_id:
            return _safe_name(run_id)
    except Exception:
        pass
    # Flow client can be started independently; create a session if Ryu has not.
    return start_session('standalone')


def get_writer(component):
    component = _safe_name(component)
    with _LOCK:
        if component in _WRITERS:
            return _WRITERS[component]
        run_id = current_session()
        logdir = _TB_ROOT / run_id / component
        logdir.mkdir(parents=True, exist_ok=True)
        writer = _summary_writer(logdir)
        _WRITERS[component] = writer
        if writer is not None:
            print('[TensorBoard] %s -> %s' % (component, logdir), flush=True)
        return writer


def next_step(component):
    with _LOCK:
        step = _STEPS.get(component, 0) + 1
        _STEPS[component] = step
        return step


def _number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _add(writer, tag, value, step):
    value = _number(value)
    if writer is not None and value is not None:
        writer.add_scalar(tag, value, step)


def _stats(writer, prefix, values, step, nonnegative=False):
    cleaned = []
    for value in values:
        value = _number(value)
        if value is None or (nonnegative and value < 0):
            continue
        cleaned.append(value)
    if not cleaned:
        return
    _add(writer, prefix + '/mean', sum(cleaned) / float(len(cleaned)), step)
    _add(writer, prefix + '/min', min(cleaned), step)
    _add(writer, prefix + '/max', max(cleaned), step)


def log_telemetry_snapshot(data, success=True, failed_endpoint=None):
    """Log one already-assembled Ryu graph snapshot.

    Called at the same cadence as upstream telemetry so bandwidth, delay, loss,
    queue and active-flow curves share one step axis.
    """
    # tensorboardX 2.5 starts a multiprocessing.Queue and a consumer thread.
    # Ryu runs these calls in an Eventlet hub; a blocked queue.put/lock can
    # prevent the hub from servicing OpenFlow sockets. Keep JSON/HTTP telemetry
    # enabled while disabling only this optional Ryu-side writer by default.
    if os.environ.get('LOWER_RYU_TENSORBOARD', '0').strip().lower() not in ('1', 'true', 'yes'):
        return
    writer = get_writer('ryu')
    step = next_step('ryu')
    if writer is None:
        return
    try:
        edges = data.get('edges') or []
        active = data.get('active_flows') or []
        _add(writer, 'System/telemetry_upload_success', 1 if success else 0, step)
        _add(writer, 'Network/edge_count', len(edges), step)
        _add(writer, 'Flow/active_verified_count', len(active), step)
        if failed_endpoint:
            # Numeric one-hot tags are easier to compare than text-only events.
            _add(writer, 'System/upload_failure/%s' % _safe_name(failed_endpoint), 1, step)

        specs = [
            ('free_bw', 'Network/free_bw_mbps', True),
            ('used_bw', 'Network/used_bw_mbps', True),
            ('delay', 'Network/delay_ms', True),
            ('loss', 'Network/loss_percent', True),
            ('pkt_err', 'Network/pkt_err_percent', True),
            ('pkt_drop', 'Network/pkt_drop_count', True),
            ('forward_queue_pkts', 'Network/queue_forward_pkts', True),
            ('reverse_queue_pkts', 'Network/queue_reverse_pkts', True),
            ('forward_queue_bytes', 'Network/queue_forward_bytes', True),
            ('reverse_queue_bytes', 'Network/queue_reverse_bytes', True),
        ]
        for key, tag, nonnegative in specs:
            _stats(writer, tag, [edge.get(key) for edge in edges], step, nonnegative=nonnegative)

        # Per-link traces are useful for diagnosing congestion/path changes.
        for edge in edges:
            src, dst = edge.get('source'), edge.get('target')
            link = '%s-%s' % (src, dst)
            for key, tag, nonnegative in specs:
                value = _number(edge.get(key))
                if value is None or (nonnegative and value < 0):
                    continue
                _add(writer, 'Link/%s/%s' % (_safe_name(link), tag.split('/')[-1]), value, step)

        # State counts from verified active-flow payload.
        counts = {}
        for flow in active:
            state = _safe_name(flow.get('state', 'unknown'))
            counts[state] = counts.get(state, 0) + 1
        for state, count in counts.items():
            _add(writer, 'Flow/state/%s' % state, count, step)
    except Exception as exc:
        _warn_once('Ryu TensorBoard logging failed but forwarding continues: %r' % (exc,))


def log_flow_result(record):
    """Log one terminal application-flow JSON record."""
    global _FLOW_TOTAL, _FLOW_COMPLETED, _FLOW_VALID, _FLOW_CLEANUP_OK
    writer = get_writer('flows')
    step = next_step('flows')
    if writer is None:
        return
    try:
        completed = record.get('app_outcome') == 'completed'
        cleanup = bool(record.get('cleanup_confirmed'))
        valid = completed and cleanup and not record.get('controller_error')
        with _LOCK:
            _FLOW_TOTAL += 1
            _FLOW_COMPLETED += int(completed)
            _FLOW_VALID += int(valid)
            _FLOW_CLEANUP_OK += int(cleanup)
            total, done, good, cleanup_ok = _FLOW_TOTAL, _FLOW_COMPLETED, _FLOW_VALID, _FLOW_CLEANUP_OK

        prefix = 'Target' if record.get('flow_type') == 'T' else 'Background'
        _add(writer, 'Flow/%s/completed' % prefix, int(completed), step)
        _add(writer, 'Flow/%s/cleanup_confirmed' % prefix, int(cleanup), step)
        _add(writer, 'Flow/%s/valid_completed' % prefix, int(valid), step)
        _add(writer, 'Flow/%s/bytes' % prefix, record.get('bytes'), step)
        _add(writer, 'Flow/%s/dscp' % prefix, record.get('dscp'), step)
        path = record.get('path')
        if isinstance(path, (list, tuple)) and len(path) >= 2:
            _add(writer, 'Flow/%s/path_hops' % prefix, len(path) - 1, step)

        timing = [
            'payload_delivery_seconds', 'delivery_seconds', 'arrival_to_delivery_seconds',
            'app_fct_seconds', 'queue_and_install_seconds', 'task_queue_seconds',
            'policy_wait_seconds', 'flow_table_elapsed_seconds', 'lifecycle_seconds',
            'max_switch_rule_lifetime_seconds'
        ]
        for key in timing:
            _add(writer, 'Flow/%s/Timing/%s' % (prefix, key), record.get(key), step)

        _add(writer, 'Flow/Cumulative/records', total, step)
        _add(writer, 'Flow/Cumulative/completion_rate', done / float(total), step)
        _add(writer, 'Flow/Cumulative/valid_completion_rate', good / float(total), step)
        _add(writer, 'Flow/Cumulative/failure_rate', (total - done) / float(total), step)
        _add(writer, 'Flow/Cumulative/cleanup_unconfirmed_rate',
             (total - cleanup_ok) / float(total), step)
    except Exception as exc:
        _warn_once('flow TensorBoard logging failed but JSON result remains valid: %r' % (exc,))


def close_all():
    with _LOCK:
        for writer in list(_WRITERS.values()):
            if writer is None:
                continue
            try:
                writer.flush()
                writer.close()
            except Exception:
                pass
        _WRITERS.clear()


atexit.register(close_all)
