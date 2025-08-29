# id_map.py
import os
import json
from typing import Dict

SECTION = os.getenv("SECTION", "10")
GROUP   = os.getenv("GROUP", "0")
NAMES_PATH = os.getenv("NAMES_FILE", "names.json")

def _load_names(path: str) -> Dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    cfg = data.get("config", data)
    return {k: str(v) for k, v in cfg.items()}

NODES_TO_USER: Dict[str, str] = _load_names(NAMES_PATH)

def _mk_channel(username: str) -> str:
    return f"sec{SECTION}.grupo{GROUP}.{username}"

NODE_TO_CHANNEL: Dict[str, str] = {node: _mk_channel(user) for node, user in NODES_TO_USER.items()}

def get_channel(node_id: str) -> str:
    if node_id == "*":
        return "*"
    if node_id not in NODE_TO_CHANNEL:
        raise KeyError(f"Nó desconhecido: {node_id}")
    return NODE_TO_CHANNEL[node_id]

def channel_to_node(channel: str) -> str:
    username = channel.split(".")[-1]
    for node, user in NODES_TO_USER.items():
        if user == username:
            return node
    return ""
