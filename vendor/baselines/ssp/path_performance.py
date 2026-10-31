"""Paper equations (6)--(11); no Torch/Mininet dependency.

Input API: Mbps, ms, percent loss, packets. Physical aggregates first;
normalization second. Equation (7) uses a bottleneck range normalization for
remaining bandwidth, keeps the product metric unchanged, and uses a FIXED topology
edge count for additive metrics (independent of the chosen path).
"""
import ast
import json
import math
from pathlib import Path

ATTRS = ('free_bw', 'delay', 'loss', 'forward_queue_pkts')


def load_profile(path=None):
    p = json.loads(Path(path or Path(__file__).with_name('network_profile.json')).read_text(encoding='utf-8'))
    validate_profile(p)
    return p


def validate_profile(p):
    if p.get('version') != 'paper-path-v3-topology-edges':
        raise ValueError('incompatible normalization profile; retraining required')
    if type(p.get('topology_edge_count')) is not int or p['topology_edge_count'] < 1:
        raise ValueError('positive fixed topology_edge_count required')
    for k in ('switch_bw_mbps', 'access_bw_mbps', 'queue_limit_packets', 'reference_packet_bytes', 'epsilon'):
        if not math.isfinite(p[k]) or p[k] <= 0:
            raise ValueError('positive finite profile field required: ' + k)
    if not math.isfinite(p['initial_delay_ms']) or p['initial_delay_ms'] < 0:
        raise ValueError('invalid initial delay')
    if not 0 <= p['initial_loss_percent'] <= 100:
        raise ValueError('invalid initial loss')
    if p['input_loss_unit'] not in ('percent', 'fraction') or p['input_delay_unit'] not in ('ms', 's'):
        raise ValueError('explicit telemetry units required')
    if p['queue_mode'] not in ('directional', 'legacy_forward'):
        raise ValueError('invalid queue mode')
    w = p['weights']
    if len(w) != 4 or any(not math.isfinite(x) or x < 0 for x in w) or not math.isclose(sum(w), 1):
        raise ValueError('four finite nonnegative weights must sum to one')


def link_bounds(p):
    # ms: bytes * 8 / Mbps / 1000. Reference scale, not a hard LLDP delay bound.
    d = p['initial_delay_ms'] + 8.0 * p['reference_packet_bytes'] * p['queue_limit_packets'] / (1000 * p['switch_bw_mbps'])
    return (p['switch_bw_mbps'], d, 1.0, float(p['queue_limit_packets']))


def link_lower_bounds(p):
    """Return the lower physical bounds used by Eq. (7) and Eq. (11)."""
    return (0.0, 0.0, 0.0, 0.0)


def _number(x, name):
    value = float(x)
    if not math.isfinite(value) or value < 0:
        raise ValueError('negative/nonfinite telemetry: ' + name)
    return value


def edge_values(edge, source, p):
    if source not in (edge['source'], edge['target']):
        raise ValueError('source is not an edge endpoint')
    q = 'forward_queue_pkts'
    if p['queue_mode'] == 'directional' and source != edge['source']:
        q = 'reverse_queue_pkts'
    b, d, loss, queue = [_number(edge[k], k) for k in ('free_bw', 'delay', 'loss', q)]
    if p['input_loss_unit'] == 'percent':
        loss /= 100.0
    if not 0 <= loss <= 1:
        raise ValueError('loss outside declared physical range')
    if p['input_delay_unit'] == 's':
        d *= 1000.0
    return (b, d, loss, queue)


def validate_snapshot(data, p):
    if not data.get('edges'):
        raise ValueError('empty edges')
    seen = set()
    for e in data['edges']:
        key = frozenset((e['source'], e['target']))
        if len(key) != 2 or key in seen:
            raise ValueError('self-loop or duplicate undirected edge')
        seen.add(key)
        edge_values(e, e['source'], p)
        edge_values(e, e['target'], p)


class InvalidPath(ValueError):
    pass


