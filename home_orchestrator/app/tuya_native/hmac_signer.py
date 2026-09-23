from __future__ import annotations
import hashlib, hmac

def sign(message: bytes, signing_key: bytes) -> str:
    """firma = hex(HMAC-SHA256(K, message)). VERIFICADO 4/4 contra doCommandNative."""
    return hmac.new(signing_key, message, hashlib.sha256).hexdigest()
