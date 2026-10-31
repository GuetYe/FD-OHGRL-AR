"""Chen et al. (2021) dual-agent structure, explicitly adapted to packet routing.

Not a reproduction of optical RMSA, computing allocation, MTL or transfer learning.
No dependency on the original project's eventlet / torch_geometric imports.
"""
import sys
from pathlib import Path
SHARED_ROOT = Path(__file__).resolve().parents[2] / 'upper' / 'core'
if not (SHARED_ROOT / 'path_performance.py').is_file():
    raise ImportError('Missing bundled shared path_performance.py: ' + str(SHARED_ROOT))
sys.path.insert(0, str(SHARED_ROOT))
from path_performance import (load_profile, validate_profile, validate_snapshot,
                              edge_values as physical_edge_values, score)
import copy
import itertools
import math
from dataclasses import dataclass

import networkx as nx
import numpy as np
import torch
from torch import nn
from torch.distributions import Categorical


def pair(src, dst):
    return str((src, dst))


def validate_config(c):
    if 'normalization_profile' not in c:
        c['normalization_profile'] = load_profile()
    validate_profile(c['normalization_profile'])
    if c['reward_weights'] != c['normalization_profile']['weights']:
        raise ValueError('Chen/OHGRL reward weights must match shared profile')
    if c['queue_mode'] != c['normalization_profile']['queue_mode']:
        raise ValueError('Chen/OHGRL queue direction must match shared profile')
    if len(c['destinations']) != len(set(c['destinations'])) or not c['destinations']:
        raise ValueError('destinations must be nonempty and unique')
    if c['source'] in c['destinations']:
        raise ValueError('source cannot be a destination')
    for name in ['k', 'hidden', 'layers', 'horizon']:
        if not isinstance(c[name], int) or c[name] < 1:
            raise ValueError(name + ' must be a positive integer')
    for key in ['entropy_coef', 'value_coef']:
        if not math.isfinite(c[key]) or c[key] < 0:
            raise ValueError(key + ' must be finite and nonnegative')
    if c['feedback_timeout'] <= c['control_interval']:
        raise ValueError('feedback_timeout must exceed control_interval')
    if not 0 <= c['gamma'] <= 1:
        raise ValueError('gamma outside [0,1]')
    if c['queue_mode'] not in ['legacy_forward', 'directional']:
        raise ValueError('unknown queue_mode')
    if c['path_weight'] not in ['distance', 'hops']:
        raise ValueError('candidate ranking must use static distance or hops')
    w = c['reward_weights']
    if len(w) != 4 or any(not math.isfinite(x) or x < 0 for x in w) or not math.isclose(sum(w), 1):
        raise ValueError('four nonnegative reward weights must sum to 1')
    for key in ['control_interval', 'poll_interval', 'feedback_timeout', 'http_timeout', 'learning_rate', 'grad_clip']:
        if not math.isfinite(c[key]) or c[key] <= 0:
            raise ValueError(key + ' must be finite and positive')


class Snapshot:
    """Existing JSON schema. Undirected topology; directional queue optional.

    Bandwidth, delay, loss in the existing API are edge-level aggregates, not
    independently observed directional metrics. Preserve this assumption.
    """
    def __init__(self, data, cfg):
        if 'timestamp' not in data or not data.get('edges'):
            raise ValueError('snapshot lacks timestamp or edges')
        validate_config(cfg)
        validate_snapshot(data, cfg['normalization_profile'])
        self.data, self.cfg = copy.deepcopy(data), cfg
        self.graph = nx.Graph()
        self.graph.add_nodes_from(n['id'] for n in data['nodes'])
        if not all(isinstance(n, int) for n in self.graph):
            raise ValueError('this adapter expects integer switch IDs')
        if cfg['source'] not in self.graph or not set(cfg['destinations']) <= set(self.graph):
            raise ValueError('source or destination absent')
        self.records = {}
        for e in data['edges']:
            u, v = e['source'], e['target']
            if u == v or u not in self.graph or v not in self.graph:
                raise ValueError('invalid edge endpoints')
            key = frozenset((u, v))
            if key in self.records:
                raise ValueError('duplicate/reverse edge: expected undirected edge list')
            required = ['free_bw', 'delay', 'loss', 'forward_queue_pkts']
            if cfg['queue_mode'] == 'directional':
                required += ['reverse_queue_pkts']
            if cfg['path_weight'] == 'distance':
                required += ['distance']
            for field in required:
                if field not in e or not math.isfinite(float(e[field])) or float(e[field]) < 0:
                    raise ValueError('missing, negative or nonfinite edge field: ' + field)
            if cfg['path_weight'] == 'distance' and e['distance'] <= 0:
                raise ValueError('static distance must be positive')
            self.records[key] = e
            self.graph.add_edge(u, v, **e)

    def edge_values(self, u, v):
        e = self.records[frozenset((u, v))]
        return np.asarray(physical_edge_values(e, u, self.cfg['normalization_profile']), dtype=np.float64)

    def performance(self, path):
        return score(self.data, path, self.cfg['normalization_profile'])

    def metrics(self, path):
        return np.asarray(self.performance(path)['metrics'], dtype=np.float64)

    def reward(self, path):
        return self.performance(path)['reward']