def path_from_markers(data, pair_key):
    """Recover ordered simple path; ambiguous/unfinished marked walks are invalid."""
    src, dst = ast.literal_eval(pair_key)
    adj, count = {}, 0
    for e in data['edges']:
        flag = e.get(pair_key, 0)
        if flag not in (0, 1):
            raise InvalidPath('repeated edge marker')
        if flag:
            u, v = e['source'], e['target']
            adj.setdefault(u, []).append(v)
            adj.setdefault(v, []).append(u)
            count += 1
    if not count or len(adj.get(src, [])) != 1 or len(adj.get(dst, [])) != 1:
        raise InvalidPath('missing/unfinished path')
    if any(len(ns) != (1 if u in (src, dst) else 2) for u, ns in adj.items()):
        raise InvalidPath('branched path')
    path = [src]
    while path[-1] != dst:
        choices = [v for v in adj[path[-1]] if len(path) == 1 or v != path[-2]]
        if len(choices) != 1 or choices[0] in path:
            raise InvalidPath('cycle or disconnected path')
        path.append(choices[0])
    if len(path) - 1 != count:
        raise InvalidPath('extra disconnected marked edges')
    return path


def aggregate(values):
    if not values:
        raise InvalidPath('empty path')
    success = 1.0
    for v in values:
        success *= 1.0 - v[2]
    return (min(v[0] for v in values), math.fsum(v[1] for v in values),
            1.0 - success, math.fsum(v[3] for v in values))


def normalize(metrics, hops, p):
    if hops < 1:
        raise InvalidPath('positive hop count required')
    lower = link_lower_bounds(p)
    upper = link_bounds(p)
    edges = p['topology_edge_count']
    eps = p['epsilon']
    # Additive denominators are fixed for this topology, NOT actual path hops.
    raw = (
        (metrics[0] - lower[0]) / (upper[0] - lower[0] + eps),
        metrics[1] / (edges * upper[1] + eps),
        metrics[2],
        metrics[3] / (edges * upper[3] + eps),
    )
    return (min(1.0, max(0.0, raw[0])), raw[1], raw[2], raw[3]), tuple(x > 1 for x in raw)


def terms(normalized):
    b, d, l, q = normalized
    return (b - 1.0, -d, -l, -q)


def score(data, path, p):
    if len(path) < 2 or len(path) != len(set(path)):
        raise InvalidPath('expected nonempty simple path')
    edges = {frozenset((e['source'], e['target'])): e for e in data['edges']}
    values = []
    for u, v in zip(path, path[1:]):
        try:
            edge = edges[frozenset((u, v))]
        except KeyError:
            raise InvalidPath('path contains nonexistent edge')
        values.append(edge_values(edge, u, p))
    metrics = aggregate(values)
    n, saturated = normalize(metrics, len(values), p)
    t = terms(n)
    return {'path': list(path), 'hops': len(values), 'metrics': metrics,
            'normalized': n, 'terms': t, 'saturated': saturated,
            'reference_exceeded': saturated, 'normalization_edge_count': p['topology_edge_count'],
            'reward': math.fsum(w*x for w, x in zip(p['weights'], t))}


def marked_terms(data, pair_key, p):
    try:
        return score(data, path_from_markers(data, pair_key), p)['terms']
    except InvalidPath:
        # Additive costs are unbounded above the reference scale. A constant -1
        # penalty could otherwise make an invalid path look better than a slow
        # valid path. Bound every simple path by the sum over ALL topology edges.
        upper = link_bounds(p)
        delay = math.fsum(edge_values(e,e['source'],p)[1] for e in data['edges'])
        queue = math.fsum(max(edge_values(e,e['source'],p)[3], edge_values(e,e['target'],p)[3]) for e in data['edges'])
        count = p['topology_edge_count']
        return (-1.0, -(1+delay/(count*upper[1]+p['epsilon'])),
                -1.0, -(1+queue/(count*upper[3]+p['epsilon'])))


def link_reward(data, u, v, p):
    for e in data['edges']:
        if {e['source'], e['target']} == {u, v}:
            x = edge_values(e, u, p)
            lower = link_lower_bounds(p)
            upper = link_bounds(p)
            # Eq. (11) applies the same per-link type-specific normalization.
            n = (
                (x[0] - lower[0]) / (upper[0] - lower[0] + p['epsilon']),
                x[1] / (upper[1] + p['epsilon']),
                x[2],
                x[3] / (upper[3] + p['epsilon']),
            )
            n = tuple(min(1.0, max(0.0, value)) for value in n)
            return math.fsum(w*t for w, t in zip(p['weights'], terms(n)))
    raise InvalidPath('next hop is not adjacent')
