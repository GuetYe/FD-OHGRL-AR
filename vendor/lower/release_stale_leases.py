#!/usr/bin/env python3
"""Python 3.6+: offline, exact-cookie OVS cleanup with verified lease release.
Run as root on the Mininet VM, beside flow_registry.py. Stop clients and Ryu;
keep Mininet/OVS running. Never deletes the database or unrelated flow rules.
"""
import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'runtime' / 'perflow.sqlite3'
AUDIT = None

def log(s):
    print(s, flush=True)
    if AUDIT:
        with (AUDIT/'recovery.log').open('a') as f:
            f.write(datetime.datetime.now().isoformat()+' '+s+'\n')

def command(args):
    r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       universal_newlines=True, timeout=15)
    if AUDIT:
        with (AUDIT/'commands.jsonl').open('a') as f:
            f.write(json.dumps(dict(args=args,code=r.returncode,stdout=r.stdout,stderr=r.stderr))+'\n')
    if r.returncode:
        raise RuntimeError('Command failed: {}\n{}'.format(' '.join(args), r.stderr.strip()))
    return r.stdout

def stopped():
    offenders=[]
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name)==os.getpid():
            continue
        try:
            parts=(p/'cmdline').read_bytes().split(b'\0')
        except FileNotFoundError:
            continue
        names=[os.path.basename(os.fsdecode(x)) for x in parts]
        if any(x in names for x in ('anycast_tcp_client.py','perflow_client.py','ryu-manager','ryu-manager.py')) or b'ryu.cmd.manager' in parts:
            offenders.append(p.name)
    if offenders:
        raise RuntimeError('Stop the client/Ryu first. Matching process PIDs: '+', '.join(offenders))

def inventory():
    bridges=command(['ovs-vsctl','--timeout=10','list-br']).split()
    result={}
    for br in bridges:
        if not re.fullmatch(r'[A-Za-z0-9_.:-]+',br) or br.startswith('-'):
            raise RuntimeError('Unsupported bridge name: '+repr(br))
        raw=command(['ovs-vsctl','--timeout=10','get','Bridge',br,'datapath_id']).strip().strip('"')
        if not re.fullmatch(r'[0-9a-fA-F]{16}',raw):
            raise RuntimeError('No valid datapath ID for bridge '+br)
        result[br]=int(raw,16)
    if not result or len(set(result.values()))!=len(result):
        raise RuntimeError('No OVS bridges, or duplicate datapath IDs. Keep the original Mininet topology running.')
    return result

def cookie_absent(br,cookie):
    out=command(['ovs-ofctl','-O','OpenFlow13','dump-flows',br,
                 'cookie=0x{:x}/0xffffffffffffffff'.format(cookie)])
    # Fail closed on unexpected/empty output; a successful empty stats reply
    # has an OFPST_FLOW or OFPMP_FLOW header.
    if not re.search(r'(?:OFPST_FLOW|OFPMP_FLOW)\s+reply',out):
        raise RuntimeError('Unrecognized flow-stats response from '+br)
    if re.search(r'cookie\s*=',out):
        raise RuntimeError('Cookie rules still present on '+br)

