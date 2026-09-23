from __future__ import annotations
import hashlib
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

def md5_password(password: str) -> str:
    return hashlib.md5(password.encode("utf-8")).hexdigest()

def build_rsa_public_key(modulus: str, exponent: str) -> RSA.RsaKey:
    # publicKey/exponent llegan en DECIMAL (BigInteger(String) en la app)
    return RSA.construct((int(modulus), int(exponent)))

def encrypt_password(password: str, rsa_public_key: RSA.RsaKey) -> bytes:
    return PKCS1_v1_5.new(rsa_public_key).encrypt(md5_password(password).encode("ascii"))

def encrypt_password_hex(password: str, rsa_public_key: RSA.RsaKey) -> str:
    return encrypt_password(password, rsa_public_key).hex()
