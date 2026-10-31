#!/usr/bin/env python3
"""Recover only three known pre-install failures, after all clients exit."""
import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
IDS = ['b0a89a7f6efa490898456c079d45362d',
       '50ac58a720334d7cbaf5d4a3a500bfff',
       'd04670e6b8c7469d88331ab4cfcdb211']
VERSION = 'OHGRL-4847ea7a2a8041a49d714de712a45f74-00001485'

def main():
    if os.geteuid() != 0:
        raise RuntimeError('Run: sudo python3 cleanup_B.py')
    dbpath = ROOT / 'runtime/perflow.sqlite3'
    if not dbpath.is_file():
        raise RuntimeError('Place this script beside flow_registry.py in the lower project.')
    busy = []
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name) == os.getpid():
            continue
        try:
            argv = (p / 'cmdline').read_bytes().split(b'\0')
            if any(Path(v.decode(errors='replace')).name in
                   ('anycast_tcp_client.py', 'perflow_client.py') for v in argv if v):
                busy.append(p.name)
        except (FileNotFoundError, ProcessLookupError):
            continue
    if busy:
        raise RuntimeError('Client still running, PID=' + ','.join(busy) +
                           '. Stop h3 client with Ctrl+C once and wait; then rerun.')
    from flow_registry import Registry
    reg = Registry(dbpath)
    rows = [reg.get(fid) for fid in IDS]
    for row in rows:
        if row is None:
            raise RuntimeError('A target row is missing; no cleanup requested.')
        if row['state'] == 'released':
            continue
        if (row['state'] != 'error' or row.get('policy_version') != VERSION
            or row.get('controller_error') != 'path has missing link'
            or row.get('path') != [3, 7, 10, 14, 18]
            or any(row.get(k) for k in ('switch_dpids', 'install_requested_mono',
                                        'installed_at', 'started_at', 'started_mono',
                                        'expected_rule_count'))):
            raise RuntimeError('Record differs from pre-install failure; no cleanup requested: ' + row['flow_id'])
    out = ROOT / 'runtime' / ('cleanup_B_' + datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    out.mkdir()
    (out / 'before.json').write_text(json.dumps(rows, indent=2))
    with sqlite3.connect('file:' + str(dbpath) + '?mode=ro', uri=True) as source:
        # Explicit read transaction provides one snapshot, including committed WAL data.
        # iterdump works on Python versions that lack Connection.backup().
        source.execute('BEGIN')
        with (out / 'backup.sql').open('w', encoding='utf-8') as dump:
            for line in source.iterdump():
                dump.write(line + '\n')
            dump.flush()
            os.fsync(dump.fileno())
    print('Backup and report:', out, flush=True)
    for row in rows:
        if row['state'] != 'released' and not row.get('client_closed_at'):
            reg.client_closed(row['flow_id'], recovery_note=
                              'Manual recovery after client exit; pre-install missing-link failure; no flow mods recorded.',
                              app_outcome='failed')
    print('Cleanup requested. Waiting up to 20 seconds for Ryu...', flush=True)
    deadline = time.monotonic() + 20
    while True:
        current = [reg.get(fid) for fid in IDS]
        if all(r['state'] == 'released' for r in current) or time.monotonic() >= deadline:
            break
        time.sleep(1)
    (out / 'after.json').write_text(json.dumps(current, indent=2))
    for row in current:
        print(row['flow_id'], row['state'])
    print('Run next: python3 preflight_experiment.py')
    if not all(r['state'] == 'released' for r in current):
        print('Not fully released. Keep Ryu running; send this output. No forced release performed.')
        return 1
    print('All three released. This does NOT repair or verify topology links.')
    return 0

if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print('STOP:', exc, flush=True)
        sys.exit(1)
