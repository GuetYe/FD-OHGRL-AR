"""V7 original18 paper experiment matrix; stdlib only, Python 3.6+."""
import copy
from pathlib import Path
from common import HERE, read, make_config

BUDGET = 900
SEEDS = [4101, 4102, 4103]
GAPS = {'low': 4., 'middle': 2., 'high': 1.5}
LEARNERS = ['ohgrl', 'chen', 'ppo']
METHODS = LEARNERS + ['ssp', 'rand_sp']


def job(method, phase='eval', load='middle', seed=4101, variant='full'):
    if method not in METHODS or load not in GAPS:
        raise ValueError('Unknown method/load')
    if variant not in ['full', 'uniform_high', 'short_paths']:
        raise ValueError('Unknown variant')
    if variant != 'full' and (method != 'ohgrl' or phase != 'eval'):
        raise ValueError('Policy replacements are frozen OHGRL evaluation only')
    training = phase in ['train', 'smoke_train']
    if training and method not in LEARNERS:
        raise ValueError('Static baselines do not train')
    smoke = phase.startswith('smoke')
    return dict(id='%s_%s_%s_%s_%s' % (phase, method, variant, load, seed),
                method=method, variant=variant, phase=phase, topology='original18',
                mode='train' if training else 'eval', load=load, gap=GAPS[load],
                seed=seed, flows=30000 if training else (24 if smoke else 300),
                budget=(30 if smoke else BUDGET) if training else 0,
                checkpoint_feedback=BUDGET if method in LEARNERS and not training else None)


def jobs(suite='all'):
    training = [job(m, 'smoke_train', seed=101) for m in LEARNERS]
    training += [job(m, 'train', seed=101) for m in LEARNERS]
    static_smoke = [job(m, 'smoke_eval', seed=4199) for m in ['ssp', 'rand_sp']]
    fixed = [job(m, load=load, seed=s) for m in METHODS for load in GAPS for s in SEEDS]
    replacements = [job('ohgrl', seed=s, variant=v)
                    for v in ['uniform_high', 'short_paths'] for s in SEEDS]
    groups = {'train': training, 'fixed': static_smoke + fixed,
              'ablation': replacements, 'all': training + static_smoke + fixed + replacements}
    if suite not in groups:
        raise ValueError('Unknown suite')
    return groups[suite]


def method_config(method, bank=None):
    if method == 'ohgrl':
        return make_config('original18', bank)
    cfg = copy.deepcopy(read(HERE / 'vendor/baselines' / ('ssp' if method == 'rand_sp' else method) / 'config.json'))
    cfg['normalization_profile'] = make_config('original18')['normalization_profile']
    if method in ['ssp', 'rand_sp']:
        cfg['paper_variant'] = method
    return cfg


def checkpoint_for(j, receipts):
    if j['method'] not in LEARNERS or j['mode'] == 'train':
        return None
    match = [r for r in receipts if r['job']['phase'] == 'train' and r['job']['method'] == j['method']]
    if len(match) != 1:
        raise ValueError('Run the matching 900-feedback training first: ' + j['method'])
    upper = Path(match[0]['run']) / 'upper'
    return upper / ('checkpoint.pt' if j['method'] == 'chen' else 'latest.pt')
