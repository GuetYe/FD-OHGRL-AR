#!/usr/bin/env python3
"""Create a deterministic JSONL workload trace without contacting the network."""
import argparse
import hashlib
import json
import random
from pathlib import Path

import setting
from traffic_schedule import validate_trace_row


def generate(flows, seed):
    rng = random.Random(seed)
    rows = []
    for i in range(flows):
        background = rng.random() < setting.background_data_rate
        rate = setting.background_data_lambda_value if background else setting.lambda_value
        mean = setting.background_data_size_mean if background else setting.data_size_mean
        std = setting.background_data_size_std if background else setting.data_size_std
        row = {'sequence': i, 'interarrival_seconds': rng.expovariate(rate),
               'size_bytes': max(1, int(rng.gauss(mean, std) * 1000)),
               'flow_type': 'B' if background else 'T'}
        rows.append(validate_trace_row(row, i))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--flows', type=int, required=True)
    parser.add_argument('--seed', type=int, required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.flows < 1:
        parser.error('--flows must be positive')
    rows = generate(args.flows, args.seed)
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = ''.join(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n' for row in rows)
    target.write_text(data, encoding='utf-8')
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    print(json.dumps({'output': str(target.resolve()), 'flows': len(rows),
                      'seed': args.seed, 'sha256': digest,
                      'traffic_parameters': {
                          'lambda_value': setting.lambda_value,
                          'data_size_mean': setting.data_size_mean,
                          'data_size_std': setting.data_size_std,
                          'background_data_rate': setting.background_data_rate,
                          'background_data_lambda_value': setting.background_data_lambda_value,
                          'background_data_size_mean': setting.background_data_size_mean,
                          'background_data_size_std': setting.background_data_size_std,
                          'thread_num': setting.thread_num}}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
