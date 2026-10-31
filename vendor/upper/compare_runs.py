"""Read-only comparison of two upper runs and their common per-flow records."""
import argparse
import json
import hashlib
import math
from pathlib import Path

def percentile(xs,q):
    xs=sorted(xs)
    if not xs:return None
    x=(len(xs)-1)*q;i=int(x);j=min(i+1,len(xs)-1)
    return xs[i]+(xs[j]-xs[i])*(x-i)
def describe(xs):
    xs=[float(v) for v in xs if v is not None and math.isfinite(float(v))]
    mean=sum(xs)/len(xs) if xs else None
    std=(sum((x-mean)**2 for x in xs)/(len(xs)-1))**0.5 if len(xs)>1 else None
    return dict(n=len(xs),mean=mean,std_ddof1=std,median=percentile(xs,.5),p95=percentile(xs,.95))
def read_events(path):
    return [json.loads(line) for line in (Path(path)/'events.jsonl').read_text().splitlines() if line.strip()]
def report(upper,flows):
    rows=[]
    for p in Path(flows).glob('*.json'):
        if p.name=='manifest.json':continue
        row=json.loads(p.read_text())
        if 'flow_id' in row and row.get('flow_type')=='T':rows.append(row)
    manifest=json.loads((Path(upper)/'manifest.json').read_text())
    traffic_manifest=json.loads((Path(flows)/'manifest.json').read_text())
    trace_path=Path(flows)/'traffic_trace.jsonl'
    trace=[json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()] if trace_path.exists() else None
    planned=sum(r.get('flow_type')=='T' for r in trace) if trace is not None else len(rows)
    denominator=max(planned,len(rows))
    trace_integrity=trace_path.exists() and hashlib.sha256(trace_path.read_bytes()).hexdigest()==traffic_manifest.get('traffic_trace_sha256')
    token=manifest.get('run_id',manifest.get('run_token'))
    prefix=manifest.get('policy_prefix') or (('PPO-DDPG-' if manifest.get('config',{}).get('algorithm')=='PPO-DDPG' else 'OHGRL-') if manifest.get('run_id') else 'CHEN-')+str(token)+'-'
    belongs=lambda r: bool(token) and str(r.get('policy_version','')).startswith(prefix)
    attributed=[r for r in rows if belongs(r)]
    valid=[r for r in attributed if r.get('app_outcome')=='completed' and r.get('cleanup_confirmed') and not r.get('controller_error')]
    events=read_events(upper)
    feedback=[r for r in events if r['event']=='feedback']
    updates=[r for r in events if r['event']=='update']
    versions={r.get('policy_version',r.get('observation',{}).get('choice_policy_version')) for r in feedback}
    versions.discard(None)
    return dict(upper_manifest=manifest,
        foreign_or_unattributed_flows=sum(not belongs(r) for r in rows),
        traffic_manifest=traffic_manifest,traffic_trace_integrity=trace_integrity,planned_target_flows=planned,
        unrecorded_target_flows=max(0,planned-len(rows)),
        foreign_policy_flows=sum(bool(r.get('policy_version')) and not belongs(r) for r in rows),
        accepted_feedback=len(feedback),unique_feedback_versions=len(versions),update_events=len(updates),
        upper_wall_seconds=(events[-1]['elapsed_seconds']-events[0]['elapsed_seconds'] if events and all('elapsed_seconds' in r for r in (events[0],events[-1])) else None),
        observed_reward_proxy=describe([r['reward_proxy'] for r in feedback]),
        all_target_attempted=len(rows),all_target_completed=sum(r.get('app_outcome')=='completed' for r in rows),
        attributed_attempted=len(attributed),attributed_completed=sum(r.get('app_outcome')=='completed' for r in attributed),
        valid_completed=len(valid),attributed_failed=sum(r.get('app_outcome')!='completed' for r in attributed),
        cleanup_unconfirmed=sum(not r.get('cleanup_confirmed') for r in attributed),
        # Dedicated flow directories include pre-policy failures in the offered workload.
        valid_completion_rate=(len(valid)/denominator if denominator else None),
        attributed_only_completion_rate=(len(valid)/len(attributed) if attributed else None),
        unassigned_failed=sum(not r.get('policy_version') and r.get('app_outcome')!='completed' for r in rows),
        application_completion_rate=(sum(r.get('app_outcome')=='completed' for r in attributed)/denominator if denominator else None),
        excluded_flow_ids=[r['flow_id'] for r in attributed if r not in valid],
        measurement={k:describe([r.get(k) for r in valid]) for k in
        ['payload_delivery_seconds','delivery_seconds','app_fct_seconds','arrival_to_delivery_seconds',
         'policy_wait_seconds','task_queue_seconds','scheduler_lag_seconds','flow_table_elapsed_seconds']})
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--left-upper','--ohgrl-upper',dest='left_upper',required=True)
    p.add_argument('--right-upper','--chen-upper',dest='right_upper',required=True)
    p.add_argument('--left-flows','--ohgrl-flows',dest='left_flows',required=True)
    p.add_argument('--right-flows','--chen-flows',dest='right_flows',required=True)
    p.add_argument('--left-name',default='OHGRL');p.add_argument('--right-name',default='Chen')
    a=p.parse_args();oh=report(a.left_upper,a.left_flows);ch=report(a.right_upper,a.right_flows)
    checks={k:oh['traffic_manifest'].get(k) is not None and oh['traffic_manifest'].get(k)==ch['traffic_manifest'].get(k) for k in ['seed','flows','source_ip','mode']}
    checks['both_finished']=all(r['traffic_manifest'].get('status')=='finished' for r in [oh,ch])
    checks['both_trace_files_verified']=oh['traffic_trace_integrity'] and ch['traffic_trace_integrity']
    checks['no_foreign_policies']=oh['foreign_policy_flows']==ch['foreign_policy_flows']==0
    checks['no_missing_flow_records']=oh['unrecorded_target_flows']==ch['unrecorded_target_flows']==0
    checks['arrival_schedule_equal']=oh['traffic_manifest'].get('arrival_schedule_semantics') is not None and oh['traffic_manifest'].get('arrival_schedule_semantics')==ch['traffic_manifest'].get('arrival_schedule_semantics')
    checks['traffic_parameters_equal']=oh['traffic_manifest'].get('traffic_parameters')==ch['traffic_manifest'].get('traffic_parameters') and oh['traffic_manifest'].get('traffic_parameters') is not None
    checks['traffic_trace_equal']=oh['traffic_manifest'].get('traffic_trace_sha256')==ch['traffic_manifest'].get('traffic_trace_sha256') and oh['traffic_manifest'].get('traffic_trace_sha256') is not None
    checks['lower_network_profile_equal']=oh['traffic_manifest'].get('network_profile_sha256')==ch['traffic_manifest'].get('network_profile_sha256') and oh['traffic_manifest'].get('network_profile_sha256') is not None
    checks['telemetry_semantics_equal']=oh['traffic_manifest'].get('telemetry_semantics_version')==ch['traffic_manifest'].get('telemetry_semantics_version') and oh['traffic_manifest'].get('telemetry_semantics_version') is not None
    def profile(run):
        m=run['upper_manifest'];cfg=m.get('config',{})
        return cfg.get('normalization_profile',m.get('normalization_profile'))
    checks['normalization_profile_equal']=profile(oh)==profile(ch) and profile(oh) is not None
    print(json.dumps(dict(runs={a.left_name:oh,a.right_name:ch},checks=checks,
        note='Use traffic_trace_equal + traffic_parameters_equal + both profile checks for paired runs. Equal updates do not imply equal interaction budget; packet/FCT time is distinct from reward-path delay and rule lifetime.'),ensure_ascii=False,indent=2))
    if not all(checks.values()):raise SystemExit(2)
if __name__=='__main__':main()
