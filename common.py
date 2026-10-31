"""Standard-library utilities shared by preparation, launcher and audits."""
import hashlib,json,os,re
from pathlib import Path
HERE=Path(__file__).resolve().parent
MASK='reachable-simple-path-v7'

def normalize_loopback(text):
    """Keep one canonical assignment at its first position, so aliases remain defined."""
    seen=set();lines=[]
    for line in text.splitlines(True):
        match=re.match(r'^(data_server_ip|server_ip)\s*=',line)
        if match:
            key=match.group(1)
            if key in seen:continue
            seen.add(key);line=key+" = '127.0.0.1'\n"
        lines.append(line)
    return ''.join(lines)

def read(p):return json.loads(Path(p).read_text(encoding='utf-8'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def write(p,value):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp')
    with tmp.open('w',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2,allow_nan=False);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(str(tmp),str(p))
def profile(name):return read(HERE/'topologies.json')[name]
def graph_signature(p):return dict(nodes=sorted(p['nodes']),edges=sorted([sorted(e) for e in p['edges']]))
def make_config(name,bank=None):
    p=profile(name);cfg=read(HERE/'base_config.json')
    cfg.update(source=p['source'],destinations=p['destinations'],mask_version=MASK,
               topology_spec=graph_signature(p),node_feature_names=['cur_site','dst_site','visited'],
               evaluation_destination_mode='distribution')
    cfg['normalization_profile']['weights']=[.25]*4
    cfg['normalization_profile']['topology_edge_count']=len(p['edges'])
    cfg['extra_evaluation'].update(enabled=bank is not None,bank=str(bank) if bank else '',
        bank_sha256=sha(bank) if bank else '',fixed_states=128,fixed_feedback_interval=300)
    return cfg
def adj(p):
    result={n:set() for n in p['nodes']}
    for u,v in p['edges']:result[u].add(v);result[v].add(u)
    return result
def ports(p):
    # Use the original graph's deterministic node/edge traversal order, not arbitrary sets.
    next_port={n:1 for n in p['nodes']};result=[]
    for u,v in p['edges']:
        result.append(dict(u=u,v=v,port1=next_port[u],port2=next_port[v]))
        next_port[u]+=1;next_port[v]+=1
    return result

def snapshot(p):
    """Idle synthetic input for model/API shape probes only, never a measured fixed bank."""
    return dict(nodes=[dict(id=n,cur_site=0,dst_site=0,visited=0) for n in p['nodes']],
        edges=[dict(source=u,target=v,free_bw=6.,used_bw=0.,delay=.1,loss=0.,distance=1.,
                    forward_queue_pkts=0.,reverse_queue_pkts=0.) for u,v in p['edges']],
        telemetry_semantics_version='perflow-aligned-delta-v2',timestamp='synthetic-v7-probe')
