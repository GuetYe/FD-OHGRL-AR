"""Pure-Python publication/feedback contract shared with the per-flow environment."""
import copy
import math
import time
from collections import Counter

def topology(data):
    nodes = tuple(sorted(n['id'] for n in data['nodes']))
    edges = tuple(sorted(tuple(sorted((e['source'], e['target']))) for e in data['edges']))
    if len(nodes) != len(set(nodes)) or len(edges) != len(set(edges)):
        raise ValueError('duplicate nodes/edges')
    if any(u == v or u not in nodes or v not in nodes for u,v in edges):
        raise ValueError('invalid edge endpoints')
    return nodes, edges

def canonical(data, pairs):
    result=copy.deepcopy(data)
    result['nodes']=sorted(result['nodes'],key=lambda n:n['id'])
    result['edges']=sorted(result['edges'],key=lambda e:tuple(sorted((e['source'],e['target']))))
    # Old candidate marker fields are not observations of executed paths.
    for edge in result['edges']:
        for key in pairs:edge[key]=0
    return result

def validate_payload(payload, data, source, destinations):
    keys=[str((source,d)) for d in destinations]
    probs=payload['high_act_list'][0]
    if len(probs)!=len(keys) or any(not math.isfinite(p) or p<0 for p in probs) or not math.isclose(sum(probs),1,abs_tol=1e-6):
        raise ValueError('invalid destination probabilities')
    edges=set(topology(data)[1])
    for key,dst in zip(keys,destinations):
        path=payload['low_act_dict'][key]
        if len(path)<2 or path[0]!=source or path[-1]!=dst or len(path)!=len(set(path)):
            raise ValueError('invalid simple path: '+key)
        if any(tuple(sorted((u,v))) not in edges for u,v in zip(path,path[1:])):
            raise ValueError('non-topology hop: '+key)

class FeedbackGate:
    def __init__(self, payload, before_stamp, post_stamp, topology_signature, settle=1):
        self.payload=copy.deepcopy(payload)
        self.excluded={str(before_stamp),str(post_stamp)}
        self.topology_signature=topology_signature
        self.settle=settle
        self.matching=set()
        self.accepted=False
        self.counts=Counter()
    def inspect(self,data):
        reason='matched'
        stamp=str(data.get('timestamp',''))
        key=data.get('choice_key')
        if self.accepted:reason='already_accepted'
        elif not stamp or stamp in self.excluded:reason='stale_or_pre_post_timestamp'
        elif topology(data)!=self.topology_signature:raise ValueError('topology changed within run')
        elif data.get('choice_policy_version')!=self.payload['policy_version']:reason='policy_version_mismatch'
        elif key not in self.payload['low_act_dict']:reason='destination_mismatch'
        elif data.get('flow_install_verified') is not True or not data.get('choice_flow_id'):reason='installation_unverified'
        elif data.get('applied_path')!=self.payload['low_act_dict'][key]:reason='path_mismatch'
        elif data.get('controller_error'):reason='controller_error'
        if reason=='matched':
            if stamp in self.matching:reason='duplicate_timestamp'
            else:
                self.matching.add(stamp)
                if len(self.matching)>self.settle:
                    self.accepted=True
                    return True,'accepted'
                reason='settling'
        elif reason not in ('stale_or_pre_post_timestamp','already_accepted'):
            self.matching.clear()
        self.counts[reason]+=1
        return False,reason
