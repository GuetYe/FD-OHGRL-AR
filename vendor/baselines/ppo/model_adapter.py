"""Hierarchical graph PPO/DDPG with an explicit per-flow sample adapter."""
import copy
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent/'core'))
import torch
import setting
from algorithms_model import LowDDPG, gen_high_act_input_batch, gen_low_act_input_batch
from data_fraction import State
from path_performance import score, link_reward
from protocol import canonical, validate_payload

class Models:
    def __init__(self,cfg):
        self.cfg=cfg
        setting.device=torch.device(cfg['device'])
        setting.src_list=[cfg['source']];setting.dst_list=cfg['destinations']
        setting.src_dst_pair=[str((cfg['source'],d)) for d in cfg['destinations']]
        setting.high_train_model=cfg['algorithm']
        setting.normalization_profile=cfg['normalization_profile'];setting.bate=cfg['normalization_profile']['weights']
        self.pairs=setting.src_dst_pair
        from ppo import OnPolicyPPO
        self.high=OnPolicyPPO(cfg)
        self.low=LowDDPG(len(setting.edge_attr_choice),len(setting.node_attr_choice),setting.low_hid_dim,
                          setting.low_actor_lr,setting.low_critic_lr,setting.low_dropout,setting.low_gamma,
                          setting.low_sigma,setting.low_tau,setting.device)
    def state(self,data):
        return State(canonical(data,self.pairs))
    def actors(self):
        # Called by the learner only, or before threads start.
        high=copy.deepcopy(self.high.actor).eval();low=copy.deepcopy(self.low.actor).eval()
        return high,low
    def decide(self,state,actors,deterministic=False,destination_mode='sample'):
        high,low=actors
        with torch.no_grad():
            if destination_mode not in ('sample','distribution'):
                raise ValueError('invalid destination mode')
            if destination_mode=='distribution':
                dist=self.high.distribution([state],high)
                behavior=dict(action=None,old_log_prob=None,value=float(self.high.values([state]).item()),
                              probabilities=dist.probs[0].cpu().tolist(),generation=self.high.generation)
            else:
                behavior=self.high.behavior(state,high,deterministic)
            # Sample once in the upper layer, then execute that categorical action.
            # Keep the behavior distribution separately; one-hot is NOT its log probability.
            probs=(behavior['probabilities'] if destination_mode=='distribution' else
                   [float(i==behavior['action']) for i in range(len(self.pairs))])
            paths={}
            for dst,key in zip(setting.dst_list,self.pairs):
                path=[self.cfg['source']]
                while path[-1]!=dst and len(path)<len(state.nodes):
                    cur=state.get_low_cur_state(path[-1],dst)
                    act=low(gen_low_act_input_batch([cur]).to(setting.device),deterministic=deterministic)
                    node=list(state.nodes)[int(act.reshape(-1).argmax().item())]
                    if node in path:raise ValueError('actor proposed cyclic path')
                    path.append(node)
                paths[key]=path
        payload={'control_tag':'T','high_act_list':[probs],'low_act_dict':paths}
        validate_payload(payload,state.data,self.cfg['source'],setting.dst_list)
        return payload,behavior
    def samples(self,before,feedback,payload):
        after=self.state(feedback)
        path=feedback['applied_path'];dst=path[-1]
        reward=score(feedback,path,self.cfg['normalization_profile'])['reward']
        # Reward is the observed selected-path result, not fabricated results for unexecuted alternatives.
        high=(before,payload['high_act_list'][0],reward,after)
        low=[]
        for u,v in zip(path,path[1:]):
            state=before.get_low_cur_state(u,dst)
            following=after.get_low_cur_state(v,dst)
            # Preserve execution direction; do not reorder endpoints on a newer graph.
            action={n:int(n==v) for n in before.nodes}
            r=link_reward(feedback,u,v,self.cfg['normalization_profile'])
            done=v==dst
            if done:r+=setting.terminal_reward
            low.append((state,action,r,following,done))
        return high,low,reward
    def evaluate_proxy(self,states,actors):
        values=[]
        for state in states:
            payload,_=self.decide(state,actors)
            values.append(sum(p*score(state.data,payload['low_act_dict'][k],self.cfg['normalization_profile'])['reward']
                              for p,k in zip(payload['high_act_list'][0],self.pairs)))
        return sum(values)/len(values)
