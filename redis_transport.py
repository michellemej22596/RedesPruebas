# redis_transport.py
import os
import json
import threading
import time
import redis

class RedisTransport:
    """
    Transporte simple sobre Redis Pub/Sub.
    - Se suscribe a 'my_channel' y llama on_packet(packet_dict) al recibir mensajes JSON.
    - publish(channel, packet_dict) publica el paquete (JSON) al canal indicado.
    Variables de entorno: REDIS_HOST, REDIS_PORT, REDIS_PWD
    """
    def __init__(self, my_channel: str, on_packet):
        self.host = os.getenv("REDIS_HOST")
        self.port = int(os.getenv("REDIS_PORT", "6379"))
        self.pwd  = os.getenv("REDIS_PWD", "")

        if not self.host:
            raise RuntimeError("Falta REDIS_HOST en el entorno. Configúralo antes de iniciar.")

        self.my_channel = my_channel
        self.on_packet = on_packet
        self._stop = threading.Event()
        self._thread = None
        self._r = None
        self._pubsub = None

    def start(self):
        # Conexión y suscripción
        self._r = redis.Redis(host=self.host, port=self.port, password=self.pwd, decode_responses=True)
        self._r.ping()
        print(f"[RedisTransport] Conectado a {self.host}:{self.port}. Canal local: {self.my_channel}")

        self._pubsub = self._r.pubsub()
        self._pubsub.subscribe(self.my_channel)

        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def _listen_loop(self):
        try:
            for msg in self._pubsub.listen():
                if self._stop.is_set():
                    break
                if msg.get("type") != "message":
                    continue
                data = msg.get("data")
                try:
                    pkt = json.loads(data) if isinstance(data, str) else data
                except Exception as e:
                    print(f"[RedisTransport] ⚠️ Mensaje no-JSON en {self.my_channel}: {e} :: {data}")
                    continue
                try:
                    self.on_packet(pkt)
                except Exception as e:
                    print(f"[RedisTransport] ⚠️ Error en callback on_packet: {e}")
        except Exception as e:
            print(f"[RedisTransport] ⚠️ Loop de escucha terminó con error: {e}")

    def publish(self, channel: str, packet: dict):
        try:
            payload = json.dumps(packet, ensure_ascii=False)
        except Exception as e:
            raise ValueError(f"Paquete no serializable a JSON: {e}")
        self._r.publish(channel, payload)

    def stop(self):
        self._stop.set()
        try:
            if self._pubsub:
                self._pubsub.unsubscribe(self.my_channel)
                self._pubsub.close()
        except Exception:
            pass
