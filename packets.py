# packets.py
from __future__ import annotations
import uuid
import time
from typing import Any, Dict, List, Optional

BROADCAST = "*"

def _now_ms() -> int:
    return int(time.time() * 1000)

def make_packet(p_type: str,
                from_channel: str,
                to_channel: str,
                hops: int = 8,
                alg: str = "flooding",
                seq_num: Optional[int] = None,
                neighbors: Optional[List[str]] = None,
                payload: Any = "") -> Dict[str, Any]:
    packet = {
        "type": p_type,
        "from": from_channel,
        "to": to_channel,
        "hops": int(hops),
        "headers": {"alg": alg},
        "payload": payload
    }
    
    # Add seq_num for info messages
    if seq_num is not None:
        packet["seq_num"] = seq_num
    
    # Add neighbors for info messages
    if neighbors is not None:
        packet["neighbors"] = neighbors
    
    return packet

def normalize_packet(pkt: Dict[str, Any]) -> Dict[str, Any]:
    return pkt

def validate_packet(pkt: Dict[str, Any]) -> bool:
    try:
        if not isinstance(pkt, dict): return False
        for k in ("type", "from", "to", "hops", "headers"):
            if k not in pkt: return False
        if not isinstance(pkt["type"], str): return False
        if not isinstance(pkt["from"], str): return False
        if not isinstance(pkt["to"], str): return False
        if not isinstance(pkt["hops"], int): return False
        if not isinstance(pkt["headers"], dict): return False
        if "alg" not in pkt["headers"]: return False
        return True
    except Exception:
        return False

def get_packet_id(pkt: Dict[str, Any]) -> str:
    try:
        return pkt.get("headers", {}).get("id", str(uuid.uuid4()))
    except Exception:
        return str(uuid.uuid4())

def dec_hops(pkt: Dict[str, Any]) -> int:
    try:
        pkt["hops"] = int(pkt.get("hops", 0)) - 1
    except Exception:
        pkt["hops"] = -1
    return pkt["hops"]

def is_deliver_to_me(pkt: Dict[str, Any], my_channel: str) -> bool:
    to_ch = pkt.get("to", "")
    return to_ch == my_channel or to_ch == BROADCAST