class Candidates:
    """K shortest simple paths generated once from static topology."""
    def __init__(self, state, cfg):
        self.cfg = cfg
        self.nodes = sorted(state.graph.nodes)
        self.signature = self.topology_signature(state)
        # Sorted insertion makes equal-length path enumeration reproducible.
        g = nx.Graph()
        g.add_nodes_from(self.nodes)
        for u, v in sorted(tuple(sorted(e)) for e in state.graph.edges):
            g.add_edge(u, v, distance=state.graph[u][v].get('distance', 1))
        weight = None if cfg['path_weight'] == 'hops' else 'distance'
        self.paths = {}
        for dst in cfg['destinations']:
            try:
                self.paths[dst] = list(itertools.islice(nx.shortest_simple_paths(g, cfg['source'], dst, weight), cfg['k']))
            except nx.NetworkXNoPath:
                self.paths[dst] = []
        if not any(self.paths.values()):
            raise ValueError('no reachable destination')
        self.lengths = {dst: [sum(g[u][v]['distance'] if weight else 1 for u, v in zip(p, p[1:]))
                             for p in paths] for dst, paths in self.paths.items()}

    def topology_signature(self, state):
        return (tuple(sorted(state.graph.nodes)), tuple(sorted(
            (min(u, v), max(u, v), state.graph[u][v]['distance'] if self.cfg['path_weight'] == 'distance' else 1)
            for u, v in state.graph.edges)))

    def check(self, state):
        if self.topology_signature(state) != self.signature:
            raise ValueError('topology/static weights changed: stop and rebuild experiment')

    def onehot(self, node):
        x = np.zeros(len(self.nodes), dtype=np.float32)
        x[self.nodes.index(node)] = 1
        return x

    def features(self, state, path):
        # Same path-level scales as the shared reward; hop count is separate.
        perf = state.performance(path)
        return np.asarray(list(perf['normalized']) + [perf['hops'] / max(1, len(state.graph)-1)], dtype=np.float32)

    def lower_state(self, state, dst):
        k = self.cfg['k']
        feats = np.zeros((k, 5), dtype=np.float32)
        mask = np.zeros(k, dtype=bool)
        for i, path in enumerate(self.paths[dst]):
            feats[i], mask[i] = self.features(state, path), True
        return np.concatenate([feats.ravel(), mask.astype(np.float32)]), np.concatenate([self.onehot(self.cfg['source']), self.onehot(dst)]), mask


def mlp(input_size, hidden, layers):
    out = []
    for _ in range(layers):
        out += [nn.Linear(input_size, hidden), nn.ELU()]
        input_size = hidden
    return nn.Sequential(*out)


class ActorCritic(nn.Module):
    def __init__(self, common_dim, context_dim, actions, cfg, master=False):
        super().__init__()
        self.master = master
        h = cfg['hidden']
        self.common = mlp(common_dim, h, cfg['layers'])
        if master:
            self.context = mlp(context_dim, h, cfg['layers'])
            self.coupler = nn.Conv2d(1, 1, kernel_size=(2, 1))
            size = h
        else:
            size = h + context_dim
        self.actor, self.critic = nn.Linear(size, actions), nn.Linear(size, 1)

    def forward(self, common, context, mask):
        z = self.common(common)
        if self.master:
            z = self.coupler(torch.stack([z, self.context(context)], dim=1).unsqueeze(1)).flatten(1)
        else:
            z = torch.cat([z, context], dim=-1)
        if not bool(mask.any(-1).all()):
            raise ValueError('all actions masked')
        logits = self.actor(z).masked_fill(~mask, -torch.inf)
        return Categorical(logits=logits), self.critic(z).squeeze(-1)


@dataclass
class Decision:
    lower_common: np.ndarray
    lower_context: np.ndarray
    lower_mask: np.ndarray
    lower_action: int
    master_common: np.ndarray
    master_context: np.ndarray
    master_mask: np.ndarray
    master_action: int
    destination: int
    path: list
    payload: dict
    proposal_order: list
    destination_mode: str = 'sample'
    master_probabilities: list = None


