#!/usr/bin/env python3
"""Validate a completed lower-layer workload run before pairing results."""
import argparse
import hashlib
import json
from pathlib import Path


def validate(directory):
    root = Path(directory)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    trace_path = root / 'traffic_trace.jsonl'
    trace_bytes = trace_path.read_bytes()
    trace = [json.loads(line) for line in trace_bytes.splitlines() if line.strip()]
    rows = []
    for path in root.glob('*.json'):
        if path.name == 'manifest.json':
            continue
        row = json.loads(path.read_text(encoding='utf-8'))
        if 'flow_id' in row:
            rows.append(row)
    errors = []
    if manifest.get('status') != 'finished':
        errors.append('lower manifest status is not finished')
    if hashlib.sha256(trace_bytes).hexdigest() != manifest.get('traffic_trace_sha256'):
        errors.append('traffic trace hash mismatch')
    if len(trace) != manifest.get('flows'):
        errors.append('trace length differs from manifest')
    expected = {row.get('sequence') for row in trace}
    actual = [row.get('sequence') for row in rows]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        errors.append('flow records missing, duplicated, or outside trace')
    benchmark_id = manifest.get('benchmark_run_id')
    if any(row.get('benchmark_run_id') != benchmark_id for row in rows):
        errors.append('record benchmark_run_id mismatch')
    targets = [row for row in rows if row.get('flow_type') == 'T']
    failed = [row for row in targets if row.get('app_outcome') != 'completed']
    cleanup = [row for row in targets if row.get('lease_acquired', row.get('cookie') is not None)
               and not row.get('cleanup_confirmed')]
    controller_errors = [row for row in targets if row.get('controller_error')]
    return {'eligible_for_pairing': not errors and not cleanup and not controller_errors,
            'errors': errors, 'run_id': benchmark_id, 'planned_flows': len(trace),
            'target_flows': len(targets), 'completed_target_flows': sum(
                row.get('app_outcome') == 'completed' for row in targets),
            'failed_target_flows': len(failed), 'cleanup_unconfirmed': len(cleanup),
            'controller_errors': len(controller_errors), 'trace_sha256': manifest.get('traffic_trace_sha256')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    args = parser.parse_args()
    result = validate(args.directory)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['eligible_for_pairing'] else 2)


if __name__ == '__main__':
    main()
