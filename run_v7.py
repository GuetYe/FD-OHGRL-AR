#!/usr/bin/env python3
"""Isolated V7 SDN experiments, one active topology per server. Python3.6+."""
import argparse,contextlib,datetime,gzip,importlib.util,json,os,shlex,signal,socket,subprocess,sys,time,traceback,zipfile
from pathlib import Path
from common import HERE,read,write,sha,profile,make_config,graph_signature
from lifecycle import check_port,finish_service
SOCKET='ohgrl_uniform_params'

def require(ok,message):
    if not ok:raise RuntimeError(message)
def module(path):
    spec=importlib.util.spec_from_file_location('v7_lab_helpers',str(path));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
def lab():return module(HERE/'helpers/lab.py')
def cmd(args,**kw):
    print('[CMD]',' '.join(shlex.quote(str(x)) for x in args),flush=True)
    return subprocess.run([str(x) for x in args],check=True,**kw)
def tmux(*args):return ['tmux','-L',SOCKET]+list(args)
def send(session,text):cmd(tmux('send-keys','-t',session,text,'Enter'))
def pane(session):
    p=subprocess.run(tmux('capture-pane','-p','-t',session,'-S','-300'),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    return p.stdout.decode(errors='replace')
def wait(predicate,seconds,description):
    deadline=time.monotonic()+seconds;last=0
    while time.monotonic()<deadline:
        if predicate():return
        if time.monotonic()-last>30:print('[WAIT]',description,flush=True);last=time.monotonic()
        time.sleep(1)
    raise TimeoutError(description)
def api():
    import urllib.request
    with urllib.request.urlopen('http://127.0.0.1:5000/api/state',timeout=3) as r:return json.load(r)
def signature(data):return dict(nodes=sorted(n['id'] for n in data['nodes']),edges=sorted(sorted((e['source'],e['target'])) for e in data['edges']))
def matching_state(p):
    try:
        x=api()
        return x.get('timestamp') and x.get('telemetry_semantics_version')=='perflow-aligned-delta-v2' and signature(x)==graph_signature(p)
    except Exception:return False
def root_required():require(hasattr(os,'geteuid') and os.geteuid()==0,'Use sudo python3 on the SDN server')
@contextlib.contextmanager
def global_lock():
    import fcntl
    path=Path('/home/anonymous/ohgrl_lab/overnight.lock');path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB);yield
def verify():
    for rel,digest in read(HERE/'SHA256.json').items():require(sha(HERE/rel)==digest,'Bundle changed: '+rel)
def config(cell,bank):
    cfg=make_config(cell.name,bank)
    write(cell/'config.json',cfg);return cfg
def quiet(a):
    l=lab()
    require(not subprocess.check_output(['ovs-vsctl','list-br']).strip(),'Existing topology: finish the current experiment first')
    for port in [5000,6633]:
        try:check_port(port)
        except OSError:raise RuntimeError('API/controller bind unavailable on port '+str(port)+'; inspect ss -ltnp')
    roots=[Path('/home/anonymous/MininetWiFi20261003_perflow')]+list(a.runtime_root.glob('*/lower'))
    for lower in roots:require(l.pending(lower)['pending_leases']==0,'Unreleased leases: '+str(lower))
    current=Path('/home/anonymous/server_scheme3/current.txt')
    if current.exists():l.ensure_quiet(Path(current.read_text().strip()))
    for receipt in a.work_root.glob('**/*.process.json'):
        require(not l.live(read(receipt)),'Old V7 process still live: '+str(receipt))
def check_installed(cell):
    record=read(cell/'installed.json')
    require(record['package_sha256']==sha(HERE/'SHA256.json'),'Runtime prepared from another package')
    for rel,digest in record['source_files'].items():require(sha(cell/rel)==digest,'Runtime source changed: '+rel)
def paths(cell):return read(cell/'s3/paths.json')

