"""Verify completed V7 training artifacts without importing model dependencies."""
import argparse,json,math,sys
from pathlib import Path
from common import HERE,read,write,sha
sys.path.insert(0,str(HERE/'helpers'))
from fixed_records import verify_feedback_rows,validate_result

def audit_run(run):
    run=Path(run);cfg=read(run/'config.json');summary=read(run/'upper/summary.json')
    experiment=read(run/'experiment.json');budget=experiment['feedback_budget'];upper=run/'upper'
    if summary.get('outcome')!='completed' or summary['accepted_feedback']!=budget or summary['errors']:raise ValueError('Training incomplete')
    if cfg['mask_version']!='reachable-simple-path-v7' or cfg['node_feature_names']!=['cur_site','dst_site','visited']:raise ValueError('Wrong V7 feature/mask version')
    if cfg['normalization_profile']['weights']!=[.25]*4:raise ValueError('Reward weights changed')
    index=[json.loads(x) for x in (upper/'dense_actor_index.jsonl').read_text().splitlines() if x.strip()]
    if len(index)!=summary['updates']+1:raise ValueError('Missing actor versions')
    bank=Path(cfg['extra_evaluation']['bank'])
    if sha(bank)!=cfg['extra_evaluation']['bank_sha256']:raise ValueError('Fixed bank changed')
    for i,row in enumerate(index):
        if row['update']!=i:raise ValueError('Noncontiguous actor versions')
        result=read(upper/'dense_fixed_results'/('update_%06d.json'%i));validate_result(result)
        if result['bank_sha256']!=sha(bank):raise ValueError('Wrong bank for score')
    if verify_feedback_rows(upper,index,budget)!=budget:raise ValueError('Feedback rows incomplete')
    for step in range(0,budget+1,300):
        cp=upper/('initial.pt' if step==0 else 'checkpoint_%06d.pt'%step)
        if not cp.is_file():raise ValueError('Missing regular checkpoint: '+str(step))
        update=0 if step==0 else read(str(cp)+'.json')['updates']
        diagnostic=read(upper/'fixed_diagnostics'/('fixed_%06d.json'%step))
        dense=read(upper/'dense_fixed_results'/('update_%06d.json'%update))
        for key in ['expected_reward','argmax_reward']:
            if not math.isclose(diagnostic[key],dense[key],rel_tol=1e-6,abs_tol=1e-5):raise ValueError('Periodic/dense reward mismatch: '+str(step))
    if list((upper/'dense_actor_snapshots').glob('*.pt')):raise ValueError('Unscored snapshots remain')
    done=read(upper/'dense_fixed_DONE.json')
    if not done['capture_complete'] or done['feedback_rows']!=budget:raise ValueError('Incomplete scorer')
    result=dict(passed=True,accepted_feedback=budget,actor_versions=len(index),bank_sha256=sha(bank),states_per_feedback=128,
                regular_checkpoints=budget//300,mask_version=cfg['mask_version'])
    write(run/'v7_artifact_audit.json',result);return result

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--run',required=True);a=parser.parse_args();print(json.dumps(audit_run(a.run),indent=2))
