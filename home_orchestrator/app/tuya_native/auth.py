"""Auth: email/contraseña con re-auth automatica (como la app). QR queda como
alternativa sin renovacion. Flujo verificado en jadx (sdk/user/pqdbppq.java)."""
from __future__ import annotations
from typing import Any, Callable, Optional
from . import login_crypto
SESSION_ERRORS={"USER_SESSION_INVALID","USER_SESSION_LOSS","SIGN_INVALID","TOKEN_INVALID"}
def login_email_password(client, email, password, *, country_code="34", login_version="1.0"):
    tok=client.call("thing.m.user.email.token.create","1.0",
        post_data={"countryCode":country_code,"email":email},session_require=False)
    pub=login_crypto.build_rsa_public_key(str(tok["publicKey"]),str(tok["exponent"]))
    user=client.call("thing.m.user.email.password.login",login_version,
        post_data={"countryCode":country_code,"email":email,
                   "passwd":login_crypto.encrypt_password_hex(password,pub),
                   "options":"{\"group\": 1}","token":tok["token"],"ifencrypt":1},
        session_require=False)
    client.session_id=user.get("sid"); client.ecode=user.get("ecode"); return user

# ---------------------------------------------------------------- QR (fallback)
# La app: qr.token.create -> URL tuyaSmart--qrLogin?token=... -> el usuario la
# escanea desde "Yo" (Me) -> escaner. qr.token.user.get devuelve True mientras
# esta pendiente y un dict con sid/ecode/uid cuando se confirma. QR NO renueva
# sesion (a diferencia de email/password), por eso es el camino alternativo.
def qr_create_token(client, *, country_code: Optional[str]=None) -> str:
    post={"countryCode":country_code} if country_code else None
    tok=client.call("thing.m.user.qr.token.create","1.0",post_data=post,session_require=False)
    if not isinstance(tok,str):
        raise RuntimeError("qr.token.create: respuesta inesperada %r"%(tok,))
    return tok
def qr_login_url(token: str) -> str:
    return "tuyaSmart--qrLogin?token=%s"%token
def qr_poll(client, token: str, *, country_code: Optional[str]=None) -> Optional[dict]:
    """UNA consulta no bloqueante: dict con sid/ecode/uid si ya se confirmo,
    None si sigue pendiente (result True). Propaga ApiError (token expirado,
    error de firma, ...) para que el llamante lo muestre."""
    post={"token":token}
    if country_code: post["countryCode"]=country_code
    result=client.call("thing.m.user.qr.token.user.get","1.0",post_data=post,session_require=False)
    if isinstance(result,dict) and result.get("sid"):
        client.session_id=result.get("sid"); client.ecode=result.get("ecode")
        return result
    return None
class SessionManager:
    def __init__(self, client, relogin_cb: Optional[Callable[[],Any]]=None):
        self.client=client; self.relogin_cb=relogin_cb
    def call(self,*a,**kw):
        try: return self.client.call(*a,**kw)
        except Exception as e:
            if getattr(e,"error_code",None) in SESSION_ERRORS and self.relogin_cb:
                self.relogin_cb(); return self.client.call(*a,**kw)
            raise
    @staticmethod
    def for_password(client,email,password,**kw):
        def relogin(): login_email_password(client,email,password,**kw)
        relogin(); return SessionManager(client,relogin)
