"""Fixed global shortest paths; reject incompatible live topology."""
import heapq
PATHS={15:[3,6,9,12,15],16:[3,7,10,13,16],17:[3,7,10,13,17],18:[3,7,10,14,18]}
def shortest_paths(data,cfg):
    if cfg['source']!=3 or set(cfg['destinations'])!=set(PATHS):raise ValueError('Unexpected source/destination group')
    graph={n['id']:{} for n in data['nodes']}
    for e in data['edges']:
        u,v=e['source'],e['target'];w=float(e['distance'])
        if not w>0:raise ValueError('Invalid distance')
        graph[u][v]=w;graph[v][u]=w
    distances={3:0.};queue=[(0.,3)]
    while queue:
        cost,u=heapq.heappop(queue)
        if cost!=distances[u]:continue
        for v,w in graph[u].items():
            new=cost+w
            if new<distances.get(v,float('inf')):
                distances[v]=new;heapq.heappush(queue,(new,v))
    for d,p in PATHS.items():
        length=sum(graph[u][v] for u,v in zip(p,p[1:]))
        if abs(length-distances[d])>1e-9:raise ValueError('Specified route is not globally shortest')
    return {str((3,d)):list(PATHS[d]) for d in cfg['destinations']}