def check_runtime(cell):
    py=paths(cell)['lower_python'];env=dict(os.environ)
    env['PATH']='/opt/ohgrl-lower/runtime/bin'+os.pathsep+str(Path(py).parent)+os.pathsep+env.get('PATH','')
    code="import shutil; names=['mnexec','ip','ovs-vsctl','ryu-manager']; missing=[n for n in names if not shutil.which(n)]; print({n:shutil.which(n) for n in names}); assert not missing, 'Missing executables: '+str(missing); import mininet, ryu, flask, requests"
    cmd([py,'-c',code],env=env,timeout=60)
def service(cell,action):
    py=paths(cell)['lower_python'];env=dict(os.environ)
    env['PATH']=str(Path(py).parent)+os.pathsep+env.get('PATH','')
    env['PYTHONUNBUFFERED']='1';env['LOWER_RYU_TENSORBOARD']='0'
    try:cmd([py,cell/'s3/s3.py',action],env=env)
    except subprocess.CalledProcessError:
        current=cell/'s3/current.txt'
        if current.exists():
            log=Path(current.read_text().strip())/(action+'.log')
            if log.exists():print('[SERVICE LOG] '+str(log)+'\n'+'\n'.join(log.read_text(errors='replace').splitlines()[-40:]),flush=True)
        raise
def signal_service(l,run,name):l.signal_once(run,name,'V7 graceful drain')
def stop_network(l,cell,run,session):
    signal_service(l,run,'client')
    wait(lambda:not l.running(run,'client'),600,'client finishes flow cleanup')
    wait(lambda:l.pending(cell/'lower')['pending_leases']==0,180,'all DSCP leases released')
    signal_service(l,run,'upper')
    wait(lambda:not l.running(run,'upper'),1800,'upper and periodic diagnostics finish')
    (run/'observer.stop').write_text('V7 close\n')
    wait(lambda:not l.running(run,'observer'),60,'observer exited')
    for name in ['broker']+['h'+str(d) for d in profile(cell.name)['destinations']]+['ryu','api']:
        finish_service(l,run,name,wait)
    if l.running(run,'topo'):send(session,'exit')
    wait(lambda:not l.running(run,'topo'),180,'topology exited normally')
    wait(lambda:not subprocess.check_output(['ovs-vsctl','list-br']).strip(),60,'OVS bridges removed')
    for port in [5000,6633]:check_port(port)
    write(run/'network_closed.json',dict(closed_at=time.time(),ports=[5000,6633]))
def score_done(run):
    done=run/'upper/dense_fixed_DONE.json';err=run/'upper/dense_fixed_ERROR.txt'
    if err.exists():raise RuntimeError('Fixed scorer failed: '+str(err))
    return done.is_file() and read(done).get('capture_complete')
def wait_scores(run,l):
    start=time.monotonic()
    while not score_done(run):
        # Status remains observable; do not time out while a legitimate backlog is draining.
        tracked=run/'upper/dense_worker.process.json'
        require(not tracked.exists() or l.live(read(tracked)),'Scorer exited without DONE: '+str(run))
        print('[SCORING]',run,'elapsed',round(time.monotonic()-start),flush=True);time.sleep(20)
def upper_command(cell,run,upperpy,mode,budget,checkpoint,bank,job=None):
    if job and (job['method']!='ohgrl' or mode=='eval'):
        method=job['method'];root=cell/'upper' if method=='ohgrl' else HERE/'vendor/baselines'/('ssp' if method=='rand_sp' else method)
        upper=[upperpy,'-u',HERE/'experiment_upper.py','--method',method,'--variant',job['variant'],
               '--root',root,'--common',cell/'upper/core','--budget',str(budget),
               'train-http' if mode=='train' else 'eval-http']
    elif mode=='train' and bank:
        upper=[upperpy,'-u',HERE/'helpers/recorded_upper.py',cell/'upper','train-http']
    else:upper=[upperpy,'-u',cell/'upper/run.py','train-http' if mode=='train' else 'eval-http']
    upper+=['--url','http://127.0.0.1:5000','--config',run/'config.json','--output',run/'upper']
    if mode=='train':
        if not job or job['method']!='chen':upper+=['--updates','100000','--feedback-budget',str(budget)]
    else:
        upper+=['--steps','100000']
        if checkpoint:upper+=['--checkpoint',checkpoint]
    return upper

