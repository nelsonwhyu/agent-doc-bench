from __future__ import annotations

import secrets
import time

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

ACCESS_TOKEN_TTL_S = 3600
AUTH_CODE_TTL_S = 600
PENDING_LOGIN_TTL_S = 600


class DevOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """Minimal single-tenant OAuth authorization server, in-memory only.

    Satisfies the dynamic-client-registration handshake Claude/ChatGPT's
    connector UI requires before it'll add a remote MCP server — there's no
    real user database behind it, just one shared passphrase.

    If `shared_secret` is set, /authorize redirects to a one-field
    passphrase form (rendered/handled by mcp_server/server.py's /dev-login
    route) before issuing a code. If `shared_secret` is None, every
    /authorize call is granted a token immediately with no form at all —
    only appropriate for a short-lived throwaway URL (e.g. a cloudflared
    quick tunnel during local testing), never for a persistent hosted URL.
    """

    def __init__(self, shared_secret: str | None = None) -> None:
        self._shared_secret = shared_secret
        self._clients: dict[str, OAuthClientInformationFull] = {}
        self._auth_codes: dict[str, AuthorizationCode] = {}
        self._access_tokens: dict[str, AccessToken] = {}
        self._refresh_tokens: dict[str, RefreshToken] = {}
        self._pending_logins: dict[str, tuple[OAuthClientInformationFull, AuthorizationParams, float]] = {}

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        client_info.client_id = client_info.client_id or secrets.token_hex(16)
        client_info.client_id_issued_at = int(time.time())
        self._clients[client_info.client_id] = client_info

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if self._shared_secret is None:
            return self._issue_code_redirect(client, params)

        pending_id = secrets.token_urlsafe(16)
        self._pending_logins[pending_id] = (client, params, time.time() + PENDING_LOGIN_TTL_S)
        return f"/dev-login?pending={pending_id}"

    def render_login_page(self, pending_id: str) -> str | None:
        """Returns the passphrase-form HTML for a pending /authorize
        redirect, or None if the pending_id is unknown/expired (server.py's
        GET /dev-login route turns None into a 400)."""
        if not self._pending_login_is_live(pending_id):
            return None
        return f"""<!doctype html>
<html>
<head><title>agent-doc-bench connector login</title></head>
<body style="font-family: sans-serif; max-width: 28rem; margin: 4rem auto;">
  <h2>agent-doc-bench</h2>
  <p>Enter the shared passphrase to connect this MCP client.</p>
  <form method="post" action="/dev-login">
    <input type="hidden" name="pending" value="{pending_id}">
    <input type="password" name="secret" placeholder="Passphrase" autofocus
           style="width: 100%; padding: 0.5rem; font-size: 1rem;">
    <button type="submit" style="margin-top: 0.75rem; padding: 0.5rem 1rem;">Connect</button>
  </form>
</body>
</html>"""

    def complete_login(self, pending_id: str, secret: str) -> str | None:
        """Checks the submitted passphrase and, if correct, returns the
        final redirect URL to send the client back to (with its
        authorization code). Returns None on a wrong/expired/unknown
        attempt (server.py's POST /dev-login route turns None into a 401)."""
        if not self._pending_login_is_live(pending_id):
            return None
        if not secrets.compare_digest(secret, self._shared_secret or ""):
            return None
        client, params, _ = self._pending_logins.pop(pending_id)
        return self._issue_code_redirect(client, params)

    def _pending_login_is_live(self, pending_id: str) -> bool:
        entry = self._pending_logins.get(pending_id)
        if entry is None:
            return False
        if entry[2] < time.time():
            del self._pending_logins[pending_id]
            return False
        return True

    def _issue_code_redirect(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        code = secrets.token_urlsafe(32)
        self._auth_codes[code] = AuthorizationCode(
            code=code,
            scopes=params.scopes or [],
            expires_at=time.time() + AUTH_CODE_TTL_S,
            client_id=client.client_id or "",
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
        )
        return construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        return self._auth_codes.get(authorization_code)

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        del self._auth_codes[authorization_code.code]
        return self._issue_tokens(authorization_code.client_id, authorization_code.scopes)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        return self._refresh_tokens.get(refresh_token)

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        del self._refresh_tokens[refresh_token.token]
        return self._issue_tokens(refresh_token.client_id, scopes)

    async def load_access_token(self, token: str) -> AccessToken | None:
        access_token = self._access_tokens.get(token)
        if access_token is None:
            return None
        if access_token.expires_at and access_token.expires_at < time.time():
            del self._access_tokens[token]
            return None
        return access_token

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        if isinstance(token, AccessToken):
            self._access_tokens.pop(token.token, None)
        else:
            self._refresh_tokens.pop(token.token, None)

    def _issue_tokens(self, client_id: str, scopes: list[str]) -> OAuthToken:
        access_token = secrets.token_urlsafe(32)
        refresh_token = secrets.token_urlsafe(32)
        self._access_tokens[access_token] = AccessToken(
            token=access_token,
            client_id=client_id,
            scopes=scopes,
            expires_at=int(time.time()) + ACCESS_TOKEN_TTL_S,
        )
        self._refresh_tokens[refresh_token] = RefreshToken(
            token=refresh_token,
            client_id=client_id,
            scopes=scopes,
        )
        return OAuthToken(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=ACCESS_TOKEN_TTL_S,
            scope=" ".join(scopes),
        )
