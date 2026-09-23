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
