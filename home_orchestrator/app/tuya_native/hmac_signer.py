"""Firma de la API movil: hex(HMAC-SHA256(K, sign_string)). K reconstruida
byte a byte (recuperada del tuya_native original)."""
from __future__ import annotations
import hashlib, hmac

def cert_fingerprint(cert_der: bytes) -> bytes:
    """SHA-256 del cert DER, hex MAYUSCULA con ':' entre bytes (como BYTES)."""
    h = hashlib.sha256(cert_der).hexdigest().upper()
    return b":".join(h[i:i+2].encode() for i in range(0, len(h), 2))

def build_signing_key(pkg_name: bytes, cert_der: bytes, bmp_secret: bytes, app_secret: bytes) -> bytes:
    """K = pkg_name + '_' + fingerprint + '_' + bmp_secret + '_' + app_secret (todo bytes)."""
    return b"_".join([pkg_name, cert_fingerprint(cert_der), bmp_secret, app_secret])

def sign(sign_string: bytes, signing_key: bytes) -> str:
    return hmac.new(signing_key, sign_string, hashlib.sha256).hexdigest()
