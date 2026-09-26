#!/usr/bin/env python3
"""
youtube_auth.py — one-time YouTube OAuth consent helper.

Run once. It opens your browser, you click "Allow", and it writes
YT_REFRESH_TOKEN into ~/src/WenVideoChannel/.env.

Requires YT_CLIENT_ID and YT_CLIENT_SECRET to already be in
~/src/WenVideoChannel/.env.

Usage:
    python3 ~/src/WenVideoChannel/youtube_auth.py
"""

import http.server
import json
import os
import secrets
import socketserver
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path

ENV_FILE = Path.home() / "src" / "WenVideoChannel" / ".env"
REDIRECT_PORT = 8765
REDIRECT_URI = f"http://127.0.0.1:{REDIRECT_PORT}/"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def read_env() -> dict:
    if not ENV_FILE.exists():
        sys.exit(f"Missing {ENV_FILE}. Run the SETUP.md steps first.")
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()
    return env


def write_env_value(key: str, value: str) -> None:
    """Insert or replace key=value in the .env file, preserving other lines."""
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    found = False
    out = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            out.append(f"{key}={value}")
            found = True
        else:
            out.append(line)
    if not found:
        out.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(out) + "\n")
    os.chmod(ENV_FILE, 0o600)


# --- minimal one-shot HTTP server to catch the OAuth redirect ------------
received = {}


class OAuthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        if "code" in params:
            received["code"] = params["code"][0]
            received["state"] = params.get("state", [""])[0]
            body = b"<h1>OK - you can close this tab.</h1>"
        else:
            received["error"] = params.get("error", ["unknown"])[0]
            body = f"<h1>Error: {received['error']}</h1>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # silence noisy default logging
        pass


def main() -> None:
    env = read_env()
    client_id = env.get("YT_CLIENT_ID")
    client_secret = env.get("YT_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit("YT_CLIENT_ID and YT_CLIENT_SECRET must be set in ~/src/WenVideoChannel/.env first.")

    state = secrets.token_urlsafe(16)
    auth_qs = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })
    auth_url = f"{AUTH_URL}?{auth_qs}"

    # Start local server first so the redirect won't race us.
    httpd = socketserver.TCPServer(("127.0.0.1", REDIRECT_PORT), OAuthHandler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()

    print("Opening browser for Google consent...")
    print(f"If it doesn't open, paste this URL into your browser:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    # Wait for the redirect.
    print("Waiting for you to click 'Allow' in the browser...")
    while "code" not in received and "error" not in received:
        pass
    httpd.shutdown()

    if "error" in received:
        sys.exit(f"OAuth error: {received['error']}")
    if received.get("state") != state:
        sys.exit("OAuth state mismatch — aborting for safety.")

    # Exchange code for refresh_token.
    data = urllib.parse.urlencode({
        "code": received["code"],
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()

    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    with urllib.request.urlopen(req) as resp:
        payload = json.loads(resp.read().decode())

    refresh_token = payload.get("refresh_token")
    if not refresh_token:
        sys.exit(
            "No refresh_token returned. This usually means you've authorized this client "
            "before — revoke it at https://myaccount.google.com/permissions and rerun."
        )

    write_env_value("YT_REFRESH_TOKEN", refresh_token)
    print("\nSuccess. YT_REFRESH_TOKEN written to ~/src/WenVideoChannel/.env.")
    print("You won't need to run this script again.")


if __name__ == "__main__":
    main()
