"""Export app delivery measurements without hiding failures or unreleased leases."""
import argparse
import json
import math
from pathlib import Path

def describe(values):
    xs=sorted(float(x) for x in values if x is not None and math.isfinite(float(x)))
    def q(p):
        if not xs:return None
        z=(len(xs)-1)*p;i=int(z);j=min(i+1,len(xs)-1)
        return xs[i]+(xs[j]-xs[i])*(z-i)
    return dict(n=len(xs),mean=sum(xs)/len(xs) if xs else None,
                median=q(.5),p95=q(.95))

def export(directory):
    directory=Path(directory)
    files=sorted(directory.glob('*.json'))
    rows=[]
    manifest=json.loads((directory/'manifest.json').read_text()) if (directory/'manifest.json').exists() else {}
    for p in files:
        if p.name=='manifest.json':continue
        row=json.loads(p.read_text())
        if 'flow_id' in row:rows.append(row)
    target=[r for r in rows if r.get('flow_type')=='T']
    valid=[r for r in target if r.get('app_outcome')=='completed' and r.get('cleanup_confirmed') and not r.get('controller_error')]
    completed=[r for r in target if r.get('app_outcome')=='completed']
    goodput=[(float(r.get('bytes',0))*8.0/float(r['payload_delivery_seconds'])/1e6)
             for r in completed if r.get('payload_delivery_seconds') and r['payload_delivery_seconds']>0]
    start=manifest.get('experiment_start_mono');end=manifest.get('experiment_end_mono')
    duration=(float(end)-float(start)) if start is not None and end is not None and end>start else None
    run_goodput=(sum(float(r.get('bytes',0)) for r in completed)*8.0/duration/1e6
                 if duration and completed else None)
    return dict(attempted=len(target),completed=sum(r.get('app_outcome')=='completed' for r in target),
                completion_rate=(len(completed)/len(target) if target else None),
                valid_completed=len(valid),failed=sum(r.get('app_outcome')!='completed' for r in target),
                cleanup_unconfirmed=sum(not r.get('cleanup_confirmed') for r in target),
                controller_errors=sum(bool(r.get('controller_error')) for r in target),
                d2d_time=[r['payload_delivery_seconds'] for r in valid],
                delivery_with_connect_seconds=[r['delivery_seconds'] for r in valid],
                arrival_to_delivery_seconds=[r['arrival_to_delivery_seconds'] for r in valid],
                app_fct_seconds=[r['app_fct_seconds'] for r in valid],
                flow_table_elapsed_seconds=[r.get('flow_table_elapsed_seconds') for r in valid],
                per_flow_goodput_mbps=describe(goodput),
                run_goodput_mbps=run_goodput,
                experiment_duration_seconds=duration,
                included_flow_ids=[r['flow_id'] for r in valid],
                excluded_flow_ids=[r['flow_id'] for r in target if r not in valid],
                note='d2d_time = last byte received - payload send start in the SAME Mininet kernel; not rule disappearance time. Report all exclusions and failure counts.')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory');a=p.parse_args()
    print(json.dumps(export(a.directory),ensure_ascii=False,indent=2))
