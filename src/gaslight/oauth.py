"""ChatGPT OAuth (PKCE) — log in with a ChatGPT account instead of an API key.

Mirrors the flow Codex CLI uses: a public client, a PKCE challenge, and a
loopback callback on port 1455. Credentials are interchangeable with
``~/.codex/auth.json``, so an existing ``codex login`` is picked up for free.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE = "https://auth.openai.com/oauth/authorize"
TOKEN = "https://auth.openai.com/oauth/token"
REDIRECT = "http://localhost:1455/auth/callback"
SCOPES = "openid profile email offline_access"

CODEX_AUTH = Path.home() / ".codex" / "auth.json"
DEFAULT_MODEL = "gpt-5.6-luna"


@dataclass
class Credentials:
    access_token: str
    refresh_token: str
    account_id: str
    source: str = "codex"

    @property
    def expires_at(self) -> int:
        try:
            payload = self.access_token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return int(json.loads(base64.urlsafe_b64decode(payload)).get("exp", 0))
        except Exception:
            return 0

    @property
    def expired(self) -> bool:
        exp = self.expires_at
        return bool(exp) and exp - 120 < time.time()


def _store_path() -> Path:
    override = os.environ.get("GASLIGHT_AUTH_PATH")
    return Path(override) if override else CODEX_AUTH


def load(path: Path | None = None) -> Credentials | None:
    """Load credentials, refreshing them if they have expired."""
    path = path or _store_path()
    if not path.exists():
        return None
    try:
        tokens = json.loads(path.read_text())["tokens"]
        creds = Credentials(tokens["access_token"], tokens["refresh_token"],
                            tokens["account_id"], source=str(path))
    except Exception:
        return None
    if creds.expired:
        refreshed = refresh(creds)
        if refreshed:
            _save(path, refreshed)
            return refreshed
    return creds


def refresh(creds: Credentials) -> Credentials | None:
    body = json.dumps({
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": creds.refresh_token,
        "scope": SCOPES,
    }).encode()
    req = urllib.request.Request(TOKEN, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        data = json.loads(urllib.request.urlopen(req, timeout=45).read())
    except Exception:
        return None
    return Credentials(
        data.get("access_token", creds.access_token),
        data.get("refresh_token", creds.refresh_token),
        creds.account_id,
        source=creds.source,
    )


def _save(path: Path, creds: Credentials) -> None:
    payload = {}
    if path.exists():
        try:
            payload = json.loads(path.read_text())
        except Exception:
            payload = {}
    payload.setdefault("OPENAI_API_KEY", None)
    payload["tokens"] = {
        "access_token": creds.access_token,
        "refresh_token": creds.refresh_token,
        "account_id": creds.account_id,
        "id_token": (payload.get("tokens") or {}).get("id_token", ""),
    }
    payload["last_refresh"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2))
    path.chmod(0o600)


def login(open_browser: bool = True, timeout: int = 300) -> Credentials:
    """Run the PKCE flow and persist the resulting credentials."""
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).decode().rstrip("=")
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state = secrets.token_urlsafe(24)
    result: dict[str, str] = {}
    done = threading.Event()

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in query and query.get("state", [""])[0] == state:
                result["code"] = query["code"][0]
                body = b"<h2>Signed in.</h2><p>You can close this tab.</p>"
            else:
                body = b"<h2>Sign-in failed.</h2>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            done.set()

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 1455), Callback)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = AUTHORIZE + "?" + urllib.parse.urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT,
        "scope": SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
    })
    print("Open this URL to sign in with ChatGPT:\n\n  " + url + "\n", flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    if not done.wait(timeout) or "code" not in result:
        server.shutdown()
        raise TimeoutError("ChatGPT sign-in did not complete")
    server.shutdown()

    body = json.dumps({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": result["code"],
        "redirect_uri": REDIRECT,
        "code_verifier": verifier,
    }).encode()
    req = urllib.request.Request(TOKEN, data=body,
                                 headers={"Content-Type": "application/json"})
    data = json.loads(urllib.request.urlopen(req, timeout=60).read())

    account_id = ""
    try:
        payload = data["id_token"].split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        auth = claims.get("https://api.openai.com/auth", {})
        account_id = auth.get("chatgpt_account_id", "")
    except Exception:
        pass

    creds = Credentials(data["access_token"], data.get("refresh_token", ""), account_id)
    _save(_store_path(), creds)
    return creds


def complete(creds: Credentials, instructions: str, text: str,
             model: str = DEFAULT_MODEL, effort: str = "low", timeout: int = 180) -> str:
    """One non-streaming-ish completion against the ChatGPT Codex backend."""
    body = json.dumps({
        "model": model,
        "instructions": instructions,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": text}]}],
        "stream": True,
        "store": False,
        "reasoning": {"effort": effort},
    }).encode()
    req = urllib.request.Request(
        "https://chatgpt.com/backend-api/codex/responses", data=body,
        headers={
            "Authorization": f"Bearer {creds.access_token}",
            "chatgpt-account-id": creds.account_id,
            "Content-Type": "application/json",
            "OpenAI-Beta": "responses=experimental",
            "originator": "codex_cli_rs",
            "User-Agent": "codex_cli_rs/0.154.0",
        })
    chunks: list[str] = []
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[5:].strip())
            except Exception:
                continue
            if event.get("type") == "response.output_text.delta":
                chunks.append(event.get("delta", ""))
            elif event.get("type") == "response.completed":
                break
    return "".join(chunks).strip()
