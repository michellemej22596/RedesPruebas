# dijkstra_rt.py
from __future__ import annotations
import json
from typing import Dict, Tuple, List
import math
import heapq

Graph = Dict[str, Dict[str, float]]

def load_topology(path: str) -> Graph:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cfg = data.get("config", data)

    graph: Graph = {}
    for u, neigh in cfg.items():
        if isinstance(neigh, dict):
            graph[u] = {v: float(w) for v, w in neigh.items()}
        elif isinstance(neigh, list):
            graph[u] = {v: 1.0 for v in neigh}
        else:
            graph[u] = {}
    return graph

def routing_table_for(graph: Graph, source: str) -> List[Dict[str, object]]:
    dist = {n: math.inf for n in graph}
    prev = {n: None for n in graph}
    dist[source] = 0.0
    pq: List[Tuple[float, str]] = [(0.0, source)]

    while pq:
        d, u = heapq.heappop(pq)
        if d != dist[u]:
            continue
        for v, w in graph.get(u, {}).items():
            nd = d + float(w)
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(pq, (nd, v))

    table = []
    for dest in graph:
        if dest == source or dist[dest] == math.inf:
            continue
        hop = dest
        while prev.get(hop) and prev[hop] != source:
            hop = prev[hop]
        next_hop = hop if prev.get(hop) == source else hop
        table.append({"destino": dest, "next_hop": next_hop, "costo": dist[dest]})
    return table