def launch(a,cell,trial,kind,budget=0,seed=101,checkpoint=None,bank=None):
    # Check before rotating current/active, preserving the owner of old services.
    quiet(a)
    l=lab();p=profile(cell.name);cfg=config(cell,bank)
    job=getattr(a,'study_job',None)
    if job:
        from paper_plan import method_config
        cfg=method_config(job['method'],bank)
    stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    run=trial/(kind+'_'+stamp);run.mkdir(parents=True)
    write(run/'config.json',cfg)
    run_paths=paths(cell);run_paths.update(budget=budget,flows=job['flows'] if job else (300 if kind=='eval' else 30000),api='http://127.0.0.1:5000')
    write(run/'run.json',run_paths)
    write(run/'source_receipt.json',dict(package_sha256=sha(HERE/'SHA256.json'),runtime=read(cell/'installed.json')))
    lowpy=a.lower_python;upperpy=run_paths['upper_python']
    cmd([lowpy,cell/'lower/generate_traffic_trace.py','--flows',str(run_paths['flows']),'--seed',str(seed),'--output',run/'raw_trace.jsonl'])
    rows=[json.loads(x) for x in (run/'raw_trace.jsonl').read_text().splitlines() if x.strip()]
    for row in rows:row['interarrival_seconds']*=job['gap'] if job else 2
    (run/'train_trace.jsonl').write_text(''.join(json.dumps(r,sort_keys=True,separators=(',',':'))+'\n' for r in rows))
    mode=job['mode'] if job else ('eval' if kind in ['eval','bank'] else 'train')
    write(run/'experiment.json',dict(mode=mode,case=kind,topology=cell.name,source=p['source'],destinations=p['destinations'],
        flows=len(rows),feedback_budget=budget,traffic_seed=seed,training_seed=cfg.get('seed'),gap_multiplier=2.,
        planned_arrival_span_seconds=sum(r['interarrival_seconds'] for r in rows),drain_seconds=600,
        trace_sha256=sha(run/'train_trace.jsonl'),checkpoint=str(checkpoint) if checkpoint else None,
        checkpoint_sha256=sha(checkpoint) if checkpoint else None,bank_sha256=sha(bank) if bank else None))
    if job:
        exp=read(run/'experiment.json');exp.update(job=job,gap_multiplier=job['gap'])
        write(run/'experiment.json',exp)
    (run/'api/data').mkdir(parents=True);(run/'work/inner').mkdir(parents=True)
    for name in ['trans_data.json','request_data.json','action_data.json']:write(run/'api/data'/name,{})
    (cell/'s3/current.txt').write_text(str(run)+'\n')
    session='v7_'+cell.name+'_'+stamp
    write(trial/'active.json',dict(run=str(run),session=session,kind=kind))
    service(cell,'api');service(cell,'ryu')
    topology_log=run/'topology.log'
    topology_command=('exec '+shlex.quote(lowpy)+' -u '+shlex.quote(str(cell/'s3/s3.py'))+
                      ' topo >> '+shlex.quote(str(topology_log))+' 2>&1')
    cmd(tmux('new-session','-d','-s',session,topology_command))
    def startup():
        require(l.running(run,'ryu'),'Ryu exited: '+str(run/'ryu.log'))
        text=topology_log.read_text(errors='replace') if topology_log.exists() else ''
        if (run/'topo.process.json').exists() and not l.running(run,'topo'):
            raise RuntimeError('Topology process exited; '+str(topology_log)+'\n'+'\n'.join(text.splitlines()[-30:]))
        return 'V7_TOPOLOGY_READY '+cell.name in text
    wait(startup,300,'all switches, host learning and pingAll')
    wait(lambda:matching_state(p),180,'exact selected topology and telemetry semantics')
    service(cell,'broker')
    # No fixed scoring during smoke/bank collection; formal training uses V6 capture/diagnostics.
    upper=upper_command(cell,run,upperpy,mode,budget,checkpoint,bank,job)
    l.start_process(run,'upper',upper,cell/'upper',dict(os.environ,OMP_NUM_THREADS='1',MKL_NUM_THREADS='1',PYTHONUNBUFFERED='1'))
    ready_manifest=[]
    def upper_ready():
        if not l.running(run,'upper'):
            logfile=run/'upper.log'
            tail='\n'.join(logfile.read_text(errors='replace').splitlines()[-60:]) if logfile.exists() else '(no upper.log)'
            raise RuntimeError('Upper exited before startup completed: '+str(logfile)+'\n'+tail)
        try:
            value=read(run/'upper/manifest.json')
        except (OSError, ValueError):
            return False
        if job and job['method'] in ['ssp','rand_sp'] and value.get('method')!=job['method']:
            return False
        ready_manifest.append(value)
        return True
    wait(upper_ready,180,'upper manifest')
    manifest=ready_manifest[-1]
    require(manifest['config']['normalization_profile']==cfg['normalization_profile'],'Wrong effective profile')
    if not job or job['method']=='ohgrl':require(manifest['config']['mask_version']==cfg['mask_version'],'Wrong V7 mask')
    if job:
        prefix=manifest.get('policy_prefix')
        if not prefix:prefix=('CHEN-'+manifest['run_token']+'-') if job['method']=='chen' else 'OHGRL-'+manifest['run_id']+'-'
        (run/'policy_prefix.txt').write_text(prefix+'\n')
    # Persist the actual effective hyperparameters for asynchronous worker equality checks.
    write(run/'config.json',manifest['config'])
    if kind!='bank':l.start_process(run,'observer',[lowpy,HERE/'helpers/lab.py','_observe',run],HERE)
    cli=run/'hosts.cli'
    commands=['h%d %s %s _host --run %s --host-id %d --lower-python %s'%(d,lowpy,shlex.quote(str(HERE/'run_v7.py')),shlex.quote(str(run)),d,shlex.quote(lowpy)) for d in p['destinations']]
    commands+=['h%d %s %s _host --run %s --host-id %d --lower-python %s'%(p['source'],lowpy,shlex.quote(str(HERE/'run_v7.py')),shlex.quote(str(run)),p['source'],shlex.quote(lowpy))]
    cli.write_text('\n'.join(commands)+'\n');send(session,'source '+str(cli))
    wait(lambda:(run/'client.process.json').exists(),90,'business client started')
    if kind=='bank':
        states=[];seen=set();deadline=time.monotonic()+1800
        while len(states)<128:
            require(not (trial/'STOP').exists(),'STOP requested during bank collection; use cleanup')
            require(time.monotonic()<deadline,'Could not collect 128 unique measured topology states')
            require(l.running(run,'upper') and l.running(run,'client'),'Bank calibration service exited')
            try:
                x=api();stamp=str(x.get('timestamp',''))
                if stamp and stamp not in seen and signature(x)==graph_signature(p) and x.get('flow_install_verified') is True and not x.get('controller_error'):
                    states.append(x);seen.add(stamp)
                    print('[BANK]',cell.name,len(states),'/128',flush=True)
            except (OSError,ValueError):pass
            time.sleep(2)
        target=cell/'fixed_bank.json'
        require(not target.exists(),'Refusing to replace a fixed bank')
        write(target,dict(states=states,normalization_profile=cfg['normalization_profile'],
            source=dict(kind='measured calibration states under untrained V7 policy',run=str(run),traffic_seed=seed,
                        selection='first 128 unique valid installed-flow state timestamps; no reward-based selection'),
            topology_spec=cfg['topology_spec'],meaning='fixed diagnostic inputs, not held-out network evaluation'))
        stop_network(l,cell,run,session)
    else:
        deadline=time.monotonic()+a.timeout_hours*3600
        while l.running(run,'observer'):
            require(not (trial/'STOP').exists(),'STOP requested: use cleanup to drain services')
            require(time.monotonic()<deadline,'Run deadline exceeded; services retained for cleanup')
            print('[RUN]',cell.name,kind,run,flush=True);time.sleep(20)
        require(not l.running(run,'upper') and not l.running(run,'client'),'Observer ended while work is still live')
        summary=l.build_summary(run);upper_summary=read(run/'upper/summary.json')
        require(not upper_summary['errors'],'Upper failed')
        require(summary['failed_records']==0 and summary['cleanup_unconfirmed_records']==0 and summary['lease_state']['pending_leases']==0,'Flow or cleanup errors')
        if mode=='train':require(upper_summary['outcome']=='completed' and upper_summary['accepted_feedback']==budget,'Feedback budget incomplete')
        else:require(summary['records']==run_paths['flows'] and summary['completed_records']==run_paths['flows'],'Evaluation cohort incomplete')
        if job and mode=='eval':
            require(upper_summary['updates']==0,'Evaluation performed parameter updates')
            if job['method'] in ['ohgrl','chen','ppo']:
                require(read(run/'upper/frozen_audit.json')['passed'],'Frozen actor audit failed')
        if checkpoint:require(sha(checkpoint)==read(run/'experiment.json')['checkpoint_sha256'],'Source checkpoint changed')
        if bank and mode=='train' and (not job or job['method']=='ohgrl'):
            wait_scores(run,l)
            from audit_v7 import audit_run
            audit_run(run)
        stop_network(l,cell,run,session)
    receipt=dict(run=str(run),kind=kind,passed=True,summary=read(run/'upper/summary.json'))
    write(run/'passed.json',receipt);return receipt

