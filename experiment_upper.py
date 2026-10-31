"""Unified process-local entry for V7 and the archived V6 comparison methods."""
import argparse
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import sys
from common import write

HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def tensor_hash(models):
    h = hashlib.sha256()
    for label, model in models:
        for key, tensor in sorted(model.state_dict().items()):
            value = tensor.detach().cpu().contiguous()
            h.update((label + key + str(value.dtype) + str(tuple(value.shape))).encode())
            h.update(value.numpy().tobytes())
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--method', choices=['ohgrl', 'chen', 'ppo', 'ssp', 'rand_sp'], required=True)
    p.add_argument('--root', required=True)
    p.add_argument('--common', required=True)
    p.add_argument('--variant', choices=['full', 'uniform_high', 'short_paths'], default='full')
    p.add_argument('--budget', type=int, default=0)
    p.add_argument('--probe', action='store_true')
    p.add_argument('--snapshot')
    a, argv = p.parse_known_args()
    mode = argv[0]
    if a.variant != 'full' and (a.method != 'ohgrl' or mode != 'eval-http'):
        raise ValueError('Replacement policies require frozen OHGRL evaluation')
    root = Path(a.root)
    cfg = read(argv[argv.index('--config') + 1])
    out = Path(argv[argv.index('--output') + 1])
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / 'core'))
    sys.path.insert(0, str(HERE))
    pp = load('path_performance', Path(a.common) / 'path_performance.py')
    pp.validate_profile(cfg['normalization_profile'])
    pp.load_profile = lambda path=None: dict(cfg['normalization_profile'])
    import torch
    torch.set_num_threads(1)
    original_load = torch.load
    if 'weights_only' not in inspect.signature(original_load).parameters:
        def compat_load(*args, **kwargs):
            kwargs.pop('weights_only', None)
            return original_load(*args, **kwargs)
        torch.load = compat_load
    mod = load('paper_method_runner', root / 'run.py')
    if a.method == 'chen':
        if a.probe:
            state = mod.Snapshot(read(a.snapshot), cfg)
            agent = mod.DualAgent.load(argv[argv.index('--checkpoint')+1], state) if '--checkpoint' in argv else mod.DualAgent(cfg, state)
            before = tensor_hash([('lower', agent.lower), ('master', agent.master)])
            payload = agent.decide(state, deterministic=True, tag='C', destination_mode='distribution').payload
            out.mkdir(parents=True, exist_ok=False)
            agent.save(out/'initial.pt')
            loaded = mod.DualAgent.load(out/'initial.pt', state)
            if before != tensor_hash([('lower', loaded.lower), ('master', loaded.master)]):
                raise ValueError('Chen checkpoint reload changed weights')
            (out/'probe_policy.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
            for path in payload['low_act_dict'].values():
                pp.score(state.data, path, cfg['normalization_profile'])
            write_frozen(out, before, tensor_hash([('lower', agent.lower), ('master', agent.master)]), 0)
            return
        # Keep the original 2N-1/first-N FLX mechanism: N=8 -> 15 feedback/update.
        # 900 and smoke=30 are exact multiples; no partial-window update is invented.
        if mode == 'train-http':
            window = 2 * cfg['horizon'] - 1
            if a.budget <= 0 or a.budget % window:
                raise ValueError('Chen budget must be a positive multiple of its FLX window')
            argv += ['--updates', str(a.budget // window)]
        agent_class = mod.DualAgent
        original_init = agent_class.__init__
        tracked = []
        def init(agent, *args, **kwargs):
            original_init(agent, *args, **kwargs)
            tracked.append(agent)
        agent_class.__init__ = init
        initial_hash = []
        original_decide = agent_class.decide
        def decide(agent, *args, **kwargs):
            if mode == 'eval-http' and not initial_hash:
                initial_hash.append(tensor_hash([('lower', agent.lower), ('master', agent.master)]))
            return original_decide(agent, *args, **kwargs)
        agent_class.decide = decide
        status = 1
        try:
            status = mod.main(argv)
        finally:
            if (out / 'events.jsonl').exists():
                rows = [json.loads(x) for x in (out / 'events.jsonl').read_text(encoding='utf-8').splitlines()]
                ends = [r for r in rows if r.get('event') == 'end']
                end = ends[-1] if ends else {}
                feedback = sum(r.get('event') == 'feedback' for r in rows)
                updates = sum(r.get('event') == 'update' for r in rows)
                summary = dict(outcome='completed' if status == 0 else ('stopped' if status == 130 else 'failed'),
                               accepted_feedback=feedback, updates=updates,
                               errors=[r.get('error') for r in rows if r.get('event') == 'failed'],
                               unused_window_samples=end.get('unused_window_samples'))
                (out / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
                if mode == 'eval-http' and initial_hash and tracked:
                    final = tensor_hash([('lower', tracked[-1].lower), ('master', tracked[-1].master)])
                    write_frozen(out, initial_hash[0], final, updates)
        if status not in (0, 130):
            raise RuntimeError('Chen failed; inspect events.jsonl')
        return

    if a.method in ['ssp', 'rand_sp']:
        static_init = mod.Pipeline.__init__
        def init_static(pipeline, *args, **kwargs):
            static_init(pipeline, *args, **kwargs)
            manifest = read(out/'manifest.json')
            manifest.update(method=a.method, training='none',
                            high_policy='uniform' if a.method=='rand_sp' else 'nearest_destination',
                            path_policy='V6 fixed globally shortest paths')
            write(out/'manifest.json', manifest)
        mod.Pipeline.__init__ = init_static
        original = mod.decide
        from route_variants import shortest_paths
        def static_decide(data, config):
            payload, behavior = original(data, config)
            payload['low_act_dict'] = shortest_paths(data, config)
            if a.method == 'rand_sp':
                payload['high_act_list'] = [[1. / len(config['destinations'])] * len(config['destinations'])]
                behavior['choice_key'] = None
            else:
                behavior['path'] = payload['low_act_dict'][behavior['choice_key']]
            return payload, behavior
        mod.decide = static_decide
    elif a.method == 'ohgrl' and mode == 'eval-http':
        original_decide = mod.Models.decide
        from route_variants import shortest_paths
        def decide(model, state, *args, **kwargs):
            payload = original_decide(model, state, *args, **kwargs)
            if a.variant == 'uniform_high':
                payload['high_act_list'] = [[1. / len(model.pairs)] * len(model.pairs)]
            if a.variant == 'short_paths':
                payload['low_act_dict'] = shortest_paths(state.data, model.cfg)
            return payload
        mod.Models.decide = decide
    if mode == 'eval-http' and a.method in ['ohgrl', 'ppo']:
        original_init = mod.Pipeline.__init__
        original_run = mod.Pipeline.run
        def init(pipeline, *args, **kwargs):
            original_init(pipeline, *args, **kwargs)
            pipeline._frozen_initial = tensor_hash([('high', pipeline.models.high.actor), ('low', pipeline.models.low.actor)])
        def run(pipeline):
            try:
                return original_run(pipeline)
            finally:
                final = tensor_hash([('high', pipeline.models.high.actor), ('low', pipeline.models.low.actor)])
                summary = read(out / 'summary.json')
                write_frozen(out, pipeline._frozen_initial, final, summary['updates'])
        mod.Pipeline.__init__ = init
        mod.Pipeline.run = run
    if a.probe:
        def probe(pipeline):
            data = read(a.snapshot)
            if a.method in ['ssp', 'rand_sp']:
                payload, _ = mod.decide(data, pipeline.cfg)
            else:
                models = pipeline.models
                before = tensor_hash([('high', models.high.actor), ('low', models.low.actor)])
                state = models.state(data)
                if a.method == 'ppo':
                    payload, _ = models.decide(state, models.actors(), deterministic=True, destination_mode='distribution')
                else:
                    payload = models.decide(state, models.actors(), deterministic=True)
                write_frozen(out, before, tensor_hash([('high', models.high.actor), ('low', models.low.actor)]), 0)
            for path in payload['low_act_dict'].values():
                pp.score(data, path, pipeline.cfg['normalization_profile'])
            (out/'probe_policy.json').write_text(json.dumps(payload, indent=2), encoding='utf-8')
            pipeline.writer.close()
            pipeline.file.close()
        mod.Pipeline.run = probe
    sys.argv = [str(root / 'run.py')] + argv
    mod.main()


def write_frozen(out, before, after, updates):
    result = dict(before=before, after=after, updates=updates, passed=before == after and updates == 0)
    (out / 'frozen_audit.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    if not result['passed']:
        raise ValueError('Frozen evaluation changed actor parameters or performed updates')


if __name__ == '__main__':
    main()