def main():
    global AUDIT
    if os.geteuid()!=0:
        raise RuntimeError('Run: sudo python3 release_stale_leases.py')
    if not DB.is_file() or not (ROOT/'flow_registry.py').is_file():
        raise RuntimeError('Place this file in the project root beside flow_registry.py.')
    stopped()
    # Same lock as PerFlowController: a running controller must not compete.
    controller_lock=open(str(DB)+'.controller.lock','a')
    try:
        fcntl.flock(controller_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError('Controller database lock is occupied. Fully stop Ryu first.')
    source=sqlite3.connect('file:'+str(DB)+'?mode=ro',uri=True,timeout=10)
    rows=[dict(json.loads(data),state=state) for state,data in source.execute("SELECT state,data FROM flows WHERE state != 'released' ORDER BY id")]
    log('Pending leases: {}'.format(len(rows)))
    if not rows:
        source.close();log('DONE: pending_leases=0');return
    for r in rows:
        c=r.get('cookie')
        if type(c)!=int or c>>56!=0x43:
            raise RuntimeError('Unexpected cookie; refusing cleanup for '+str(r.get('flow_id')))
        if not isinstance(r.get('switch_dpids',[]),list):
            raise RuntimeError('Unexpected switch_dpids data.')
        if not r.get('switch_dpids') and any(r.get(k) is not None for k in ('installed_at','install_requested_mono')):
            raise RuntimeError('Installation recorded but switch list missing: '+r['flow_id'])
    stamp=datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    AUDIT=ROOT/'runtime'/('lease_recovery_'+stamp)
    AUDIT.mkdir()
    log('Audit and backup: '+str(AUDIT))
    # iterdump supports the VM's Python 3.6, including committed WAL contents.
    source.execute('BEGIN')
    with (AUDIT/'backup.sql').open('w') as f:
        for line in source.iterdump():f.write(line+'\n')
        f.flush();os.fsync(f.fileno())
    source.close()
    (AUDIT/'before.json').write_text(json.dumps(rows,indent=2))
    bridges=inventory()
    (AUDIT/'bridges.json').write_text(json.dumps(bridges,indent=2))
    known=set(bridges.values())
    required={d for r in rows for d in r.get('switch_dpids',[])}
    missing=required-known
    if missing:
        raise RuntimeError('Required switch DPIDs missing: {}. No leases released; restore original switches first.'.format(sorted(missing)))
    log('OVS bridges checked: {}. Exact-cookie cleanup begins.'.format(len(bridges)))
    from flow_registry import Registry
    reg=Registry(DB)
    for i,r in enumerate(rows,1):
        stopped()
        if inventory()!=bridges:
            raise RuntimeError('OVS topology inventory changed; stopping cleanup.')
        fid=r['flow_id'];cookie=r['cookie']
        log('[{}/{}] flow={} cookie=0x{:x}: deleting exact rules'.format(i,len(rows),fid,cookie))
        for br in bridges:
            command(['ovs-ofctl','-O','OpenFlow13','del-flows',br,
                     'cookie=0x{:x}/0xffffffffffffffff'.format(cookie)])
        for br in bridges:cookie_absent(br,cookie)
        time.sleep(2)
        stopped()
        if inventory()!=bridges:
            raise RuntimeError('OVS topology inventory changed during verification.')
        for br in bridges:cookie_absent(br,cookie)
        # Preserve original client closure time when it exists. If the old
        # process died before marking closure, record explicitly how inferred.
        note='Offline recovery: no recognized benchmark client process; exact cookie absent on all local OVS bridges in two checks; controller lock held.'
        current=reg.get(fid)
        if not current.get('client_closed_at'):
            reg.client_closed(fid,recovery_client_closed_inferred=True,recovery_note=note)
        expected=r.get('switch_dpids',[])
        reg.update(fid,state='quarantine',rules_absent_at=time.time(),
                   rules_absent_mono=time.monotonic(),absence_confirmed_dpids=expected,
                   recovery_note=note,recovery_audit_dir=str(AUDIT),
                   recovery_verified_bridges=bridges)
        reg.release(fid,expected,[],recovery_method='offline_ovs_exact_cookie_two_checks')
        log('  RELEASED (DSCP={})'.format(r.get('dscp')))
    remaining=reg.pending()
    (AUDIT/'after.json').write_text(json.dumps(remaining,indent=2))
    log('DONE: pending_leases={}'.format(len(remaining)))
    log('Restart Ryu, then run python3 preflight_experiment.py. Lease cleanup alone does not prove topology health.')

if __name__=='__main__':
    try:
        main()
    except Exception as e:
        log('STOPPED: '+str(e))
        if AUDIT:log('Evidence saved: '+str(AUDIT))
        log('Unverified leases remain reserved. Do not delete the database.')
        sys.exit(1)
