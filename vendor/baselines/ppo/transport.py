"""Shared validated HTTP transport and logging; no learning/replay policy here."""
import hashlib, json, math, os, random, threading, time, uuid
from pathlib import Path
import numpy as np
import requests
import torch
from tensorboardX import SummaryWriter
from model_adapter import Models, setting
from path_performance import validate_snapshot
from protocol import topology
class Transport:
    def __init__(self,args,cfg):
        self.args=args; self.cfg=cfg
        self.output=Path(args.output); self.output.mkdir(parents=True,exist_ok=False)
        self.stop=threading.Event(); self.log_lock=threading.Lock()
        self.started=time.monotonic(); self.run_id=uuid.uuid4().hex; self.topo=None
        self.file=open(self.output/'events.jsonl','a',encoding='utf-8',buffering=1)
        self.writer=SummaryWriter(str(self.output/'tensorboard'),flush_secs=10)
        torch.manual_seed(cfg['seed']); np.random.seed(cfg['seed']); random.seed(cfg['seed'])
        torch.set_num_threads(cfg['torch_threads'])
        self.models=Models(cfg)
        if args.mode=='eval-http':
            ckpt=torch.load(args.checkpoint,map_location=setting.device,weights_only=True)
            if ckpt.get('format')!='PPO-DDPG-perflow-actors-v1':raise ValueError('wrong checkpoint format')
            for field in ['algorithm','source','destinations','normalization_profile','model_hyperparameters','telemetry_semantics_version']:
                if ckpt['config'].get(field)!=cfg[field]:raise ValueError('checkpoint incompatible: '+field)
            self.models.high.actor.load_state_dict(ckpt['high_actor'])
            self.models.low.actor.load_state_dict(ckpt['low_actor'])
        self.save_actors('initial.pt',self.models.actors(),0)
        (self.output/'manifest.json').write_text(json.dumps(dict(config=cfg,arguments=vars(args),run_id=self.run_id,
            policy_prefix='PPO-DDPG-'+self.run_id+'-',
            policy='fresh sequential PPO rollouts; frozen actors during each rollout; verified selected-flow feedback',
            checkpoint='actors only; evaluation load is not training resume',
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
        payload={'format':'PPO-DDPG-perflow-actors-v1','config':self.cfg,'updates':update,
                 'high_actor':actors[0].state_dict(),'low_actor':actors[1].state_dict()}
        target=self.output/name;tmp=target.with_suffix('.tmp');torch.save(payload,tmp);os.replace(tmp,target)
