#!/usr/bin/env python3
"""
Router LSR Interactivo - Link State Routing con comandos manuales
Uso: python interactive_lsr_router.py topo.json A

Comandos disponibles:
- send <destino> <mensaje>     : Enviar mensaje a un nodo específico
- broadcast <mensaje>          : Enviar mensaje a todos los nodos
- hello <destino>             : Enviar HELLO manual a un nodo
- show lsdb                   : Mostrar Link State Database
- show routes                 : Mostrar tabla de enrutamiento
- show neighbors              : Mostrar vecinos descubiertos
- status                      : Mostrar estado general del router
- help                        : Mostrar ayuda
- quit/exit                   : Salir del programa
"""

import sys
import time
import threading
from typing import Dict, Set, Any, List

from redis_transport import RedisTransport
from id_map import NODE_TO_CHANNEL, get_channel, channel_to_node
from packets import make_packet, validate_packet, normalize_packet, get_packet_id, dec_hops, is_deliver_to_me
from dijkstra_rt import load_topology, routing_table_for

HELLO_PERIOD = 5.0   # s
LSP_PERIOD   = 7.5   # s

class InteractiveLSRRouter:
    def __init__(self, node_id: str, graph: Dict[str, Dict[str, float]]):
        if node_id not in NODE_TO_CHANNEL:
            raise ValueError(f"Nodo '{node_id}' não está em NODE_TO_CHANNEL")

        self.node_id = node_id
        self.channel_local: str = NODE_TO_CHANNEL[node_id]
        self.neighbors: List[str] = list(graph.get(node_id, {}).keys())
        self.discovered_neighbors: Set[str] = set()

        # LSR state
        self.lsdb: Dict[str, Dict[str, Any]] = {}
        self.sequence_number = 0
        self.seen_lsp_ids: Set[str] = set()
        self.routing_table: List[Dict[str, Any]] = []

        self.transport = RedisTransport(self.channel_local, self._on_packet)
        self._stop = threading.Event()
        self._t_hello = None
        self._t_lsp = None

        print(f"🔗 [{self.node_id}] LSR Router iniciado")
        print(f"📡 Canal: {self.channel_local}")
        print(f"👥 Vecinos configurados: {self.neighbors}")
        print("=" * 50)

    def start(self) -> None:
        """Inicia el router y los timers automáticos"""
        self.transport.start()
        self._schedule_hello()
        self._schedule_lsp()
        print(f"✅ [{self.node_id}] Router LSR activo - escuchando mensajes...")

    def stop(self) -> None:
        """Detiene el router y limpia recursos"""
        self._stop.set()
        if self._t_hello:
            self._t_hello.cancel()
        if self._t_lsp:
            self._t_lsp.cancel()
        try:
            self.transport.stop()
        except Exception:
            pass

    # ========== TIMERS AUTOMÁTICOS ==========
    def _schedule_hello(self):
        if self._stop.is_set(): 
            return
        self._t_hello = threading.Timer(HELLO_PERIOD, self._emit_hello)
        self._t_hello.daemon = True
        self._t_hello.start()

    def _schedule_lsp(self):
        if self._stop.is_set(): 
            return
        self._t_lsp = threading.Timer(LSP_PERIOD, self._emit_lsp)
        self._t_lsp.daemon = True
        self._t_lsp.start()

    def _emit_hello(self):
        """Envía HELLOs periódicos a todos los vecinos"""
        try:
            for neigh in self.neighbors:
                ch = get_channel(neigh)
                pkt = make_packet("hello", self.channel_local, ch, hops=1, alg="lsr", payload="HELLO")
                self.transport.publish(ch, pkt)
                print(f"📡 [{self.node_id}] HELLO automático → {neigh}")
        finally:
            self._schedule_hello()

    def _emit_lsp(self):
        """Envía LSPs periódicos con información de vecinos"""
        try:
            neighbors_costs = {get_channel(n): 1 for n in self.neighbors}
            lsp = make_packet("info", self.channel_local, "*", hops=8, alg="lsr", payload="")
            lsp["originator"] = self.node_id
            lsp["neighbors"] = neighbors_costs
            lsp["headers"] = [{"id": f"LSP-{self.node_id}-{self.sequence_number}"}]
            self.sequence_number += 1
            self._flood_lsp(lsp)
            print(f"📢 [{self.node_id}] LSP automático enviado (seq: {self.sequence_number-1})")
        finally:
            self._schedule_lsp()

    # ========== MANEJO DE PAQUETES ==========
    def _on_packet(self, packet: Dict[str, Any]) -> None:
        """Procesa paquetes recibidos"""
        packet = normalize_packet(packet)
        if not validate_packet(packet):
            return

        pkt_type = packet["type"]
        sender = channel_to_node(packet.get("from", ""))

        if pkt_type == "hello":
            self._handle_hello(packet)
        elif pkt_type == "hello_ack":
            print(f"✅ [{self.node_id}] HELLO_ACK recibido de {sender}")
        elif pkt_type == "info":
            self._handle_lsp(packet)
        elif pkt_type == "message":
            self._handle_data_packet(packet)

    def _handle_hello(self, packet: Dict[str, Any]) -> None:
        """Maneja paquetes HELLO"""
        sender_ch = packet.get("from", "")
        sender_node = channel_to_node(sender_ch)
        
        if sender_node:
            self.discovered_neighbors.add(sender_node)
            if sender_node not in self.neighbors:
                self.neighbors.append(sender_node)
                print(f"🔍 [{self.node_id}] Nuevo vecino descubierto: {sender_node}")

        # Responder con HELLO_ACK
        ack = make_packet("hello_ack", self.channel_local, sender_ch, hops=1, alg="lsr", payload="HELLO_ACK")
        self.transport.publish(sender_ch, ack)

    def _handle_lsp(self, packet: Dict[str, Any]) -> None:
        """Maneja Link State Packets"""
        lsp_id = get_packet_id(packet)
        if not lsp_id or lsp_id in self.seen_lsp_ids:
            return
        
        self.seen_lsp_ids.add(lsp_id)
        originator = packet.get("originator", "")
        if not originator:
            return

        # Actualizar LSDB
        self.lsdb[originator] = {
            "neighbors": dict(packet.get("neighbors", {}))
        }
        print(f"📊 [{self.node_id}] LSP recibido de {originator} - LSDB actualizada")

        # Reenviar LSP (flooding)
        exclude = packet.get("from", "")
        self._flood_lsp(packet, exclude=exclude)

        # Recalcular tabla de enrutamiento
        self._calculate_routing_table()

    def _handle_data_packet(self, packet: Dict[str, Any]) -> None:
        """Maneja paquetes de datos"""
        if is_deliver_to_me(packet, self.channel_local):
            sender = channel_to_node(packet.get("from", ""))
            payload = packet.get("payload", "")
            print(f"📨 [{self.node_id}] ✅ MENSAJE RECIBIDO de {sender}: '{payload}'")
            return

        # Reenviar usando tabla de enrutamiento
        dst_ch = packet.get("to", "")
        dst_node = channel_to_node(dst_ch)
        if not dst_node:
            print(f"❌ [{self.node_id}] Destino desconocido: {dst_ch}")
            return

        next_hop = self._get_next_hop(dst_node)
        if next_hop:
            self._forward_packet(packet, next_hop)
            print(f"🔄 [{self.node_id}] Reenviando a {dst_node} vía {next_hop}")
        else:
            print(f"❌ [{self.node_id}] Sin ruta para {dst_node}")

    # ========== FLOODING Y FORWARDING ==========
    def _flood_lsp(self, packet: Dict[str, Any], exclude: str = None) -> None:
        """Inunda LSP a todos los vecinos excepto el remitente"""
        for neigh in self.neighbors:
            ch = get_channel(neigh)
            if exclude and ch == exclude:
                continue
            self.transport.publish(ch, packet)

    def _forward_packet(self, packet: Dict[str, Any], next_hop_node: str) -> None:
        """Reenvía paquete al siguiente salto"""
        if dec_hops(packet) <= 0:
            print(f"⚠️ [{self.node_id}] Paquete descartado - TTL agotado")
            return
        self.transport.publish(get_channel(next_hop_node), packet)

    # ========== TABLA DE ENRUTAMIENTO ==========
    def _calculate_routing_table(self) -> None:
        """Recalcula tabla de enrutamiento usando Dijkstra"""
        graph: Dict[str, Dict[str, float]] = {}
        
        # Construir grafo desde LSDB
        for node, rec in self.lsdb.items():
            graph[node] = {v: float(c) for v, c in rec.get("neighbors", {}).items()}

        # Agregar información local
        if self.node_id not in graph:
            graph[self.node_id] = {n: 1.0 for n in self.neighbors}

        # Calcular rutas con Dijkstra
        self.routing_table = routing_table_for(graph, self.node_id)

    def _get_next_hop(self, destination_node: str) -> str:
        """Obtiene el siguiente salto para un destino"""
        for entry in self.routing_table:
            if entry["destino"] == destination_node:
                return str(entry["next_hop"])
        return ""

    # ========== API PÚBLICA ==========
    def send_message(self, dst_node: str, payload: str, hops: int = 8) -> None:
        """Envía mensaje a un nodo específico"""
        if dst_node == "*":
            self.broadcast_message(payload, hops)
            return

        pkt = make_packet("message", self.channel_local, get_channel(dst_node), hops=hops, alg="lsr", payload=payload)
        next_hop = self._get_next_hop(dst_node)
        
        if next_hop:
            self._forward_packet(pkt, next_hop)
            print(f"📤 [{self.node_id}] Mensaje enviado a {dst_node} vía {next_hop}")
        else:
            # Fallback: flooding
            for neigh in self.neighbors:
                self.transport.publish(get_channel(neigh), pkt)
            print(f"📤 [{self.node_id}] Mensaje enviado por flooding (sin ruta)")

    def broadcast_message(self, payload: str, hops: int = 8) -> None:
        """Envía mensaje broadcast a todos los nodos"""
        pkt = make_packet("message", self.channel_local, "*", hops=hops, alg="lsr", payload=payload)
        for neigh in self.neighbors:
            self.transport.publish(get_channel(neigh), pkt)
        print(f"📡 [{self.node_id}] Broadcast enviado a todos los vecinos")

    def send_hello(self, dst_node: str) -> None:
        """Envía HELLO manual a un nodo específico"""
        pkt = make_packet("hello", self.channel_local, get_channel(dst_node), hops=1, alg="lsr", payload="HELLO")
        self.transport.publish(get_channel(dst_node), pkt)
        print(f"👋 [{self.node_id}] HELLO manual enviado a {dst_node}")

    # ========== COMANDOS DE INFORMACIÓN ==========
    def show_lsdb(self) -> None:
        """Muestra la Link State Database"""
        print(f"\n📊 LSDB de {self.node_id}:")
        print("=" * 40)
        if not self.lsdb:
            print("  (vacía)")
        else:
            for node, info in self.lsdb.items():
                neighbors = info.get("neighbors", {})
                print(f"  {node}: {dict(neighbors)}")
        print()

    def show_routing_table(self) -> None:
        """Muestra la tabla de enrutamiento"""
        print(f"\n🗺️  Tabla de Enrutamiento de {self.node_id}:")
        print("=" * 50)
        if not self.routing_table:
            print("  (vacía)")
        else:
            print("  Destino | Next-Hop | Costo")
            print("  --------|----------|------")
            for entry in self.routing_table:
                dest = entry["destino"]
                nh = entry["next_hop"]
                cost = entry["costo"]
                print(f"  {dest:7} | {nh:8} | {cost}")
        print()

    def show_neighbors(self) -> None:
        """Muestra vecinos configurados y descubiertos"""
        print(f"\n👥 Vecinos de {self.node_id}:")
        print("=" * 30)
        print(f"  Configurados: {self.neighbors}")
        print(f"  Descubiertos: {list(self.discovered_neighbors)}")
        print()

    def show_status(self) -> None:
        """Muestra estado general del router"""
        print(f"\n📋 Estado del Router {self.node_id}:")
        print("=" * 40)
        print(f"  Canal: {self.channel_local}")
        print(f"  Vecinos configurados: {len(self.neighbors)}")
        print(f"  Vecinos descubiertos: {len(self.discovered_neighbors)}")
        print(f"  Entradas en LSDB: {len(self.lsdb)}")
        print(f"  Rutas en tabla: {len(self.routing_table)}")
        print(f"  Sequence number: {self.sequence_number}")
        print(f"  LSPs vistos: {len(self.seen_lsp_ids)}")
        print()

