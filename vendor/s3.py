#!/usr/bin/env python3
"""Single-server launch helpers; compatible with Python 3.6. No model changes."""
import sys, os, json, signal, subprocess, time, shlex, re, shutil, socket, runpy, fcntl
from pathlib import Path
from datetime import datetime
HERE = Path(__file__).resolve().parent
PYLOW = '/opt/ohgrl-lower/venv/bin/python'
os.environ['PATH'] = os.pathsep.join(['/opt/ohgrl-lower/runtime/bin', str(Path(PYLOW).parent), os.environ.get('PATH', '')])

def save(p, obj):
    p.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding='utf-8')
def read(p):
    return json.loads(p.read_text(encoding='utf-8'))
def die(s):
    raise SystemExit(s)
def call(cmd, cwd=None):
    print('[CMD] ' + ' '.join(shlex.quote(str(x)) for x in cmd), flush=True)
    subprocess.check_call([str(x) for x in cmd], cwd=str(cwd) if cwd else None)
def identity(pid):
    try:
        s = Path('/proc/{}/stat'.format(pid)).read_text().rsplit(')',1)[1].split()
        return None if s[0] == 'Z' else s[19]
    except OSError:
        return None
def live(info):
    return identity(info['pid']) == info['start_ticks'] and info['start_ticks'] is not None

