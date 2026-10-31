"""Validated transport/logging, aligned with the three learning baselines."""
import hashlib,json,math,threading,time,uuid
from pathlib import Path
import requests
from tensorboardX import SummaryWriter
from path_performance import validate_snapshot
from protocol import topology
class Transport:
    def __init__(self,args,cfg):
        self.args=args;self.cfg=cfg
        self.output=Path(args.output);self.output.mkdir(parents=True,exist_ok=False)
        self.stop=threading.Event();self.log_lock=threading.Lock()
        self.started=time.monotonic();self.run_id=uuid.uuid4().hex;self.topo=None
        self.file=open(self.output/'events.jsonl','a',encoding='utf-8',buffering=1)
        self.writer=SummaryWriter(str(self.output/'tensorboard'),flush_secs=10)
        manifest=dict(config=cfg,arguments=vars(args),run_id=self.run_id,policy_prefix='SSP-'+self.run_id+'-',
            method='Xuan et al. 2000 SSP, Section 4.2.1; centralized SDN per-flow implementation',
            training='none; deterministic classical baseline',
            profile_sha256=hashlib.sha256(Path(__file__).with_name('network_profile.json').read_bytes()).hexdigest())
        (self.output/'manifest.json').write_text(json.dumps(manifest,indent=2))
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