def print_help():
    """Muestra ayuda de comandos"""
    print("\n🔗 Comandos disponibles para LSR Router:")
    print("=" * 50)
    print("  send <destino> <mensaje>     - Enviar mensaje a nodo específico")
    print("  broadcast <mensaje>          - Enviar mensaje a todos los nodos")
    print("  hello <destino>             - Enviar HELLO manual")
    print("  show lsdb                   - Mostrar Link State Database")
    print("  show routes                 - Mostrar tabla de enrutamiento")
    print("  show neighbors              - Mostrar vecinos")
    print("  status                      - Mostrar estado del router")
    print("  help                        - Mostrar esta ayuda")
    print("  quit/exit                   - Salir del programa")
    print("\nEjemplos:")
    print("  send B Hola desde A!")
    print("  broadcast Mensaje para todos")
    print("  hello C")
    print()

def main():
    if len(sys.argv) < 3:
        print("Uso: python interactive_lsr_router.py <topo.json> <Nodo>")
        sys.exit(1)

    topo_path = sys.argv[1]
    node = sys.argv[2]

    try:
        graph = load_topology(topo_path)
    except Exception as e:
        print(f"❌ Error cargando topología '{topo_path}': {e}")
        sys.exit(2)

    router = InteractiveLSRRouter(node, graph)

    try:
        router.start()
        print_help()
        
        # Esperar un poco para que se establezcan conexiones
        time.sleep(2)
        
        while True:
            try:
                cmd = input(f"[{node}]> ").strip()
                if not cmd:
                    continue
                    
                parts = cmd.split(None, 2)
                action = parts[0].lower()
                
                if action in ["quit", "exit"]:
                    break
                elif action == "help":
                    print_help()
                elif action == "send" and len(parts) >= 3:
                    dest, message = parts[1], " ".join(parts[2:])
                    router.send_message(dest, message)
                elif action == "broadcast" and len(parts) >= 2:
                    message = " ".join(parts[1:])
                    router.broadcast_message(message)
                elif action == "hello" and len(parts) >= 2:
                    dest = parts[1]
                    router.send_hello(dest)
                elif action == "show" and len(parts) >= 2:
                    what = parts[1].lower()
                    if what == "lsdb":
                        router.show_lsdb()
                    elif what in ["routes", "routing"]:
                        router.show_routing_table()
                    elif what == "neighbors":
                        router.show_neighbors()
                    else:
                        print("❌ Opciones: show lsdb|routes|neighbors")
                elif action == "status":
                    router.show_status()
                else:
                    print("❌ Comando desconocido. Usa 'help' para ver comandos disponibles.")
                    
            except EOFError:
                break
            except KeyboardInterrupt:
                break
                
    except KeyboardInterrupt:
        pass
    finally:
        print("\n👋 Cerrando router LSR...")
        router.stop()

if __name__ == "__main__":
    main()
