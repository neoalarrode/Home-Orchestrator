"""Firma nativa de la API movil (doCommandNative). En runtime el plugin firma con
K en Python puro: hex(HMAC-SHA256(K, signstr)). K es una CONSTANTE derivada de
version-app + certificado + bmp_secret + appSecret, recuperada UNA vez con el
emulador nativo (ver ~/dev/tuya-emu). Mientras no este resuelta K, esto avisa."""
from __future__ import annotations

class NativeSignerUnavailable(RuntimeError):
    pass

# K se inyecta desde ~/.tuya_native (fuera del repo) o config; NUNCA en el repo.
def load_signing_key(path="~/.tuya_native/signing_key.bin") -> bytes:
    import os
    p = os.path.expanduser(path)
    if not os.path.exists(p):
        raise NativeSignerUnavailable(
            "Falta la clave de firma K. Recuperarla con el emulador (~/dev/tuya-emu) "
            "y guardarla en %s. El plugin firma con hex(HMAC-SHA256(K, signstr))." % p)
    return open(p, "rb").read()
