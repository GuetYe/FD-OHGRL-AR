#!/usr/bin/env python3
"""OHGRL per-flow training/evaluation. Run separately from Chen on the same API."""
import argparse
import copy
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import queue
import random
import sys
import threading
import time
import traceback
import uuid
from collections import deque
sys.path.insert(0,str(Path(__file__).resolve().parent/'core'))
import numpy as np
import requests
import torch
from tensorboardX import SummaryWriter
from path_performance import load_profile, validate_snapshot, score
from model_adapter import Models, setting
from protocol import FeedbackGate, topology

class Replay:
    def __init__(self,capacity,seed):
        self.items=deque(maxlen=capacity);self.lock=threading.Lock();self.rng=random.Random(seed)
    def add(self,sample):
        with self.lock:self.items.append((sample,))
    def sample(self,n):
        with self.lock:return self.rng.sample(list(self.items),n)
    def size(self):
        with self.lock:return len(self.items)

class Pipeline:
    def __init__(self,args,cfg):
        self.args=args;self.cfg=cfg
        self.output=Path(args.output);self.output.mkdir(parents=True,exist_ok=False)
        self.stop=threading.Event();self.budget_reached=threading.Event();self.lock=threading.RLock();self.log_lock=threading.Lock()
        self.pending=None;self.accepted=0;self.updates=0;self.publications=0
        self.replay_draws_high=0;self.replay_draws_low=0;self.errors=[]
        self.started=time.monotonic();self.run_id=uuid.uuid4().hex
        self.high=Replay(cfg['high_capacity'],cfg['seed']);self.low=Replay(cfg['low_capacity'],cfg['seed']+1)
        self.eval_queue=queue.Queue(maxsize=2);self.best_score=None
        self.file=open(self.output/'events.jsonl','a',encoding='utf-8',buffering=1)
        self.writer=SummaryWriter(str(self.output/'tensorboard'),flush_secs=10)
        torch.manual_seed(cfg['seed']);np.random.seed(cfg['seed']);random.seed(cfg['seed'])
        torch.set_num_threads(cfg['torch_threads'])
        self.models=Models(cfg)
        if args.mode=='eval-http':
            load_options={'map_location':setting.device}
            if 'weights_only' in inspect.signature(torch.load).parameters:load_options['weights_only']=False
            ckpt=torch.load(args.checkpoint,**load_options)
            for field in ['algorithm','source','destinations','normalization_profile','model_hyperparameters','telemetry_semantics_version','mask_version','topology_spec','node_feature_names']:
                if ckpt['config'].get(field)!=cfg[field]:raise ValueError('checkpoint incompatible: '+field)
            self.models.high.actor.load_state_dict(ckpt['high_actor'])
            self.models.low.actor.load_state_dict(ckpt['low_actor'])
        self.latest=self.models.actors()
        self.save_actors('initial.pt',self.latest,0)
        self.topo=None
        (self.output/'manifest.json').write_text(json.dumps(dict(config=cfg,arguments=vars(args),run_id=self.run_id,
            source_sha256={str(p.relative_to(Path(__file__).parent)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),Path(__file__).with_name('model_adapter.py'),Path(__file__).parent/'core/algorithms_model.py']},
            policy='one in-flight publication; verified feedback only; no warmup replay',
            checkpoint='actors only; eval load is not a training resume',
            profile_sha256=hashlib.sha256((Path(__file__).parent/'core/network_profile.json').read_bytes()).hexdigest()),indent=2))
    def log(self,event,**data):
        row=dict(event=event,wall_time=time.time(),elapsed_seconds=time.monotonic()-self.started,**data)
        with self.log_lock:
            self.file.write(json.dumps(row,ensure_ascii=False,allow_nan=False)+'\n')
            print(json.dumps(row,ensure_ascii=False,allow_nan=False),flush=True)
    def scalar(self,tag,value,step):
        if math.isfinite(float(value)):
            with self.log_lock:self.writer.add_scalar(tag,float(value),step)
    def get(self,endpoint):
        r=requests.get(self.args.url.rstrip('/')+endpoint,timeout=self.cfg['http_timeout']);r.raise_for_status()
        data=r.json();validate_snapshot(data,self.cfg['normalization_profile'])
        if data.get('telemetry_semantics_version')!=self.cfg['telemetry_semantics_version']:
            raise ValueError('telemetry version mismatch; deploy the matching lower-layer code')
        sig=topology(data)
        expected=self.cfg['topology_spec']
        if set(sig[0])!=set(expected['nodes']) or {tuple(sorted(e)) for e in sig[1]}!={tuple(sorted(e)) for e in expected['edges']}:raise ValueError('V7 topology differs from selected profile')
        if any(type(n) is not int for n in sig[0]):raise ValueError('switch IDs must be integers')
        if self.cfg['source'] not in sig[0] or not set(self.cfg['destinations']) <= set(sig[0]):raise ValueError('missing endpoints')
        for edge in data['edges']:
            if not math.isfinite(float(edge.get('distance',0))) or float(edge.get('distance',0))<=0:raise ValueError('distance must be positive')
        if len(sig[1])!=self.cfg['normalization_profile']['topology_edge_count']:raise ValueError('wrong topology edge count')
        if not data.get('timestamp'):raise ValueError('missing telemetry timestamp')
        if self.topo is not None and sig!=self.topo:raise ValueError('topology changed')
        return data
    def initial(self):
        deadline=time.monotonic()+self.cfg['feedback_timeout'];last=0
        while not self.stop.is_set():
            try:
                data=self.get('/api/request');self.topo=topology(data);return data
            except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
                if time.monotonic()>=deadline:raise TimeoutError('no valid initial topology: '+str(exc))
                if time.monotonic()-last>=10:self.log('waiting_topology',reason=str(exc));last=time.monotonic()
                self.stop.wait(self.cfg['poll_interval'])
        raise RuntimeError('stopped before initial topology')
    def save_actors(self,name,actors,update):
        payload={'format':'OHGRL-v7-reachable-actors-v1','config':self.cfg,'updates':update,
                 'high_actor':actors[0].state_dict(),'low_actor':actors[1].state_dict()}
        target=self.output/name;tmp=target.with_suffix('.tmp');torch.save(payload,tmp);os.replace(tmp,target)
    def control_loop(self):
        data=self.initial()
        last_report=0
        retry_started=time.monotonic()
        while not self.stop.is_set():
            with self.lock:pending=self.pending;actors=self.latest
            if pending is not None or self.budget_reached.is_set():
                self.stop.wait(self.cfg['poll_interval']);retry_started=time.monotonic();continue
            try:
                data=self.get('/api/request')
                before=self.models.state(data)
                inference_start=time.monotonic()
                payload=self.models.decide(before,actors,deterministic=self.args.mode=='eval-http' and not self.args.stochastic_eval)
                inference_seconds=time.monotonic()-inference_start
                payload['control_tag']='T' if self.args.mode=='train-http' else 'C'
                self.publications+=1
                payload.update(policy_version='OHGRL-%s-%08d'%(self.run_id,self.publications),published_at=time.time(),
                               state_timestamp=str(data['timestamp']))
                before_stamp=self.get('/api/state')['timestamp']
                self.log('publish_attempt',action=payload)
                r=requests.post(self.args.url.rstrip('/')+'/api/action',json=payload,timeout=self.cfg['http_timeout'])
                r.raise_for_status();echo=r.json()
                if any(echo.get(k)!=payload[k] for k in ['control_tag','high_act_list','low_act_dict','policy_version','published_at']):
                    raise RuntimeError('action echo mismatch')
                # Exclude the immediately post-publication frame, matching Chen's bridge.
                post=self.get('/api/state')['timestamp']
                gate=FeedbackGate(payload,before_stamp,post,self.topo,self.cfg['feedback_settle_snapshots'])
                with self.lock:self.pending={'payload':payload,'before':before,'gate':gate,'started':time.monotonic()}
                self.log('action',policy_version=payload['policy_version'],probabilities=payload['high_act_list'][0],paths=payload['low_act_dict'],inference_seconds=inference_seconds)
                retry_started=time.monotonic()
            except (requests.RequestException,ValueError,KeyError,AssertionError,TypeError) as exc:
                if time.monotonic()-retry_started>self.cfg['feedback_timeout']:raise TimeoutError('cannot publish policy: '+str(exc))
                if time.monotonic()-last_report>=10:self.log('control_retry',reason=str(exc));last_report=time.monotonic()
                self.stop.wait(self.cfg['poll_interval'])
    def collector_loop(self):
        report_at=0
        while not self.stop.is_set():
            with self.lock:p=self.pending
            if p is None:
                self.stop.wait(self.cfg['poll_interval']);continue
            elapsed=time.monotonic()-p['started']
            if elapsed>self.cfg['feedback_timeout']:
                raise TimeoutError('feedback timeout: '+repr(dict(p['gate'].counts)))
            if elapsed<self.cfg['control_interval']:
                self.stop.wait(self.cfg['poll_interval']);continue
            try:
                data=self.get('/api/state')
                ok,reason=p['gate'].inspect(data)
                if ok:
                    high,low,reward=self.models.samples(p['before'],data,p['payload'])
                    if self.args.mode=='train-http':
                        self.high.add(high)
                        for sample in low:self.low.add(sample)
                    with self.lock:
                        self.accepted+=1;step=self.accepted
                        # Keep publication blocked until sample insertion is complete.
                        self.pending=None
                        if self.args.mode=='eval-http' and step>=self.args.steps:
                            self.stop.set()
                        elif self.args.feedback_budget and step>=self.args.feedback_budget:
                            self.budget_reached.set()
                    performance=score(data,data['applied_path'],self.cfg['normalization_profile'])
                    self.log('feedback',step=step,policy_version=p['payload']['policy_version'],
                             flow_id=data['choice_flow_id'],choice_key=data['choice_key'],path=data['applied_path'],
                             reward_proxy=reward,wait_seconds=elapsed,matched_frames=len(p['gate'].matching),
                             rejection_counts=dict(p['gate'].counts),path_metrics=list(performance['metrics']),
                             normalized_metrics=list(performance['normalized']),
                             normalization_edge_count=performance['normalization_edge_count'])
                    self.scalar('Reward/observed_path',reward,step)
                    self.scalar('Train/Reward/path_reward_proxy',reward,step)
                    for name,value in zip(['bottleneck_free_bw_mbps','delay_ms','loss_fraction','queue_packets'],performance['metrics']):
                        self.scalar('Train/PathRaw/'+name,value,step)
                    for name,value in zip(['bottleneck_free_bw','delay','loss','queue'],performance['normalized']):
                        self.scalar('Train/PathNormalized/'+name,value,step)
                    self.scalar('Train/Path/hops',performance['hops'],step)
                    self.scalar('Runtime/feedback_wait_seconds',elapsed,step)
                    if self.args.mode=='eval-http' and step>=self.args.steps:
                        self.stop.set()
                    elif self.args.feedback_budget and step>=self.args.feedback_budget:
                        self.budget_reached.set()
                elif time.monotonic()-report_at>=10:
                    self.log('waiting_feedback',reason=reason,wait_seconds=elapsed,policy_version=p['payload']['policy_version'])
                    report_at=time.monotonic()
            except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
                if p['gate'].accepted:raise RuntimeError('accepted feedback could not be converted: '+str(exc))
                p['gate'].matching.clear()
                p['gate'].counts['invalid_or_unavailable_state']+=1
                if time.monotonic()-report_at>=10:self.log('feedback_retry',reason=str(exc));report_at=time.monotonic()
            self.stop.wait(self.cfg['poll_interval'])
    def learner_loop(self):
        while not self.stop.is_set():
            enough=(self.high.size()>=max(self.cfg['high_warmup'],self.cfg['high_batch']) and
                    self.low.size()>=max(self.cfg['low_warmup'],self.cfg['low_batch']))
            with self.lock:accepted=self.accepted
            # Bound replay use explicitly. A fresh feedback grants configurable update credit.
            credit=max(0,accepted-self.cfg['high_warmup']+1)*self.cfg['updates_per_feedback']
            if not enough or self.updates>=credit:
                if self.budget_reached.is_set():
                    self.stop.set();break
                self.stop.wait(.1);continue
            hb=self.high.sample(self.cfg['high_batch']);lb=self.low.sample(self.cfg['low_batch'])
            high_metrics=self.models.high.update(hb) or {};low_metrics=self.models.low.update(lb) or {}
            diagnostics=dict(high=high_metrics,low=low_metrics)
            for group,metrics in diagnostics.items():
                for name,value in metrics.items():
                    if not math.isfinite(float(value)):raise FloatingPointError(group+" nonfinite "+name)
                    self.scalar("Diagnostics/"+group+"/"+name,value,self.updates+1)
            for model in [self.models.high.actor,self.models.low.actor]:
                if any(not torch.isfinite(p).all() for p in model.parameters()):raise FloatingPointError('nonfinite actor parameters')
            self.updates+=1
            actors=self.models.actors()
            with self.lock:self.latest=actors
            self.replay_draws_high+=len(hb);self.replay_draws_low+=len(lb)
            self.save_actors('latest.pt',actors,self.updates)
            try:self.eval_queue.put_nowait((self.updates,actors))
            except queue.Full:self.log('evaluation_queue_full',update=self.updates)
            self.log('update',update=self.updates,accepted_feedback=accepted,high_replay=self.high.size(),low_replay=self.low.size(),
                     high_replay_draws=self.replay_draws_high,low_replay_draws=self.replay_draws_low,diagnostics=diagnostics)
            self.scalar('Training/high_replay_size',self.high.size(),self.updates)
            self.scalar('Training/low_replay_size',self.low.size(),self.updates)
            self.scalar('Training/accepted_feedback',accepted,self.updates)
            self.scalar('Training/high_sample_reuse_ratio',self.replay_draws_high/max(1,accepted),self.updates)
            self.scalar('Training/batch_reward',np.mean([s[0][2] for s in hb]),self.updates)
            if self.updates>=self.args.updates:self.stop.set();break
            self.stop.wait(self.cfg['train_interval'])
    def evaluator_loop(self):
        incumbent=None
        while not self.stop.is_set() or not self.eval_queue.empty():
            try:update,actors=self.eval_queue.get(timeout=.2)
            except queue.Empty:continue
            if self.high.size()==0:continue
            batch=self.high.sample(min(self.cfg['evaluation_states'],self.high.size()))
            states=[s[0][0] for s in batch]
            # Candidate and incumbent on the SAME states, no network publication.
            value=self.models.evaluate_proxy(states,actors)
            old=self.models.evaluate_proxy(states,incumbent) if incumbent else None
            if old is None or value>old:
                incumbent=actors;self.save_actors('best_proxy.pt',actors,update)
            self.log('evaluation_proxy',update=update,reward_proxy=value,incumbent_proxy=old)
            self.scalar('Evaluation/offline_expected_path_proxy',value,update)
    def guard(self,fn):
        try:fn()
        except Exception as exc:
            self.errors.append(repr(exc));self.log('fatal',thread=fn.__name__,error=repr(exc),traceback=traceback.format_exc());self.stop.set()
    def run(self):
        fns=[self.control_loop,self.collector_loop]
        if self.args.mode=='train-http':fns += [self.learner_loop,self.evaluator_loop]
        threads=[threading.Thread(target=self.guard,args=(fn,),name=fn.__name__) for fn in fns]
        self.log('start',mode=self.args.mode,algorithm=self.cfg['algorithm'])
        try:
            for t in threads:t.start()
            while any(t.is_alive() for t in threads):
                for t in threads:t.join(.2)
        except KeyboardInterrupt:
            self.stop.set();self.log('interrupted')
            for t in threads:t.join()
        finally:
            self.stop.set()
            summary=dict(updates=self.updates,accepted_feedback=self.accepted,publications=self.publications,
                         high_replay_draws=self.replay_draws_high,low_replay_draws=self.replay_draws_low,
                         elapsed_seconds=time.monotonic()-self.started,errors=self.errors,
                         outcome='failed' if self.errors else ('completed' if ((self.updates>=self.args.updates or self.budget_reached.is_set()) if self.args.mode=='train-http' else self.accepted>=self.args.steps) else 'stopped'),
                         note='Transmission success/FCT/cleanup statistics come from common lower-layer flow records.')
            (self.output/'summary.json').write_text(json.dumps(summary,indent=2))
            self.writer.flush();self.writer.close();self.file.close()
        if self.errors:raise RuntimeError(self.errors[0])

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['train-http','eval-http'])
    p.add_argument('--url',default='http://10.33.107.94:5000')
    p.add_argument('--config',default=str(Path(__file__).with_name('config.json')))
    p.add_argument('--output',required=True)
    p.add_argument('--updates',type=int,default=100)
    p.add_argument('--feedback-budget',type=int,default=0)
    p.add_argument('--steps',type=int,default=100)
    p.add_argument('--checkpoint')
    p.add_argument('--stochastic-eval',action='store_true')
    p.add_argument('--device',choices=['cpu','cuda'])
    a=p.parse_args()
    if a.updates<1 or a.steps<1 or a.feedback_budget<0:p.error('invalid budget')
    if a.mode=='eval-http' and not a.checkpoint:p.error('eval-http requires --checkpoint')
    cfg=json.loads(Path(a.config).read_text())
    cfg['normalization_profile']=load_profile()
    # Apply explicit tuning overrides before constructing models, and persist actual values.
    allowed={'high_actor_lr','high_critic_lr','high_alpha_lr','low_actor_lr','low_critic_lr'}
    overrides=cfg.get('learning_rate_overrides',{})
    if set(overrides)-allowed:raise ValueError('unknown learning-rate override')
    for key,value in overrides.items():
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
            raise ValueError('invalid learning rate: '+key)
        setattr(setting,key,float(value))
    cfg.setdefault('evaluation_destination_mode','distribution')
    if cfg['evaluation_destination_mode'] not in ['argmax','distribution']:raise ValueError('invalid evaluation destination mode')
    cfg['model_hyperparameters']={k:getattr(setting,k) for k in [
        'high_hid_dim','low_hid_dim','high_actor_lr','high_critic_lr','high_alpha_lr',
        'low_actor_lr','low_critic_lr','high_gamma','low_gamma','high_tau','low_tau',
        'high_dropout','low_dropout','terminal_reward','terminal_punishment','ordering_attr_list']}
    cfg['model_hyperparameters']['high_temperature']=float(setting.high_temperature)
    cfg['model_hyperparameters']['target_entropy']=-len(cfg['destinations'])
    if a.feedback_budget and a.mode=='train-http' and a.feedback_budget<max(cfg['high_warmup'],cfg['high_batch']):
        raise ValueError('feedback budget below high warmup/batch')
    if a.device:cfg['device']=a.device
    if cfg['algorithm'] not in ['SAC','DDPG']:raise ValueError('this replay-based runner supports SAC/DDPG, not off-policy PPO')
    for key in ['high_batch','low_batch','high_capacity','low_capacity','high_warmup','low_warmup','updates_per_feedback','torch_threads','evaluation_states']:
        if type(cfg[key]) is not int or cfg[key]<1:raise ValueError(key+' must be a positive integer')
    if cfg['high_capacity']<max(cfg['high_batch'],cfg['high_warmup']) or cfg['low_capacity']<max(cfg['low_batch'],cfg['low_warmup']):raise ValueError('replay capacity below warmup/batch')
    for key in ['http_timeout','feedback_timeout','poll_interval']:
        if not math.isfinite(cfg[key]) or cfg[key]<=0:raise ValueError(key+' must be positive')
    for key in ['control_interval','train_interval']:
        if not math.isfinite(cfg[key]) or cfg[key]<0:raise ValueError(key+' must be nonnegative')
    if type(cfg['feedback_settle_snapshots']) is not int or cfg['feedback_settle_snapshots']<0:raise ValueError('invalid settling frames')
    Pipeline(a,cfg).run()
if __name__=='__main__':main()