def host(a):
    l=lab();run=Path(a.run);cfg=read(run/'run.json');effective=read(run/'config.json');lower=Path(cfg['lower'])
    source=effective['source'];hostid=a.host_id
    addresses=subprocess.check_output(['ip','-4','-o','addr','show']).decode()
    require('10.0.0.%d/'%hostid in addresses,'Host command must execute in the matching Mininet namespace')
    if hostid==source:
        manifest=read(run/'upper/manifest.json')
        prefix=(run/'policy_prefix.txt').read_text().strip() if (run/'policy_prefix.txt').exists() else 'OHGRL-'+manifest['run_id']+'-'
        (run/'policy_prefix.txt').write_text(prefix+'\n')
        args=[a.lower_python,'-u',lower/'anycast_tcp_client.py','sto_anycast','--url',cfg['api'],
              '--flows',str(cfg['flows']),'--seed',str(read(run/'experiment.json')['traffic_seed']),
              '--traffic-trace',run/'train_trace.jsonl','--policy-transport','broker','--policy-socket',lower/'runtime/policy.sock',
              '--policy-prefix',prefix,'--startup-timeout','600','--output',run/'flows']
        l.start_process(run,'client',args,lower)
    else:
        require(hostid in effective['destinations'],'Not a destination host')
        l.start_process(run,'h'+str(hostid),[a.lower_python,'-u',lower/'anycast_tcp_server.py','--ip','10.0.0.%d'%hostid,'--port','5001'],lower)