class DualAgent:
    def __init__(self, cfg, state):
        validate_config(cfg)
        self.cfg = copy.deepcopy(cfg)
        torch.manual_seed(cfg['seed'])
        np.random.seed(cfg['seed'])
        torch.set_num_threads(1)
        self.candidates = Candidates(state, cfg)
        k, d, n = cfg['k'], len(cfg['destinations']), len(self.candidates.nodes)
        self.lower = ActorCritic(k * 6, 2 * n, k, cfg)
        self.master = ActorCritic(d * 6, (d + 1) * n, d, cfg, master=True)
        self.optimizers = [torch.optim.Adam(m.parameters(), lr=cfg['learning_rate']) for m in [self.lower, self.master]]
        self.buffer, self.updates = [], 0

    @staticmethod
    def tensors(common, context, mask):
        return (torch.as_tensor(np.asarray(common), dtype=torch.float32),
                torch.as_tensor(np.asarray(context), dtype=torch.float32),
                torch.as_tensor(np.asarray(mask), dtype=torch.bool))

    def choose(self, net, common, context, mask, deterministic):
        with torch.no_grad():
            dist, _ = net(*self.tensors([common], [context], [mask]))
            return int(dist.probs.argmax(-1).item() if deterministic else dist.sample().item())

    def decide(self, state, deterministic=False, tag='T', destination_mode='sample'):
        if destination_mode not in ('sample', 'distribution'):
            raise ValueError('invalid destination mode')
        self.candidates.check(state)
        proposals, lower = [], {}
        for dst in self.cfg['destinations']:
            if not self.candidates.paths[dst]:
                continue
            common, context, mask = self.candidates.lower_state(state, dst)
            action = self.choose(self.lower, common, context, mask, deterministic)
            path = self.candidates.paths[dst][action]
            lower[dst] = (common, context, mask, action)
            proposals.append((self.candidates.lengths[dst][action], dst, path))
        proposals.sort(key=lambda item: (item[0], item[1]))
        d, n = len(self.cfg['destinations']), len(self.candidates.nodes)
        features, identity = np.zeros((d, 5), np.float32), np.zeros((d, n), np.float32)
        mask = np.zeros(d, bool)
        for i, (_, dst, path) in enumerate(proposals):
            features[i], identity[i], mask[i] = self.candidates.features(state, path), self.candidates.onehot(dst), True
        common = np.concatenate([features.ravel(), mask.astype(np.float32)])
        context = np.concatenate([self.candidates.onehot(self.cfg['source']), identity.ravel()])
        with torch.no_grad():
            dist, _ = self.master(*self.tensors([common], [context], [mask]))
            master_probs = dist.probs[0].cpu().tolist()
        action = (0 if destination_mode=='distribution' else
                  self.choose(self.master, common, context, mask, deterministic))
        _, dst, path = proposals[action]
        # Host's existing weighted_random consumes a nested list in configured dst order.
        probs = [float(x == dst) for x in self.cfg['destinations']]
        if destination_mode=='distribution':
            mapped = {p_dst:master_probs[i] for i,(_,p_dst,_) in enumerate(proposals)}
            probs = [mapped.get(x,0.0) for x in self.cfg['destinations']]
        payload = {'control_tag': tag, 'high_act_list': [probs],
                   'low_act_dict': {pair(self.cfg['source'], p_dst): p for _, p_dst, p in proposals}}
        return Decision(*lower[dst], common, context, mask,
                        None if destination_mode=='distribution' else action,
                        None if destination_mode=='distribution' else dst,
                        [] if destination_mode=='distribution' else list(path), payload,
                        [x[1] for x in proposals], destination_mode, master_probs)

    def observe(self, decision, reward):
        if decision.destination_mode=='distribution':
            raise ValueError('distribution decisions are evaluation only; no fabricated training action')
        if not math.isfinite(reward):
            raise ValueError('nonfinite reward')
        self.buffer.append((decision, reward))
        n = self.cfg['horizon']
        if len(self.buffer) < 2 * n - 1:
            return None
        if len(self.buffer) != 2 * n - 1:
            raise RuntimeError('invalid FLX window length')
        rewards = [x[1] for x in self.buffer]
        targets = torch.tensor([sum(self.cfg['gamma'] ** j * rewards[i + j] for j in range(n)) for i in range(n)], dtype=torch.float32)
        batch = [x[0] for x in self.buffer[:n]]
        result = {'update': self.updates + 1, 'collected': 2 * n - 1, 'loss_samples_per_agent': n,
                  'return_mean': targets.mean().item()}
        for name, model, opt in zip(['lower', 'master'], [self.lower, self.master], self.optimizers):
            common = [getattr(x, name + '_common') for x in batch]
            context = [getattr(x, name + '_context') for x in batch]
            mask = [getattr(x, name + '_mask') for x in batch]
            actions = torch.tensor([getattr(x, name + '_action') for x in batch])
            dist, values = model(*self.tensors(common, context, mask))
            advantage = targets - values
            policy_loss = -(dist.log_prob(actions) * advantage.detach()).mean()
            value_loss = advantage.square().mean()
            loss = policy_loss + self.cfg['value_coef'] * value_loss - self.cfg['entropy_coef'] * dist.entropy().mean()
            if not torch.isfinite(loss):
                raise ValueError('nonfinite loss; inspect telemetry and reward scale')
            opt.zero_grad()
            loss.backward()
            norm = nn.utils.clip_grad_norm_(model.parameters(), self.cfg['grad_clip'], error_if_nonfinite=True)
            opt.step()
            result[name + '_loss'], result[name + '_grad_norm'] = loss.item(), float(norm)
        # Fresh window after each update. Do not replay old-policy look-ahead samples.
        self.buffer.clear()
        self.updates += 1
        return result

    def save(self, path):
        from pathlib import Path
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + '.tmp')
        torch.save({'format': 2, 'method': 'Chen-Dual-Packet (no TL)', 'config': self.cfg,
                    'lower': self.lower.state_dict(), 'master': self.master.state_dict(),
                    'optimizers': [x.state_dict() for x in self.optimizers], 'updates': self.updates,
                    'candidates': self.candidates.paths, 'signature': self.candidates.signature,
                    'torch_rng': torch.get_rng_state()}, tmp)
        tmp.replace(path)

    @classmethod
    def load(cls, path, state):
        # Only load your own checkpoints (pickle-based torch format).
        ckpt = torch.load(path, map_location='cpu', weights_only=False)
        if ckpt.get('format') != 2:
            raise ValueError('requires new path-normalized checkpoint; retrain old models')
        if ckpt['config']['normalization_profile'] != state.cfg['normalization_profile']:
            raise ValueError('normalization profile mismatch')
        if ckpt['config'].get('telemetry_semantics_version')!=state.cfg.get('telemetry_semantics_version'):
            raise ValueError('checkpoint telemetry version mismatch; retrain')
        state = Snapshot(state.data, ckpt['config'])
        agent = cls(ckpt['config'], state)
        if ckpt['signature'] != agent.candidates.signature or ckpt['candidates'] != agent.candidates.paths:
            raise ValueError('checkpoint topology/candidate mismatch')
        agent.lower.load_state_dict(ckpt['lower'])
        agent.master.load_state_dict(ckpt['master'])
        for opt, saved in zip(agent.optimizers, ckpt['optimizers']):
            opt.load_state_dict(saved)
        agent.updates = ckpt['updates']
        torch.set_rng_state(ckpt['torch_rng'])
        return agent


