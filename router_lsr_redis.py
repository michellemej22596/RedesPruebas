# router_lsr_redis.py
"""
Router de Link State Routing (LSR) usando Redis Pub/Sub.
- Descobre vizinhos com HELLO (periódico)
- Inunda LSPs com sua vizinhança (periódico)
- Monta LSDB, calcula tabela com Dijkstra, faz forwarding por next-hop

Uso:
  python router_lsr_redis.py topo.json A
ENV:
  REDIS_HOST, REDIS_PORT, REDIS_PWD, SECTION, GROUP, NAMES_FILE
"""
from __future__ import annotations
import sys
import time
from typing import Dict, Set, Any, List
import threading

from redis_transport import RedisTransport
from id_map import NODE_TO_CHANNEL, get_channel, channel_to_node
from packets import make_packet, validate_packet, normalize_packet, get_packet_id, dec_hops, is_deliver_to_me
from dijkstra_rt import load_topology, routing_table_for

HELLO_PERIOD = 5.0   # s
LSP_PERIOD   = 7.5   # s

class LinkStateRouterRedis:
    def __init__(self, node_id: str, graph: Dict[str, Dict[str, float]]):
        if node_id not in NODE_TO_CHANNEL:
            raise ValueError(f"Nodo '{node_id}' não está em NODE_TO_CHANNEL")

        self.node_id = node_id
        self.channel_local: str = NODE_TO_CHANNEL[node_id]
        self.neighbors: List[str] = list(graph.get(node_id, {}).keys())  # IDs dos vizinhos

        self.lsdb: Dict[str, Dict[str, Any]] = {}
        self.sequence_number = 0
        self.seen_lsp_ids: Set[str] = set()
        self.routing_table: List[Dict[str, Any]] = []

        self.transport = RedisTransport(self.channel_local, self._on_packet)

        self._stop = threading.Event()
        self._t_hello = None
        self._t_lsp = None

        print(f"[{self.node_id}] Iniciado. Canal={self.channel_local} Vizinhos={self.neighbors}")

    def start(self) -> None:
        self.transport.start()
        self._schedule_hello()
        self._schedule_lsp()
        print(f"[{self.node_id}] Escutando em Redis... (Ctrl+C para sair)")

    # ---------- timers ----------
    def _schedule_hello(self):
        if self._stop.is_set(): return
        self._t_hello = threading.Timer(HELLO_PERIOD, self._emit_hello)
        self._t_hello.daemon = True
        self._t_hello.start()

    def _schedule_lsp(self):
        if self._stop.is_set(): return
        self._t_lsp = threading.Timer(LSP_PERIOD, self._emit_lsp)
        self._t_lsp.daemon = True
        self._t_lsp.start()

    def _emit_hello(self):
        try:
            print(f"[{self.node_id}] 📡 Enviando HELLO a vecinos: {self.neighbors}")
            for neigh in self.neighbors:
                ch = get_channel(neigh)
                pkt = make_packet("hello", self.channel_local, ch, hops=1, alg="lsr", payload="HELLO")
                self.transport.publish(ch, pkt)
                print(f"[{self.node_id}] 📤 HELLO → {neigh} ({ch})")
        finally:
            self._schedule_hello()

    def _emit_lsp(self):
        try:
            neighbors_costs = {n: 1 for n in self.neighbors}
            lsp = make_packet("lsp", self.channel_local, "*", hops=8, alg="lsr", 
                              neighbors=[get_channel(n) for n in self.neighbors], payload="")
            lsp["originator"] = self.node_id
            lsp["neighbors"] = neighbors_costs
            self.sequence_number += 1
            self._flood_lsp(lsp)
        finally:
            self._schedule_lsp()

    # ---------- recepção ----------
    def _on_packet(self, packet: Dict[str, Any]) -> None:
        packet = normalize_packet(packet)
        if not validate_packet(packet):
            return

        print(f"[{self.node_id}] 📨 Recibido: {packet['type']} de {channel_to_node(packet.get('from', ''))}")

        if packet["type"] == "hello":
            self._handle_hello(packet)
        elif packet["type"] == "hello_ack":
            self._handle_hello_ack(packet)
        elif packet["type"] == "lsp":
            self._handle_lsp(packet)
        elif packet["type"] == "message":
            self._handle_data_packet(packet)

    def _handle_hello(self, packet: Dict[str, Any]) -> None:
        sender_ch = packet.get("from", "")
        sender_node = channel_to_node(sender_ch)
        
        print(f"[{self.node_id}] 👋 HELLO recibido de {sender_node}")
        
        if sender_node and sender_node not in self.neighbors:
            self.neighbors.append(sender_node)
            print(f"[{self.node_id}] ✨ Novo vizinho descoberto: {sender_node}")

        ack = make_packet("hello_ack", self.channel_local, sender_ch, hops=1, alg="lsr", payload="HELLO_ACK")
        self.transport.publish(sender_ch, ack)
        print(f"[{self.node_id}] 📤 HELLO_ACK enviado a {sender_node}")

    def _handle_hello_ack(self, packet: Dict[str, Any]) -> None:
        sender_ch = packet.get("from", "")
        sender_node = channel_to_node(sender_ch)
        print(f"[{self.node_id}] ✅ HELLO_ACK recibido de {sender_node}")
        
        if sender_node and sender_node not in self.neighbors:
            self.neighbors.append(sender_node)
            print(f"[{self.node_id}] ✨ Novo vizinho descoberto via ACK: {sender_node}")

    def _handle_lsp(self, packet: Dict[str, Any]) -> None:
        lsp_id = get_packet_id(packet)
        if not lsp_id or lsp_id in self.seen_lsp_ids:
            return
        self.seen_lsp_ids.add(lsp_id)

        originator = packet.get("originator", "")
        if not originator:
            return

        self.lsdb[originator] = {
            "neighbors": dict(packet.get("neighbors", {}))
        }
        print(f"[{self.node_id}] LSP recebido de {originator}")

        exclude = packet.get("from", "")
        self._flood_lsp(packet, exclude=exclude)

        self._calculate_routing_table()

    def _handle_data_packet(self, packet: Dict[str, Any]) -> None:
        if is_deliver_to_me(packet, self.channel_local):
            print(f"[{self.node_id}] ✅ Mensagem entregue: {packet.get('payload')}")
            return

        dst_ch = packet.get("to", "")
        dst_node = channel_to_node(dst_ch)
        if not dst_node:
            print(f"[{self.node_id}] Destino desconhecido: {dst_ch}")
            return

        next_hop = self._get_next_hop(dst_node)
        if next_hop:
            self._forward_packet(packet, next_hop)
        else:
            print(f"[{self.node_id}] Sem rota para {dst_node}")

    # ---------- flooding & forwarding ----------
    def _flood_lsp(self, packet: Dict[str, Any], exclude: str = None) -> None:
        for neigh in self.neighbors:
            ch = get_channel(neigh)
            if exclude and ch == exclude:
                continue
            self.transport.publish(ch, packet)
            print(f"[{self.node_id}] LSP → {neigh} ({ch})")

    def _forward_packet(self, packet: Dict[str, Any], next_hop_node: str) -> None:
        if dec_hops(packet) <= 0:
            return
        self.transport.publish(get_channel(next_hop_node), packet)
        print(f"[{self.node_id}] Dados → {next_hop_node}")

    # ---------- tabela de rotas ----------
    def _calculate_routing_table(self) -> None:
        graph: Dict[str, Dict[str, float]] = {}
        for node, rec in self.lsdb.items():
            graph[node] = {v: float(c) for v, c in rec.get("neighbors", {}).items()}

        if self.node_id not in graph:
            graph[self.node_id] = {n: 1.0 for n in self.neighbors}

        self.routing_table = routing_table_for(graph, self.node_id)
        print(f"[{self.node_id}] Tabela recalculada: {self.routing_table}")

    def _get_next_hop(self, destination_node: str) -> str:
        for entry in self.routing_table:
            if entry["destino"] == destination_node:
                return str(entry["next_hop"])
        return ""

    # ---------- API de envio ----------
    def send(self, dst_node: str, payload: str, hops: int = 8) -> None:
        pkt = make_packet("message", self.channel_local, get_channel(dst_node), hops=hops, alg="lsr", payload=payload)
        nh = self._get_next_hop(dst_node)
        if nh:
            self._forward_packet(pkt, nh)
        else:
            for neigh in self.neighbors:
                self.transport.publish(get_channel(neigh), pkt)
            print(f"[{self.node_id}] (fallback) mensagem inicial por flooding")

def main():
    if len(sys.argv) < 3:
        print("Uso: python router_lsr_redis.py <topo.json> <Nodo>")
        sys.exit(1)

    topo_path = sys.argv[1]
    node = sys.argv[2]

    try:
        graph = load_topology(topo_path)
    except Exception as e:
        print(f"Erro carregando topologia '{topo_path}': {e}")
        sys.exit(2)

    router = LinkStateRouterRedis(node, graph)

    try:
        router.start()
        if node == "A":
            time.sleep(2.0)
            router.send("D", "Olá de A (LSR+Redis)!", hops=6)
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nSaindo...")
    finally:
        router._stop.set()
        try:
            router.transport.stop()
        except Exception:
            pass

if __name__ == "__main__":
    main()
