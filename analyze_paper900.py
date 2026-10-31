"""Audit the completed matrix and report FCT means across business seeds."""
import argparse
import csv
import json
import math
from pathlib import Path
import statistics
from common import read, write, sha
from paper_plan import jobs, checkpoint_for


def resolve_run(trial, recorded):
    # A downloaded trial is authoritative; never silently read another server copy.
    name = str(recorded).replace('\\', '/').rstrip('/').split('/')[-1]
    candidate = trial / name
    if not candidate.is_dir():
        raise ValueError('Missing run directory in this trial: '+name)
    return candidate


def validate_frozen(audit):
    if (audit.get('passed') is not True or audit.get('updates') != 0
            or not audit.get('before') or audit.get('before') != audit.get('after')):
        raise ValueError('Frozen model hashes/updates failed audit')


def validate_flow(record, job):
    value=record.get('arrival_to_delivery_seconds')
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
        raise ValueError('Invalid scheduled-arrival FCT')
    action=record['action'];key=record['choice_key'];path=record['path']
    if record['policy_version']!=action['policy_version'] or path!=action['low_act_dict'][key]:
        raise ValueError('Flow differs from immutable published version/path')
    if len(path)!=len(set(path)):
        raise ValueError('Cyclic flow path')
    from common import profile, adj
    topology = profile('original18'); neighbors = adj(topology)
    if (not path or path[0] != topology['source'] or path[-1] not in topology['destinations']
            or any(v not in neighbors.get(u, set()) for u,v in zip(path,path[1:]))):
        raise ValueError('Invalid topology path')
    probabilities = action['high_act_list'][0]
    if (len(probabilities)!=4 or any(isinstance(p,bool) or not isinstance(p,(int,float))
            or not math.isfinite(p) or p<0 or p>1 for p in probabilities)
            or abs(sum(probabilities)-1)>1e-6):
        raise ValueError('Invalid destination distribution')
    if job['method']=='rand_sp' or job['variant']=='uniform_high':
        if any(abs(p-.25)>1e-8 for p in probabilities):
            raise ValueError('Uniform destination replacement was not applied')
    if job['method'] in ['ssp','rand_sp'] or job['variant']=='short_paths':
        from route_variants import PATHS
        if path!=PATHS[path[-1]]:
            raise ValueError('Flow differs from declared fixed shortest path')
    if job['method']=='ssp' and (path[-1]!=15 or probabilities!=[1.,0.,0.,0.]):
        raise ValueError('SSP nearest destination/tie rule differs')


def analyze(trial):
    receipts = read(trial/'paper900_receipts.json')
    receipts = [dict(r, run=str(resolve_run(trial,r['run']))) for r in receipts]
    expected = {j['id']:j for j in jobs()}
    actual = {r['job']['id']:r for r in receipts}
    if set(actual) != set(expected) or len(actual) != len(receipts):
        raise ValueError('Full 59-job matrix is incomplete or contains duplicate jobs')
    groups = {}
    traces = {}
    rows = []
    for key, j in expected.items():
        r = actual[key]
        if r['job'] != j:
            raise ValueError('Job definition changed: '+key)
        run = Path(r['run'])
        if not read(run/'passed.json')['passed'] or not read(run/'network_closed.json'):
            raise ValueError('Run did not close successfully: '+key)
        summary = read(run/'upper/summary.json')
        if summary['errors']:
            raise ValueError('Upper errors: '+key)
        if j['mode']=='train':
            if summary['accepted_feedback'] != j['budget'] or summary['outcome']!='completed':
                raise ValueError('Training budget incomplete: '+key)
            if j['phase']=='train':
                model=run/'upper'/('checkpoint.pt' if j['method']=='chen' else 'latest.pt')
                if sha(model)!=r['checkpoint_sha256']:
                    raise ValueError('Training checkpoint changed: '+key)
            continue
        cp = checkpoint_for(j, receipts)
        if cp and sha(cp) != read(run/'experiment.json')['checkpoint_sha256']:
            raise ValueError('Evaluation checkpoint differs: '+key)
        if summary['updates'] != 0:
            raise ValueError('Evaluation optimized model parameters: '+key)
        if cp:
            validate_frozen(read(run/'upper/frozen_audit.json'))
        records = []
        for f in (run/'flows').glob('*.json'):
            value = read(f)
            if 'flow_id' in value and 'app_outcome' in value:
                records.append(value)
        if len(records) != j['flows'] or len({r.get('sequence',r['flow_id']) for r in records}) != j['flows']:
            raise ValueError('Missing or duplicate flow records: '+key)
        if len({r['flow_id'] for r in records}) != j['flows']:
            raise ValueError('Duplicate flow IDs: '+key)
        if any(r['app_outcome']!='completed' or not r.get('cleanup_confirmed') for r in records):
            raise ValueError('Failed flow or cleanup: '+key)
        for record in records:
            validate_flow(record,j)
        exp = read(run/'experiment.json')
        if sha(run/'train_trace.jsonl') != exp['trace_sha256']:
            raise ValueError('Traffic trace file changed: '+key)
        trace_key = (j['seed'],j['load'],j['flows'])
        if traces.setdefault(trace_key,exp['trace_sha256']) != exp['trace_sha256']:
            raise ValueError('Paired traffic mismatch: '+key)
        if j['phase'] != 'eval':
            continue
        values = [r['arrival_to_delivery_seconds'] for r in records]
        row = dict(method=j['method'],variant=j['variant'],load=j['load'],seed=j['seed'],
                   flows=len(values),mean_fct_seconds=statistics.mean(values),run=str(run))
        rows.append(row)
        groups.setdefault((j['method'],j['variant'],j['load']),[]).append(row['mean_fct_seconds'])
    aggregate = []
    for (method, variant, load), values in sorted(groups.items()):
        if len(values)!=3:
            raise ValueError('Missing business-seed replication')
        aggregate.append(dict(method=method,variant=variant,load=load,business_seeds=3,
                              mean_fct_seconds=statistics.mean(values),
                              seed_mean_sd_seconds=statistics.stdev(values)))
    write(trial/'paper900_analysis.json',dict(passed=True,jobs=59,formal_evaluation_runs=len(rows),
          formal_evaluation_flows=sum(r['flows'] for r in rows),results=aggregate,per_seed=rows,
          note='SD is across three business-seed means from one trained model, not independent training repeats.'))
    with (trial/'paper900_comparison.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(aggregate[0]))
        writer.writeheader();writer.writerows(aggregate)
    return aggregate


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--trial-dir',required=True,type=Path)
    args=parser.parse_args()
    print(json.dumps(analyze(args.trial_dir),indent=2))
