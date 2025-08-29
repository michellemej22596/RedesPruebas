# router_flooding_redis.py
"""
Router de Flooding usando Redis Pub/Sub como red.
- Escucha su canal propio y reenvía paquetes a todos sus vecinos (según topo.json).
- Evita duplicados con headers[0].id
- Decrementa 'hops' y descarta al llegar a 0
- Entrega payload si el destino coincide con su canal o si el paquete es broadcast

Requisitos:
    pip install redis
Variables de entorno (recomendado):
    export REDIS_HOST="..."
    export REDIS_PORT="6379"
    export REDIS_PWD="..."
Ejecución (ejemplo):
    python router_flooding_redis.py topo.json A
"""

from __future__ import annotations
import sys
import json
import time
from typing import Dict, Set, Any, List

# Utilidades locales
from redis_transport import RedisTransport
from id_map import NODE_TO_CHANNEL, get_channel
from packets import make_packet, validate_packet, normalize_packet, get_packet_id, dec_hops, is_deliver_to_me

# Reutilizamos el loader de topología 
from dijkstra_rt import load_topology


class FloodingRouterRedis:
    def __init__(self, node_id: str, graph: Dict[str, Dict[str, float]]):
        if node_id not in NODE_TO_CHANNEL:
            raise ValueError(f"Nodo '{node_id}' no está en NODE_TO_CHANNEL")

        self.node_id = node_id
        self.channel_local: str = NODE_TO_CHANNEL[node_id]

        # vecinos lógicos (claves del grafo para node_id)
        self.neighbors: List[str] = list(graph.get(node_id, {}).keys())

        # control de duplicados
        self.seen: Set[str] = set()

        # transporte redis (callback en _on_packet)
        self.transport = RedisTransport(self.channel_local, self._on_packet)

        print(f"[{self.node_id}] Iniciado. Canal={self.channel_local} Vecinos={self.neighbors}")

    def start(self) -> None:
        self.transport.start()
        print(f"[{self.node_id}] Escuchando en Redis... (Ctrl+C para salir)")

    # ======== Recepción ========

    def _on_packet(self, packet: Dict[str, Any]) -> None:
        # Normalizamos y validamos
        packet = normalize_packet(packet)
        if not validate_packet(packet):
            return  # ignorar malformados

        # Evitar loops/duplicados
        pkt_id = get_packet_id(packet)
        if not pkt_id or pkt_id in self.seen:
            return
        self.seen.add(pkt_id)

        # ¿Es para mí (o broadcast)?
        if is_deliver_to_me(packet, self.channel_local):
            pld = packet.get("payload", "")
            print(f"[{self.node_id}] ✅ Mensaje recibido: {pld}")
            return

        # Forwarding: decrementar hops
        if dec_hops(packet) <= 0:
            # TTL/Hops agotado
            return

        # Reenviar a todos los vecinos
        self._flood_forward(packet)

    def _flood_forward(self, packet: Dict[str, Any]) -> None:
        for neigh in self.neighbors:
            try:
                ch = get_channel(neigh)
                self.transport.publish(ch, packet)
                print(f"[{self.node_id}] ↪️ reenviando {get_packet_id(packet)} a {neigh} ({ch})")
            except Exception as e:
                print(f"[{self.node_id}] ⚠️ Error reenviando a {neigh}: {e}")

    # ======== Envío inicial ========

    def send(self, dst_node: str, payload: str, hops: int = 8) -> None:
        """Envía un paquete inicial hacia dst_node (flooding a todos los vecinos)."""
        try:
            dst_channel = get_channel(dst_node)
        except Exception as e:
            print(f"[{self.node_id}] Error: {e}")
            return

        pkt = make_packet("message", self.channel_local, dst_channel, hops=hops, alg="flooding", payload=payload)
        # Marca este paquete como visto para que no se vuelva a reenviar
        pkt_id = get_packet_id(pkt)
        if pkt_id:
            self.seen.add(pkt_id)

        # Inunda a todos los vecinos
        for neigh in self.neighbors:
            try:
                ch = get_channel(neigh)
                self.transport.publish(ch, pkt)
                print(f"[{self.node_id}] 🚀 enviando inicial a {neigh} ({ch})")
            except Exception as e:
                print(f"[{self.node_id}] ⚠️ No pude publicar a {neigh}: {e}")


def main():
    if len(sys.argv) < 3:
        print("Uso: python router_flooding_redis.py <topo.json> <Nodo>")
        sys.exit(1)

    topo_path = sys.argv[1]
    node = sys.argv[2]

    try:
        graph = load_topology(topo_path)
    except Exception as e:
        print(f"Error cargando topología '{topo_path}': {e}")
        sys.exit(2)

    router = FloodingRouterRedis(node, graph)

    try:
        router.start()

        # prueba rápida automática desde A:
        if node == "A":
            time.sleep(1.5)
            router.send("D", "Hola desde A con Flooding+Redis!", hops=6)

        # Mantener vivo el proceso; se puede leer el input si se desea
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\nSaliendo...")
    finally:
        try:
            router.transport.stop()
        except Exception:
            pass


if __name__ == "__main__":
    main()
