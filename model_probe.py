"""Real V7 actors, prefix replay, gradient updates and actor reload; no network services."""
import argparse,copy,inspect,json,math,sys,tempfile
from pathlib import Path
from common import HERE,make_config,profile,snapshot,write

def main():
    p=argparse.ArgumentParser();p.add_argument('--topology',required=True);p.add_argument('--root',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    root=Path(a.root);sys.path[:0]=[str(root),str(root/'core')]
    import torch,numpy as np
    torch.set_num_threads(1);torch.manual_seed(1234)
    from model_adapter import Models,setting
    from algorithms_model import gen_low_act_input_batch
    cfg=make_config(a.topology)
    for k,v in cfg['learning_rate_overrides'].items():setattr(setting,k,v)
    model=Models(cfg);data=snapshot(profile(a.topology));state=model.state(data)
    actors=model.actors();payload=model.decide(state,actors,deterministic=True)
    for _ in range(3):model.decide(state,actors,deterministic=False)
    high_batch=[];low_batch=[];terminal=0
    for key,path in payload['low_act_dict'].items():
        feedback=copy.deepcopy(data);feedback['applied_path']=path
        high,low,reward=model.samples(state,feedback,payload)
        high_batch.append((high,));low_batch.extend((x,) for x in low)
        for i,(cur,action,r,following,done) in enumerate(low):
            assert cur.visited_path==tuple(path[:i+1]) and following.visited_path==tuple(path[:i+2])
            assert path[i+1] in cur.legal_next_nodes
            assert cur.torch_norm_features.shape[1]==3
            assert bool(following.nodes_order_tensor.any()) != done
            terminal+=int(done)
    # Both nonterminal target policy and current policy are evaluated with their prefix mask.
    low_metrics=model.low.update((low_batch*16)[:16]);high_metrics=model.high.update((high_batch*8)[:8])
    assert terminal==4
    assert all(math.isfinite(float(v)) for group in [low_metrics,high_metrics] for v in group.values())
    out=Path(a.output);out.mkdir(parents=True,exist_ok=False)
    ckpt=out/'probe.pt';torch.save(dict(high_actor=actors[0].state_dict(),low_actor=actors[1].state_dict(),config=cfg),ckpt)
    kw={'map_location':'cpu'}
    if 'weights_only' in inspect.signature(torch.load).parameters:kw['weights_only']=False
    saved=torch.load(str(ckpt),**kw);new=Models(cfg)
    new.high.actor.load_state_dict(saved['high_actor']);new.low.actor.load_state_dict(saved['low_actor'])
    assert new.decide(new.state(data),new.actors(),deterministic=True)==payload
    write(out/'probe.json',dict(topology=a.topology,nodes=len(data['nodes']),edges=len(data['edges']),
        source=cfg['source'],destinations=cfg['destinations'],paths=payload['low_act_dict'],
        destination_probabilities=payload['high_act_list'][0],low_transition_count=len(low_batch),
        terminal_transitions=terminal,gradient_metrics=dict(low=low_metrics,high=high_metrics),
        result='PASS: real inference, prefix replay, terminal target exclusion, gradient updates, actor reload',
        input_kind='synthetic idle topology, not measured network performance'))
    print('[MODEL PASS]',a.topology,flush=True)
if __name__=='__main__':main()
