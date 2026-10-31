#!/usr/bin/env python3
"""900-feedback original18 comparison, frozen testing and policy replacement."""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys
import time
import traceback
from common import HERE, read, write, sha
from paper_plan import jobs, checkpoint_for, BUDGET
import run_v7 as runtime


def run_worker(a):
    runtime.root_required()
    runtime.verify()
    cell = a.runtime_root / 'original18'
    trial = a.work_root / 'original18' / a.trial
    trial.mkdir(parents=True, exist_ok=True)
    design = dict(budget=BUDGET, topology='original18', package_sha256=sha(HERE/'SHA256.json'))
    design_path = trial/'paper900_design.json'
    if design_path.exists() and read(design_path) != design:
        raise ValueError('Existing trial belongs to another design/package; use a new trial')
    with runtime.global_lock():
        write(design_path, design)
        runtime.quiet(a)
        runtime.check_installed(cell)
        runtime.check_runtime(cell)
        if Path(a.lower_python).resolve() != Path(runtime.paths(cell)['lower_python']).resolve():
            raise ValueError('Lower interpreter differs from installed runtime')
        if (trial/'STOP').exists():
            raise ValueError('Stopped trial retained; use a new trial after cleanup')
        if (trial/'active.json').exists():
            previous = Path(read(trial/'active.json')['run'])
            if not (previous/'passed.json').exists():
                raise ValueError('Previous run is incomplete; retain records, cleanup, then use a fresh trial')
        receipts = read(trial/'paper900_receipts.json') if (trial/'paper900_receipts.json').exists() else []
        for j in jobs(a.suite):
            existing = [r for r in receipts if r['job']['id'] == j['id']]
            if existing:
                if len(existing)!=1 or existing[0]['job'] != j or not (Path(existing[0]['run'])/'passed.json').exists():
                    raise ValueError('Previous receipt differs or is incomplete')
                if read(Path(existing[0]['run'])/'passed.json').get('passed') is not True:
                    raise ValueError('Previous receipt did not pass')
                continue
            a.study_job = j
            write(trial/'status.json', dict(status='RUNNING', job=j, suite=a.suite))
            try:
                cp = checkpoint_for(j, receipts)
                if cp:
                    training = next(r for r in receipts if r['job']['phase']=='train' and r['job']['method']==j['method'])
                    summary = read(Path(training['run'])/'upper/summary.json')
                    if summary['accepted_feedback'] != BUDGET or summary['outcome'] != 'completed' or summary['errors']:
                        raise ValueError('Matching training has not reached exactly 900 feedback')
                    if not cp.is_file() or sha(cp) != training['checkpoint_sha256']:
                        raise ValueError('Training checkpoint missing or changed')
                bank = cell/'fixed_bank.json' if j['method']=='ohgrl' and j['phase']=='train' else None
                receipt = runtime.launch(a, cell, trial, 'eval' if j['mode']=='eval' else j['phase'],
                                         budget=j['budget'], seed=j['seed'], checkpoint=cp, bank=bank)
                receipt['job'] = j
                if j['phase']=='train':
                    trained = Path(receipt['run'])/'upper'/('checkpoint.pt' if j['method']=='chen' else 'latest.pt')
                    if not trained.exists():
                        raise ValueError('Missing final trained checkpoint')
                    receipt['checkpoint_sha256'] = sha(trained)
                # Verify matched traffic bytes across all methods and replacements.
                if j['mode']=='eval':
                    actual = read(Path(receipt['run'])/'experiment.json')['trace_sha256']
                    for old in receipts:
                        other = old['job']
                        if other['mode']=='eval' and (other['load'],other['seed'],other['flows']) == (j['load'],j['seed'],j['flows']):
                            if read(Path(old['run'])/'experiment.json')['trace_sha256'] != actual:
                                raise ValueError('Paired evaluation traffic differs')
                receipts.append(receipt)
                write(trial/'paper900_receipts.json', receipts)
            except BaseException as exc:
                write(trial/'status.json', dict(status='FAILED', job=j, error=repr(exc), traceback=traceback.format_exc()))
                if (trial/'active.json').exists():
                    runtime.signal_service(runtime.lab(), Path(read(trial/'active.json')['run']), 'client')
                raise
        write(trial/'status.json', dict(status='COMPLETED', suite=a.suite,
                                       completed_jobs=len(receipts), feedback_budget=BUDGET))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['plan','run','_worker','status','stop','cleanup','pack'])
    p.add_argument('--suite', choices=['all','train','fixed','ablation'], default='all')
    p.add_argument('--trial', default='paper900_trial1')
    p.add_argument('--runtime-root', type=Path, default=Path('/srv/ohgrl/runtime'))
    p.add_argument('--work-root', type=Path, default=Path('/srv/ohgrl/results'))
    p.add_argument('--lower-python', default='/opt/ohgrl-lower/venv/bin/python')
    p.add_argument('--timeout-hours', type=float, default=24)
    a = p.parse_args()
    runtime.require(a.trial.replace('_','').replace('-','').isalnum(), 'Use an alphanumeric trial identifier')
    if a.command == 'plan':
        print(json.dumps(dict(topology='original18', feedback_budget=BUDGET, jobs=jobs(a.suite)), indent=2))
        return
    if a.command == '_worker':
        run_worker(a)
        return
    if a.command == 'run':
        runtime.root_required()
        runtime.verify()
        with runtime.global_lock():
            runtime.quiet(a)
            runtime.check_installed(a.runtime_root/'original18')
        a.work_root.mkdir(parents=True, exist_ok=True)
        session = 'worker_paper900_' + a.trial
        command = [sys.executable,'-u',str(HERE/'run_paper900.py'),'_worker'] + sys.argv[2:]
        log = a.work_root/('launcher_paper900_' + a.trial + '.log')
        shell = 'exec ' + ' '.join(shlex.quote(x) for x in command) + ' >> ' + shlex.quote(str(log)) + ' 2>&1'
        runtime.cmd(runtime.tmux('new-session','-d','-s',session,shell))
        print('[LOG]', log)
        return
    if a.command == 'cleanup':
        import subprocess
        code = subprocess.run(runtime.tmux('has-session','-t','worker_paper900_'+a.trial),
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE).returncode
        if code == 0:
            raise RuntimeError('Worker is still active: request stop and wait before cleanup')
    forwarded = [str(HERE/'run_v7.py'),a.command,'--topology','original18','--trial',a.trial,
                 '--runtime-root',str(a.runtime_root),'--work-root',str(a.work_root),
                 '--lower-python',a.lower_python]
    sys.argv = forwarded
    runtime.main()


if __name__ == '__main__':
    main()