def worker(a):
    root_required();verify()
    with global_lock():
        quiet(a)
        names=list(read(HERE/'topologies.json')) if a.topology=='all' else [a.topology]
        for name in names:
            cell=a.runtime_root/name;check_installed(cell);check_runtime(cell);trial=a.work_root/name/a.trial
            trial.mkdir(parents=True,exist_ok=True)
            require(not (trial/'STOP').exists(),'STOP file exists; use a fresh trial')
            status=read(trial/'status.json') if (trial/'status.json').exists() else {}
            if status.get('status')=='COMPLETED' and a.phase=='run':continue
            receipts=read(trial/'receipts.json') if (trial/'receipts.json').exists() else []
            if (trial/'active.json').exists():
                active=read(trial/'active.json');run=Path(active['run'])
                require((run/'passed.json').exists(),'An earlier attempt lacks a passed receipt; retain it and use a fresh --trial after cleanup')
            def task(kind,**kw):
                write(trial/'status.json',dict(status='RUNNING',phase=kind,topology=name,started_at=time.time()))
                row=launch(a,cell,trial,kind,**kw);receipts.append(row);write(trial/'receipts.json',receipts);return row
            try:
                smoke=next((r for r in receipts if r['kind']=='smoke'),None)
                if not smoke:smoke=task('smoke',budget=30)
                if a.phase=='smoke':write(trial/'status.json',dict(status='SMOKE_PASSED',topology=name));continue
                bank=cell/'fixed_bank.json'
                if not bank.exists():task('bank',checkpoint=Path(smoke['run'])/'upper/initial.pt',seed=9091)
                training=next((r for r in receipts if r['kind']=='train'),None)
                if not training:training=task('train',budget=a.budget,bank=bank)
                checkpoint=Path(training['run'])/'upper/latest.pt'
                for seed in [5101,5102,5103]:
                    if not any(r['kind']=='eval' and read(Path(r['run'])/'experiment.json')['traffic_seed']==seed for r in receipts):task('eval',seed=seed,checkpoint=checkpoint,bank=bank)
                write(trial/'status.json',dict(status='COMPLETED',topology=name,feedback_budget=a.budget,training_seed=1234,evaluation_seeds=[5101,5102,5103]))
            except BaseException as exc:
                write(trial/'status.json',dict(status='FAILED',topology=name,error=repr(exc),traceback=traceback.format_exc()))
                if (trial/'active.json').exists():
                    active=read(trial/'active.json');signal_service(lab(),Path(active['run']),'client')
                raise
