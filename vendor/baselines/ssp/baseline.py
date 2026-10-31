"""Xuan et al. (2000), Section 4.2.1: shortest-shortest path (SSP).

D(P) is the sum of topology distance weights. Centralized SDN computation is
used in place of distributed table establishment; the route criterion is unchanged.
"""
import math
import networkx as nx
from protocol import topology, validate_payload

def decide(data,cfg):
    nodes,edges=topology(data)
    source=cfg['source'];destinations=cfg['destinations']
    if source not in nodes or not destinations or source in destinations or len(set(destinations))!=len(destinations):
        raise ValueError('invalid source/destination group')
    graph=nx.Graph();graph.add_nodes_from(nodes)
    # Canonical insertion plus destination-ID tie breaking makes ties reproducible.
    for edge in sorted(data['edges'],key=lambda e:tuple(sorted((e['source'],e['target'])))):
        distance=edge.get('distance')
        if isinstance(distance,bool) or not isinstance(distance,(int,float)) or not math.isfinite(distance) or distance<=0:
            raise ValueError('SSP requires positive finite topology distance')
        u,v=sorted((edge['source'],edge['target']))
        graph.add_edge(u,v,weight=float(distance))
    lengths,paths=nx.single_source_dijkstra(graph,source,weight='weight')
    if any(d not in paths for d in destinations):
        # The shared lower protocol requires a valid route for every group member.
        # Do not silently shrink the common candidate set.
        raise ValueError('unreachable candidate destination')
    chosen=min(destinations,key=lambda d:(lengths[d],d))
    payload={'control_tag':'C','high_act_list':[[float(d==chosen) for d in destinations]],
             'low_act_dict':{str((source,d)):paths[d] for d in destinations}}
    validate_payload(payload,data,source,destinations)
    # SSP next hops decrease the nearest-recipient distance by the link cost.
    # This verifies the group-level SSP criterion, not merely a unicast path.
    nearest=nx.multi_source_dijkstra_path_length(graph,destinations,weight='weight')
    selected=paths[chosen]
    for u,v in zip(selected,selected[1:]):
        if not math.isclose(nearest[u],graph[u][v]['weight']+nearest[v],rel_tol=1e-9,abs_tol=1e-9):
            raise ValueError('selected next hop violates SSP order')
    return payload,dict(choice_key=str((source,chosen)),destination=chosen,path=selected,
                        routing_metric='distance',path_distance=lengths[chosen],
                        candidate_distances={str(d):lengths[d] for d in destinations},
                        group_distance_along_path=[nearest[n] for n in selected])
