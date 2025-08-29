# test_redis_connection.py
import os, redis

HOST = os.getenv("REDIS_HOST")
PORT = int(os.getenv("REDIS_PORT", "6379"))
PWD  = os.getenv("REDIS_PWD", "")

r = redis.Redis(host=HOST, port=PORT, password=PWD)
print("Conectado:", r.ping())