def pack(a):
    stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S');target=a.work_root/('OHGRL_v7_results_'+stamp+'.zip')
    require(a.work_root.exists(),'No V7 results')
    with zipfile.ZipFile(str(target),'w',zipfile.ZIP_DEFLATED) as z:
        for name in ['run_paper900.py','paper_plan.py','experiment_upper.py','route_variants.py',
                     'analyze_paper900.py','common.py','topologies.json','base_config.json',
                     'BASELINE_PROVENANCE.json','PAPER900_VALIDATION.md','README1.md']:
            if (HERE/name).is_file():z.write(str(HERE/name),'paper900_source/'+name)
        for source in (HERE/'vendor/baselines').rglob('*'):
            if source.is_file() and source.suffix in {'.py','.json'} and '__pycache__' not in source.parts:
                z.write(str(source),'paper900_source/'+source.relative_to(HERE).as_posix())
        for p in a.work_root.rglob('*'):
            if not p.is_file() or p.suffix in {'.zip','.pyc','.sqlite3','.sock'} or any(x in {'tensorboard','dense_actor_snapshots'} for x in p.relative_to(a.work_root).parts):continue
            z.write(str(p),'results/'+p.relative_to(a.work_root).as_posix())
        z.write(str(HERE/'SHA256.json'),'package_SHA256.json');z.write(str(HERE/'topologies.json'),'topologies.json')
        for cell in a.runtime_root.iterdir():
            if not (cell/'installed.json').is_file():continue
            for name in ['fixed_bank.json','topology.json','installed.json']:
                if (cell/name).is_file():z.write(str(cell/name),'runtime/'+cell.name+'/'+name)
            for rel in read(cell/'installed.json')['source_files']:
                z.write(str(cell/rel),'runtime/'+cell.name+'/source/'+rel)
    if os.environ.get('SUDO_UID'):os.chown(str(target),int(os.environ['SUDO_UID']),int(os.environ.get('SUDO_GID','0')))
    print('[PACK]',target)
