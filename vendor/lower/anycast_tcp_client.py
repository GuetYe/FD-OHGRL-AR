#!/usr/bin/env python3
"""Per-flow benchmark generator. Compatible with positional --sto_anycast invocation."""
import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
from traffic_schedule import validate_trace_row, scheduled_arrivals
import random
import time
import uuid
import threading
from policy_access import DEFAULT_SOCKET, wait_policy
from pathlib import Path
import setting
from perflow_client import choose_action,run_flow,write_record
from flow_registry import DEFAULT_DB


def main():
    p=argparse.ArgumentParser()
    p.add_argument('mode',nargs='?',default='sto_anycast',choices=['sto_anycast','unicast'])
    p.add_argument('--sto_anycast',action='store_true')
    p.add_argument('--url',default='http://%s:%s'%(setting.data_server_ip,setting.data_server_port))
    p.add_argument('--source-ip',default='10.0.0.%s'%setting.src_node_list[0])
    p.add_argument('--flows',type=int,default=setting.packet_nums)
    p.add_argument('--seed',type=int,default=1234)
    p.add_argument('--output',default=None)
    p.add_argument('--database',default=str(DEFAULT_DB))
    p.add_argument('--traffic-trace',default=None,help='JSONL planned trace from another run; replays identical inter-arrivals/sizes/types')
    p.add_argument('--policy-transport', choices=['broker','http'], default='broker')
    p.add_argument('--policy-socket', default=DEFAULT_SOCKET)
    p.add_argument('--policy-prefix',default=None,help='e.g. CHEN- or OHGRL-; rejects stale policy from the other method')
    p.add_argument('--startup-timeout', type=float, default=0, help='0 waits indefinitely before arrivals')
    p.add_argument('--policy-wait-timeout', type=float, default=300, help='per-flow policy wait; 0 unlimited')
    a=p.parse_args()
    if any(not math.isfinite(v) or v<0 for v in [a.startup_timeout,a.policy_wait_timeout]):
        p.error('wait timeouts must be nonnegative')
    stop = threading.Event()
    policy_socket = a.policy_socket if a.policy_transport == 'broker' else None
    if a.flows<1:raise ValueError('flows must be positive')
    output=Path(a.output or ('result/perflow_'+uuid.uuid4().hex))
    output.mkdir(parents=True,exist_ok=False)
    servers=setting.server_ip_port

    workload=dict(lambda_value=setting.lambda_value,data_size_mean=setting.data_size_mean,
                  data_size_std=setting.data_size_std,background_data_rate=setting.background_data_rate,
                  background_data_lambda_value=setting.background_data_lambda_value,
                  background_data_size_mean=setting.background_data_size_mean,
                  background_data_size_std=setting.background_data_size_std,thread_num=setting.thread_num)

    trace=[]
    if a.traffic_trace:
        source=Path(a.traffic_trace)
        rows=[json.loads(line) for line in source.read_text(encoding='utf-8').splitlines() if line.strip()]
        if len(rows) < a.flows: raise ValueError('traffic trace has fewer rows than --flows')
        trace=[validate_trace_row(rows[i],i) for i in range(a.flows)]
        trace_origin=str(source.resolve())
    else:
        traffic=random.Random(a.seed)
        for i in range(a.flows):
            background=traffic.random()<setting.background_data_rate
            rate=setting.background_data_lambda_value if background else setting.lambda_value
            size_mean=setting.background_data_size_mean if background else setting.data_size_mean
            size_std=setting.background_data_size_std if background else setting.data_size_std
            trace.append(dict(sequence=i,interarrival_seconds=traffic.expovariate(rate),
                              size_bytes=max(1,int(traffic.gauss(size_mean,size_std)*1000)),
                              flow_type='B' if background else 'T'))
        trace_origin='generated-from-seed'
        trace=[validate_trace_row(row,i) for i,row in enumerate(trace)]
    trace_path=output/'traffic_trace.jsonl'
    trace_path.write_text(''.join(json.dumps(row,sort_keys=True,separators=(',',':'))+'\n' for row in trace),encoding='utf-8')
    trace_sha256=hashlib.sha256(trace_path.read_bytes()).hexdigest()
    profile_path=Path(__file__).resolve().with_name('network_profile.json')
    profile_sha256=hashlib.sha256(profile_path.read_bytes()).hexdigest()

    manifest=dict(benchmark_run_id=uuid.uuid4().hex,arrival_schedule_semantics='absolute-monotonic-v2',seed=a.seed,flows=a.flows,url=a.url,mode=a.mode,source_ip=a.source_ip,
                  metric='payload_delivery_seconds: receiver last-byte minus payload send start, same Mininet kernel',
                  timing='arrival and task-queue delay recorded separately',database=a.database,
                  traffic_parameters=workload,traffic_trace_sha256=trace_sha256,traffic_trace_origin=trace_origin,
                  network_profile_sha256=profile_sha256,telemetry_semantics_version='perflow-aligned-delta-v2',expected_policy_prefix=a.policy_prefix)
    # These fields are consumed by the paired-run validator. Keep their values
    # explicit in every run so a missing field cannot silently bypass alignment.
    manifest.update(client_measurement_version='tcp-ack-last-byte-v1',
                    worker_semantics='threadpool-executor-absolute-monotonic-arrival-v2',
                    flow_timeouts={'setup_timeout_seconds':30,'transfer_timeout_seconds':300,
                                   'cleanup_timeout_seconds':60})
    manifest.update(policy_transport=a.policy_transport, policy_socket=policy_socket,
                    policy_wait_timeout=a.policy_wait_timeout, status='waiting_for_policy')
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    try:
        if a.mode != 'unicast' and any(r['flow_type']=='T' for r in trace):
            startup_policy, startup_wait = wait_policy(a.url, servers, a.source_ip, policy_socket,
                                         a.startup_timeout, stop=stop, label='startup policy', expected_prefix=a.policy_prefix)
            # Pin this exact run after startup; later target flows cannot silently
            # switch to another trial of the same algorithm.
            version=str(startup_policy.get('policy_version') or startup_policy.get('policy_id'))
            a.policy_prefix=version.rsplit('-',1)[0]+'-'
            manifest['expected_policy_prefix']=a.policy_prefix
        else:
            startup_wait = 0
    except (KeyboardInterrupt, TimeoutError) as exc:
        manifest.update(status='startup_aborted', error=repr(exc))
        (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
        print('[STOP] No business flows generated:', repr(exc), flush=True)
        return
    manifest.update(status='running', startup_wait_seconds=startup_wait,
                    experiment_start_mono=time.monotonic())
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print('[READY] Starting arrivals; startup wait excluded from flow timing.', flush=True)
    def task(i,size,kind,arrival,planned_gap,submitted):
        task_started=time.monotonic()
        phase="policy"
        try:
            server,decision=choose_action(servers,a.source_ip,a.url,random.Random(a.seed+i*1000003),
                                         controlled=(a.mode!='unicast' and kind=='T'),
                                         fixed_destination=next(iter(servers)) if a.mode=='unicast' and kind=='T' else None,
                                         policy_socket=policy_socket, policy_wait_timeout=a.policy_wait_timeout, stop=stop,
                                         policy_prefix=a.policy_prefix)
            decision.update(benchmark_run_id=manifest['benchmark_run_id'],expected_policy_prefix=a.policy_prefix,
                            submitted_mono=submitted,scheduler_lag_seconds=submitted-arrival,sequence=i,planned_interarrival_seconds=planned_gap,planned_size_bytes=size,
                            traffic_trace_sha256=trace_sha256,task_queue_seconds=task_started-arrival)
            phase='transfer'
            result=run_flow(a.source_ip,server,decision,size,kind,arrival,output,database=a.database)
        except Exception as error:
            result=dict(flow_id=uuid.uuid4().hex,benchmark_run_id=manifest['benchmark_run_id'],
                        expected_policy_prefix=a.policy_prefix,submitted_mono=submitted,scheduler_lag_seconds=submitted-arrival,sequence=i,flow_type=kind,arrival_mono=arrival,
                        app_outcome='failed',cleanup_confirmed=False,error=repr(error),
                        failure_phase=phase, task_queue_seconds=task_started-arrival,
                        planned_interarrival_seconds=planned_gap,planned_size_bytes=size,traffic_trace_sha256=trace_sha256,
                        policy_wait_seconds=time.monotonic()-task_started if phase=='policy' else None)
            write_record(output,result)
        print(json.dumps({'flow_id':result['flow_id'],'outcome':result['app_outcome'],
                          'cleanup_confirmed':result['cleanup_confirmed'],
                          'error':result.get('error'), 'controller_error':result.get('controller_error')},ensure_ascii=False),flush=True)
        return result
    # Arrival generation is separate from workers: waiting for DSCP/cleanup must
    # not silently change the offered arrival timestamps.
    with ThreadPoolExecutor(max_workers=setting.thread_num) as pool:
        try:
            futures=[]
            for item,arrival,submitted in scheduled_arrivals(trace,manifest['experiment_start_mono'],stop):
                i=item['sequence'];size=item['size_bytes'];kind=item['flow_type']
                futures.append(pool.submit(task,i,size,kind,arrival,item['interarrival_seconds'],submitted))
            for f in as_completed(futures):f.result()
        except KeyboardInterrupt:
            stop.set()
            for f in futures: f.cancel()
            print('[STOP] Policy waits cancelled; active flows finish cleanup.', flush=True)
    manifest['status']='interrupted' if stop.is_set() else 'finished'
    manifest['experiment_end_mono']=time.monotonic()
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2))
    print('Flow records:',str(output.resolve()),flush=True)

if __name__=='__main__':main()
