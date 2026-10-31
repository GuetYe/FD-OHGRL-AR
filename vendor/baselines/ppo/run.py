#!/usr/bin/env python3
"""PPO-DDPG baseline for the verified per-flow environment."""
import argparse
from collections import deque
import json
import math
from pathlib import Path
import random
import time
import traceback
import requests
import torch
from transport import Transport
from model_adapter import setting
from path_performance import load_profile, score
from protocol import FeedbackGate


class Pipeline(Transport):
    def interact(self, before, actors):
        start = time.monotonic()
        distribution = self.args.mode=='eval-http' and self.args.destination_mode=='distribution'
        payload, behavior = self.models.decide(before, actors,
            deterministic=self.args.mode=='eval-http' and not self.args.stochastic_eval,
            destination_mode='distribution' if distribution else 'sample')
        inference_seconds = time.monotonic()-start
        self.publications += 1
        payload.update(control_tag='T' if self.args.mode=='train-http' else 'C',
                       policy_version='PPO-DDPG-%s-%08d'%(self.run_id,self.publications),
                       published_at=time.time(), state_timestamp=str(before.data['timestamp']))
        selected_key = None if distribution else self.models.pairs[behavior['action']]
        allowed_keys = {key for key,p in zip(self.models.pairs,payload['high_act_list'][0]) if p>0}
        # Retry the SAME sampled action after transient failure: do not resample
        # conditioned on transport success. No subsequent publication until feedback.
        deadline = time.monotonic()+self.cfg['feedback_timeout']
        report_at = 0
        while True:
            try:
                pre = self.get('/api/state')['timestamp']
                self.log('publish_attempt',action=payload,behavior=behavior)
                response = requests.post(self.args.url.rstrip('/')+'/api/action',json=payload,
                                         timeout=self.cfg['http_timeout'])
                response.raise_for_status(); echo=response.json()
                if any(echo.get(k)!=payload[k] for k in ['control_tag','high_act_list','low_act_dict','policy_version','published_at']):
                    raise RuntimeError('action echo mismatch')
                post = self.get('/api/state')['timestamp']
                break
            except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
                if time.monotonic()>=deadline:raise TimeoutError('policy publication timeout: '+str(exc))
                if time.monotonic()-report_at>=10:self.log('control_retry',reason=str(exc));report_at=time.monotonic()
                self.stop.wait(self.cfg['poll_interval'])
        gate = FeedbackGate(payload,pre,post,self.topo,self.cfg['feedback_settle_snapshots'])
        self.log('action',policy_version=payload['policy_version'],probabilities=payload['high_act_list'][0],
                 behavior=behavior,paths=payload['low_act_dict'],inference_seconds=inference_seconds)
        start = time.monotonic()
        while True:
            elapsed = time.monotonic()-start
            if elapsed>=self.cfg['feedback_timeout']:
                raise TimeoutError('feedback timeout: '+repr(dict(gate.counts)))
            if elapsed<self.cfg['control_interval']:
                self.stop.wait(self.cfg['poll_interval']);continue
            try:
                feedback = self.get('/api/state')
                # General gate allows all candidate keys; PPO must additionally
                # prove execution of its sampled categorical destination.
                if (feedback.get('choice_key') not in allowed_keys or
                        (not distribution and feedback.get('choice_key')!=selected_key)):
                    gate.matching.clear();gate.counts['sampled_destination_mismatch']+=1
                    ok,reason=False,'sampled_destination_mismatch'
                else:
                    ok,reason=gate.inspect(feedback)
                if ok:break
                if time.monotonic()-report_at>=10:
                    self.log('waiting_feedback',reason=reason,wait_seconds=elapsed);report_at=time.monotonic()
            except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
                gate.matching.clear();gate.counts['invalid_or_unavailable_state']+=1
                if time.monotonic()-report_at>=10:self.log('feedback_retry',reason=str(exc));report_at=time.monotonic()
            self.stop.wait(self.cfg['poll_interval'])
        high,low,reward=self.models.samples(before,feedback,payload)
        after=high[3]
        with torch.no_grad():next_value=float(self.models.high.values([after]).item())
        transition=dict(behavior,state=before,next_state=after,next_value=next_value,reward=reward,
                        policy_version=payload['policy_version'],flow_id=feedback['choice_flow_id'])
        self.accepted+=1
        performance=score(feedback,feedback['applied_path'],self.cfg['normalization_profile'])
        self.log('feedback',step=self.accepted,policy_version=payload['policy_version'],
                 flow_id=feedback['choice_flow_id'],choice_key=feedback['choice_key'],path=feedback['applied_path'],
                 reward_proxy=reward,wait_seconds=elapsed,matched_frames=len(gate.matching),
                 rejection_counts=dict(gate.counts),behavior=behavior,
                 path_metrics=list(performance['metrics']),normalized_metrics=list(performance['normalized']),
                 normalization_edge_count=performance['normalization_edge_count'])
        self.scalar('Train/Reward/path_reward_proxy',reward,self.accepted)
        self.scalar('Reward/observed_path',reward,self.accepted)
        for name,value in zip(['bottleneck_free_bw_mbps','delay_ms','loss_fraction','queue_packets'],performance['metrics']):
            self.scalar('Train/PathRaw/'+name,value,self.accepted)
        for name,value in zip(['bottleneck_free_bw','delay','loss','queue'],performance['normalized']):
            self.scalar('Train/PathNormalized/'+name,value,self.accepted)
        self.scalar('Train/Path/hops',performance['hops'],self.accepted)
        self.scalar('Runtime/feedback_wait_seconds',elapsed,self.accepted)
        return transition,low

    def run(self):
        self.accepted=0;self.updates=0;self.publications=0;self.low_updates=0;self.high_passes=0;self.low_draws=0
        low_replay=deque(maxlen=self.cfg['low_capacity']);rng=random.Random(self.cfg['seed']+1)
        errors=[];outcome='stopped';rollout=[]
        self.log('start',mode=self.args.mode,algorithm=self.cfg['algorithm'])
        try:
            before=self.models.state(self.initial())
            training=self.args.mode=='train-http'
            while True:
                # Collection and optimization are deliberately disjoint.
                actors=self.models.actors();rollout=[]
                count=self.cfg['rollout_steps'] if training else self.args.steps-self.accepted
                if training and self.args.feedback_budget:
                    count=min(count,self.args.feedback_budget-self.accepted)
                for _ in range(count):
                    transition,low=self.interact(before,actors)
                    if training:
                        rollout.append(transition)
                        low_replay.extend((sample,) for sample in low)
                    # Exact chaining is required for GAE, even if another
                    # telemetry endpoint currently exposes a fresher frame.
                    before=transition['next_state']
                if not training:outcome='completed';break
                metrics=self.models.high.update(rollout)
                self.high_passes+=metrics['sample_passes']
                if len(low_replay)>=max(self.cfg['low_warmup'],self.cfg['low_batch']):
                    for _ in range(len(rollout)*self.cfg['low_updates_per_feedback']):
                        self.models.low.update(rng.sample(list(low_replay),self.cfg['low_batch']))
                        self.low_updates+=1;self.low_draws+=self.cfg['low_batch']
                for model in [self.models.high.actor,self.models.high.critic,self.models.low.actor,self.models.low.critic]:
                    if any(not torch.isfinite(p).all() for p in model.parameters()):
                        raise FloatingPointError('nonfinite model parameters')
                self.updates+=1
                self.save_actors('latest.pt',self.models.actors(),self.updates)
                self.log('update',update=self.updates,accepted_feedback=self.accepted,
                         low_replay=len(low_replay),low_updates=self.low_updates,low_replay_draws=self.low_draws,
                         high_sample_passes=self.high_passes,**metrics)
                for k,v in metrics.items():self.scalar('PPO/'+k,v,self.updates)
                self.scalar('Training/accepted_feedback',self.accepted,self.updates)
                self.scalar('Training/low_replay_size',len(low_replay),self.updates)
                rollout.clear()  # No reuse under the next behavior policy.
                if self.updates>=self.args.updates or (self.args.feedback_budget and self.accepted>=self.args.feedback_budget):
                    outcome='completed';break
                # GAE has ended; obtain a fresh start observation after optimization.
                before=self.models.state(self.initial())
        except KeyboardInterrupt:
            self.log('interrupted',discarded_rollout_samples=len(rollout))
        except Exception as exc:
            errors.append(repr(exc));outcome='failed';self.log('fatal',error=repr(exc),traceback=traceback.format_exc())
        finally:
            summary=dict(outcome=outcome,updates=self.updates,accepted_feedback=self.accepted,publications=self.publications,
                         low_updates=self.low_updates,high_sample_passes=self.high_passes,low_replay_draws=self.low_draws,
                         errors=errors,elapsed_seconds=time.monotonic()-self.started,
                         note='Verified feedback is not application completion; use common lower-layer flow records for FCT and cleanup.')
            (self.output/'summary.json').write_text(json.dumps(summary,indent=2))
            self.writer.flush();self.writer.close();self.file.close()
        if errors:raise RuntimeError(errors[0])


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
    p.add_argument('--destination-mode',choices=['distribution','legacy'],default='distribution',
                   help='eval only: publish raw destination probabilities per flow (default) or legacy one-hot')
    p.add_argument('--device',choices=['cpu','cuda'])
    a=p.parse_args()
    if a.mode=='eval-http' and a.destination_mode=='distribution' and a.stochastic_eval:
        p.error('distribution evaluation keeps deterministic lower paths; do not combine --stochastic-eval')
    if a.updates<1 or a.steps<1 or a.feedback_budget<0:p.error('invalid budget')
    if a.mode=='eval-http' and not a.checkpoint:p.error('eval-http requires --checkpoint')
    cfg=json.loads(Path(a.config).read_text());cfg['normalization_profile']=load_profile()
    cfg['model_hyperparameters']={k:getattr(setting,k) for k in [
        'high_hid_dim','low_hid_dim','high_actor_lr','high_critic_lr','low_actor_lr','low_critic_lr',
        'high_gamma','low_gamma','low_tau','high_dropout','low_dropout','terminal_reward','terminal_punishment','ordering_attr_list']}
    if a.device:cfg['device']=a.device
    if cfg['algorithm']!='PPO-DDPG':raise ValueError('this runner is exclusively PPO-DDPG')
    for k in ['rollout_steps','ppo_epochs','ppo_minibatch','low_batch','low_warmup','low_capacity','low_updates_per_feedback','torch_threads']:
        if type(cfg[k]) is not int or cfg[k]<1:raise ValueError(k+' must be a positive integer')
    if cfg['low_capacity']<max(cfg['low_batch'],cfg['low_warmup']):raise ValueError('insufficient low replay capacity')
    for k in ['http_timeout','feedback_timeout','poll_interval','max_grad_norm']:
        if not math.isfinite(cfg[k]) or cfg[k]<=0:raise ValueError(k+' must be positive')
    for k in ['control_interval','entropy_coef']:
        if not math.isfinite(cfg[k]) or cfg[k]<0:raise ValueError(k+' must be nonnegative')
    if not 0<cfg['ppo_clip']<1 or not 0<=cfg['gae_lambda']<=1:raise ValueError('invalid PPO clip/lambda')
    if type(cfg['feedback_settle_snapshots']) is not int or cfg['feedback_settle_snapshots']<0:raise ValueError('invalid settling frames')
    Pipeline(a,cfg).run()

if __name__=='__main__':main()