def feedback_matches(state, decision, cfg):
    """Version/destination alignment, plus verified per-flow installation in v3.

    A feedback snapshot is attributable to ``decision`` when a target flow has
    explicitly consumed the same ``policy_version`` and the flow selected the
    same source-destination pair.  We intentionally do *not* require the
    telemetry path markers (or the whole high-level probability payload) to be
    an atomic echo of the publication, because Ryu flow installation and state
    sampling are trigger-driven and asynchronous in this experiment.

    Freshness and the additional post-consumption telemetry-frame requirement
    are enforced by ``HTTPBridge.feedback`` in run.py.
    """
    distribution = getattr(decision, 'destination_mode', 'sample')=='distribution'
    key = state.data.get('choice_key') if distribution else pair(cfg['source'], decision.destination)
    if distribution:
        allowed = {pair(cfg['source'],d) for d,p in zip(cfg['destinations'],decision.payload['high_act_list'][0]) if p>0}
        if key not in allowed or key not in decision.payload['low_act_dict']:
            return False
    expected_version = decision.payload.get('policy_version')
    observed_version = state.data.get('choice_policy_version')
    if expected_version is None or str(observed_version) != str(expected_version):
        return False
    if state.data.get('choice_key') != key:
        return False
    if cfg.get('feedback_alignment') == 'per_flow_installation':
        path = decision.payload['low_act_dict'][key] if distribution else decision.path
        return (state.data.get('flow_install_verified') is True
                and bool(state.data.get('choice_flow_id'))
                and state.data.get('applied_path') == path)
    return True

