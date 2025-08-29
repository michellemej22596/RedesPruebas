# test_redis_storage.py
import os, redis

HOST = os.getenv("REDIS_HOST")
PORT = int(os.getenv("REDIS_PORT", "6379"))
PWD  = os.getenv("REDIS_PWD", "")

r = redis.Redis(host=HOST, port=PORT, password=PWD)
r.set("mensaje", "¡Hola desde Redis!")
msg = r.get("mensaje")
print(f"Mensaje desde Redis: {msg.decode('utf-8') if msg else None}")