def main():
    action = sys.argv[1] if len(sys.argv)>1 else 'help'
    if action == 'configure':
        if len(sys.argv)!=5: die('configure LOWER OHGRL UPPER_PYTHON (all absolute paths)')
        lower, upper, py = [Path(x).expanduser().resolve() for x in sys.argv[2:]]
        for p in [lower/'policy_broker.py', lower/'http_data_server.py', lower/'preflight_experiment.py', lower/'generate_traffic_trace.py',upper/'run.py',py]:
            if not p.is_file(): die('Missing: '+str(p))
        if (HERE/'current.txt').exists(): die('Already configured: use the existing configuration; do not reconfigure an active experiment.')
        call([PYLOW,'-c','import mininet, mn_wifi, ryu, flask, requests; print("Lower Python ready")'])
        call([py,'-c','import torch, numpy, requests, tensorboardX; print("Upper Python ready")'])
        call([py,str(upper/'run.py'),'--help'],upper)
        for p,key in [(lower/'setting.py','data_server_ip'),(lower/'ryu/setting.py','server_ip')]:
            s=p.read_text(); pat=r'(?m)^'+key+r'\s*=.*$'
            if len(re.findall(pat,s))!=1: die('Cannot safely locate '+key+' in '+str(p))
            backup=Path(str(p)+'.before_server3')
            if not backup.exists(): shutil.copy2(str(p),str(backup))
            p.write_text(re.sub(pat,key+" = '127.0.0.1'",s))
        save(HERE/'paths.json',dict(lower=str(lower),upper=str(upper),upper_python=str(py)))
        print('[OK] Paths saved; API addresses changed to 127.0.0.1 with backups. Workload settings unchanged.')
        return
    if action == 'help':
        print('configure LOWER OHGRL UPPER_PYTHON | fix-addresses | new [50|1500] | api | ryu | ryu-log | topo | broker | receiver 15|16|17|18 | upper | client | status | stop NAME | preflight | api-check')
        return
    paths=read(HERE/'paths.json'); lower=Path(paths['lower']); upper=Path(paths['upper'])
    current=HERE/'current.txt'
    # Serialize mutations across host/h3; shared filesystem, not network namespace.
    lock=open(str(HERE/'launch.lock'),'a'); fcntl.flock(lock,fcntl.LOCK_EX)
    if action=='fix-addresses':
        if current.exists():
            old=Path(current.read_text().strip())
            tracked=old/'ryu.process.json'
            if tracked.exists() and live(read(tracked)): die('Stop Ryu before changing its address settings.')
        for p,key in [(lower/'setting.py','data_server_ip'),(lower/'ryu/setting.py','server_ip')]:
            s=p.read_text(); pat=r'(?m)^'+key+r'\s*=.*$'
            if len(re.findall(pat,s))!=1: die('Cannot safely locate '+key+' in '+str(p))
            backup=Path(str(p)+'.before_server3')
            if not backup.exists(): shutil.copy2(str(p),str(backup))
            replacement=key+" = '127.0.0.1'"
            if not re.search(r'(?m)^'+re.escape(replacement)+r'$',s):
                p.write_text(re.sub(pat,replacement,s))
            print('[OK] '+str(p)+' '+replacement)
        return
    if action=='new':
        budget=int(sys.argv[2]) if len(sys.argv)>2 else 1500
        if budget not in [50,1500]: die('Use 50 for smoke test or 1500 for full run.')
        if current.exists():
            old=Path(current.read_text().strip())
            for p in old.glob('*.process.json'):
                if live(read(p)): die('Old service still alive: '+p.stem+'. Stop old run first.')
        db=lower/'runtime/perflow.sqlite3'
        if db.exists(): call([PYLOW,str(lower/'preflight_experiment.py')],lower)
        run=HERE/'runs'/('C_'+str(budget)+'_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
        run.mkdir(parents=True)
        shutil.copy2(str(HERE/'config_C.json'),str(run/'config.json'))
        shutil.copy2(str(lower/'setting.py'),str(run/'lower_setting.py'))
        shutil.copy2(str(lower/'ryu/setting.py'),str(run/'ryu_setting.py'))
        shutil.copy2(str(lower/'network_profile.json'),str(run/'network_profile.json'))
        save(run/'run.json',dict(paths,budget=budget,flows=30000,api='http://127.0.0.1:5000'))
        call([PYLOW,str(lower/'generate_traffic_trace.py'),'--flows','30000','--seed','101','--output',str(run/'train_trace.jsonl')],lower)
        (run/'api/data').mkdir(parents=True)
        for name in ['trans_data.json','request_data.json','action_data.json']:
            save(run/'api/data'/name,{})
        # http_data_server.py uses ../../api/data; working directory is run/work/inner.
        (run/'work/inner').mkdir(parents=True)
        current.write_text(str(run)+'\n')
        print('[NEW] '+str(run)); return
    if not current.exists(): die('Run new first.')
    run=Path(current.read_text().strip()); cfg=read(run/'run.json')
    if action=='status':
        print('[RUN] '+str(run))
        for p in sorted(run.glob('*.process.json')):
            info=read(p); print(p.name.split('.')[0], 'RUNNING' if live(info) else 'EXITED', 'PID',info['pid'])
        for name in ['upper/summary.json','flows/manifest.json']:
            p=run/name
            if p.exists(): print('\n'+name+'\n'+p.read_text())
        for name in ['ryu','upper','client']:
            p=run/(name+'.log')
            if p.exists():
                print('\n[TAIL] '+str(p),flush=True); subprocess.call(['tail','-n','8',str(p)])
        events=run/'upper/events.jsonl'
        if events.exists():
            print('\n[LATEST EVENTS]',flush=True); subprocess.call(['tail','-n','5',str(events)])
        return
    if action=='stop':
        name=sys.argv[2]; p=run/(name+'.process.json')
        if not p.exists(): die('No tracked service: '+name)
        info=read(p)
        if live(info): os.kill(info['pid'],signal.SIGINT); print('[STOP] SIGINT sent to '+name+'. Wait for EXITED; keep Ryu/topology alive for client cleanup.')
        else: print('[STOP] Already exited: '+name)
        return
    if action=='preflight': call([PYLOW,str(lower/'preflight_experiment.py')],lower); return
    if action=='api-check':
        import urllib.request
        for endpoint in ['state','request','action']:
            try:
                with urllib.request.urlopen(cfg['api']+'/api/'+endpoint,timeout=5) as r: data=json.loads(r.read().decode())
                print(endpoint, json.dumps(data,ensure_ascii=False)[:1800])
            except Exception as e: print(endpoint,repr(e))
        print('Run this twice ~10 seconds apart: state timestamp must advance. Empty action is normal BEFORE upper starts.')
        return
    if action=='ryu-log':
        log=run/'ryu.log'
        if not log.exists(): die('No Ryu log yet. Start Ryu first.')
        print('[FOLLOW] '+str(log)+' (Ctrl+C stops this viewer only)',flush=True)
        fcntl.flock(lock,fcntl.LOCK_UN)
        os.execvp('tail',['tail','-n','60','-F',str(log)])
    if action=='topo':
        tracked=run/'topo.process.json'
        if tracked.exists() and live(read(tracked)): die('Topology already running.')
        if os.geteuid()!=0: die('Use sudo bash s3.sh topo')
        cmd=[PYLOW,'-u','generate_wired_topo18.py']; cwd=lower/'mininet_wifi'
        # Track foreground topology as well, so new cannot rotate a live run.
        save(run/'topo.process.json',dict(pid=os.getpid(),start_ticks=identity(os.getpid()),command=cmd,cwd=str(cwd)))
        fcntl.flock(lock,fcntl.LOCK_UN)
        os.chdir(str(cwd)); os.execv(cmd[0],cmd)
    if action=='api-serve':
        fcntl.flock(lock,fcntl.LOCK_UN)
        os.chdir(str(run/'work/inner'))
        app=runpy.run_path(str(lower/'http_data_server.py'))['app']
        app.run(host='127.0.0.1',port=5000,debug=False,use_reloader=False)
        return
    name=action; cwd=lower
    if action=='api':
        with socket.socket() as probe:
            probe.settimeout(3)
            try:probe.connect(('127.0.0.1',5000))
            except ConnectionRefusedError:pass
            else:die('Port 5000 has a live listener. Identify its owner before starting API.')
        s=socket.socket()
        s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        try:
            s.bind(('127.0.0.1',5000))
            s.listen(1)
        except OSError: die('Port 5000 occupied. Identify its owner; do not start a second API.')
        finally: s.close()
        cmd=[PYLOW,'-u',str(HERE/'s3.py'),'api-serve']
    elif action=='ryu':
        cwd=lower/'ryu'
        # configure may have run before someone replaced the lower source.
        # Fail loudly instead of silently sending telemetry to an old IP.
        for p,key in [(lower/'setting.py','data_server_ip'),(lower/'ryu/setting.py','server_ip')]:
            source=p.read_text()
            if len(re.findall(r'(?m)^'+key+r"\s*=\s*['\"]127\.0\.0\.1['\"]\s*(?:#.*)?$",source))!=1:
                die('Incorrect '+str(p)+' '+key+': must be 127.0.0.1. Restore the configured value before starting Ryu.')
        cmd=['ryu-manager','network_structure.py','network_monitor.py','network_delay.py','network_shortest_path.py','arp_handler.py','--observe-links','--ofp-tcp-listen-port','6633']
    elif action=='broker':
        cmd=[PYLOW,'-u',str(lower/'policy_broker.py'),'--url',cfg['api'],'--socket',str(lower/'runtime/policy.sock')]
    elif action=='receiver':
        host=int(sys.argv[2])
        if host not in [15,16,17,18]: die('Receiver must be 15,16,17,18')
        addresses=subprocess.check_output(['ip','-4','-o','addr','show']).decode()
        if '10.0.0.{}/'.format(host) not in addresses: die('Run inside h'+str(host))
        name='h'+str(host)
        cmd=[PYLOW,'-u',str(lower/'anycast_tcp_server.py'),'--ip','10.0.0.'+str(host),'--port','5001']
    elif action=='upper':
        cwd=upper
        cmd=[cfg['upper_python'],'-u',str(upper/'run.py'),'train-http','--url',cfg['api'],'--config',str(run/'config.json'),'--updates','100000','--feedback-budget',str(cfg['budget']),'--output',str(run/'upper')]
        if (run/'upper').exists(): die('Upper output exists; start a NEW run, not an overwrite.')
    elif action=='client':
        addresses=subprocess.check_output(['ip','-4','-o','addr','show']).decode()
        if '10.0.0.3/' not in addresses: die('Run inside h3, using Mininet CLI: h3 bash .../s3.sh client')
        info=read(run/'upper.process.json')
        if not live(info): die('Upper is not running. Inspect upper.log.')
        manifest=read(run/'upper/manifest.json'); rid=manifest['run_id']
        if not re.fullmatch('[0-9a-f]{32}',rid): die('Unexpected upper run_id')
        prefix='OHGRL-'+rid+'-'
        if (run/'flows').exists(): die('Client output exists; do not repeat this run.')
        (run/'policy_prefix.txt').write_text(prefix+'\n')
        cmd=[PYLOW,'-u',str(lower/'anycast_tcp_client.py'),'sto_anycast','--url',cfg['api'],'--flows',str(cfg['flows']),'--seed','101','--traffic-trace',str(run/'train_trace.jsonl'),'--policy-transport','broker','--policy-socket',str(lower/'runtime/policy.sock'),'--policy-prefix',prefix,'--startup-timeout','600','--output',str(run/'flows')]
    else: die('Unknown action '+action)
    p=run/(name+'.process.json')
    if p.exists() and live(read(p)): die(name+' already running')
    def signals():
        signal.signal(signal.SIGHUP,signal.SIG_IGN)
        signal.signal(signal.SIGINT,signal.SIG_DFL)
    (run/(name+'.command.sh')).write_text('cd '+shlex.quote(str(cwd))+'\n'+' '.join(shlex.quote(x) for x in cmd)+'\n')
    env=os.environ.copy()
    if name=='ryu':
        env['PYTHONUNBUFFERED']='1'
        env['LOWER_RYU_TENSORBOARD']='0'
    with open(str(run/(name+'.log')),'ab',buffering=0) as out:
        proc=subprocess.Popen(cmd,cwd=str(cwd),stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True,preexec_fn=signals,env=env)
    save(p,dict(pid=proc.pid,start_ticks=identity(proc.pid),command=cmd,cwd=str(cwd)))
    time.sleep(0.7)
    print('[START] {} PID={} {}'.format(name,proc.pid,'RUNNING' if proc.poll() is None else 'FAILED (see log)'))
    print('[LOG] '+str(run/(name+'.log')),flush=True)
    subprocess.call(['tail','-n','6',str(run/(name+'.log'))])
    if proc.poll() is not None: die('Service exited during startup; see log above.')

if __name__=='__main__':
    main()
