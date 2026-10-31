#!/usr/bin/env python3
"""Classical SSP baseline, Xuan et al. 2000, for the common per-flow environment."""
import argparse,json,math,time,traceback
from pathlib import Path
import requests
from baseline import decide
from transport import Transport
from protocol import FeedbackGate
from path_performance import score,load_profile,validate_snapshot
class Pipeline(Transport):
    def interact(self, before):
        start = time.monotonic()
        payload, behavior = decide(before,self.cfg)
        inference_seconds = time.monotonic()-start
        self.publications += 1
        payload.update(control_tag='C',
                       policy_version='SSP-%s-%08d'%(self.run_id,self.publications),
                       published_at=time.time(), state_timestamp=str(before['timestamp']))
        selected_key = behavior['choice_key']
        # Retry the SAME computed action after transient failure: do not resample
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
                # General gate allows all candidate keys; SSP must additionally
                # prove execution of its selected nearest destination.
                if selected_key is not None and feedback.get('choice_key')!=selected_key:
                    gate.matching.clear();gate.counts['selected_destination_mismatch']+=1
                    ok,reason=False,'selected_destination_mismatch'
                else:
                    ok,reason=gate.inspect(feedback)
                if ok:break
                if time.monotonic()-report_at>=10:
                    self.log('waiting_feedback',reason=reason,wait_seconds=elapsed);report_at=time.monotonic()
            except (requests.RequestException,ValueError,KeyError,TypeError) as exc:
                gate.matching.clear();gate.counts['invalid_or_unavailable_state']+=1
                if time.monotonic()-report_at>=10:self.log('feedback_retry',reason=str(exc));report_at=time.monotonic()
            self.stop.wait(self.cfg['poll_interval'])
        reward=score(feedback,feedback['applied_path'],self.cfg['normalization_profile'])['reward']
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
        return feedback
    def run(self):
        self.publications=0;self.accepted=0;errors=[];outcome='stopped'
        self.log('start',mode='eval-http',algorithm='SSP')
        try:
            self.initial()
            while self.accepted<self.args.steps:
                before=self.initial()
                self.interact(before)
            outcome='completed'
        except KeyboardInterrupt:
            self.log('interrupted')
        except Exception as exc:
            errors.append(repr(exc));outcome='failed'
            self.log('fatal',error=repr(exc),traceback=traceback.format_exc())
        finally:
            summary=dict(outcome=outcome,updates=0,accepted_feedback=self.accepted,publications=self.publications,
                         elapsed_seconds=time.monotonic()-self.started,errors=errors,
                         note='SSP has no training. Application completion/FCT/cleanup come from common lower flow records.')
            (self.output/'summary.json').write_text(json.dumps(summary,indent=2))
            self.writer.flush();self.writer.close();self.file.close()
        if errors:raise RuntimeError(errors[0])

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['decide','eval-http'])
    p.add_argument('--config',default=str(Path(__file__).with_name('config.json')))
    p.add_argument('--url',default='http://10.33.107.94:5000')
    p.add_argument('--output')
    p.add_argument('--steps',type=int,default=100)
    p.add_argument('--snapshot')
    args=p.parse_args();cfg=json.loads(Path(args.config).read_text())
    cfg['normalization_profile']=load_profile()
    if cfg['algorithm']!='SSP' or cfg['routing_metric']!='distance':p.error('this baseline implements SSP with topology distance')
    if args.steps<1:p.error('steps must be positive')
    for k in ['http_timeout','feedback_timeout','poll_interval']:
        if not math.isfinite(cfg[k]) or cfg[k]<=0:p.error(k+' must be positive')
    if not math.isfinite(cfg['control_interval']) or cfg['control_interval']<0:p.error('invalid control interval')
    if cfg['feedback_timeout']<=cfg['control_interval']:p.error('feedback timeout must exceed control interval')
    if type(cfg['feedback_settle_snapshots']) is not int or cfg['feedback_settle_snapshots']<0:p.error('invalid settling frames')
    if args.mode=='decide':
        if not args.snapshot:p.error('decide requires --snapshot')
        data=json.loads(Path(args.snapshot).read_text());validate_snapshot(data,cfg['normalization_profile'])
        payload,details=decide(data,cfg)
        print(json.dumps(dict(action=payload,decision=details),indent=2));return
    if not args.output:p.error('eval-http requires --output')
    Pipeline(args,cfg).run()
if __name__=='__main__':main()