def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['verify','check','probe','smoke','run','status','stop','cleanup','score','pack','_worker','_host'])
    p.add_argument('--topology',default='original18',choices=['all']+list(read(HERE/'topologies.json')))
    p.add_argument('--trial',default='trial1');p.add_argument('--budget',type=int,default=900)
    p.add_argument('--runtime-root',type=Path,default=Path('/home/anonymous/ohgrl_lab/OHGRL_v7/runtime'))
    p.add_argument('--work-root',type=Path,default=Path('/home/anonymous/ohgrl_lab/OHGRL_v7/results'))
    p.add_argument('--lower-python',default='/opt/ohgrl-lower/venv/bin/python');p.add_argument('--timeout-hours',type=float,default=24)
    p.add_argument('--run');p.add_argument('--host-id',type=int);p.add_argument('--phase',default='run',choices=['run','smoke'])
    a=p.parse_args();require(a.trial.replace('_','').replace('-','').isalnum(),'Use an ASCII alphanumeric trial name')
    if a.command=='_host':host(a);return
    if a.command=='verify':verify();print('[VERIFIED]');return
    if a.command=='pack':pack(a);return
    if a.command=='status':
        pattern=('*' if a.topology=='all' else a.topology)+'/'+a.trial+'/status.json'
        for f in a.work_root.glob(pattern):
            print(f);print(f.read_text())
            active=f.parent/'active.json'
            if active.exists():
                run=Path(read(active)['run']);print('[ACTIVE]',run)
                for file in ['summary.json','dense_fixed_DONE.json','dense_fixed_ERROR.txt']:
                    q=run/'upper'/file
                    if q.exists():print(file,q.read_text()[:1800])
                index=run/'upper/dense_actor_index.jsonl';scores=run/'upper/dense_fixed_reward.jsonl'
                for q in [index,scores]:print(q.name,sum(1 for _ in q.open()) if q.exists() else 0)
        return
    names=list(read(HERE/'topologies.json')) if a.topology=='all' else [a.topology]
    if a.command in ['check','probe']:
        verify()
        for name in names:
            cell=a.runtime_root/name;check_installed(cell)
            if a.command=='check':
                check_runtime(cell)
                cmd([paths(cell)['upper_python'],'-u',HERE/'eval_startup_probe.py','--topology',name,'--root',cell/'upper'],timeout=180)
            if a.command=='probe':
                out=a.work_root/name/('probe_'+datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
                cmd([paths(cell)['upper_python'],'-u',HERE/'model_probe.py','--topology',name,'--root',cell/'upper','--output',out],timeout=600)
        if a.command=='check':
            root_required()
            with global_lock():quiet(a)
            print('[CHECK PASSED] prepared graph, source fingerprints, idle ports and lease registries')
        return
    root_required()
    if a.command=='_worker':worker(a);return
    if a.command in ['run','smoke']:
        require(a.budget>=30 and a.budget%300==0,'Formal feedback budget must be a positive multiple of300')
        verify()
        for name in names:check_installed(a.runtime_root/name)
        session='worker_v7_'+a.topology
        exists=subprocess.run(tmux('has-session','-t',session),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
        require(not exists,'Worker session already exists: '+session)
        with global_lock():quiet(a)
        a.work_root.mkdir(parents=True,exist_ok=True)
        arguments=[sys.executable,'-u',str(HERE/'run_v7.py'),'_worker']+sys.argv[2:]+['--phase','smoke' if a.command=='smoke' else 'run']
        logfile=a.work_root/('launcher_'+a.topology+'_'+a.trial+'.log')
        launch='exec '+' '.join(shlex.quote(x) for x in arguments)+' >> '+shlex.quote(str(logfile))+' 2>&1'
        cmd(tmux('new-session','-d','-s',session,launch));print('[LOG]',logfile);return
    if a.command=='stop':
        for name in names:
            trial=a.work_root/name/a.trial
            if trial.exists():
                (trial/'STOP').write_text('user stop\n')
                if (trial/'active.json').exists():signal_service(lab(),Path(read(trial/'active.json')['run']),'client')
        return
    if a.command=='score':
        require(a.topology!='all','Score one topology at a time')
        trial=a.work_root/a.topology/a.trial;run=Path(a.run) if a.run else Path(read(trial/'active.json')['run']);l=lab()
        require(not l.running(run,'upper'),'Wait for trainer exit before recovering scorer')
        info=run/'upper/dense_worker.process.json'
        require(not info.exists() or not l.live(read(info)),'Scorer is still live')
        cmd([paths(a.runtime_root/a.topology)['upper_python'],'-u',HERE/'helpers/dense_worker.py','--run',run,'--root',a.runtime_root/a.topology/'upper'])
        return
    if a.command=='cleanup':
        with global_lock():
            for name in names:
                trial=a.work_root/name/a.trial
                if (trial/'active.json').exists():
                    x=read(trial/'active.json')
                    for run in sorted(trial.iterdir()):
                        if not run.is_dir() or not (run/'run.json').exists():continue
                        if str(run)!=x['run'] and not any(lab().live(read(p)) for p in run.glob('*.process.json')):continue
                        session=x['session'] if str(run)==x['run'] else 'v7_'+name+'_'+run.name.split('_',1)[1]
                        stop_network(lab(),a.runtime_root/name,run,session)
        return
if __name__=='__main__':main()
